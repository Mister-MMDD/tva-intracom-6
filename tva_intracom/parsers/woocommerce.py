"""Parser generique pour WooCommerce / PrestaShop (export CSV comptable).

Ces CMS e-commerce ne collectent JAMAIS la TVA a la place du vendeur.
Le vendeur doit tout declarer via l'OSS ou sa TVA locale.

La structure varie selon les plugins d'export. Ce parser adopte un mapping
flexible pour s'adapter aux colonnes les plus courantes des extensions
comptables (WooCommerce PDF & Packing Slip, WP All Export, PrestaShop
export CSV natif, Module comptabilite PrestaShop...).

Colonnes reconnues (normalisees, flexible) :
- order_id / id / reference : identifiant commande
- country / shipping_country / delivery_country / pays : pays destination
- total_ht / subtotal / amount_excl_tax / total_products_wt : montant HT
- currency / devise : devise
- status / order_status : statut (pour filtrer les annulees)
- date / order_date / date_add : date
- customer_type / is_company / company : detection B2B
- vat_number / siret / tax_id : numero TVA client
"""

from __future__ import annotations

import csv
import logging
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Optional

from ..models import BuyerType, Sale
from ..ecb_rates import prefetch_rates
from . import ParseResult

logger = logging.getLogger(__name__)

_COUNTRY_COLS = [
    "country", "shipping_country", "delivery_country", "pays",
    "billing_country", "country_code", "pays_livraison",
    "customer_country", "ship_to_country",
]
_AMOUNT_COLS = [
    "total_ht", "subtotal", "amount_excl_tax", "total_products",
    "order_total_excl_tax", "net_amount", "total_paid_tax_excl",
    "amount_ht", "montant_ht",
]
_ID_COLS = [
    "order_id", "id", "reference", "order_number", "id_order",
    "numero_commande",
]
_CURRENCY_COLS = ["currency", "devise", "currency_code"]
_STATUS_COLS = ["status", "order_status", "current_state", "etat"]
_DATE_COLS = ["date", "order_date", "date_add", "created_at", "date_commande"]
_COMPANY_COLS = [
    "company", "is_company", "customer_type", "type_client", "societe",
]
_VAT_COLS = [
    "vat_number", "tax_id", "siret", "tva_number", "buyer_vat_number",
    "numero_tva",
]
_STOCK_COLS = [
    "warehouse", "stock_country", "ship_from", "entrepot",
    "shipping_from_country",
]

# Statuts a ignorer (commandes annulees).
_CANCELLED_STATUSES = {
    "cancelled", "canceled", "annule", "annulee", "refunded",
    "failed", "trash",
}


def _normalize(header: str) -> str:
    return header.strip().lower().replace(" ", "_").replace("-", "_")


def _find_col(headers: list[str], candidates: list[str]) -> Optional[str]:
    for col in candidates:
        if col in headers:
            return col
    return None


