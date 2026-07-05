"""Taux de change EUR via l'API de la Banque Centrale Europeenne (BCE/ECB).

Utilise le service SDW (Statistical Data Warehouse) de la BCE qui fournit
les taux de reference quotidiens sans cle API.

Endpoint : https://data-api.ecb.europa.eu/service/data/EXR/D.{CCY}.EUR.SP00.A

Optimisations :
  - Cache deux niveaux : mémoire (dict) + disque (JSON ~/.cache/tva_intracom/)
  - prefetch_rates() : pré-charge en parallèle toutes les devises/dates d'un
    fichier en un seul appel avant le traitement ligne par ligne.
"""

from __future__ import annotations

import json
import logging
import pathlib
import re
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP
from typing import Optional

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
# Cache deux niveaux
# ------------------------------------------------------------------
_CACHE_DIR  = pathlib.Path.home() / ".cache" / "tva_intracom"
_CACHE_FILE = _CACHE_DIR / "ecb_rates.json"

_rate_cache: dict[str, Decimal] = {}   # clé : "CCY|YYYY-MM-DD"
_unsaved_count: int = 0                # nouvelles entrées non encore écrites sur disque
_SAVE_BATCH_SIZE: int = 10             # écriture disque toutes les N nouvelles entrées
# Verrou unique protégeant à la fois _rate_cache, _unsaved_count et _save_disk_cache().
# Nécessaire car prefetch_rates() utilise ThreadPoolExecutor : plusieurs threads
# écrivent dans _rate_cache simultanément, et _save_disk_cache() lit le dict entier.
_cache_lock = threading.Lock()


def _cache_key(currency: str, d: date) -> str:
    return f"{currency.upper()}|{d.isoformat()}"


def _load_disk_cache() -> None:
    if not _CACHE_FILE.exists():
        return
    try:
        raw = json.loads(_CACHE_FILE.read_text(encoding="utf-8"))
        with _cache_lock:
            for k, v in raw.items():
                _rate_cache[k] = Decimal(v)
        logger.debug("Cache BCE chargé : %d entrées depuis %s", len(_rate_cache), _CACHE_FILE)
    except Exception as exc:
        logger.warning("Cache BCE disque illisible, ignoré : %s", exc)


def _save_disk_cache() -> None:
    # Snapshot sous lock pour éviter une corruption si un thread écrit dans
    # _rate_cache pendant la sérialisation JSON.
    try:
        with _cache_lock:
            snapshot = dict(_rate_cache)
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
        data = {k: str(v) for k, v in snapshot.items()}
        _CACHE_FILE.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    except Exception as exc:
        logger.warning("Impossible d'écrire le cache BCE : %s", exc)


_load_disk_cache()


# ------------------------------------------------------------------
# Requête HTTP
# ------------------------------------------------------------------

# Backoff exponentiel sur erreurs réseau/HTTP transitoires (dont HTTP 429).
# Ne couvre PAS les réponses malformées (JSON invalide, structure inattendue) :
# une réponse mal formée n'est pas transitoire, la retenter ne change rien.
_FETCH_MAX_ATTEMPTS = 3
_FETCH_BACKOFF_BASE_SECONDS = 1.0  # 1s, puis 2s, puis 4s


