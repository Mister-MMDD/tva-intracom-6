"""Détection du format Amazon et normalisation des dates.

Fonctions pures sans état — aucune dépendance vers les autres sous-modules.
"""

from __future__ import annotations

import csv
import logging
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

logger = logging.getLogger(__name__)


def normalize_header(h: str | None) -> str:
    """Normalise un nom de colonne CSV en snake_case minuscule."""
    if h is None:
        return ""
    return h.strip().lower().replace(" ", "_").replace("-", "_")


def detect_separator(line: str) -> str:
    """Détecte le séparateur CSV dominant (tab / point-virgule / virgule).

    Utilise d'abord `csv.Sniffer` (qui tient compte des guillemets — un
    champ tel que `"tax_label,2026"` ne fausse pas la détection car le
    contenu entre guillemets n'est pas compté). Si le Sniffer échoue à
    déterminer un dialecte (ligne trop courte, ambiguë, ou un seul champ),
    on retombe sur le comptage brut de caractères — plus simple mais
    sensible aux guillemets, d'où le Sniffer en première intention.
    """
    try:
        dialect = csv.Sniffer().sniff(line, delimiters="\t;,")
        if dialect.delimiter in ("\t", ";", ","):
            return dialect.delimiter
    except csv.Error:
        pass

    # Fallback : comptage brut (comportement historique), utilisé seulement
    # si le Sniffer n'a pas pu conclure.
    counts = {"\t": line.count("\t"), ";": line.count(";"), ",": line.count(",")}
    best = max(counts, key=lambda s: counts[s])
    return best if counts[best] > 0 else ","


def detect_format(headers: set[str]) -> int:
    """Détecte le format Amazon (1–5) sur le set de headers normalisés.

    Format 5 : rapport fiscal V5 avec OUR_PRICE/SHIPPING/GIFTWRAP détaillés,
               TRANSACTION_ID, ORDER_DATE, juridictions multiples.
    Format 4 : CSV 2025+ avec TRANSACTION_COMPLETE_DATE + TAX_COLLECTION_RESPONSIBILITY
               (sans TAX_COLLECTION_MODEL).
    Format 3 : TSV/CSV avec TRANSACTION_COMPLETE_DATE + TAX_COLLECTION_MODEL.
    Format 2 : ACTIVITY_PERIOD sans TRANSACTION_COMPLETE_DATE.
    Format 1 : ancien format (fallback).
    """
    if (
        "our_price_tax_exclusive_selling_price" in headers
        and "transaction_id" in headers
        and "order_date" in headers
    ):
        return 5
    if "transaction_complete_date" in headers:
        if (
            "tax_collection_responsibility" in headers
            and "tax_collection_model" not in headers
        ):
            return 4
        return 3
    if "activity_period" in headers:
        return 2
    return 1


# Colonnes critiques attendues par format — utilisées pour les warnings d'import.
EXPECTED_COLUMNS: dict[int, list[str]] = {
    3: [
        "transaction_complete_date", "tax_collection_model",
        "total_activity_value_amt_vat_excl", "sale_depart_country", "sale_arrival_country",
    ],
    4: [
        "transaction_complete_date", "tax_collection_responsibility",
        "total_activity_value_amt_vat_excl", "sale_depart_country", "sale_arrival_country",
        "activity_transaction_id",
    ],
    5: [
        "transaction_id", "order_date", "transaction_type",
        "our_price_tax_exclusive_selling_price", "shipping_tax_exclusive_selling_price",
        "giftwrap_tax_exclusive_selling_price", "ship_from_country", "ship_to_country",
        "tax_collection_responsibility", "jurisdiction_level", "currency",
        "invoice_level_exchange_rate",
    ],
}


