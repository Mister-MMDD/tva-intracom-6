"""Verification des numeros de TVA intracommunautaire via le service VIES.

Optimisations :
  - validate_vat_numbers_parallel() : N numéros en parallèle (ThreadPoolExecutor)
  - Cache SQLite persistant avec TTL configurable : les numéros vérifiés sont
    sauvegardés dans une base SQLite locale avec leur date de dernière vérification.
    Les entrées sont automatiquement revalidées après CACHE_TTL_DAYS jours.
  - Migration automatique depuis l'ancien cache JSON si présent.
  - Vitesse absolue : après le premier traitement, les lancements suivants sont
    quasi-instantanés pour les numéros encore frais.
"""

from __future__ import annotations

import json
import logging
import os
import re
import sqlite3
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

logger = logging.getLogger(__name__)

VIES_REST_URL = "https://ec.europa.eu/taxation_customs/vies/rest-api/check-vat-number"
DEFAULT_TIMEOUT = 10

from pathlib import Path as _Path

# Chemin de la base SQLite (remplace l'ancien vies_disk_cache.json)
# Résolu de manière absolue par rapport à l'emplacement de ce fichier,
# pour éviter que le CWD au moment du lancement change l'emplacement réel.
CACHE_DB_FILE = str(_Path(__file__).parent / "vies_cache.db")
# Ancien fichier JSON : migré automatiquement au premier démarrage puis ignoré
_LEGACY_JSON_FILE = str(_Path(__file__).parent / "vies_disk_cache.json")

# TTL du cache : durée en jours avant qu'un numéro soit revalidé auprès de VIES.
# Valeur par défaut : 90 jours. Modifiable via set_cache_ttl().
CACHE_TTL_DAYS: int = 90

# Retry backoff pour erreurs temporaires VIES (serveur UE instable)
_RETRY_MAX_ATTEMPTS = 3
_RETRY_BASE_DELAY   = 1.0   # secondes, doublé à chaque tentative (1 → 2 → 4)

# Verrou global pour les accès SQLite depuis plusieurs threads
_db_lock = threading.Lock()   # conservé pour rétrocompatibilité interne
# En mode WAL, chaque thread possède sa propre connexion (threading.local) et
# SQLite garantit la cohérence des lectures concurrentes sans verrou applicatif.
# Seules les écritures (INSERT/UPDATE/DELETE + COMMIT) nécessitent une sérialisation
# pour éviter les erreurs "database is locked" entre threads workers.
_write_lock = threading.Lock()


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
# Helpers TTL
# ---------------------------------------------------------------------------

def set_cache_ttl(days: int) -> None:
    """Modifie le TTL du cache VIES (en jours). Appel optionnel depuis app.py."""
    global CACHE_TTL_DAYS
    CACHE_TTL_DAYS = max(1, int(days))
    logger.info("Cache VIES : TTL mis à jour à %d jours.", CACHE_TTL_DAYS)


def _now_utc() -> str:
    """Timestamp ISO UTC actuel, stocké tel quel dans SQLite."""
    return datetime.now(timezone.utc).isoformat()


def _is_expired(checked_at_iso: str) -> bool:
    """Retourne True si l'entrée dépasse le TTL configuré."""
    try:
        checked_at = datetime.fromisoformat(checked_at_iso)
        # Assure la comparaison timezone-aware
        if checked_at.tzinfo is None:
            checked_at = checked_at.replace(tzinfo=timezone.utc)
        return datetime.now(timezone.utc) - checked_at > timedelta(days=CACHE_TTL_DAYS)
    except (ValueError, TypeError):
        return True  # date invalide → considérée expirée


# ---------------------------------------------------------------------------
# Initialisation SQLite
# ---------------------------------------------------------------------------

_local = threading.local()


