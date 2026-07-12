"""Taux de TVA par pays, catégories de produits et appartenance a l'UE.

Source unique de vérité pour toutes les données pays UE du projet :
  - EU_COUNTRIES, STANDARD_VAT_RATES, REDUCED_VAT_RATES (calcul moteur)
  - COUNTRY_NAMES, COUNTRY_ISO3 (noms et codes ISO 3166-1 alpha-3)
  - COUNTRY_FISCAL_META (déclarations fiscales locales — utilisé par app.py)
  - VAT_RATE_HISTORY (taux historiques depuis le 01/01/2024 pour toutes catégories)

Taux actuels (STANDARD_VAT_RATES & REDUCED_VAT_RATES) : situation au 01/01/2026+.
Taux historiques (VAT_RATE_HISTORY) : corrections applicables pour les
fichiers couvrant la période 01/01/2024–présent.
Périmètre historique : à partir du 01/01/2024 (antérieur = hors scope).

Source : Commission européenne, Taxation and Customs Union,
         tableau des taux TVA 2024/2026.
Dernière vérification : juin 2026.
"""

from __future__ import annotations

import logging
from datetime import date
from decimal import Decimal
from typing import Dict, List, NamedTuple, Optional, Set, Tuple

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Noms et codes pays — SOURCE UNIQUE
# ---------------------------------------------------------------------------

COUNTRY_NAMES: Dict[str, str] = {
    "AT": "Autriche",    "BE": "Belgique",   "BG": "Bulgarie",
    "HR": "Croatie",     "CY": "Chypre",     "CZ": "Tchèque",
    "DK": "Danemark",    "EE": "Estonie",    "FI": "Finlande",
    "FR": "France",      "DE": "Allemagne",  "GR": "Grèce",
    "HU": "Hongrie",     "IE": "Irlande",    "IT": "Italie",
    "LV": "Lettonie",    "LT": "Lituanie",   "LU": "Luxembourg",
    "MT": "Malte",       "NL": "Pays-Bas",   "PL": "Pologne",
    "PT": "Portugal",    "RO": "Roumanie",   "SK": "Slovaquie",
    "SI": "Slovénie",    "ES": "Espagne",    "SE": "Suède",
    "MC": "Monaco",     "XI": "Irlande du Nord",
}

COUNTRY_ISO3: Dict[str, str] = {
    "AT": "AUT", "BE": "BEL", "BG": "BGR", "HR": "HRV", "CY": "CYP",
    "CZ": "CZE", "DK": "DNK", "EE": "EST", "FI": "FIN", "FR": "FRA",
    "DE": "DEU", "GR": "GRC", "HU": "HUN", "IE": "IRL", "IT": "ITA",
    "LV": "LVA", "LT": "LTU", "LU": "LUX", "MT": "MLT", "NL": "NLD",
    "PL": "POL", "PT": "PRT", "RO": "ROU", "SK": "SVK", "SI": "SVN",
    "ES": "ESP", "SE": "SWE", "MC": "MCO", "XI": "NIR",
}

# Métadonnées fiscales locales par pays.
# Mise à jour juin 2026 : Taux réduit principal de la Finlande ajusté à 13.5%.
COUNTRY_FISCAL_META: Dict[str, Tuple[str, str, str, str, str]] = {
    "AT": ("Umsatzsteuervoranmeldung (UVA)",    "Bemessungsgrundlage", "Umsatzsteuer", "20%",   "10%"),
    "BE": ("Declaration TVA / BTW-aangifte",    "Base imposable",      "TVA / BTW",   "21%",   "6%"),
    "BG": ("Spravka-deklaraciya po ZDDS",       "Danachna osnova",     "DDS",         "20%",   "9%"),
    "HR": ("Prijava PDV-a",                     "Porezna osnovica",    "PDV",         "25%",   "5%"),
    "CY": ("FPA Dilosi",                        "Forologitea axia",    "FPA",         "19%",   "5%"),
    "CZ": ("Priznani k DPH",                    "Zaklad dane",         "DPH",         "21%",   "12%"),
    "DK": ("Momsangivelse",                     "Afgiftsgrundlag",     "Moms",        "25%",   "—"),
    "EE": ("Kaibemaksudeklaratsioon (KMD)",     "Maksustatav kaive",   "Kaibemaks",  "24%",   "9%"),   
    "FI": ("Arvonlisaveroilmoitus",             "Veron peruste",       "ALV",         "25.5%", "13.5%"), # 13.5% depuis jan. 2026
    "FR": ("Declaration TVA (CA3 / OSS)",       "Base HT",             "TVA",         "20%",   "5.5%"),
    "MC": ("Declaration TVA (CA3 / OSS)",       "Base HT",             "TVA",         "20%",   "5.5%"),
    "DE": ("Umsatzsteuer-Voranmeldung (UStVA)", "Bemessungsgrundlage", "Umsatzsteuer","19%",   "7%"),
    "GR": ("Dilosi FPA",                        "Forologitea axia",    "FPA",         "24%",   "6%"),
    "HU": ("AFA-bevalles",                      "Adoalap",             "AFA",         "27%",   "5%"),
    "IE": ("VAT Return (VAT3)",                 "Taxable amount",      "VAT",         "23%",   "0%"),   
    "IT": ("Dichiarazione IVA / Liquidazione",  "Imponibile",          "IVA",         "22%",   "4%"),
    "LV": ("PVN deklaracija",                   "Nodokla baze",        "PVN",         "21%",   "5%"),
    "LT": ("PVM deklaracija (FR0600)",          "Apmokestinamoji verte","PVM",        "21%",   "5%"),
    "LU": ("Declaration TVA trimestrielle",     "Base imposable",      "TVA",         "17%",   "3%"),   
    "MT": ("VAT Return",                        "Taxable value",       "VAT",         "18%",   "0%"),
    "NL": ("BTW-aangifte",                      "Belaste omzet",       "BTW",         "21%",   "9%"),
    "PL": ("Deklaracja JPK_V7M / JPK_V7K",     "Podstawa opodatkowania","VAT",       "23%",   "5%"),
    "PT": ("Declaracao Periodica de IVA",       "Base tributavel",     "IVA",         "23%",   "6%"),
    "RO": ("Decont de TVA (D300)",              "Baza de impozitare",  "TVA",         "21%",   "9%"),   
    "SK": ("Danove priznanie k DPH",            "Zaklad dane",         "DPH",         "23%",   "5%"),   
    "SI": ("Obracun DDV (DDV-O)",               "Davcna osnova",       "DDV",         "22%",   "9.5%"),
    "ES": ("Modelo 303 / Declaracion IVA",      "Base imponible",      "IVA",         "21%",   "4%"),
    "SE": ("Mervardesskattdeklaration (SKV 4700)","Beskattningsunderlag","Moms",      "25%",   "6%"),
    "XI": ("VAT Return (Northern Ireland)",     "Taxable amount",      "VAT",         "20%",   "5%"),
}

