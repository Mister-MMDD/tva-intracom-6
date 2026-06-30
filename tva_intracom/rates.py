"""Taux de TVA par pays, catégories de produits et appartenance a l'UE.

Source unique de vérité pour toutes les données pays UE du projet :
  - EU_COUNTRIES, STANDARD_VAT_RATES, REDUCED_VAT_RATES (calcul moteur)
  - COUNTRY_NAMES, COUNTRY_ISO3 (noms et codes ISO 3166-1 alpha-3)
  - COUNTRY_FISCAL_META (déclarations fiscales locales — utilisé par app.py)
  - VAT_RATE_HISTORY (taux historiques depuis le 01/01/2024)

Taux actuels (STANDARD_VAT_RATES) : situation au 01/07/2025+.
Taux historiques (VAT_RATE_HISTORY) : corrections applicables pour les
fichiers couvrant la période 01/01/2024–présent.
Périmètre historique : à partir du 01/01/2024 (antérieur = hors scope).

Source : Commission européenne, Taxation and Customs Union,
         tableau des taux TVA 2024/2026.
Dernière vérification : juin 2026.

Changements couverts :
  EE : 20% → 22% au 01/01/2024 → 24% au 01/07/2025
  FI : 24% → 25.5% au 01/09/2024
  RO : 19% → 21% au 01/08/2025
  SK : 20% → 23% au 01/01/2025
"""

from __future__ import annotations

import logging
from datetime import date
from decimal import Decimal
from typing import Dict, List, NamedTuple, Optional, Set, Tuple

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Noms et codes pays — SOURCE UNIQUE (ne pas redéfinir dans app.py)
# ---------------------------------------------------------------------------

# Noms français des États membres (code ISO 3166-1 alpha-2 → nom FR).
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
}

# Codes ISO 3166-1 alpha-3 (pour les graphiques Plotly choroplèthe).
COUNTRY_ISO3: Dict[str, str] = {
    "AT": "AUT", "BE": "BEL", "BG": "BGR", "HR": "HRV", "CY": "CYP",
    "CZ": "CZE", "DK": "DNK", "EE": "EST", "FI": "FIN", "FR": "FRA",
    "DE": "DEU", "GR": "GRC", "HU": "HUN", "IE": "IRL", "IT": "ITA",
    "LV": "LVA", "LT": "LTU", "LU": "LUX", "MT": "MLT", "NL": "NLD",
    "PL": "POL", "PT": "PRT", "RO": "ROU", "SK": "SVK", "SI": "SVN",
    "ES": "ESP", "SE": "SWE",
}

# Métadonnées fiscales locales par pays.
# Tuple : (nom_déclaration_locale, libellé_base, libellé_taxe,
#           taux_standard_affiché, taux_réduit_principal_affiché)
# Les taux affichés (str) sont dérivés de STANDARD_VAT_RATES et
# REDUCED_VAT_RATES — ne pas les modifier indépendamment.
COUNTRY_FISCAL_META: Dict[str, Tuple[str, str, str, str, str]] = {
    "AT": ("Umsatzsteuervoranmeldung (UVA)",    "Bemessungsgrundlage", "Umsatzsteuer", "20%",   "10%"),
    "BE": ("Declaration TVA / BTW-aangifte",    "Base imposable",      "TVA / BTW",   "21%",   "6%"),
    "BG": ("Spravka-deklaraciya po ZDDS",       "Danachna osnova",     "DDS",         "20%",   "9%"),
    "HR": ("Prijava PDV-a",                     "Porezna osnovica",    "PDV",         "25%",   "5%"),
    "CY": ("FPA Dilosi",                        "Forologitea axia",    "FPA",         "19%",   "5%"),
    "CZ": ("Priznani k DPH",                    "Zaklad dane",         "DPH",         "21%",   "12%"),
    "DK": ("Momsangivelse",                     "Afgiftsgrundlag",     "Moms",        "25%",   "—"),
    "EE": ("Kaibemaksudeklaratsioon (KMD)",     "Maksustatav kaive",   "Kaibemaks",  "24%",   "9%"),   # 24% depuis juil. 2025
    "FI": ("Arvonlisaveroilmoitus",             "Veron peruste",       "ALV",         "25.5%", "10%"),
    "FR": ("Declaration TVA (CA3 / OSS)",       "Base HT",             "TVA",         "20%",   "5.5%"),
    "DE": ("Umsatzsteuer-Voranmeldung (UStVA)", "Bemessungsgrundlage", "Umsatzsteuer","19%",   "7%"),
    "GR": ("Dilosi FPA",                        "Forologitea axia",    "FPA",         "24%",   "6%"),
    "HU": ("AFA-bevalles",                      "Adoalap",             "AFA",         "27%",   "5%"),
    "IE": ("VAT Return (VAT3)",                 "Taxable amount",      "VAT",         "23%",   "0%"),   # taux zéro irlandais
    "IT": ("Dichiarazione IVA / Liquidazione",  "Imponibile",          "IVA",         "22%",   "4%"),
    "LV": ("PVN deklaracija",                   "Nodokla baze",        "PVN",         "21%",   "5%"),
    "LT": ("PVM deklaracija (FR0600)",          "Apmokestinamoji verte","PVM",        "21%",   "5%"),
    "LU": ("Declaration TVA trimestrielle",     "Base imposable",      "TVA",         "17%",   "3%"),   # 17% depuis jan. 2023
    "MT": ("VAT Return",                        "Taxable value",       "VAT",         "18%",   "0%"),
    "NL": ("BTW-aangifte",                      "Belaste omzet",       "BTW",         "21%",   "9%"),
    "PL": ("Deklaracja JPK_V7M / JPK_V7K",     "Podstawa opodatkowania","VAT",       "23%",   "5%"),
    "PT": ("Declaracao Periodica de IVA",       "Base tributavel",     "IVA",         "23%",   "6%"),
    "RO": ("Decont de TVA (D300)",              "Baza de impozitare",  "TVA",         "21%",   "9%"),   # 21% depuis aout. 2025
    "SK": ("Danove priznanie k DPH",            "Zaklad dane",         "DPH",         "23%",   "5%"),   # 23% depuis jan. 2025
    "SI": ("Obracun DDV (DDV-O)",               "Davcna osnova",       "DDV",         "22%",   "9.5%"),
    "ES": ("Modelo 303 / Declaracion IVA",      "Base imponible",      "IVA",         "21%",   "4%"),
    "SE": ("Mervardesskattdeklaration (SKV 4700)","Beskattningsunderlag","Moms",      "25%",   "6%"),
}

