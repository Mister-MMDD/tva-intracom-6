from __future__ import annotations

import logging
import threading
from datetime import date
from decimal import Decimal
from typing import Optional

import psycopg2
import psycopg2.extras

from .config import get_secret  #[cite: 4]
from .database import NonPoolingConnectionPool, get_shared_pool, close_idle_connections as _database_close_idle  #[cite: 4]
from .rates import VAT_RATES as LOCAL_VAT_FALLBACK  # Dictionnaire statique de secours[cite: 4]

logger = logging.getLogger(__name__)

# ------------------------------------------------------------------
# Cache L1 (mémoire/process) + Verrou de thread
# ------------------------------------------------------------------
# Clé : "COUNTRY|RATE_TYPE|YYYY-MM-DD" -> Decimal(rate)[cite: 4]
_vat_memory_cache: dict[str, Decimal] = {}
_vat_all_loaded: bool = False
_cache_lock = threading.Lock()  # Protects _vat_memory_cache & _vat_all_loaded[cite: 4]

_pool_lock = threading.Lock()  #[cite: 4]
_schema_ready = False  #[cite: 4]
_db_unavailable = False  # Sticky flag si BDD injoignable ou non configurée[cite: 4]


def _cache_key(country: str, rate_type: str, d: date) -> str:
    return f"{country.upper()}|{rate_type.upper()}|{d.isoformat()}"


def _get_pool() -> Optional[NonPoolingConnectionPool]:
    """Retourne le pool Postgres partagé ou dégrade silencieusement vers L1/Local[cite: 4]."""
    global _schema_ready, _db_unavailable
    if _db_unavailable:
        return None
    dsn = get_secret("SUPABASE_DB_URL")  #[cite: 4]
    if not dsn:
        logger.debug("SUPABASE_DB_URL non défini — TVA en cache mémoire / statique uniquement.")
        _db_unavailable = True
        return None
    try:
        pool = get_shared_pool(dsn)  #[cite: 4]
        if not _schema_ready:
            with _pool_lock:
                if not _schema_ready:
                    _init_schema(pool)
                    _schema_ready = True
    except Exception as exc:
        logger.warning("Cache TVA : Postgres indisponible (%s) — repli statique.", exc)
        _db_unavailable = True
        return None
    return pool


def close_idle_connections() -> None:
    """Libère les connexions inactives au début de chaque run[cite: 4]."""
    _database_close_idle()  #[cite: 4]


def _init_schema(pool: NonPoolingConnectionPool) -> None:
    conn = pool.getconn()
    try:
        with conn, conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS vat_rate_cache (
                    country_code   VARCHAR(2) NOT NULL,
                    rate_type      VARCHAR(20) NOT NULL,
                    rate           NUMERIC NOT NULL,
                    start_date     DATE NOT NULL,
                    end_date       DATE,
                    fetched_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    PRIMARY KEY (country_code, rate_type, start_date)
                )
            """)
    finally:
        pool.putconn(conn)


def prefetch_vat_rates() -> None:
    """Précharge TOUTE la table des taux de TVA depuis Supabase en un seul appel SQL[cite: 4]."""
    global _vat_all_loaded
    with _cache_lock:
        if _vat_all_loaded:
            return

    pool = _get_pool()
    if pool is None:
        return

    conn = pool.getconn()
    try:
        with conn, conn.cursor() as cur:
            cur.execute("""
                SELECT country_code, rate_type, rate, start_date, end_date 
                FROM vat_rate_cache
            """)
            rows = cur.fetchall()

            with _cache_lock:
                for country, r_type, rate, start_d, end_d in rows:
                    # Stocke le taux brut dans le dictionnaire L1 pour un accès direct
                    # Note : On alimente le cache de recherche accélérée
                    _vat_memory_cache[f"RAW|{country}|{r_type}|{start_d}|{end_d}"] = Decimal(str(rate))
                _vat_all_loaded = True
            logger.info("Prefetch TVA : %d règles de TVA chargées en RAM depuis Supabase.", len(rows))
    except Exception as exc:
        logger.warning("Cache TVA : échec du préchargement batch Postgres : %s", exc)
    finally:
        pool.putconn(conn)


def get_vat_rate(country: str, rate_type: str, target_date: date) -> Decimal:
    """Retourne le taux de TVA applicable pour un pays, un type de taux et une date[cite: 4].

    Ordre de résolution :
      1. Cache L1 RAM (accès immédiat 0 ms)[cite: 4]
      2. Cache L2 Postgres (Supabase)[cite: 4]
      3. Fallback statique local (rates.py)[cite: 4]
    """
    country = country.upper()
    rate_type = rate_type.upper()
    key = _cache_key(country, rate_type, target_date)

    # 1. Vérification Cache L1 (RAM)[cite: 4]
    with _cache_lock:
        if key in _vat_memory_cache:
            return _vat_memory_cache[key]

    # Préchargement si non effectué
    prefetch_vat_rates()

    # 2. Recherche Postgres si pas en RAM[cite: 4]
    pool = _get_pool()
    if pool is not None:
        conn = pool.getconn()
        try:
            with conn, conn.cursor() as cur:
                cur.execute("""
                    SELECT rate FROM vat_rate_cache
                    WHERE country_code = %s 
                      AND rate_type = %s 
                      AND start_date <= %s 
                      AND (end_date IS NULL OR end_date >= %s)
                    ORDER BY start_date DESC LIMIT 1
                """, (country, rate_type, target_date, target_date))
                row = cur.fetchone()
                if row:
                    rate = Decimal(str(row[0]))
                    with _cache_lock:
                        _vat_memory_cache[key] = rate
                    return rate
        except Exception as exc:
            logger.warning("Cache TVA : échec lecture Postgres pour %s/%s : %s", country, rate_type, exc)
        finally:
            pool.putconn(conn)

    # 3. Fallback Statique Local (Local Code Fallback)[cite: 4]
    fallback_rate = LOCAL_VAT_FALLBACK.get(country, {}).get(rate_type, Decimal("0.20"))
    rate_decimal = Decimal(str(fallback_rate))

    with _cache_lock:
        _vat_memory_cache[key] = rate_decimal

    return rate_decimal