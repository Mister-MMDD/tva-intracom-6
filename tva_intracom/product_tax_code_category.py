"""Classification produit Amazon (PRODUCT_TAX_CODE -> catégorie interne),
Niveau 2 de la stratégie CN/CPA (voir synthèse taux_reduit_dynamique.md).

Remplace intégralement l'ancien catalogue manuel ASIN -> catégorie
(upload sidebar, supprimé — voir README - évolution.md). Contrairement à
l'ancien catalogue :
  - la classification est GLOBALE (une table product_tax_code_category,
    pas un scope par vendeur/ASIN) : PRODUCT_TAX_CODE est une
    classification Amazon standardisée, indépendante du vendeur ;
  - elle est résolue UNE SEULE FOIS, à l'import du fichier (voir
    parsers/amazon/loader.py), et stockée telle quelle sur Sale.product_category.
    Décision validée par Matthieu (2026-09-15) : pas de recalcul à la
    volée à partir d'une table live comme le faisait l'ancien
    asin_to_category dans engine.py — une correction en base impose de
    ré-importer le fichier pour être prise en compte. Le risque fiscal
    est jugé acceptable : un code jamais vu retombe sur STANDARD (le
    taux le plus sûr), donc une correction qui traîne ne fait jamais
    sous-déclarer, seulement retarder un taux réduit.

Cette table ne contient AUCUN mapping pré-rempli par ce module : les
associations PRODUCT_TAX_CODE -> catégorie relèvent de la fiscalité
(section 4 de la synthèse, décision Matthieu + validation cabinet
comptable à venir), pas d'une déduction de code. Tout code jamais vu est
écrit en base avec source='unresolved_default' et catégorie STANDARD, pour
audit et correction manuelle a posteriori (pas de blocage du calcul).
"""

from __future__ import annotations

import logging
import threading
from typing import Optional

from .config import get_secret
from .database import NonPoolingConnectionPool, get_shared_pool, close_idle_connections as _database_close_idle

logger = logging.getLogger(__name__)

# Catégories internes valides (mêmes valeurs que product_category ailleurs
# dans le projet — rates.py / vat_rates_db.py). Une valeur hors de cet
# ensemble en base (corruption, faute de frappe lors d'une correction
# manuelle) est traitée comme un mapping non résolu (repli STANDARD),
# jamais propagée telle quelle au moteur de calcul.
_VALID_CATEGORIES = {
    "STANDARD", "FOOD", "MEDICINES", "BOOKS", "CLOTHING",
    "SUPER_REDUCED", "PARKING",
}

_VALID_SOURCES = {"known_mapping", "manual_override", "unresolved_default"}

# ------------------------------------------------------------------
# Cache L1 (mémoire/process) + verrou de thread
# ------------------------------------------------------------------
# Table volontairement petite (dizaines de lignes, cf. décision section 4
# de la synthèse) : chargée en UNE fois par process, pas de granularité
# fine par clé comme _country_history_cache dans vat_rates_db.py.
_category_cache: dict[str, str] = {}
_cache_loaded = False
_cache_lock = threading.Lock()

_pool_lock = threading.Lock()
_schema_ready = False
_db_unavailable = False  # Sticky flag si BDD injoignable ou non configurée — voir vat_rates_db.py


def _get_pool() -> Optional[NonPoolingConnectionPool]:
    """Retourne le pool Postgres partagé ou dégrade silencieusement vers repli STANDARD.

    Réutilise le pool partagé (voir database.get_shared_pool, utilisé par
    auth.py/billing.py/ecb_rates.py/vies_engine.py/vat_rates_db.py) plutôt
    que d'ouvrir une connexion dédiée à ce module."""
    global _schema_ready, _db_unavailable
    if _db_unavailable:
        return None
    dsn = get_secret("SUPABASE_DB_URL")
    if not dsn:
        logger.debug("[PRODUCT_TAX_CODE_CATEGORY] SUPABASE_DB_URL non défini — repli STANDARD uniquement.")
        _db_unavailable = True
        return None
    try:
        pool = get_shared_pool(dsn)
        if not _schema_ready:
            with _pool_lock:
                if not _schema_ready:
                    _init_schema(pool)
                    _schema_ready = True
    except Exception as exc:
        logger.warning("[PRODUCT_TAX_CODE_CATEGORY] Postgres indisponible (%s) — repli STANDARD.", exc)
        _db_unavailable = True
        return None
    return pool


