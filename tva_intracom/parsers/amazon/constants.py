"""Constantes et utilitaires purs partagés entre les sous-modules Amazon.

Aucune dépendance vers les autres sous-modules amazon/ — peut être importé
partout sans risque de cycle.
"""

from __future__ import annotations

import logging
import re
from decimal import Decimal, InvalidOperation
from typing import Callable

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Types de transactions
# ---------------------------------------------------------------------------
SALE_TYPES     : frozenset[str] = frozenset({"sale", "shipment"})
REFUND_TYPES   : frozenset[str] = frozenset({"refund", "return", "adjustment"})
TRANSFER_TYPES : frozenset[str] = frozenset({"fc_transfer", "fc transfer"})
INBOUND_TYPES  : frozenset[str] = frozenset({"inbound"})
# Écritures de facturation pure Amazon (régularisations de facture, avoirs
# administratifs) — distinctes des SALE_TYPES/REFUND_TYPES : comptées à
# part pour visibilité (voir loader.py), jamais assimilées à une vente ou
# un remboursement.
INVOICE_TYPES     : frozenset[str] = frozenset({"invoice"})
CREDIT_NOTE_TYPES : frozenset[str] = frozenset({"credit_note"})

# ---------------------------------------------------------------------------
# Colonnes réellement consommées (row.get(...)) par les parseurs formats 1-5,
# loader.py (_process_rows, INVOICE/CREDIT_NOTE, FC transfers, garde CSV
# mono-colonne exclue) et aggregate.py (preaggregate_v5).
#
# Optimisation RAM (voir loader._read_and_prepare_rows) : un rapport Amazon
# "brut" peut contenir jusqu'à ~95 colonnes, mais seule une fraction est
# réellement lue. Sur un CSV de 100k lignes, matérialiser 95 colonnes/ligne
# en dict Python (via `df.to_dicts()`) plutôt que ce sous-ensemble mesure un
# pic RAM ~4x plus élevé (profilé : 823 Mo vs 255 Mo pour le pipeline complet
# sur un rapport synthétique de 60 Mo / 95 colonnes / 100k lignes).
#
# IMPORTANT : si un nouveau champ est lu quelque part via row.get("xxx") ou
# row.get(self._COL_XXX) dans parsers.py / loader.py / aggregate.py, il DOIT
# être ajouté ici, sinon il sera silencieusement absent de raw_rows (get()
# renverra toujours None/"" au lieu de lever une erreur). Cette liste a été
# construite par grep exhaustif sur les deux fichiers + relecture manuelle
# des constantes _COL_* de _Format5Parser — à revalider si ces fichiers
# changent.
# ---------------------------------------------------------------------------
NEEDED_COLUMNS: frozenset[str] = frozenset({
    # Identifiants / meta
    "unique_account_identifier", "marketplace", "program_type",
    "transaction_type", "transaction_event_id", "activity_transaction_id",
    "order_id", "transaction_id", "asin", "product_id",
    "vat_inv_number", "vat_invoice_number",
    # Dates
    "tax_calculation_date", "transaction_complete_date",
    "transaction_settlement_date", "order_date", "shipment_date",
    # Pays / territoire
    "departure_country", "arrival_country", "sale_depart_country",
    "sale_arrival_country", "ship_from_country", "ship_to_country",
    "arrival_post_code", "ship_to_postal_code", "delivery_postal_code",
    "ship_to_zip",
    # TVA acheteur / classification
    "buyer_vat_number", "buyer_country", "buyer_tax_registration",
    "buyer_tax_registration_type",
    # Quantité
    "qty", "quantity",
    # Devise / change
    "transaction_currency_code", "currency", "exchange_rate",
    "invoice_level_exchange_rate", "invoice_level_exchange_rate_date",
    "invoice_level_currency_code",
    # Montants HT (formats 1-4)
    "total_activity_value_amt_vat_excl", "price_of_items_amt_vat_excl",
    "total_ship_charge_amt_vat_excl", "total_gift_wrap_amt_vat_excl",
    "transaction_total_vat_excl_amount",
    # Montants HT (format 5 — composantes séparées, sommées par aggregate.py)
    "our_price_tax_exclusive_selling_price",
    "shipping_tax_exclusive_selling_price",
    "giftwrap_tax_exclusive_selling_price",
    "our_price_tax_exclusive_promo_amount",
    "shipping_tax_exclusive_promo_amount",
    "giftwrap_tax_exclusive_promo_amount",
    "our_price_tax_amount", "shipping_tax_amount", "giftwrap_tax_amount",
    # TVA Amazon (deemed supplier)
    "total_activity_value_vat_amt",
    "tax_collection_model", "tax_collection_responsibility",
    "marketplace_facilitator_tax_collection_model",
    "tax_reporting_scheme", "jurisdiction_level",
    # INVOICE / CREDIT_NOTE (écritures de facturation pure)
})