# ---------------------------------------------------------------------------
# Codes cases officiels des déclarations TVA locales par pays (hors FR)
# ---------------------------------------------------------------------------
# Source unique partagée par l'export CSV pré-formaté (ui/tabs/telechargements.py)
# et le rapport HTML (local_vat_report.py). Clé = taux de TVA en % (str, sans
# décimales inutiles, ex "19", "5" ou "5.5") tel qu'il apparaît dans VatResult.vat_rate.
# Valeur = (code_case, libellé_court) ou libellé_court seul si le pays n'a pas
# de code de case numéroté (ex. Italie : taux uniquement).
# ⚠️ Non exhaustif : seuls les pays où un client a eu besoin d'un mapping
# précis ont été vérifiés. Les autres pays utilisent un rendu générique
# (taux + libellé neutre, pas de code case) — voir local_vat_report.py.
LOCAL_VAT_BOX_CODES: Dict[str, Tuple[List[str], Dict]] = {
    "DE": (["Kennzahl", "Bezeichnung", "Base (EUR)", "TVA (EUR)", "Nb"], {"19": ("81", "19%"), "7": ("86", "7%")}),
    "ES": (["Casilla", "Concepto", "Base (EUR)", "TVA (EUR)", "Nb"], {"21": ("01", "21%"), "10": ("03", "10%"), "4": ("05", "4%")}),
    "IT": (["Aliquota", "Descrizione", "Base (EUR)", "TVA (EUR)", "N."], {"22": "22%", "10": "10%", "4": "4%"}),
    "PL": (["Pole", "Opis", "Base", "TVA", "Liczba"], {"23": ("K_19", "23%"), "8": ("K_17", "8%"), "5": ("K_15", "5%")}),
    "NL": (["Rubriek", "Omschrijving", "Base (EUR)", "TVA (EUR)", "Antal"], {"21": ("1a", "21%"), "9": ("1b", "9%")}),
    "BE": (["Grille", "Description", "Base (EUR)", "TVA (EUR)", "Nb"], {"21": ("03", "21%"), "12": ("02", "12%"), "6": ("01", "6%")}),
    "PT": (["Campo", "Descricao", "Base (EUR)", "TVA (EUR)", "N."], {"23": ("1", "23%"), "13": ("2", "13%"), "6": ("3", "6%")}),
    "SE": (["Ruta", "Beskrivning", "Base", "TVA", "Antal"], {"25": ("05", "25%"), "12": ("06", "12%"), "6": ("07", "6%")}),
    "AT": (["Kennzahl", "Bezeichnung", "Base (EUR)", "TVA (EUR)", "Anz."], {"20": ("022", "20%"), "10": ("029", "10%"), "13": ("006", "13%")}),
    "CZ": (["Radek", "Popis", "Base", "TVA", "Pocet"], {"21": ("1", "21%"), "12": ("2", "12%")}),
    "RO": (["Rand", "Descriere", "Base", "TVA", "Nr."], {"19": ("9", "19%"), "9": ("10", "9%"), "5": ("11", "5%")}),
    "HU": (["Sor", "Megnevezes", "Base", "TVA", "Db"], {"27": ("B2", "27%"), "18": ("C2", "18%"), "5": ("D2", "5%")}),
    "IE": (["Box", "Description", "Base (EUR)", "TVA (EUR)", "Count"], {"23": ("T1", "23%"), "9": ("T1", "9%"), "0": ("E1", "0%")}),
}

EU_COUNTRIES: Set[str] = set(COUNTRY_NAMES.keys())

