"""Parser pour AliExpress / eBay / Temu (fichiers CSV/TSV marketplace).

Ces plateformes fonctionnent sous le meme regime qu'Amazon : elles sont
"assujettis presumes" (deemed suppliers) et collectent la TVA directement
sur les ventes B2C transfrontalières < 150 EUR (regime IOSS).

Gros volumes de ventes transfrontalières avec beaucoup d'importations
directes depuis l'Asie.

Colonnes reconnues (normalisees, flexible) :
- order_id / transaction_id / order_number : identifiant
- country / buyer_country / ship_to_country / destination : pays acheteur
- amount / price / order_amount / total_amount : montant HT
- currency : devise
- seller_country / ship_from_country / origin : pays d'expedition
- date / order_date / create_time : date
- status / order_status : statut
- platform : nom de la plateforme (optionnel)
"""

from __future__ import annotations

import csv
import logging
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Optional

from ..models import BuyerType, Sale
from ..rates import COUNTRY_CURRENCIES
from . import ParseResult

logger = logging.getLogger(__name__)

_COUNTRY_COLS = [
    "country", "buyer_country", "ship_to_country", "destination",
    "destination_country", "delivery_country", "recipient_country",
    "shipping_country",
]
_AMOUNT_COLS = [
    "amount", "price", "order_amount", "total_amount", "subtotal",
    "item_price", "product_amount", "net_amount", "amount_excl_tax",
    "base_amount",
]
_ID_COLS = [
    "order_id", "transaction_id", "order_number", "id", "order_no",
]
_ORIGIN_COLS = [
    "seller_country", "ship_from_country", "origin", "origin_country",
    "warehouse_country", "departure_country", "from_country",
]
_CURRENCY_COLS = ["currency", "currency_code", "order_currency"]
_STATUS_COLS = ["status", "order_status", "financial_status"]
_DATE_COLS = ["date", "order_date", "create_time", "created_at", "payment_date"]
_PLATFORM_COLS = ["platform", "marketplace", "channel", "source"]

_CANCELLED_STATUSES = {
    "cancelled", "canceled", "refund", "refunded", "dispute",
    "closed", "failed",
}


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
    seller_country: str = "CN",
    encoding: str = "utf-8",
    convert_currencies: bool = False,
) -> ParseResult:
    """Parse un fichier AliExpress / eBay / Temu (CSV/TSV).

    Args:
        path: chemin du fichier.
        seller_country: pays d'etablissement du vendeur (defaut CN pour
            les vendeurs tiers Asie, mettre 'FR' si vendeur UE).
        encoding: encodage du fichier.
        convert_currencies: convertir les devises via BCE.

    Note: Pour ces plateformes marketplace, la plateforme est generalement
    l'assujetti presume (deemed supplier) pour les ventes B2C < 150 EUR.
    Le moteur engine.py gere cette logique automatiquement.
    """
    path = Path(path)
    result = ParseResult(
        sales=[], refunds=[], stock_countries=set(), platform="aliexpress"
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
        origin_col = _find_col(headers, _ORIGIN_COLS)
        currency_col = _find_col(headers, _CURRENCY_COLS)
        status_col = _find_col(headers, _STATUS_COLS)
        date_col = _find_col(headers, _DATE_COLS)
        platform_col = _find_col(headers, _PLATFORM_COLS)

        if not country_col or not amount_col:
            result.warnings.append(
                f"Colonnes requises introuvables. Trouvees: {headers}. "
                f"Besoin d'une colonne pays destination et montant."
            )
            return result

        for line_no, row in enumerate(reader, start=2):
            result.total_rows += 1
            normalized_row = {_normalize(k): v for k, v in row.items() if k}

            # Filtrer les commandes annulees.
            if status_col:
                status = (normalized_row.get(status_col) or "").strip().lower()
                if status in _CANCELLED_STATUSES:
                    result.skipped_rows += 1
                    continue

            buyer_country = normalized_row.get(country_col, "").strip().upper()
            amount_ht = _safe_decimal(normalized_row.get(amount_col, ""))

            if not buyer_country or amount_ht == 0:
                result.skipped_rows += 1
                continue

            sale_id = normalized_row.get(id_col, f"AE{line_no}") if id_col else f"AE{line_no}"

            # Pays d'origine / stock.
            stock_country = seller_country.upper()
            if origin_col:
                origin = normalized_row.get(origin_col, "").strip().upper()
                if origin:
                    stock_country = origin

            currency = "EUR"
            if currency_col:
                currency = normalized_row.get(currency_col, "EUR").strip().upper() or "EUR"

            tx_date = ""
            if date_col:
                tx_date = normalized_row.get(date_col, "").strip()

            # Detecter la plateforme specifique (premiere ligne gagne).
            if platform_col and not hasattr(result, "_platform_set"):
                plat = normalized_row.get(platform_col, "").strip().lower()
                if plat:
                    if "ebay" in plat:
                        result.platform = "ebay"
                    elif "temu" in plat:
                        result.platform = "temu"
                    elif "ali" in plat:
                        result.platform = "aliexpress"
                    result._platform_set = True  # type: ignore[attr-defined]

            # Ces plateformes = toujours B2C (pas de gestion B2B).
            buyer_type = BuyerType.B2C

            # Conversion devise.
            original_amount = amount_ht
            exchange_rate = Decimal("1")
            target_currency = "EUR"  # BUGFIX : la devise de calcul du moteur reste toujours EUR, meme si seller_country (home_country) differe ; voir tva_intracom/parsers/amazon/loader.py
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
                except ValueError:
                    result.warnings.append(
                        f"Ligne {line_no}: conversion {currency}->{target_currency} impossible."
                    )

            sale = Sale(
                sale_id=sale_id.strip(),
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
            result.stock_countries.add(stock_country)
            result.sales.append(sale)

    return result
