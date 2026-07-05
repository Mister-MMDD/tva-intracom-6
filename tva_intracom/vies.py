"""Verification des numeros de TVA intracommunautaire via le service VIES.

Backend Postgres (Supabase) — remplace l'ancien cache SQLite local (fichier
unique partagé de facto par tous les comptes, non persistant entre
redéploiements Streamlit Cloud — même défaut que l'ancien auth.py avant sa
migration Postgres).

Architecture à trois niveaux, pour isoler les comptes/cabinets entre eux tout
en mutualisant les vérifications automatiques (utile en cas d'indisponibilité
du serveur VIES de l'UE) :

  1. vies_scope_cache      — cache PRIVÉ par "scope" (compte isolé pour une
                              adresse e-mail personnelle, ou domaine partagé
                              pour une adresse professionnelle). Consulté en
                              premier.
  2. vies_global_cache     — cache PARTAGÉ entre tous les scopes, alimenté
                              UNIQUEMENT par les vérifications automatiques
                              réussies. Sert de filet de sécurité mutualisé.
  3. API VIES (ec.europa.eu) — dernier recours, en cas d'absence dans les
                              deux caches ci-dessus.

  vies_manual_overrides    — classifications manuelles saisies par
                              l'utilisateur. Strictement scopées
                              (scope_id, full_vat). NE REMONTENT JAMAIS dans
                              vies_global_cache : une classification manuelle
                              d'un cabinet ne doit jamais influencer le calcul
                              d'un autre compte.
  vies_check_history        — piste d'audit append-only, elle aussi scopée :
                              chaque scope conserve sa propre preuve de la
                              date à laquelle IL a eu connaissance d'un statut
                              VIES, même quand la donnée provient du cache
                              global (mutualisée mais horodatée localement).

Connexion : variable d'environnement SUPABASE_DB_URL — même base Postgres que
tva_intracom/auth.py et tva_intracom/billing.py. Jamais en dur dans le code.

Dépendance : psycopg2-binary (déjà présente dans requirements.txt pour auth.py).
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

import psycopg2
import psycopg2.pool
from psycopg2.extras import execute_values
import streamlit as st

logger = logging.getLogger(__name__)

VIES_REST_URL = "https://ec.europa.eu/taxation_customs/vies/rest-api/check-vat-number"
DEFAULT_TIMEOUT = 10

# TTL du cache : durée en jours avant qu'un numéro soit revalidé auprès de VIES.
# Valeur par défaut : 90 jours. Modifiable via set_cache_ttl().
CACHE_TTL_DAYS: int = 90

# Retry backoff pour erreurs temporaires VIES (serveur UE instable)
_RETRY_MAX_ATTEMPTS = 3
_RETRY_BASE_DELAY = 1.0  # secondes, doublé à chaque tentative (1 → 2 → 4)


# ---------------------------------------------------------------------------
# Résolution de la portée (scope) de cache par compte
# ---------------------------------------------------------------------------
# Liste fixe (décision produit) : domaines de messagerie personnelle, jamais
# traités comme un "domaine d'entreprise" partagé — chaque compte reste isolé
# même si, par accident, deux clients du même webmail existaient.
PERSONAL_EMAIL_DOMAINS = {
    "gmail.com", "googlemail.com",
    "outlook.com", "outlook.fr", "hotmail.com", "hotmail.fr",
    "live.com", "live.fr", "msn.com",
    "yahoo.com", "yahoo.fr", "yahoo.co.uk",
    "icloud.com", "me.com", "mac.com",
    "aol.com",
    "gmx.com", "gmx.fr", "gmx.net",
    "laposte.net",
    "orange.fr", "wanadoo.fr",
    "free.fr", "sfr.fr", "bbox.fr", "neuf.fr", "numericable.fr", "aliceadsl.fr",
    "protonmail.com", "proton.me", "pm.me",
    "yandex.com", "yandex.ru",
    "mail.com", "zoho.com",
}


def resolve_scope_id(email: str) -> str:
    """Détermine la portée (scope) de cache VIES pour un compte utilisateur.

    - adresse sur un domaine de messagerie personnelle (gmail.com, outlook.com,
      free.fr...) → scope isolé par compte : ``"user:<email>"``.
    - adresse sur un domaine professionnel/entreprise → scope partagé par
      domaine (ex: tous les collaborateurs d'un cabinet en
      ``@cabinet-untel.fr`` partagent le même cache VIES) : ``"domain:<domaine>"``.

    Appelée une fois par session depuis app.py juste après authentification,
    et transmise à toutes les fonctions de ce module ainsi qu'à
    ``engine.compute_all_with_vies``.
    """
    email = (email or "").strip().lower()
    if "@" not in email:
        # Ne devrait pas arriver (auth.py exige un e-mail) — filet de sécurité.
        return f"user:{email or 'inconnu'}"
    domain = email.rsplit("@", 1)[1]
    if domain in PERSONAL_EMAIL_DOMAINS:
        return f"user:{email}"
    return f"domain:{domain}"


# ---------------------------------------------------------------------------
# Dataclass résultat
# ---------------------------------------------------------------------------

@dataclass
class ViesResult:
    valid: bool
    country_code: str
    vat_number: str
    name: str = ""
    address: str = ""
    error: str = ""


# ---------------------------------------------------------------------------
# Helpers TTL / dates
# ---------------------------------------------------------------------------

def set_cache_ttl(days: int) -> None:
    """Modifie le TTL du cache VIES (en jours). Appel optionnel depuis app.py."""
    global CACHE_TTL_DAYS
    CACHE_TTL_DAYS = max(1, int(days))
    logger.info("Cache VIES : TTL mis à jour à %d jours.", CACHE_TTL_DAYS)


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _is_expired(checked_at) -> bool:
    """Retourne True si l'entrée dépasse le TTL configuré.

    Accepte un datetime (valeur normale renvoyée par psycopg2 pour une
    colonne TIMESTAMPTZ) ou, par prudence, une chaîne ISO.
    """
    if checked_at is None:
        return True
    if isinstance(checked_at, str):
        try:
            checked_at = datetime.fromisoformat(checked_at)
        except ValueError:
            return True
    if checked_at.tzinfo is None:
        checked_at = checked_at.replace(tzinfo=timezone.utc)
    return _now_utc() - checked_at > timedelta(days=CACHE_TTL_DAYS)


def _parse_flexible_date(s: str) -> datetime:
    """Parse 'YYYY-MM-DD' ou une date ISO complète en datetime UTC tz-aware.

    Une date seule ('YYYY-MM-DD') est interprétée comme minuit UTC ce jour-là,
    pour retrouver le comportement de get_vies_status_as_of() : « statut connu
    strictement avant cette date » quand seule la date de vente est fournie.
    """
    s = (s or "").strip()
    try:
        if len(s) == 10:
            return datetime.strptime(s, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except ValueError:
        return _now_utc()


# ---------------------------------------------------------------------------
# Pool Postgres (Supabase) — même base que auth.py / billing.py
# ---------------------------------------------------------------------------

_pool: Optional[psycopg2.pool.SimpleConnectionPool] = None
_pool_lock = threading.Lock()


def _get_pool() -> psycopg2.pool.SimpleConnectionPool:
    global _pool
    if _pool is None:
        with _pool_lock:
            if _pool is None:
                dsn = st.secrets.get("SUPABASE_DB_URL") or os.environ.get("SUPABASE_DB_URL")
                if not dsn:
                    raise RuntimeError(
                        "SUPABASE_DB_URL non définie — impossible de se connecter à la "
                        "base du cache VIES. Configurez ce secret côté Streamlit Cloud "
                        "(même valeur que pour auth.py / billing.py)."
                    )
                pool = psycopg2.pool.SimpleConnectionPool(1, 25, dsn)
                _init_schema(pool)
                _pool = pool
    return _pool


def _init_schema(pool: psycopg2.pool.SimpleConnectionPool) -> None:
    conn = pool.getconn()
    try:
        with conn, conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS vies_global_cache (
                    vat_id       TEXT PRIMARY KEY,
                    valid        BOOLEAN NOT NULL,
                    country_code TEXT NOT NULL,
                    vat_number   TEXT NOT NULL,
                    name         TEXT DEFAULT '',
                    address      TEXT DEFAULT '',
                    error        TEXT DEFAULT '',
                    checked_at   TIMESTAMPTZ NOT NULL
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS vies_scope_cache (
                    scope_id     TEXT NOT NULL,
                    vat_id       TEXT NOT NULL,
                    valid        BOOLEAN NOT NULL,
                    country_code TEXT NOT NULL,
                    vat_number   TEXT NOT NULL,
                    name         TEXT DEFAULT '',
                    address      TEXT DEFAULT '',
                    error        TEXT DEFAULT '',
                    checked_at   TIMESTAMPTZ NOT NULL,
                    PRIMARY KEY (scope_id, vat_id)
                )
            """)
            cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_scope_cache_checked_at
                    ON vies_scope_cache(checked_at)
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS vies_check_history (
                    id           BIGSERIAL PRIMARY KEY,
                    scope_id     TEXT NOT NULL,
                    vat_id       TEXT NOT NULL,
                    valid        BOOLEAN NOT NULL,
                    country_code TEXT NOT NULL,
                    vat_number   TEXT NOT NULL,
                    name         TEXT DEFAULT '',
                    address      TEXT DEFAULT '',
                    error        TEXT DEFAULT '',
                    checked_at   TIMESTAMPTZ NOT NULL
                )
            """)
            cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_history_scope_vat
                    ON vies_check_history(scope_id, vat_id, checked_at)
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS vies_manual_overrides (
                    scope_id  TEXT NOT NULL,
                    full_vat  TEXT NOT NULL,
                    is_valid  BOOLEAN NOT NULL,
                    set_at    TIMESTAMPTZ NOT NULL,
                    PRIMARY KEY (scope_id, full_vat)
                )
            """)
    finally:
        pool.putconn(conn)