def _get_connection() -> sqlite3.Connection:
    """Retourne la connexion SQLite du thread courant (une par thread, réutilisée).

    Stratégie :
      - threading.local() : chaque thread possède sa propre connexion → pas de
        contention entre threads lors des lectures parallèles (phase 1 du batch).
      - WAL (Write-Ahead Logging) : les lectures ne bloquent pas les écritures et
        vice-versa. Activé une seule fois à la création de la connexion.
      - check_same_thread=False : requis car sqlite3 vérifie l'origine du thread
        par défaut ; ici la sécurité est assurée par threading.local + _db_lock.
    """
    conn = getattr(_local, "conn", None)
    if conn is None:
        conn = sqlite3.connect(
            CACHE_DB_FILE,
            check_same_thread=False,
            timeout=30,  # délai d'attente max si la DB est verrouillée (multi-processus)
        )
        conn.row_factory = sqlite3.Row
        # WAL : lectures et écritures en parallèle sans blocage mutuel.
        # Crucial pour le ThreadPoolExecutor (25 threads) qui lit le cache en phase 1.
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")   # durabilité suffisante, moins d'I/O
        _local.conn = conn
    return conn


def _close_thread_connection() -> None:
    """Ferme et supprime la connexion du thread courant (fin de thread worker).

    À appeler via un wrapper dans validate_vat_numbers_parallel() pour que
    les threads du ThreadPoolExecutor libèrent leurs connexions proprement.
    """
    conn = getattr(_local, "conn", None)
    if conn is not None:
        try:
            conn.close()
        except Exception:
            pass
        _local.conn = None


def _init_db() -> None:
    """Crée les tables et index si ils n'existent pas encore."""
    with _write_lock:
        conn = _get_connection()
        conn.execute("""
            CREATE TABLE IF NOT EXISTS vies_cache (
                vat_id        TEXT PRIMARY KEY,   -- numéro normalisé (ex: FR12345678901)
                valid         INTEGER NOT NULL,   -- 1=valide, 0=invalide
                country_code  TEXT NOT NULL,
                vat_number    TEXT NOT NULL,
                name          TEXT DEFAULT '',
                address       TEXT DEFAULT '',
                error         TEXT DEFAULT '',
                checked_at    TEXT NOT NULL       -- ISO UTC timestamp
            )
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_checked_at ON vies_cache(checked_at)
        """)
        # ------------------------------------------------------------------
        # Historique append-only des vérifications VIES.
        #
        # vies_cache (ci-dessus) est un cache TTL : chaque revérification
        # ÉCRASE l'entrée précédente (UPSERT). Pour la piste d'audit fiscale,
        # on a besoin de l'inverse : savoir ce que VIES répondait à une date
        # donnée dans le passé, même après une revérification ultérieure
        # qui aurait changé le statut (ex: client valide en janvier, radié
        # en décembre — il faut pouvoir prouver qu'au moment de la vente de
        # janvier, le numéro était bien valide selon VIES).
        #
        # Chaque appel à _db_set() insère une ligne ici en plus de l'UPSERT
        # dans vies_cache — jamais de DELETE ni d'UPDATE sur cette table.
        # ------------------------------------------------------------------
        conn.execute("""
            CREATE TABLE IF NOT EXISTS vies_check_history (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                vat_id        TEXT NOT NULL,
                valid         INTEGER NOT NULL,
                country_code  TEXT NOT NULL,
                vat_number    TEXT NOT NULL,
                name          TEXT DEFAULT '',
                address       TEXT DEFAULT '',
                error         TEXT DEFAULT '',
                checked_at    TEXT NOT NULL       -- ISO UTC timestamp de CETTE vérification
            )
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_history_vat_id ON vies_check_history(vat_id, checked_at)
        """)
        conn.commit()


def _row_to_result(row: sqlite3.Row) -> ViesResult:
    return ViesResult(
        valid=bool(row["valid"]),
        country_code=row["country_code"],
        vat_number=row["vat_number"],
        name=row["name"] or "",
        address=row["address"] or "",
        error=row["error"] or "",
    )


