"""Pré-agrégation des lignes multi-juridictions du format Amazon V5.

Le format V5 émet une ligne par juridiction fiscale (COUNTRY, STATE, CITY…)
pour la même transaction. Ce module regroupe ces lignes en une seule
ligne logique avant le traitement par le moteur.

Responsabilité unique : transformer une liste de lignes brutes V5 en une
liste de lignes agrégées (une par acte de vente).
"""

from __future__ import annotations

import logging
from typing import Iterator

from .constants import safe_decimal

logger = logging.getLogger(__name__)

# Colonnes de montants à sommer sur toutes les lignes d'une transaction
_AMOUNT_COLS: list[str] = [
    "our_price_tax_exclusive_selling_price",
    "shipping_tax_exclusive_selling_price",
    "giftwrap_tax_exclusive_selling_price",
    "our_price_tax_exclusive_promo_amount",
    "shipping_tax_exclusive_promo_amount",
    "giftwrap_tax_exclusive_promo_amount",
    "our_price_tax_amount",
    "shipping_tax_amount",
    "giftwrap_tax_amount",
]


def _aggregate_group(rows: list[dict]) -> dict:
    """Agrège toutes les lignes d'une même clé en une seule ligne logique.

    Stratégie :
    - Métadonnées (pays, devise, type, dates, ASIN…) : issues de la première
      ligne de niveau COUNTRY, ou de la première ligne si aucune n'est COUNTRY.
    - Montants HT et TVA Amazon : sommés sur toutes les lignes.

    Cette approche est correcte pour l'UE (une seule ligne COUNTRY par
    transaction). Pour les pays multi-niveaux (US, CA, BR…), la somme donne
    la charge fiscale totale.
    """
    if not rows:
        return {}

    # Ligne de référence : préférer la ligne COUNTRY pour les métadonnées
    ref = next(
        (r for r in rows if r.get("jurisdiction_level", "").strip().lower() == "country"),
        rows[0],
    )

    aggregated = dict(ref)
    for col in _AMOUNT_COLS:
        total = sum(safe_decimal(r.get(col, "")) for r in rows)
        aggregated[col] = str(total)
    aggregated["_v5_row_count"] = str(len(rows))
    return aggregated


def preaggregate_v5(
    raw_rows: list[dict],
    parser,   # _Format5Parser — évite l'import circulaire
) -> tuple[list[tuple[int, dict]], frozenset]:
    """Regroupe les lignes brutes V5 par clé (VAT Invoice + ASIN + Type).

    Clé d'agrégation choisie : VAT Invoice Number + ASIN + Transaction Type.

    Pourquoi pas Transaction ID ?
      Transaction ID == Shipment ID dans ce format. Un même Shipment peut
      couvrir plusieurs Order ID distincts (tournée logistique Amazon).

    Pourquoi pas Order ID + ASIN ?
      Certaines commandes multi-articles partagent un même Order ID + ASIN
      avec des prix différents (multi-lignes légitimes).

    La clé commerciale unique dans ce format est donc :
      VAT Invoice Number (facture Amazon) + ASIN + Type de transaction.
    Fallback (pas de VAT Invoice) : Order ID + ASIN + Type.

    Returns:
        rows_to_process : liste de (line_no, row_agrégée)
        multi_asin_orders : frozenset des (order_id, tx_type) couvrant
                            plusieurs ASIN (pour construire le sale_id).
    """
    # Pré-calcul : Order ID partagés entre plusieurs ASIN (même type tx)
    # → sale_id sera "ORDER_ID (ASIN)" pour les distinguer
    order_tx_to_asins: dict[tuple, set] = {}
    for row in raw_rows:
        oid  = row.get("order_id", "").strip()
        asin = row.get("asin", "").strip()
        tx   = row.get("transaction_type", "").strip().upper()
        if oid:
            order_tx_to_asins.setdefault((oid, tx), set()).add(asin)

    multi_asin_orders: frozenset = frozenset(
        ot for ot, asins in order_tx_to_asins.items() if len(asins) > 1
    )

    # Regroupement par clé
    groups: dict[str, list[dict]] = {}
    line_order: list[str] = []
    line_no_by_key: dict[str, int] = {}
    fallback_counter = 0

    for line_no, row in enumerate(raw_rows, start=2):
        vat_invoice = row.get("vat_invoice_number", "").strip()
        asin        = row.get("asin", "").strip()
        tx_type     = row.get("transaction_type", "").strip().upper()

        # "N/A" = valeur littérale Amazon pour "pas de facture TVA"
        if vat_invoice and vat_invoice.upper() != "N/A":
            agg_key = f"{vat_invoice}|{asin}|{tx_type}"
        else:
            order_id = row.get("order_id", "").strip()
            if order_id:
                agg_key = f"__NOINV__|{order_id}|{asin}|{tx_type}"
            else:
                fallback_counter += 1
                agg_key = f"__NOINV__|L{line_no}|{fallback_counter}"

        if agg_key not in groups:
            groups[agg_key] = []
            line_order.append(agg_key)
            line_no_by_key[agg_key] = line_no
        groups[agg_key].append(row)

    rows_to_process = [
        (line_no_by_key[key], _aggregate_group(groups[key]))
        for key in line_order
    ]

    logger.info(
        "Format 5 : %d lignes brutes → %d transactions agrégées "
        "(clé : VAT Invoice Number + ASIN + Transaction Type).",
        len(raw_rows), len(rows_to_process),
    )
    return rows_to_process, multi_asin_orders