class _ConnCtx:
    """Emprunte une connexion au pool et la restitue systématiquement, y
    compris en cas d'exception — pattern répété par toutes les fonctions
    de ce module (remplace le threading.local() de l'ancienne version
    SQLite, devenu inutile avec le pool psycopg2)."""

    def __enter__(self):
        self._pool = _get_pool()
        self._conn = self._pool.getconn()
        return self._conn

    def __exit__(self, exc_type, exc, tb):
        self._pool.putconn(self._conn)
        return False


def _conn() -> _ConnCtx:
    return _ConnCtx()


# ---------------------------------------------------------------------------
# Lecture / écriture cache scope + cache global
# ---------------------------------------------------------------------------

def _row_to_result(row) -> ViesResult:
    valid, cc, num, name, addr, err = row
    return ViesResult(valid=bool(valid), country_code=cc, vat_number=num,
                       name=name or "", address=addr or "", error=err or "")


def _db_get_scope(scope_id: str, vat_id: str) -> tuple[Optional[ViesResult], bool]:
    with _conn() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT valid, country_code, vat_number, name, address, error, checked_at "
            "FROM vies_scope_cache WHERE scope_id=%s AND vat_id=%s",
            (scope_id, vat_id),
        )
        row = cur.fetchone()
    if row is None:
        return None, False
    result = _row_to_result(row[:6])
    return result, not _is_expired(row[6])


def _db_get_global(vat_id: str) -> tuple[Optional[ViesResult], bool]:
    with _conn() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT valid, country_code, vat_number, name, address, error, checked_at "
            "FROM vies_global_cache WHERE vat_id=%s",
            (vat_id,),
        )
        row = cur.fetchone()
    if row is None:
        return None, False
    result = _row_to_result(row[:6])
    return result, not _is_expired(row[6])


