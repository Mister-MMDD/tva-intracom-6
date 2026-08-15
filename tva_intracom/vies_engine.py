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

import hashlib
import json
import logging
import re
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed

# Verrou global (process entier) bornant le nombre de requêtes HTTP VIES
# réellement EN VOL simultanément, tous appels/utilisateurs confondus.
#
# Sans ça : chaque appel à validate_vat_numbers_parallel crée son propre
# ThreadPoolExecutor(max_workers=25). MAX_CONCURRENT_BIG_JOBS (voir
# background_calc.py) ne s'applique qu'aux fichiers > 20k lignes — un
# fichier "moyen" (ex. 15k lignes, donc en dessous de ce seuil) passe à
# travers ce garde-fou. Avec 4 utilisateurs en parallèle sur des fichiers
# moyens, on peut se retrouver avec jusqu'à 100 threads réseau simultanés :
# risque de saturation RAM (pile de threads) et surtout de bannissement
# temporaire par l'API VIES (limite de requêtes concurrentes par IP source).
#
# Ce sémaphore ne remplace pas MAX_CONCURRENT_BIG_JOBS (qui borne le nombre
# de CALCULS complets simultanés, RAM des Sale/VatResult incluse) : il borne
# spécifiquement la charge réseau VIES, à un niveau plus bas et orthogonal.
# Changement volontairement minimal : on ne touche ni à la taille des
# ThreadPoolExecutor existants ni à leur cycle de vie (with-block par appel),
# on ajoute juste un acquire/release autour de l'appel réseau lui-même.
_VIES_GLOBAL_CONCURRENCY_LIMIT = 25
_vies_global_semaphore = threading.BoundedSemaphore(_VIES_GLOBAL_CONCURRENCY_LIMIT)
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

import psycopg2
import psycopg2.pool
from psycopg2.extras import execute_values

from .security import encrypt_data as _enc, decrypt_data as _dec

logger = logging.getLogger(__name__)

VIES_REST_URL = "https://ec.europa.eu/taxation_customs/vies/rest-api/check-vat-number"
DEFAULT_TIMEOUT = 10

# TTL du cache : durée en jours avant qu'un numéro soit revalidé auprès de VIES.
# Valeur par défaut : 7 jours, utilisée pour le cache global mutualisé (non
# scopé, partagé entre tous les comptes) et comme fallback pour tout scope
# n'ayant jamais appelé set_cache_ttl().
#
# IMPORTANT (isolation multi-tenant) : le TTL est configurable PAR SCOPE, pas
# globalement. set_cache_ttl() ne doit jamais modifier une variable partagée
# par tous les process/sessions Streamlit — sur Streamlit Cloud plusieurs
# comptes peuvent partager le même process Python, et un TTL global mutable
# permettrait à un utilisateur de modifier le comportement de cache VIES de
# tous les autres. _SCOPE_TTL_DAYS stocke donc le TTL par scope_id ; le cache
# global mutualisé (vies_global_cache), lui, n'est jamais scopé et utilise
# toujours DEFAULT_CACHE_TTL_DAYS, non modifiable depuis l'UI.
DEFAULT_CACHE_TTL_DAYS: int = 7
_SCOPE_TTL_DAYS: dict[str, int] = {}

# PERF (voir README - évolution.md) : compilée une seule fois au chargement
# du module plutôt qu'à chaque appel de _clean_vat_number (potentiellement
# des dizaines de milliers d'appels sur un gros fichier). re.compile() est
# techniquement déjà mise en cache par le module `re` (jusqu'à 512 patterns),
# mais un objet Pattern dédié évite ce lookup de cache et documente l'usage.
_VAT_CLEAN_RE = re.compile(r"[\s.\-]")


def _get_ttl_days(scope_id: Optional[str] = None) -> int:
    """TTL effectif (en jours) pour ce scope, ou le défaut global si le
    scope n'a jamais personnalisé sa valeur (ou si scope_id est None, pour
    le cache global mutualisé qui n'est délibérément pas personnalisable).

    `_SCOPE_TTL_DAYS` reste un simple dict en mémoire (process/run courant)
    pour éviter un aller-retour DB à chaque appel — cette fonction est
    invoquée pour CHAQUE ligne lors du calcul de fraîcheur du cache. Mais
    ce dict n'est plus la SEULE source de vérité : au premier accès pour un
    scope donné dans ce run, on tente une lecture en base (persistée par
    set_cache_ttl) et on met le résultat en cache localement. Ainsi un TTL
    personnalisé survit à un redémarrage du process (mise en veille
    Railway, redéploiement) — voir set_cache_ttl et _load_ttl_from_db.
    """
    if scope_id is None:
        return DEFAULT_CACHE_TTL_DAYS
    if scope_id in _SCOPE_TTL_DAYS:
        return _SCOPE_TTL_DAYS[scope_id]
    _db_ttl = _load_ttl_from_db(scope_id)
    _SCOPE_TTL_DAYS[scope_id] = _db_ttl if _db_ttl is not None else DEFAULT_CACHE_TTL_DAYS
    return _SCOPE_TTL_DAYS[scope_id]


