"""Détection du format Amazon et normalisation des dates.

Fonctions pures sans état — aucune dépendance vers les autres sous-modules.
"""

from __future__ import annotations

from typing import Optional


def normalize_header(h: str) -> str:
    """Normalise un nom de colonne CSV en snake_case minuscule."""
    return h.strip().lower().replace(" ", "_").replace("-", "_")


def detect_separator(line: str) -> str:
    """Détecte le séparateur CSV dominant (tab / point-virgule / virgule).

    On compte les occurrences plutôt que "in" pour éviter les faux positifs :
    une virgule peut apparaître dans un nom de colonne sans être le séparateur.
    """
    counts = {"\t": line.count("\t"), ";": line.count(";"), ",": line.count(",")}
    best = max(counts, key=lambda s: counts[s])
    return best if counts[best] > 0 else ","


def detect_format(headers: set[str]) -> int:
    """Détecte le format Amazon (1–5) sur le set de headers normalisés.

    Format 5 : rapport fiscal V5 avec OUR_PRICE/SHIPPING/GIFTWRAP détaillés,
               TRANSACTION_ID, ORDER_DATE, juridictions multiples.
    Format 4 : CSV 2025+ avec TRANSACTION_COMPLETE_DATE + TAX_COLLECTION_RESPONSIBILITY
               (sans TAX_COLLECTION_MODEL).
    Format 3 : TSV/CSV avec TRANSACTION_COMPLETE_DATE + TAX_COLLECTION_MODEL.
    Format 2 : ACTIVITY_PERIOD sans TRANSACTION_COMPLETE_DATE.
    Format 1 : ancien format (fallback).
    """
    if (
        "our_price_tax_exclusive_selling_price" in headers
        and "transaction_id" in headers
        and "order_date" in headers
    ):
        return 5
    if "transaction_complete_date" in headers:
        if (
            "tax_collection_responsibility" in headers
            and "tax_collection_model" not in headers
        ):
            return 4
        return 3
    if "activity_period" in headers:
        return 2
    return 1


# Colonnes critiques attendues par format — utilisées pour les warnings d'import.
EXPECTED_COLUMNS: dict[int, list[str]] = {
    3: [
        "transaction_complete_date", "tax_collection_model",
        "total_activity_value_amt_vat_excl", "sale_depart_country", "sale_arrival_country",
    ],
    4: [
        "transaction_complete_date", "tax_collection_responsibility",
        "total_activity_value_amt_vat_excl", "sale_depart_country", "sale_arrival_country",
        "activity_transaction_id",
    ],
    5: [
        "transaction_id", "order_date", "transaction_type",
        "our_price_tax_exclusive_selling_price", "shipping_tax_exclusive_selling_price",
        "giftwrap_tax_exclusive_selling_price", "ship_from_country", "ship_to_country",
        "tax_collection_responsibility", "jurisdiction_level", "currency",
        "invoice_level_exchange_rate",
    ],
}


def parse_date(date_str: str) -> str:
    """Normalise une date Amazon vers YYYY-MM-DD. Retourne '' si invalide.

    Formats reconnus :
      YYYY-MM-DD                → inchangé
      YYYY-MM-DD HH:MM:SS       → tronqué à la date
      DD.MM.YYYY                → inversé
      DD-MM-YYYY                → inversé (format V5 Amazon EU)
    """
    s = date_str.strip()
    if not s:
        return ""
    # Tronquer si datetime complet : "2026-05-01 10:49:00" → "2026-05-01"
    if " " in s:
        s = s.split(" ")[0]
    # DD.MM.YYYY
    if "." in s:
        parts = s.split(".")
        if len(parts) == 3:
            try:
                return f"{int(parts[2]):04d}-{int(parts[1]):02d}-{int(parts[0]):02d}"
            except ValueError:
                pass
    # DD-MM-YYYY (V5 Amazon EU : "30-03-2026") vs YYYY-MM-DD
    # Distingué par la longueur de la dernière partie (4 chiffres = année)
    if "-" in s:
        parts = s.split("-")
        if len(parts) == 3 and len(parts[2]) == 4:
            try:
                return f"{int(parts[2]):04d}-{int(parts[1]):02d}-{int(parts[0]):02d}"
            except ValueError:
                pass
    return s  # déjà YYYY-MM-DD ou format inconnu