def _db_set_scope(scope_id: str, vat_id: str, result: ViesResult, log_history: bool = True) -> None:
    """Écrit dans le cache PRIVÉ du scope et journalise dans son historique
    d'audit. N'écrit jamais dans vies_global_cache (voir _db_set_global)."""
    checked_at = _now_utc()
    with _conn() as conn, conn.cursor() as cur:
        cur.execute("""
            INSERT INTO vies_scope_cache
                (scope_id, vat_id, valid, country_code, vat_number, name, address, error, checked_at)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT (scope_id, vat_id) DO UPDATE SET
                valid=EXCLUDED.valid, country_code=EXCLUDED.country_code,
                vat_number=EXCLUDED.vat_number, name=EXCLUDED.name,
                address=EXCLUDED.address, error=EXCLUDED.error,
                checked_at=EXCLUDED.checked_at
        """, (scope_id, vat_id, result.valid, result.country_code, result.vat_number,
              result.name, result.address, result.error, checked_at))
        if log_history:
            cur.execute("""
                INSERT INTO vies_check_history
                    (scope_id, vat_id, valid, country_code, vat_number, name, address, error, checked_at)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
            """, (scope_id, vat_id, result.valid, result.country_code, result.vat_number,
                  result.name, result.address, result.error, checked_at))
        conn.commit()


def _db_set_global(vat_id: str, result: ViesResult) -> None:
    """Écrit UNIQUEMENT dans le cache global mutualisé. Appelée seulement à
    la suite d'une vérification AUTOMATIQUE réussie contre l'API VIES —
    jamais depuis set_manual_override()."""
    checked_at = _now_utc()
    with _conn() as conn, conn.cursor() as cur:
        cur.execute("""
            INSERT INTO vies_global_cache
                (vat_id, valid, country_code, vat_number, name, address, error, checked_at)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT (vat_id) DO UPDATE SET
                valid=EXCLUDED.valid, country_code=EXCLUDED.country_code,
                vat_number=EXCLUDED.vat_number, name=EXCLUDED.name,
                address=EXCLUDED.address, error=EXCLUDED.error,
                checked_at=EXCLUDED.checked_at
        """, (vat_id, result.valid, result.country_code, result.vat_number,
              result.name, result.address, result.error, checked_at))
        conn.commit()


# ---------------------------------------------------------------------------
# Variantes BATCH — un seul aller-retour réseau pour N numéros, au lieu de N
# allers-retours séquentiels. Utilisées uniquement par
# validate_vat_numbers_parallel (le chemin utilisé pour tout traitement de
# fichier) ; check_vat_raw (vérification isolée d'un seul numéro) continue
# d'utiliser les fonctions unitaires ci-dessus, qui restent nécessaires.
# ---------------------------------------------------------------------------

def _db_get_scope_batch(scope_id: str, vat_ids: list[str]) -> dict[str, tuple[ViesResult, bool]]:
    """Une seule requête pour tous les vat_ids d'un coup."""
    if not vat_ids:
        return {}
    with _conn() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT vat_id, valid, country_code, vat_number, name, address, error, checked_at "
            "FROM vies_scope_cache WHERE scope_id=%s AND vat_id = ANY(%s)",
            (scope_id, list(vat_ids)),
        )
        rows = cur.fetchall()
    out: dict[str, tuple[ViesResult, bool]] = {}
    for row in rows:
        vat_id, checked_at = row[0], row[7]
        out[vat_id] = (_row_to_result(row[1:7]), not _is_expired(checked_at))
    return out


def _db_get_global_batch(vat_ids: list[str]) -> dict[str, tuple[ViesResult, bool]]:
    """Une seule requête pour tous les vat_ids d'un coup."""
    if not vat_ids:
        return {}
    with _conn() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT vat_id, valid, country_code, vat_number, name, address, error, checked_at "
            "FROM vies_global_cache WHERE vat_id = ANY(%s)",
            (list(vat_ids),),
        )
        rows = cur.fetchall()
    out: dict[str, tuple[ViesResult, bool]] = {}
    for row in rows:
        vat_id, checked_at = row[0], row[7]
        out[vat_id] = (_row_to_result(row[1:7]), not _is_expired(checked_at))
    return out


def _db_set_scope_batch(scope_id: str, items: list[tuple[str, ViesResult]], log_history: bool = True) -> None:
    """Upsert en lot dans vies_scope_cache + insertion en lot dans
    vies_check_history — un aller-retour réseau au lieu de N."""
    if not items:
        return
    checked_at = _now_utc()
    scope_rows = [
        (scope_id, vat_id, r.valid, r.country_code, r.vat_number, r.name, r.address, r.error, checked_at)
        for vat_id, r in items
    ]
    with _conn() as conn, conn.cursor() as cur:
        execute_values(cur, """
            INSERT INTO vies_scope_cache
                (scope_id, vat_id, valid, country_code, vat_number, name, address, error, checked_at)
            VALUES %s
            ON CONFLICT (scope_id, vat_id) DO UPDATE SET
                valid=EXCLUDED.valid, country_code=EXCLUDED.country_code,
                vat_number=EXCLUDED.vat_number, name=EXCLUDED.name,
                address=EXCLUDED.address, error=EXCLUDED.error,
                checked_at=EXCLUDED.checked_at
        """, scope_rows)
        if log_history:
            execute_values(cur, """
                INSERT INTO vies_check_history
                    (scope_id, vat_id, valid, country_code, vat_number, name, address, error, checked_at)
                VALUES %s
            """, scope_rows)
        conn.commit()


def _db_set_global_batch(items: list[tuple[str, ViesResult]]) -> None:
    """Upsert en lot dans vies_global_cache — un aller-retour réseau au lieu
    de N. N'écrit jamais depuis un chemin lié aux overrides manuels."""
    if not items:
        return
    checked_at = _now_utc()
    rows = [
        (vat_id, r.valid, r.country_code, r.vat_number, r.name, r.address, r.error, checked_at)
        for vat_id, r in items
    ]
    with _conn() as conn, conn.cursor() as cur:
        execute_values(cur, """
            INSERT INTO vies_global_cache
                (vat_id, valid, country_code, vat_number, name, address, error, checked_at)
            VALUES %s
            ON CONFLICT (vat_id) DO UPDATE SET
                valid=EXCLUDED.valid, country_code=EXCLUDED.country_code,
                vat_number=EXCLUDED.vat_number, name=EXCLUDED.name,
                address=EXCLUDED.address, error=EXCLUDED.error,
                checked_at=EXCLUDED.checked_at
        """, rows)
        conn.commit()