# ---------------------------------------------------------------------------
# TVA — placeholders et préfixes UE
# ---------------------------------------------------------------------------

# Pattern des numéros TVA fictifs qu'Amazon insère quand l'acheteur B2B
# n'a pas fourni de vrai numéro (ex: FRINV88941X, ITINV47760X).
_AMAZON_VAT_PLACEHOLDER = re.compile(r'^[A-Z]{2}INV\d+X?$', re.IGNORECASE)

# Préfixes pays UE valides pour les numéros TVA intracommunautaires.
# XI = Irlande du Nord (post-Brexit, toujours dans l'espace TVA UE pour les biens).
EU_VAT_PREFIXES: frozenset[str] = frozenset({
    "AT", "BE", "BG", "HR", "CY", "CZ", "DK", "EE", "FI", "FR", "DE", "GR",
    "HU", "IE", "IT", "LV", "LT", "LU", "MT", "NL", "PL", "PT", "RO", "SK",
    "SI", "ES", "SE", "XI",
})

# ---------------------------------------------------------------------------
# Normalisation des codes pays
# ---------------------------------------------------------------------------

# EL → GR : la Grèce utilise EL dans ses propres administrations.
# UK → GB : alias parfois présent dans anciens fichiers Amazon pré-Brexit.
COUNTRY_CODE_ALIASES: dict[str, str] = {
    "EL": "GR",
    "UK": "GB",
}

# Territoires hors territoire TVA UE (Art. 6 Dir. 2006/112/CE) malgré un
# code pays UE. Source de vérité centralisée dans rates.py.
# Cet import permet à classify.py de continuer à appeler is_vat_exception_territory()
# sans modification, mais la logique et les données vivent dans rates.py.
from ...rates import is_non_fiscal_eu as _is_non_fiscal_eu  # noqa: E402


def is_vat_exception_territory(country: str, postal_code: str) -> bool:
    """True si le code postal indique un territoire hors TVA UE (art. 6 Dir. 2006/112/CE).

    Délègue à rates.is_non_fiscal_eu() — source de vérité unique.
    """
    return _is_non_fiscal_eu(country, postal_code)

# Mapping marketplace → devise (fallback format 1 sans colonne devise).
MARKETPLACE_CURRENCY: dict[str, str] = {
    "amazon.co.uk":  "GBP", "amazon.uk":      "GBP",
    "amazon.pl":     "PLN", "amazon.se":      "SEK",
    "amazon.dk":     "DKK", "amazon.com":     "USD",
    "amazon.ca":     "CAD", "amazon.com.br":  "BRL",
    "amazon.com.mx": "MXN", "amazon.co.jp":   "JPY",
    "amazon.com.au": "AUD", "amazon.ae":      "AED",
    "amazon.sg":     "SGD", "amazon.in":      "INR",
    # Zone Euro
    "amazon.fr": "EUR", "amazon.de": "EUR", "amazon.it": "EUR",
    "amazon.es": "EUR", "amazon.nl": "EUR", "amazon.be": "EUR",
    "amazon.at": "EUR", "amazon.lu": "EUR", "amazon.ie": "EUR",
    "amazon.pt": "EUR", "amazon.gr": "EUR", "amazon.fi": "EUR",
}

# ---------------------------------------------------------------------------
# Fonctions utilitaires pures (pas de logging, pas d'I/O)
# ---------------------------------------------------------------------------

def is_vat_placeholder(vat_number: str) -> bool:
    """True si le numéro est un placeholder Amazon (FRINV88941X…)."""
    return bool(vat_number and _AMAZON_VAT_PLACEHOLDER.match(vat_number.strip()))


def is_valid_vat_intracom(vat: str, reg_type: str = "VAT") -> bool:
    """True si le numéro est un vrai numéro TVA intracommunautaire utilisable.

    Critères :
    - reg_type != BusinessReg (numéro registre national, pas TVA intracom)
    - Commence par un préfixe pays UE reconnu (2 lettres)
    """
    if not vat:
        return False
    if reg_type.strip().lower() == "businessreg":
        return False
    return vat.strip().upper()[:2] in EU_VAT_PREFIXES


