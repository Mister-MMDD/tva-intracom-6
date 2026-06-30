"""Parser pour les rapports Mirakl (Fnac, Darty, Leroy Merlin, Decathlon...).

Mirakl est la technologie marketplace utilisee par les grandes enseignes
francaises. Le rapport mensuel "Rapport de transactions operateur" est
un fichier Excel (.xlsx) ou CSV.

Colonnes cles attendues (normalisees) :
- billing_country / buyer_country / country : pays de l'acheteur
- base_amount / amount_excl_tax / subtotal : montant HT
- order_id / transaction_id : identifiant
- currency : devise (defaut EUR)
- customer_type / buyer_type : B2B ou B2C (optionnel, defaut B2C)
- shipping_country / warehouse_country : pays d'expedition (stock)
- vat_number : numero TVA acheteur (optionnel)
- date / order_date / transaction_date : date de la transaction
"""

from __future__ import annotations

import csv
import logging
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Optional

from ..models import BuyerType, Sale
from . import ParseResult

logger = logging.getLogger(__name__)

# Mapping flexible des noms de colonnes Mirakl (normalises).
_COUNTRY_COLS = [
    "billing_country", "buyer_country", "country", "country_code",
    "shipping_country_code", "customer_country",
]
_AMOUNT_COLS = [
    "base_amount", "amount_excl_tax", "subtotal", "amount_ht",
    "total_price_excl_tax", "order_amount_excl_tax",
]
_ID_COLS = [
    "order_id", "transaction_id", "order_line_id", "id",
]
_STOCK_COLS = [
    "shipping_country", "warehouse_country", "ship_from_country",
    "fulfillment_country", "departure_country",
]
_CURRENCY_COLS = ["currency", "currency_code", "transaction_currency"]
_BUYER_TYPE_COLS = ["customer_type", "buyer_type", "client_type"]
_VAT_NUMBER_COLS = ["vat_number", "buyer_vat_number", "tax_number", "tva_number"]
_DATE_COLS = ["date", "order_date", "transaction_date", "created_date"]


def _normalize(header: str) -> str:
    return header.strip().lower().replace(" ", "_").replace("-", "_")


def _find_col(headers: list[str], candidates: list[str]) -> Optional[str]:
    """Trouve la premiere colonne qui matche parmi les candidats."""
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