def _db_get(vat_id: str) -> tuple[ViesResult | None, bool]:
    """Récupère une entrée du cache.

    Retourne (result, is_fresh) :
      - result=None si absent
      - is_fresh=False si présent mais expiré (TTL dépassé)

    Pas de verrou : en mode WAL, chaque thread a sa propre connexion
    (threading.local) et SQLite garantit la cohérence des lectures concurrentes.
    """
    conn = _get_connection()
    row = conn.execute(
        "SELECT * FROM vies_cache WHERE vat_id = ?", (vat_id,)
    ).fetchone()

    if row is None:
        return None, False
    result = _row_to_result(row)
    fresh = not _is_expired(row["checked_at"])
    return result, fresh


def _db_set(vat_id: str, result: ViesResult) -> None:
    """Insère ou met à jour une entrée dans le cache SQLite, et journalise
    la vérification dans l'historique append-only (piste d'audit)."""
    with _write_lock:
        conn = _get_connection()
        checked_at = _now_utc()
        conn.execute("""
            INSERT INTO vies_cache
                (vat_id, valid, country_code, vat_number, name, address, error, checked_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(vat_id) DO UPDATE SET
                valid        = excluded.valid,
                country_code = excluded.country_code,
                vat_number   = excluded.vat_number,
                name         = excluded.name,
                address      = excluded.address,
                error        = excluded.error,
                checked_at   = excluded.checked_at
        """, (
            vat_id,
            1 if result.valid else 0,
            result.country_code,
            result.vat_number,
            result.name,
            result.address,
            result.error,
            checked_at,
        ))
        conn.execute("""
            INSERT INTO vies_check_history
                (vat_id, valid, country_code, vat_number, name, address, error, checked_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            vat_id,
            1 if result.valid else 0,
            result.country_code,
            result.vat_number,
            result.name,
            result.address,
            result.error,
            checked_at,
        ))
        conn.commit()


def get_vies_history(full_vat: str) -> list[dict]:
    """Retourne tout l'historique des vérifications VIES pour un numéro,
    de la plus ancienne à la plus récente — pour preuve de bonne foi en cas
    de contrôle fiscal (chaque vérification, jamais écrasée ni supprimée).
    """
    conn = _get_connection()
    rows = conn.execute(
        "SELECT * FROM vies_check_history WHERE vat_id = ? ORDER BY checked_at ASC",
        (full_vat,),
    ).fetchall()
    return [
        {
            "checked_at": row["checked_at"],
            "valid": bool(row["valid"]),
            "country_code": row["country_code"],
            "vat_number": row["vat_number"],
            "name": row["name"] or "",
            "address": row["address"] or "",
            "error": row["error"] or "",
        }
        for row in rows
    ]


def get_vies_status_as_of(full_vat: str, as_of_date_iso: str) -> dict | None:
    """Retourne le statut VIES tel qu'il était connu À UNE DATE DONNÉE
    (ex: la date d'une vente), en cherchant la dernière vérification
    antérieure ou égale à `as_of_date_iso` (format 'YYYY-MM-DD' ou ISO complet).

    Utile pour justifier une exonération B2B lors d'un contrôle portant sur
    une vente ancienne, même si le numéro a été revérifié depuis avec un
    statut différent (ex: client radié après la vente).

    Retourne None si aucune vérification n'a été faite avant cette date —
    dans ce cas, le statut au moment de la vente n'est pas prouvable et le
    statut courant ne doit pas être présenté comme rétroactif.
    """
    conn = _get_connection()
    row = conn.execute(
        """
        SELECT * FROM vies_check_history
        WHERE vat_id = ? AND checked_at <= ?
        ORDER BY checked_at DESC LIMIT 1
        """,
        (full_vat, as_of_date_iso),
    ).fetchone()
    if row is None:
        return None
    return {
        "checked_at": row["checked_at"],
        "valid": bool(row["valid"]),
        "country_code": row["country_code"],
        "vat_number": row["vat_number"],
        "name": row["name"] or "",
        "address": row["address"] or "",
        "error": row["error"] or "",
    }


def _db_delete_expired() -> int:
    """Purge les entrées expirées ET les erreurs transitoires stockées par erreur.
    Retourne le nombre de lignes supprimées."""
    cutoff = (
        datetime.now(timezone.utc) - timedelta(days=CACHE_TTL_DAYS)
    ).isoformat()
    with _write_lock:
        conn = _get_connection()
        cur = conn.execute(
            "DELETE FROM vies_cache WHERE checked_at < ?", (cutoff,)
        )
        deleted_ttl = cur.rowcount
        transient_patterns = [
            "%ms_unavailable%", "%service_unavailable%",
            "%ms_max_concurrent_req%", "%global_max_concurrent_req%",
            "%timeout%", "%erreur de connexion%",
            "%erreur http 500%", "%erreur http 502%",
            "%erreur http 503%", "%erreur http 504%",
            "%non concluante%",
        ]
        deleted_transient = 0
        for pat in transient_patterns:
            cur = conn.execute(
                "DELETE FROM vies_cache WHERE LOWER(error) LIKE ?", (pat,)
            )
            deleted_transient += cur.rowcount
        conn.commit()
    total = deleted_ttl + deleted_transient
    if total:
        logger.info(
            "Cache VIES SQLite : %d entrée(s) expirées (TTL), "
            "%d erreur(s) transitoires purgées.",
            deleted_ttl, deleted_transient,
        )
    return total


def get_cache_stats() -> dict:
    """Retourne des statistiques sur le cache SQLite (pour affichage dans app.py)."""
    conn = _get_connection()
    total = conn.execute("SELECT COUNT(*) FROM vies_cache").fetchone()[0]
    valid = conn.execute(
        "SELECT COUNT(*) FROM vies_cache WHERE valid = 1"
    ).fetchone()[0]
    cutoff = (
        datetime.now(timezone.utc) - timedelta(days=CACHE_TTL_DAYS)
    ).isoformat()
    expired = conn.execute(
        "SELECT COUNT(*) FROM vies_cache WHERE checked_at < ?", (cutoff,)
    ).fetchone()[0]
    oldest = conn.execute(
        "SELECT MIN(checked_at) FROM vies_cache"
    ).fetchone()[0]
    newest = conn.execute(
        "SELECT MAX(checked_at) FROM vies_cache"
    ).fetchone()[0]
    try:
        manual_total = conn.execute(
            "SELECT COUNT(*) FROM vies_manual_overrides"
        ).fetchone()[0]
        manual_valid = conn.execute(
            "SELECT COUNT(*) FROM vies_manual_overrides WHERE is_valid = 1"
        ).fetchone()[0]
    except Exception:
        manual_total = 0
        manual_valid = 0
    return {
        "total":          total,
        "valid":          valid,
        "invalid":        total - valid,
        "expired":        expired,
        "fresh":          total - expired,
        "oldest_check":   oldest,
        "newest_check":   newest,
        "ttl_days":       CACHE_TTL_DAYS,
        "manual_total":   manual_total,
        "manual_valid":   manual_valid,
        "manual_invalid": manual_total - manual_valid,
    }


# ---------------------------------------------------------------------------
# Migration JSON → SQLite
# ---------------------------------------------------------------------------

def _migrate_json_to_sqlite() -> int:
    """Importe l'ancien cache JSON dans SQLite si le fichier existe.

    Chaque entrée migrée reçoit un checked_at = maintenant - (TTL/2) pour
    forcer une revalidation progressive dans la moitié du TTL, sans invalider
    d'un coup toute la base. Les erreurs transitoires sont ignorées.

    Retourne le nombre d'entrées migrées.
    """
    if not os.path.exists(_LEGACY_JSON_FILE):
        return 0

    try:
        with open(_LEGACY_JSON_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as exc:
        logger.warning("Migration JSON→SQLite : lecture impossible (%s).", exc)
        return 0

    # Timestamp fictif : moitié du TTL dans le passé pour forcer revalidation douce
    half_ttl_ago = (
        datetime.now(timezone.utc) - timedelta(days=CACHE_TTL_DAYS // 2)
    ).isoformat()

    migrated = 0
    transient_skipped = 0

    with _write_lock:
        conn = _get_connection()
        for vat_id, res in data.items():
            error = res.get("error", "")
            if _is_transient(error):
                transient_skipped += 1
                continue
            conn.execute("""
                INSERT OR IGNORE INTO vies_cache
                    (vat_id, valid, country_code, vat_number, name, address, error, checked_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                vat_id,
                1 if res.get("valid", False) else 0,
                res.get("country_code", ""),
                res.get("vat_number", vat_id),
                res.get("name", ""),
                res.get("address", ""),
                error,
                half_ttl_ago,
            ))
            migrated += 1
        conn.commit()

    if migrated or transient_skipped:
        logger.info(
            "Migration JSON→SQLite : %d numéros importés, %d erreurs transitoires ignorées.",
            migrated, transient_skipped,
        )
        # Renommer le JSON pour éviter de remigrer au prochain démarrage
        try:
            os.rename(_LEGACY_JSON_FILE, _LEGACY_JSON_FILE + ".migrated")
            logger.info("Ancien cache JSON renommé en %s.migrated", _LEGACY_JSON_FILE)
        except OSError as exc:
            logger.warning("Impossible de renommer l'ancien cache JSON : %s", exc)

    return migrated


# ---------------------------------------------------------------------------
# Erreurs transitoires
# ---------------------------------------------------------------------------

_TRANSIENT_ERRORS = {
    "ms_unavailable", "service_unavailable", "ms_max_concurrent_req",
    "global_max_concurrent_req", "timeout", "erreur de connexion",
    "erreur http 500", "erreur http 502", "erreur http 503", "erreur http 504",
    "non concluante",
}


def _is_transient(error: str | None) -> bool:
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
# Initialisation au démarrage du module
# ---------------------------------------------------------------------------

_init_db()
_migrate_json_to_sqlite()
_db_delete_expired()


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
        "AT","BE","BG","HR","CY","CZ","DK","EE","FI","FR","DE","GR","HU",
        "IE","IT","LV","LT","LU","MT","NL","PL","PT","RO","SK","SI","ES","SE",
    }
    raw = buyer_vat.strip().upper()
    clean = raw.replace(" ", "").replace("-", "").replace(".", "")
    if not clean:
        return clean

    # Normaliser les alias connus dans le préfixe du numéro lui-même
    if clean[:2] in _ALIASES:
        clean = _ALIASES[clean[:2]] + clean[2:]

    # Si le numéro commence déjà par un préfixe pays EU reconnu → le garder
    if clean[:2] in EU_CC:
        return clean

    # Sinon ajouter le préfixe du pays de destination (normalisé aussi)
    cc = buyer_country.strip().upper() if buyer_country else ""
    cc = _ALIASES.get(cc, cc)
    if cc:
        return f"{cc}{clean}"
    return clean