def get_vies_history(scope_id: str, full_vat: str) -> list[dict]:
    """Historique des vérifications VIES pour un numéro, DANS LE SCOPE
    courant — de la plus ancienne à la plus récente. Piste d'audit propre à
    ce compte/cabinet, même pour les entrées obtenues via le cache global."""
    with _conn() as conn, conn.cursor() as cur:
        cur.execute("""
            SELECT checked_at, valid, country_code, vat_number, name, address, error
            FROM vies_check_history WHERE scope_id=%s AND vat_id=%s
            ORDER BY checked_at ASC
        """, (scope_id, full_vat))
        rows = cur.fetchall()
    return [
        {
            "checked_at": r[0].isoformat() if r[0] else "",
            "valid": bool(r[1]), "country_code": r[2], "vat_number": r[3],
            "name": r[4] or "", "address": r[5] or "", "error": r[6] or "",
        }
        for r in rows
    ]


def get_vies_history_bulk(scope_id: str, full_vats: list[str]) -> dict[str, list[dict]]:
    """Comme get_vies_history(), mais pour plusieurs numéros en une seule
    requête. Utilisée par excel_report._write_vies_history_tab pour éviter
    une requête Postgres par numéro de TVA unique dans le fichier traité."""
    if not full_vats:
        return {}
    with _conn() as conn, conn.cursor() as cur:
        cur.execute("""
            SELECT vat_id, checked_at, valid, country_code, vat_number, name, address, error
            FROM vies_check_history WHERE scope_id=%s AND vat_id = ANY(%s)
            ORDER BY vat_id ASC, checked_at ASC
        """, (scope_id, list(full_vats)))
        rows = cur.fetchall()
    result: dict[str, list[dict]] = {}
    for r in rows:
        result.setdefault(r[0], []).append({
            "checked_at": r[1].isoformat() if r[1] else "",
            "valid": bool(r[2]), "country_code": r[3], "vat_number": r[4],
            "name": r[5] or "", "address": r[6] or "", "error": r[7] or "",
        })
    return result


def get_vies_status_as_of(scope_id: str, full_vat: str, as_of_date_iso: str) -> Optional[dict]:
    """Statut VIES tel que connu par CE scope à une date donnée (ex: date
    d'une vente), pour justifier une exonération B2B lors d'un contrôle
    fiscal. Retourne None si ce scope n'avait aucune vérification
    antérieure à cette date."""
    as_of = _parse_flexible_date(as_of_date_iso)
    with _conn() as conn, conn.cursor() as cur:
        cur.execute("""
            SELECT checked_at, valid, country_code, vat_number, name, address, error
            FROM vies_check_history
            WHERE scope_id=%s AND vat_id=%s AND checked_at <= %s
            ORDER BY checked_at DESC LIMIT 1
        """, (scope_id, full_vat, as_of))
        row = cur.fetchone()
    if row is None:
        return None
    return {
        "checked_at": row[0].isoformat() if row[0] else "",
        "valid": bool(row[1]), "country_code": row[2], "vat_number": row[3],
        "name": row[4] or "", "address": row[5] or "", "error": row[6] or "",
    }


def _db_delete_expired_scope(scope_id: str) -> int:
    """Purge les entrées expirées ET les erreurs transitoires du scope
    courant. N'affecte jamais le cache global (mutualisé, purgé
    indépendamment par purge_expired_global_cache())."""
    cutoff = _now_utc() - timedelta(days=CACHE_TTL_DAYS)
    transient_patterns = [
        "%ms_unavailable%", "%service_unavailable%",
        "%ms_max_concurrent_req%", "%global_max_concurrent_req%",
        "%timeout%", "%erreur de connexion%",
        "%erreur http 500%", "%erreur http 502%",
        "%erreur http 503%", "%erreur http 504%",
        "%non concluante%",
    ]
    with _conn() as conn, conn.cursor() as cur:
        cur.execute(
            "DELETE FROM vies_scope_cache WHERE scope_id=%s AND checked_at < %s",
            (scope_id, cutoff),
        )
        deleted = cur.rowcount
        for pat in transient_patterns:
            cur.execute(
                "DELETE FROM vies_scope_cache WHERE scope_id=%s AND LOWER(error) LIKE %s",
                (scope_id, pat),
            )
            deleted += cur.rowcount
        conn.commit()
    if deleted:
        logger.info("Cache VIES [%s] : %d entrée(s) purgée(s).", scope_id, deleted)
    return deleted


def purge_malformed_entries() -> int:
    """Purge administrative, une fois par session (appelée depuis app.py) :
    supprime les entrées vat_id mal préfixées par un bug historique (double
    préfixe pays, ex. "DEIT123..."). Opère sur les DEUX tables (scope +
    global) car le bug était antérieur à la scopisation."""
    _EU_CC = {"AT", "BE", "BG", "HR", "CY", "CZ", "DK", "EE", "FI", "FR", "DE", "GR", "HU",
              "IE", "IT", "LV", "LT", "LU", "MT", "NL", "PL", "PT", "RO", "SK", "SI", "ES", "SE"}
    deleted = 0
    with _conn() as conn, conn.cursor() as cur:
        for table in ("vies_global_cache", "vies_scope_cache"):
            cur.execute(f"SELECT DISTINCT vat_id FROM {table}")
            to_delete = [
                r[0] for r in cur.fetchall()
                if len(r[0]) >= 4 and r[0][:2].upper() in _EU_CC
                and r[0][2:4].upper() in _EU_CC and r[0][:2].upper() != r[0][2:4].upper()
            ]
            for vat_id in to_delete:
                cur.execute(f"DELETE FROM {table} WHERE vat_id=%s", (vat_id,))
                deleted += cur.rowcount
        conn.commit()
    return deleted