def _parse_csv(
    path: Path,
    encoding: str,
    seller_country: str,
    convert_currencies: bool,
) -> ParseResult:
    """Parse un fichier CSV Mirakl."""
    result = ParseResult(
        sales=[], refunds=[], stock_countries=set(), platform="mirakl"
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
        stock_col = _find_col(headers, _STOCK_COLS)
        currency_col = _find_col(headers, _CURRENCY_COLS)
        buyer_type_col = _find_col(headers, _BUYER_TYPE_COLS)
        vat_col = _find_col(headers, _VAT_NUMBER_COLS)
        date_col = _find_col(headers, _DATE_COLS)

        if not country_col or not amount_col:
            result.warnings.append(
                f"Colonnes requises introuvables. Trouvees: {headers}. "
                f"Besoin d'une colonne pays ({_COUNTRY_COLS}) "
                f"et montant ({_AMOUNT_COLS})."
            )
            return result

        for line_no, row in enumerate(reader, start=2):
            result.total_rows += 1
            normalized_row = {_normalize(k): v for k, v in row.items() if k}

            buyer_country = normalized_row.get(country_col, "").strip().upper()
            amount_str = normalized_row.get(amount_col, "")
            amount_ht = _safe_decimal(amount_str)

            if not buyer_country or amount_ht == 0:
                result.skipped_rows += 1
                continue

            sale_id = normalized_row.get(id_col, f"M{line_no}") if id_col else f"M{line_no}"
            stock_country = (
                normalized_row.get(stock_col, "").strip().upper()
                if stock_col else seller_country.upper()
            )
            if not stock_country:
                stock_country = seller_country.upper()

            # Devise.
            currency = "EUR"
            if currency_col:
                currency = normalized_row.get(currency_col, "EUR").strip().upper() or "EUR"

            # Type d'acheteur.
            buyer_type = BuyerType.B2C
            if buyer_type_col:
                bt = normalized_row.get(buyer_type_col, "").strip().upper()
                if bt in ("B2B", "PROFESSIONNEL", "PRO", "BUSINESS"):
                    buyer_type = BuyerType.B2B

            # Numero TVA.
            buyer_vat = ""
            if vat_col:
                buyer_vat = normalized_row.get(vat_col, "").strip()
                if buyer_vat:
                    buyer_type = BuyerType.B2B

            # Date transaction.
            tx_date = ""
            if date_col:
                tx_date = normalized_row.get(date_col, "").strip()

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
                stock_country=stock_country,
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
            result.stock_countries.add(stock_country)
            result.sales.append(sale)

    return result


def _parse_xlsx(
    path: Path,
    seller_country: str,
    convert_currencies: bool,
) -> ParseResult:
    """Parse un fichier Excel (.xlsx) Mirakl."""
    import openpyxl

    result = ParseResult(
        sales=[], refunds=[], stock_countries=set(), platform="mirakl"
    )

    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    ws = wb.active
    if ws is None:
        result.warnings.append("Fichier Excel vide ou pas de feuille active.")
        return result

    rows = list(ws.iter_rows(values_only=True))
    if not rows:
        return result

    # Premiere ligne = headers.
    headers = [_normalize(str(h)) if h else "" for h in rows[0]]
    country_col = _find_col(headers, _COUNTRY_COLS)
    amount_col = _find_col(headers, _AMOUNT_COLS)
    id_col = _find_col(headers, _ID_COLS)
    stock_col = _find_col(headers, _STOCK_COLS)
    currency_col = _find_col(headers, _CURRENCY_COLS)
    buyer_type_col = _find_col(headers, _BUYER_TYPE_COLS)
    vat_col = _find_col(headers, _VAT_NUMBER_COLS)
    date_col = _find_col(headers, _DATE_COLS)

    if not country_col or not amount_col:
        result.warnings.append(
            f"Colonnes requises introuvables dans l'Excel. "
            f"Trouvees: {headers}."
        )
        wb.close()
        return result

    col_idx = {h: i for i, h in enumerate(headers)}

    for line_no, row in enumerate(rows[1:], start=2):
        result.total_rows += 1

        def _cell(col_name: Optional[str]) -> str:
            if col_name is None:
                return ""
            idx = col_idx.get(col_name)
            if idx is None or idx >= len(row):
                return ""
            val = row[idx]
            return str(val).strip() if val is not None else ""

        buyer_country = _cell(country_col).upper()
        amount_ht = _safe_decimal(_cell(amount_col))

        if not buyer_country or amount_ht == 0:
            result.skipped_rows += 1
            continue

        sale_id = _cell(id_col) or f"M{line_no}"
        stock_country = _cell(stock_col).upper() or seller_country.upper()
        currency = _cell(currency_col).upper() or "EUR"

        buyer_type = BuyerType.B2C
        bt = _cell(buyer_type_col).upper()
        if bt in ("B2B", "PROFESSIONNEL", "PRO", "BUSINESS"):
            buyer_type = BuyerType.B2B

        buyer_vat = _cell(vat_col)
        if buyer_vat:
            buyer_type = BuyerType.B2B

        tx_date = _cell(date_col)

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
            sale_id=sale_id,
            amount_ht=amount_ht,
            buyer_type=buyer_type,
            stock_country=stock_country,
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
        result.stock_countries.add(stock_country)
        result.sales.append(sale)

    wb.close()
    return result


def _parse_date(date_str: str):
    """Parse une date au format courant (dd/mm/yyyy, yyyy-mm-dd, dd.mm.yyyy)."""
    from datetime import date as date_type

    if not date_str:
        return None
    try:
        if "/" in date_str:
            parts = date_str.split("/")
            if len(parts[0]) == 4:
                return date_type(int(parts[0]), int(parts[1]), int(parts[2]))
            return date_type(int(parts[2]), int(parts[1]), int(parts[0]))
        elif "." in date_str:
            parts = date_str.split(".")
            return date_type(int(parts[2]), int(parts[1]), int(parts[0]))
        elif "-" in date_str:
            parts = date_str.split("-")
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
) -> ParseResult:
    """Parse un fichier Mirakl (Excel .xlsx ou CSV).

    Detecte automatiquement le format en fonction de l'extension.
    """
    path = Path(path)

    if path.suffix.lower() in (".xlsx", ".xls"):
        return _parse_xlsx(path, seller_country, convert_currencies)
    else:
        return _parse_csv(path, encoding, seller_country, convert_currencies)
