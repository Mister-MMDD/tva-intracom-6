"""Taux de change EUR via l'API de la Banque Centrale Europeenne (BCE/ECB).

Utilise le service SDW (Statistical Data Warehouse) de la BCE qui fournit
les taux de reference quotidiens sans cle API.

Endpoint : https://data-api.ecb.europa.eu/service/data/EXR/D.{CCY}.EUR.SP00.A

Optimisations :
  - Cache deux niveaux : mémoire (dict, par process) + table Postgres globale
    `ecb_rate_cache` (même base Supabase que auth.py / billing.py / vies_engine.py),
    partagée entre toutes les instances Streamlit Cloud et persistante entre
    redéploiements. Contrairement au cache VIES, il n'y a pas de scope par
    compte : un taux BCE (devise, date) est une donnée de marché publique,
    identique pour tout le monde — une seule table plate suffit, sans aucune
    donnée personnelle.
  - prefetch_rates() : pré-charge en parallèle toutes les devises/dates d'un
    fichier en un seul appel avant le traitement ligne par ligne.
  - Le module reste utilisable sans base configurée (SUPABASE_DB_URL absent,
    tests, environnement local) : dans ce cas on retombe silencieusement sur
    le cache mémoire seul (pas de persistance inter-instances, mais aucun
    plantage).
"""

from __future__ import annotations

import json
import logging
import re
import threading
import time
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from typing import Optional

import psycopg2
import psycopg2.pool
import psycopg2.extras

from .config import get_secret

logger = logging.getLogger(__name__)

ECB_BASE_URL = "https://data-api.ecb.europa.eu/service/data/EXR"

SUPPORTED_CURRENCIES = {
    "USD", "GBP", "JPY", "CHF", "SEK", "DKK", "NOK", "PLN", "CZK",
    "HUF", "RON", "BGN", "TRY", "AUD", "CAD", "CNY", "INR",
    "BRL", "MXN", "SGD", "KRW", "THB", "ZAR",
    # HRK (kuna croate) retiré : la Croatie a rejoint la zone euro le 01/01/2023.
    # Pour les fichiers historiques antérieurs à 2023 contenant des HRK,
    # le taux de conversion fixe officiel est 1 EUR = 7,53450 HRK (Règl. UE 2022/1540).
}

_CENT = Decimal("0.01")

# ------------------------------------------------------------------
# Cache deux niveaux : mémoire (process) + Postgres global (persistant)
# ------------------------------------------------------------------
_rate_cache: dict[str, Decimal] = {}   # clé : "CCY|YYYY-MM-DD" (L1, par process)
# Verrou unique protégeant _rate_cache. Nécessaire car prefetch_rates() utilise
# ThreadPoolExecutor : plusieurs threads écrivent dans _rate_cache simultanément.
_cache_lock = threading.Lock()

# Durée de conservation des taux en base avant purge.
# On garde 10 ans par défaut car un taux de change historique ne change jamais 
# et prend très peu de place. Évite de re-télécharger des années de données 
# lors de retraitements de fichiers anciens.
_RETENTION_DAYS = 3650

_pool: Optional["psycopg2.pool.ThreadedConnectionPool"] = None
_pool_lock = threading.Lock()
# Sticky : évite de retenter une connexion Postgres à chaque appel si
# SUPABASE_DB_URL n'est pas configuré (tests, dev local) ou si la base est
# injoignable. Le cache BCE est un pur confort de performance/coût réseau,
# jamais une dépendance dure — on dégrade silencieusement vers "mémoire seule".
_db_unavailable = False


def _cache_key(currency: str, d: date) -> str:
    return f"{currency.upper()}|{d.isoformat()}"