# ---------------------------------------------------------------------------
# Territoires exclus du territoire fiscal de l'UE (TVA)
# ---------------------------------------------------------------------------
# Territoires exclus du territoire fiscal de l'UE (TVA)
# ---------------------------------------------------------------------------
NON_FISCAL_EU_POSTCODES: Dict[str, Dict] = {
    "ES": {
        "prefixes": [
            "35",  # Îles Canaries – hors TVA UE
            "38",  # Îles Canaries – hors TVA UE
            "51",  # Ceuta – hors TVA UE
            "52",  # Melilla – hors TVA UE
        ]
    },

    "FR": {
        "prefixes": [
            "97",  # DOM (Guadeloupe, Martinique, Guyane, Réunion, Mayotte) – dans l’UE mais hors TVA
            "98",  # TOM (Polynésie, Nouvelle-Calédonie, etc.) – hors UE et hors TVA
            "975", # Saint-Pierre-et-Miquelon – hors UE et hors TVA
            "984", # Terres australes et antarctiques françaises – hors UE et hors TVA
            "986", # Wallis-et-Futuna – hors UE et hors TVA
            "987", # Polynésie française – hors UE et hors TVA
            "988", # Nouvelle-Calédonie – hors UE et hors TVA
        ],
    },

    "DE": {
        "prefixes": [
            "27498", # Helgoland – hors TVA UE
            "78266", # Büsingen – hors TVA UE
        ]
    },

    "GR": {
        "prefixes": [
            "63086", # Mont Athos – hors TVA UE
            "63087", # Mont Athos – hors TVA UE
            "63088", # Mont Athos – hors TVA UE
        ]
    },

    "IT": {
        "prefixes": [
            "23030", # Livigno – hors TVA UE
            "22060", # Campione d’Italia – hors TVA UE
            "22061", # Lac de Lugano (zone italienne) – hors TVA UE
        ]
    },

    "FI": {
        "prefixes": [
            "22",   # Îles Åland – hors TVA UE
        ]
    },

    "DK": {
        "country_codes": [
            "GL",  # Groenland – hors UE et hors TVA
            "FO",  # Îles Féroé – hors UE et hors TVA
        ]
    },

    "NL": {
        "country_codes": [
            "CW",  # Curaçao – hors UE et hors TVA
            "AW",  # Aruba – hors UE et hors TVA
            "SX",  # Sint-Maarten – hors UE et hors TVA
            "BQ",  # Bonaire, Saba, Saint-Eustache – hors UE et hors TVA
        ]
    },

    "CY": {
        "notes": [
            "CY-NORTH",  # Chypre du Nord – hors TVA UE (absence de contrôle effectif)
            "GB-SBA",    # Bases britanniques d’Akrotiri et Dhekelia – hors TVA UE
        ]
    },
}


def is_non_fiscal_eu(country: str, post_code: str | None) -> bool:
    """Retourne True si le territoire est exclu du territoire fiscal de l'UE (TVA).
    
    Gère à la fois les pays ayant leur propre code ISO (ex: GL pour le Groenland)
    et les régions identifiées par leur code postal (ex: 35 pour les Canaries).
    """
    code = country.upper()

    # 1. Vérification par code pays direct (cas GL, FO, CW, AW, etc.)
    for rules in NON_FISCAL_EU_POSTCODES.values():
        if code in rules.get("country_codes", []) or code in rules.get("notes", []):
            return True

    # 2. Vérification par préfixe de code postal
    if code not in NON_FISCAL_EU_POSTCODES:
        return False
    if not post_code:
        return False
    pc = post_code.strip().replace(" ", "").upper()
    rule = NON_FISCAL_EU_POSTCODES[code]
    for prefix in rule.get("prefixes", []):
        if pc.startswith(prefix):
            return True
    return False

def is_eu(country: str) -> bool:
    return country.upper() in EU_COUNTRIES

def is_fiscal_eu(country: str, post_code: str | None = None) -> bool:
    """Retourne True si le territoire appartient au territoire fiscal de l'UE.
    
    Un territoire est dans le territoire fiscal s'il est dans l'UE (is_eu)
    ET qu'il n'est pas explicitement exclu (is_non_fiscal_eu).
    """
    # Si c'est un territoire exclu (même s'il a son propre code pays), on renvoie False
    if is_non_fiscal_eu(country, post_code):
        return False
    # Sinon, on vérifie s'il appartient à l'UE
    return is_eu(country)

# Taux standard courants (2026)
STANDARD_VAT_RATES: Dict[str, Decimal] = {
    "AT": Decimal("20"), "BE": Decimal("21"), "BG": Decimal("20"), "HR": Decimal("25"),
    "CY": Decimal("19"), "CZ": Decimal("21"), "DK": Decimal("25"), "EE": Decimal("24"),   
    "FI": Decimal("25.5"), "FR": Decimal("20"), "DE": Decimal("19"), "GR": Decimal("24"),
    "HU": Decimal("27"), "IE": Decimal("23"), "IT": Decimal("22"), "LV": Decimal("21"),
    "LT": Decimal("21"), "LU": Decimal("17"), "MT": Decimal("18"), "NL": Decimal("21"),
    "PL": Decimal("23"), "PT": Decimal("23"), "RO": Decimal("21"), "SK": Decimal("23"),   
    "SI": Decimal("22"), "ES": Decimal("21"), "SE": Decimal("25"),
    "MC": Decimal("20"), "XI": Decimal("20"),
}

# ---------------------------------------------------------------------------
# Historique des taux — Périmètre 01/01/2024 → Présent (Toutes catégories)
# ---------------------------------------------------------------------------

class _VatPeriod(NamedTuple):
    country: str
    date_from: date
    date_to: Optional[date]
    rate: Decimal
    category: str = "STANDARD"  # Valeur par défaut pour garder la flexibilité


