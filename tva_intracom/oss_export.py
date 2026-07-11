"""Export des états récapitulatifs fiscaux : OSS et B2B reverse charge.

Génère :
- Un fichier Excel (.xlsx) avec 3 onglets :
    1. OSS_Résumé      : TVA due par pays de destination (portail OSS URSSAF)
    2. OSS_Détail      : ligne par ligne des ventes OSS
    3. B2B_Recap       : livraisons intracommunautaires B2B avec numéros TVA
- Deux fichiers CSV :
    1. oss_urssaf.csv  : format portail OSS URSSAF (pays, base HT, taux, TVA)
    2. b2b_recap.csv   : état récapitulatif B2B avec numéros TVA acheteurs

Usage:
    from tva_intracom.oss_export import build_oss_export
    xlsx_path, oss_csv, b2b_csv = build_oss_export(results, period="2024-T1")
"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass, field
from datetime import date as _date
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from typing import List, Optional

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill, Border, Side
from openpyxl.utils import get_column_letter

from .models import Scenario, VatResult
from .ecb_rates import convert_to_eur_for_oss

_CENT = Decimal("0.01")
_ZERO = Decimal("0.00")

# ---------------------------------------------------------------------------
# Agrégation partagée — utilisée par oss_export.py ET oss_xml.py
# ---------------------------------------------------------------------------

# Type : départ → arrivée → taux → {ht, tva, nb}
OssAggType = dict  # dict[str, dict[str, dict[Decimal, dict[str, Decimal | int]]]]


def convert_ht_tva_for_oss_period(res: VatResult, period: str) -> tuple[Decimal, Decimal]:
    """Retourne (ht, tva) d'un VatResult OSS, reconverti au besoin au taux BCE
    de clôture de la période déclarée (Règl. UE 2020/194, art. 5 bis).

    Fonction PARTAGÉE — c'est la seule source de vérité pour la conversion de
    devise appliquée à une ligne OSS. Utilisée à la fois par
    `aggregate_oss_results()` (pour OSS_Résumé et le XML officiel) et par
    l'onglet OSS_Détail, afin que les deux affichages utilisent strictement
    le même montant pour une même vente en devise étrangère.

    Si `period` est vide/non reconnu, ou si la vente est déjà en EUR, on
    retombe sur `res.sale.amount_ht` / `res.vat_amount` tels quels
    (comportement historique, taux du jour de la vente).
    """
    ht  = res.sale.amount_ht
    tva = res.vat_amount

    if period and res.sale.original_currency and res.sale.original_currency != "EUR":
        try:
            tx_date = _date.fromisoformat((res.sale.transaction_date or "")[:10])
        except ValueError:
            tx_date = _date.today()
        sign = Decimal("-1") if ht < 0 else Decimal("1")
        try:
            new_ht_abs, _rate_used, _src = convert_to_eur_for_oss(
                abs(res.sale.original_amount),
                res.sale.original_currency,
                period,
                tx_date,
                fallback_rate=res.sale.exchange_rate or None,
            )
            ht = sign * new_ht_abs
            tva = (ht * (res.vat_rate / Decimal("100"))).quantize(_CENT, rounding=ROUND_HALF_UP)
        except ValueError:
            # BCE indisponible et pas de fallback : on garde le montant déjà
            # converti au taux du jour de la vente plutôt que de bloquer
            # toute la génération du rapport.
            pass

    return ht, tva


def aggregate_oss_results(results: list[VatResult], period: str = "") -> OssAggType:
    """Agrège les VatResult OSS_B2C par pays de départ puis pays d'arrivée.

    Structure retournée (utilisée par oss_xml.py pour le XML officiel et
    par oss_export.py pour l'Excel/CSV URSSAF) :

        {
          "FR": {
            "DE": {
              Decimal("19"): {
                  "ht": Decimal(...), "tva": Decimal(...),        # net (vente+avoir)
                  "ht_vente": Decimal(...), "tva_vente": Decimal(...),   # ventes seules (brut)
                  "ht_remb":  Decimal(...), "tva_remb":  Decimal(...),   # avoirs seuls (négatif)
                  "nb": int,
              },
              ...
            },
          },
          "DE": { ... },
        }

    Les clés "ht"/"tva" (net) sont historiques — c'est ce que consomme
    oss_xml.py et find_oss_negative_buckets(). Les clés "*_vente"/"*_remb"
    sont ajoutées pour permettre un affichage brut/avoir/net séparé
    (OSS_Résumé) sans changer le comportement du XML officiel.

    Args:
        period: période OSS déclarée (ex: "2026-Q1"). Si fournie et reconnue,
            les ventes/avoirs en devise étrangère sont reconvertis en EUR au
            taux BCE du DERNIER JOUR de cette période (Règl. UE 2020/194,
            art. 5 bis) au lieu du taux du jour de la vente déjà figé sur
            `sale.amount_ht` lors de l'import. Si `period` est vide ou non
            reconnu, on retombe sur `sale.amount_ht`/`res.vat_amount` tels
            quels (comportement historique).
    """
    aggregated: OssAggType = {}

    for res in results:
        if res.scenario not in (Scenario.OSS_B2C, Scenario.IOSS_DIRECT):
            continue

        departure = res.sale.stock_country   # MemberStateOfSupply (pour OSS) ou Pays tiers (pour IOSS)
        arrival   = res.vat_country          # MemberStateOfConsumption
        rate      = res.vat_rate

        ht, tva = convert_ht_tva_for_oss_period(res, period)

        aggregated.setdefault(departure, {})
        aggregated[departure].setdefault(arrival, {})
        aggregated[departure][arrival].setdefault(
            rate, {
                "ht": Decimal("0.00"), "tva": Decimal("0.00"),
                "ht_vente": Decimal("0.00"), "tva_vente": Decimal("0.00"),
                "ht_remb":  Decimal("0.00"), "tva_remb":  Decimal("0.00"),
                "nb": 0,
            }
        )

        bucket = aggregated[departure][arrival][rate]
        bucket["ht"]  += ht
        bucket["tva"] += tva
        if ht >= 0:
            bucket["ht_vente"]  += ht
            bucket["tva_vente"] += tva
        else:
            bucket["ht_remb"]  += ht
            bucket["tva_remb"] += tva
        bucket["nb"]  += 1

    return aggregated


@dataclass
class OssNegativeBucket:
    """Couple (pays départ → pays destination, taux) dont le solde net OSS
    est négatif sur la période — situation que le portail OSS et le XML
    officiel n'acceptent pas dans le corps principal de la déclaration.

    Survient typiquement quand les avoirs (remboursements) dépassent les
    ventes d'un même pays/taux sur la période — souvent le signe qu'un
    avoir se rapporte en réalité à une vente d'une période antérieure déjà
    déclarée, et qui devrait alors être ventilé dans le bloc
    `CorrectionsOfVatReturns` du XML en référençant la période d'origine
    (Règl. UE 2020/194). L'outil ne peut pas déterminer automatiquement
    cette période d'origine (aucune référence à la vente initiale n'est
    conservée sur l'avoir) — à vérifier et corriger manuellement.
    """
    departure: str
    arrival: str
    vat_rate: Decimal
    base_ht: Decimal
    vat_amount: Decimal


@dataclass
class MatchedRefundCorrection:
    """Un avoir dont l'origine a été rattachée avec CERTITUDE à une vente
    antérieure de la même commande (même sale_id), au sein du même jeu de
    données fourni. Le rattachement se fait UNIQUEMENT sur sale_id identique
    (même couple pays/taux) — jamais par déduction sur order_date, jugé non
    fiable pour générer automatiquement une correction fiscale (voir
    models.py, champ Sale.order_date)."""
    sale_id: str
    origin_period: str          # période OSS d'origine déduite (ex: "2026-Q1")
    base_ht: Decimal
    vat_amount: Decimal
    refund_result: VatResult    # référence à l'objet, pour exclusion précise
                                 # (par identité Python id()) du corps XML
                                 # principal — voir oss_xml.generate_oss_xml.


@dataclass
class NegativeBucketSuggestion:
    """Détail d'un couple (départ, arrivée, taux) en solde négatif, avec la
    part des avoirs qui a pu être rattachée à une vente d'origine identifiée
    (matched, groupée par période d'origine) et la part restée sans
    correspondance (unmatched — à traiter manuellement, comme avant)."""
    bucket: "OssNegativeBucket"
    matched: list[MatchedRefundCorrection]
    unmatched_ht: Decimal
    unmatched_vat_amount: Decimal
    unmatched_count: int

    @property
    def fully_resolved(self) -> bool:
        """True si TOUS les avoirs du couple négatif ont pu être rattachés
        à une origine identifiée — condition nécessaire pour générer
        automatiquement les corrections sans laisser de solde négatif
        résiduel dans le corps principal du XML."""
        return self.unmatched_ht == Decimal("0.00") and self.unmatched_vat_amount == Decimal("0.00")


def _oss_quarter_of(transaction_date: str) -> str:
    """Déduit le trimestre OSS 'YYYY-QN' d'une transaction_date 'YYYY-MM-DD'.
    Retourne '' si la date est vide ou non reconnue."""
    d = (transaction_date or "")[:10]
    if len(d) < 7:
        return ""
    try:
        year = int(d[:4])
        month = int(d[5:7])
    except ValueError:
        return ""
    q = (month - 1) // 3 + 1
    return f"{year}-Q{q}"


def suggest_negative_bucket_corrections(
    results: list[VatResult],
    period: str,
) -> list[NegativeBucketSuggestion]:
    """Pour chaque couple (départ, arrivée, taux) en solde négatif sur la
    période, tente de rattacher chaque avoir constitutif à une vente
    d'origine PRÉSENTE DANS LE MÊME JEU DE DONNÉES `results` (même sale_id,
    même couple pays/taux, montant positif). Ce rattachement n'est possible
    que si le fichier importé couvre aussi la période d'origine de la vente
    créditée — sinon l'avoir reste `unmatched`, exactement comme le
    comportement actuel (blocage manuel).

    N'utilise PAS order_date : seul un sale_id identique, retrouvé dans le
    jeu de données réellement fourni, est considéré comme une preuve
    suffisante pour une correction fiscale automatisée.
    """
    aggregated = aggregate_oss_results(results, period=period)
    negative_buckets = find_oss_negative_buckets(aggregated)
    if not negative_buckets:
        return []

    neg_keys = {(b.departure, b.arrival, b.vat_rate) for b in negative_buckets}

    # Ventes positives disponibles pour matching, indexées par (sale_id, pays, taux)
    positive_by_sale_id: dict[tuple, list[VatResult]] = {}
    refunds_by_bucket: dict[tuple, list[VatResult]] = {}
    for res in results:
        if res.scenario not in (Scenario.OSS_B2C, Scenario.IOSS_DIRECT):
            continue
        key = (res.sale.stock_country, res.vat_country, res.vat_rate)
        if key not in neg_keys:
            continue
        if res.sale.amount_ht > 0:
            positive_by_sale_id.setdefault(
                (res.sale.sale_id, res.sale.stock_country, res.vat_country, res.vat_rate), []
            ).append(res)
        elif res.sale.amount_ht < 0:
            refunds_by_bucket.setdefault(key, []).append(res)

    suggestions: list[NegativeBucketSuggestion] = []
    for b in negative_buckets:
        key = (b.departure, b.arrival, b.vat_rate)
        matched: list[MatchedRefundCorrection] = []
        unmatched_ht = Decimal("0.00")
        unmatched_vat = Decimal("0.00")
        unmatched_count = 0

        for refund in refunds_by_bucket.get(key, []):
            candidates = positive_by_sale_id.get(
                (refund.sale.sale_id, refund.sale.stock_country, refund.vat_country, refund.vat_rate)
            )
            origin_quarter = _oss_quarter_of(candidates[0].sale.transaction_date) if candidates else ""
            # On n'accepte le rattachement que si une origine a été trouvée
            # ET qu'elle correspond bien à une période DIFFÉRENTE de la
            # période courante (sinon ce n'est pas un avoir "à cheval",
            # juste un solde négatif normal intra-période — pas notre sujet).
            if origin_quarter and origin_quarter != period:
                matched.append(MatchedRefundCorrection(
                    sale_id=refund.sale.sale_id,
                    origin_period=origin_quarter,
                    base_ht=refund.sale.amount_ht,
                    vat_amount=refund.vat_amount,
                    refund_result=refund,
                ))
            else:
                unmatched_ht += refund.sale.amount_ht
                unmatched_vat += refund.vat_amount
                unmatched_count += 1

        suggestions.append(NegativeBucketSuggestion(
            bucket=b,
            matched=matched,
            unmatched_ht=unmatched_ht,
            unmatched_vat_amount=unmatched_vat,
            unmatched_count=unmatched_count,
        ))

    return suggestions


def find_oss_negative_buckets(aggregated: OssAggType) -> list[OssNegativeBucket]:
    """Liste les couples (départ, arrivée, taux) dont le solde HT ou TVA est négatif."""
    negatives: list[OssNegativeBucket] = []
    for departure, destinations in aggregated.items():
        for arrival, rates in destinations.items():
            for rate, amounts in rates.items():
                if amounts["ht"] < 0 or amounts["tva"] < 0:
                    negatives.append(OssNegativeBucket(
                        departure=departure, arrival=arrival, vat_rate=rate,
                        base_ht=amounts["ht"], vat_amount=amounts["tva"],
                    ))
    return negatives

# Palette couleurs
_BLUE_HEADER = "1F4E79"   # Bleu foncé headers principaux
_BLUE_LIGHT  = "BDD7EE"   # Bleu clair sous-headers
_GREEN_HDR   = "375623"   # Vert foncé onglet B2B
_GREEN_LIGHT = "C6EFCE"   # Vert clair total B2B
_ORANGE_HDR  = "C55A11"   # Orange onglet OSS détail
_ORANGE_LIGHT= "FCE4D6"   # Orange clair
_TOTAL_FILL  = "FFF2CC"   # Jaune ligne totaux
_WHITE       = "FFFFFF"
_GREY_ROW    = "F2F2F2"

_THIN = Side(style="thin", color="BFBFBF")
_BORDER = Border(left=_THIN, right=_THIN, top=_THIN, bottom=_THIN)

COUNTRY_NAMES = {
    "AT": "Autriche", "BE": "Belgique", "BG": "Bulgarie", "HR": "Croatie",
    "CY": "Chypre", "CZ": "Tchéquie", "DK": "Danemark", "EE": "Estonie",
    "FI": "Finlande", "FR": "France", "DE": "Allemagne", "GR": "Grèce",
    "HU": "Hongrie", "IE": "Irlande", "IT": "Italie", "LV": "Lettonie",
    "LT": "Lituanie", "LU": "Luxembourg", "MT": "Malte", "NL": "Pays-Bas",
    "PL": "Pologne", "PT": "Portugal", "RO": "Roumanie", "SK": "Slovaquie",
    "SI": "Slovénie", "ES": "Espagne", "SE": "Suède",
}


@dataclass
class OssCountryLine:
    country: str
    country_name: str
    vat_rate: Decimal
    base_ht: Decimal          # NET (vente + avoir) — clé historique
    vat_amount: Decimal       # NET (vente + avoir) — clé historique
    nb_transactions: int
    base_ht_vente: Decimal = _ZERO   # brut, ventes seules
    vat_vente: Decimal      = _ZERO   # brut, ventes seules
    base_ht_remb: Decimal   = _ZERO   # avoirs seuls (négatif)
    vat_remb: Decimal       = _ZERO   # avoirs seuls (négatif)


@dataclass
class B2bLine:
    sale_id: str
    buyer_vat_number: str
    buyer_country: str
    country_name: str
    amount_ht: Decimal
    transaction_date: str


@dataclass
class OssExportData:
    oss_by_country: List[OssCountryLine]
    oss_details: List[tuple]   # (VatResult, ht_converti, tva_converti) — voir convert_ht_tva_for_oss_period
    b2b_lines: List[B2bLine]
    period: str
    total_oss_ht: Decimal = _ZERO
    total_oss_vat: Decimal = _ZERO
    total_b2b_ht: Decimal = _ZERO


def _aggregate(results: List[VatResult], period: str = "") -> OssExportData:
    """Agrège les VatResult en données prêtes pour l'export.

    S'appuie sur aggregate_oss_results() — source unique de vérité partagée
    avec oss_xml.py pour garantir la cohérence entre l'Excel URSSAF et le XML OSS.
    """
    oss_agg = aggregate_oss_results(results, period=period)
    b2b_results = [r for r in results if r.scenario == Scenario.B2B_REVERSE_CHARGE]

    # Aplatissement de la structure hiérarchique départ→arrivée→taux
    # en une liste plate par (pays_destination, taux) pour l'Excel URSSAF.
    # Note : l'Excel OSS consolide TOUS les pays de départ — la DGFiP attend
    # une vue par pays de consommation (pas de départ) dans l'état OSS FR.
    country_map: dict[tuple[str, Decimal], dict] = {}

    for departure, destinations in oss_agg.items():
        for arrival, rates in destinations.items():
            for rate, amounts in rates.items():
                key = (arrival, rate)
                if key not in country_map:
                    country_map[key] = {
                        "country": arrival,
                        "country_name": COUNTRY_NAMES.get(arrival, arrival),
                        "vat_rate": rate,
                        "base_ht": _ZERO,
                        "vat_amount": _ZERO,
                        "base_ht_vente": _ZERO,
                        "vat_vente": _ZERO,
                        "base_ht_remb": _ZERO,
                        "vat_remb": _ZERO,
                        "nb": 0,
                    }
                country_map[key]["base_ht"]       += amounts["ht"]
                country_map[key]["vat_amount"]     += amounts["tva"]
                country_map[key]["base_ht_vente"]  += amounts["ht_vente"]
                country_map[key]["vat_vente"]      += amounts["tva_vente"]
                country_map[key]["base_ht_remb"]   += amounts["ht_remb"]
                country_map[key]["vat_remb"]       += amounts["tva_remb"]
                country_map[key]["nb"]             += amounts["nb"]

    # Reconstruire la liste de détail OSS depuis les résultats d'origine, en
    # appliquant la MÊME reconversion BCE de clôture de période que le Résumé
    # (convert_ht_tva_for_oss_period) — auparavant le détail affichait le
    # montant au taux du jour de la vente, différent du Résumé pour toute
    # vente en devise étrangère.
    oss_detail_results = [
        (r, *convert_ht_tva_for_oss_period(r, period))
        for r in results if r.scenario == Scenario.OSS_B2C
    ]

    oss_lines = [
        OssCountryLine(
            country=v["country"],
            country_name=v["country_name"],
            vat_rate=v["vat_rate"],
            base_ht=v["base_ht"],
            vat_amount=v["vat_amount"],
            nb_transactions=v["nb"],
            base_ht_vente=v["base_ht_vente"],
            vat_vente=v["vat_vente"],
            base_ht_remb=v["base_ht_remb"],
            vat_remb=v["vat_remb"],
        )
        for v in sorted(country_map.values(), key=lambda x: x["country"])
    ]

    b2b_lines = [
        B2bLine(
            sale_id=(getattr(r.sale, "display_id", "") or r.sale.sale_id),
            buyer_vat_number=r.sale.buyer_vat_number,
            buyer_country=r.sale.buyer_country,
            country_name=COUNTRY_NAMES.get(r.sale.buyer_country, r.sale.buyer_country),
            amount_ht=r.sale.amount_ht,
            transaction_date=r.sale.transaction_date,
        )
        for r in b2b_results
    ]

    return OssExportData(
        oss_by_country=oss_lines,
        oss_details=oss_detail_results,
        b2b_lines=b2b_lines,
        period="",
        total_oss_ht=sum((l.base_ht for l in oss_lines), _ZERO),
        total_oss_vat=sum((l.vat_amount for l in oss_lines), _ZERO),
        total_b2b_ht=sum((l.amount_ht for l in b2b_lines), _ZERO),
    )


def _hdr_cell(ws, row: int, col: int, value: str, bg: str, fg: str = _WHITE, bold: bool = True, size: int = 10):
    c = ws.cell(row=row, column=col, value=value)
    c.font = Font(bold=bold, color=fg, name="Arial", size=size)
    c.fill = PatternFill("solid", start_color=bg)
    c.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    c.border = _BORDER
    return c


def _data_cell(ws, row: int, col: int, value, fmt: str = None, zebra: bool = False):
    c = ws.cell(row=row, column=col, value=value)
    c.font = Font(name="Arial", size=9)
    c.border = _BORDER
    c.alignment = Alignment(vertical="center")
    if fmt:
        c.number_format = fmt
    if zebra:
        c.fill = PatternFill("solid", start_color=_GREY_ROW)
    return c


def _total_cell(ws, row: int, col: int, value, fmt: str = None):
    c = ws.cell(row=row, column=col, value=value)
    c.font = Font(bold=True, name="Arial", size=9)
    c.fill = PatternFill("solid", start_color=_TOTAL_FILL)
    c.border = _BORDER
    c.alignment = Alignment(vertical="center")
    if fmt:
        c.number_format = fmt
    return c


def _build_oss_resume(wb: Workbook, data: OssExportData, period: str):
    ws = wb.create_sheet("OSS_Résumé")
    ws.sheet_view.showGridLines = False

    # Titre
    ws.merge_cells("A1:J1")
    t = ws["A1"]
    t.value = f"ÉTAT RÉCAPITULATIF OSS — {period}"
    t.font = Font(bold=True, size=13, color=_WHITE, name="Arial")
    t.fill = PatternFill("solid", start_color=_BLUE_HEADER)
    t.alignment = Alignment(horizontal="center", vertical="center")
    ws.row_dimensions[1].height = 28

    ws.merge_cells("A2:J2")
    sub = ws["A2"]
    sub.value = "Guichet unique OSS — à déclarer auprès de l'administration fiscale française (URSSAF / DGFiP)"
    sub.font = Font(italic=True, size=9, color="595959", name="Arial")
    sub.alignment = Alignment(horizontal="center")

    # Headers colonnes
    headers = ["Code pays", "Pays", "Taux TVA",
               "Base vente (€)", "TVA vente (€)",
               "Base avoir (€)", "TVA avoir (€)",
               "Base nette (€)", "TVA nette (€)",
               "Nb transactions"]
    widths =  [12,           22,     10,
               16,             15,
               16,             15,
               16,             15,
               14]
    for col, (h, w) in enumerate(zip(headers, widths), 1):
        _hdr_cell(ws, 3, col, h, _BLUE_LIGHT, fg="1F4E79", size=9)
        ws.column_dimensions[get_column_letter(col)].width = w
    ws.row_dimensions[3].height = 20

    # Données
    for i, line in enumerate(data.oss_by_country):
        r = i + 4
        zebra = i % 2 == 1
        _data_cell(ws, r, 1, line.country, zebra=zebra).alignment = Alignment(horizontal="center", vertical="center")
        _data_cell(ws, r, 2, line.country_name, zebra=zebra)
        _data_cell(ws, r, 3, float(line.vat_rate) / 100, fmt="0.0%", zebra=zebra).alignment = Alignment(horizontal="center", vertical="center")
        _data_cell(ws, r, 4, float(line.base_ht_vente), fmt='#,##0.00 "€"', zebra=zebra).alignment = Alignment(horizontal="right", vertical="center")
        _data_cell(ws, r, 5, float(line.vat_vente), fmt='#,##0.00 "€"', zebra=zebra).alignment = Alignment(horizontal="right", vertical="center")
        _data_cell(ws, r, 6, float(line.base_ht_remb), fmt='#,##0.00 "€"', zebra=zebra).alignment = Alignment(horizontal="right", vertical="center")
        _data_cell(ws, r, 7, float(line.vat_remb), fmt='#,##0.00 "€"', zebra=zebra).alignment = Alignment(horizontal="right", vertical="center")
        _data_cell(ws, r, 8, float(line.base_ht), fmt='#,##0.00 "€"', zebra=zebra).alignment = Alignment(horizontal="right", vertical="center")
        _data_cell(ws, r, 9, float(line.vat_amount), fmt='#,##0.00 "€"', zebra=zebra).alignment = Alignment(horizontal="right", vertical="center")
        _data_cell(ws, r, 10, line.nb_transactions, zebra=zebra).alignment = Alignment(horizontal="center", vertical="center")
        ws.row_dimensions[r].height = 16

    # Ligne total
    n = len(data.oss_by_country)
    total_row = n + 4
    _total_cell(ws, total_row, 1, "TOTAL").alignment = Alignment(horizontal="center", vertical="center")
    _total_cell(ws, total_row, 2, f"{n} pays")
    _total_cell(ws, total_row, 3, "")
    _total_cell(ws, total_row, 4, f"=SUM(D4:D{total_row-1})", fmt='#,##0.00 "€"').alignment = Alignment(horizontal="right", vertical="center")
    _total_cell(ws, total_row, 5, f"=SUM(E4:E{total_row-1})", fmt='#,##0.00 "€"').alignment = Alignment(horizontal="right", vertical="center")
    _total_cell(ws, total_row, 6, f"=SUM(F4:F{total_row-1})", fmt='#,##0.00 "€"').alignment = Alignment(horizontal="right", vertical="center")
    _total_cell(ws, total_row, 7, f"=SUM(G4:G{total_row-1})", fmt='#,##0.00 "€"').alignment = Alignment(horizontal="right", vertical="center")
    _total_cell(ws, total_row, 8, f"=SUM(H4:H{total_row-1})", fmt='#,##0.00 "€"').alignment = Alignment(horizontal="right", vertical="center")
    _total_cell(ws, total_row, 9, f"=SUM(I4:I{total_row-1})", fmt='#,##0.00 "€"').alignment = Alignment(horizontal="right", vertical="center")
    _total_cell(ws, total_row, 10, f"=SUM(J4:J{total_row-1})").alignment = Alignment(horizontal="center", vertical="center")
    ws.row_dimensions[total_row].height = 18

    # Note de bas de page
    note_row = total_row + 2
    ws.merge_cells(f"A{note_row}:J{note_row}")
    n_cell = ws[f"A{note_row}"]
    n_cell.value = (
        "⚠️  Ce document est un récapitulatif indicatif. « Base/TVA nette » (vente + avoir) est le "
        "montant à déclarer sur le portail OSS. "
        "Vérifiez les montants avant dépôt sur le portail OSS URSSAF (https://www.impots.gouv.fr)."
    )
    n_cell.font = Font(italic=True, size=8, color="C00000", name="Arial")


def _build_oss_detail(wb: Workbook, data: OssExportData):
    ws = wb.create_sheet("OSS_Détail")
    ws.sheet_view.showGridLines = False

    ws.merge_cells("A1:H1")
    t = ws["A1"]
    t.value = "DÉTAIL DES VENTES OSS (B2C intracommunautaires)"
    t.font = Font(bold=True, size=12, color=_WHITE, name="Arial")
    t.fill = PatternFill("solid", start_color=_ORANGE_HDR)
    t.alignment = Alignment(horizontal="center", vertical="center")
    ws.row_dimensions[1].height = 25

    headers = ["ID vente", "Date", "Stock", "Destination", "Pays dest.", "Base HT (€)", "Taux TVA", "TVA (€)"]
    widths =  [20,         14,     10,      14,             16,            16,             11,          14]
    for col, (h, w) in enumerate(zip(headers, widths), 1):
        _hdr_cell(ws, 2, col, h, _ORANGE_LIGHT, fg=_ORANGE_HDR, size=9)
        ws.column_dimensions[get_column_letter(col)].width = w
    ws.row_dimensions[2].height = 18

    # data.oss_details contient des tuples (VatResult, ht_converti, tva_converti) —
    # ht/tva déjà reconvertis au taux BCE de clôture de période (même valeur
    # que celle agrégée dans OSS_Résumé, voir convert_ht_tva_for_oss_period).
    for i, (r, ht, tva) in enumerate(data.oss_details):
        row = i + 3
        zebra = i % 2 == 1
        _data_cell(ws, row, 1, (getattr(r.sale, "display_id", "") or r.sale.sale_id), zebra=zebra)
        _data_cell(ws, row, 2, r.sale.transaction_date, zebra=zebra).alignment = Alignment(horizontal="center", vertical="center")
        _data_cell(ws, row, 3, r.sale.stock_country, zebra=zebra).alignment = Alignment(horizontal="center", vertical="center")
        _data_cell(ws, row, 4, r.sale.buyer_country, zebra=zebra).alignment = Alignment(horizontal="center", vertical="center")
        _data_cell(ws, row, 5, COUNTRY_NAMES.get(r.sale.buyer_country, r.sale.buyer_country), zebra=zebra)
        _data_cell(ws, row, 6, float(ht), fmt='#,##0.00 "€"', zebra=zebra).alignment = Alignment(horizontal="right", vertical="center")
        _data_cell(ws, row, 7, float(r.vat_rate) / 100, fmt="0.0%", zebra=zebra).alignment = Alignment(horizontal="center", vertical="center")
        _data_cell(ws, row, 8, float(tva), fmt='#,##0.00 "€"', zebra=zebra).alignment = Alignment(horizontal="right", vertical="center")
        ws.row_dimensions[row].height = 15

    # Totaux
    n = len(data.oss_details)
    tr = n + 3
    for col in range(1, 6):
        _total_cell(ws, tr, col, "")
    _total_cell(ws, tr, 5, "TOTAL")
    _total_cell(ws, tr, 6, f"=SUM(F3:F{tr-1})", fmt='#,##0.00 "€"').alignment = Alignment(horizontal="right", vertical="center")
    _total_cell(ws, tr, 7, "")
    _total_cell(ws, tr, 8, f"=SUM(H3:H{tr-1})", fmt='#,##0.00 "€"').alignment = Alignment(horizontal="right", vertical="center")


def _build_b2b_recap(wb: Workbook, data: OssExportData, period: str):
    ws = wb.create_sheet("B2B_Recap")
    ws.sheet_view.showGridLines = False

    ws.merge_cells("A1:F1")
    t = ws["A1"]
    t.value = f"ÉTAT RÉCAPITULATIF — LIVRAISONS INTRACOMMUNAUTAIRES B2B — {period}"
    t.font = Font(bold=True, size=12, color=_WHITE, name="Arial")
    t.fill = PatternFill("solid", start_color=_GREEN_HDR)
    t.alignment = Alignment(horizontal="center", vertical="center")
    ws.row_dimensions[1].height = 25

    ws.merge_cells("A2:F2")
    sub = ws["A2"]
    sub.value = "Autoliquidation — Exonéré de TVA (art. 262 ter I CGI) — À reporter sur DES / état récapitulatif"
    sub.font = Font(italic=True, size=9, color="595959", name="Arial")
    sub.alignment = Alignment(horizontal="center")

    headers = ["ID vente", "Date", "N° TVA acheteur", "Code pays", "Pays acheteur", "Montant HT (€)"]
    widths  = [20,         14,     22,                 12,          20,               18]
    for col, (h, w) in enumerate(zip(headers, widths), 1):
        _hdr_cell(ws, 3, col, h, _GREEN_LIGHT, fg=_GREEN_HDR, size=9)
        ws.column_dimensions[get_column_letter(col)].width = w
    ws.row_dimensions[3].height = 18

    for i, line in enumerate(data.b2b_lines):
        row = i + 4
        zebra = i % 2 == 1
        _data_cell(ws, row, 1, line.sale_id, zebra=zebra)
        _data_cell(ws, row, 2, line.transaction_date, zebra=zebra).alignment = Alignment(horizontal="center", vertical="center")
        _data_cell(ws, row, 3, line.buyer_vat_number or "—", zebra=zebra)
        _data_cell(ws, row, 4, line.buyer_country, zebra=zebra).alignment = Alignment(horizontal="center", vertical="center")
        _data_cell(ws, row, 5, line.country_name, zebra=zebra)
        _data_cell(ws, row, 6, float(line.amount_ht), fmt='#,##0.00 "€"', zebra=zebra).alignment = Alignment(horizontal="right", vertical="center")
        ws.row_dimensions[row].height = 15

    n = len(data.b2b_lines)
    tr = n + 4
    for col in range(1, 6):
        _total_cell(ws, tr, col, "")
    _total_cell(ws, tr, 5, "TOTAL HT")
    _total_cell(ws, tr, 6, f"=SUM(F4:F{tr-1})", fmt='#,##0.00 "€"').alignment = Alignment(horizontal="right", vertical="center")

    note_row = tr + 2
    ws.merge_cells(f"A{note_row}:F{note_row}")
    n_cell = ws[f"A{note_row}"]
    n_cell.value = (
        "⚠️  Vérifiez chaque numéro TVA via le service VIES (https://ec.europa.eu/taxation_customs/vies/) "
        "avant de déposer votre état récapitulatif."
    )
    n_cell.font = Font(italic=True, size=8, color="C00000", name="Arial")


def build_oss_excel(results: List[VatResult], output_path: str | Path, period: str = "") -> Path:
    """Génère le fichier Excel multi-onglets OSS uniquement.

    Args:
        results: liste de VatResult issus du moteur.
        output_path: chemin de sortie du fichier .xlsx.
        period: libellé de la période (ex: "2024-T1", "Mars 2024").

    Returns:
        Path du fichier généré.
    """
    data = _aggregate(results, period=period)
    data.period = period

    wb = Workbook()
    # Supprimer la feuille par défaut
    wb.remove(wb.active)

    _build_oss_resume(wb, data, period)
    _build_oss_detail(wb, data)

    output_path = Path(output_path)
    wb.save(str(output_path))
    return output_path


def build_b2b_excel(results: List[VatResult], output_path: str | Path, period: str = "") -> Path:
    """Génère le fichier Excel pour les livraisons B2B (État récapitulatif)."""
    data = _aggregate(results, period=period)
    
    wb = Workbook()
    wb.remove(wb.active)
    _build_b2b_recap(wb, data, period)

    output_path = Path(output_path)
    wb.save(str(output_path))
    return output_path


def build_oss_csv(results: List[VatResult], period: str = "") -> tuple[bytes, bytes]:
    """Génère les deux CSV : OSS URSSAF et B2B récapitulatif.

    Returns:
        Tuple (oss_csv_bytes, b2b_csv_bytes) encodés UTF-8 avec BOM
        (compatible Excel direct).
    """
    data = _aggregate(results, period=period)

    # --- CSV OSS URSSAF ---
    oss_buf = io.StringIO()
    oss_writer = csv.writer(oss_buf, delimiter=";", quoting=csv.QUOTE_MINIMAL)
    oss_writer.writerow([f"État récapitulatif OSS — {period}"])
    oss_writer.writerow([])
    oss_writer.writerow(["Code pays", "Pays", "Taux TVA (%)", "Base HT (EUR)", "TVA due (EUR)", "Nb transactions"])
    for line in data.oss_by_country:
        oss_writer.writerow([
            line.country,
            line.country_name,
            str(line.vat_rate).replace(".", ","),
            str(line.base_ht).replace(".", ","),
            str(line.vat_amount).replace(".", ","),
            line.nb_transactions,
        ])
    oss_writer.writerow([])
    oss_writer.writerow([
        "TOTAL", "",
        "",
        str(data.total_oss_ht).replace(".", ","),
        str(data.total_oss_vat).replace(".", ","),
        sum(l.nb_transactions for l in data.oss_by_country),
    ])

    # --- CSV B2B ---
    b2b_buf = io.StringIO()
    b2b_writer = csv.writer(b2b_buf, delimiter=";", quoting=csv.QUOTE_MINIMAL)
    b2b_writer.writerow([f"État récapitulatif B2B intracommunautaire — {period}"])
    b2b_writer.writerow([])
    b2b_writer.writerow(["ID vente", "Date", "N° TVA acheteur", "Code pays", "Pays acheteur", "Montant HT (EUR)"])
    for line in data.b2b_lines:
        b2b_writer.writerow([
            line.sale_id,
            line.transaction_date,
            line.buyer_vat_number or "",
            line.buyer_country,
            line.country_name,
            str(line.amount_ht).replace(".", ","),
        ])
    b2b_writer.writerow([])
    b2b_writer.writerow(["TOTAL", "", "", "", "", str(data.total_b2b_ht).replace(".", ",")])

    # UTF-8 BOM pour compatibilité Excel
    oss_bytes = ("\ufeff" + oss_buf.getvalue()).encode("utf-8")
    b2b_bytes = ("\ufeff" + b2b_buf.getvalue()).encode("utf-8")
    return oss_bytes, b2b_bytes


def build_oss_export(
    results: List[VatResult],
    output_dir: str | Path,
    period: str = "",
) -> tuple[Path, bytes, bytes]:
    """Point d'entrée principal : génère Excel + les deux CSV.

    Returns:
        Tuple (xlsx_path, oss_csv_bytes, b2b_csv_bytes).
    """
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    xlsx_path = build_oss_excel(results, output_dir / "etat_recapitulatif_oss.xlsx", period)
    oss_csv, b2b_csv = build_oss_csv(results, period)
    return xlsx_path, oss_csv, b2b_csv