def _get_pool() -> Optional["psycopg2.pool.ThreadedConnectionPool"]:
    global _pool, _db_unavailable
    if _db_unavailable:
        return None
    if _pool is not None:
        return _pool
    with _pool_lock:
        if _pool is not None:
            return _pool
        if _db_unavailable:
            return None
        dsn = get_secret("SUPABASE_DB_URL")
        if not dsn:
            logger.debug(
                "SUPABASE_DB_URL non défini — cache BCE en mémoire uniquement "
                "(pas de persistance inter-instances/redéploiements)."
            )
            _db_unavailable = True
            return None
        try:
            pool = psycopg2.pool.ThreadedConnectionPool(1, 5, dsn, sslmode="require")
            _init_schema(pool)
        except Exception as exc:
            logger.warning(
                "Cache BCE : connexion Postgres indisponible (%s) — repli "
                "mémoire uniquement pour cette session.", exc,
            )
            _db_unavailable = True
            return None
        _pool = pool
        return _pool


def _init_schema(pool: "psycopg2.pool.ThreadedConnectionPool") -> None:
    conn = pool.getconn()
    try:
        with conn, conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS ecb_rate_cache (
                    currency   TEXT NOT NULL,
                    rate_date  DATE NOT NULL,
                    rate       NUMERIC NOT NULL,
                    fetched_at TIMESTAMPTZ NOT NULL,
                    PRIMARY KEY (currency, rate_date)
                )
            """)
            # Table globale, pas de scope_id : un taux BCE est une donnée de
            # marché publique identique pour tous les comptes — inutile de la
            # dupliquer par utilisateur comme vies_scope_cache.
            cur.execute("""
                DELETE FROM ecb_rate_cache
                 WHERE rate_date < %s
            """, (date.today() - timedelta(days=_RETENTION_DAYS),))
    finally:
        pool.putconn(conn)


def _db_get_rates_batch(currency_dates: list[tuple[str, date]]) -> dict[tuple[str, date], Decimal]:
    """Récupère plusieurs taux depuis la base de données en une seule requête.
    
    Optimisé pour prefetch_rates() afin d'éviter N requêtes SQL individuelles.
    """
    if not currency_dates:
        return {}
    pool = _get_pool()
    if pool is None:
        return {}
    
    # On filtre par devises et par plage de dates globale pour rester simple et performant
    currencies = list(set(c.upper() for c, d in currency_dates))
    min_date = min(d for c, d in currency_dates)
    max_date = max(d for c, d in currency_dates)
    
    conn = pool.getconn()
    try:
        results = {}
        with conn, conn.cursor() as cur:
            # On utilise ANY pour les devises
            cur.execute(
                """
                SELECT currency, rate_date, rate 
                FROM ecb_rate_cache 
                WHERE currency = ANY(%s) AND rate_date >= %s AND rate_date <= %s
                """,
                (currencies, min_date, max_date),
            )
            for ccy, d, rate in cur.fetchall():
                # On ne garde que ce qui a été demandé (la requête par plage peut ramener plus)
                results[(ccy.upper(), d)] = Decimal(str(rate))
        return results
    except Exception as exc:
        logger.warning("Cache BCE : lecture batch Postgres échouée : %s", exc)
        return {}
    finally:
        pool.putconn(conn)


def _db_get_rate(currency: str, target_date: date) -> Optional[Decimal]:
    pool = _get_pool()
    if pool is None:
        return None
    conn = pool.getconn()
    try:
        with conn, conn.cursor() as cur:
            cur.execute(
                "SELECT rate FROM ecb_rate_cache WHERE currency = %s AND rate_date = %s",
                (currency, target_date),
            )
            row = cur.fetchone()
            return Decimal(str(row[0])) if row else None
    except Exception as exc:
        logger.warning("Cache BCE : lecture Postgres échouée pour %s/%s : %s", currency, target_date, exc)
        return None
    finally:
        pool.putconn(conn)


def _db_upsert_rate(currency: str, target_date: date, rate: Decimal) -> None:
    _db_upsert_batch([(currency, target_date, rate)])


def _db_upsert_batch(entries: list[tuple[str, date, Decimal]]) -> None:
    """Enregistre plusieurs (devise, date, taux) en une seule transaction.

    Utilisé par prefetch_rates() pour éviter un aller-retour réseau par taux.
    Utilise execute_values pour des performances optimales sur les gros lots.
    """
    if not entries:
        return
    pool = _get_pool()
    if pool is None:
        return
    now = datetime.now(timezone.utc)
    conn = pool.getconn()
    try:
        with conn, conn.cursor() as cur:
            psycopg2.extras.execute_values(
                cur,
                """
                INSERT INTO ecb_rate_cache (currency, rate_date, rate, fetched_at)
                VALUES %s
                ON CONFLICT (currency, rate_date) DO NOTHING
                """,
                [(ccy, d, rate, now) for ccy, d, rate in entries],
            )
    except Exception as exc:
        logger.warning("Cache BCE : écriture Postgres échouée (%d entrées) : %s", len(entries), exc)
    finally:
        pool.putconn(conn)


# ------------------------------------------------------------------
# Requête HTTP
# ------------------------------------------------------------------

# Backoff exponentiel sur erreurs réseau/HTTP transitoires (dont HTTP 429).
# Ne couvre PAS les réponses malformées (JSON invalide, structure inattendue) :
# une réponse mal formée n'est pas transitoire, la retenter ne change rien.
_FETCH_MAX_ATTEMPTS = 3
_FETCH_BACKOFF_BASE_SECONDS = 1.0  # 1s, puis 2s, puis 4s


def _request_ecb(url: str, description: str) -> Optional[dict]:
    """Effectue une requête à l'API BCE avec gestion des retries."""
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    for attempt in range(1, _FETCH_MAX_ATTEMPTS + 1):
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except (urllib.error.URLError, urllib.error.HTTPError, OSError) as exc:
            is_last_attempt = attempt >= _FETCH_MAX_ATTEMPTS
            if is_last_attempt:
                logger.warning(
                    "ECB API indisponible (%s) après %d tentative(s) : %s",
                    description, attempt, exc,
                )
                return None
            delay = _FETCH_BACKOFF_BASE_SECONDS * (2 ** (attempt - 1))
            logger.debug(
                "ECB API échec (%s, tentative %d/%d) : %s — retry dans %.0fs",
                description, attempt, _FETCH_MAX_ATTEMPTS, exc, delay,
            )
            time.sleep(delay)
        except (json.JSONDecodeError, ValueError) as exc:
            logger.warning("Réponse ECB non parsable (%s) : %s", description, exc)
            return None
    return None