def _load_ttl_from_db(scope_id: str) -> Optional[int]:
    """Lit le TTL personnalisé du scope depuis vies_scope_settings.

    Retourne None si aucune personnalisation n'existe pour ce scope, ou en
    cas d'erreur DB (comportement identique aux autres lectures du module :
    on log et on retombe sur le défaut plutôt que de faire planter le
    calcul — voir la note existante sur les erreurs DB silencieuses dans
    les fonctions de batching)."""
    try:
        with _conn() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT ttl_days FROM vies_scope_settings WHERE scope_id=%s",
                (scope_id,),
            )
            row = cur.fetchone()
        return int(row[0]) if row else None
    except Exception as exc:
        logger.warning(
            "Impossible de lire le TTL persisté pour le scope [%s] (%s) — "
            "utilisation du défaut (%d j) pour ce run.",
            scope_id, exc, DEFAULT_CACHE_TTL_DAYS,
        )
        return None

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
    "outlook.com", "outlook.fr", "hotmail.com", "hotmail.fr", "live.com", "live.fr", "msn.com",
    "yahoo.com", "yahoo.fr", "yahoo.co.uk",
    "icloud.com", "me.com", "mac.com",
    "aol.com",
    "gmx.com", "gmx.fr", "gmx.net", "gmx.de", "web.de", "t-online.de",
    "laposte.net",
    "orange.fr", "wanadoo.fr",
    "free.fr",
    "sfr.fr", "bbox.fr", "neuf.fr", "numericable.fr", "aliceadsl.fr",
    "mail.com", "zoho.com",
    "yandex.com", "yandex.ru", "mail.ru",
    "qq.com", "163.com", "126.com",
    "naver.com",
    "rediffmail.com",
    "protonmail.com", "proton.me", "pm.me", "aleeas.com",
    "startmail.com",
    "mailfence.com",
    "countermail.com",
    "hushmail.com",
    "runbox.com",
    "posteo.de",
    "kolabnow.com",
    "disroot.org",
    "ctemplar.com",
    "privateemail.com",
    "migadu.com",

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
    # Date de dernière vérification connue (ISO 8601 UTC), renseignée quand le
    # résultat provient du cache (scope ou global) ; vide pour un résultat
    # tout juste obtenu de l'API VIES.
    checked_at: str = ""
    # True si ce résultat n'est PAS une vérification fraîche mais un repli sur
    # une entrée de cache déjà expirée (TTL dépassé), utilisée uniquement
    # parce que l'API VIES était indisponible au moment du calcul. Un tel
    # résultat ne doit jamais être traité comme une confirmation automatique
    # fiable : voir engine.py (is_inconclusive) qui le traite comme un
    # inconclusif classique (B2C par défaut, motif affiché à l'utilisateur).
    stale_fallback: bool = False


# ---------------------------------------------------------------------------
# Helpers TTL / dates
# ---------------------------------------------------------------------------

