"""Classes de parsing par format Amazon (1 à 5).

Chaque classe extrait les champs bruts d'une ligne CSV normalisée.
Aucune logique métier ici : pas de classification B2B/B2C, pas de conversion devise.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Optional

from .constants import currency_from_marketplace, safe_decimal
from .detect import parse_date


class _RowParser:
    """Interface commune pour extraire les champs d'une ligne normalisée."""

    def tx_type(self, row: dict) -> str:
        raise NotImplementedError

    def sale_id(self, row: dict, line_no: int) -> str:
        raise NotImplementedError

    def departure(self, row: dict) -> str:
        raise NotImplementedError

    def arrival(self, row: dict) -> str:
        raise NotImplementedError

    def arrival_post_code(self, row: dict) -> str:
        """Code postal de destination. Retourne '' si absent."""
        return ""

    def buyer_vat(self, row: dict) -> str:
        raise NotImplementedError

    def amount_ht(self, row: dict) -> Decimal:
        raise NotImplementedError

    def currency(self, row: dict) -> str:
        raise NotImplementedError

    def tx_date(self, row: dict) -> str:
        raise NotImplementedError

    def order_date(self, row: dict) -> str:
        """Date de commande brute, si distincte de tx_date(). Vide par défaut
        (formats 1-4 : Amazon ne fournit qu'une seule date d'activité, déjà
        renvoyée par tx_date() — pas d'écart commande/expédition observable)."""
        return ""

    def shipment_date(self, row: dict) -> str:
        """Date d'expédition brute, si distincte de tx_date(). Vide par défaut."""
        return ""

    def qty(self, row: dict) -> int:
        raise NotImplementedError

    def is_deemed_supplier(self, row: dict) -> bool:
        raise NotImplementedError

    def amazon_vat(self, row: dict) -> Decimal:
        """TVA collectée par Amazon. Retourne 0 si la colonne est absente."""
        return Decimal("0.00")

    def asin(self, row: dict) -> str:
        raise NotImplementedError

    def transaction_event_id(self, row: dict) -> str:
        """Retourne l'identifiant de l'événement de transaction brut."""
        return row.get("transaction_event_id", "").strip()

# ---------------------------------------------------------------------------
# Format 1 — Ancien format
# ---------------------------------------------------------------------------

class _Format1Parser(_RowParser):

    def tx_type(self, row: dict) -> str:
        return row.get("transaction_type", "").strip().lower()

    def sale_id(self, row: dict, line_no: int) -> str:
        eid = row.get("activity_transaction_id", "").strip()
        if not eid:
            eid = row.get("transaction_event_id", "").strip()
        return eid or f"L{line_no}"

    def departure(self, row: dict) -> str:
        return row.get("departure_country", "").strip().upper()

    def arrival(self, row: dict) -> str:
        return row.get("arrival_country", "").strip().upper()

    def arrival_post_code(self, row: dict) -> str:
        return row.get("arrival_post_code", "").strip()

    def buyer_vat(self, row: dict) -> str:
        return row.get("buyer_vat_number", "").strip()

    def amount_ht(self, row: dict) -> Decimal:
        v = row.get("total_activity_value_amt_vat_excl", "").strip()
        if v and v != "-":
            return safe_decimal(v)
        return (
            safe_decimal(row.get("price_of_items_amt_vat_excl", ""))
            + safe_decimal(row.get("total_ship_charge_amt_vat_excl", ""))
            + safe_decimal(row.get("total_gift_wrap_amt_vat_excl", ""))
        )

    def currency(self, row: dict) -> str:
        col = row.get("transaction_currency_code", "").strip().upper()
        if col:
            return col
        return currency_from_marketplace(row.get("marketplace", ""))

    def tx_date(self, row: dict) -> str:
        return parse_date(row.get("tax_calculation_date", ""))

    def qty(self, row: dict) -> int:
        return int(safe_decimal(row.get("qty", "1")) or 1)

    def is_deemed_supplier(self, row: dict) -> bool:
        # Format 1 : pas de colonne directe — le moteur détermine via seller_eu/stock_eu
        return False

    def asin(self, row: dict) -> str:
        return row.get("asin", "").strip()


# ---------------------------------------------------------------------------
# Format 2 — Intermédiaire
# ---------------------------------------------------------------------------