# ---------------------------------------------------------------------------
# Etats membres de l'Union europeenne (codes ISO 3166-1 alpha-2).
# ---------------------------------------------------------------------------
EU_COUNTRIES: Set[str] = set(COUNTRY_NAMES.keys())

# ---------------------------------------------------------------------------
# Territoires exclus du territoire fiscal de l'UE (TVA)
# ---------------------------------------------------------------------------
# Ces territoires font partie d'un État membre sur le plan politique et douanier,
# mais sont EXCLUS du champ d'application de la directive TVA 2006/112/CE
# (art. 6). Une vente vers ces territoires est traitée comme une EXPORTATION
# (exonérée de TVA), et NON comme une vente OSS ou domestique.
#
# Implémentation : détection par code postal (champ ARRIVAL_POST_CODE du rapport
# Amazon). Les codes pays restent "ES", "DE", "GR", etc. dans les données Amazon.
#
# Sources :
#   - Directive 2006/112/CE, art. 6 (territoires exclus)
#   - Règlement UE 2020/194 (procédures OSS)
#   - Commission EU, "Territorial status of EU countries and certain territories"
# Dernière vérification : juin 2026.
#
# Structure : {code_pays: {"prefixes": [...], "ranges": [(min, max), ...]}}
#   prefixes : débuts de codes postaux (str, comparaison startswith)
#   ranges   : plages de codes postaux numériques (int, bornes incluses)
#
# PAYS CONCERNÉS :
#   ES — Canaries (35xxx, 38xxx) ; Ceuta (51xxx) ; Melilla (52xxx)
#   DE — Heligoland (27498) ; Büsingen (78266)
#   GR — Mont Athos (63086)
#   IT — Livigno (23030, complément par commune) ; Campione d'Italia (22060)
#   FI — Îles Åland (22xxx) [hors TVA UE, mais dans l'UE douanière]
#
# NOTE Åland : en pratique Amazon livre aux Åland avec ARRIVAL_COUNTRY="FI".
# Ces ventes doivent être exonérées. Elles représentent un volume marginal.
#
# TERRITOIRES NON LISTÉS (hors scope e-commerce Amazon) :
#   FR — DOM/COM (GP, MQ, GF, RE, PM, MF, BL, NC, PF, WF, TF, YT)
#        → ARRIVAL_COUNTRY sera "GP", "MQ", etc. (codes séparés), pas "FR".
#        → Déjà traités correctement par is_eu() → False → EXPORT.

NON_FISCAL_EU_POSTCODES: Dict[str, Dict] = {
    "ES": {
        "prefixes": ["35", "38", "51", "52"],
        # 35xxx / 38xxx = Canaries ; 51xxx = Ceuta ; 52xxx = Melilla
    },
    "FR": {
        "prefixes": ["97", "98"],
        # 97xxx = DOM (Guadeloupe, Martinique, Guyane, Réunion, Mayotte)
        # 98xxx = COM (Saint-Pierre-et-Miquelon, Saint-Martin, Saint-Barthélemy…)
        # Note : en pratique Amazon renseigne souvent un code pays séparé
        # (GP, MQ, GF, RE, YT…) pour les DOM/COM → déjà hors EU_COUNTRIES.
        # Ce filtre couvre le cas où Amazon utilise "FR" avec un CP 97/98.
    },
    "DE": {
        "prefixes": ["27498", "78266"],
        # 27498 = Heligoland ; 78266 = Büsingen am Hochrhein
    },
    "GR": {
        "prefixes": ["63086", "63087", "63088"],
        # Mont Athos (Agion Oros) — trois codes postaux connus
    },
    "IT": {
        "prefixes": ["23030", "22060"],
        # 23030 = Livigno ; 22060 = Campione d'Italia
        # Note : ces communes partagent leur CP avec d'autres localités.
        # La détection n'est pas parfaite sans la commune — volume négligeable.
    },
    "FI": {
        "prefixes": ["22"],
        # Îles Åland (220xx–228xx)
    },
}