def _fetch_ecb_rate(currency: str, target_date: date) -> Optional[Decimal]:
    """Interroge l'API ECB pour EUR/{currency} à une date donnée.

    Élargit la fenêtre à 7 jours pour couvrir weekends/jours fériés.
    """
    currency = currency.upper()
    if currency == "EUR":
        return Decimal("1")

    start = target_date - timedelta(days=7)
    end   = target_date
    key   = f"D.{currency}.EUR.SP00.A"
    url   = (
        f"{ECB_BASE_URL}/{key}"
        f"?startPeriod={start.isoformat()}"
        f"&endPeriod={end.isoformat()}"
        f"&detail=dataonly"
        f"&format=jsondata"
    )

    data = _request_ecb(url, f"{currency} au {target_date}")
    if data is None:
        return None

    try:
        observations = data["dataSets"][0]["series"]["0:0:0:0:0"]["observations"]
        last_key = max(observations.keys(), key=int)
        return Decimal(str(observations[last_key][0]))
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        logger.warning("Structure ECB inattendue pour %s : %s", currency, exc)
        return None


def _fetch_ecb_batch(
    currencies: list[str], start_date: date, end_date: date
) -> dict[str, dict[date, Decimal]]:
    """Récupère les taux pour plusieurs devises sur une période donnée.

    Utilise une seule requête groupée (batch) pour optimiser les performances
    sur les fichiers multi-années.
    """
    if not currencies:
        return {}

    # On demande 7 jours de plus au début pour avoir un taux de repli (weekend/férié)
    # pour le premier jour de la période demandée.
    start = start_date - timedelta(days=7)
    ccy_key = "+".join(sorted(set(c.upper() for c in currencies)))
    url = (
        f"{ECB_BASE_URL}/D.{ccy_key}.EUR.SP00.A"
        f"?startPeriod={start.isoformat()}"
        f"&endPeriod={end_date.isoformat()}"
        f"&detail=dataonly"
        f"&format=jsondata"
    )

    data = _request_ecb(url, f"batch {ccy_key} du {start} au {end_date}")
    if not data:
        return {}

    try:
        # 1. Extraire la liste des dates (dimension observation)
        dim_obs = data["structure"]["dimensions"]["observation"]
        date_list = [date.fromisoformat(d["id"]) for d in dim_obs[0]["values"]]

        # 2. Identifier la dimension CURRENCY dans les séries
        series_dims = data["structure"]["dimensions"]["series"]
        ccy_dim_idx = -1
        for i, dim in enumerate(series_dims):
            if dim["id"] == "CURRENCY":
                ccy_dim_idx = i
                break
        if ccy_dim_idx == -1:
            return {}

        ccy_list = [v["id"] for v in series_dims[ccy_dim_idx]["values"]]

        # 3. Extraire les taux pour chaque série
        results: dict[str, dict[date, Decimal]] = {}
        all_series = data["dataSets"][0]["series"]
        for s_key, s_data in all_series.items():
            indices = [int(i) for i in s_key.split(":")]
            ccy = ccy_list[indices[ccy_dim_idx]]
            
            ccy_rates: dict[date, Decimal] = {}
            obs = s_data.get("observations", {})
            for idx_str, val_list in obs.items():
                d = date_list[int(idx_str)]
                ccy_rates[d] = Decimal(str(val_list[0]))
            results[ccy] = ccy_rates
        
        return results
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        logger.warning("Erreur lors du parsing du batch ECB : %s", exc)
        return {}