class _Format2Parser(_RowParser):

    def tx_type(self, row: dict) -> str:
        return row.get("transaction_type", "").strip().lower()

    def sale_id(self, row: dict, line_no: int) -> str:
        eid = row.get("transaction_event_id", "").strip()
        return eid or f"L{line_no}"

    def departure(self, row: dict) -> str:
        return row.get("sale_depart_country", "").strip().upper()

    def arrival(self, row: dict) -> str:
        return row.get("sale_arrival_country", "").strip().upper()

    def arrival_post_code(self, row: dict) -> str:
        return row.get("arrival_post_code", "").strip()

    def buyer_vat(self, row: dict) -> str:
        return row.get("buyer_vat_number", "").strip()

    def amount_ht(self, row: dict) -> Decimal:
        return safe_decimal(row.get("transaction_total_vat_excl_amount", ""))

    def currency(self, row: dict) -> str:
        return "EUR"  # Format 2 n'a pas de colonne devise connue

    def tx_date(self, row: dict) -> str:
        return parse_date(row.get("transaction_settlement_date", ""))

    def qty(self, row: dict) -> int:
        return int(safe_decimal(row.get("quantity", "1")) or 1)

    def is_deemed_supplier(self, row: dict) -> bool:
        model = row.get("marketplace_facilitator_tax_collection_model", "").strip().lower()
        return "facilitator" in model or "marketplace" in model

    def asin(self, row: dict) -> str:
        return row.get("asin", "").strip()


# ---------------------------------------------------------------------------
# Format 3 — Nouveau format 2024+
# ---------------------------------------------------------------------------

class _Format3Parser(_RowParser):

    def tx_type(self, row: dict) -> str:
        return row.get("transaction_type", "").strip().lower()

    def sale_id(self, row: dict, line_no: int) -> str:
        eid = row.get("order_id", "").strip()
        return eid or f"L{line_no}"

    def departure(self, row: dict) -> str:
        from .constants import normalize_country_code
        return normalize_country_code(row.get("sale_depart_country", "").strip().upper())

    def arrival(self, row: dict) -> str:
        from .constants import normalize_country_code
        arr = row.get("sale_arrival_country", "").strip().upper()
        if not arr:
            arr = row.get("buyer_country", "").strip().upper()
        return normalize_country_code(arr)

    def arrival_post_code(self, row: dict) -> str:
        return row.get("arrival_post_code", "").strip()

    def buyer_vat(self, row: dict) -> str:
        return row.get("buyer_vat_number", "").strip()

    def amount_ht(self, row: dict) -> Decimal:
        return safe_decimal(row.get("total_activity_value_amt_vat_excl", ""))

    def currency(self, row: dict) -> str:
        return row.get("transaction_currency_code", "EUR").strip().upper() or "EUR"

    def tx_date(self, row: dict) -> str:
        return parse_date(row.get("transaction_complete_date", ""))

    def qty(self, row: dict) -> int:
        return 1  # Format 3 n'a pas de colonne qty explicite

    def is_deemed_supplier(self, row: dict) -> bool:
        model = row.get("tax_collection_model", "").strip().lower()
        return "facilitator" in model or "marketplace" in model

    def amazon_vat(self, row: dict) -> Decimal:
        raw = row.get("total_activity_value_vat_amt", "").strip()
        if not raw:
            return Decimal("0.00")
        return abs(safe_decimal(raw))

    def asin(self, row: dict) -> str:
        return row.get("asin", "").strip() or row.get("product_id", "").strip()


# ---------------------------------------------------------------------------
# Format 4 — CSV 2025+ (hérite de Format 3, 3 surcharges)
# ---------------------------------------------------------------------------

class _Format4Parser(_Format3Parser):
    """Format Amazon 2025+ (CSV).

    Proche du format 3 mais :
    - TAX_COLLECTION_MODEL absent → TAX_COLLECTION_RESPONSIBILITY à la place
    - ACTIVITY_TRANSACTION_ID présent (sale_id plus fiable qu'ORDER_ID)
    - QTY présent (colonne explicite)
    """

    def sale_id(self, row: dict, line_no: int) -> str:
        eid = row.get("activity_transaction_id", "").strip()
        if not eid:
            eid = row.get("transaction_event_id", "").strip()
        return eid or f"L{line_no}"

    def qty(self, row: dict) -> int:
        return int(safe_decimal(row.get("qty", "1")) or 1)

    def is_deemed_supplier(self, row: dict) -> bool:
        return row.get("tax_collection_responsibility", "").strip().upper() == "MARKETPLACE"


