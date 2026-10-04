"""Générateur de données de ventes multi-années pour tester le seuil OSS.

Produit un fichier CSV au format Amazon VAT Transactions Report (Format 4)
identique à source_vente.csv, couvrant plusieurs années civiles.

Scénarios générés par année pour tester tous les cas de TVA :
  - Ventes B2C intra-UE cross-border (OSS) — réparties pour piloter le cumul
  - Ventes B2C domestiques France
  - Ventes B2B cross-border (reverse charge avec VIES)
  - Ventes B2B avec NIF national ES/IT (autoliquidation art.194)
  - Avoirs/Remboursements (RETURN)
  - Transferts de stock FBA (FC_TRANSFER)
  - Import ≤ 150 EUR avec IOSS propre (IOSS_DIRECT)
  - Import > 150 EUR DDP (IMPORT_SELLER_AS_IMPORTER)
  - Import > 150 EUR standard (IMPORT_STANDARD)
  - Deemed supplier (DEEMED_SUPPLIER)
  - Produits hors champ TVA (OUT_OF_SCOPE)
  - Passage de seuil OSS en cours d'année (vente de franchissement)
  - Reset du cumul au 1er janvier

Usage:
    python generer_donnees_multian_avec_VIES.py [--annees 2022 2023 2024] [--output fichier.csv]
    python generer_donnees_multian_avec_VIES.py  # produit data/ventes_multian_test.csv
"""

from __future__ import annotations

import argparse
import csv
import random
import sys
import uuid
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from typing import List, Optional

# ---------------------------------------------------------------------------
# Constantes
# ---------------------------------------------------------------------------
SEUIL_OSS = Decimal("10000.00")
IOSS_THRESHOLD = Decimal("150.00")

# Pays UE disponibles pour les ventes cross-border B2C
_EU_DEST = ["DE", "IT", "ES", "NL", "BE", "PL", "SE", "AT", "PT", "CZ",
            "HU", "RO", "GR", "DK", "FI", "SK", "HR", "LT", "LV", "BG"]

# Pays hors UE pour les imports
_NON_EU_DEST = ["US", "GB", "CH", "CN", "JP", "CA", "AU"]

# Numéros TVA fictifs B2B valides par pays (format correct mais fictifs)
# Nous générons des numéros uniques pour chaque vente
def _generate_vat_number(country: str, seq: int) -> str:
    """Génère un numéro de TVA fictif unique pour un pays."""
    if country == "DE":
        return f"DE{str(seq % 999999999).zfill(9)}"
    elif country == "IT":
        return f"IT{str(seq % 99999999999).zfill(11)}"
    elif country == "ES":
        return f"ES{chr(65 + (seq % 26))}{str(seq % 99999999).zfill(8)}"
    elif country == "NL":
        return f"NL{str(seq % 999999999).zfill(9)}B01"
    elif country == "PL":
        return f"PL{str(seq % 9999999999).zfill(10)}"
    elif country == "BE":
        return f"BE0{str(seq % 999999999).zfill(9)}"
    elif country == "AT":
        return f"ATU{str(seq % 99999999).zfill(8)}"
    else:
        return f"{country}{str(seq).zfill(9)}"

# Numéros VIES réels (fournis par l'utilisateur depuis le certificat)
# Ces numéros seront utilisés pour simuler des VIES valides réels
_REAL_VIES_NUMBERS = {
    "ES": [
        "ESB08266009", "ESB17259458", "ESB18987685", "ESB21527999", "ESB22182745",
        "ESB29074010", "ESB29186475", "ESB30708556", "ESB40536377", "ESB40621179",
        "ESB41580309", "ESB45821675", "ESB54223391", "ESB54294160", "ESB56462856",
        "ESB57226102", "ESB57601692", "ESB58369679", "ESB60204880", "ESB60779337",
        "ESB61284535", "ESB64536212", "ESB64587876", "ESB66124843", "ESB66274887",
        "ESB67795088", "ESB72504913", "ESB73927022", "ESB80553373", "ESB80853237",
        "ESB83648931", "ESB85503100", "ESB87960936", "ESB90140609", "ESB91352955",
        "ESB97882377", "ESB99222598",
    ],
    "FR": [
        "FR06920678505", "FR08511672412", "FR09950620369", "FR10951316215", "FR12422146910",
        "FR12523272805", "FR16484718572", "FR16494705692", "FR16829410885", "FR17434224705",
        "FR19884148180", "FR21819378209", "FR24825078900", "FR25829491398", "FR26407749043",
        "FR28935006932", "FR30850309378", "FR30907756464", "FR32838991354", "FR32898740832",
        "FR33840144943", "FR33994327898", "FR34102213337", "FR34935085019", "FR35403882723",
        "FR35432009231", "FR35843115601", "FR35994530467", "FR38752480160", "FR38901730374",
        "FR39344685144", "FR39987654688", "FR43535123855", "FR43818067811", "FR45817350044",
        "FR47477943294", "FR49793311473", "FR50927761783", "FR52491684445", "FR52924922917",
        "FR53883875783", "FR54432958932", "FR56799492186", "FR58808891907", "FR63933873531",
        "FR64799211503", "FR64809889942", "FR67101275261", "FR68908915853", "FR70529848616",
        "FR72429667728", "FR72494315609", "FR75411517477", "FR76511980054", "FR77492888482",
        "FR77909222861", "FR77912266236", "FR78843272238", "FR78922308614", "FR82492404389",
        "FR83692713043", "FR84789144979", "FR85479685653", "FR85505001198", "FR87917775322",
        "FR89102234275",
    ],
    "IT": [
        "IT02168380398", "IT02432960207", "IT02681150351", "IT02788370167", "IT03164051207",
        "IT03185900267", "IT03428700128", "IT03562540967", "IT03658330042", "IT04329530986",
        "IT04399420985", "IT04630310235", "IT05726780967", "IT06441840821", "IT06907830829",
        "IT08685551007", "IT09704470013", "IT10982090150", "IT12002220015", "IT13146961001",
        "IT13397910962", "IT14672160968", "IT14984171000",
    ],
}

