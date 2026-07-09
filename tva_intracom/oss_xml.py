"""
Module de génération du fichier XML officiel pour la déclaration Guichet Unique (OSS).
D'après les spécifications de l'architecture standard de l'Union Européenne.

Structure XML correcte (Reg. UE 2021/965) :
  VatReturnDetails
    └─ SupplyFromMemberState  (= pays de départ du stock, un bloc par pays)
         └─ MemberStateOfSupply      : code ISO du pays de départ
         └─ SuppliesPerMemberStateOfConsumption  (un bloc par pays destination)
              └─ MemberStateOfConsumption : code ISO du pays destination
              └─ GoodsSupplies           : un bloc par taux TVA
                   └─ VatRate            : taux + attribut type (STANDARD/REDUCED)
                   └─ TaxableAmount      : base HT
                   └─ VatAmountIssued    : TVA collectée
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from decimal import Decimal
from typing import List
from xml.dom import minidom

from tva_intracom.models import Scenario, VatResult
from tva_intracom.rates import STANDARD_VAT_RATES, is_eu
from tva_intracom.oss_export import (
    aggregate_oss_results, find_oss_negative_buckets, COUNTRY_NAMES,
    suggest_negative_bucket_corrections, NegativeBucketSuggestion,
)

# Type interne : départ → arrivée → taux → {ht, tva, nb}
_AggType = dict


def aggregate_oss_data(results: List[VatResult], period: str = "") -> _AggType:
    """Alias de aggregate_oss_results() — conservé pour compatibilité ascendante.

    Utilise désormais la fonction commune d'oss_export.py pour garantir que
    l'Excel URSSAF et le XML officiel agrègent les données de manière identique,
    y compris la reconversion EUR au taux BCE de clôture de période pour les
    ventes en devise étrangère (Règl. UE 2020/194, art. 5 bis).
    """
    return aggregate_oss_results(results, period=period)


def preview_negative_bucket_suggestions(
    results: List[VatResult], period: str
) -> list[NegativeBucketSuggestion]:
    """Alias public pour l'UI : prévisualise les rattachements avoir→origine
    possibles AVANT de tenter generate_oss_xml(confirm_corrections=True).
    Permet d'afficher à l'utilisateur le détail (rattaché / non rattaché)
    et de ne proposer la confirmation que si fully_resolved est vrai pour
    tous les couples concernés."""
    return suggest_negative_bucket_corrections(results, period=period)


def generate_oss_xml(
    results: List[VatResult],
    seller_vat: str,
    period: str,
    intermediary_vat: str | None = None,
    local_vat_numbers: dict[str, str] | None = None,
    confirm_corrections: bool = False,
) -> bytes:
    """Génère le contenu XML conforme pour la télédéclaration OSS.

    Args:
        results:          liste de VatResult issus du moteur de TVA.
        seller_vat:       numéro TVA de l'assujetti déclarant (ex: "FR12345678901").
        period:           période de déclaration au format OSS (ex: "2026-Q1").
        intermediary_vat: numéro TVA de l'intermédiaire (optionnel, régime IOSS).
        local_vat_numbers: dictionnaire {code_pays: numéro_TVA_local} pour
                           les pays de départ du stock hors pays d'identification.
        confirm_corrections: si True, et que TOUS les avoirs responsables d'un
                           solde négatif ont pu être rattachés avec certitude
                           à une vente d'origine présente dans `results`
                           (même sale_id — voir oss_export.suggest_negative_bucket_corrections),
                           génère automatiquement le bloc <CorrectionsOfVatReturns>
                           correspondant et exclut ces avoirs du corps principal.
                           Si au moins un avoir d'un couple négatif reste sans
                           origine identifiée, la génération est TOUJOURS bloquée
                           pour ce couple (comportement historique), même avec
                           confirm_corrections=True — voir le détail de l'erreur.
                           Par défaut False : comportement historique inchangé
                           (blocage systématique, à décider manuellement).

    Returns:
        Contenu XML encodé UTF-8, indenté et prêt au dépôt.
    """
    # ── Validation et normalisation de la période ─────────────────────────
    # Formats acceptés :
    #   - "2026-Q1" : format international OSS (norme DGFIP)
    #   - "2026-T1" : format français courant dans l'UI
    # Les deux sont normalisés vers "YYYY-QN" avant injection dans le XML.
    # Une période vide produit une erreur explicite plutôt qu'un XML silencieusement invalide.
    if not period or not period.strip():
        raise ValueError(
            "La période OSS est vide. "
            "Renseignez le champ période avant de générer le XML (ex: '2026-Q1')."
        )
    period = period.strip()
    # Normaliser T → Q (format FR → format international)
    period = re.sub(r"(?i)-T([1-4])$", lambda m: f"-Q{m.group(1)}", period)
    
    # Normaliser YYYY-MM (mensuel) vers YYYY-QN car le XML OSS officiel
    # de l'URSSAF/DGFIP n'accepte QUE le format trimestriel (QN).
    # Une vente du mois 06 doit être déclarée dans le XML du Q2.
    m_match = re.match(r"^(\d{4})-(\d{2})$", period)
    if m_match:
        year, month = m_match.groups()
        q = (int(month) - 1) // 3 + 1
        period = f"{year}-Q{q}"

    # Formats valides :
    #   YYYY-QN          : trimestriel standard OSS
    #   YYYY-SN          : semestriel (agrégation multi-fichiers)
    #   YYYY             : annuel (art. 369 sexies Dir. 2006/112)
    #   YYYY-QN_QM       : plage trimestrielle (agrégation partielle)
    #   YYYY-YYYY        : plage multi-année
    _VALID_PERIODS = [
        re.compile(r"^\d{4}-Q[1-4]$"),           # 2026-Q1
        re.compile(r"^\d{4}-S[12]$"),             # 2026-S1
        re.compile(r"^\d{4}$"),                   # 2026
        re.compile(r"^\d{4}-Q[1-4]_Q[1-4]$"),    # 2026-Q1_Q3
        re.compile(r"^\d{4}-\d{4}$"),             # 2025-2026
    ]
    if not any(p.match(period) for p in _VALID_PERIODS):
        raise ValueError(
            f"Format de période OSS invalide : '{period}'. "
            "Formats acceptés : '2026-Q1' (trimestriel), '2026-S1' (semestriel), "
            "'2026' (annuel), '2026-Q1_Q3' (plage), '2025-2026' (multi-année)."
        )

    aggregated_data = aggregate_oss_data(results, period=period)

    # ── Garde-fou soldes négatifs (corrections de périodes antérieures) ───
    # Le portail OSS et le XML officiel n'acceptent PAS de montant négatif
    # dans le corps principal de la déclaration (SuppliesPerMemberStateOfConsumption).
    # Un solde négatif par (pays, taux) signifie le plus souvent qu'un avoir
    # de la période dépasse les ventes du même pays/taux — typiquement parce
    # que l'avoir se rapporte à une vente d'une période DÉJÀ déclarée.
    #
    # Version assistée (confirm_corrections=True) : on tente de rattacher
    # chaque avoir responsable à sa vente d'origine via un sale_id identique
    # PRÉSENT DANS `results` (voir suggest_negative_bucket_corrections). Ce
    # rattachement n'est possible que si le fichier importé couvre aussi la
    # période d'origine — sinon l'avoir reste bloqué comme avant. On ne
    # génère JAMAIS de correction sur la base d'order_date seul : c'est un
    # champ informatif (voir models.py), pas une preuve suffisante pour une
    # déclaration fiscale automatisée.
    negative_buckets = find_oss_negative_buckets(aggregated_data)
    corrections_xml: list[NegativeBucketSuggestion] = []
    excluded_refund_ids: set[int] = set()

    if negative_buckets:
        suggestions = suggest_negative_bucket_corrections(results, period=period)
        still_blocking: list[NegativeBucketSuggestion] = []

        for sug in suggestions:
            if confirm_corrections and sug.fully_resolved and sug.matched:
                # Tous les avoirs de ce couple ont une origine identifiée :
                # on les exclut du corps principal et on les reporte dans
                # CorrectionsOfVatReturns par période d'origine.
                corrections_xml.append(sug)
                excluded_refund_ids.update(id(m.refund_result) for m in sug.matched)
            else:
                still_blocking.append(sug)

        if still_blocking:
            details = "\n".join(
                f"  - {COUNTRY_NAMES.get(b.bucket.departure, b.bucket.departure)} → "
                f"{COUNTRY_NAMES.get(b.bucket.arrival, b.bucket.arrival)} ({b.bucket.vat_rate}%) : "
                f"HT={b.bucket.base_ht:.2f} €, TVA={b.bucket.vat_amount:.2f} €"
                + (
                    f" — {len(b.matched)} avoir(s) rattaché(s) automatiquement, "
                    f"{b.unmatched_count} avoir(s) SANS origine identifiée "
                    f"(HT={b.unmatched_ht:.2f} €, TVA={b.unmatched_vat_amount:.2f} €) à traiter manuellement"
                    if b.matched else
                    " — aucun avoir rattachable automatiquement (origine non trouvée dans ce fichier)"
                )
                for b in still_blocking
            )
            raise ValueError(
                "Solde OSS négatif détecté pour la période "
                f"'{period}' sur le(s) couple(s) pays/taux suivant(s) :\n{details}\n\n"
                "Le formulaire OSS n'accepte pas de montant négatif dans le corps "
                "de la déclaration. Cela survient en général quand des avoirs "
                "(remboursements) de la période excèdent les ventes du même "
                "pays/taux, ce qui indique souvent qu'ils se rapportent à une "
                "vente d'une période déjà déclarée — auquel cas il faut les "
                "saisir comme correction de la période d'origine sur le portail "
                "OSS (bloc CorrectionsOfVatReturns), et non les inclure dans "
                "cette déclaration."
                + (
                    "\n\nCertains avoirs n'ont pas pu être rattachés automatiquement "
                    "à une vente d'origine (la vente correspondante n'est pas présente "
                    "dans le fichier importé, ou aucun sale_id identique n'a été trouvé) "
                    "— vérifiez-les manuellement avant de régénérer le XML."
                    if any(b.matched for b in still_blocking) or confirm_corrections
                    else ""
                )
            )

        # Reconstruire l'agrégation du corps principal en excluant les avoirs
        # désormais reportés en correction (pour ne plus les compter deux fois
        # et ne plus laisser de solde négatif dans le corps principal).
        if excluded_refund_ids:
            filtered_results = [r for r in results if id(r) not in excluded_refund_ids]
            aggregated_data = aggregate_oss_data(filtered_results, period=period)

    root = ET.Element(
        "OssVatReturn",
        {
            "xmlns":   "urn:ec.europa.eu:taxud:fiscalis:oss:v1",
            "version": "1.0",
        },
    )

    # ── En-tête ────────────────────────────────────────────────────────────
    header = ET.SubElement(root, "Header")
    ET.SubElement(header, "TraderVatNumber").text = seller_vat
    if intermediary_vat:
        ET.SubElement(header, "IntermediaryVatNumber").text = intermediary_vat
    ET.SubElement(header, "Period").text = period
    ET.SubElement(header, "NationalCurrency").text = "EUR"

    # ── Corps de la déclaration ────────────────────────────────────────────
    details = ET.SubElement(root, "VatReturnDetails")

    # Niveau 1 : pays de DÉPART du stock (un bloc par stock_country)
    for departure_country, destinations in sorted(aggregated_data.items()):
        # Union Scheme : seuls les pays de départ membres de l'UE sont déclarés
        # dans ce bloc SupplyFromMemberState. L'IOSS (départ hors UE) suit une
        # structure différente (IossGoodsSupplies) non supportée ici pour le moment.
        if not is_eu(departure_country):
            continue

        supply_from = ET.SubElement(details, "SupplyFromMemberState")
        ET.SubElement(supply_from, "MemberStateOfSupply").text = departure_country

        # Ajout du numéro de TVA local si différent du pays d'identification (ex: FR)
        # Conformément aux spécifications UE (balise MemberStateOfSupplyVatNumber).
        if local_vat_numbers and departure_country in local_vat_numbers:
            local_num = local_vat_numbers[departure_country]
            if local_num and local_num.strip():
                ET.SubElement(supply_from, "MemberStateOfSupplyVatNumber").text = local_num.strip()

        # Niveau 2 : pays de DESTINATION / consommation
        for arrival_country, rates in sorted(destinations.items()):
            supplies_per_ms = ET.SubElement(
                supply_from, "SuppliesPerMemberStateOfConsumption"
            )
            ET.SubElement(supplies_per_ms, "MemberStateOfConsumption").text = arrival_country

            # Niveau 3 : un bloc GoodsSupplies par taux TVA
            for rate, amounts in sorted(rates.items()):
                goods = ET.SubElement(supplies_per_ms, "GoodsSupplies")
                # Qualifier STANDARD vs REDUCED en comparant au taux standard
                # du pays de consommation (STANDARD_VAT_RATES[arrival_country]).
                # Le seuil fixe >= 15 était fragile : un taux intermédiaire élevé
                # (ex: PT 13%) ou un taux standard inhabituellement bas pouvait
                # être mal classifié.
                std_rate = STANDARD_VAT_RATES.get(arrival_country, Decimal("20"))
                rate_type = "STANDARD" if rate >= std_rate else "REDUCED"
                ET.SubElement(goods, "VatRate", type=rate_type).text = f"{rate:.2f}"
                ET.SubElement(goods, "TaxableAmount").text   = f"{amounts['ht']:.2f}"
                ET.SubElement(goods, "VatAmountIssued").text = f"{amounts['tva']:.2f}"

    # ── Corrections de périodes antérieures (avoirs rattachés automatiquement) ──
    # ⚠️ Structure approximative : la spécification exacte du bloc
    # CorrectionsOfVatReturns (Règl. UE 2021/965) n'a pas pu être vérifiée
    # ici contre le schéma XSD officiel DGFIP/UE — à valider avant tout dépôt
    # réel (comparer avec un export de référence ou la documentation
    # officielle du portail OSS avant le premier envoi utilisant ce bloc).
    if corrections_xml:
        # Regroupement par (période d'origine, pays de consommation, taux)
        grouped: dict[tuple[str, str, Decimal], dict[str, Decimal]] = {}
        for sug in corrections_xml:
            arrival_country = sug.bucket.arrival
            for m in sug.matched:
                gkey = (m.origin_period, arrival_country, sug.bucket.vat_rate)
                gbucket = grouped.setdefault(gkey, {"ht": Decimal("0.00"), "tva": Decimal("0.00")})
                gbucket["ht"]  += m.base_ht
                gbucket["tva"] += m.vat_amount

        for (origin_period, arrival_country, rate), amounts in sorted(grouped.items()):
            corr = ET.SubElement(details, "CorrectionsOfVatReturns")
            ET.SubElement(corr, "MemberStateOfConsumption").text = arrival_country
            ET.SubElement(corr, "ReturnPeriod").text = origin_period
            ET.SubElement(corr, "VatRate").text = f"{rate:.2f}"
            # Montants négatifs ici car il s'agit d'une diminution de la TVA
            # due sur la période d'origine (avoir rattaché a posteriori).
            ET.SubElement(corr, "TaxableAmountCorrection").text = f"{amounts['ht']:.2f}"
            ET.SubElement(corr, "VatAmountCorrection").text = f"{amounts['tva']:.2f}"

    # ── Sérialisation pretty-print ─────────────────────────────────────────
    raw_xml    = ET.tostring(root, encoding="utf-8")
    parsed_xml = minidom.parseString(raw_xml)
    return parsed_xml.toprettyxml(indent="    ", encoding="utf-8")