# ------------------------------------------------------------------
# API publique
# ------------------------------------------------------------------

def get_rate(currency: str, target_date: date) -> Optional[Decimal]:
    """Retourne le taux EUR/{currency} (unités de devise pour 1 EUR).

    Ordre de résolution : cache mémoire (L1, ce process) -> cache Postgres
    global (L2, partagé entre toutes les instances Streamlit Cloud et
    persistant entre redéploiements) -> API BCE en dernier recours.
    Thread-safe : _rate_cache protégé par _cache_lock. Le L2 est optionnel :
    s'il n'est pas configuré/joignable, on saute directement à l'API BCE.
    """
    currency = currency.upper()
    if currency == "EUR":
        return Decimal("1")

    key = _cache_key(currency, target_date)

    with _cache_lock:
        if key in _rate_cache:
            return _rate_cache[key]

    db_rate = _db_get_rate(currency, target_date)
    if db_rate is not None:
        with _cache_lock:
            _rate_cache[key] = db_rate
        return db_rate

    # Requête HTTP hors du lock pour ne pas bloquer les autres threads.
    rate = _fetch_ecb_rate(currency, target_date)

    if rate is not None:
        with _cache_lock:
            _rate_cache[key] = rate
        _db_upsert_rate(currency, target_date, rate)

    return rate