VAT_RATE_HISTORY: List[_VatPeriod] = [
    # --- Estonie (EE) ---
    _VatPeriod("EE", date(2024, 1, 1),  date(2025, 6, 30),  Decimal("22"), "STANDARD"),
    _VatPeriod("EE", date(2025, 7, 1),  None,               Decimal("24"), "STANDARD"),
    _VatPeriod("EE", date(2024, 1, 1),  date(2025, 6, 30),  Decimal("22"), "FOOD"),      # Suit le taux standard
    _VatPeriod("EE", date(2024, 1, 1),  date(2025, 6, 30),  Decimal("22"), "CLOTHING"),  # Suit le taux standard

    # --- Finlande (FI) ---
    _VatPeriod("FI", date(2024, 1, 1),  date(2024, 8, 31),  Decimal("24"), "STANDARD"),
    _VatPeriod("FI", date(2024, 9, 1),  None,               Decimal("25.5"), "STANDARD"),
    # Livres, médicaments & super-réduits (10% en 2024 -> 14% en 2025 -> 13.5% en 2026 par défaut)
    _VatPeriod("FI", date(2024, 1, 1),  date(2024, 12, 31), Decimal("10"), "BOOKS"),
    _VatPeriod("FI", date(2025, 1, 1),  date(2025, 12, 31), Decimal("14"), "BOOKS"),
    _VatPeriod("FI", date(2024, 1, 1),  date(2024, 12, 31), Decimal("10"), "MEDICINES"),
    _VatPeriod("FI", date(2025, 1, 1),  date(2025, 12, 31), Decimal("14"), "MEDICINES"),
    _VatPeriod("FI", date(2024, 1, 1),  date(2024, 12, 31), Decimal("10"), "SUPER_REDUCED"),
    _VatPeriod("FI", date(2025, 1, 1),  date(2025, 12, 31), Decimal("14"), "SUPER_REDUCED"),
    # Alimentation générale (14% stable en 2024/2025 -> passe à 13.5% en 2026 par défaut)
    _VatPeriod("FI", date(2024, 1, 1),  date(2025, 12, 31), Decimal("14"), "FOOD"),
    # Vêtements (suit le taux standard de près)
    _VatPeriod("FI", date(2024, 1, 1),  date(2024, 8, 31),  Decimal("24"), "CLOTHING"),
    _VatPeriod("FI", date(2024, 9, 1),  None,               Decimal("25.5"), "CLOTHING"),

    # --- Roumanie (RO) ---
    _VatPeriod("RO", date(2024, 1, 1),  date(2025, 7, 31),  Decimal("19"), "STANDARD"),
    _VatPeriod("RO", date(2025, 8, 1),  None,               Decimal("21"), "STANDARD"),
    _VatPeriod("RO", date(2024, 1, 1),  date(2025, 7, 31),  Decimal("19"), "CLOTHING"),  # Suit le taux standard

    # --- Slovaquie (SK) ---
    _VatPeriod("SK", date(2024, 1, 1),  date(2024, 12, 31), Decimal("20"), "STANDARD"),
    _VatPeriod("SK", date(2025, 1, 1),  None,               Decimal("23"), "STANDARD"),
    # Réforme des taux réduits au 01/01/2025 (10% -> 5% sur l'essentiel)
    _VatPeriod("SK", date(2024, 1, 1),  date(2024, 12, 31), Decimal("10"), "BOOKS"),
    _VatPeriod("SK", date(2024, 1, 1),  date(2024, 12, 31), Decimal("10"), "FOOD"),
    _VatPeriod("SK", date(2024, 1, 1),  date(2024, 12, 31), Decimal("10"), "MEDICINES"),
    _VatPeriod("SK", date(2024, 1, 1),  date(2024, 12, 31), Decimal("10"), "SUPER_REDUCED"),
    _VatPeriod("SK", date(2024, 1, 1),  date(2024, 12, 31), Decimal("20"), "CLOTHING"),

    # --- Espagne (ES) ---
    # Mesures anti-inflation alimentaires : 0% puis 2% en 2024, retour à la normale (4%) en 2025
    _VatPeriod("ES", date(2024, 1, 1),  date(2024, 9, 30),  Decimal("0"),  "FOOD"),
    _VatPeriod("ES", date(2024, 10, 1), date(2024, 12, 31), Decimal("2"),  "FOOD"),
    _VatPeriod("ES", date(2024, 1, 1),  date(2024, 9, 30),  Decimal("0"),  "SUPER_REDUCED"),
    _VatPeriod("ES", date(2024, 10, 1), date(2024, 12, 31), Decimal("2"),  "SUPER_REDUCED"),
]

# Index précalculé : (pays, catégorie) → liste de périodes (triées par date_from)
_HISTORY_INDEX: Dict[Tuple[str, str], List[_VatPeriod]] = {}
for _p in VAT_RATE_HISTORY:
    _HISTORY_INDEX.setdefault((_p.country, _p.category), []).append(_p)