def purge_expired_global_cache() -> int:
    """Purge administrative du cache global mutualisé (pas exposée dans
    l'UI Streamlit standard — appel manuel/CLI si besoin)."""
    cutoff = _now_utc() - timedelta(days=CACHE_TTL_DAYS)
    with _conn() as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM vies_global_cache WHERE checked_at < %s", (cutoff,))
        deleted = cur.rowcount
        conn.commit()
    return deleted


def get_cache_stats(scope_id: str) -> dict:
    """Statistiques pour l'affichage app.py : compteurs du scope courant
    + taille du cache global mutualisé (lecture seule, jamais modifié par
    les actions du scope)."""
    cutoff = _now_utc() - timedelta(days=CACHE_TTL_DAYS)
    with _conn() as conn, conn.cursor() as cur:
        cur.execute("""
            SELECT COUNT(*),
                   COUNT(*) FILTER (WHERE valid),
                   COUNT(*) FILTER (WHERE checked_at < %s),
                   MIN(checked_at), MAX(checked_at)
            FROM vies_scope_cache WHERE scope_id=%s
        """, (cutoff, scope_id))
        total, valid, expired, oldest, newest = cur.fetchone()

        cur.execute("SELECT COUNT(*) FROM vies_global_cache")
        global_total = cur.fetchone()[0]

        cur.execute("""
            SELECT COUNT(*), COUNT(*) FILTER (WHERE is_valid)
            FROM vies_manual_overrides WHERE scope_id=%s
        """, (scope_id,))
        manual_total, manual_valid = cur.fetchone()

    total = total or 0
    valid = valid or 0
    expired = expired or 0
    manual_total = manual_total or 0
    manual_valid = manual_valid or 0
    return {
        "total": total,
        "valid": valid,
        "invalid": total - valid,
        "expired": expired,
        "fresh": total - expired,
        "oldest_check": oldest.isoformat() if oldest else None,
        "newest_check": newest.isoformat() if newest else None,
        "ttl_days": CACHE_TTL_DAYS,
        "manual_total": manual_total,
        "manual_valid": manual_valid,
        "manual_invalid": manual_total - manual_valid,
        "global_total": global_total,
    }


# ---------------------------------------------------------------------------
# Erreurs transitoires
# ---------------------------------------------------------------------------

_TRANSIENT_ERRORS = {
    "ms_unavailable", "service_unavailable", "ms_max_concurrent_req",
    "global_max_concurrent_req", "timeout", "erreur de connexion",
    "erreur http 500", "erreur http 502", "erreur http 503", "erreur http 504",
    "non concluante",
}


def _is_transient(error: Optional[str]) -> bool:
    return any(t in (error or "").lower() for t in _TRANSIENT_ERRORS)


def _is_empty_response(res: ViesResult) -> bool:
    """Réponse VIES "vide" : valid=False, sans nom/adresse, sans erreur."""
    return (
        not res.valid
        and not res.error
        and not res.name.strip()
        and not res.address.strip()
    )


def _is_unreliable(res: ViesResult) -> bool:
    """Résultat non définitif (erreur transitoire explicite)."""
    return _is_transient(res.error)


def _is_downgrade(previous: ViesResult, new_result: ViesResult) -> bool:
    """Détecte un downgrade suspect : numéro précédemment VALIDE qui revient
    soudainement vide sans erreur (dégradation serveur VIES sous charge)."""
    return (
        previous.valid
        and not new_result.valid
        and not new_result.error
    )


# ---------------------------------------------------------------------------
# Normalisation des numéros de TVA
# ---------------------------------------------------------------------------

def _clean_vat_number(raw: str) -> tuple[str, str]:
    cleaned = re.sub(r"[\s.\-]", "", raw.strip())
    if len(cleaned) < 3:
        raise ValueError(f"Numero de TVA trop court : {raw}")
    return cleaned[:2].upper(), cleaned[2:].upper()


def _normalize_vat_id(raw: str) -> str:
    cc, num = _clean_vat_number(raw)
    return f"{cc}{num}"


def normalize_full_vat(buyer_vat: str, buyer_country: str) -> str:
    """Normalise un numéro de TVA au format VIES complet : CC + numéro.

    Le préfixe pays (2 lettres) n'est ajouté que s'il est absent ET que
    le numéro n'a pas déjà un préfixe pays EU reconnu.

    Cas particuliers :
      - Espagne (NIF/CIF) : "B71547129" commence par une lettre mais ce
        n'est pas un préfixe pays → on ajoute "ES".
      - Italie : 11 chiffres, pas de préfixe → on ajoute "IT".
      - Luxembourg vers BE/DE : "LU24104331" commence par "LU" (préfixe
        EU valide ≠ pays destination) → on NE préfixe PAS avec "BE"/"DE",
        on laisse "LU24104331" tel quel pour VIES.

    Règle : si les 2 premiers caractères du numéro sont un code pays EU
    reconnu, on utilise ce préfixe natif, pas buyer_country.

    Normalisation : EL → GR (Grèce), UK → GB (Royaume-Uni pré-Brexit).

    Fonction canonique (référence unique) — importée par engine.py.
    """
    _ALIASES = {"EL": "GR", "UK": "GB"}
    EU_CC = {
        "AT", "BE", "BG", "HR", "CY", "CZ", "DK", "EE", "FI", "FR", "DE", "GR", "HU",
        "IE", "IT", "LV", "LT", "LU", "MT", "NL", "PL", "PT", "RO", "SK", "SI", "ES", "SE",
    }
    raw = buyer_vat.strip().upper()
    clean = raw.replace(" ", "").replace("-", "").replace(".", "")
    if not clean:
        return clean

    if clean[:2] in _ALIASES:
        clean = _ALIASES[clean[:2]] + clean[2:]

    if clean[:2] in EU_CC:
        return clean

    cc = buyer_country.strip().upper() if buyer_country else ""
    cc = _ALIASES.get(cc, cc)
    if cc:
        return f"{cc}{clean}"
    return clean


# ---------------------------------------------------------------------------
# Appel VIES (inchangé — pur appel réseau, aucune notion de scope ici)
# ---------------------------------------------------------------------------

