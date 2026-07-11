"""Constantes et utilitaires purs partagés entre les sous-modules Amazon.

Aucune dépendance vers les autres sous-modules amazon/ — peut être importé
partout sans risque de cycle.
"""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation

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

    _PATTERNS: dict[str, object] = {
        "ES": [
            lambda s: bool(re.match(r'^\d{8}[A-Z]$', s)),           # NIF personne physique
            lambda s: bool(re.match(r'^[A-Z]\d{7}[A-Z0-9]$', s)),   # CIF / NIF entité
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
    for check in _PATTERNS.get(cc, []):
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
        return Decimal("0")