def vat_rate_at_date(
    country: str,
    tx_date: date,
    product_category: str = "STANDARD",
) -> Decimal:
    """Retourne le taux TVA applicable à une date de transaction donnée pour une catégorie spécifique.

    Cherche en priorité dans l'historique précalculé. En l'absence de correspondance,
    bascule sur le taux courant par défaut (situations stables).
    """
    code = country.upper()
    cat = product_category.upper()

    # Normalisation linguistique FR/EN des catégories pour le moteur
    if cat in ("LIVRES", "BOOKS"):
        cat = "BOOKS"
    elif cat in ("ALIMENTATION", "FOOD"):
        cat = "FOOD"
    elif cat in ("MEDICAMENTS", "MEDICINES"):
        cat = "MEDICINES"
    elif cat in ("VETEMENTS", "CLOTHING"):
        cat = "CLOTHING"

    # Recherche dans l'historique (clé composite Pays + Catégorie)
    periods = _HISTORY_INDEX.get((code, cat))
    if periods:
        for period in periods:
            if period.date_from <= tx_date:
                if period.date_to is None or tx_date <= period.date_to:
                    return period.rate

        logger.debug(
            "vat_rate_at_date : aucune période historique pour %s (%s) au %s "
            "— utilisation du taux courant par défaut.",
            code, cat, tx_date,
        )

    # Filet de sécurité / Fallback sur les structures de taux en vigueur (2026)
    if cat == "STANDARD":
        if code not in STANDARD_VAT_RATES:
            raise KeyError(f"Pays inconnu : '{code}'.")
        return STANDARD_VAT_RATES[code]
    
    if code not in REDUCED_VAT_RATES:
        raise KeyError(f"Pays inconnu : '{code}'.")
        
    country_rates = REDUCED_VAT_RATES[code]
    if cat in country_rates:
        return country_rates[cat]
    elif product_category.upper() in country_rates:
        return country_rates[product_category.upper()]
    else:
        logger.warning("Catégorie '%s' non configurée pour %s. Fallback STANDARD.", product_category, code)
        return STANDARD_VAT_RATES.get(code, Decimal("20"))


def has_rate_changed(country: str) -> bool:
    """Retourne True si le pays a enregistré au moins un changement (standard ou réduit)."""
    code = country.upper()
    return any(k[0] == code for k in _HISTORY_INDEX.keys())


def rate_periods_for_country(country: str) -> List[_VatPeriod]:
    """Retourne toutes les périodes historiques d'un pays (toutes catégories confondues)."""
    code = country.upper()
    periods = [p for keys, p_list in _HISTORY_INDEX.items() if keys[0] == code for p in p_list]
    return sorted(periods, key=lambda x: (x.category, x.date_from))


# ─────────────────────────────────────────────────────────────────────────
# Seuils Intrastat / EMEBI (France) — obligation STATISTIQUE uniquement.
#
# Depuis 2022, la douane française a scindé l'ancienne "DEB" en deux
# obligations distinctes et indépendantes :
#   1. EMEBI (Enquête Mensuelle sur les Échanges de Biens intra-UE) :
#      obligation STATISTIQUE, déclenchée uniquement au-delà du seuil ci-
#      dessous, et par sens de flux (introductions / expéditions). En
#      dessous du seuil, aucune obligation statistique — la douane peut
#      toutefois solliciter certains opérateurs par échantillonnage même
#      sous le seuil (hors périmètre de cet outil).
#   2. État récapitulatif TVA (ESL/DES) : obligation FISCALE, dès le
#      1er euro, pour les livraisons intracommunautaires B2B exonérées
#      (art. 289 B CGI) — indépendante du seuil EMEBI. Voir
#      `excel_report.py::_write_calendar_tab` (onglet Calendrier Fiscal),
#      qui génère cette échéance séparément de l'onglet Intrastat.
#
# Le seuil est historiquement resté stable (460 000 €/an) mais n'est pas
# garanti par la loi d'une année sur l'autre : il doit être revérifié
# chaque année sur pro.douane.gouv.fr. Cette table ne doit JAMAIS être
# lue comme une source légale figée — seulement comme la dernière valeur
# connue au moment de la mise à jour de ce fichier.
INTRASTAT_EMEBI_THRESHOLDS_FR: Dict[int, Decimal] = {
    2022: Decimal("460000"),
    2023: Decimal("460000"),
    2024: Decimal("460000"),
    2025: Decimal("460000"),
    2026: Decimal("460000"),  # À reconfirmer chaque année sur pro.douane.gouv.fr
}

_LATEST_KNOWN_INTRASTAT_YEAR = max(INTRASTAT_EMEBI_THRESHOLDS_FR)


def intrastat_emebi_threshold_for_year(year: int) -> tuple[Decimal, bool]:
    """Retourne (seuil EMEBI en €, seuil_confirmé) pour l'année donnée.

    `seuil_confirmé` est False lorsque l'année demandée est postérieure à la
    dernière année explicitement répertoriée dans `INTRASTAT_EMEBI_THRESHOLDS_FR` :
    dans ce cas, la dernière valeur connue est retournée par extrapolation,
    mais l'appelant DOIT signaler à l'utilisateur que ce seuil reste à
    vérifier (le seuil est fixé par arrêté douanier et peut changer sans
    préavis d'une année sur l'autre).
    """
    if year in INTRASTAT_EMEBI_THRESHOLDS_FR:
        return INTRASTAT_EMEBI_THRESHOLDS_FR[year], True
    return INTRASTAT_EMEBI_THRESHOLDS_FR[_LATEST_KNOWN_INTRASTAT_YEAR], False