def prefetch_rates(
    currency_dates: list[tuple[str, date]],
    max_workers: int = 8,
    progress_callback=None,
) -> None:
    """Pré-charge les taux BCE pour une liste de (devise, date).

    Optimisé : 
      1. Vérifie le cache mémoire (L1).
      2. Vérifie la base de données en lot (L2).
      3. Utilise des requêtes par lots (batch) vers l'API BCE pour le reste.

    Args:
        currency_dates: liste de tuples (devise, date).
        max_workers: (obsolète pour le mode batch, conservé pour compatibilité).
        progress_callback: optionnel, callable(done: int, total: int).
    """
    _FIXED_RATE_CURRENCIES = {"EUR", "HRK"}
    requested: list[tuple[str, date]] = []
    seen: set[tuple[str, date]] = set()

    for currency, d in currency_dates:
        currency = currency.upper()
        if currency in _FIXED_RATE_CURRENCIES:
            continue
        key = _cache_key(currency, d)
        if key not in _rate_cache and (currency, d) not in seen:
            requested.append((currency, d))
            seen.add((currency, d))

    if not requested:
        logger.debug("Prefetch BCE : tous les taux déjà en cache mémoire.")
        if progress_callback:
            progress_callback(len(currency_dates), len(currency_dates))
        return

    total_requested = len(requested)
    
    # 1. Vérification du cache de la base de données (L2) en lot
    db_hits = _db_get_rates_batch(requested)
    if db_hits:
        loaded_from_db = 0
        for (ccy, d), rate in db_hits.items():
            key = _cache_key(ccy, d)
            with _cache_lock:
                _rate_cache[key] = rate
            loaded_from_db += 1
        
        # On ne garde que ce qui n'est toujours pas trouvé
        to_fetch = [pair for pair in requested if (pair[0].upper(), pair[1]) not in db_hits]
        
        logger.info(
            "Prefetch BCE : %d/%d taux récupérés depuis la DB.", 
            loaded_from_db, total_requested
        )
    else:
        to_fetch = requested

    if not to_fetch:
        logger.info("Prefetch BCE terminé : tout était déjà en cache (mémoire ou DB).")
        if progress_callback:
            progress_callback(total_requested, total_requested)
        return

    # 2. Chargement via API BCE pour le reliquat
    # Groupement par devises pour déterminer la période globale
    currencies = sorted({c for c, d in to_fetch})
    all_dates = [d for c, d in to_fetch]
    min_date = min(all_dates)
    max_date = max(all_dates)

    total = len(to_fetch)
    logger.info(
        "Prefetch BCE : Chargement batch API pour %d devises sur la période %s à %s (%d taux manquants)",
        len(currencies), min_date, max_date, total
    )

    # On signale le début du téléchargement
    if progress_callback:
        try:
            progress_callback(0, total)
        except Exception:
            pass

    batch_results = _fetch_ecb_batch(currencies, min_date, max_date)

    loaded = 0
    to_persist: list[tuple[str, date, Decimal]] = []

    # On remplit le cache pour les dates demandées en utilisant les résultats du batch
    # avec la règle du "dernier taux connu" (carry forward) pour les weekends/jours fériés.
    for i, (ccy, target_date) in enumerate(to_fetch, start=1):
        ccy = ccy.upper()
        rate = None
        if ccy in batch_results:
            ccy_rates = batch_results[ccy]
            # Recherche du taux exact ou du plus récent (jusqu'à 7 jours en arrière)
            for days in range(8):
                d = target_date - timedelta(days=days)
                if d in ccy_rates:
                    rate = ccy_rates[d]
                    break
        
        if rate is not None:
            key = _cache_key(ccy, target_date)
            with _cache_lock:
                _rate_cache[key] = rate
            to_persist.append((ccy, target_date, rate))
            loaded += 1
        
        # Optimisation : on ne rapporte le progrès que périodiquement pour éviter de saturer l'UI
        if progress_callback and (i % 200 == 0 or i == total):
            try:
                # On rapporte le progrès par rapport à to_fetch, mais on pourrait
                # aussi rapporter par rapport à total_requested.
                progress_callback(i, total)
            except Exception:
                pass

    # Une seule transaction Postgres pour tout le lot
    if to_persist:
        _db_upsert_batch(to_persist)
        
    logger.info("Prefetch BCE terminé : %d/%d taux mis en cache via API batch.", loaded, total)