def is_non_fiscal_eu(country: str, post_code: str | None) -> bool:
    """Retourne True si le code postal désigne un territoire exclu du périmètre
    fiscal TVA de l'UE (art. 6 directive 2006/112/CE), même si le code pays
    appartient à l'UE politique.

    Usage : appelée dans l'adaptateur Amazon lors du parsing de chaque ligne,
    avant la construction de l'objet Sale. Si True → traiter comme EXPORT.

    Args:
        country:   code ISO 3166-1 alpha-2 du pays de destination (ex: "ES").
        post_code: code postal de destination tel que fourni par Amazon
                   (champ ARRIVAL_POST_CODE). Peut être None ou vide.

    Returns:
        True  → territoire hors UE fiscale → traiter comme exportation.
        False → territoire dans l'UE fiscale (cas normal).

    Examples:
        >>> is_non_fiscal_eu("ES", "35001")   # Las Palmas, Canaries
        True
        >>> is_non_fiscal_eu("ES", "28001")   # Madrid
        False
        >>> is_non_fiscal_eu("DE", "27498")   # Heligoland
        True
        >>> is_non_fiscal_eu("FR", "75001")   # Paris
        False
    """
    code = country.upper()
    if code not in NON_FISCAL_EU_POSTCODES:
        return False
    if not post_code:
        # Pas de code postal → on ne peut pas trancher → cas normal (conservateur)
        return False

    pc = post_code.strip().replace(" ", "").upper()
    rule = NON_FISCAL_EU_POSTCODES[code]

    for prefix in rule.get("prefixes", []):
        if pc.startswith(prefix):
            return True

    return False


def is_eu(country: str) -> bool:
    """Retourne True si le code pays appartient a l'UE."""
    return country.upper() in EU_COUNTRIES


def is_fiscal_eu(country: str, post_code: str | None = None) -> bool:
    """Retourne True si la destination est dans le périmètre fiscal TVA de l'UE.

    Combine is_eu() (appartenance à l'UE politique) et is_non_fiscal_eu()
    (exclusions art. 6 directive TVA). C'est cette fonction qu'il faut utiliser
    pour décider du régime TVA applicable (OSS, domestique, exportation).

    Args:
        country:   code ISO 3166-1 alpha-2.
        post_code: code postal de destination (ARRIVAL_POST_CODE Amazon).
                   Si None ou vide, seule l'appartenance politique est vérifiée
                   (comportement conservateur : pas de faux positif EXPORT).

    Returns:
        True  → destination dans l'UE fiscale → appliquer TVA/OSS normalement.
        False → hors UE fiscale → traiter comme exportation (TVA=0).

    Examples:
        >>> is_fiscal_eu("ES", "35001")   # Canaries → hors UE fiscale
        False
        >>> is_fiscal_eu("ES", "28001")   # Madrid → UE fiscale
        True
        >>> is_fiscal_eu("GB", "SW1A")    # Royaume-Uni → hors UE
        False
        >>> is_fiscal_eu("ES")            # sans CP → conservateur → True
        True
    """
    if not is_eu(country):
        return False
    if post_code and is_non_fiscal_eu(country, post_code):
        return False
    return True


# Taux standard de TVA, exprimes en pourcentage.
# CORRECTIONS juin 2026 :
#   EE : 24% (20%→22% au 01/01/2024 ; 22%→24% au 01/07/2025)
#   RO : 21% (art. 291 CF roumain modifié, en vigueur depuis jan. 2024)
#   LU : 17% (hausse 16%→17% au 01/01/2023, Règl. grand-ducal 2022)
STANDARD_VAT_RATES: Dict[str, Decimal] = {
    "AT": Decimal("20"),
    "BE": Decimal("21"),
    "BG": Decimal("20"),
    "HR": Decimal("25"),
    "CY": Decimal("19"),
    "CZ": Decimal("21"),
    "DK": Decimal("25"),
    "EE": Decimal("24"),   # 24% depuis 01/07/2025 (20%→22% jan.2024, 22%→24% juil.2025)
    "FI": Decimal("25.5"),
    "FR": Decimal("20"),
    "DE": Decimal("19"),
    "GR": Decimal("24"),
    "HU": Decimal("27"),
    "IE": Decimal("23"),
    "IT": Decimal("22"),
    "LV": Decimal("21"),
    "LT": Decimal("21"),
    "LU": Decimal("17"),   # 17% depuis 01/01/2023 (était 16%)
    "MT": Decimal("18"),
    "NL": Decimal("21"),
    "PL": Decimal("23"),
    "PT": Decimal("23"),
    "RO": Decimal("21"),   # 21% depuis 01/08/2025 (était 19%)
    "SK": Decimal("23"),   # 23% depuis 01/01/2025 (était 20%)
    "SI": Decimal("22"),
    "ES": Decimal("21"),
    "SE": Decimal("25"),
}