def check_vat(country_code: str, vat_number: str, timeout: int = DEFAULT_TIMEOUT) -> ViesResult:
    """Interroge l'API REST officielle de la Commission Européenne pour un numéro."""
    payload = {
        "countryCode": country_code.upper(),
        "vatNumber": vat_number.upper()
    }
    req_data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        VIES_REST_URL,
        data=req_data,
        headers={"Content-Type": "application/json", "User-Agent": "Mozilla/5.0"}
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            raw_body = response.read().decode("utf-8")
            res_data = json.loads(raw_body)

            if "error" in res_data:
                return ViesResult(
                    valid=False, country_code=country_code, vat_number=vat_number,
                    error=res_data["error"].get("errorMsg", "Erreur API inconnue")
                )

            result = ViesResult(
                valid=res_data.get("valid", res_data.get("isValid", False)),
                country_code=res_data.get("countryCode", country_code),
                vat_number=res_data.get("vatNumber", vat_number),
                name=res_data.get("name", ""),
                address=res_data.get("address", ""),
            )

            if (not result.valid and not result.name and not result.address
                    and "valid" not in res_data and "isValid" not in res_data):
                logger.warning(
                    "VIES réponse sans clé 'valid'/'isValid' pour %s%s — corps brut : %s",
                    country_code, vat_number, raw_body[:500],
                )
            return result

    except urllib.error.HTTPError as exc:
        err_msg = f"Erreur HTTP {exc.code}"
        try:
            body = exc.read().decode("utf-8")
            if body:
                err_json = json.loads(body)
                if "error" in err_json:
                    err_msg = err_json["error"].get("errorMsg", err_msg)
        except Exception:
            pass
        return ViesResult(valid=False, country_code=country_code, vat_number=vat_number, error=err_msg)
    except (urllib.error.URLError, TimeoutError) as exc:
        return ViesResult(valid=False, country_code=country_code, vat_number=vat_number,
                          error=f"Erreur de connexion / Timeout : {exc}")
    except Exception as exc:
        return ViesResult(valid=False, country_code=country_code, vat_number=vat_number, error=str(exc))


def check_vat_with_retry(
    country_code: str,
    vat_number: str,
    timeout: int = DEFAULT_TIMEOUT,
    max_attempts: int = _RETRY_MAX_ATTEMPTS,
    base_delay: float = _RETRY_BASE_DELAY,
) -> ViesResult:
    """Appelle check_vat avec retry backoff exponentiel sur erreurs transitoires."""
    delay = base_delay
    last_result: Optional[ViesResult] = None
    for attempt in range(1, max_attempts + 1):
        result = check_vat(country_code, vat_number, timeout=timeout)
        if not _is_unreliable(result) and not _is_empty_response(result):
            return result
        last_result = result
        if attempt < max_attempts:
            reason = result.error if result.error else "réponse vide/ambiguë"
            logger.warning(
                "VIES réponse non concluante %s%s (tentative %d/%d, attente %.1fs) : %s",
                country_code, vat_number, attempt, max_attempts, delay, reason,
            )
            time.sleep(delay)
            delay *= 2
    logger.warning(
        "VIES : %d tentatives épuisées pour %s%s — résultat non-conclusif conservé.",
        max_attempts, country_code, vat_number,
    )
    return last_result  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# Validation unitaire avec cache scope → global → API
# ---------------------------------------------------------------------------

def check_vat_raw(scope_id: str, raw: str, timeout: int = DEFAULT_TIMEOUT) -> ViesResult:
    """Validation d'un numéro unique via la cascade scope → global → API."""
    try:
        norm = _normalize_vat_id(raw)
    except ValueError as exc:
        return ViesResult(valid=False, country_code="", vat_number=raw, error=str(exc))

    # 1) Cache privé du scope, frais
    cached, is_fresh = _db_get_scope(scope_id, norm)
    if cached is not None and is_fresh:
        return cached

    # 2) Cache global mutualisé, frais — copié dans le scope + historisé
    #    pour CE scope (mutualisation, mais preuve d'audit propre au compte).
    global_cached, global_fresh = _db_get_global(norm)
    if global_cached is not None and global_fresh:
        _db_set_scope(scope_id, norm, global_cached, log_history=True)
        return global_cached

    if cached is not None and not is_fresh:
        logger.info("Cache VIES [%s] : %s expiré (TTL=%dj), revalidation.", scope_id, norm, CACHE_TTL_DAYS)

    # 3) API VIES
    try:
        cc, num = _clean_vat_number(raw)
        res = check_vat_with_retry(cc, num, timeout=timeout)

        if _is_unreliable(res):
            fallback = cached if cached is not None else global_cached
            if fallback is not None:
                logger.info("VIES instable pour %s — résultat en cache conservé.", norm)
                return fallback
            return res

        if cached is not None and _is_downgrade(cached, res):
            logger.warning(
                "VIES : %s précédemment VALIDE reçoit une réponse vide — "
                "résultat ignoré, ancienne valeur conservée.", norm,
            )
            return cached

        # Vérification automatique fiable → mutualisée dans le cache global
        # ET dans le cache privé du scope (jamais l'inverse pour les overrides
        # manuels, voir set_manual_override).
        _db_set_global(norm, res)
        _db_set_scope(scope_id, norm, res)
        return res
    except Exception as exc:
        return ViesResult(valid=False, country_code="", vat_number=raw, error=str(exc))


# ---------------------------------------------------------------------------
# Validation en lot parallèle avec cache scope → global → API
# ---------------------------------------------------------------------------

