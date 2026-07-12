"""Parser pour les fichiers Shopify (orders_export.csv).

Shopify genere un CSV universel tres propre. Le vendeur possede son propre
site et expedie depuis son propre stock. Shopify ne collecte JAMAIS la TVA
a la place du vendeur -> tout est a declarer via OSS ou TVA locale.

Colonnes cles Shopify :
- Name / Order Number : identifiant commande
- Shipping Country : pays de destination (buyer_country)
- Subtotal : montant HT
- Total : montant TTC (pour reference)
- Taxes : montant TVA applique par Shopify
- Currency : devise de la commande
- Shipping Province : region (optionnel)
- Financial Status : paid, refunded, partially_refunded
- Billing Country : pays de facturation (fallback si shipping absent)
- Created at : date de la commande
"""

from __future__ import annotations

import csv
import logging
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Optional

from ..models import BuyerType, Sale
from ..ecb_rates import prefetch_rates
from ..rates import COUNTRY_CURRENCIES
from . import ParseResult

logger = logging.getLogger(__name__)

# Mapping des colonnes Shopify (normalisees).
_SHIPPING_COUNTRY_COLS = [
    "shipping_country", "shipping_country_code", "ship_country",
]
_BILLING_COUNTRY_COLS = [
    "billing_country", "billing_country_code",
]
_AMOUNT_COLS = ["subtotal", "total_price", "net_amount", "amount"]
_ID_COLS = ["name", "order_number", "order_id", "id", "number"]
_CURRENCY_COLS = ["currency", "presentment_currency"]
_STATUS_COLS = ["financial_status", "status", "payment_status"]
_DATE_COLS = ["created_at", "order_date", "date", "processed_at"]
_TAXES_COLS = ["taxes", "tax", "total_tax", "tax_amount"]

# Statuts qui indiquent un remboursement.
_REFUND_STATUSES = {"refunded", "partially_refunded"}


def _normalize(header: str | None) -> str:
    if header is None:
        return ""
    return header.strip().lower().replace(" ", "_").replace("-", "_")


def _find_col(headers: list[str], candidates: list[str]) -> Optional[str]:
    for col in candidates:
        if col in headers:
            return col
    return None


def _safe_decimal(value: str | None) -> Decimal:
    if value is None:
        return Decimal("0")
    cleaned = value.strip().replace(",", "").replace("\xa0", "").replace(" ", "")
    if not cleaned or cleaned == "-":
        return Decimal("0")
    try:
        return Decimal(cleaned)
    except InvalidOperation:
        return Decimal("0")


def _detect_separator(first_line: str) -> str:
    if "\t" in first_line:
        return "\t"
    if ";" in first_line:
        return ";"
    return ","


def _parse_date(date_str: str):
    """Parse une date Shopify (format ISO ou courant)."""
    from datetime import date as date_type

    if not date_str:
        return None
    # Shopify utilise souvent: "2024-03-15 10:30:00 +0100" ou "2024-03-15T10:30:00"
    date_part = date_str.split("T")[0].split(" ")[0]
    try:
        if "-" in date_part:
            parts = date_part.split("-")
            if len(parts[0]) == 4:
                return date_type(int(parts[0]), int(parts[1]), int(parts[2]))
        elif "/" in date_part:
            parts = date_part.split("/")
            if len(parts[0]) == 4:
                return date_type(int(parts[0]), int(parts[1]), int(parts[2]))
            return date_type(int(parts[2]), int(parts[1]), int(parts[0]))
    except (ValueError, IndexError):
        pass
    return None


