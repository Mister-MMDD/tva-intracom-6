"""Gestion centralisée des connexions Postgres (Supabase).

Remplace les 4 copies quasi-identiques de `_NonPoolingConnectionPool`
auparavant dupliquées dans auth.py, billing.py, ecb_rates.py et
vies_engine.py. Un seul point de vérité pour la config SSL, le retry, et
la fermeture des connexions en fin de run.

Pourquoi PAS un vrai psycopg2.pool.ThreadedConnectionPool (minconn=1) :
un pool classique garde au moins une connexion TCP ouverte en permanence
vers le pooler Supabase (aws-0-eu-west-1.pooler.supabase.com:6543), pour
toute la durée de vie du process, indépendamment du nombre d'utilisateurs
actifs — c'est précisément ce qui empêchait Railway de détecter
l'inactivité réelle et bloquait le scale-to-zero serverless. Ce choix
d'architecture est intentionnel et ne doit pas être "corrigé" en
réintroduisant un vrai pool.

Deux modes de fonctionnement, choisis via `cache_connection` :

  - `cache_connection=True` (auth.py, billing.py, ecb_rates.py, vies_engine.py) :
    une connexion est ouverte la première fois dans un thread donné, puis
    réutilisée pour tous les appels suivants de ce même thread tant
    qu'elle n'a pas été explicitement fermée. `putconn()` est un no-op
    par défaut (on ne paie le coût TLS qu'une fois par run) ; la
    fermeture réelle a lieu via `close_idle_connections()`, à appeler par
    app.py au tout début de chaque nouveau run.
    Note vies_engine.py : bien que ce module utilise un `ThreadPoolExecutor`
    jusqu'à 25 workers pour les appels HTTP VIES parallèles, AUCUN appel DB
    n'a lieu depuis ces workers (uniquement les requêtes réseau VIES) — les
    requêtes/écritures batch tournent toutes sur le thread appelant, avant
    ou après la section parallèle. `cache_connection=True` y est donc sûr
    (et évite ~2 s de handshake Supabase par requête batch, mesuré en prod).

  - `cache_connection=False` : chaque `getconn()` ouvre une connexion
    neuve, et `putconn()` la ferme immédiatement. Réservé au cas où du code
    DB serait un jour appelé DEPUIS plusieurs threads/workers concurrents
    partageant le même objet pool (psycopg2 n'est pas thread-safe par
    connexion) — aucun module de ce projet n'est actuellement dans ce cas.

`threading.local()` isole naturellement chaque thread (= chaque run
Streamlit en cours), sans risque de mélange de requêtes entre
utilisateurs, et fonctionne aussi hors Streamlit (CLI, mono-thread).
"""
from __future__ import annotations

import logging
import threading
from typing import Callable, Optional, TypeVar

import psycopg2

logger = logging.getLogger(__name__)

T = TypeVar("T")


class NonPoolingConnectionPool:
    """Objet à la même API que psycopg2.pool.ThreadedConnectionPool
    (`getconn()` / `putconn()` / `closeall()`), voir docstring du module
    pour le détail des deux modes (`cache_connection`).
    """

    def __init__(
        self,
        dsn: str,
        sslmode: str = "require",
        cache_connection: bool = True,
    ) -> None:
        self._dsn = dsn
        self._sslmode = sslmode
        self._cache_connection = cache_connection
        self._local = threading.local()

    def getconn(self, *_args, **_kwargs):
        if not self._cache_connection:
            return psycopg2.connect(self._dsn, sslmode=self._sslmode)

        conn = getattr(self._local, "conn", None)
        if conn is not None:
            try:
                if conn.closed == 0:
                    return conn
            except Exception:
                pass
            # Connexion périmée/cassée (ex. coupée côté serveur) : on la
            # jette et on en ouvre une neuve ci-dessous.
            self._close_quietly(conn)
            self._local.conn = None

        conn = psycopg2.connect(self._dsn, sslmode=self._sslmode)
        self._local.conn = conn
        return conn

    def putconn(self, conn, *_args, close: bool = False, **_kwargs) -> None:
        if not self._cache_connection:
            # Mode sans cache (vies_engine) : on ferme systématiquement,
            # peu importe `close`.
            self._close_quietly(conn)
            return

        if close:
            # Le retry de _run() nous demande explicitement de fermer une
            # connexion identifiée comme cassée : on honore ce paramètre
            # (contrairement à l'ancien code, qui l'ignorait silencieusement
            # et se contentait de jeter l'objet pool).
            self._close_quietly(conn)
            cached = getattr(self._local, "conn", None)
            if cached is conn:
                self._local.conn = None
            return

        # No-op volontaire sinon : on garde la connexion ouverte pour la
        # réutiliser dans ce même run. Fermeture réelle via
        # close_idle_connections(), pas ici.

    def closeall(self) -> None:
        conn = getattr(self._local, "conn", None)
        if conn is not None:
            self._close_quietly(conn)
            self._local.conn = None

    @staticmethod
    def _close_quietly(conn) -> None:
        try:
            conn.close()
        except Exception:
            logger.debug("Fermeture d'une connexion déjà invalide (ignorée).", exc_info=True)