# Numéros VIES invalides (format correct mais invalides pour le test)
_INVALID_VIES_NUMBERS = {
    "DE": ["DE999999999"],
    "IT": ["IT99999999999"],
    "ES": ["ESX99999999"],
    "NL": ["NL999999999B01"],
    "PL": ["PL9999999999"],
    "BE": ["BE0999999999"],
    "AT": ["ATU99999999"],
    "SE": ["SE999999999901"],
    "DK": ["DK99999999"],
    "CZ": ["CZ99999999"],
    "HU": ["HU99999999"],
    "RO": ["RO99999999"],
    "BG": ["BG999999999"],
}

# NIF nationaux ES/IT (sans préfixe, pour tester la détection _is_national_tax_id)
def _generate_nif_number(country: str, seq: int) -> str:
    """Génère un NIF national fictif unique pour un pays."""
    if country == "ES":
        # Format NIF espagnol: 1 lettre + 8 chiffres
        return f"{chr(65 + (seq % 26))}{str(seq % 99999999).zfill(8)}"
    elif country == "IT":
        # Format codice fiscale italien: 11 chiffres
        return f"{str(seq % 99999999999).zfill(11)}"
    else:
        return f"{str(seq).zfill(11)}"

# Numéros IOSS fictifs pour le vendeur
def _generate_ioss_number(seq: int) -> str:
    """Génère un numéro IOSS fictif unique."""
    return f"IM{str(seq % 999999999).zfill(9)}"

# Devises disponibles pour les ventes
_CURRENCIES = ["EUR", "USD", "GBP", "PLN", "CZK", "HUF", "SEK", "DKK", "RON", "BGN", "CAD", "AUD", "JPY", "NOK", "CNY", "INR", "BRL"]

# Taux de change fictifs (1 devise = X EUR)
# NOTE: Ces taux sont uniquement pour la génération de données de test.
# L'application TVA doit utiliser l'API BCE pour les taux de change réels.
_CURRENCY_RATES = {
    "EUR": Decimal("1.00"),
    "USD": Decimal("0.92"),
    "GBP": Decimal("1.15"),
    "PLN": Decimal("0.23"),
    "CZK": Decimal("0.025"),
    "HUF": Decimal("0.0025"),
    "SEK": Decimal("0.095"),
    "DKK": Decimal("0.135"),
    "RON": Decimal("0.20"),
    "BGN": Decimal("0.51"),
    "CAD": Decimal("0.67"),
    "AUD": Decimal("0.62"),
    "JPY": Decimal("0.0062"),
    "NOK": Decimal("0.087"),
    "CNY": Decimal("0.13"),
    "INR": Decimal("0.011"),
    "BRL": Decimal("0.17"),
}

# Devise par pays (logique réaliste)
_COUNTRY_CURRENCY = {
    # Zone Euro
    "FR": "EUR", "DE": "EUR", "IT": "EUR", "ES": "EUR", "NL": "EUR", "BE": "EUR",
    "AT": "EUR", "PT": "EUR", "GR": "EUR", "IE": "EUR", "LU": "EUR", "FI": "EUR",
    "CY": "EUR", "MT": "EUR", "SI": "EUR", "SK": "EUR",
    # UE non-euro
    "PL": "PLN", "CZ": "CZK", "HU": "HUF", "SE": "SEK", "DK": "DKK",
    "RO": "RON", "BG": "BGN", "HR": "EUR", "LT": "EUR", "LV": "EUR",
    # Hors UE - principales devises
    "GB": "GBP", "US": "USD", "CH": "CHF", "CA": "CAD", "AU": "AUD",
    "JP": "JPY", "NO": "NOK", "CN": "CNY", "IN": "INR", "BR": "BRL",
}

# Taux TVA standard simplifiés (copie légère pour le générateur — pas d'import du moteur)
_VAT_RATES = {
    "FR": Decimal("20"), "DE": Decimal("19"), "IT": Decimal("22"),
    "ES": Decimal("21"), "NL": Decimal("21"), "BE": Decimal("21"),
    "PL": Decimal("23"), "SE": Decimal("25"), "AT": Decimal("20"),
    "PT": Decimal("23"), "CZ": Decimal("21"), "HU": Decimal("27"),
    "RO": Decimal("21"), "GR": Decimal("24"), "DK": Decimal("25"),
    "FI": Decimal("25.5"), "SK": Decimal("23"), "HR": Decimal("25"),
    "LT": Decimal("21"), "LV": Decimal("21"), "BG": Decimal("20"),
}