# ---------------------------------------------------------------------------
# Historique des taux standard — périmètre 01/01/2024 → présent
# ---------------------------------------------------------------------------
# Structure : liste de (pays, date_debut, date_fin_incluse_ou_None, taux)
# date_fin = None signifie "taux en vigueur jusqu'à aujourd'hui (et au-delà)".
# Règle d'application : un taux est valide pour date_debut <= tx_date <= date_fin.
# Les pays absents de cette table ont un taux stable depuis au moins 2024
# → on retombe sur STANDARD_VAT_RATES directement.
#
# STRUCTURE PRÉVUE POUR LES TAUX RÉDUITS :
# Quand les taux réduits historiques seront ajoutés, utiliser le même
# mécanisme via vat_rate_at_date(country, tx_date, category="FOOD") etc.
# Il suffira d'étendre _VatPeriod avec un champ `category` et d'enrichir
# VAT_RATE_HISTORY avec les périodes concernées.

class _VatPeriod(NamedTuple):
    country: str
    date_from: date        # premier jour d'application (inclus)
    date_to: Optional[date]  # dernier jour (inclus) ; None = toujours valide
    rate: Decimal


VAT_RATE_HISTORY: List[_VatPeriod] = [
    # --- Estonie (EE) ---
    # 20% avant 2024 (hors périmètre — non listé)
    _VatPeriod("EE", date(2024, 1, 1),  date(2025, 6, 30),  Decimal("22")),
    _VatPeriod("EE", date(2025, 7, 1),  None,               Decimal("24")),

    # --- Finlande (FI) ---
    _VatPeriod("FI", date(2024, 1, 1),  date(2024, 8, 31),  Decimal("24")),
    _VatPeriod("FI", date(2024, 9, 1),  None,               Decimal("25.5")),

    # --- Roumanie (RO) ---
    _VatPeriod("RO", date(2024, 1, 1),  date(2025, 7, 31),  Decimal("19")),
    _VatPeriod("RO", date(2025, 8, 1),  None,               Decimal("21")),

    # --- Slovaquie (SK) ---
    _VatPeriod("SK", date(2024, 1, 1),  date(2024, 12, 31), Decimal("20")),
    _VatPeriod("SK", date(2025, 1, 1),  None,               Decimal("23")),
]

# Index précalculé pour accélération : pays → liste de périodes (triées par date_from)
_HISTORY_INDEX: Dict[str, List[_VatPeriod]] = {}
for _p in VAT_RATE_HISTORY:
    _HISTORY_INDEX.setdefault(_p.country, []).append(_p)


def vat_rate_at_date(
    country: str,
    tx_date: date,
    product_category: str = "STANDARD",
) -> Decimal:
    """Retourne le taux TVA standard applicable à une date de transaction donnée.

    Cherche d'abord dans VAT_RATE_HISTORY (taux historiques).
    Si le pays n'a pas de période historique, retourne STANDARD_VAT_RATES
    (taux stable depuis au moins 01/01/2024).

    Args:
        country: code ISO 3166-1 alpha-2 (ex: "EE", "FR").
        tx_date: date de la transaction (TRANSACTION_COMPLETE_DATE).
        product_category: réservé pour les taux réduits futurs ("STANDARD"
            est le seul cas géré actuellement — extension prévue).

    Returns:
        Taux en Decimal (ex: Decimal("22") pour 22%).

    Raises:
        KeyError: si le pays est inconnu (ni dans l'historique ni dans
            STANDARD_VAT_RATES).
    """
    # NOTE ÉVOLUTION : quand les taux réduits historiques seront ajoutés,
    # filtrer ici sur `product_category` en plus du pays et de la date.
    # Pour l'instant on ignore category (taux standard uniquement).

    code = country.upper()
    periods = _HISTORY_INDEX.get(code)

    if periods:
        for period in periods:
            if period.date_from <= tx_date:
                if period.date_to is None or tx_date <= period.date_to:
                    return period.rate
        # Aucune période trouvée pour cette date (ex: avant 01/01/2024)
        # → on retombe sur STANDARD_VAT_RATES comme filet de sécurité
        logger.debug(
            "vat_rate_at_date : aucune période historique pour %s au %s "
            "— utilisation du taux standard courant.",
            code, tx_date,
        )

    if code not in STANDARD_VAT_RATES:
        raise KeyError(f"Pays inconnu : '{code}'. Vérifiez le code ISO 3166-1 alpha-2.")

    return STANDARD_VAT_RATES[code]


def has_rate_changed(country: str) -> bool:
    """Retourne True si le pays a eu un changement de taux dans le périmètre historique."""
    return country.upper() in _HISTORY_INDEX