_shared_pool: Optional["NonPoolingConnectionPool"] = None
_shared_pool_lock = threading.Lock()


def get_shared_pool(dsn: str) -> "NonPoolingConnectionPool":
    """Pool Postgres partagé par auth.py, billing.py, ecb_rates.py et
    vies_engine.py — les 4 modules pointent vers la même base
    (SUPABASE_DB_URL) et n'ont donc aucune raison de maintenir chacun leur
    propre connexion mise en cache par thread : avant ce partage, un run
    Streamlit qui touchait aux 4 modules ouvrait 4 connexions TCP/TLS
    distinctes vers Supabase au lieu d'une seule réutilisée par tous.

    Le mode `cache_connection=True` et la politique de fermeture
    (`close_idle_connections()` par module, voir plus bas) sont inchangés :
    ce partage ne réintroduit PAS de connexion persistante entre les runs,
    donc ne remet pas en cause le scale-to-zero Railway (voir docstring de
    NonPoolingConnectionPool ci-dessus).
    """
    global _shared_pool
    if _shared_pool is None:
        with _shared_pool_lock:
            if _shared_pool is None:
                _shared_pool = NonPoolingConnectionPool(dsn, sslmode="require", cache_connection=True)
    return _shared_pool


def has_shared_pool() -> bool:
    """True si le pool partagé a déjà été créé (par n'importe quel module),
    sans effet de bord — utilisé par ecb_rates.cache_info() pour restituer
    exactement l'ancienne sémantique de `_pool is not None`."""
    with _shared_pool_lock:
        return _shared_pool is not None


def reset_shared_pool() -> None:
    """Force la recréation du pool partagé au prochain `get_shared_pool()`.

    À appeler par le `on_retry` de n'importe lequel des 4 modules quand une
    connexion s'avère cassée côté serveur — un seul reset suffit puisque le
    pool est partagé (contrairement à avant, où chaque module réinitialisait
    uniquement sa propre variable `_pool`).
    """
    global _shared_pool
    with _shared_pool_lock:
        _shared_pool = None


def close_idle_connections() -> None:
    """Ferme la connexion mise en cache par CE thread sur le pool partagé,
    si elle existe. Idempotent et sans effet si le pool n'a pas encore été
    créé (ex. module jamais utilisé dans ce run) — peut donc être appelé
    sans risque même par un module qui n'a fait aucun accès DB.
    """
    with _shared_pool_lock:
        _p = _shared_pool
    if _p is not None:
        try:
            _p.closeall()
        except Exception:
            logger.debug("Fermeture de connexion partagée idle ignorée (déjà invalide).", exc_info=True)


def run_with_retry(
    get_pool: Callable[[], "NonPoolingConnectionPool"],
    fn: Callable[..., T],
    on_retry: Optional[Callable[[], None]] = None,
) -> T:
    """Exécute fn(conn, cur) avec une connexion prise dans le pool, avec un
    retry unique si la connexion s'avère fermée côté serveur (cas fréquent
    avec PgBouncer en mode transaction, qui recycle agressivement les
    connexions inactives : `psycopg2.InterfaceError: connection already
    closed`).

    `on_retry` est appelé entre les deux tentatives si l'appelant a besoin
    de forcer la recréation de son objet pool global (voir `_get_pool()`
    dans auth.py / billing.py / ecb_rates.py).

    IMPORTANT : la connexion est TOUJOURS rendue au pool via `finally`, y
    compris quand `fn` lève une exception autre que InterfaceError/
    OperationalError (ex: erreur métier dans le code appelant avec
    `cache_connection=False`, utilisé par VIES). Sans ce `finally`, une
    telle exception sautait `pool.putconn(conn)` et laissait la connexion
    ouverte côté serveur sans jamais être rendue au pool — fuite de
    connexion pouvant saturer le quota Supabase lors de pics d'erreurs.
    """
    last_exc: Optional[Exception] = None
    for attempt in range(2):
        pool = get_pool()
        conn = pool.getconn()
        close_conn = False
        try:
            with conn, conn.cursor() as cur:
                result = fn(conn, cur)
            return result
        except (psycopg2.InterfaceError, psycopg2.OperationalError) as exc:
            last_exc = exc
            close_conn = True
            logger.warning(
                "Connexion DB perdue (tentative %d/2) : %s", attempt + 1, exc
            )
            if on_retry is not None:
                on_retry()
        finally:
            # Rendue au pool dans tous les cas (succès, erreur retryable,
            # ou toute autre exception levée par fn) : jamais de fuite.
            # `close=True` uniquement pour les erreurs de connectivité
            # (la connexion est probablement invalide côté serveur) ;
            # sinon `close=False` pour préserver le pooling normal.
            pool.putconn(conn, close=close_conn)
    raise last_exc
