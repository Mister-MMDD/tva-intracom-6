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
from tva_intracom.rates import STANDARD_VAT_RATES
from tva_intracom.oss_export import aggregate_oss_results, find_oss_negative_buckets, COUNTRY_NAMES

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


def generate_oss_xml(
    results: List[VatResult],
    seller_vat: str,
    period: str,
    intermediary_vat: str | None = None,
) -> bytes:
    """Génère le contenu XML conforme pour la télédéclaration OSS.

    Args:
        results:          liste de VatResult issus du moteur de TVA.
        seller_vat:       numéro TVA de l'assujetti déclarant (ex: "FR12345678901").
        period:           période de déclaration au format OSS (ex: "2026-Q1").
        intermediary_vat: numéro TVA de l'intermédiaire (optionnel, régime IOSS).

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
    # que l'avoir se rapporte à une vente d'une période DÉJÀ déclarée, qui
    # devrait être ventilée dans le bloc CorrectionsOfVatReturns en référençant
    # la période d'origine (Règl. UE 2020/194). L'outil ne conserve pas de lien
    # entre un avoir et la vente d'origine : il ne peut donc pas construire ce
    # bloc automatiquement. On bloque plutôt que de générer un XML invalide ou
    # silencieusement incorrect.
    negative_buckets = find_oss_negative_buckets(aggregated_data)
    if negative_buckets:
        details = "\n".join(
            f"  - {COUNTRY_NAMES.get(b.departure, b.departure)} → "
            f"{COUNTRY_NAMES.get(b.arrival, b.arrival)} ({b.vat_rate}%) : "
            f"HT={b.base_ht:.2f} €, TVA={b.vat_amount:.2f} €"
            for b in negative_buckets
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
            "cette déclaration. Vérifiez les avoirs concernés avant de "
            "régénérer le XML."
        )

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
        supply_from = ET.SubElement(details, "SupplyFromMemberState")
        ET.SubElement(supply_from, "MemberStateOfSupply").text = departure_country

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

    # ── Sérialisation pretty-print ─────────────────────────────────────────
    raw_xml    = ET.tostring(root, encoding="utf-8")
    parsed_xml = minidom.parseString(raw_xml)
    return parsed_xml.toprettyxml(indent="    ", encoding="utf-8")