def rate_periods_for_country(country: str) -> List[_VatPeriod]:
    """Retourne les périodes historiques d'un pays (liste vide si taux stable)."""
    return _HISTORY_INDEX.get(country.upper(), [])


# Mapping complet des taux réduits par pays et catégories de produits.
# Catégories standardisées :
#   STANDARD   → taux normal (fallback)
#   BOOKS      → Livres papier / numériques
#   FOOD       → Alimentation générale
#   CLOTHING   → Vêtements
#   MEDICINES  → Médicaments / santé
#   SUPER_REDUCED → Taux super-réduit (ES 4%, IT 4%, IE 0%…) — biens de 1ère nécessité
#   PARKING    → Taux "parking" (AT, BE, IE…) — biens soumis à un taux intermédiaire
#               historique non prévu par la directive mais toléré.
# Les clés FR/EN sont maintenues pour la compatibilité avec les données existantes.
REDUCED_VAT_RATES: Dict[str, Dict[str, Decimal]] = {
    "AT": {  # Autriche — super-réduit: 10% alimentation/culture ; parking: 13%
        "BOOKS": Decimal("10"), "LIVRES": Decimal("10"),
        "FOOD": Decimal("10"), "ALIMENTATION": Decimal("10"),
        "MEDICINES": Decimal("10"), "MEDICAMENTS": Decimal("10"),
        "CLOTHING": Decimal("20"), "VETEMENTS": Decimal("20"),
        "SUPER_REDUCED": Decimal("10"),  # même niveau que réduit en AT
        "PARKING": Decimal("13"),        # taux intermédiaire AT (vins, animaux…)
    },
    "BE": {  # Belgique — super-réduit: 6% ; parking: 12%
        "BOOKS": Decimal("6"), "LIVRES": Decimal("6"),
        "FOOD": Decimal("6"), "ALIMENTATION": Decimal("6"),
        "MEDICINES": Decimal("6"), "MEDICAMENTS": Decimal("6"),
        "CLOTHING": Decimal("21"), "VETEMENTS": Decimal("21"),
        "SUPER_REDUCED": Decimal("6"),
        "PARKING": Decimal("12"),        # taux parking BE (beurre, margarine…)
    },
    "BG": {  # Bulgarie — pas de taux super-réduit ni parking
        # FOOD : taux réduit 9% en vigueur depuis août 2022 (décret CM BG, art. 66 ZDDS modifié)
        "BOOKS": Decimal("9"), "LIVRES": Decimal("9"),
        "FOOD": Decimal("9"), "ALIMENTATION": Decimal("9"),
        "MEDICINES": Decimal("20"), "MEDICAMENTS": Decimal("20"),
        "CLOTHING": Decimal("20"), "VETEMENTS": Decimal("20"),
        "SUPER_REDUCED": Decimal("9"),
        "PARKING": Decimal("20"),
    },
    "HR": {  # Croatie — super-réduit: 5% ; pas de parking
        "BOOKS": Decimal("5"), "LIVRES": Decimal("5"),
        "FOOD": Decimal("5"), "ALIMENTATION": Decimal("5"),
        "MEDICINES": Decimal("5"), "MEDICAMENTS": Decimal("5"),
        "CLOTHING": Decimal("25"), "VETEMENTS": Decimal("25"),
        "SUPER_REDUCED": Decimal("5"),
        "PARKING": Decimal("25"),
    },
    "CY": {  # Chypre — super-réduit: 5% ; pas de parking
        "BOOKS": Decimal("5"), "LIVRES": Decimal("5"),
        "FOOD": Decimal("5"), "ALIMENTATION": Decimal("5"),
        "MEDICINES": Decimal("5"), "MEDICAMENTS": Decimal("5"),
        "CLOTHING": Decimal("19"), "VETEMENTS": Decimal("19"),
        "SUPER_REDUCED": Decimal("5"),
        "PARKING": Decimal("19"),
    },
    "CZ": {  # République Tchèque — livres exonérés ; pas de super-réduit officiel
        "BOOKS": Decimal("0"), "LIVRES": Decimal("0"),
        "FOOD": Decimal("12"), "ALIMENTATION": Decimal("12"),
        "MEDICINES": Decimal("12"), "MEDICAMENTS": Decimal("12"),
        "CLOTHING": Decimal("21"), "VETEMENTS": Decimal("21"),
        "SUPER_REDUCED": Decimal("0"),
        "PARKING": Decimal("21"),
    },
    "DK": {  # Danemark — taux unique, pas de réduit
        "BOOKS": Decimal("25"), "LIVRES": Decimal("25"),
        "FOOD": Decimal("25"), "ALIMENTATION": Decimal("25"),
        "MEDICINES": Decimal("25"), "MEDICAMENTS": Decimal("25"),
        "CLOTHING": Decimal("25"), "VETEMENTS": Decimal("25"),
        "SUPER_REDUCED": Decimal("25"),
        "PARKING": Decimal("25"),
    },
    "EE": {  # Estonie — pas de super-réduit ni parking
        "BOOKS": Decimal("9"), "LIVRES": Decimal("9"),
        "FOOD": Decimal("24"), "ALIMENTATION": Decimal("24"),
        "MEDICINES": Decimal("9"), "MEDICAMENTS": Decimal("9"),
        "CLOTHING": Decimal("24"), "VETEMENTS": Decimal("24"),
        "SUPER_REDUCED": Decimal("9"),
        "PARKING": Decimal("24"),
    },
    "FI": {  # Finlande — taux alimentaire : 14% (depuis sept. 2024, anciennement 14% puis 13%)
        # Source : Verohallinto (administration fiscale FI), réforme budgétaire 2024
        "BOOKS": Decimal("10"), "LIVRES": Decimal("10"),
        "FOOD": Decimal("14"), "ALIMENTATION": Decimal("14"),
        "MEDICINES": Decimal("10"), "MEDICAMENTS": Decimal("10"),
        "CLOTHING": Decimal("25.5"), "VETEMENTS": Decimal("25.5"),
        "SUPER_REDUCED": Decimal("10"),
        "PARKING": Decimal("25.5"),
    },
    "FR": {  # France — super-réduit: 2.1% (médicaments remboursables, presse)
        "BOOKS": Decimal("5.5"), "LIVRES": Decimal("5.5"),
        "FOOD": Decimal("5.5"), "ALIMENTATION": Decimal("5.5"),
        "MEDICINES": Decimal("5.5"), "MEDICAMENTS": Decimal("5.5"),
        "CLOTHING": Decimal("20"), "VETEMENTS": Decimal("20"),
        "SUPER_REDUCED": Decimal("2.1"),  # médicaments remboursables SS, presse
        "PARKING": Decimal("20"),          # pas de taux parking en France
    },
    "DE": {  # Allemagne — pas de super-réduit ni parking
        "BOOKS": Decimal("7"), "LIVRES": Decimal("7"),
        "FOOD": Decimal("7"), "ALIMENTATION": Decimal("7"),
        "MEDICINES": Decimal("19"), "MEDICAMENTS": Decimal("19"),
        "CLOTHING": Decimal("19"), "VETEMENTS": Decimal("19"),
        "SUPER_REDUCED": Decimal("7"),
        "PARKING": Decimal("19"),
    },
    "GR": {  # Grèce — super-réduit: 6% (médicaments, livres) ; pas de parking
        "BOOKS": Decimal("6"), "LIVRES": Decimal("6"),
        "FOOD": Decimal("13"), "ALIMENTATION": Decimal("13"),
        "MEDICINES": Decimal("6"), "MEDICAMENTS": Decimal("6"),
        "CLOTHING": Decimal("24"), "VETEMENTS": Decimal("24"),
        "SUPER_REDUCED": Decimal("6"),
        "PARKING": Decimal("24"),
    },
    "HU": {  # Hongrie — super-réduit: 5% (livres, médicaments) ; pas de parking
        "BOOKS": Decimal("5"), "LIVRES": Decimal("5"),
        "FOOD": Decimal("18"), "ALIMENTATION": Decimal("18"),
        "MEDICINES": Decimal("5"), "MEDICAMENTS": Decimal("5"),
        "CLOTHING": Decimal("27"), "VETEMENTS": Decimal("27"),
        "SUPER_REDUCED": Decimal("5"),
        "PARKING": Decimal("27"),
    },
    "IE": {  # Irlande — super-réduit: 4.8% (bétail) ; parking: 13.5%
        # ATTENTION : le taux zéro vêtements s'applique UNIQUEMENT aux vêtements/chaussures
        # pour enfants de moins de 11 ans (taille S/28). Vêtements adultes → 23% (standard).
        # Sans catégorie CLOTHING_CHILD / CLOTHING_ADULT dans le catalogue ASIN,
        # on applique 0% par défaut (favorable au vendeur, à documenter avec le cabinet).
        # Source : Revenue.ie, Schedule 2 VATCA 2010
        "BOOKS": Decimal("0"), "LIVRES": Decimal("0"),
        "FOOD": Decimal("0"), "ALIMENTATION": Decimal("0"),
        "MEDICINES": Decimal("0"), "MEDICAMENTS": Decimal("0"),
        "CLOTHING": Decimal("0"), "VETEMENTS": Decimal("0"),   # enfants < 11 ans uniquement
        "SUPER_REDUCED": Decimal("4.8"),   # bétail, semences agricoles
        "PARKING": Decimal("13.5"),         # combustibles, services de construction…
    },
    "IT": {  # Italie — super-réduit: 4% (alimentation de base, livres, médicaments)
        "BOOKS": Decimal("4"), "LIVRES": Decimal("4"),
        "FOOD": Decimal("4"), "ALIMENTATION": Decimal("4"),
        "MEDICINES": Decimal("10"), "MEDICAMENTS": Decimal("10"),
        "CLOTHING": Decimal("22"), "VETEMENTS": Decimal("22"),
        "SUPER_REDUCED": Decimal("4"),   # biens de 1ère nécessité : eau, lait, pain…
        "PARKING": Decimal("22"),         # pas de taux parking en IT
    },
    "LV": {  # Lettonie — pas de super-réduit ni parking
        # FOOD : taux réduit 5% sur produits alimentaires de base (art. 42 PVN likums, depuis 2018)
        "BOOKS": Decimal("5"), "LIVRES": Decimal("5"),
        "FOOD": Decimal("5"), "ALIMENTATION": Decimal("5"),
        "MEDICINES": Decimal("12"), "MEDICAMENTS": Decimal("12"),
        "CLOTHING": Decimal("21"), "VETEMENTS": Decimal("21"),
        "SUPER_REDUCED": Decimal("5"),
        "PARKING": Decimal("21"),
    },
    "LT": {  # Lituanie — pas de super-réduit ni parking
        # FOOD : taux réduit 5% sur produits alimentaires de base (art. 19(1) PVMį, en vigueur)
        "BOOKS": Decimal("9"), "LIVRES": Decimal("9"),
        "FOOD": Decimal("5"), "ALIMENTATION": Decimal("5"),
        "MEDICINES": Decimal("5"), "MEDICAMENTS": Decimal("5"),
        "CLOTHING": Decimal("21"), "VETEMENTS": Decimal("21"),
        "SUPER_REDUCED": Decimal("5"),
        "PARKING": Decimal("21"),
    },
    "LU": {  # Luxembourg — super-réduit: 3% ; parking: 14% ; taux standard: 17% (depuis jan. 2023)
        # Source : administration de l'Enregistrement, des Domaines et de la TVA (AED) LU
        "BOOKS": Decimal("3"), "LIVRES": Decimal("3"),
        "FOOD": Decimal("3"), "ALIMENTATION": Decimal("3"),
        "MEDICINES": Decimal("3"), "MEDICAMENTS": Decimal("3"),
        "CLOTHING": Decimal("17"), "VETEMENTS": Decimal("17"),
        "SUPER_REDUCED": Decimal("3"),
        "PARKING": Decimal("14"),   # vins, carburants, imprimerie…
    },
    "MT": {  # Malte — pas de super-réduit ni parking
        "BOOKS": Decimal("5"), "LIVRES": Decimal("5"),
        "FOOD": Decimal("0"), "ALIMENTATION": Decimal("0"),
        "MEDICINES": Decimal("0"), "MEDICAMENTS": Decimal("0"),
        "CLOTHING": Decimal("18"), "VETEMENTS": Decimal("18"),
        "SUPER_REDUCED": Decimal("0"),
        "PARKING": Decimal("18"),
    },
    "NL": {  # Pays-Bas — pas de super-réduit ni parking
        "BOOKS": Decimal("9"), "LIVRES": Decimal("9"),
        "FOOD": Decimal("9"), "ALIMENTATION": Decimal("9"),
        "MEDICINES": Decimal("9"), "MEDICAMENTS": Decimal("9"),
        "CLOTHING": Decimal("21"), "VETEMENTS": Decimal("21"),
        "SUPER_REDUCED": Decimal("9"),
        "PARKING": Decimal("21"),
    },
    "PL": {  # Pologne — pas de super-réduit ni parking
        "BOOKS": Decimal("5"), "LIVRES": Decimal("5"),
        "FOOD": Decimal("5"), "ALIMENTATION": Decimal("5"),
        "MEDICINES": Decimal("8"), "MEDICAMENTS": Decimal("8"),
        "CLOTHING": Decimal("23"), "VETEMENTS": Decimal("23"),
        "SUPER_REDUCED": Decimal("5"),
        "PARKING": Decimal("23"),
    },
    "PT": {  # Portugal — super-réduit: 6% ; parking: 13%
        "BOOKS": Decimal("6"), "LIVRES": Decimal("6"),
        "FOOD": Decimal("6"), "ALIMENTATION": Decimal("6"),
        "MEDICINES": Decimal("6"), "MEDICAMENTS": Decimal("6"),
        "CLOTHING": Decimal("23"), "VETEMENTS": Decimal("23"),
        "SUPER_REDUCED": Decimal("6"),
        "PARKING": Decimal("13"),   # vins, huile d'olive, combustibles…
    },
    "RO": {  # Roumanie — taux réduit alimentation/livres/médicaments : 9% (depuis 2024)
        # Source : Commission EU, tableau des taux TVA 2024 ; art. 291 CF roumain modifié
        "BOOKS": Decimal("9"), "LIVRES": Decimal("9"),
        "FOOD": Decimal("9"), "ALIMENTATION": Decimal("9"),
        "MEDICINES": Decimal("9"), "MEDICAMENTS": Decimal("9"),
        "CLOTHING": Decimal("21"), "VETEMENTS": Decimal("21"),
        "SUPER_REDUCED": Decimal("9"),
        "PARKING": Decimal("21"),
    },
    "SK": {  # Slovaquie — pas de super-réduit ni parking
        "BOOKS": Decimal("5"), "LIVRES": Decimal("5"),
        "FOOD": Decimal("5"), "ALIMENTATION": Decimal("5"),
        "MEDICINES": Decimal("5"), "MEDICAMENTS": Decimal("5"),
        "CLOTHING": Decimal("23"), "VETEMENTS": Decimal("23"),
        "SUPER_REDUCED": Decimal("5"),
        "PARKING": Decimal("23"),
    },
    "SI": {  # Slovénie — pas de super-réduit ni parking
        "BOOKS": Decimal("9.5"), "LIVRES": Decimal("9.5"),
        "FOOD": Decimal("9.5"), "ALIMENTATION": Decimal("9.5"),
        "MEDICINES": Decimal("9.5"), "MEDICAMENTS": Decimal("9.5"),
        "CLOTHING": Decimal("22"), "VETEMENTS": Decimal("22"),
        "SUPER_REDUCED": Decimal("9.5"),
        "PARKING": Decimal("22"),
    },
    "ES": {  # Espagne — super-réduit: 4% (alimentation de base, médicaments, livres)
        "BOOKS": Decimal("4"), "LIVRES": Decimal("4"),
        "FOOD": Decimal("4"), "ALIMENTATION": Decimal("4"),
        "MEDICINES": Decimal("4"), "MEDICAMENTS": Decimal("4"),
        "CLOTHING": Decimal("21"), "VETEMENTS": Decimal("21"),
        "SUPER_REDUCED": Decimal("4"),   # pain, lait, œufs, fromages, fruits, légumes…
        "PARKING": Decimal("21"),         # pas de taux parking en ES
    },
    "SE": {  # Suède — pas de super-réduit ni parking
        "BOOKS": Decimal("6"), "LIVRES": Decimal("6"),
        "FOOD": Decimal("12"), "ALIMENTATION": Decimal("12"),
        "MEDICINES": Decimal("0"), "MEDICAMENTS": Decimal("0"),
        "CLOTHING": Decimal("25"), "VETEMENTS": Decimal("25"),
        "SUPER_REDUCED": Decimal("0"),
        "PARKING": Decimal("25"),
    }
}