# ---------------------------------------------------------------------------
# Appel VIES (inchangé)
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
    last_result: ViesResult | None = None
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
# Validation unitaire avec cache SQLite
# ---------------------------------------------------------------------------

def check_vat_raw(raw: str, timeout: int = DEFAULT_TIMEOUT) -> ViesResult:
    """Validation d'un numéro unique avec gestion du cache SQLite + TTL."""
    try:
        norm = _normalize_vat_id(raw)
    except ValueError as exc:
        return ViesResult(valid=False, country_code="", vat_number=raw, error=str(exc))

    # Cache hit frais
    cached, is_fresh = _db_get(norm)
    if cached is not None and is_fresh:
        return cached

    # Cache hit expiré : on loggue pour info mais on revalide quand même
    if cached is not None and not is_fresh:
        logger.info("Cache VIES : %s expiré (TTL=%dd), revalidation.", norm, CACHE_TTL_DAYS)

    try:
        cc, num = _clean_vat_number(raw)
        res = check_vat_with_retry(cc, num, timeout=timeout)

        if _is_unreliable(res):
            # Erreur transitoire : on retourne le résultat en cache (même expiré)
            # plutôt que de pénaliser le vendeur sur un problème serveur UE.
            if cached is not None:
                logger.info("VIES instable pour %s — résultat en cache conservé.", norm)
                return cached
            return res

        # Protection anti-downgrade
        if cached is not None and _is_downgrade(cached, res):
            logger.warning(
                "VIES : %s précédemment VALIDE reçoit une réponse vide — "
                "résultat ignoré, ancienne valeur conservée.", norm,
            )
            return cached

        _db_set(norm, res)
        return res
    except Exception as exc:
        return ViesResult(valid=False, country_code="", vat_number=raw, error=str(exc))