def parse_date(date_str: str | None) -> str:
    """Normalise une date Amazon vers YYYY-MM-DD. Retourne '' si invalide.

    Formats reconnus :
      YYYY-MM-DD                → inchangé
      YYYY-MM-DD HH:MM:SS       → tronqué à la date
      YYYY-MM-DDTHH:MM:SSZ      → ISO 8601 avec séparateur 'T' (de plus en
                                   plus fréquent dans les exports Amazon
                                   récents) → tronqué à la date
      DD.MM.YYYY                → inversé
      DD-MM-YYYY                → inversé (format V5 Amazon EU)
    """
    if date_str is None:
        return ""
    s = date_str.strip()
    if not s:
        return ""
    # Séparateur ISO 8601 'T' entre date et heure : "2026-07-08T21:52:45Z".
    # Normalisé en espace AVANT le test " in s" ci-dessous, pour que le
    # tronquage à la date fonctionne de façon identique aux formats
    # "YYYY-MM-DD HH:MM:SS" déjà gérés. Sans cette normalisation, la chaîne
    # complète (avec l'heure et le 'Z') passait telle quelle en aval et
    # cassait le tri chronologique dans le moteur (comparaison lexicale sur
    # une valeur non normalisée).
    s = s.replace("T", " ")
    # Tronquer si datetime complet : "2026-05-01 10:49:00" → "2026-05-01"
    if " " in s:
        s = s.split(" ")[0]
    # DD.MM.YYYY
    if "." in s:
        parts = s.split(".")
        if len(parts) == 3:
            try:
                return f"{int(parts[2]):04d}-{int(parts[1]):02d}-{int(parts[0]):02d}"
            except ValueError:
                pass
    # DD-MM-YYYY (V5 Amazon EU : "30-03-2026") vs YYYY-MM-DD
    # Distingué par la longueur de la dernière partie (4 chiffres = année)
    if "-" in s:
        parts = s.split("-")
        if len(parts) == 3 and len(parts[2]) == 4:
            try:
                return f"{int(parts[2]):04d}-{int(parts[1]):02d}-{int(parts[0]):02d}"
            except ValueError:
                pass
    return s  # déjà YYYY-MM-DD ou format inconnu


def detect_format3_grouped_risk(data_sample: list[dict], min_multiple: int = 2,
                                max_multiple: int = 20, rel_tol: float = 0.005) -> bool:
    """Détecte un risque de ventes groupées (quantité > 1) dans un fichier Format 3.

    Le Format 3 n'a pas de colonne quantité : `_Format3Parser.qty()` force 1.
    Une ligne représentant N unités apparaît donc comme UNE unité au prix N × p.

    Heuristique (par ASIN) : on prend le plus petit montant HT positif observé
    comme prix unitaire de référence ; si un autre montant du même ASIN en est
    un multiple entier (2..max_multiple, tolérance relative `rel_tol`), la
    ligne est probablement groupée. Un simple order_id partagé par plusieurs
    lignes de montants différents NE suffit PAS : c'est le cas normal d'une
    commande multi-articles (faux positif de l'ancienne heuristique).

    Args:
        data_sample: liste de dict avec au minimum les clés ``asin`` et
            ``total_activity_value_amt_vat_excl`` (montant HT, str/nombre/None).
            Les lignes sans ASIN ou sans montant exploitable sont ignorées.

    Returns:
        True si au moins un ASIN présente un montant multiple du prix unitaire.
    """
    if not data_sample:
        return False

    amount_key = "total_activity_value_amt_vat_excl"
    by_asin: dict[str, set[Decimal]] = {}
    for row in data_sample:
        asin = str(row.get("asin") or "").strip().upper()
        if not asin:
            continue
        raw = row.get(amount_key)
        if raw is None or raw == "":
            continue
        try:
            amount = abs(Decimal(str(raw).strip().replace(",", ".")))
        except (InvalidOperation, ValueError, TypeError):
            continue
        if amount > 0:
            by_asin.setdefault(asin, set()).add(amount)

    tol = Decimal(str(rel_tol))
    for asin, amounts in by_asin.items():
        if len(amounts) < 2:
            continue
        unit = min(amounts)
        for amount in amounts:
            if amount == unit:
                continue
            ratio = amount / unit
            k = int(ratio.to_integral_value(rounding=ROUND_HALF_UP))
            if min_multiple <= k <= max_multiple and abs(amount - unit * k) <= unit * k * tol:
                logger.warning(
                    "Format 3 : ventes groupées potentielles pour ASIN=%s "
                    "(montant %s ≈ %d × prix unitaire %s). Le calcul de l'AIC peut être "
                    "sur- ou sous-évalué (quantité forcée à 1).",
                    asin, amount, k, unit,
                )
                return True

    return False