# Pays ayant adopté l'autoliquidation nationale (art. 194 directive 2006/112/CE)
# pour les livraisons de biens par un vendeur étranger à un assujetti local.
# Sources : Commission EU, VAT in the Digital Age 2024 ; confirmations cabinets comptables.
# Dernière vérification : juin 2026.
#
# CRITÈRE D'INCLUSION : art. 194 adopté de manière GÉNÉRALE pour les livraisons de biens
# entre assujettis. Les adoptions partielles (sectorielles) sont EXCLUES pour éviter
# d'appliquer à tort le reverse charge sur des ventes e-commerce hors secteur couvert.
#
# PAYS EXCLUS intentionnellement malgré adoption partielle :
#   EE (Estonie) : art. 41 KMSS — uniquement biens d'occasion, déchets, matières premières
#                  forestières et métaux ferreux. Ne couvre PAS les ventes e-commerce courantes.
#                  → Vendeur doit collecter la TVA EE (22%) ou s'immatriculer localement.
#
# Pays N'AYANT PAS adopté art. 194 → vendeur collecte la TVA B2B cross-border :
# DE, AT, BE, NL, FR, IE, DK, FI, SE, LU, PT, MT, CY, GR, SI, EE
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
    # EE EXCLU : adoption partielle sectorielle uniquement (biens d'occasion/déchets/forêt)
}