# ---------------------------------------------------------------------------
# Validation en lot parallèle avec cache SQLite
# ---------------------------------------------------------------------------

def validate_vat_numbers_parallel(
    vat_ids: list[str],
    max_workers: int = 25,
    timeout: int = DEFAULT_TIMEOUT,
) -> dict[str, ViesResult]:
    """Valide plusieurs numéros de TVA en parallèle avec cache SQLite + TTL.

    Logique :
      1. Numéros frais en cache → réponse immédiate (0 requête réseau)
      2. Numéros absents OU expirés → requête VIES parallèle
      3. Résultats fiables → écriture en cache (mise à jour du checked_at)
      4. Erreurs transitoires → résultat en cache expiré si disponible, sinon inconclusif
    """
    to_fetch: dict[str, str] = {}       # norm → raw original
    results: dict[str, ViesResult] = {}
    expired_cache: dict[str, ViesResult] = {}  # fallback si VIES instable

    # --- Phase 1 : tri cache frais / à revalider ---
    for vat_id in vat_ids:
        try:
            norm = _normalize_vat_id(vat_id)
        except ValueError:
            results[vat_id] = ViesResult(
                valid=False, country_code="", vat_number=vat_id,
                error="Normalisation impossible"
            )
            continue

        cached, is_fresh = _db_get(norm)
        if cached is not None and is_fresh:
            results[vat_id] = cached
        else:
            if cached is not None:
                expired_cache[norm] = cached  # fallback en cas d'instabilité VIES
                logger.debug("Cache VIES expiré pour %s, revalidation.", norm)
            to_fetch[norm] = vat_id

    # --- Phase 2 : requêtes réseau parallèles pour les numéros à revalider ---
    batch_results: dict[str, ViesResult] = {}

    if to_fetch:
        def _check_one(item: tuple[str, str]) -> tuple[str, ViesResult]:
            norm_id, orig = item
            try:
                country_code, number = _clean_vat_number(orig)
                result = check_vat_with_retry(country_code, number, timeout=timeout)
                return norm_id, result
            finally:
                # Ferme la connexion SQLite du thread worker après chaque tâche.
                # Les threads du ThreadPoolExecutor sont recyclés : sans cette ligne,
                # chaque thread accumulerait une connexion ouverte indéfiniment.
                _close_thread_connection()

        workers = min(max_workers, len(to_fetch))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(_check_one, item): item for item in to_fetch.items()}
            for future in as_completed(futures):
                norm_id, result = future.result()
                batch_results[norm_id] = result

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

        # --- Phase 3 : écriture cache + construction réponse finale ---
        for norm_id, result in batch_results.items():
            orig_id = to_fetch[norm_id]

            # Erreurs transitoires : jamais en cache
            if _is_unreliable(result):
                # Fallback sur l'entrée expirée si disponible
                fallback = expired_cache.get(norm_id)
                if fallback is not None:
                    logger.info(
                        "VIES instable pour %s — entrée expirée en cache utilisée "
                        "comme fallback.", norm_id,
                    )
                    results[orig_id] = fallback
                else:
                    results[orig_id] = ViesResult(
                        valid=False,
                        country_code=result.country_code,
                        vat_number=result.vat_number,
                        error=result.error or "Réponse VIES non concluante (à revérifier)",
                    )
                continue

            # Réponse vide en contexte de dégradation serveur : pas en cache
            if server_degraded and _is_empty_response(result):
                fallback = expired_cache.get(norm_id)
                if fallback is not None:
                    results[orig_id] = fallback
                else:
                    results[orig_id] = ViesResult(
                        valid=False,
                        country_code=result.country_code,
                        vat_number=result.vat_number,
                        error="Réponse VIES non concluante (à revérifier)",
                    )
                continue

            # Protection anti-downgrade
            prev = expired_cache.get(norm_id)
            if prev is not None and _is_downgrade(prev, result):
                logger.warning(
                    "VIES : %s précédemment VALIDE reçoit une réponse vide — "
                    "résultat ignoré, ancienne valeur conservée.", norm_id,
                )
                results[orig_id] = prev
                # On ne met PAS à jour le cache pour ne pas perdre la valeur valide
                continue

            # Résultat fiable → mise en cache (checked_at rafraîchi)
            _db_set(norm_id, result)
            results[orig_id] = result

    return results


