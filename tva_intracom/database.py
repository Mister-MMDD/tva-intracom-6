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
    """
    last_exc: Optional[Exception] = None
    for attempt in range(2):
        pool = get_pool()
        conn = pool.getconn()
        try:
            with conn, conn.cursor() as cur:
                result = fn(conn, cur)
            pool.putconn(conn)
            return result
        except (psycopg2.InterfaceError, psycopg2.OperationalError) as exc:
            last_exc = exc
            logger.warning(
                "Connexion DB perdue (tentative %d/2) : %s", attempt + 1, exc
            )
            pool.putconn(conn, close=True)
            if on_retry is not None:
                on_retry()
    raise last_exc