# Colonnes du format Amazon VAT Transactions Report (Format 4)
# Alignées sur source_vente.csv
_COLUMNS = [
    "UNIQUE_ACCOUNT_IDENTIFIER",
    "ACTIVITY_PERIOD",
    "SALES_CHANNEL",
    "MARKETPLACE",
    "PROGRAM_TYPE",
    "TRANSACTION_TYPE",
    "TRANSACTION_EVENT_ID",
    "ACTIVITY_TRANSACTION_ID",
    "TAX_CALCULATION_DATE",
    "TRANSACTION_DEPART_DATE",
    "TRANSACTION_ARRIVAL_DATE",
    "TRANSACTION_COMPLETE_DATE",
    "SELLER_SKU",
    "ASIN",
    "ITEM_DESCRIPTION",
    "ITEM_MANUFACTURE_COUNTRY",
    "QTY",
    "ITEM_WEIGHT",
    "TOTAL_ACTIVITY_WEIGHT",
    "COST_PRICE_OF_ITEMS",
    "PRICE_OF_ITEMS_AMT_VAT_EXCL",
    "PROMO_PRICE_OF_ITEMS_AMT_VAT_EXCL",
    "TOTAL_PRICE_OF_ITEMS_AMT_VAT_EXCL",
    "SHIP_CHARGE_AMT_VAT_EXCL",
    "PROMO_SHIP_CHARGE_AMT_VAT_EXCL",
    "TOTAL_SHIP_CHARGE_AMT_VAT_EXCL",
    "GIFT_WRAP_AMT_VAT_EXCL",
    "PROMO_GIFT_WRAP_AMT_VAT_EXCL",
    "TOTAL_GIFT_WRAP_AMT_VAT_EXCL",
    "TOTAL_ACTIVITY_VALUE_AMT_VAT_EXCL",
    "PRICE_OF_ITEMS_VAT_RATE_PERCENT",
    "PRICE_OF_ITEMS_VAT_AMT",
    "PROMO_PRICE_OF_ITEMS_VAT_AMT",
    "TOTAL_PRICE_OF_ITEMS_VAT_AMT",
    "SHIP_CHARGE_VAT_RATE_PERCENT",
    "SHIP_CHARGE_VAT_AMT",
    "PROMO_SHIP_CHARGE_VAT_AMT",
    "TOTAL_SHIP_CHARGE_VAT_AMT",
    "GIFT_WRAP_VAT_RATE_PERCENT",
    "GIFT_WRAP_VAT_AMT",
    "PROMO_GIFT_WRAP_VAT_AMT",
    "TOTAL_GIFT_WRAP_VAT_AMT",
    "TOTAL_ACTIVITY_VALUE_VAT_AMT",
    "PRICE_OF_ITEMS_AMT_VAT_INCL",
    "PROMO_PRICE_OF_ITEMS_AMT_VAT_INCL",
    "TOTAL_PRICE_OF_ITEMS_AMT_VAT_INCL",
    "SHIP_CHARGE_AMT_VAT_INCL",
    "PROMO_SHIP_CHARGE_AMT_VAT_INCL",
    "TOTAL_SHIP_CHARGE_AMT_VAT_INCL",
    "GIFT_WRAP_AMT_VAT_INCL",
    "PROMO_GIFT_WRAP_AMT_VAT_INCL",
    "TOTAL_GIFT_WRAP_AMT_VAT_INCL",
    "TOTAL_ACTIVITY_VALUE_AMT_VAT_INCL",
    "TRANSACTION_CURRENCY_CODE",
    "COMMODITY_CODE",
    "STATISTICAL_CODE_DEPART",
    "STATISTICAL_CODE_ARRIVAL",
    "COMMODITY_CODE_SUPPLEMENTARY_UNIT",
    "ITEM_QTY_SUPPLEMENTARY_UNIT",
    "TOTAL_ACTIVITY_SUPPLEMENTARY_UNIT",
    "PRODUCT_TAX_CODE",
    "DEPATURE_CITY",
    "DEPARTURE_COUNTRY",
    "DEPARTURE_POST_CODE",
    "ARRIVAL_CITY",
    "ARRIVAL_COUNTRY",
    "ARRIVAL_POST_CODE",
    "SALE_DEPART_COUNTRY",
    "SALE_ARRIVAL_COUNTRY",
    "TRANSPORTATION_MODE",
    "DELIVERY_CONDITIONS",
    "SELLER_DEPART_VAT_NUMBER_COUNTRY",
    "SELLER_DEPART_COUNTRY_VAT_NUMBER",
    "SELLER_ARRIVAL_VAT_NUMBER_COUNTRY",
    "SELLER_ARRIVAL_COUNTRY_VAT_NUMBER",
    "TRANSACTION_SELLER_VAT_NUMBER_COUNTRY",
    "TRANSACTION_SELLER_VAT_NUMBER",
    "BUYER_VAT_NUMBER_COUNTRY",
    "BUYER_VAT_NUMBER",
    "VAT_CALCULATION_IMPUTATION_COUNTRY",
    "TAXABLE_JURISDICTION",
    "TAXABLE_JURISDICTION_LEVEL",
    "VAT_INV_NUMBER",
    "VAT_INV_CONVERTED_AMT",
    "VAT_INV_CURRENCY_CODE",
    "VAT_INV_EXCHANGE_RATE",
    "VAT_INV_EXCHANGE_RATE_DATE",
    "EXPORT_OUTSIDE_EU",
    "INVOICE_URL",
    "BUYER_NAME",
    "ARRIVAL_ADDRESS",
    "SUPPLIER_NAME",
    "SUPPLIER_VAT_NUMBER",
    "TAX_REPORTING_SCHEME",
    "TAX_COLLECTION_RESPONSIBILITY",
]


# ---------------------------------------------------------------------------
# Dataclass scénario
# ---------------------------------------------------------------------------

@dataclass
class ScenarioSpec:
    """Décrit un scénario de vente à générer."""
    label: str
    tx_type: str           # SHIPMENT, RETURN, FC_TRANSFER
    departure: str         # pays de départ (stock)
    arrival: str           # pays de destination (acheteur)
    amount_ht: Decimal
    buyer_vat: str = ""    # "" = B2C, valeur = B2B
    qty: int = 1
    note: str = ""         # pour le CSV commentaire humain (ITEM_DESCRIPTION)
    ioss_number: str = ""   # Numéro IOSS du vendeur (cas IOSS_DIRECT)
    seller_is_importer: bool = False  # DDP - vendeur importateur
    product_tax_code: str = "A_GEN_STANDARD"  # Pour OUT_OF_SCOPE
    currency: str = "EUR"  # Devise de la transaction
    exchange_rate: Decimal = Decimal("1.00")  # Taux de change


# ---------------------------------------------------------------------------
# Générateur de lignes
# ---------------------------------------------------------------------------

def _rnd_date(year: int, month_start: int = 1, month_end: int = 12) -> date:
    """Date aléatoire dans l'intervalle [month_start, month_end] pour l'année donnée."""
    start = date(year, month_start, 1)
    if month_end == 12:
        end = date(year, 12, 31)
    else:
        import calendar
        last_day = calendar.monthrange(year, month_end)[1]
        end = date(year, month_end, last_day)
    delta = (end - start).days
    return start + timedelta(days=random.randint(0, delta))


def _fmt_date(d: date) -> str:
    return d.strftime("%Y-%m-%d")