def validate_vat_numbers(
    vat_ids: list[str],
    timeout: int = DEFAULT_TIMEOUT,
) -> dict[str, ViesResult]:
    """Compatibilité descendante."""
    return validate_vat_numbers_parallel(vat_ids, max_workers=10, timeout=timeout)


# ---------------------------------------------------------------------------
# Utilitaires d'administration (appelables depuis app.py)
# ---------------------------------------------------------------------------

def purge_expired_cache() -> int:
    """Purge manuellement les entrées expirées. Retourne le nombre supprimé."""
    return _db_delete_expired()


def force_revalidate(vat_ids: list[str]) -> None:
    """Force la revalidation de numéros spécifiques en supprimant leur entrée en cache."""
    with _write_lock:
        conn = _get_connection()
        for vat_id in vat_ids:
            try:
                norm = _normalize_vat_id(vat_id)
                conn.execute("DELETE FROM vies_cache WHERE vat_id = ?", (norm,))
            except ValueError:
                pass
        conn.commit()
    logger.info("Revalidation forcée pour %d numéro(s).", len(vat_ids))

# ---------------------------------------------------------------------------
# Classification manuelle des numéros non vérifiables (inconclusifs)
# ---------------------------------------------------------------------------
# Stockage dans la même base SQLite que le cache VIES (CACHE_DB_FILE).
# Table : vies_manual_overrides
#   full_vat  TEXT PK  — numéro complet normalisé (ex: "DE123456789")
#   is_valid  INTEGER  — 1 = Valide (B2B exonéré), 0 = Invalide (B2C, TVA due)
#   set_at    TEXT     — timestamp ISO UTC de la saisie
# ---------------------------------------------------------------------------