def parse(
    path: Path | str,
    seller_country: str = "FR",
    encoding: str = "utf-8",
    convert_currencies: bool = False,
    stock_country: str = "",
) -> ParseResult:
    """Parse un fichier Shopify orders_export.csv.

    Args:
        path: chemin du fichier CSV.
        seller_country: pays d'etablissement du vendeur (defaut FR).
        encoding: encodage du fichier.
        convert_currencies: convertir les devises via BCE.
        stock_country: pays de stock (defaut = seller_country car Shopify
            est en vente directe depuis l'entrepot du vendeur).
    """
    path = Path(path)
    if not stock_country:
        stock_country = seller_country.upper()

    result = ParseResult(
        sales=[], refunds=[], stock_countries={stock_country}, platform="shopify"
    )

    with path.open(encoding=encoding, errors="replace", newline="") as handle:
        first_line = handle.readline()
        sep = _detect_separator(first_line)
        handle.seek(0)

        reader = csv.DictReader(handle, delimiter=sep)
        if reader.fieldnames:
            reader.fieldnames = [_normalize(f) for f in reader.fieldnames]

        headers = list(reader.fieldnames or [])
        ship_country_col = _find_col(headers, _SHIPPING_COUNTRY_COLS)
        bill_country_col = _find_col(headers, _BILLING_COUNTRY_COLS)
        amount_col = _find_col(headers, _AMOUNT_COLS)
        id_col = _find_col(headers, _ID_COLS)
        currency_col = _find_col(headers, _CURRENCY_COLS)
        status_col = _find_col(headers, _STATUS_COLS)
        date_col = _find_col(headers, _DATE_COLS)
        taxes_col = _find_col(headers, _TAXES_COLS)

        country_col = ship_country_col or bill_country_col
        if not country_col or not amount_col:
            result.warnings.append(
                f"Colonnes requises introuvables. Trouvees: {headers}. "
                f"Besoin d'une colonne pays (shipping/billing_country) "
                f"et montant (subtotal)."
            )
            return result

        rows = list(reader)
        if convert_currencies:
            from datetime import date as date_type
            to_prefetch = []
            for row in rows:
                c = (row.get(currency_col) or "EUR").strip().upper() if currency_col else "EUR"
                if c != "EUR":
                    d_str = (row.get(date_col) or "").strip() if date_col else ""
                    d_obj = _parse_date(d_str) or date_type.today()
                    to_prefetch.append((c, d_obj))
            if to_prefetch:
                prefetch_rates(to_prefetch)

        for line_no, row in enumerate(rows, start=2):
            result.total_rows += 1
            normalized_row = {_normalize(k): v for k, v in row.items() if k}

            buyer_country = (normalized_row.get(country_col) or "").strip().upper()
            # Fallback sur billing country si shipping absent.
            if not buyer_country and bill_country_col:
                buyer_country = (normalized_row.get(bill_country_col) or "").strip().upper()

            amount_ht = _safe_decimal(normalized_row.get(amount_col, ""))

            if not buyer_country or amount_ht == 0:
                result.skipped_rows += 1
                continue

            sale_id = normalized_row.get(id_col, f"SH{line_no}") if id_col else f"SH{line_no}"

            currency = "EUR"
            if currency_col:
                currency = (normalized_row.get(currency_col) or "EUR").strip().upper() or "EUR"

            tx_date = ""
            if date_col:
                tx_date = (normalized_row.get(date_col) or "").strip()

            # Shopify = vente directe -> toujours B2C (pas de gestion B2B native).
            buyer_type = BuyerType.B2C

            # Detecter les remboursements.
            is_refund = False
            if status_col:
                status = (normalized_row.get(status_col) or "").strip().lower()
                if status in _REFUND_STATUSES:
                    is_refund = True

            # Conversion devise.
            original_amount = amount_ht
            exchange_rate = Decimal("1")
            target_currency = COUNTRY_CURRENCIES.get(seller_country.upper(), "EUR")
            exchange_rate_source = target_currency.lower()

            if convert_currencies and currency != target_currency:
                from ..ecb_rates import convert_to_currency
                from datetime import date as date_type

                tx_date_obj = _parse_date(tx_date)
                if tx_date_obj is None:
                    tx_date_obj = date_type.today()
                try:
                    amount_ht, exchange_rate, exchange_rate_source = convert_to_currency(
                        abs(amount_ht), currency, target_currency, tx_date_obj
                    )
                    if is_refund:
                        amount_ht = -amount_ht
                except ValueError:
                    result.warnings.append(
                        f"Ligne {line_no}: conversion {currency}->{target_currency} impossible."
                    )

            if is_refund:
                amount_ht = -abs(amount_ht)

            sale = Sale(
                sale_id=(sale_id or "").strip(),
                amount_ht=amount_ht,
                buyer_type=buyer_type,
                stock_country=stock_country,
                buyer_country=buyer_country,
                seller_country=seller_country.upper(),
                buyer_vat_valid=False,
                buyer_vat_number="",
                original_currency=currency,
                original_amount=original_amount,
                exchange_rate=exchange_rate,
                exchange_rate_source=exchange_rate_source,
                transaction_date=tx_date,
            )

            if is_refund:
                result.refunds.append(sale)
            else:
                result.sales.append(sale)

    return result