def convert_to_eur(
    amount: Decimal,
    currency: str,
    target_date: date,
    fallback_rate: Optional[Decimal] = None,
) -> tuple[Decimal, Decimal, str]:
    """Convertit un montant en devise vers EUR au taux BCE du jour."""
    currency = currency.upper()
    if currency == "EUR":
        return amount, Decimal("1"), "eur"

    # HRK (kuna croate) : taux de conversion fixe et irrévocable depuis le 01/01/2023
    # (Règlement UE 2022/1540, art. 1). L'API BCE ne publie plus de cours pour HRK.
    if currency == "HRK":
        _HRK_FIXED = Decimal("7.53450")
        eur_amount = (amount / _HRK_FIXED).quantize(_CENT, rounding=ROUND_HALF_UP)
        logger.debug("HRK converti au taux fixe UE : 1 EUR = 7,53450 HRK")
        return eur_amount, _HRK_FIXED, "fixed_eur_hrk"

    rate = get_rate(currency, target_date)
    if rate is not None:
        eur_amount = (amount / rate).quantize(_CENT, rounding=ROUND_HALF_UP)
        return eur_amount, rate, "ecb"

    if fallback_rate is not None:
        eur_amount = (amount / fallback_rate).quantize(_CENT, rounding=ROUND_HALF_UP)
        return eur_amount, fallback_rate, "fallback"

    raise ValueError(
        f"Impossible d'obtenir le taux EUR/{currency} au {target_date}. "
        "Vérifiez la connexion Internet ou fournissez un taux de secours."
    )


def convert_to_currency(
    amount: Decimal,
    source_currency: str,
    target_currency: str,
    target_date: date,
    fallback_rate: Optional[Decimal] = None,
) -> tuple[Decimal, Decimal, str]:
    """Convertit un montant d'une devise source vers une devise cible via EUR.
    
    Retourne (montant_cible, taux_source_vers_cible, source_info).
    """
    source_currency = source_currency.upper()
    target_currency = target_currency.upper()
    
    # 1. Conversion source -> EUR
    eur_amount, rate_source, source_info = convert_to_eur(amount, source_currency, target_date, fallback_rate)
    
    if target_currency == "EUR":
        return eur_amount, rate_source, source_info
    
    # 2. Conversion EUR -> cible
    rate_target = get_rate(target_currency, target_date)
    if rate_target is None:
        # Fallback : si on ne peut pas avoir le taux cible, on reste en EUR et on avertit
        logger.warning("Taux pour devise cible %s indisponible au %s. Reste en EUR.", target_currency, target_date)
        return eur_amount, rate_source, source_info
    
    target_amount = (eur_amount * rate_target).quantize(_CENT, rounding=ROUND_HALF_UP)
    
    # Taux combiné (pour info)
    combined_rate = (rate_target / rate_source) if rate_source else rate_target
    
    return target_amount, combined_rate, f"{source_info}_to_{target_currency.lower()}"


def quarter_end_date(period: str) -> Optional[date]:
    """Calcule la date de clôture d'une période OSS pour la conversion devise.

    Le règlement d'exécution UE 2020/194 (modifiant les règles d'application
    OSS, art. 5 bis) impose d'utiliser le taux de change publié par la BCE
    le DERNIER JOUR de la période de déclaration — et non le taux du jour
    de chaque vente — lorsqu'une conversion en EUR est nécessaire pour l'OSS.

    Accepte les formats produits/normalisés par oss_xml.py :
        "2026-Q1" / "2026-T1" -> dernier jour du trimestre
        "2026"                -> 31 décembre
        "2026-S1"             -> dernier jour du semestre (30/06 ou 31/12)
    Les formats "plage" (2026-Q1_Q3, 2025-2026) ne sont pas couverts ici
    (déclarations multi-trimestres/années : à traiter période par période
    en amont) — retourne None dans ce cas, ce qui fait retomber l'appelant
    sur le comportement antérieur (taux du jour de la vente).

    Returns:
        La date de clôture, ou None si le format n'est pas reconnu.
    """
    if not period:
        return None
    p = period.strip().upper().replace("T", "Q")  # tolère le format FR "T"

    m = re.fullmatch(r"(\d{4})-Q([1-4])", p)
    if m:
        year, q = int(m.group(1)), int(m.group(2))
        month = q * 3
        if month == 12:
            return date(year, 12, 31)
        return date(year, month + 1, 1) - timedelta(days=1)

    m = re.fullmatch(r"(\d{4})-S([12])", p)
    if m:
        year, s = int(m.group(1)), int(m.group(2))
        return date(year, 6, 30) if s == 1 else date(year, 12, 31)

    m = re.fullmatch(r"(\d{4})", p)
    if m:
        return date(int(m.group(1)), 12, 31)

    return None