def _ensure_manual_override_table() -> None:
    """Crée la table des overrides manuels si elle n'existe pas encore."""
    with _write_lock:
        conn = _get_connection()
        conn.execute("""
            CREATE TABLE IF NOT EXISTS vies_manual_overrides (
                full_vat  TEXT PRIMARY KEY,
                is_valid  INTEGER NOT NULL,
                set_at    TEXT NOT NULL
            )
        """)
        conn.commit()


def set_manual_override(full_vat: str, valid: bool) -> None:
    """Enregistre une classification manuelle pour un numéro TVA inconclusif.

    Le résultat est stocké dans la même base SQLite que le cache VIES.
    Il écrase le résultat VIES lors du prochain calcul dans engine.py.

    Args:
        full_vat: numéro complet normalisé (ex: "DE123456789").
        valid:    True → considéré valide (B2B, autoliquidation) ;
                  False → considéré invalide (B2C, TVA OSS due).
    """
    _ensure_manual_override_table()
    with _write_lock:
        conn = _get_connection()
        conn.execute(
            """
            INSERT INTO vies_manual_overrides (full_vat, is_valid, set_at)
            VALUES (?, ?, ?)
            ON CONFLICT(full_vat) DO UPDATE SET
                is_valid = excluded.is_valid,
                set_at   = excluded.set_at
            """,
            (full_vat.upper().strip(), 1 if valid else 0, _now_utc()),
        )
        conn.commit()
    logger.info("Override manuel VIES : %s → %s", full_vat, "VALIDE" if valid else "INVALIDE")