def validate_vat_numbers_parallel(
    scope_id: str,
    vat_ids: list[str],
    max_workers: int = 25,
    timeout: int = DEFAULT_TIMEOUT,
    progress_callback=None,
) -> dict[str, ViesResult]:
    """Valide plusieurs numéros de TVA en parallèle.

    Logique :
      1. Numéros frais dans le cache du SCOPE → réponse immédiate.
      2. Sinon, frais dans le cache GLOBAL mutualisé → copié dans le scope
         (avec entrée d'historique propre au scope) → réponse immédiate.
      3. Sinon → requête VIES parallèle. Résultat fiable → écrit dans le
         cache global ET dans le cache du scope.
      4. Erreurs transitoires → repli sur la meilleure entrée en cache
         disponible (scope expiré, sinon global), sinon inconclusif.

    Args:
        progress_callback: optionnel, callable(done: int, total: int)
            appelé après chaque numéro traité (cache immédiat compris),
            depuis le thread principal — sûr à utiliser avec les widgets
            Streamlit (st.progress, etc.) appelés par app.py.
    """
    to_fetch: dict[str, str] = {}
    results: dict[str, ViesResult] = {}
    fallback_cache: dict[str, ViesResult] = {}  # secours si VIES instable

    total = len(vat_ids)
    done = 0

    def _tick(n: int = 1):
        nonlocal done
        done += n
        if progress_callback is not None:
            try:
                progress_callback(done, total)
            except Exception:
                pass

    # --- Normalisation ---
    norm_map: dict[str, str] = {}  # vat_id original -> normalisé
    for vat_id in vat_ids:
        try:
            norm_map[vat_id] = _normalize_vat_id(vat_id)
        except ValueError:
            results[vat_id] = ViesResult(
                valid=False, country_code="", vat_number=vat_id,
                error="Normalisation impossible"
            )
            _tick()

    all_norms = list(norm_map.values())

    # --- Phase 1 : DEUX requêtes batch au lieu de 2×N requêtes séquentielles ---
    scope_cache_map = _db_get_scope_batch(scope_id, all_norms)
    global_cache_map = _db_get_global_batch(all_norms)

    to_copy_from_global: list[tuple[str, ViesResult]] = []

    for vat_id, norm in norm_map.items():
        scope_entry = scope_cache_map.get(norm)
        if scope_entry is not None and scope_entry[1]:  # (result, fresh)
            results[vat_id] = scope_entry[0]
            _tick()
            continue

        global_entry = global_cache_map.get(norm)
        if global_entry is not None and global_entry[1]:
            results[vat_id] = global_entry[0]
            to_copy_from_global.append((norm, global_entry[0]))
            _tick()
            continue

        if scope_entry is not None:
            fallback_cache[norm] = scope_entry[0]
            logger.debug("Cache VIES [%s] expiré pour %s, revalidation.", scope_id, norm)
        elif global_entry is not None:
            fallback_cache[norm] = global_entry[0]

        to_fetch[norm] = vat_id

    # Une seule requête pour copier tous les hits du cache global vers le scope
    # (+ historique) au lieu d'une requête par numéro.
    if to_copy_from_global:
        _db_set_scope_batch(scope_id, to_copy_from_global, log_history=True)

    # --- Phase 2 : requêtes réseau parallèles pour les numéros à revalider ---
    batch_results: dict[str, ViesResult] = {}

    if to_fetch:
        def _check_one(item: tuple[str, str]) -> tuple[str, ViesResult]:
            norm_id, orig = item
            country_code, number = _clean_vat_number(orig)
            result = check_vat_with_retry(country_code, number, timeout=timeout)
            return norm_id, result

        workers = min(max_workers, len(to_fetch))
        with ThreadPoolExecutor(max_workers=workers) as pool_exec:
            futures = {pool_exec.submit(_check_one, item): item for item in to_fetch.items()}
            for future in as_completed(futures):
                norm_id, result = future.result()
                batch_results[norm_id] = result
                _tick()

        # --- Détection dégradation globale du serveur VIES ---
        invalid_results = [r for r in batch_results.values() if not r.valid]
        empty_results = [r for r in invalid_results if _is_empty_response(r)]
        _EMPTY_RATIO_THRESHOLD = 0.3
        server_degraded = (
            len(invalid_results) >= 3
            and len(empty_results) / len(invalid_results) > _EMPTY_RATIO_THRESHOLD
        )
        if server_degraded:
            logger.warning(
                "VIES : serveur dégradé détecté (%d/%d réponses invalides vides). "
                "Ces résultats ne sont pas mis en cache.",
                len(empty_results), len(invalid_results),
            )

        # --- Phase 3 : classification, puis DEUX écritures batch au lieu de
        #     2×N écritures séquentielles ---
        to_write_global: list[tuple[str, ViesResult]] = []
        to_write_scope: list[tuple[str, ViesResult]] = []

        for norm_id, result in batch_results.items():
            orig_id = to_fetch[norm_id]

            if _is_unreliable(result):
                fb = fallback_cache.get(norm_id)
                if fb is not None:
                    logger.info(
                        "VIES instable pour %s — entrée en cache utilisée comme repli.", norm_id,
                    )
                    results[orig_id] = fb
                else:
                    results[orig_id] = ViesResult(
                        valid=False, country_code=result.country_code,
                        vat_number=result.vat_number,
                        error=result.error or "Réponse VIES non concluante (à revérifier)",
                    )
                continue

            if server_degraded and _is_empty_response(result):
                fb = fallback_cache.get(norm_id)
                if fb is not None:
                    results[orig_id] = fb
                else:
                    results[orig_id] = ViesResult(
                        valid=False, country_code=result.country_code,
                        vat_number=result.vat_number,
                        error="Réponse VIES non concluante (à revérifier)",
                    )
                continue

            prev = fallback_cache.get(norm_id)
            if prev is not None and _is_downgrade(prev, result):
                logger.warning(
                    "VIES : %s précédemment VALIDE reçoit une réponse vide — "
                    "résultat ignoré, ancienne valeur conservée.", norm_id,
                )
                results[orig_id] = prev
                continue

            to_write_global.append((norm_id, result))
            to_write_scope.append((norm_id, result))
            results[orig_id] = result

        # Deux allers-retours réseau pour tout le lot, au lieu de 2×N.
        _db_set_global_batch(to_write_global)
        _db_set_scope_batch(scope_id, to_write_scope, log_history=True)

    return results


def validate_vat_numbers(
    scope_id: str,
    vat_ids: list[str],
    timeout: int = DEFAULT_TIMEOUT,
    progress_callback=None,
) -> dict[str, ViesResult]:
    """Compatibilité descendante (version séquentielle-friendly, même cascade)."""
    return validate_vat_numbers_parallel(
        scope_id, vat_ids, max_workers=10, timeout=timeout, progress_callback=progress_callback
    )