def _fetch_ecb_rate(currency: str, target_date: date) -> Optional[Decimal]:
    """Interroge l'API ECB pour EUR/{currency} à une date donnée.

    Élargit la fenêtre à 7 jours pour couvrir weekends/jours fériés.
    Retente jusqu'à _FETCH_MAX_ATTEMPTS fois avec un délai exponentiel
    (1s / 2s / 4s) en cas d'erreur réseau ou HTTP transitoire (429, 5xx…).
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
    req = urllib.request.Request(url, headers={"Accept": "application/json"})

    data = None
    for attempt in range(1, _FETCH_MAX_ATTEMPTS + 1):
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            break  # succès, on sort de la boucle de retry
        except (urllib.error.URLError, urllib.error.HTTPError, OSError) as exc:
            is_last_attempt = attempt >= _FETCH_MAX_ATTEMPTS
            if is_last_attempt:
                logger.warning(
                    "ECB API indisponible pour %s au %s après %d tentative(s) : %s",
                    currency, target_date, attempt, exc,
                )
                return None
            delay = _FETCH_BACKOFF_BASE_SECONDS * (2 ** (attempt - 1))
            logger.debug(
                "ECB API échec pour %s au %s (tentative %d/%d) : %s — retry dans %.0fs",
                currency, target_date, attempt, _FETCH_MAX_ATTEMPTS, exc, delay,
            )
            time.sleep(delay)
        except (json.JSONDecodeError, ValueError) as exc:
            # Non transitoire : inutile de retenter.
            logger.warning("Réponse ECB non parsable : %s", exc)
            return None

    if data is None:
        return None

    try:
        observations = data["dataSets"][0]["series"]["0:0:0:0:0"]["observations"]
        last_key = max(observations.keys(), key=int)
        return Decimal(str(observations[last_key][0]))
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        logger.warning("Structure ECB inattendue pour %s : %s", currency, exc)
        return None


# ------------------------------------------------------------------
# API publique
# ------------------------------------------------------------------

def get_rate(currency: str, target_date: date) -> Optional[Decimal]:
    """Retourne le taux EUR/{currency} (unités de devise pour 1 EUR).

    Vérifie le cache mémoire en premier, interroge l'API BCE si absent.
    La persistance disque est batché : écriture toutes les _SAVE_BATCH_SIZE
    nouvelles entrées (et toujours en fin de prefetch_rates). Évite des
    centaines d'écritures disque sur un gros fichier Amazon.
    Thread-safe : _rate_cache et _unsaved_count protégés par _cache_lock.
    """
    global _unsaved_count

    currency = currency.upper()
    if currency == "EUR":
        return Decimal("1")

    key = _cache_key(currency, target_date)

    with _cache_lock:
        if key in _rate_cache:
            return _rate_cache[key]

    # Requête HTTP hors du lock pour ne pas bloquer les autres threads.
    rate = _fetch_ecb_rate(currency, target_date)

    if rate is not None:
        with _cache_lock:
            _rate_cache[key] = rate
            _unsaved_count += 1
            do_save = _unsaved_count >= _SAVE_BATCH_SIZE
            if do_save:
                _unsaved_count = 0
        if do_save:
            _save_disk_cache()

    return rate


def prefetch_rates(
    currency_dates: list[tuple[str, date]],
    max_workers: int = 8,
    progress_callback=None,
) -> None:
    """Pré-charge en parallèle les taux BCE pour une liste de (devise, date).

    À appeler UNE FOIS avant le traitement d'un fichier. Les taux sont
    mis en cache mémoire + disque et réutilisés automatiquement par get_rate().
    Les paires déjà en cache sont ignorées (pas de requête inutile).

    Args:
        currency_dates: liste de tuples (devise, date).
        max_workers: threads parallèles (défaut 8, limité par l'API BCE).
        progress_callback: optionnel, callable(done: int, total: int) appelé
            après chaque taux traité, depuis le thread principal (sûr avec
            les widgets Streamlit type st.progress appelés par app.py).
            `total` correspond au nombre de taux réellement à récupérer
            (paires déjà en cache exclues) — si tout est déjà en cache,
            le callback n'est jamais appelé.
    """
    # Dédupliquer + ignorer ce qui est déjà en cache.
    # HRK exclu : taux fixe irrévocable depuis le 01/01/2023, pas d'appel BCE nécessaire.
    _FIXED_RATE_CURRENCIES = {"EUR", "HRK"}
    to_fetch: list[tuple[str, date]] = []
    seen: set[str] = set()
    for currency, d in currency_dates:
        currency = currency.upper()
        if currency in _FIXED_RATE_CURRENCIES:
            continue
        key = _cache_key(currency, d)
        if key not in _rate_cache and key not in seen:
            to_fetch.append((currency, d))
            seen.add(key)

    if not to_fetch:
        logger.debug("Prefetch BCE : tous les taux déjà en cache.")
        return

    logger.info(
        "Prefetch BCE : %d taux à charger (%d threads)...",
        len(to_fetch), min(max_workers, len(to_fetch))
    )

    def _fetch_one(args: tuple[str, date]) -> tuple[str, Optional[Decimal]]:
        currency, d = args
        return _cache_key(currency, d), _fetch_ecb_rate(currency, d)

    total = len(to_fetch)
    done = 0
    loaded = 0
    with ThreadPoolExecutor(max_workers=min(max_workers, len(to_fetch))) as pool:
        futures = {pool.submit(_fetch_one, item): item for item in to_fetch}
        for future in as_completed(futures):
            key, rate = future.result()
            if rate is not None:
                with _cache_lock:
                    _rate_cache[key] = rate
                loaded += 1
            done += 1
            if progress_callback is not None:
                try:
                    progress_callback(done, total)
                except Exception:
                    pass

    if loaded:
        _save_disk_cache()
    logger.info("Prefetch BCE terminé : %d/%d taux chargés.", loaded, len(to_fetch))


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


def convert_to_eur_for_oss(
    original_amount: Decimal,
    currency: str,
    period: str,
    transaction_date: date,
    fallback_rate: Optional[Decimal] = None,
) -> tuple[Decimal, Decimal, str]:
    """Convertit un montant en EUR avec le taux BCE de clôture de période OSS.

    Si `period` n'est pas reconnu (plage multi-trimestres/années), on retombe
    sur le taux du jour de la transaction (comportement précédent) pour ne
    pas bloquer un cas d'usage existant — à traiter période par période en amont.
    """
    currency = currency.upper()
    if currency == "EUR":
        return original_amount, Decimal("1"), "eur"

    rate_date = quarter_end_date(period) or transaction_date
    return convert_to_eur(original_amount, currency, rate_date, fallback_rate=fallback_rate)


def get_rates_for_dates(
    currency: str, dates: list[date]
) -> dict[str, Optional[Decimal]]:
    """Récupère les taux pour plusieurs dates (dédupliquées)."""
    unique_dates = sorted(set(dates))
    return {d.isoformat(): get_rate(currency, d) for d in unique_dates}


def clear_cache(disk: bool = True) -> None:
    """Vide le cache mémoire et optionnellement le disque."""
    _rate_cache.clear()
    if disk and _CACHE_FILE.exists():
        try:
            _CACHE_FILE.unlink()
        except Exception as exc:
            logger.warning("Impossible de supprimer le cache disque : %s", exc)


def cache_info() -> dict:
    """Infos sur l'état du cache (utile pour debug/UI)."""
    return {
        "entries": len(_rate_cache),
        "disk_file": str(_CACHE_FILE),
        "disk_exists": _CACHE_FILE.exists(),
        "disk_size_kb": round(_CACHE_FILE.stat().st_size / 1024, 1) if _CACHE_FILE.exists() else 0,
        "currencies": sorted({k.split("|")[0] for k in _rate_cache}),
    }