# Mapping complet des taux actuels (Situation 2026)
REDUCED_VAT_RATES: Dict[str, Dict[str, Decimal]] = {
    "AT": {  
        "BOOKS": Decimal("10"), "LIVRES": Decimal("10"),
        "FOOD": Decimal("10"), "ALIMENTATION": Decimal("10"),
        "MEDICINES": Decimal("10"), "MEDICAMENTS": Decimal("10"),
        "CLOTHING": Decimal("20"), "VETEMENTS": Decimal("20"),
        "SUPER_REDUCED": Decimal("10"),  
        "PARKING": Decimal("13"),        
    },
    "BE": {  
        "BOOKS": Decimal("6"), "LIVRES": Decimal("6"),
        "FOOD": Decimal("6"), "ALIMENTATION": Decimal("6"),
        "MEDICINES": Decimal("6"), "MEDICAMENTS": Decimal("6"),
        "CLOTHING": Decimal("21"), "VETEMENTS": Decimal("21"),
        "SUPER_REDUCED": Decimal("6"),
        "PARKING": Decimal("12"),        
    },
    "BG": {  
        "BOOKS": Decimal("9"), "LIVRES": Decimal("9"),
        "FOOD": Decimal("9"), "ALIMENTATION": Decimal("9"),
        "MEDICINES": Decimal("20"), "MEDICAMENTS": Decimal("20"),
        "CLOTHING": Decimal("20"), "VETEMENTS": Decimal("20"),
        "SUPER_REDUCED": Decimal("9"),
        "PARKING": Decimal("20"),
    },
    "HR": {  
        "BOOKS": Decimal("5"), "LIVRES": Decimal("5"),
        "FOOD": Decimal("5"), "ALIMENTATION": Decimal("5"),
        "MEDICINES": Decimal("5"), "MEDICAMENTS": Decimal("5"),
        "CLOTHING": Decimal("25"), "VETEMENTS": Decimal("25"),
        "SUPER_REDUCED": Decimal("5"),
        "PARKING": Decimal("25"),
    },
    "CY": {  
        "BOOKS": Decimal("5"), "LIVRES": Decimal("5"),
        "FOOD": Decimal("5"), "ALIMENTATION": Decimal("5"),
        "MEDICINES": Decimal("5"), "MEDICAMENTS": Decimal("5"),
        "CLOTHING": Decimal("19"), "VETEMENTS": Decimal("19"),
        "SUPER_REDUCED": Decimal("5"),
        "PARKING": Decimal("19"),
    },
    "CZ": {  
        "BOOKS": Decimal("0"), "LIVRES": Decimal("0"),
        "FOOD": Decimal("12"), "ALIMENTATION": Decimal("12"),
        "MEDICINES": Decimal("12"), "MEDICAMENTS": Decimal("12"),
        "CLOTHING": Decimal("21"), "VETEMENTS": Decimal("21"),
        "SUPER_REDUCED": Decimal("0"),
        "PARKING": Decimal("21"),
    },
    "DK": {  
        "BOOKS": Decimal("25"), "LIVRES": Decimal("25"),
        "FOOD": Decimal("25"), "ALIMENTATION": Decimal("25"),
        "MEDICINES": Decimal("25"), "MEDICAMENTS": Decimal("25"),
        "CLOTHING": Decimal("25"), "VETEMENTS": Decimal("25"),
        "SUPER_REDUCED": Decimal("25"),
        "PARKING": Decimal("25"),
    },
    "EE": {  
        "BOOKS": Decimal("9"), "LIVRES": Decimal("9"),
        "FOOD": Decimal("24"), "ALIMENTATION": Decimal("24"),
        "MEDICINES": Decimal("9"), "MEDICAMENTS": Decimal("9"),
        "CLOTHING": Decimal("24"), "VETEMENTS": Decimal("24"),
        "SUPER_REDUCED": Decimal("9"),
        "PARKING": Decimal("24"),
    },
    "FI": {  
        "BOOKS": Decimal("13.5"), "LIVRES": Decimal("13.5"),  # Fixé à 13.5% au Budget 2026
        "FOOD": Decimal("13.5"), "ALIMENTATION": Decimal("13.5"),
        "MEDICINES": Decimal("13.5"), "MEDICAMENTS": Decimal("13.5"),
        "CLOTHING": Decimal("25.5"), "VETEMENTS": Decimal("25.5"),
        "SUPER_REDUCED": Decimal("13.5"),
        "PARKING": Decimal("25.5"),
    },
    "FR": {  
        "BOOKS": Decimal("5.5"), "LIVRES": Decimal("5.5"),
        "FOOD": Decimal("5.5"), "ALIMENTATION": Decimal("5.5"),
        "MEDICINES": Decimal("5.5"), "MEDICAMENTS": Decimal("5.5"),
        "CLOTHING": Decimal("20"), "VETEMENTS": Decimal("20"),
        "SUPER_REDUCED": Decimal("2.1"),  
        "PARKING": Decimal("20"),          
    },
    "DE": {  
        "BOOKS": Decimal("7"), "LIVRES": Decimal("7"),
        "FOOD": Decimal("7"), "ALIMENTATION": Decimal("7"),
        "MEDICINES": Decimal("19"), "MEDICAMENTS": Decimal("19"),
        "CLOTHING": Decimal("19"), "VETEMENTS": Decimal("19"),
        "SUPER_REDUCED": Decimal("7"),
        "PARKING": Decimal("19"),
    },
    "GR": {  
        "BOOKS": Decimal("6"), "LIVRES": Decimal("6"),
        "FOOD": Decimal("13"), "ALIMENTATION": Decimal("13"),
        "MEDICINES": Decimal("6"), "MEDICAMENTS": Decimal("6"),
        "CLOTHING": Decimal("24"), "VETEMENTS": Decimal("24"),
        "SUPER_REDUCED": Decimal("6"),
        "PARKING": Decimal("24"),
    },
    "HU": {  
        "BOOKS": Decimal("5"), "LIVRES": Decimal("5"),
        "FOOD": Decimal("18"), "ALIMENTATION": Decimal("18"),
        "MEDICINES": Decimal("5"), "MEDICAMENTS": Decimal("5"),
        "CLOTHING": Decimal("27"), "VETEMENTS": Decimal("27"),
        "SUPER_REDUCED": Decimal("5"),
        "PARKING": Decimal("27"),
    },
    "IE": {  
        "BOOKS": Decimal("0"), "LIVRES": Decimal("0"),
        "FOOD": Decimal("0"), "ALIMENTATION": Decimal("0"),
        "MEDICINES": Decimal("0"), "MEDICAMENTS": Decimal("0"),
        "CLOTHING": Decimal("0"), "VETEMENTS": Decimal("0"),   
        "SUPER_REDUCED": Decimal("4.8"),   
        "PARKING": Decimal("13.5"),         
    },
    "IT": {  
        "BOOKS": Decimal("4"), "LIVRES": Decimal("4"),
        "FOOD": Decimal("4"), "ALIMENTATION": Decimal("4"),
        "MEDICINES": Decimal("10"), "MEDICAMENTS": Decimal("10"),
        "CLOTHING": Decimal("22"), "VETEMENTS": Decimal("22"),
        "SUPER_REDUCED": Decimal("4"),   
        "PARKING": Decimal("22"),         
    },
    "LV": {  
        "BOOKS": Decimal("5"), "LIVRES": Decimal("5"),
        "FOOD": Decimal("5"), "ALIMENTATION": Decimal("5"),
        "MEDICINES": Decimal("12"), "MEDICAMENTS": Decimal("12"),
        "CLOTHING": Decimal("21"), "VETEMENTS": Decimal("21"),
        "SUPER_REDUCED": Decimal("5"),
        "PARKING": Decimal("21"),
    },
    "LT": {  
        "BOOKS": Decimal("9"), "LIVRES": Decimal("9"),
        "FOOD": Decimal("5"), "ALIMENTATION": Decimal("5"),
        "MEDICINES": Decimal("5"), "MEDICAMENTS": Decimal("5"),
        "CLOTHING": Decimal("21"), "VETEMENTS": Decimal("21"),
        "SUPER_REDUCED": Decimal("5"),
        "PARKING": Decimal("21"),
    },
    "LU": {  
        "BOOKS": Decimal("3"), "LIVRES": Decimal("3"),
        "FOOD": Decimal("3"), "ALIMENTATION": Decimal("3"),
        "MEDICINES": Decimal("3"), "MEDICAMENTS": Decimal("3"),
        "CLOTHING": Decimal("17"), "VETEMENTS": Decimal("17"),
        "SUPER_REDUCED": Decimal("3"),
        "PARKING": Decimal("14"),   
    },
    "MT": {  
        "BOOKS": Decimal("5"), "LIVRES": Decimal("5"),
        "FOOD": Decimal("0"), "ALIMENTATION": Decimal("0"),
        "MEDICINES": Decimal("0"), "MEDICAMENTS": Decimal("0"),
        "CLOTHING": Decimal("18"), "VETEMENTS": Decimal("18"),
        "SUPER_REDUCED": Decimal("0"),
        "PARKING": Decimal("18"),
    },
    "NL": {  
        "BOOKS": Decimal("9"), "LIVRES": Decimal("9"),
        "FOOD": Decimal("9"), "ALIMENTATION": Decimal("9"),
        "MEDICINES": Decimal("9"), "MEDICAMENTS": Decimal("9"),
        "CLOTHING": Decimal("21"), "VETEMENTS": Decimal("21"),
        "SUPER_REDUCED": Decimal("9"),
        "PARKING": Decimal("21"),
    },
    "PL": {  
        "BOOKS": Decimal("5"), "LIVRES": Decimal("5"),
        "FOOD": Decimal("5"), "ALIMENTATION": Decimal("5"),
        "MEDICINES": Decimal("8"), "MEDICAMENTS": Decimal("8"),
        "CLOTHING": Decimal("23"), "VETEMENTS": Decimal("23"),
        "SUPER_REDUCED": Decimal("5"),
        "PARKING": Decimal("23"),
    },
    "PT": {  
        "BOOKS": Decimal("6"), "LIVRES": Decimal("6"),
        "FOOD": Decimal("6"), "ALIMENTATION": Decimal("6"),
        "MEDICINES": Decimal("6"), "MEDICAMENTS": Decimal("6"),
        "CLOTHING": Decimal("23"), "VETEMENTS": Decimal("23"),
        "SUPER_REDUCED": Decimal("6"),
        "PARKING": Decimal("13"),   
    },
    "RO": {  
        "BOOKS": Decimal("11"), "LIVRES": Decimal("11"),
        "FOOD": Decimal("11"), "ALIMENTATION": Decimal("11"),
        "MEDICINES": Decimal("11"), "MEDICAMENTS": Decimal("11"),
        "CLOTHING": Decimal("21"), "VETEMENTS": Decimal("21"),
        "SUPER_REDUCED": Decimal("9"),
        "PARKING": Decimal("21"),
    },
    "SK": {  
        "BOOKS": Decimal("5"), "LIVRES": Decimal("5"),
        "FOOD": Decimal("5"), "ALIMENTATION": Decimal("5"),
        "MEDICINES": Decimal("5"), "MEDICAMENTS": Decimal("5"),
        "CLOTHING": Decimal("23"), "VETEMENTS": Decimal("23"),
        "SUPER_REDUCED": Decimal("5"),
        "PARKING": Decimal("23"),
    },
    "SI": {  
        "BOOKS": Decimal("9.5"), "LIVRES": Decimal("9.5"),
        "FOOD": Decimal("9.5"), "ALIMENTATION": Decimal("9.5"),
        "MEDICINES": Decimal("9.5"), "MEDICAMENTS": Decimal("9.5"),
        "CLOTHING": Decimal("22"), "VETEMENTS": Decimal("22"),
        "SUPER_REDUCED": Decimal("9.5"),
        "PARKING": Decimal("22"),
    },
    "ES": {  
        "BOOKS": Decimal("4"), "LIVRES": Decimal("4"),
        "FOOD": Decimal("4"), "ALIMENTATION": Decimal("4"),
        "MEDICINES": Decimal("4"), "MEDICAMENTS": Decimal("4"),
        "CLOTHING": Decimal("21"), "VETEMENTS": Decimal("21"),
        "SUPER_REDUCED": Decimal("4"),
        "PARKING": Decimal("21"),
    },
    "SE": {  
        "BOOKS": Decimal("6"), "LIVRES": Decimal("6"),
        "FOOD": Decimal("12"), "ALIMENTATION": Decimal("12"),
        "MEDICINES": Decimal("0"), "MEDICAMENTS": Decimal("0"),
        "CLOTHING": Decimal("25"), "VETEMENTS": Decimal("25"),
        "SUPER_REDUCED": Decimal("6"),
        "PARKING": Decimal("25"),
    },
    "MC": {  # Monaco : mêmes taux que la France (FR)
        "BOOKS": Decimal("5.5"), "LIVRES": Decimal("5.5"),
        "FOOD": Decimal("5.5"), "ALIMENTATION": Decimal("5.5"),
        "MEDICINES": Decimal("5.5"), "MEDICAMENTS": Decimal("5.5"),
        "CLOTHING": Decimal("20"), "VETEMENTS": Decimal("20"),
        "SUPER_REDUCED": Decimal("2.1"),  
        "PARKING": Decimal("20"),
    },
    "XI": {
        "BOOKS": Decimal("0"), "LIVRES": Decimal("0"),
        "FOOD": Decimal("5"), "ALIMENTATION": Decimal("5"),
        "MEDICINES": Decimal("0"), "MEDICAMENTS": Decimal("0"),
        "CLOTHING": Decimal("20"), "VETEMENTS": Decimal("20"),
        "SUPER_REDUCED": Decimal("0"),
        "PARKING": Decimal("20"),
    },
}