def _vat_amt(ht: Decimal, country: str) -> Decimal:
    rate = _VAT_RATES.get(country, Decimal("20"))
    return (ht * rate / Decimal("100")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _make_row(
    year: int,
    spec: ScenarioSpec,
    seq: int,
    account_id: str = "FR_SELLER_001",
) -> dict:
    """Construit un dict complet prêt à écrire en CSV."""

    tx_date = _rnd_date(year)
    tx_date_str = _fmt_date(tx_date)
    activity_period = tx_date.strftime("%Y-%m")

    # Gestion de la devise et du taux de change
    currency = spec.currency
    exchange_rate = spec.exchange_rate

    # Cas spécial : FC_TRANSFER (transfert de stock FBA)
    if spec.tx_type == "FC_TRANSFER":
        amount_ht = Decimal("0.00")
        vat_amt = Decimal("0.00")
        amount_ttc = Decimal("0.00")
        vat_rate = Decimal("0")
    else:
        amount_ht = spec.amount_ht
        if spec.tx_type == "RETURN":
            amount_ht = -abs(amount_ht)

        vat_rate = _VAT_RATES.get(spec.arrival, Decimal("20"))
        vat_amt = _vat_amt(abs(amount_ht), spec.arrival)
        if amount_ht < 0:
            vat_amt = -vat_amt
        amount_ttc = amount_ht + vat_amt

    tx_id = f"EVT-{year}-{seq:06d}"
    activity_tx_id = f"ACT-{year}-{seq:06d}"
    sku = f"SKU-{seq % 50:04d}"
    asin = f"B{str(seq % 1000).zfill(9)}"

    # Buyer VAT - pour les NIF nationaux ES/IT, le pays n'est pas dans le numéro
    if spec.buyer_vat and spec.buyer_vat[:2] in ["ES", "IT", "DE", "NL", "PL", "BE", "AT", "SE", "DK", "CZ", "HU", "RO", "BG"]:
        buyer_vat_country = spec.buyer_vat[:2]
    elif spec.buyer_vat:
        # NIF national sans préfixe - utiliser le pays d'arrivée
        buyer_vat_country = spec.arrival
    else:
        buyer_vat_country = ""

    # Détermination du schéma de taxation
    tax_reporting_scheme = "DOMESTIC"
    if spec.tx_type == "FC_TRANSFER":
        tax_reporting_scheme = ""
    elif spec.departure != spec.arrival:
        if spec.arrival in _NON_EU_DEST:
            # Import
            if spec.amount_ht <= IOSS_THRESHOLD and spec.ioss_number:
                tax_reporting_scheme = "IOSS"
            else:
                tax_reporting_scheme = "IMPORT"
        else:
            # Intra-UE
            tax_reporting_scheme = "OSS" if not spec.buyer_vat else "DOMESTIC"

    # Tax collection responsibility
    tax_collection = "SELLER"
    if spec.buyer_vat:
        tax_collection = "BUYER"  # Reverse charge
    elif spec.tx_type == "FC_TRANSFER":
        tax_collection = ""
    elif spec.departure != spec.arrival and spec.arrival in _NON_EU_DEST:
        # Imports
        if spec.amount_ht <= IOSS_THRESHOLD and spec.ioss_number:
            tax_collection = "SELLER"  # IOSS direct
        elif spec.amount_ht <= IOSS_THRESHOLD:
            tax_collection = "AMAZON"  # Deemed supplier
        elif spec.seller_is_importer:
            tax_collection = "SELLER"  # DDP
        else:
            tax_collection = "BUYER"  # Import standard (douane)

    row = {col: "" for col in _COLUMNS}
    row.update({
        "UNIQUE_ACCOUNT_IDENTIFIER":        account_id,
        "ACTIVITY_PERIOD":                  activity_period,
        "SALES_CHANNEL":                    "amazon.fr",
        "MARKETPLACE":                      "amazon.fr",
        "PROGRAM_TYPE":                     "FBA",
        "TRANSACTION_TYPE":                 spec.tx_type,
        "TRANSACTION_EVENT_ID":             tx_id,
        "ACTIVITY_TRANSACTION_ID":          activity_tx_id,
        "TAX_CALCULATION_DATE":             tx_date_str,
        "TRANSACTION_DEPART_DATE":          tx_date_str,
        "TRANSACTION_ARRIVAL_DATE":         tx_date_str,
        "TRANSACTION_COMPLETE_DATE":        tx_date_str,
        "SELLER_SKU":                       sku,
        "ASIN":                             asin,
        "ITEM_DESCRIPTION":                 f"[{spec.label}] {spec.note or 'Article test'}",
        "QTY":                              str(spec.qty),
        "ITEM_WEIGHT":                      "0.5",
        "TOTAL_ACTIVITY_WEIGHT":            str(0.5 * spec.qty),
        # Montants HT
        "PRICE_OF_ITEMS_AMT_VAT_EXCL":     str(amount_ht),
        "TOTAL_PRICE_OF_ITEMS_AMT_VAT_EXCL": str(amount_ht),
        "TOTAL_ACTIVITY_VALUE_AMT_VAT_EXCL": str(amount_ht),
        # TVA
        "PRICE_OF_ITEMS_VAT_RATE_PERCENT":  str(vat_rate),
        "PRICE_OF_ITEMS_VAT_AMT":           str(vat_amt),
        "TOTAL_PRICE_OF_ITEMS_VAT_AMT":     str(vat_amt),
        "TOTAL_ACTIVITY_VALUE_VAT_AMT":     str(vat_amt),
        # Montants TTC
        "PRICE_OF_ITEMS_AMT_VAT_INCL":     str(amount_ttc),
        "TOTAL_PRICE_OF_ITEMS_AMT_VAT_INCL": str(amount_ttc),
        "TOTAL_ACTIVITY_VALUE_AMT_VAT_INCL": str(amount_ttc),
        # Zéros
        "PROMO_PRICE_OF_ITEMS_AMT_VAT_EXCL": "0",
        "SHIP_CHARGE_AMT_VAT_EXCL":         "0",
        "PROMO_SHIP_CHARGE_AMT_VAT_EXCL":   "0",
        "TOTAL_SHIP_CHARGE_AMT_VAT_EXCL":   "0",
        "GIFT_WRAP_AMT_VAT_EXCL":           "0",
        "PROMO_GIFT_WRAP_AMT_VAT_EXCL":     "0",
        "TOTAL_GIFT_WRAP_AMT_VAT_EXCL":     "0",
        "PROMO_PRICE_OF_ITEMS_VAT_AMT":     "0",
        "SHIP_CHARGE_VAT_RATE_PERCENT":     "0",
        "SHIP_CHARGE_VAT_AMT":             "0",
        "PROMO_SHIP_CHARGE_VAT_AMT":        "0",
        "TOTAL_SHIP_CHARGE_VAT_AMT":        "0",
        "GIFT_WRAP_VAT_RATE_PERCENT":       "0",
        "GIFT_WRAP_VAT_AMT":               "0",
        "PROMO_GIFT_WRAP_VAT_AMT":          "0",
        "TOTAL_GIFT_WRAP_VAT_AMT":          "0",
        "PROMO_PRICE_OF_ITEMS_AMT_VAT_INCL": "0",
        "PROMO_SHIP_CHARGE_AMT_VAT_INCL":   "0",
        "TOTAL_SHIP_CHARGE_AMT_VAT_INCL":   "0",
        "GIFT_WRAP_AMT_VAT_INCL":           "0",
        "PROMO_GIFT_WRAP_AMT_VAT_INCL":     "0",
        "TOTAL_GIFT_WRAP_AMT_VAT_INCL":     "0",
        # Devise
        "TRANSACTION_CURRENCY_CODE":        currency,
        "VAT_INV_CURRENCY_CODE":            currency,
        "VAT_INV_EXCHANGE_RATE":            str(exchange_rate),
        "VAT_INV_EXCHANGE_RATE_DATE":       tx_date_str,
        "VAT_INV_CONVERTED_AMT":            str(amount_ttc),
        # Géographie
        "DEPATURE_CITY":                    "Paris",
        "DEPARTURE_COUNTRY":                spec.departure,
        "DEPARTURE_POST_CODE":              "75001",
        "ARRIVAL_CITY":                     "Berlin" if spec.arrival == "DE" else "Destination",
        "ARRIVAL_COUNTRY":                  spec.arrival,
        "ARRIVAL_POST_CODE":                "10115" if spec.arrival == "DE" else "00100",
        "SALE_DEPART_COUNTRY":              spec.departure,
        "SALE_ARRIVAL_COUNTRY":             spec.arrival,
        "TRANSPORTATION_MODE":              "ROAD",
        "DELIVERY_CONDITIONS":              "DAP",
        # TVA vendeur
        "SELLER_DEPART_VAT_NUMBER_COUNTRY": "FR",
        "SELLER_DEPART_COUNTRY_VAT_NUMBER": "FR12345678901",
        "TRANSACTION_SELLER_VAT_NUMBER_COUNTRY": "FR",
        "TRANSACTION_SELLER_VAT_NUMBER":    "FR12345678901",
        # TVA acheteur (B2B si renseigné)
        "BUYER_VAT_NUMBER_COUNTRY":         buyer_vat_country,
        "BUYER_VAT_NUMBER":                 spec.buyer_vat,
        # Divers
        "PRODUCT_TAX_CODE":                 spec.product_tax_code,
        "VAT_CALCULATION_IMPUTATION_COUNTRY": spec.arrival,
        "TAXABLE_JURISDICTION":             spec.arrival,
        "TAXABLE_JURISDICTION_LEVEL":       "COUNTRY",
        "VAT_INV_NUMBER":                   f"INV-{year}-{seq:06d}",
        "EXPORT_OUTSIDE_EU":                "TRUE" if spec.arrival in _NON_EU_DEST else "FALSE",
        "TAX_REPORTING_SCHEME":             tax_reporting_scheme,
        "TAX_COLLECTION_RESPONSIBILITY":    tax_collection,
    })
    return row


# ---------------------------------------------------------------------------
# Construction des scénarios par année
# ---------------------------------------------------------------------------

def _build_scenarios_for_year(
    year: int,
    oss_target: str,   # "below" | "cross" | "above"
    rng: random.Random,
    target_count: int = 30,
    seq_start: int = 0,  # Pour garantir l'unicité des numéros
) -> tuple[List[ScenarioSpec], int]:
    """
    Construit la liste des scénarios pour une année selon l'objectif OSS.

    Renvoie (specs, seq_counter_final) pour garantir l'unicité entre années.

    oss_target :
        "below"  → cumul OSS restera < 10 000 € (test TVA FR sous seuil)
        "cross"  → une vente franchit le seuil (test alerte franchissement)
        "above"  → cumul OSS > 10 000 € dès le début (test OSS normal)
    """
    specs: List[ScenarioSpec] = []
    seq_counter = seq_start  # Compteur global pour l'unicité

    # Distribution approximative des types de transactions
    n_b2b = max(1, int(target_count * 0.04))          # B2B reverse charge
    n_b2b_domestic = max(1, int(target_count * 0.02)) # B2B domestique RC
    n_oss = max(1, int(target_count * 0.25))          # OSS B2C intra-UE
    n_import_ioss = max(1, int(target_count * 0.05))  # Import IOSS ≤ 150€
    n_import_ddp = max(1, int(target_count * 0.05))   # Import DDP > 150€
    n_import_std = max(1, int(target_count * 0.05))   # Import standard > 150€
    n_deemed = max(1, int(target_count * 0.05))       # Deemed supplier
    n_transfer = max(1, int(target_count * 0.05))     # FC_TRANSFER
    n_nif = max(1, int(target_count * 0.03))          # NIF national ES/IT
    n_out_scope = max(1, int(target_count * 0.02))    # OUT_OF_SCOPE
    n_fx = max(1, int(target_count * 0.08))           # Ventes en devises étrangères
    n_misc = max(2, int(target_count * 0.05))         # Avoirs/Exports
    n_dom = target_count - (n_b2b + n_b2b_domestic + n_oss + n_import_ioss + n_import_ddp +
                           n_import_std + n_deemed + n_transfer + n_nif +
                           n_out_scope + n_fx + n_misc)

    # --- 1. Ventes domestiques France (ne comptent pas dans le cumul OSS) ---
    for i in range(n_dom):
        amt = Decimal(str(rng.randint(10, 500)))
        specs.append(ScenarioSpec(
            label="B2C_DOM_FR",
            tx_type="SHIPMENT",
            departure="FR", arrival="FR",
            amount_ht=amt,
            note=f"Vente domestique France #{i+1}",
        ))

    # --- 1b. Ventes en devises étrangères (pour tester la conversion) ---
    for i in range(n_fx):
        # Choisir une destination qui a une devise différente de l'EUR
        fx_countries = ["PL", "CZ", "HU", "SE", "DK", "RO", "BG", "GB", "US", "CH"]
        dest = rng.choice(fx_countries)
        currency = _COUNTRY_CURRENCY.get(dest, "USD")
        exchange_rate = _CURRENCY_RATES.get(currency, Decimal("1.00"))
        
        # Montant dans la devise locale
        amt_local = Decimal(str(rng.randint(10, 500)))
        # Convertir en EUR pour le montant interne
        amt_eur = (amt_local * exchange_rate).quantize(Decimal("0.01"))
        
        specs.append(ScenarioSpec(
            label="B2C_FX",
            tx_type="SHIPMENT",
            departure="FR", arrival=dest,
            amount_ht=amt_eur,  # Toujours en EUR pour le calcul
            currency=currency,
            exchange_rate=exchange_rate,
            note=f"Vente en {currency} vers {dest} (taux {exchange_rate})",
        ))

    # --- 2. Ventes B2B cross-border (reverse charge — ne comptent pas OSS) ---
    # Pays avec VIES réels disponibles (priorité)
    countries_with_real = ["ES", "FR", "IT"]
    # Autres pays UE
    countries_other = ["DE", "NL", "PL", "BE", "AT", "SE", "DK", "CZ", "HU", "RO", "BG"]
    
    for i in range(n_b2b):
        # 50% de chance de choisir un pays avec VIES réels
        if rng.random() < 0.5 and countries_with_real:
            country = rng.choice(countries_with_real)
        else:
            country = rng.choice(countries_other)
        
        # Utiliser des VIES réels pour 30% des cas, invalides pour 20%, sinon générés
        use_real = rng.random() < 0.3
        use_invalid = not use_real and rng.random() < 0.2857  # 20% du total
        
        if use_real and country in _REAL_VIES_NUMBERS:
            vat = rng.choice(_REAL_VIES_NUMBERS[country])
            note = f"B2B reverse charge VIES (VALIDE) vers {country}"
        elif use_invalid and country in _INVALID_VIES_NUMBERS:
            vat = rng.choice(_INVALID_VIES_NUMBERS[country])
            note = f"B2B reverse charge VIES (INVALIDE) vers {country}"
        else:
            vat = _generate_vat_number(country, seq_counter)
            seq_counter += 1
            note = f"B2B reverse charge VIES (GENERE) vers {country}"
        
        amt = Decimal(str(rng.randint(100, 2000)))
        specs.append(ScenarioSpec(
            label="B2B_RC",
            tx_type="SHIPMENT",
            departure="FR", arrival=country,
            amount_ht=amt,
            buyer_vat=vat,
            note=note,
        ))

    # --- 2b. Ventes B2B domestiques (autoliquidation nationale) ---
    # Pays avec reverse charge domestique (Art. 194)
    domestic_rc_countries = ["ES", "IT"]  # ES et IT ont l'art. 194
    for i in range(n_b2b_domestic):
        country = rng.choice(domestic_rc_countries)
        vat = _generate_vat_number(country, seq_counter)
        seq_counter += 1
        amt = Decimal(str(rng.randint(100, 2000)))
        specs.append(ScenarioSpec(
            label="B2B_DOM_RC",
            tx_type="SHIPMENT",
            departure=country, arrival=country,  # Stock et acheteur dans le même pays
            amount_ht=amt,
            buyer_vat=vat,
            note=f"B2B autoliquidation domestique {country} (art.194)",
        ))

    # --- 3. B2B avec NIF national ES/IT (art.194) ---
    for i in range(n_nif):
        country = rng.choice(["ES", "IT"])
        nif = _generate_nif_number(country, seq_counter)
        seq_counter += 1
        amt = Decimal(str(rng.randint(100, 2000)))
        specs.append(ScenarioSpec(
            label="B2B_NIF",
            tx_type="SHIPMENT",
            departure="FR", arrival=country,
            amount_ht=amt,
            buyer_vat=nif,  # NIF sans préfixe
            note=f"B2B NIF national {country} (art.194)",
        ))

    # --- 4. Ventes B2C cross-border intra-UE (OSS) ---
    # Pilotage du cumul selon oss_target
    oss_amounts = []
    if oss_target == "below":
        # Rester sous 10 000 €. On réduit drastiquement le nombre de ventes OSS
        # et on compense avec du domestique pour garder le total de lignes
        n_oss_real = max(1, min(n_oss, 5))  # Max 5 ventes OSS pour below
        # Montants petits pour rester sous 10000
        target_total = Decimal("8000")  # Cible 8000 pour rester sous le seuil
        base_amt = target_total / n_oss_real
        for _ in range(n_oss_real):
            variation = Decimal(str(rng.randint(-50, 50)))
            amt = (base_amt + variation).quantize(Decimal("0.01"))
            oss_amounts.append(max(Decimal("100"), amt))
        # On compense le nombre de lignes en ajoutant du domestique
        for _ in range(n_oss - n_oss_real):
            specs.append(ScenarioSpec(
                label="B2C_DOM_FR", tx_type="SHIPMENT",
                departure="FR", arrival="FR", amount_ht=Decimal(str(rng.randint(10, 100)))
            ))

    elif oss_target == "cross":
        # Ventes juste sous le seuil + UNE vente de franchissement
        # Adapter au nombre de ventes OSS disponibles
        if n_oss >= 2:
            # On veut sum(n_oss-1) = 9000 pour être juste sous le seuil
            base_amt = Decimal("9000") / (n_oss - 1)
            for _ in range(n_oss - 1):
                oss_amounts.append(base_amt.quantize(Decimal("0.01")))
            oss_amounts.append(Decimal("2000"))   # franchissement
        else:
            # Si pas assez de ventes, faire une seule vente qui franchit
            oss_amounts.append(Decimal("11000"))

    else:  # "above"
        # Bien au-dessus du seuil - utiliser des montants importants
        # Pour garantir > 10000 avec peu de ventes
        if n_oss > 0:
            target_total = Decimal("15000")  # Cible 15000 pour être bien au-dessus
            base_amt = target_total / n_oss
            for _ in range(n_oss):
                variation = Decimal(str(rng.randint(-200, 200)))
                amt = (base_amt + variation).quantize(Decimal("0.01"))
                oss_amounts.append(max(Decimal("500"), amt))

    countries_pool = _EU_DEST.copy()
    for i, amt in enumerate(oss_amounts):
        dest = countries_pool[i % len(countries_pool)]
        specs.append(ScenarioSpec(
            label="B2C_OSS",
            tx_type="SHIPMENT",
            departure="FR", arrival=dest,
            amount_ht=max(Decimal("0.01"), amt),
            note=f"OSS B2C vers {dest} (cible={oss_target})",
        ))

    # --- 5. Import IOSS ≤ 150 EUR (numéro IOSS propre du vendeur) ---
    for i in range(n_import_ioss):
        dest = rng.choice(_NON_EU_DEST)
        currency = _COUNTRY_CURRENCY.get(dest, "USD")
        exchange_rate = _CURRENCY_RATES.get(currency, Decimal("1.00"))
        
        # Montant dans la devise locale (<= 150 EUR converti)
        amt_local = Decimal(str(rng.randint(10, 149)))
        amt_eur = (amt_local * exchange_rate).quantize(Decimal("0.01"))
        
        ioss_num = _generate_ioss_number(seq_counter)
        seq_counter += 1
        specs.append(ScenarioSpec(
            label="IMPORT_IOSS",
            tx_type="SHIPMENT",
            departure="FR", arrival=dest,
            amount_ht=amt_eur,
            currency=currency,
            exchange_rate=exchange_rate,
            ioss_number=ioss_num,
            note=f"Import IOSS <=150 EUR vers {dest} en {currency} (n° {ioss_num})",
        ))

    # --- 6. Import DDP > 150 EUR (vendeur importateur) ---
    for i in range(n_import_ddp):
        dest = rng.choice(_NON_EU_DEST)
        currency = _COUNTRY_CURRENCY.get(dest, "USD")
        exchange_rate = _CURRENCY_RATES.get(currency, Decimal("1.00"))
        
        amt_local = Decimal(str(rng.randint(151, 1000)))
        amt_eur = (amt_local * exchange_rate).quantize(Decimal("0.01"))
        
        specs.append(ScenarioSpec(
            label="IMPORT_DDP",
            tx_type="SHIPMENT",
            departure="FR", arrival=dest,
            amount_ht=amt_eur,
            currency=currency,
            exchange_rate=exchange_rate,
            seller_is_importer=True,
            note=f"Import DDP >150 EUR vers {dest} en {currency} (vendeur importateur)",
        ))

    # --- 7. Import standard > 150 EUR (douane) ---
    for i in range(n_import_std):
        dest = rng.choice(_NON_EU_DEST)
        currency = _COUNTRY_CURRENCY.get(dest, "USD")
        exchange_rate = _CURRENCY_RATES.get(currency, Decimal("1.00"))
        
        amt_local = Decimal(str(rng.randint(151, 1000)))
        amt_eur = (amt_local * exchange_rate).quantize(Decimal("0.01"))
        
        specs.append(ScenarioSpec(
            label="IMPORT_STD",
            tx_type="SHIPMENT",
            departure="FR", arrival=dest,
            amount_ht=amt_eur,
            currency=currency,
            exchange_rate=exchange_rate,
            note=f"Import standard >150 EUR vers {dest} (douane)",
        ))

    # --- 8. Deemed supplier (Amazon assujetti présumé) ---
    for i in range(n_deemed):
        dest = rng.choice(_EU_DEST)
        amt = Decimal(str(rng.randint(10, 149)))  # <= 150 pour deemed supplier
        specs.append(ScenarioSpec(
            label="DEEMED_SUPPLIER",
            tx_type="SHIPMENT",
            departure="US", arrival=dest,  # Vendeur hors UE
            amount_ht=amt,
            note=f"Deemed supplier Amazon vers {dest}",
        ))

    # --- 9. FC_TRANSFER (transferts de stock FBA) ---
    for i in range(n_transfer):
        dep = rng.choice(_EU_DEST)
        arr = rng.choice([c for c in _EU_DEST if c != dep])
        qty = rng.randint(10, 100)
        specs.append(ScenarioSpec(
            label="FC_TRANSFER",
            tx_type="FC_TRANSFER",
            departure=dep, arrival=arr,
            amount_ht=Decimal("0.00"),
            qty=qty,
            note=f"Transfert stock FBA {dep}-{arr}",
        ))

    # --- 10. OUT_OF_SCOPE (produits hors champ TVA) ---
    for i in range(n_out_scope):
        amt = Decimal(str(rng.randint(10, 500)))
        specs.append(ScenarioSpec(
            label="OUT_OF_SCOPE",
            tx_type="SHIPMENT",
            departure="FR", arrival=rng.choice(_EU_DEST),
            amount_ht=amt,
            product_tax_code="A_GEN_NOTAX",
            note=f"Produit hors champ TVA #{i+1}",
        ))

    # --- 11. Avoirs/Remboursements & Exports ---
    for i in range(n_misc // 2):
        specs.append(ScenarioSpec(
            label="AVOIR_OSS", tx_type="RETURN",
            departure="FR", arrival=rng.choice(_EU_DEST),
            amount_ht=Decimal(str(rng.randint(10, 100))),
            note="Remboursement OSS",
        ))
    for i in range(n_misc - (n_misc // 2)):
        specs.append(ScenarioSpec(
            label="EXPORT_HUE", tx_type="SHIPMENT",
            departure="FR", arrival=rng.choice(_NON_EU_DEST),
            amount_ht=Decimal(str(rng.randint(50, 300))),
            note="Export hors UE",
        ))

    return specs, seq_counter



# ---------------------------------------------------------------------------
# Point d'entrée principal
# ---------------------------------------------------------------------------

def generate(
    years: List[int],
    output_path: Path,
    seed: int = 42,
    total_count: int = 15000,
) -> None:
    """Génère le fichier CSV multi-années."""
    rng = random.Random(seed)

    # Stratégie OSS par année pour couvrir tous les cas
    oss_strategies = ["below", "cross", "above"]

    all_rows: List[dict] = []
    seq = 1
    seq_counter = 1  # Pour l'unicité des numéros TVA/NIF/IOSS

    rows_per_year = total_count // len(years)

    print(f"Generation de {total_count} ventes pour {len(years)} annee(s) : {years}")
    print(f"Cible par annee : ~{rows_per_year} lignes")
    print(f"Seuil OSS : {SEUIL_OSS} EUR\n")

    for i, year in enumerate(years):
        strategy = oss_strategies[i % len(oss_strategies)]
        # On ajuste target_count pour la dernière année pour tomber juste sur total_count
        target_count = rows_per_year if i < len(years) - 1 else (total_count - len(all_rows))

        specs, seq_counter = _build_scenarios_for_year(
            year, strategy, rng, target_count=target_count, seq_start=seq_counter
        )

        # Trier les specs dans un ordre aléatoire pour simuler l'ordre réel
        rng.shuffle(specs)

        year_oss_total = Decimal("0")
        year_rows = []

        for spec in specs:
            row = _make_row(year, spec, seq, account_id="FR_SELLER_TEST_001")
            year_rows.append(row)
            seq += 1

            # Compte dans le cumul OSS uniquement si :
            # - SHIPMENT (pas RETURN, pas FC_TRANSFER)
            # - B2C (pas de buyer_vat)
            # - Intra-UE (arrival dans _EU_DEST et pas dans _NON_EU_DEST)
            # - Stock UE (departure dans _EU_DEST)
            # - Pas OUT_OF_SCOPE
            # - En EUR (les ventes en devises étrangères ne comptent pas pour le seuil OSS)
            if (spec.tx_type == "SHIPMENT" and
                not spec.buyer_vat and
                spec.arrival in _EU_DEST and
                spec.departure in _EU_DEST and
                spec.product_tax_code != "A_GEN_NOTAX" and
                spec.currency == "EUR"):
                # Compte dans le cumul OSS si B2C cross-border intra-UE
                year_oss_total += spec.amount_ht

        # Trier par date de transaction pour la chronologie
        year_rows.sort(key=lambda r: r["TRANSACTION_COMPLETE_DATE"])
        all_rows.extend(year_rows)

        oss_status = (
            "X SOUS le seuil" if year_oss_total < SEUIL_OSS
            else f"! FRANCHISSEMENT" if strategy == "cross"
            else "^ AU-DESSUS du seuil"
        )
        print(f"  {year} ({strategy:6s}) : {len(specs):3d} transactions, "
              f"cumul OSS estime ~ {year_oss_total:>10,.2f} EUR  {oss_status}")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=_COLUMNS)
        writer.writeheader()
        writer.writerows(all_rows)

    print(f"\n[Fait] Fichier genere : {output_path}")
    print(f"   {len(all_rows)} lignes, {len(years)} annees ({years[0]}-{years[-1]})")
    print()
    print("Types de scenarios generes :")
    print("  [X] B2C domestiques France")
    print("  [X] B2B reverse charge (VIES)")
    print("  [X] B2B NIF national ES/IT (art.194)")
    print("  [X] B2C OSS intra-UE cross-border")
    print("  [X] Import IOSS <= 150 EUR (numero propre)")
    print("  [X] Import DDP > 150 EUR (vendeur importateur)")
    print("  [X] Import standard > 150 EUR (douane)")
    print("  [X] Deemed supplier (Amazon)")
    print("  [X] FC_TRANSFER (transferts stock FBA)")
    print("  [X] OUT_OF_SCOPE (produits hors champ)")
    print("  [X] Ventes en devises etrangeres (PLN, CZK, HUF, SEK, DKK, RON, BGN, GBP, USD, CHF)")
    print("  [X] Avoirs/Remboursements (RETURN)")
    print("  [X] Exports hors UE")
    print()
    print("Strategies OSS par annee :")
    print("  Annee 1 (below)  -> cumul OSS < 10 000 EUR  -> test TVA FR sous seuil")
    print("  Annee 2 (cross)  -> une vente franchit le seuil -> test alerte franchissement")
    print("  Annee 3 (above)  -> cumul OSS >> 10 000 EUR -> test declaration OSS normale")
    print("  (cycle si > 3 ans)")


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Générateur de données de ventes multi-années pour tester le seuil OSS."
    )
    parser.add_argument(
        "--annees",
        nargs="+",
        type=int,
        default=[2022, 2023, 2024],
        metavar="ANNEE",
        help="Années à générer (défaut : 2022 2023 2024).",
    )
    parser.add_argument(
        "--output",
        default="data/ventes_multian_test_new2.csv",
        help="Chemin du fichier CSV de sortie (défaut : data/ventes_multian_test.csv).",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=42,
        help="Graine aléatoire pour la reproductibilité (défaut : 42).",
    )
    parser.add_argument(
        "--count",
        type=int,
        default=10000,
        help="Nombre total de lignes à générer (défaut : 100000).",
    )
    args = parser.parse_args(argv)

    years = sorted(set(args.annees))
    if not years:
        print("Erreur : aucune année spécifiée.", file=sys.stderr)
        return 1

    generate(
        years=years,
        output_path=Path(args.output),
        seed=args.seed,
        total_count=args.count,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