def set_cache_ttl(scope_id: str, days: int) -> None:
    """Modifie le TTL du cache VIES (en jours) pour CE scope uniquement.

    N'affecte jamais les autres comptes/domaines ni le cache global
    mutualisé : le TTL reste tenu à jour dans un dict en mémoire indexé par
    scope_id (pour un accès rapide sans aller-retour DB à chaque ligne),
    jamais dans une variable partagée par tout le process.

    Persisté en base (table vies_scope_settings) pour survivre à un
    redémarrage du process — mise en veille Railway (scale-to-zero) ou
    redéploiement — qui vidait auparavant silencieusement toute
    personnalisation, ramenant l'utilisateur à 7 jours sans qu'il en soit
    informé. En cas d'erreur DB, le TTL reste appliqué pour le run courant
    (mémoire) mais la personnalisation ne survivra pas au redémarrage —
    on log l'erreur plutôt que de faire planter l'UI pour ce geste mineur.
    """
    _days = max(1, int(days))
    _SCOPE_TTL_DAYS[scope_id] = _days
    try:
        with _conn() as conn, conn.cursor() as cur:
            cur.execute("""
                INSERT INTO vies_scope_settings (scope_id, ttl_days, updated_at)
                VALUES (%s,%s,%s)
                ON CONFLICT (scope_id) DO UPDATE SET
                    ttl_days=EXCLUDED.ttl_days, updated_at=EXCLUDED.updated_at
            """, (scope_id, _days, _now_utc()))
            conn.commit()
    except Exception as exc:
        logger.warning(
            "TTL VIES [%s] appliqué pour ce run (%d j) mais non persisté en "
            "base (%s) — reviendra au défaut après redémarrage du process.",
            scope_id, _days, exc,
        )
        return
    logger.info("Cache VIES [%s] : TTL mis à jour à %d jours (persisté).", scope_id, _days)


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _is_expired(checked_at, scope_id: Optional[str] = None, ttl_days: Optional[int] = None) -> bool:
    """Retourne True si l'entrée dépasse le TTL configuré pour ce scope.

    Accepte un datetime (valeur normale renvoyée par psycopg2 pour une
    colonne TIMESTAMPTZ) ou, par prudence, une chaîne ISO.

    scope_id doit être fourni pour toute entrée appartenant à un cache
    scopé (vies_scope_cache, vies_manual_overrides) ; le laisser à None
    revient à utiliser DEFAULT_CACHE_TTL_DAYS, réservé au cache global
    mutualisé (vies_global_cache) qui n'est pas personnalisable.

    PERF (voir README - évolution.md) : `ttl_days` est optionnel et permet
    à un appelant qui traite un LOT de lignes pour un même scope (ex.
    `_db_get_scope_batch`, `_db_get_global_batch`) de calculer le TTL une
    seule fois via `_get_ttl_days(scope_id)` et de le repasser ici pour
    chaque ligne, au lieu de refaire un lookup dict (déjà bon marché, mais
    répété inutilement des centaines/milliers de fois par batch) pour une
    valeur strictement identique sur tout le lot. Si `ttl_days` n'est pas
    fourni, le comportement est inchangé (résolution via `_get_ttl_days`).
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
    _ttl = ttl_days if ttl_days is not None else _get_ttl_days(scope_id)
    return _now_utc() - checked_at > timedelta(days=_ttl)


def _parse_checked_at(checked_at_str: str) -> Optional[datetime]:
    """Parse le `checked_at` ISO d'un ViesResult (voir _row_to_result) en
    datetime UTC tz-aware, ou None si vide/invalide (l'appelant retombe
    alors sur `_now_utc()` — voir `_db_set_scope`/`_db_set_scope_batch`).

    Utilisée pour faire hériter la date de vérification D'ORIGINE lors
    d'une copie cache global → cache scope, plutôt que de régénérer la
    date de la copie (voir docstring de `_db_set_scope`).
    """
    if not checked_at_str:
        return None
    try:
        _dt = datetime.fromisoformat(checked_at_str)
    except ValueError:
        return None
    return _dt if _dt.tzinfo is not None else _dt.replace(tzinfo=timezone.utc)


def _parse_flexible_date(s: str) -> Optional[datetime]:
    """Parse 'YYYY-MM-DD' ou une date ISO complète en datetime UTC tz-aware.

    Une date seule ('YYYY-MM-DD') est interprétée comme minuit UTC ce jour-là,
    pour retrouver le comportement de get_vies_status_as_of() : « statut connu
    strictement avant cette date » quand seule la date de vente est fournie.

    Retourne None si `s` est vide ou ne peut pas être parsée — NE JAMAIS
    replier silencieusement sur "maintenant" : get_vies_status_as_of() sert
    à reconstituer le statut VIES connu à la date d'une vente pour justifier
    une exonération B2B en cas de contrôle fiscal (art. 262 ter I CGI). Un
    repli sur "maintenant" y ferait remonter à tort une validation VIES
    postérieure à la vente.
    """
    s = (s or "").strip()
    if not s:
        logger.warning("_parse_flexible_date : date vide fournie, aucun repli sur la date du jour.")
        return None
    try:
        if len(s) == 10:
            return datetime.strptime(s, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except ValueError:
        logger.warning(
            "_parse_flexible_date : date '%s' non parsable, aucun repli sur la date du jour.", s,
        )
        return None


# ---------------------------------------------------------------------------
# Pool Postgres (Supabase) — même base que auth.py / billing.py
# ---------------------------------------------------------------------------

from .config import get_secret
from .database import NonPoolingConnectionPool, get_shared_pool, close_idle_connections as _database_close_idle

_pool_lock = threading.Lock()
_schema_ready = False


def _get_pool() -> "NonPoolingConnectionPool":
    """Retourne le pool Postgres PARTAGÉ (database.get_shared_pool) — même
    base que auth.py/billing.py/ecb_rates.py. Depuis le partage, ce module
    ne maintient plus sa propre connexion mise en cache : `cache_connection=True`
    reste le mode utilisé par le pool partagé (voir database.py pour le
    détail), la justification ci-dessous (pourquoi cache_connection=True est
    sûr pour ce module précisément) reste valable telle quelle.

    Aucune fonction DB de ce module n'est appelée DEPUIS les workers du
    ThreadPoolExecutor (voir validate_vat_numbers_parallel : `_check_one()`
    ne fait que l'appel HTTP VIES, jamais de requête SQL). Les deux requêtes
    batch (_db_get_scope_batch / _db_get_global_batch) et les deux écritures
    batch (_db_set_scope_batch / _db_set_global_batch) tournent toutes sur
    le thread principal (script Streamlit, ou thread du job d'arrière-plan
    pour les gros fichiers — voir ui/background_calc.py), jamais
    concurremment entre elles.
    """
    global _schema_ready
    dsn = get_secret("SUPABASE_DB_URL")
    if not dsn:
        raise RuntimeError(
            "SUPABASE_DB_URL non définie — impossible de se connecter à la "
            "base du cache VIES. Configurez ce secret côté Streamlit Cloud "
            "(même valeur que pour auth.py / billing.py)."
        )
    pool = get_shared_pool(dsn)
    if not _schema_ready:
        with _pool_lock:
            if not _schema_ready:
                _schema_ready = True
                _init_schema(pool)
    return pool


def close_idle_connections() -> None:
    """Ferme la connexion partagée mise en cache par ce thread, si elle
    existe. À appeler par app.py en tout début de run (voir app.py), avant
    même run_auth_flow(), pour qu'une connexion ne survive jamais plus
    longtemps qu'un seul run. Délègue au pool partagé
    (database.close_idle_connections) — idempotent si auth.py/billing.py/
    ecb_rates.py l'ont déjà fermée dans ce run."""
    _database_close_idle()


def _init_schema(pool: "NonPoolingConnectionPool") -> None:
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
            # TTL du cache VIES personnalisé par scope (voir set_cache_ttl /
            # _get_ttl_days) — persisté pour survivre à la mise en veille
            # Railway (scale-to-zero) et aux redéploiements, qui vidaient
            # auparavant _SCOPE_TTL_DAYS (dict en mémoire uniquement) et
            # ramenaient silencieusement tous les scopes à 7 jours.
            cur.execute("""
                CREATE TABLE IF NOT EXISTS vies_scope_settings (
                    scope_id   TEXT PRIMARY KEY,
                    ttl_days   INTEGER NOT NULL,
                    updated_at TIMESTAMPTZ NOT NULL
                )
            """)
            # PERF (voir README - évolution.md) : horodatage de la dernière
            # purge administrative `purge_malformed_entries()` — permet de
            # ne l'exécuter réellement qu'une fois par jour au lieu d'une
            # fois par SESSION utilisateur (le garde précédent était en
            # st.session_state, donc réinitialisé à chaque nouvel onglet).
            cur.execute("""
                CREATE TABLE IF NOT EXISTS vies_maintenance (
                    task_name    TEXT PRIMARY KEY,
                    last_run_at  TIMESTAMPTZ NOT NULL
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
    """Accepte soit 6 colonnes (valid..error, ancien usage), soit 7
    (valid..error, checked_at) — le 7e élément, s'il est présent, est reporté
    dans ViesResult.checked_at pour permettre l'affichage de la date de
    dernière vérification connue en cas de repli sur cache périmé."""
    if len(row) >= 7:
        valid, cc, num, name, addr, err, checked_at = row[:7]
    else:
        valid, cc, num, name, addr, err = row
        checked_at = None
    return ViesResult(
        valid=bool(valid), country_code=cc, vat_number=num,
        name=_dec(name), address=_dec(addr), error=err or "",
        checked_at=checked_at.isoformat() if checked_at else "",
    )


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
    result = _row_to_result(row[:7])
    return result, not _is_expired(row[6], scope_id)


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
    result = _row_to_result(row[:7])
    return result, not _is_expired(row[6])


def _db_set_scope(scope_id: str, vat_id: str, result: ViesResult, log_history: bool = True,
                   checked_at: Optional[datetime] = None) -> None:
    """Écrit dans le cache PRIVÉ du scope et journalise dans son historique
    d'audit. N'écrit jamais dans vies_global_cache (voir _db_set_global).

    `checked_at` : date de vérification à enregistrer. Par défaut (None),
    utilise l'instant présent — cas normal d'une vérification fraîche
    (résultat direct de l'API VIES). Lors d'une COPIE depuis le cache
    global déjà frais (cascade scope → global → API, voir check_vat_raw),
    l'appelant DOIT passer la date de vérification D'ORIGINE (celle
    enregistrée dans vies_global_cache), et non l'instant de la copie :
    sinon la fraîcheur du scope se prolonge artificiellement à chaque
    copie, indépendamment de la dernière vérification réelle auprès de
    VIES. Bug identifié le 13/08/2026 : un scope B copiant une entrée du
    cache global vérifiée par un scope A le 6 août, copie effectuée le 9
    août, obtenait un `checked_at` de scope = 9 août au lieu de 6 août —
    le scope B passait alors pour "à jour" jusqu'au 16 août (9+7j) alors
    que la vérification réelle contre VIES datait du 6 août.
    """
    checked_at = checked_at or _now_utc()
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
              _enc(result.name), _enc(result.address), result.error, checked_at))
        if log_history:
            cur.execute("""
                INSERT INTO vies_check_history
                    (scope_id, vat_id, valid, country_code, vat_number, name, address, error, checked_at)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
            """, (scope_id, vat_id, result.valid, result.country_code, result.vat_number,
                  _enc(result.name), _enc(result.address), result.error, checked_at))
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
              _enc(result.name), _enc(result.address), result.error, checked_at))
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
    # PERF : TTL identique pour toutes les lignes de ce batch (même scope) —
    # calculé une seule fois plutôt que dans chaque appel à _is_expired.
    _ttl_days = _get_ttl_days(scope_id)
    for row in rows:
        vat_id, checked_at = row[0], row[7]
        out[vat_id] = (_row_to_result(row[1:8]), not _is_expired(checked_at, scope_id, _ttl_days))
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
    # PERF : cache global = toujours DEFAULT_CACHE_TTL_DAYS (non scopé), un
    # seul appel suffit pour tout le batch (voir _is_expired / _get_ttl_days).
    _ttl_days = DEFAULT_CACHE_TTL_DAYS
    for row in rows:
        vat_id, checked_at = row[0], row[7]
        out[vat_id] = (_row_to_result(row[1:8]), not _is_expired(checked_at, None, _ttl_days))
    return out


def _db_set_scope_batch(scope_id: str, items: list[tuple[str, ViesResult]], log_history: bool = True,
                         use_result_checked_at: bool = False) -> None:
    """Upsert en lot dans vies_scope_cache + insertion en lot dans
    vies_check_history — un aller-retour réseau au lieu de N.

    `use_result_checked_at` : si True, chaque ligne utilise la date de
    `result.checked_at` (déjà connue, ex : copiée depuis le cache global)
    au lieu de l'instant présent — voir docstring de `_db_set_scope` pour
    la justification (ne pas prolonger artificiellement la fraîcheur du
    scope à chaque copie). Réservé aux appels qui copient un résultat déjà
    frais (`validate_vat_numbers_parallel`, section "to_copy_from_global") ;
    les écritures suivant une vraie vérification API (fresh check) doivent
    conserver le comportement par défaut (False → `_now_utc()`).
    """
    if not items:
        return
    _now = _now_utc()

    def _row_checked_at(r: ViesResult) -> datetime:
        if not use_result_checked_at:
            return _now
        return _parse_checked_at(r.checked_at) or _now

    scope_rows = [
        (scope_id, vat_id, r.valid, r.country_code, r.vat_number, _enc(r.name), _enc(r.address), r.error,
         _row_checked_at(r))
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
        (vat_id, r.valid, r.country_code, r.vat_number, _enc(r.name), _enc(r.address), r.error, checked_at)
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
            "name": _dec(r[4]), "address": _dec(r[5]), "error": r[6] or "",
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
            "name": _dec(r[5]), "address": _dec(r[6]), "error": r[7] or "",
        })
    return result


def get_vies_status_as_of(scope_id: str, full_vat: str, as_of_date_iso: str) -> Optional[dict]:
    """Statut VIES tel que connu par CE scope à une date donnée (ex: date
    d'une vente), pour justifier une exonération B2B lors d'un contrôle
    fiscal. Retourne None si ce scope n'avait aucune vérification
    antérieure à cette date, OU si `as_of_date_iso` est vide/non parsable
    (dans ce dernier cas, un warning est loggé — voir _parse_flexible_date)."""
    as_of = _parse_flexible_date(as_of_date_iso)
    if as_of is None:
        return None
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


def _mask_vat(vat: str) -> str:
    """Masque partiellement un numéro de TVA pour les logs (DPP Amazon)."""
    if not vat or len(vat) < 5:
        return "***"
    return f"{vat[:4]}***{vat[-2:]}"

def _db_delete_expired_scope(scope_id: str) -> int:
    """Purge les entrées expirées ET les erreurs transitoires du scope
    courant. N'affecte jamais le cache global (mutualisé, purgé
    indépendamment par purge_expired_global_cache())."""
    cutoff = _now_utc() - timedelta(days=_get_ttl_days(scope_id))
    # Rétention historique : Amazon DPP exige la suppression des PII.
    # On garde 365 jours pour raisons fiscales, mais on purge le reste.
    history_cutoff = _now_utc() - timedelta(days=365)

    transient_patterns = [
        "%ms_unavailable%", "%service_unavailable%",
        "%ms_max_concurrent_req%", "%global_max_concurrent_req%",
        "%timeout%", "%erreur de connexion%",
        "%erreur http 500%", "%erreur http 502%",
        "%erreur http 503%", "%erreur http 504%",
        "%non concluante%",
    ]
    with _conn() as conn, conn.cursor() as cur:
        # Cache
        cur.execute(
            "DELETE FROM vies_scope_cache WHERE scope_id=%s AND checked_at < %s",
            (scope_id, cutoff),
        )
        deleted = cur.rowcount

        # Historique (Data Retention)
        cur.execute(
            "DELETE FROM vies_check_history WHERE scope_id=%s AND checked_at < %s",
            (scope_id, history_cutoff),
        )

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


def anonymize_and_retain_scope_history(scope_id: str) -> None:
    """Pseudonymise la piste d'audit VIES d'un scope au lieu de la supprimer.

    RGPD art. 17.3.b : le droit à l'effacement ne s'applique pas quand la
    conservation est nécessaire au respect d'une obligation légale — ici,
    la justification d'exonérations B2B lors d'un contrôle fiscal
    (`vies_check_history`, déjà retenue 365 jours par `_db_delete_expired_scope`
    en fonctionnement normal, voir plus bas). Supprimer purement et
    simplement cet historique à la suppression d'un compte contredirait
    cette politique de rétention déjà en place.

    Pour un scope privé (``"user:<email>"``), la colonne `scope_id` contient
    l'e-mail en clair — il ne suffit donc pas de garder les lignes, il faut
    couper le lien direct avec la personne : on renomme le scope en un
    identifiant pseudonyme dérivé (haché, non réversible), qui reste
    purgeable par la purge périodique normale une fois les 365 jours
    écoulés, exactement comme n'importe quel autre scope.

    Un scope partagé (``"domain:<domaine>"``, cabinet) ne contient aucune
    PII directe dans son identifiant — rien à pseudonymiser, et de toute
    façon `auth.delete_account()` n'appelle jamais cette fonction pour ce
    type de scope (les données restent utiles aux autres membres du cabinet).
    """
    if not scope_id.startswith("user:"):
        return
    pseudo_scope = "deleted:" + hashlib.sha256(scope_id.encode()).hexdigest()[:32]
    with _conn() as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE vies_check_history SET scope_id=%s WHERE scope_id=%s",
            (pseudo_scope, scope_id),
        )
        conn.commit()
    logger.info(
        "Piste d'audit VIES pseudonymisée pour suppression de compte (scope privé -> %s).",
        pseudo_scope,
    )


def delete_all_scope_data(scope_id: str) -> None:
    """Supprime les données non soumises à rétention légale d'un scope
    (cache privé, overrides manuels), et pseudonymise — au lieu de
    supprimer — la piste d'audit VIES (`vies_check_history`), conservée
    365 jours pour justifier d'éventuelles exonérations B2B lors d'un
    contrôle fiscal (voir `anonymize_and_retain_scope_history`).

    Utilisé lors de la suppression d'un compte utilisateur (si scope
    individuel) — voir `tva_intracom/auth.py::delete_account()`.
    """
    anonymize_and_retain_scope_history(scope_id)
    with _conn() as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM vies_scope_cache WHERE scope_id=%s", (scope_id,))
        cur.execute("DELETE FROM vies_manual_overrides WHERE scope_id=%s", (scope_id,))
        conn.commit()
    logger.info(
        "Données VIES supprimées pour le scope [%s] (cache + overrides ; "
        "historique conservé sous forme pseudonymisée, voir rétention légale).",
        scope_id,
    )


def export_scope_data(scope_id: str) -> dict:
    """Exporte toutes les données d'un scope pour la portabilité des données (RGPD)."""
    with _conn() as conn, conn.cursor() as cur:
        # Cache
        cur.execute(
            "SELECT vat_id, valid, country_code, vat_number, name, address, error, checked_at "
            "FROM vies_scope_cache WHERE scope_id=%s", (scope_id,)
        )
        cache = [
            {
                "vat_id": r[0], "valid": r[1], "country_code": r[2], "vat_number": r[3],
                "name": _dec(r[4]), "address": _dec(r[5]), "error": r[6],
                "checked_at": r[7].isoformat() if r[7] else None
            }
            for r in cur.fetchall()
        ]

        # Historique
        cur.execute(
            "SELECT vat_id, valid, country_code, vat_number, name, address, error, checked_at "
            "FROM vies_check_history WHERE scope_id=%s", (scope_id,)
        )
        history = [
            {
                "vat_id": r[0], "valid": r[1], "country_code": r[2], "vat_number": r[3],
                "name": _dec(r[4]), "address": _dec(r[5]), "error": r[6],
                "checked_at": r[7].isoformat() if r[7] else None
            }
            for r in cur.fetchall()
        ]

        # Overrides
        cur.execute(
            "SELECT full_vat, is_valid, set_at FROM vies_manual_overrides WHERE scope_id=%s", (scope_id,)
        )
        overrides = [
            {"full_vat": r[0], "is_valid": r[1], "set_at": r[2].isoformat() if r[2] else None}
            for r in cur.fetchall()
        ]

    return {
        "scope_id": scope_id,
        "vies_cache": cache,
        "vies_history": history,
        "vies_manual_overrides": overrides
    }


def get_scope_vies_snapshot(scope_id: str) -> list[dict]:
    """Photographie complète, pour un scope donné, de TOUS les numéros de TVA
    intracommunautaire jamais vérifiés — utilisée pour générer le
    "Certificat de Validité VIES" (voir vies_certificate.py). Pour chaque
    numéro :
      - le statut RETENU aujourd'hui par le moteur (override manuel s'il
        existe et n'est pas expiré, sinon dernier statut du cache scope) ;
      - la date de première et de dernière vérification connues par ce
        scope (piste d'audit vies_check_history).

    Ne renvoie que des données déjà scopées (isolation par compte/cabinet) —
    jamais le cache global mutualisé.
    """
    with _conn() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT vat_id, valid, country_code, vat_number, checked_at "
            "FROM vies_scope_cache WHERE scope_id=%s",
            (scope_id,),
        )
        cache_rows = cur.fetchall()

        cur.execute(
            "SELECT vat_id, MIN(checked_at), MAX(checked_at), COUNT(*) "
            "FROM vies_check_history WHERE scope_id=%s GROUP BY vat_id",
            (scope_id,),
        )
        history_bounds = {
            r[0]: {"first_checked_at": r[1], "last_checked_at": r[2], "nb_checks": r[3]}
            for r in cur.fetchall()
        }

        cur.execute(
            "SELECT full_vat, is_valid, set_at FROM vies_manual_overrides WHERE scope_id=%s",
            (scope_id,),
        )
        overrides = {r[0]: (bool(r[1]), r[2]) for r in cur.fetchall()}

    snapshot = []
    for vat_id, valid, country_code, vat_number, checked_at in cache_rows:
        _bounds = history_bounds.get(vat_id, {})
        _override = overrides.get(vat_id)
        _is_manual = _override is not None and not _is_expired(_override[1], scope_id)
        _final_valid = _override[0] if _is_manual else bool(valid)
        snapshot.append({
            "vat_id": vat_id,
            "country_code": country_code,
            "vat_number": vat_number,
            "valid": _final_valid,
            "source": "manuel" if _is_manual else "VIES",
            "first_checked_at": (_bounds.get("first_checked_at") or checked_at),
            "last_checked_at": (_bounds.get("last_checked_at") or checked_at),
            "nb_checks": _bounds.get("nb_checks", 1),
        })

    # Numéros classifiés manuellement mais absents du cache scope (rare,
    # ex. override posé sur un numéro jamais revu depuis par le moteur) :
    # on les inclut quand même, la piste d'audit doit rester complète.
    _known_vat_ids = {row["vat_id"] for row in snapshot}
    for full_vat, (is_valid, set_at) in overrides.items():
        if full_vat in _known_vat_ids or _is_expired(set_at, scope_id):
            continue
        _bounds = history_bounds.get(full_vat, {})
        snapshot.append({
            "vat_id": full_vat,
            "country_code": full_vat[:2],
            "vat_number": full_vat[2:],
            "valid": is_valid,
            "source": "manuel",
            "first_checked_at": _bounds.get("first_checked_at") or set_at,
            "last_checked_at": _bounds.get("last_checked_at") or set_at,
            "nb_checks": _bounds.get("nb_checks", 0),
        })

    snapshot.sort(key=lambda d: d["vat_id"])
    return snapshot


_MALFORMED_PURGE_MIN_INTERVAL_DAYS = 1


def purge_malformed_entries(force: bool = False) -> int:
    """Purge administrative (appelée depuis app.py une fois par session) :
    supprime les entrées vat_id mal préfixées par un bug historique (double
    préfixe pays, ex. "DEIT123..." ou "FRFR123..." en cas de répétition du
    même préfixe). Opère sur les DEUX tables (scope + global) car le bug
    était antérieur à la scopisation.

    BUGFIX (voir README - évolution.md) : la clause excluait auparavant le
    cas où les deux préfixes détectés étaient identiques (ex. "FRFR..."),
    laissant ces doublons non nettoyés par la procédure optimisée. Retirée :
    tout vat_id dont les 4 premiers caractères forment deux codes pays UE
    valides consécutifs est un doublon de préfixe, identiques ou non.

    PERF (voir README - évolution.md) : deux correctifs par rapport à la
    version précédente.
      1. Un seul `DELETE ... WHERE` par table (comparaison d'ensemble via
         `= ANY(%s)` sur les 2 préfixes pays) remplace le `SELECT DISTINCT`
         suivi d'une boucle Python de `DELETE` ligne par ligne — coûteux
         (un aller-retour réseau par ligne à supprimer) et qui grossissait
         avec la taille du cache global mutualisé.
      2. La purge réelle n'est plus tentée qu'au plus une fois par
         `_MALFORMED_PURGE_MIN_INTERVAL_DAYS` (horodatage persisté en base,
         table `vies_maintenance`), au lieu d'une fois par SESSION Streamlit
         (le garde précédent vivait en `st.session_state`, donc s'exécutait
         à nouveau à chaque nouvel onglet/utilisateur). `force=True` (tests,
         CLI) ignore ce throttle.
    """
    _EU_CC = ["AT", "BE", "BG", "HR", "CY", "CZ", "DK", "EE", "FI", "FR", "DE", "GR", "HU",
              "IE", "IT", "LV", "LT", "LU", "MT", "NL", "PL", "PT", "RO", "SK", "SI", "ES", "SE", "XI"]
    deleted = 0
    with _conn() as conn, conn.cursor() as cur:
        if not force:
            cur.execute(
                "SELECT last_run_at FROM vies_maintenance WHERE task_name = %s",
                ("purge_malformed_entries",),
            )
            row = cur.fetchone()
            if row and row[0] and (_now_utc() - row[0]) < timedelta(days=_MALFORMED_PURGE_MIN_INTERVAL_DAYS):
                return 0

        for table in ("vies_global_cache", "vies_scope_cache"):
            cur.execute(
                f"""
                DELETE FROM {table}
                WHERE length(vat_id) >= 4
                  AND upper(left(vat_id, 2)) = ANY(%(cc)s)
                  AND upper(substring(vat_id from 3 for 2)) = ANY(%(cc)s)
                """,
                {"cc": _EU_CC},
            )
            deleted += cur.rowcount

        cur.execute(
            """
            INSERT INTO vies_maintenance (task_name, last_run_at)
            VALUES (%s, %s)
            ON CONFLICT (task_name) DO UPDATE SET last_run_at = EXCLUDED.last_run_at
            """,
            ("purge_malformed_entries", _now_utc()),
        )
        conn.commit()
    return deleted


def purge_expired_global_cache() -> int:
    """Purge administrative du cache global mutualisé (pas exposée dans
    l'UI Streamlit standard — appel manuel/CLI si besoin)."""
    cutoff = _now_utc() - timedelta(days=DEFAULT_CACHE_TTL_DAYS)
    with _conn() as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM vies_global_cache WHERE checked_at < %s", (cutoff,))
        deleted = cur.rowcount
        conn.commit()
    return deleted


def get_cache_stats(scope_id: str) -> dict:
    """Statistiques pour l'affichage app.py : compteurs du scope courant
    + taille du cache global mutualisé (lecture seule, jamais modifié par
    les actions du scope)."""
    cutoff = _now_utc() - timedelta(days=_get_ttl_days(scope_id))
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
        "ttl_days": _get_ttl_days(scope_id),
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
    # Coupures réseau/DB brutes (ex: connexion Supabase/Postgres fermée par le
    # serveur en cours d'écriture cache, sous forte concurrence des 25 workers
    # du ThreadPoolExecutor). Ces erreurs remontent SANS le préfixe "Erreur de
    # connexion / Timeout" car elles surviennent hors de l'appel HTTP VIES lui
    # même (dans check_vat_raw, autour des accès cache), via le except générique.
    # Elles ne signifient rien sur la validité du n° TVA — à traiter comme
    # inconclusive_count, jamais comme invalid_count.
    "remote end closed connection", "connection reset by peer",
    "broken pipe", "connection aborted", "server closed the connection",
    "could not connect", "connection refused",
}