# ---------------------------------------------------------------------------
# Utilitaires d'administration (appelables depuis app.py)
# ---------------------------------------------------------------------------

def purge_expired_cache(scope_id: str) -> int:
    """Purge manuellement les entrées expirées DU SCOPE COURANT.

    N'affecte jamais le cache global mutualisé — voir
    purge_expired_global_cache() pour une purge administrative globale.
    """
    return _db_delete_expired_scope(scope_id)


def force_revalidate(scope_id: str, vat_ids: list[str]) -> None:
    """Force la revalidation de numéros spécifiques pour CE scope, en
    supprimant leur entrée du cache privé du scope. N'affecte pas le cache
    global (un autre scope continuera de bénéficier de la valeur mutualisée
    tant qu'elle est fraîche)."""
    with _conn() as conn, conn.cursor() as cur:
        for vat_id in vat_ids:
            try:
                norm = _normalize_vat_id(vat_id)
            except ValueError:
                continue
            cur.execute(
                "DELETE FROM vies_scope_cache WHERE scope_id=%s AND vat_id=%s",
                (scope_id, norm),
            )
        conn.commit()
    logger.info("Revalidation forcée [%s] pour %d numéro(s).", scope_id, len(vat_ids))


# ---------------------------------------------------------------------------
# Classification manuelle des numéros non vérifiables (inconclusifs)
# ---------------------------------------------------------------------------
# Table vies_manual_overrides, clé (scope_id, full_vat). Ces classifications
# sont volontairement exclues de toute mutualisation : elles ne sont JAMAIS
# écrites dans vies_global_cache, et un scope ne voit jamais les overrides
# d'un autre scope.
# ---------------------------------------------------------------------------

def set_manual_override(scope_id: str, full_vat: str, valid: bool) -> None:
    """Enregistre une classification manuelle pour un numéro TVA inconclusif,
    strictement dans le scope courant.

    Args:
        scope_id: portée du compte/domaine appelant (jamais partagée).
        full_vat: numéro complet normalisé (ex: "DE123456789").
        valid:    True → considéré valide (B2B, autoliquidation) ;
                  False → considéré invalide (B2C, TVA OSS due).
    """
    with _conn() as conn, conn.cursor() as cur:
        cur.execute("""
            INSERT INTO vies_manual_overrides (scope_id, full_vat, is_valid, set_at)
            VALUES (%s,%s,%s,%s)
            ON CONFLICT (scope_id, full_vat) DO UPDATE SET
                is_valid=EXCLUDED.is_valid, set_at=EXCLUDED.set_at
        """, (scope_id, full_vat.upper().strip(), valid, _now_utc()))
        conn.commit()
    logger.info("Override manuel VIES [%s] : %s → %s", scope_id, full_vat,
                "VALIDE" if valid else "INVALIDE")


def get_manual_overrides(scope_id: str, include_expired: bool = False) -> dict[str, bool]:
    """Retourne les overrides manuels du scope courant.

    Args:
        scope_id: portée du compte/domaine appelant.
        include_expired: si False (par défaut), exclut les overrides dont
            `set_at` dépasse CACHE_TTL_DAYS — même condition d'âge que
            l'expiration du cache VIES classique. Passer True pour
            l'affichage en UI (liste des overrides, y compris expirés, pour
            pouvoir les revalider ou les supprimer).

    Returns:
        Dict ``{full_vat: is_valid}`` scopé au compte/domaine appelant.
    """
    try:
        with _conn() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT full_vat, is_valid, set_at FROM vies_manual_overrides WHERE scope_id=%s",
                (scope_id,),
            )
            rows = cur.fetchall()
    except Exception:
        return {}
    if include_expired:
        return {r[0]: bool(r[1]) for r in rows}
    result: dict[str, bool] = {}
    for full_vat, is_valid, set_at in rows:
        if _is_expired(set_at):
            logger.info(
                "Override manuel VIES [%s] expiré (> %d j), ignoré au calcul : %s.",
                scope_id, CACHE_TTL_DAYS, full_vat,
            )
            continue
        result[full_vat] = bool(is_valid)
    return result


def clear_manual_overrides(scope_id: str) -> None:
    """Supprime tous les overrides manuels DU SCOPE COURANT (bouton
    Réinitialiser dans app.py). N'affecte pas les autres scopes."""
    try:
        with _conn() as conn, conn.cursor() as cur:
            cur.execute("DELETE FROM vies_manual_overrides WHERE scope_id=%s", (scope_id,))
            conn.commit()
        logger.info("Overrides manuels VIES supprimés pour le scope [%s].", scope_id)
    except Exception:
        pass


def delete_manual_override(scope_id: str, full_vat: str) -> None:
    """Supprime l'override manuel d'un seul numéro TVA, dans le scope courant."""
    try:
        with _conn() as conn, conn.cursor() as cur:
            cur.execute(
                "DELETE FROM vies_manual_overrides WHERE scope_id=%s AND full_vat=%s",
                (scope_id, full_vat.upper().strip()),
            )
            conn.commit()
        logger.info("Override manuel VIES [%s] supprimé : %s", scope_id, full_vat)
    except Exception as exc:
        logger.warning("Erreur suppression override [%s] %s : %s", scope_id, full_vat, exc)
        raise


def get_manual_overrides_full(scope_id: str) -> list[tuple[str, bool, str]]:
    """Overrides manuels du scope courant avec leur date, pour l'affichage UI.

    Returns:
        Liste de tuples ``(full_vat, is_valid, set_at)`` triés du plus récent
        au plus ancien. ``set_at`` est une chaîne ISO 8601 UTC.
    """
    try:
        with _conn() as conn, conn.cursor() as cur:
            cur.execute("""
                SELECT full_vat, is_valid, set_at FROM vies_manual_overrides
                WHERE scope_id=%s ORDER BY set_at DESC
            """, (scope_id,))
            rows = cur.fetchall()
        return [(r[0], bool(r[1]), r[2].isoformat() if r[2] else "") for r in rows]
    except Exception:
        return []