def is_eu(country: str) -> bool:
    """Retourne True si le code pays appartient a l'UE."""
    return country.upper() in EU_COUNTRIES


def vat_rate(
    country: str,
    product_category: str = "STANDARD",
    tx_date: Optional[date] = None,
) -> Decimal:
    """Retourne le taux de TVA (en %) pour un pays de l'UE.

    Si tx_date est fourni et que le pays a un historique de taux (VAT_RATE_HISTORY),
    retourne le taux en vigueur à cette date plutôt que le taux actuel.
    Utile pour les fichiers Amazon couvrant plusieurs années.

    Args:
        country: code ISO 3166-1 alpha-2 (ex: "EE", "FR").
        product_category: catégorie produit. Actuellement seul "STANDARD"
            tient compte de l'historique de date. Extension prévue pour
            les taux réduits.
        tx_date: date de la transaction. Si None, retourne le taux actuel.

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

    # Taux réduits : pas encore d'historique de date — taux courant uniquement.
    # NOTE ÉVOLUTION : quand les taux réduits historiques seront ajoutés,
    # passer tx_date à vat_rate_at_date() avec le filtre category.
    if category != "STANDARD" and code in REDUCED_VAT_RATES:
        if category in REDUCED_VAT_RATES[code]:
            return REDUCED_VAT_RATES[code][category]
        logger.warning(
            "Catégorie '%s' inconnue pour %s — fallback taux standard.",
            category, code,
        )

    # Taux standard : avec correction historique si date fournie.
    if tx_date is not None and has_rate_changed(code):
        return vat_rate_at_date(code, tx_date)

    return STANDARD_VAT_RATES[code]