def _safe_decimal(value: str) -> Decimal:
    cleaned = value.strip().replace(",", ".").replace("\xa0", "").replace(" ", "")
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
    from datetime import date as date_type

    if not date_str:
        return None
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
        elif "." in date_part:
            parts = date_part.split(".")
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
    """Parse un fichier WooCommerce / PrestaShop (CSV generique).

    Args:
        path: chemin du fichier CSV.
        seller_country: pays d'etablissement du vendeur.
        encoding: encodage du fichier.
        convert_currencies: convertir les devises via BCE.
        stock_country: pays de stock (defaut = seller_country).
    """
    path = Path(path)
    if not stock_country:
        stock_country = seller_country.upper()

    result = ParseResult(
        sales=[], refunds=[], stock_countries={stock_country}, platform="woocommerce"
    )

    with path.open(encoding=encoding, errors="replace", newline="") as handle:
        first_line = handle.readline()
        sep = _detect_separator(first_line)
        handle.seek(0)

        reader = csv.DictReader(handle, delimiter=sep)
        if reader.fieldnames:
            reader.fieldnames = [_normalize(f) for f in reader.fieldnames]

        headers = list(reader.fieldnames or [])
        country_col = _find_col(headers, _COUNTRY_COLS)
        amount_col = _find_col(headers, _AMOUNT_COLS)
        id_col = _find_col(headers, _ID_COLS)
        currency_col = _find_col(headers, _CURRENCY_COLS)
        status_col = _find_col(headers, _STATUS_COLS)
        date_col = _find_col(headers, _DATE_COLS)
        company_col = _find_col(headers, _COMPANY_COLS)
        vat_col = _find_col(headers, _VAT_COLS)
        stock_col = _find_col(headers, _STOCK_COLS)

        if not country_col or not amount_col:
            result.warnings.append(
                f"Colonnes requises introuvables. Trouvees: {headers}. "
                f"Besoin d'une colonne pays ({_COUNTRY_COLS[:5]}...) "
                f"et montant ({_AMOUNT_COLS[:5]}...)."
            )
            return result

        rows = list(reader)
        if convert_currencies:
            from datetime import date as date_type
            to_prefetch = []
            for row in rows:
                c = row.get(currency_col, "EUR").strip().upper() if currency_col else "EUR"
                if c != "EUR":
                    d_str = row.get(date_col, "").strip() if date_col else ""
                    d_obj = _parse_date(d_str) or date_type.today()
                    to_prefetch.append((c, d_obj))
            if to_prefetch:
                prefetch_rates(to_prefetch)

        for line_no, row in enumerate(rows, start=2):
            result.total_rows += 1
            normalized_row = {_normalize(k): v for k, v in row.items() if k}

            # Filtrer les commandes annulees.
            if status_col:
                status = normalized_row.get(status_col, "").strip().lower()
                if status in _CANCELLED_STATUSES:
                    result.skipped_rows += 1
                    continue

            buyer_country = normalized_row.get(country_col, "").strip().upper()
            amount_ht = _safe_decimal(normalized_row.get(amount_col, ""))

            if not buyer_country or amount_ht == 0:
                result.skipped_rows += 1
                continue

            sale_id = normalized_row.get(id_col, f"WC{line_no}") if id_col else f"WC{line_no}"

            currency = "EUR"
            if currency_col:
                currency = normalized_row.get(currency_col, "EUR").strip().upper() or "EUR"

            tx_date = ""
            if date_col:
                tx_date = normalized_row.get(date_col, "").strip()

            # Detection B2B.
            buyer_type = BuyerType.B2C
            buyer_vat = ""
            if vat_col:
                buyer_vat = normalized_row.get(vat_col, "").strip()
                if buyer_vat:
                    buyer_type = BuyerType.B2B
            if buyer_type == BuyerType.B2C and company_col:
                company = normalized_row.get(company_col, "").strip()
                if company and company.lower() not in ("", "n/a", "-", "non"):
                    # Presence d'un nom de societe -> potentiellement B2B,
                    # mais sans numero TVA on ne peut pas autoliquider.
                    pass

            # Stock country.
            row_stock = stock_country
            if stock_col:
                s = normalized_row.get(stock_col, "").strip().upper()
                if s:
                    row_stock = s

            # Conversion devise.
            original_amount = amount_ht
            exchange_rate = Decimal("1")
            exchange_rate_source = "eur"

            if convert_currencies and currency != "EUR":
                from ..ecb_rates import convert_to_eur
                from datetime import date as date_type

                tx_date_obj = _parse_date(tx_date)
                if tx_date_obj is None:
                    tx_date_obj = date_type.today()
                try:
                    amount_ht, exchange_rate, exchange_rate_source = convert_to_eur(
                        abs(amount_ht), currency, tx_date_obj
                    )
                except ValueError:
                    result.warnings.append(
                        f"Ligne {line_no}: conversion {currency}->EUR impossible."
                    )

            sale = Sale(
                sale_id=sale_id.strip(),
                amount_ht=amount_ht,
                buyer_type=buyer_type,
                stock_country=row_stock,
                buyer_country=buyer_country,
                seller_country=seller_country.upper(),
                buyer_vat_valid=bool(buyer_vat),
                buyer_vat_number=buyer_vat,
                original_currency=currency,
                original_amount=original_amount,
                exchange_rate=exchange_rate,
                exchange_rate_source=exchange_rate_source,
                transaction_date=tx_date,
            )
            result.stock_countries.add(row_stock)
            result.sales.append(sale)

    return result