def get_manual_overrides(include_expired: bool = False) -> dict[str, bool]:
    """Retourne les overrides manuels enregistrés.

    Args:
        include_expired: si False (par défaut), exclut les overrides dont
            `set_at` dépasse CACHE_TTL_DAYS — la même condition d'âge que
            celle utilisée pour l'expiration du cache VIES classique
            (_is_expired). Un override posé il y a plus de CACHE_TTL_DAYS
            jours est traité comme n'importe quelle entrée de cache expirée :
            il ne doit plus écraser silencieusement le résultat du moteur.
            Passer True pour l'affichage en UI (ex: liste des overrides dans
            app.py), où l'on veut voir les entrées expirées pour pouvoir les
            revalider ou les supprimer, mais où elles ne doivent pas encore
            être appliquées au calcul.

    Returns:
        Dict ``{full_vat: is_valid}`` — vide si aucun override valide/actif
        ou table absente.
    """
    try:
        _ensure_manual_override_table()
        conn = _get_connection()
        rows = conn.execute(
            "SELECT full_vat, is_valid, set_at FROM vies_manual_overrides"
        ).fetchall()
        if include_expired:
            return {row["full_vat"]: bool(row["is_valid"]) for row in rows}
        result: dict[str, bool] = {}
        for row in rows:
            if _is_expired(row["set_at"]):
                logger.info(
                    "Override manuel VIES expiré (> %d j), ignoré au calcul : %s "
                    "(posé le %s). Repasse en validation VIES normale.",
                    CACHE_TTL_DAYS, row["full_vat"], row["set_at"],
                )
                continue
            result[row["full_vat"]] = bool(row["is_valid"])
        return result
    except Exception:
        return {}


def clear_manual_overrides() -> None:
    """Supprime tous les overrides manuels (bouton Réinitialiser dans app.py)."""
    try:
        _ensure_manual_override_table()
        with _write_lock:
            conn = _get_connection()
            conn.execute("DELETE FROM vies_manual_overrides")
            conn.commit()
        logger.info("Overrides manuels VIES supprimés.")
    except Exception:
        pass

def delete_manual_override(full_vat: str) -> None:
    """Supprime l'override manuel d'un seul numéro TVA.

    Après suppression, le numéro repasse par le résultat VIES normal
    lors du prochain calcul.

    Args:
        full_vat: numéro complet normalisé (ex: "DE123456789").
    """
    try:
        _ensure_manual_override_table()
        with _write_lock:
            conn = _get_connection()
            conn.execute(
                "DELETE FROM vies_manual_overrides WHERE full_vat = ?",
                (full_vat.upper().strip(),),
            )
            conn.commit()
        logger.info("Override manuel VIES supprimé : %s", full_vat)
    except Exception as exc:
        logger.warning("Erreur suppression override %s : %s", full_vat, exc)
        raise


def get_manual_overrides_full() -> list[tuple[str, bool, str]]:
    """Retourne tous les overrides manuels avec leur date, pour l'affichage UI.

    Returns:
        Liste de tuples ``(full_vat, is_valid, set_at)`` triés du plus récent
        au plus ancien. ``set_at`` est une chaîne ISO 8601 UTC.
    """
    try:
        _ensure_manual_override_table()
        conn = _get_connection()
        rows = conn.execute(
            "SELECT full_vat, is_valid, set_at "
            "FROM vies_manual_overrides ORDER BY set_at DESC"
        ).fetchall()
        return [(row["full_vat"], bool(row["is_valid"]), row["set_at"] or "") for row in rows]
    except Exception:
        return []