# ---------------------------------------------------------------------------
# Format 5 — Rapport fiscal détaillé V5
# ---------------------------------------------------------------------------

class _Format5Parser(_RowParser):
    """Format Amazon V5 — rapport fiscal détaillé.

    Spécificités :
    - Colonnes OUR_PRICE / SHIPPING / GIFTWRAP séparées (HT, TVA, promos).
    - Une ligne par juridiction fiscale (COUNTRY / STATE / CITY…) par transaction.
    - Clé d'agrégation = VAT Invoice Number + ASIN + Transaction Type.
    - Tax Collection Responsibility : "Marketplace" → deemed supplier.
    - Invoice Level Exchange Rate fourni par Amazon (utilisé comme fallback BCE).
    """

    # Noms de colonnes V5 normalisés
    _COL_TX_ID        = "transaction_id"
    _COL_ORDER_ID     = "order_id"
    _COL_TX_TYPE      = "transaction_type"
    _COL_ORDER_DATE   = "order_date"
    _COL_SHIP_DATE    = "shipment_date"
    _COL_ASIN         = "asin"
    _COL_QTY          = "quantity"
    _COL_CURRENCY     = "currency"
    _COL_SHIP_FROM_CC = "ship_from_country"
    _COL_SHIP_TO_CC   = "ship_to_country"
    _COL_ARRIVAL_PC   = "ship_to_postal_code"   # code postal destination V5
    _COL_BUYER_VAT    = "buyer_tax_registration"
    _COL_RESP         = "tax_collection_responsibility"
    _COL_JURIS_LEVEL  = "jurisdiction_level"
    _COL_FX_RATE      = "invoice_level_exchange_rate"
    _COL_FX_DATE      = "invoice_level_exchange_rate_date"
    _COL_FX_CURRENCY  = "invoice_level_currency_code"
    _COL_VAT_INVOICE  = "vat_invoice_number"
    _COL_TAX_SCHEME   = "tax_reporting_scheme"
    # Montants HT par composante
    _COL_PRICE_HT     = "our_price_tax_exclusive_selling_price"
    _COL_SHIP_HT      = "shipping_tax_exclusive_selling_price"
    _COL_GIFT_HT      = "giftwrap_tax_exclusive_selling_price"
    # Promos HT
    _COL_PRICE_PROMO  = "our_price_tax_exclusive_promo_amount"
    _COL_SHIP_PROMO   = "shipping_tax_exclusive_promo_amount"
    _COL_GIFT_PROMO   = "giftwrap_tax_exclusive_promo_amount"
    # TVA par composante
    _COL_PRICE_VAT    = "our_price_tax_amount"
    _COL_SHIP_VAT     = "shipping_tax_amount"
    _COL_GIFT_VAT     = "giftwrap_tax_amount"

    _EU_JURISDICTION_LEVEL = "country"

    # Injecté par aggregate.py avant le traitement pour les sale_id multi-ASIN
    _multi_asin_orders: frozenset = frozenset()

    def tx_type(self, row: dict) -> str:
        return row.get(self._COL_TX_TYPE, "").strip().lower()

    def sale_id(self, row: dict, line_no: int) -> str:
        """Order ID lisible + suffixe (ASIN) si commande multi-articles."""
        order_id = row.get(self._COL_ORDER_ID, "").strip()
        asin     = row.get(self._COL_ASIN, "").strip()
        tx_type  = row.get("transaction_type", "").strip().upper()
        if order_id:
            if (order_id, tx_type) in self._multi_asin_orders and asin:
                return f"{order_id} ({asin})"
            return order_id
        return f"L{line_no}"

    def departure(self, row: dict) -> str:
        return row.get(self._COL_SHIP_FROM_CC, "").strip().upper()

    def arrival(self, row: dict) -> str:
        return row.get(self._COL_SHIP_TO_CC, "").strip().upper()

    def arrival_post_code(self, row: dict) -> str:
        return row.get(self._COL_ARRIVAL_PC, "").strip()

    def buyer_vat(self, row: dict) -> str:
        """Retourne le numéro TVA uniquement si c'est un vrai numéro intracommunautaire.

        Le format V5 remplit buyer_tax_registration avec des numéros fiscaux nationaux
        (codice fiscale IT, NIF ES…) — on filtre via buyer_tax_registration_type.
        """
        from .constants import is_valid_vat_intracom
        vat      = row.get(self._COL_BUYER_VAT, "").strip()
        reg_type = row.get("buyer_tax_registration_type", "VAT").strip()
        if not is_valid_vat_intracom(vat, reg_type):
            return ""
        return vat

    def tx_date(self, row: dict) -> str:
        """Date d'exigibilité de la TVA (fait générateur).

        Le fait générateur de la TVA sur une livraison de biens est en
        principe la date de LIVRAISON (art. 65/66 directive 2006/112/CE),
        donc la date d'expédition — pas la date de commande. Amazon V5
        fournit les deux colonnes séparément (ORDER_DATE et SHIPMENT_DATE),
        qui peuvent différer d'une commande à l'autre (commande fin de mois,
        expédition le mois suivant).

        On utilise donc SHIPMENT_DATE en priorité, avec repli sur
        ORDER_DATE si l'expédition n'est pas renseignée.
        """
        d = row.get(self._COL_SHIP_DATE, "").strip()
        if not d:
            d = row.get(self._COL_ORDER_DATE, "").strip()
        return parse_date(d)

    def order_date(self, row: dict) -> str:
        """Date de commande brute (ORDER_DATE), pour le rapport de réconciliation."""
        return parse_date(row.get(self._COL_ORDER_DATE, "").strip())

    def shipment_date(self, row: dict) -> str:
        """Date d'expédition brute (SHIPMENT_DATE), si renseignée."""
        return parse_date(row.get(self._COL_SHIP_DATE, "").strip())

    def qty(self, row: dict) -> int:
        return int(safe_decimal(row.get(self._COL_QTY, "1")) or 1)

    def currency(self, row: dict) -> str:
        return row.get(self._COL_CURRENCY, "EUR").strip().upper() or "EUR"

    def asin(self, row: dict) -> str:
        return row.get(self._COL_ASIN, "").strip()

    def is_deemed_supplier(self, row: dict) -> bool:
        return row.get(self._COL_RESP, "").strip().lower() == "marketplace"

    # --- Montants ---

    def _price_ht(self, row: dict) -> Decimal:
        return safe_decimal(row.get(self._COL_PRICE_HT, "")) \
             - abs(safe_decimal(row.get(self._COL_PRICE_PROMO, "")))

    def _shipping_ht(self, row: dict) -> Decimal:
        return safe_decimal(row.get(self._COL_SHIP_HT, "")) \
             - abs(safe_decimal(row.get(self._COL_SHIP_PROMO, "")))

    def _giftwrap_ht(self, row: dict) -> Decimal:
        return safe_decimal(row.get(self._COL_GIFT_HT, "")) \
             - abs(safe_decimal(row.get(self._COL_GIFT_PROMO, "")))

    def amount_ht(self, row: dict) -> Decimal:
        """Base imposable = OUR_PRICE + SHIPPING + GIFTWRAP (net promos, art. 78 Dir. TVA)."""
        return self._price_ht(row) + self._shipping_ht(row) + self._giftwrap_ht(row)

    def amazon_vat(self, row: dict) -> Decimal:
        """TVA déclarée par Amazon = somme des tax amounts par composante."""
        return (
            abs(safe_decimal(row.get(self._COL_PRICE_VAT, "")))
            + abs(safe_decimal(row.get(self._COL_SHIP_VAT, "")))
            + abs(safe_decimal(row.get(self._COL_GIFT_VAT, "")))
        )

    def amazon_fx_rate(self, row: dict) -> Optional[Decimal]:
        """Taux de change Amazon (Invoice Level). None si absent ou nul."""
        raw = row.get(self._COL_FX_RATE, "").strip()
        if not raw:
            return None
        val = safe_decimal(raw)
        return val if val != Decimal("0") else None

    def is_country_level(self, row: dict) -> bool:
        """True si la ligne correspond au niveau COUNTRY (TVA nationale UE)."""
        return row.get(self._COL_JURIS_LEVEL, "").strip().lower() == self._EU_JURISDICTION_LEVEL


# ---------------------------------------------------------------------------
# Registre des parsers instanciés
# ---------------------------------------------------------------------------

PARSERS: dict[int, _RowParser] = {
    1: _Format1Parser(),
    2: _Format2Parser(),
    3: _Format3Parser(),
    4: _Format4Parser(),
    5: _Format5Parser(),
}