def _is_transient(error: Optional[str]) -> bool:
    return any(t in (error or "").lower() for t in _TRANSIENT_ERRORS)


def _is_empty_response(res: ViesResult) -> bool:
    """Réponse VIES "vide" : valid=False, sans nom/adresse, sans erreur.

    DÉCISION (audit externe reçu 2026-08-15, point 3) — REJETÉE, documentée
    ici plutôt que patchée :

    L'audit propose de sortir `_is_empty_response` des conditions de retry
    de `check_vat_with_retry`, au motif qu'une réponse vide est la réponse
    standard et définitive de VIES pour un numéro réellement invalide (ce
    qui est vrai en général), et que retenter inutilement coûte du temps
    sur les gros fichiers contenant beaucoup de numéros invalides.

    Rejet : ce comportement existe précisément pour absorber un incident
    réel du 31/07/2026 (voir commentaire dans check_vat(), branche
    errorWrappers, L1260-1268) — une panne du service national ALLEMAND
    renvoyait `valid=False, error=""`, strictement indiscernable d'un
    numéro réellement invalide côté `ViesResult`, et avait fait basculer en
    masse des numéros de TVA allemands VALIDES en "invalides" pendant la
    panne, faute de fallback sur le cache. `_is_empty_response` + le retry
    associé est le filet de sécurité qui absorbe ce cas : sans lui,
    l'incident du 31/07/2026 se reproduirait à la prochaine panne
    d'indisponibilité d'un État membre ne renvoyant pas errorWrappers.

    Le coût (retries + attente sur les VRAIS numéros invalides) est réel
    mais accepté : impact temps de traitement, pas impact fiscal. Le risque
    inverse (patcher et rouvrir une faille déjà colmatée sur un incident de
    production vécu) est disproportionné au regard du gain. Principe
    Reject > Defer > Patch appliqué ici.
    """
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
    cleaned = _VAT_CLEAN_RE.sub("", raw.strip())
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
        "IE", "IT", "LV", "LT", "LU", "MT", "NL", "PL", "PT", "RO", "SK", "SI", "ES", "SE", "XI",
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

            # Forme alternative observée en pratique : {"errorWrappers": [{"error": "MS_UNAVAILABLE"}, ...]}
            # — une LISTE, distincte de la clé "error" singulière ci-dessus. Sans cette
            # branche, un numéro dont l'État membre est indisponible (MS_UNAVAILABLE,
            # MS_MAX_CONCURRENT_REQ...) recevait un ViesResult(valid=False, error="")
            # — indiscernable d'un "réellement invalide" pour _is_unreliable()/
            # _is_transient(), qui se basent uniquement sur result.error. Conséquence
            # vécue en prod : tous les numéros allemands faussement marqués invalides
            # lors d'une panne du service national DE, faute de fallback_cache après
            # une purge de la base (voir incident du 31/07/2026).
            error_wrappers = res_data.get("errorWrappers")
            if error_wrappers:
                codes = [
                    w.get("error", "Erreur inconnue")
                    for w in error_wrappers
                    if isinstance(w, dict)
                ]
                return ViesResult(
                    valid=False, country_code=country_code, vat_number=vat_number,
                    error=", ".join(codes) or "Erreur API inconnue (errorWrappers vide)",
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
    """Appelle check_vat avec retry backoff exponentiel sur erreurs transitoires.

    Le sémaphore global (`_vies_global_semaphore`) n'est acquis qu'autour de
    l'appel réseau lui-même (`check_vat`), PAS autour du `time.sleep()` de
    backoff entre tentatives. Avant ce correctif, un pays en panne/lent
    pouvait faire dormir jusqu'à `_VIES_GLOBAL_CONCURRENCY_LIMIT` threads
    tout en gardant leur slot de sémaphore réservé, bloquant les vérifications
    d'autres pays dont le service VIES fonctionne normalement. Relâcher le
    sémaphore pendant le sleep laisse ces slots disponibles pour les autres
    threads pendant l'attente.
    """
    delay = base_delay
    last_result: Optional[ViesResult] = None
    for attempt in range(1, max_attempts + 1):
        with _vies_global_semaphore:
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
        _db_set_scope(scope_id, norm, global_cached, log_history=True,
                      checked_at=_parse_checked_at(global_cached.checked_at))
        return global_cached

    if cached is not None and not is_fresh:
        logger.info("Cache VIES [%s] : %s expiré (TTL=%dj), revalidation.", scope_id, _mask_vat(norm), _get_ttl_days(scope_id))

    # 3) API VIES
    try:
        cc, num = _clean_vat_number(raw)
        res = check_vat_with_retry(cc, num, timeout=timeout)

        if _is_unreliable(res):
            # On ne fait plus de repli sur le cache périmé même si VIES est
            # indisponible (décision : sécurité B2C par défaut).
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
        if global_entry is not None:
            # BUGFIX : Si l'utilisateur a réduit son TTL (ex: 1 jour), on ne doit 
            # pas utiliser une entrée du cache global qui a 6 jours (même si 
            # elle est considérée "fraîche" par le défaut global de 7j).
            # On vérifie la fraîcheur par rapport au TTL du SCOPE.
            if not _is_expired(global_entry[0].checked_at, scope_id):
                results[vat_id] = global_entry[0]
                to_copy_from_global.append((norm, global_entry[0]))
                _tick()
                continue

        if scope_entry is not None:
            fallback_cache[norm] = scope_entry[0]
            logger.debug("Cache VIES [%s] expiré pour %s, revalidation.", scope_id, _mask_vat(norm))
        elif global_entry is not None:
            fallback_cache[norm] = global_entry[0]

        to_fetch[norm] = vat_id

    # Une seule requête pour copier tous les hits du cache global vers le scope
    # (+ historique) au lieu d'une requête par numéro. use_result_checked_at=True :
    # ces entrées sont des COPIES d'un cache déjà frais, pas des vérifications
    # nouvelles — on hérite de leur date de vérification d'origine plutôt que de
    # régénérer la date de copie (voir docstring de _db_set_scope).
    if to_copy_from_global:
        _db_set_scope_batch(scope_id, to_copy_from_global, log_history=True,
                            use_result_checked_at=True)

    # --- Phase 2 : requêtes réseau parallèles pour les numéros à revalider ---
    batch_results: dict[str, ViesResult] = {}

    if to_fetch:
        def _check_one(item: tuple[str, str]) -> tuple[str, ViesResult]:
            norm_id, orig = item
            country_code, number = _clean_vat_number(orig)
            # Le thread peut démarrer immédiatement (pas de limite sur
            # max_workers ici) ; le sémaphore est désormais acquis à
            # l'intérieur de check_vat_with_retry, autour de chaque appel
            # réseau individuel seulement (pas autour des sleep de retry) —
            # voir sa docstring.
            result = check_vat_with_retry(country_code, number, timeout=timeout)
            return norm_id, result

        workers = min(max_workers, len(to_fetch))
        with ThreadPoolExecutor(max_workers=workers) as pool_exec:
            futures = {pool_exec.submit(_check_one, item): item for item in to_fetch.items()}
            for future in as_completed(futures):
                norm_id, result = future.result()
                batch_results[norm_id] = result
                _tick()

        # --- Phase 3 : classification, puis DEUX écritures batch au lieu de
        #     2×N écritures séquentielles ---
        to_write_global: list[tuple[str, ViesResult]] = []
        to_write_scope: list[tuple[str, ViesResult]] = []

        for norm_id, result in batch_results.items():
            orig_id = to_fetch[norm_id]

            if _is_unreliable(result):
                # On ne fait plus de repli sur le cache périmé même si VIES est
                # indisponible (décision : sécurité B2C par défaut).
                results[orig_id] = ViesResult(
                    valid=False, country_code=result.country_code,
                    vat_number=result.vat_number,
                    error=result.error or "Réponse VIES non concluante (à revérifier)",
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
    logger.info("Override manuel VIES [%s] : %s → %s", scope_id, _mask_vat(full_vat),
                "VALIDE" if valid else "INVALIDE")


def get_manual_overrides(scope_id: str, include_expired: bool = False) -> dict[str, bool]:
    """Retourne les overrides manuels du scope courant.

    Args:
        scope_id: portée du compte/domaine appelant.
        include_expired: si False (par défaut), exclut les overrides dont
            `set_at` dépasse le TTL du scope (cf. _get_ttl_days) — même condition d'âge que
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
    except Exception as exc:
        # NE PAS avaler silencieusement : engine.py (ligne ~856) a un
        # try/except englobant spécifiquement pour logger un warning quand le
        # chargement des overrides échoue — mais ce warning ne se déclenche
        # jamais si on renvoie {} ici sans relancer, puisqu'aucune exception
        # ne remonte alors jusqu'à lui. Une panne DB passagère ferait alors
        # disparaître silencieusement TOUTES les classifications manuelles du
        # compte pour ce calcul, sans aucune trace nulle part.
        logger.warning(
            "get_manual_overrides [%s] : échec de lecture des overrides manuels (%s) — "
            "aucune classification manuelle appliquée à ce calcul.", scope_id, exc,
        )
        raise
    if include_expired:
        return {r[0]: bool(r[1]) for r in rows}
    result: dict[str, bool] = {}
    for full_vat, is_valid, set_at in rows:
        if _is_expired(set_at, scope_id):
            logger.info(
                "Override manuel VIES [%s] expiré (> %d j), ignoré au calcul : %s.",
                scope_id, _get_ttl_days(scope_id), full_vat,
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
        logger.info("Override manuel VIES [%s] supprimé : %s", scope_id, _mask_vat(full_vat))
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