def close_idle_connections() -> None:
    """Libère les connexions inactives au début de chaque run (appelé par app.py)."""
    _database_close_idle()


def _init_schema(pool: NonPoolingConnectionPool) -> None:
    """Crée product_tax_code_category si absente. Schéma section 4 de la
    synthèse taux_reduit_dynamique.md — validé par Matthieu."""
    conn = pool.getconn()
    try:
        with conn, conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS product_tax_code_category (
                    product_tax_code   VARCHAR(64) PRIMARY KEY,
                    category           VARCHAR(20) NOT NULL,
                    source             VARCHAR(20) NOT NULL,
                    resolved_at        TIMESTAMPTZ NOT NULL DEFAULT now()
                )
            """)
    finally:
        pool.putconn(conn)


def _load_categories() -> dict[str, str]:
    """Charge toute la table en mémoire (une seule requête par process,
    tant que clear_cache() n'est pas appelé) — table volontairement petite,
    pas de pagination ni de lookup unitaire nécessaire."""
    global _cache_loaded
    with _cache_lock:
        if _cache_loaded:
            return _category_cache

    pool = _get_pool()
    if pool is None:
        return {}
    conn = pool.getconn()
    try:
        with conn, conn.cursor() as cur:
            cur.execute("SELECT product_tax_code, category FROM product_tax_code_category")
            rows = cur.fetchall()
    except Exception as exc:
        logger.warning("[PRODUCT_TAX_CODE_CATEGORY] chargement Postgres échoué : %s", exc)
        # Pas de mise en cache "loaded" sur échec : on retentera au
        # prochain appel plutôt que de figer une table vide.
        return {}
    finally:
        pool.putconn(conn)

    with _cache_lock:
        for code, category in rows:
            category = (category or "").strip().upper()
            _category_cache[code] = category if category in _VALID_CATEGORIES else "STANDARD"
        _cache_loaded = True
    return _category_cache


def _record_unresolved(product_tax_code: str) -> None:
    """Audit d'un code jamais vu : écrit en base avec source='unresolved_default',
    catégorie STANDARD. Idempotent (ON CONFLICT DO NOTHING — un autre thread/run
    a pu l'écrire entre-temps), et sans effet si Postgres est indisponible."""
    pool = _get_pool()
    if pool is None:
        return
    conn = pool.getconn()
    try:
        with conn, conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO product_tax_code_category (product_tax_code, category, source)
                VALUES (%s, 'STANDARD', 'unresolved_default')
                ON CONFLICT (product_tax_code) DO NOTHING
                """,
                (product_tax_code,),
            )
    except Exception as exc:
        logger.warning("[PRODUCT_TAX_CODE_CATEGORY] audit unresolved échoué pour %s : %s",
                        product_tax_code, exc)
    finally:
        pool.putconn(conn)


def map_product_tax_code_to_category(product_tax_code: str) -> str:
    """Résout un PRODUCT_TAX_CODE Amazon (ex. A_GEN_STANDARD, A_BOOKS_GEN)
    en catégorie interne (STANDARD, FOOD, MEDICINES, BOOKS, CLOTHING,
    SUPER_REDUCED, PARKING).

    Code vide ou jamais vu -> STANDARD (repli sûr, jamais de blocage du
    calcul). Un code jamais vu est audité en base (source='unresolved_default')
    pour permettre une correction manuelle a posteriori — voir docstring
    du module pour la décision de ne PAS recalculer à la volée depuis
    cette table (contrairement à l'ancien asin_to_category)."""
    code = (product_tax_code or "").strip().upper()
    if not code:
        return "STANDARD"

    categories = _load_categories()
    category = categories.get(code)
    if category is not None:
        return category

    _record_unresolved(code)
    with _cache_lock:
        # Évite de ré-auditer (et de refaire un aller-retour Postgres) à
        # chaque ligne du même run pour un code déjà rencontré une fois.
        _category_cache[code] = "STANDARD"
    return "STANDARD"


def clear_cache(persistent: bool = False) -> None:
    """Vide le cache mémoire L1. persistent=True force aussi la
    revérification du schéma Postgres au prochain accès (utile en tests)."""
    global _cache_loaded, _schema_ready, _db_unavailable
    with _cache_lock:
        _category_cache.clear()
        _cache_loaded = False
    if persistent:
        _schema_ready = False
        _db_unavailable = False


def cache_info() -> dict:
    return {
        "db_configured": not _db_unavailable,
        "cache_loaded": _cache_loaded,
        "entries": len(_category_cache),
    }