# ---------------------------------------------------------------------------
# Autoliquidation domestique (art. 194 directive 2006/112/CE)
# ---------------------------------------------------------------------------
# Restauré depuis rates0.py — supprimé par erreur lors de la réécriture des
# taux réduits. EE exclu intentionnellement (adoption partielle uniquement :
# biens d'occasion/déchets/matières premières forestières et métaux ferreux,
# art. 41 KMSS).
DOMESTIC_REVERSE_CHARGE_COUNTRIES: Set[str] = {
    "ES",  # art. 84 Ley IVA
    "IT",  # art. 17 DPR 633/72
    "PL",  # art. 17 uVAT
    "CZ",  # §92a zVAT
    "SK",  # §69 zVAT
    "HU",  # §142 aVAT
    "RO",  # art. 307 CF
    "BG",  # art. 82 ZDDS
    "HR",  # art. 75 ZPDV
    "LT",  # art. 96 PVMĮ
    "LV",  # art. 141 PVN
}


def vat_rate(
    country: str,
    product_category: str = "STANDARD",
    tx_date: Optional[date] = None,
) -> Decimal:
    """Retourne le taux de TVA (en %) pour un pays de l'UE.

    Restaurée depuis rates0.py — supprimée par erreur lors de la réécriture
    des taux réduits historisés. Délègue désormais à vat_rate_at_date(),
    qui gère l'historique par catégorie (et pas seulement STANDARD).

    Args:
        country: code ISO 3166-1 alpha-2 (ex: "EE", "FR").
        product_category: catégorie produit ("STANDARD", "BOOKS", "FOOD", ...).
        tx_date: date de la transaction. Si None, retourne le taux courant
            (2026) sans tenir compte de l'historique.

    Returns:
        Taux en Decimal (ex: Decimal("22") pour 22%).

    Raises:
        KeyError: si le pays est inconnu.
    """
    code = country.upper()
    category = product_category.strip().upper()

    if code not in STANDARD_VAT_RATES:
        raise KeyError(
            f"Aucun taux de TVA connu pour le pays '{country}'. "
            "S'agit-il d'un pays hors UE ?"
        )

    # Avec date : on délègue entièrement à vat_rate_at_date, qui sait
    # maintenant gérer l'historique par catégorie (pas seulement STANDARD).
    if tx_date is not None:
        return vat_rate_at_date(code, tx_date, category)

    # Sans date : taux courant (2026).
    if category != "STANDARD" and code in REDUCED_VAT_RATES:
        if category in REDUCED_VAT_RATES[code]:
            return REDUCED_VAT_RATES[code][category]
        logger.warning(
            "Catégorie '%s' inconnue pour %s — fallback taux standard.",
            category, code,
        )

    return STANDARD_VAT_RATES[code]