def convert_to_currency_for_oss(
    original_amount: Decimal,
    source_currency: str,
    target_currency: str,
    period: str,
    transaction_date: date,
    fallback_rate: Optional[Decimal] = None,
) -> tuple[Decimal, Decimal, str]:
    """Convertit un montant vers la devise cible avec le taux BCE de clôture de période OSS.

    Si `period` n'est pas reconnu (plage multi-trimestres/années), on retombe
    sur le taux du jour de la transaction (comportement précédent) pour ne
    pas bloquer un cas d'usage existant — à traiter période par période en amont.
    """
    source_currency = source_currency.upper()
    target_currency = target_currency.upper()
    if source_currency == target_currency:
        return original_amount, Decimal("1"), target_currency.lower()

    rate_date = quarter_end_date(period) or transaction_date
    return convert_to_currency(original_amount, source_currency, target_currency, rate_date, fallback_rate=fallback_rate)


def get_rates_for_dates(
    currency: str, dates: list[date]
) -> dict[str, Optional[Decimal]]:
    """Récupère les taux pour plusieurs dates (dédupliquées)."""
    unique_dates = sorted(set(dates))
    return {d.isoformat(): get_rate(currency, d) for d in unique_dates}


def clear_cache(persistent: bool = True) -> None:
    """Vide le cache mémoire (L1) et, par défaut, le cache Postgres global (L2).

    `persistent` conserve le nom de paramètre historique en gardant le même
    ordre d'appel (`clear_cache()` vide tout par défaut) pour ne pas casser
    les appelants existants (tests, CLI) ; il pilote maintenant la purge de
    la table `ecb_rate_cache` plutôt que d'un fichier disque.
    Si aucune base n'est configurée/joignable, la purge Postgres est un
    no-op silencieux (le cache mémoire est tout de même vidé).
    """
    _rate_cache.clear()
    if not persistent:
        return
    pool = _get_pool()
    if pool is None:
        return
    conn = pool.getconn()
    try:
        with conn, conn.cursor() as cur:
            cur.execute("DELETE FROM ecb_rate_cache")
    except Exception as exc:
        logger.warning("Impossible de vider le cache BCE Postgres : %s", exc)
    finally:
        pool.putconn(conn)


def cache_info() -> dict:
    """Infos sur l'état du cache (utile pour debug/UI)."""
    info: dict = {
        "memory_entries": len(_rate_cache),
        "memory_currencies": sorted({k.split("|")[0] for k in _rate_cache}),
        "db_configured": not _db_unavailable or _pool is not None,
        "db_entries": None,
        "db_oldest_date": None,
        "db_retention_days": _RETENTION_DAYS,
    }
    pool = _get_pool()
    if pool is None:
        return info
    conn = pool.getconn()
    try:
        with conn, conn.cursor() as cur:
            cur.execute("SELECT COUNT(*), MIN(rate_date) FROM ecb_rate_cache")
            count, oldest = cur.fetchone()
            info["db_entries"] = count
            info["db_oldest_date"] = oldest.isoformat() if oldest else None
    except Exception as exc:
        logger.warning("Cache BCE : lecture des stats Postgres échouée : %s", exc)
    finally:
        pool.putconn(conn)
    return info