# PERF (audit du 2026-08-19) : ce dict était auparavant construit À CHAQUE
# APPEL de is_national_tax_id() (donc à chaque ligne ayant un buyer_vat non
# vide, dans la boucle la plus chaude de tout le pipeline de parsing Amazon
# — voir loader._process_rows -> classify.classify_buyer). Sur un rapport
# de 100k+ lignes très majoritairement B2B, ça revenait à reconstruire un
# dict de 12 entrées + ~20 fermetures lambda pour rien, à chaque ligne.
# Hissé au niveau module pour n'être construit qu'une seule fois au chargement.
# Les deux patterns ES sont en plus précompilés (au lieu de re.match(pattern, s)
# qui repasse par le cache interne du module re à chaque appel).
_ES_NIF_PHYSIQUE = re.compile(r'^\d{8}[A-Z]$')
_ES_CIF_ENTITE   = re.compile(r'^[A-Z]\d{7}[A-Z0-9]$')

_NATIONAL_TAX_ID_PATTERNS: dict[str, list[Callable[[str], bool]]] = {
    "ES": [
        lambda s: bool(_ES_NIF_PHYSIQUE.match(s)),   # NIF personne physique
        lambda s: bool(_ES_CIF_ENTITE.match(s)),      # CIF / NIF entité
    ],
    "IT": [lambda s: s.isdigit() and len(s) == 11],
    "PL": [lambda s: s.isdigit() and len(s) == 10],
    "CZ": [lambda s: s.isdigit() and len(s) in (8, 9, 10)],
    "SK": [lambda s: s.isdigit() and len(s) == 10],
    "HU": [lambda s: s.isdigit() and len(s) == 8],
    "RO": [lambda s: s.isdigit() and 2 <= len(s) <= 10],
    "BG": [lambda s: s.isdigit() and len(s) in (9, 10)],
    "HR": [lambda s: s.isdigit() and len(s) == 11],
    "LT": [lambda s: s.isdigit() and len(s) in (9, 11)],
    "LV": [lambda s: s.isdigit() and len(s) == 11],
    "EE": [lambda s: s.isdigit() and len(s) == 8],
}


def is_national_tax_id(vat: str, buyer_country: str) -> bool:
    """True si le numéro est un identifiant fiscal national (pas TVA intracom).

    Ces numéros identifient des professionnels assujettis dans leur pays mais
    ne sont pas interrogeables sur VIES. Amazon les place dans BUYER_VAT_NUMBER
    sans colonne buyer_tax_registration_type pour les filtrer (formats 1-4).

    Formats détectés par pays :
    - ES : NIF/CIF : 1 lettre + 7 chiffres + 1 char  (B65885360, F99091738)
                     ou 8 chiffres + 1 lettre finale  (51235746A)
    - IT : Codice fiscale (partita IVA) : 11 chiffres purs  (03645930961)
    - PL : NIP : 10 chiffres purs  (1234567890)
    - CZ : DIČ (sans préfixe CZ) : 8, 9 ou 10 chiffres
    - SK : IČ DPH (sans préfixe SK) : 10 chiffres
    - HU : Adószám : 8 chiffres purs
    - RO : CIF : 2 à 10 chiffres purs
    - BG : ЕИК/БУЛСТАТ : 9 ou 10 chiffres purs
    - HR : OIB : 11 chiffres purs
    - LT : Mokesčių mokėtojo kodas : 9 ou 11 chiffres purs
    - LV : Reģistrācijas numurs : 11 chiffres purs
    - EE : Registrikood : 8 chiffres purs
    """
    if not vat or not buyer_country:
        return False
    if is_vat_placeholder(vat):
        return False
    v = vat.strip().upper().replace("-", "").replace(" ", "").replace(".", "")
    # Déjà un préfixe EU → numéro TVA intracom, pas un NIF national
    if v[:2] in EU_VAT_PREFIXES:
        return False
    cc = buyer_country.strip().upper()

    for check in _NATIONAL_TAX_ID_PATTERNS.get(cc, []):
        if check(v):
            return True
    return False


def normalize_country_code(code: str) -> str:
    """Normalise un code pays vers ISO 3166-1 alpha-2. Ex: EL→GR, UK→GB."""
    if not code:
        return code
    upper = code.strip().upper()
    return COUNTRY_CODE_ALIASES.get(upper, upper)


def currency_from_marketplace(marketplace: str) -> str:
    """Retourne la devise attendue pour une marketplace (fallback EUR)."""
    return MARKETPLACE_CURRENCY.get(marketplace.strip().lower(), "EUR")


def safe_decimal(value: str | None) -> Decimal:
    """Convertit une chaîne en Decimal, retourne 0 si vide ou invalide."""
    if value is None:
        return Decimal("0")
    cleaned = value.strip().replace(",", ".")
    if not cleaned or cleaned in ("-", "n/a", ""):
        return Decimal("0")
    try:
        return Decimal(cleaned)
    except InvalidOperation:
        logger.warning(
            "safe_decimal (Amazon) : montant illisible ('%s', nettoyé en '%s') -- "
            "traité comme 0. Vérifier la ligne source si le résultat semble décalé.",
            value, cleaned,
        )
        return Decimal("0")