"""
Module CA3 — Déclaration nationale française TVA (Cerfa n°3310-CA3-SD).

⚠️ CORRECTION IMPORTANTE (voir README/Roadmap) : les références de lignes
Cerfa de ce module ont été revérifiées contre le formulaire officiel
3310-CA3-SD (téléformulaire 2026, cadres A et B) et corrigées comme suit :
  - Ventes domestiques FR              → case A1 (0979), pas "01"
  - Livraisons intracom B2B exonérées  → case F2 (0034), pas "02"
  - Exportations hors UE               → case E1 (0032), pas "14"
  - AIC — base (opération réalisée)    → case B2 (0031) — absente avant
  - AIC — mémo TVA due                 → Ligne 17 (0035) — absente avant
  - Taux normal 20 %                   → Ligne 08 (0207), pas "Ligne 20"
  - Taux réduit 5,5 %                  → Ligne 09 (0105), pas "Ligne 22"
  - Taux intermédiaire 10 %            → Ligne 9B (0151), pas "Ligne 25"
  - Taux particulier 2,1 % (métropole) → Ligne T6 (1010), pas "Ligne 24"
  - Déduction immobilisations          → Ligne 19 (0703), pas "Ligne 20"
  - Déduction autres biens/services    → Ligne 20 (0702), pas "Ligne 21"
  - Crédit période précédente          → Ligne 22 (8001), pas "Ligne 27"
    (Ligne 27 réelle est la sortie "crédit à reporter" de la période
    COURANTE vers la période SUIVANTE — pas l'entrée du crédit précédent.)
La TVA déductible sur AIC (art. 272 CGI) est désormais intégrée dans la
Ligne 20 (Autres biens et services) plutôt qu'affichée comme une ligne "08"
séparée qui n'existe pas côté déductible sur le formulaire réel.

Améliorations v2 :
- Acquisitions intracommunautaires assimilées (AIC FBA, art. 17
  Dir. 2006/112/CE) calculées depuis les mouvements de stock FC Transfer.
- Section C : Déductions — TVA déductible sur immobilisations (Ligne 19),
  TVA déductible sur autres biens/services (Ligne 20, inclut l'AIC
  déductible), crédit de taxe de la période précédente (Ligne 22). Ces
  montants ne peuvent pas être déduits automatiquement depuis les fichiers
  Amazon (données d'achats indisponibles) : l'utilisateur les saisit comme
  paramètres.
- Section D : Solde net à payer / crédit à reporter.
- Note informative ligne 3A (opérations OSS déclarées sur portail séparé —
  ce n'est pas une case du Cerfa CA3 lui-même, c'est le régime OSS qui est
  hors CA3 par nature ; la mention "3A" est une convention interne du
  rapport pour signaler ce rapprochement, pas une référence officielle).

⚠️ Limites connues non couvertes par ce module :
  - Le cas DOM (taux 8,5 % / 2,1 % via lignes 10/11) n'est PAS géré : ce
    module suppose un vendeur établi en France métropolitaine. Un
    seller_country en DOM produirait des références de ligne incorrectes.
  - La ligne T6 (taux particulier 2,1 % métropole — presse, médicaments
    remboursables) est un cas rare ; à vérifier au cas par cas si utilisée.

ROADMAP — export EDI-TVA (télédéclaration) :
Ce module ne génère aujourd'hui qu'un rapport HTML (`generate_ca3_html_report_v2`),
destiné à la SAISIE MANUELLE sur le portail impots.gouv.fr (mode EFI) ou par un
cabinet comptable. Il n'existe pas d'export au format EDI-TVA (norme d'échange
utilisée en mode EDI par les partenaires EDI homologués DGFIP pour la
télétransmission directe des CA3). Ajouter ce format nécessiterait :
  - le mapping des lignes CA3 vers le schéma EDI-TVA (cahier des charges DGFIP,
    non fourni avec ce dépôt — à obtenir auprès de la DGFIP ou d'un partenaire
    EDI homologué),
  - une homologation ou un partenariat avec un opérateur EDI existant (la
    télétransmission directe à la DGFIP n'est pas ouverte à un éditeur non
    homologué sans passer par un partenaire EDI),
  - une gestion de la signature/authentification propre au canal EDI.
Non implémenté dans cette version — voir README, section Roadmap.
"""

from __future__ import annotations

import logging
from decimal import Decimal, ROUND_HALF_UP
from typing import List, Dict, Optional

from tva_intracom.models import VatResult, Scenario
from tva_intracom.i18n import _

logger = logging.getLogger(__name__)


def _round(amount: Decimal) -> Decimal:
    return amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


# ---------------------------------------------------------------------------
# Estimation valeur AIC depuis les FC Transfers (même logique que excel_report)
# ---------------------------------------------------------------------------

def _asin_avg_price_from_results(results: List[VatResult]) -> Dict[str, Decimal]:
    """Prix de vente HT moyen par ASIN (approximation valeur d'achat — art. 83 dir.)."""
    totals: Dict[str, list] = {}
    for r in results:
        asin = getattr(r.sale, "asin", "").strip()
        amt  = r.sale.amount_ht
        if asin and amt > Decimal("0"):
            totals.setdefault(asin, []).append(amt)
    return {
        a: sum(v, Decimal("0")) / Decimal(str(len(v)))
        for a, v in totals.items() if v
    }


def _compute_aic_from_fc_transfers(
        all_fc_transfers: list,
        results: List[VatResult],
        seller_country: str = "FR",
) -> tuple[Decimal, Decimal]:
    """Calcule la base AIC et la TVA AIC estimées pour la CA3.

    Périmètre : flux ENTRANT vers seller_country (introductions).
    Retourne (base_aic_ht, tva_aic) — nets cumulés sur la période.

    ⚠ Valeur estimée : prix de vente moyen HT × qté (art. 83 impose la
    valeur d'achat, inconnue depuis Amazon). Approximation par excès.
    """
    from tva_intracom.rates import vat_rate as _vat_rate, STANDARD_VAT_RATES

    avg_price = _asin_avg_price_from_results(results)
    base_aic  = Decimal("0.00")
    tva_aic   = Decimal("0.00")

    for t in all_fc_transfers:
        dep = (t.get("DEPARTURE_COUNTRY") or t.get("departure_country") or
               t.get("SALE_DEPART_COUNTRY") or t.get("sale_depart_country") or "").strip().upper()
        arr = (t.get("ARRIVAL_COUNTRY") or t.get("arrival_country") or
               t.get("SALE_ARRIVAL_COUNTRY") or t.get("sale_arrival_country") or "").strip().upper()
        if arr != seller_country.upper() or dep == arr:
            continue
        asin = (t.get("ASIN") or t.get("asin") or "").strip()
        try:
            qty = int(float(t.get("QTY") or t.get("qty") or 1))
        except (ValueError, TypeError):
            qty = 1
        avg = avg_price.get(asin, Decimal("0"))
        ligne_base = _round(Decimal(str(qty)) * avg)
        taux = _vat_rate(seller_country, "STANDARD") if seller_country in STANDARD_VAT_RATES else Decimal("20")
        ligne_tva  = _round(ligne_base * taux / Decimal("100"))
        base_aic  += ligne_base
        tva_aic   += ligne_tva

    return _round(base_aic), _round(tva_aic)


# ---------------------------------------------------------------------------
# Calcul des lignes Cerfa
# ---------------------------------------------------------------------------

def compute_ca3_lines_v2(
        results: List[VatResult],
        refund_results: Optional[List[VatResult]] = None,
        all_fc_transfers: Optional[list] = None,
        tva_deductible_immos:    Decimal = Decimal("0.00"),
        tva_deductible_autres:   Decimal = Decimal("0.00"),
        credit_periode_precedente: Decimal = Decimal("0.00"),
        seller_country: str = "FR",
) -> Dict[str, Decimal]:
    """Calcule les montants des lignes du formulaire Cerfa CA3.

    Références vérifiées contre le Cerfa 3310-CA3-SD officiel (cadres A et B) :

    Cadre A — Opérations réalisées (chiffre d'affaires HT) :
      A1  Ventes, prestations de services — ventes domestiques FR (0979)
      F2  Livraisons intracommunautaires B2B exonérées, départ seller_country (0034)
      E1  Exportations hors UE, départ seller_country (0032)
      B2  Acquisitions intracommunautaires — base AIC FBA (0031)

    Cadre B — TVA brute, opérations réalisées en France métropolitaine :
      L08  Taux normal 20 % (0207) — inclut la part AIC (voir note ci-dessous)
      L09  Taux réduit 5,5 % (0105)
      L9B  Taux réduit 10 % (0151)
      LT6  Taux particulier 2,1 % métropole — presse, médicaments remboursables (1010)
      L17  Mémo "Dont TVA sur acquisitions intracommunautaires" (0035)

    Lignes saisies par l'utilisateur (données d'achat indisponibles depuis Amazon) :
      L19  TVA déductible sur immobilisations (0703)
      L20  TVA déductible sur autres biens et services (0702) — inclut l'AIC déductible
      L22  Crédit de taxe de la période précédente (8001)

    NOTE AIC : l'acquisition intracommunautaire assimilée (transfert de stock
    FBA entrant, art. 17 Dir. 2006/112/CE) est déclarée à DEUX endroits
    distincts du formulaire, comme l'exige le Cerfa réel :
      1. Sa base HT figure en case B2 (0031), comme "opération réalisée"
         distincte des ventes (A1/F2/E1).
      2. Sa TVA (collectée ET déductible simultanément, effet net nul —
         art. 272 CGI) est ADDITIONNÉE dans la décomposition par taux
         (L08 Taux normal 20 %, en supposant — comme approximation — que
         le stock transféré est taxé au taux standard), avec un mémo
         séparé en Ligne 17 indiquant la part de la TVA brute totale qui
         provient spécifiquement de l'AIC. Côté déductible, le même
         montant est intégré dans L20 (Autres biens et services).
    Cette convention "AIC toujours au taux standard" est une approximation
    documentée (comme la valorisation par prix de vente moyen) — à corriger
    manuellement si une part du stock transféré relève d'un taux réduit.

    Chaque ligne "brute" ci-dessus (A1, F2, E1, L08, L09, LT6, L9B) est en
    réalité décomposée en trois variantes dans le dict retourné, pour
    permettre un affichage vente / avoir / net séparé (voir
    generate_ca3_html_report_v2) :
      "<ligne>_base_vente" / "<ligne>_tva_vente" : ventes seules (brut)
      "<ligne>_base_remb"  / "<ligne>_tva_remb"  : avoirs seuls (négatif)
      "<ligne>_base_ht"    / "<ligne>_tva_due"   : net (vente + remb) — clés
        historiques, ce sont elles qui doivent figurer sur le Cerfa officiel.
    B2 (AIC) n'a pas de variante avoir : les transferts de stock FBA ne
    sont jamais remboursés.
    """
    _BASE_LINES = ("A1", "F2", "E1")
    _RATE_LINES = ("L08", "L09", "LT6", "L9B")

    lines: Dict[str, Decimal] = {
        "B2_base_ht":   Decimal("0.00"),   # AIC — base (cadre A, case B2)
        "L17_tva_aic":  Decimal("0.00"),   # AIC — mémo TVA (Ligne 17)
        # Déductions (saisies)
        "L19_tva_ded":  _round(tva_deductible_immos),
        "L20_tva_ded":  _round(tva_deductible_autres),   # + AIC déductible ajouté plus bas
        "L22_credit":   _round(credit_periode_precedente),
    }
    for k in _BASE_LINES:
        lines[f"{k}_base_vente"] = Decimal("0.00")
        lines[f"{k}_base_remb"]  = Decimal("0.00")
        lines[f"{k}_base_ht"]    = Decimal("0.00")
    for k in _RATE_LINES:
        lines[f"{k}_base_vente"] = Decimal("0.00")
        lines[f"{k}_tva_vente"]  = Decimal("0.00")
        lines[f"{k}_base_remb"]  = Decimal("0.00")
        lines[f"{k}_tva_remb"]   = Decimal("0.00")
        lines[f"{k}_base_ht"]    = Decimal("0.00")
        lines[f"{k}_tva_due"]    = Decimal("0.00")

    def _aggregate(res: VatResult, is_refund: bool) -> None:
        stock_from_seller = res.sale.stock_country == seller_country.upper()
        buyer_in_seller   = res.sale.buyer_country == seller_country.upper()
        suffix = "remb" if is_refund else "vente"

        if res.scenario == Scenario.DOMESTIC and stock_from_seller and (
                buyer_in_seller or res.sale.buyer_country == "MC"
        ):
            # Monaco (MC) : assimilé au territoire français pour la TVA
            # (convention fiscale franco-monégasque du 18 mai 1963) — le
            # moteur (engine.py) classe déjà ces ventes en DOMESTIC/FR_DOMESTIC,
            # mais buyer_country reste "MC" (pas "FR") : on l'inclut donc
            # explicitement ici, sinon ces ventes disparaîtraient du rapport CA3.
            amt  = res.sale.amount_ht
            tva  = res.vat_amount
            rate = res.vat_rate
            lines[f"A1_base_{suffix}"] += amt
            if rate in (Decimal("20"), Decimal("20.00")):
                bucket = "L08"
            elif rate in (Decimal("5.5"), Decimal("5.50")):
                bucket = "L09"
            elif rate in (Decimal("2.1"), Decimal("2.10")):
                bucket = "LT6"
            elif rate in (Decimal("10"), Decimal("10.00")):
                bucket = "L9B"
            else:
                logger.warning("CA3 v2 : taux %.2f%% non mappé (sale_id=%s) → Ligne 08.",
                               float(rate), res.sale.sale_id)
                bucket = "L08"
            lines[f"{bucket}_base_{suffix}"] += amt
            lines[f"{bucket}_tva_{suffix}"]  += tva

        elif res.scenario == Scenario.B2B_REVERSE_CHARGE and stock_from_seller:
            lines[f"F2_base_{suffix}"] += res.sale.amount_ht

        elif res.scenario == Scenario.EXPORT and stock_from_seller:
            lines[f"E1_base_{suffix}"] += res.sale.amount_ht

    for res in results:
        _aggregate(res, is_refund=False)
    for res in (refund_results or []):
        _aggregate(res, is_refund=True)

    # Reconstruction des totaux nets (vente + avoir) — ce sont ces clés
    # historiques qui doivent être utilisées pour le Cerfa officiel.
    for k in _BASE_LINES:
        lines[f"{k}_base_ht"] = lines[f"{k}_base_vente"] + lines[f"{k}_base_remb"]
    for k in _RATE_LINES:
        lines[f"{k}_base_ht"] = lines[f"{k}_base_vente"] + lines[f"{k}_base_remb"]
        lines[f"{k}_tva_due"] = lines[f"{k}_tva_vente"]  + lines[f"{k}_tva_remb"]

    # Case B2 + Ligne 17 : AIC depuis les FC Transfers entrant (jamais d'avoir
    # sur les transferts de stock FBA — pas de variante remb). Approximation :
    # la totalité de l'AIC est supposée au taux standard, donc additionnée
    # dans L08 (base + TVA), avec un mémo distinct en Ligne 17.
    if all_fc_transfers:
        b, t = _compute_aic_from_fc_transfers(all_fc_transfers, results, seller_country)
        lines["B2_base_ht"]  = b
        lines["L17_tva_aic"] = t
        lines["L08_base_ht"] += b
        lines["L08_tva_due"] += t
        # TVA déductible sur AIC (art. 272 CGI, déduction immédiate si le
        # stock transféré est destiné à la revente) — intégrée dans la
        # Ligne 20 (Autres biens et services), pas une ligne séparée.
        lines["L20_tva_ded"] += t

    for k in lines:
        lines[k] = _round(lines[k])

    return lines


# ---------------------------------------------------------------------------
# Génération HTML
# ---------------------------------------------------------------------------

def generate_ca3_html_report_v2(
        results: List[VatResult],
        company_name: str,
        siren: str,
        period_label: str,
        refund_results: Optional[List[VatResult]] = None,
        all_fc_transfers: Optional[list] = None,
        tva_deductible_immos:      Decimal = Decimal("0.00"),
        tva_deductible_autres:     Decimal = Decimal("0.00"),
        credit_periode_precedente: Decimal = Decimal("0.00"),
        seller_country: str = "FR",
) -> str:
    """Génère le rapport HTML de contrôle CA3 — version 3 (multi-taux + AIC + déductions)."""

    lines = compute_ca3_lines_v2(
        results, refund_results,
        all_fc_transfers=all_fc_transfers,
        tva_deductible_immos=tva_deductible_immos,
        tva_deductible_autres=tva_deductible_autres,
        credit_periode_precedente=credit_periode_precedente,
        seller_country=seller_country,
    )

    total_ca_ht   = lines["A1_base_ht"] + lines["F2_base_ht"] + lines["B2_base_ht"] + lines["E1_base_ht"]
    tva_brute_due = lines["L08_tva_due"] + lines["L09_tva_due"] + lines["LT6_tva_due"] + lines["L9B_tva_due"]
    # La TVA AIC (Ligne 17) est déjà incluse dans L08_tva_due (voir
    # compute_ca3_lines_v2) — tva_brute_due est donc déjà "avec AIC" ; on
    # garde ce nom de variable pour ne pas casser le reste du calcul du
    # solde, mais il n'y a plus de double-comptage à faire ici.
    tva_brute_due_avec_aic = tva_brute_due

    total_ded = lines["L19_tva_ded"] + lines["L20_tva_ded"] + lines["L22_credit"]
    # Note : la TVA déductible sur AIC est déjà incluse dans L20_tva_ded
    # (voir compute_ca3_lines_v2) — même montant que la part perçue via
    # Ligne 17, donc pas d'impact net sur le solde final.

    solde = _round(tva_brute_due_avec_aic - total_ded)
    solde_label = (_("ca3_solde_to_pay") if solde >= 0 else _("ca3_solde_credit"))
    solde_color = ("#C00000" if solde >= 0 else "#375623")

    oss_base = sum(
        r.sale.amount_ht for r in results
        if r.scenario == Scenario.OSS_B2C and r.sale.stock_country == seller_country.upper()
    )
    oss_tva = sum(
        r.vat_amount for r in results
        if r.scenario == Scenario.OSS_B2C and r.sale.stock_country == seller_country.upper()
    )

    has_aic  = lines["B2_base_ht"] > 0
    has_l09  = lines["L09_base_ht"] != 0 or lines["L09_base_vente"] != 0 or lines["L09_base_remb"] != 0
    has_lt6  = lines["LT6_base_ht"] != 0 or lines["LT6_base_vente"] != 0 or lines["LT6_base_remb"] != 0
    has_l9b  = lines["L9B_base_ht"] != 0 or lines["L9B_base_vente"] != 0 or lines["L9B_base_remb"] != 0
    has_ded  = any(lines[k] > 0 for k in ("L19_tva_ded", "L20_tva_ded", "L22_credit"))

    # Totaux vente / avoir (hors AIC — l'AIC n'a pas de variante avoir)
    _RATE_LINES = ("L08", "L09", "LT6", "L9B")
    tva_vente_total  = sum(lines[f"{k}_tva_vente"]  for k in _RATE_LINES)
    tva_remb_total   = sum(lines[f"{k}_tva_remb"]   for k in _RATE_LINES)
    base_vente_total = sum(lines[f"{k}_base_vente"] for k in _RATE_LINES)
    base_remb_total  = sum(lines[f"{k}_base_remb"]  for k in _RATE_LINES)
    has_remb = any(
        lines[f"{k}_base_remb"] != 0
        for k in ("A1", "F2", "E1", "L08", "L09", "LT6", "L9B")
    )

    # Base nette TOTALE, cohérente avec tva_brute_due (donc AIC INCLUS) :
    # L08_base_ht contient déjà la base AIC (voir compute_ca3_lines_v2),
    # donc sommer les *_base_ht des lignes de taux donne une base nette qui
    # inclut l'AIC — contrairement à (base_vente_total + base_remb_total)
    # qui, elle, exclut l'AIC (calculée uniquement à partir de *_base_vente
    # et *_base_remb, jamais touchées par l'ajout de l'AIC). Utiliser cette
    # dernière pour la ligne "TOTAL" produisait une base nette (46 273,68)
    # incohérente avec une TVA nette (9 369,75) qui, elle, incluait l'AIC.
    base_net_total_avec_aic = sum(lines[f"{k}_base_ht"] for k in _RATE_LINES)

    # TVA nette "hors AIC" : la TVA brute due (Ligne 16, AIC inclus) moins
    # UNIQUEMENT la part AIC (Ligne 17) — pas les autres déductions (L19,
    # L20 hors AIC, L22), qui restent affichées séparément en section C/D.
    # Sert à afficher, à côté du total "AIC inclus", un second total "AIC
    # déduit" directement lisible dans le tableau B, sans attendre la
    # section D (qui elle défalque TOUTES les déductions, pas seulement AIC).
    tva_nette_hors_aic = _round(tva_brute_due_avec_aic - lines["L17_tva_aic"])
    base_nette_hors_aic = _round(base_net_total_avec_aic - lines["B2_base_ht"])

    def _fmt(v: Decimal) -> str:
        return f"{v:,.2f}"

    CSS = """
        @page { size: A4; margin: 20mm 15mm; }
        * { box-sizing: border-box; }
        body { font-family: Arial, sans-serif; color: #2c3e50; font-size: 10pt; margin:0; padding:0; }
        .hdr-banner { border-bottom: 3px solid #1f4e79; padding-bottom:10px; margin-bottom:20px; }
        .title { font-size:18pt; font-weight:bold; color:#1f4e79; margin:0 0 4px 0; }
        .subtitle { font-size:10pt; color:#7f8c8d; margin:0; letter-spacing:1px; text-transform:uppercase; }
        .meta { background:#f8f9fa; border:1px solid #e9ecef; padding:12px; margin-bottom:20px; border-radius:4px;
                display:table; width:100%; }
        .meta-r { display:table-row; }
        .ml { display:table-cell; font-weight:bold; color:#495057; padding:4px 10px 4px 0; width:22%; }
        .mv { display:table-cell; color:#212529; padding:4px 0; width:28%; }
        h2 { font-size:11pt; color:#1f4e79; border-left:4px solid #1f4e79; padding-left:8px;
             margin:22px 0 12px; text-transform:uppercase; }
        table.t { width:100%; border-collapse:collapse; margin-bottom:16px; }
        table.t th { background:#1f4e79; color:#fff; font-weight:bold; padding:7px 9px;
                     font-size:9pt; border:1px solid #1f4e79; }
        table.t td { padding:7px 9px; border:1px solid #dee2e6; font-size:9pt; }
        table.t tr:nth-child(even) td { background:#f8f9fa; }
        .tr { text-align:right !important; }
        .tc { text-align:center !important; }
        .cb { background:#e9ecef; font-weight:bold; font-family:monospace; padding:2px 5px; border-radius:3px; }
        .tot td { font-weight:bold; background:#eaeded !important; border-top:2px solid #1f4e79 !important; }
        .oss-note { background:#fff3cd; border:1px solid #ffc107; padding:10px 12px;
                    border-radius:4px; font-size:9pt; margin-bottom:16px; }
        .aic-note { background:#e8f4f8; border:1px solid #17a2b8; padding:10px 12px;
                    border-radius:4px; font-size:9pt; margin-bottom:16px; }
        .solde-box { border:2px solid; padding:14px 18px; border-radius:6px; margin-top:18px;
                     font-size:12pt; font-weight:bold; text-align:center; }
        .notice { font-size:8pt; color:#7f8c8d; margin-top:28px; padding:10px;
                  border-top:1px solid #dee2e6; }
    """

    OSS_BLOC = ""
    if oss_base > 0:
        OSS_BLOC = f"""
        <div class="oss-note">
            <strong>{_("ca3_oss_note_title")}</strong>
            {_("ca3_oss_note_text", country=seller_country)}<br>
            {_("ca3_oss_base_ht")} <strong>{_fmt(oss_base)} €</strong> —
            {_("ca3_oss_vat_declared")} <strong>{_fmt(oss_tva)} €</strong>
        </div>"""

    AIC_BLOC = ""
    if has_aic:
        AIC_BLOC = f"""
        <div class="aic-note">
            <strong>{_("ca3_aic_note_title")}</strong>
            {_("ca3_aic_note_text", country=seller_country)}
            {_("ca3_aic_note_warning")}
        </div>"""

    B2_ROW = ""
    if has_aic:
        B2_ROW = f"""
            <tr>
                <td class="tc"><span class="cb">{_("ca3_box_prefix")} B2</span></td>
                <td>{_("ca3_row_b2_label", country=seller_country)}
                    <br><small>{_("ca3_row_b2_small")}</small></td>
                <td class="tr">{_fmt(lines['B2_base_ht'])}</td>
                <td class="tr">—</td>
                <td class="tr">{_fmt(lines['B2_base_ht'])}</td>
            </tr>"""

    def _rate_row(cadre: str, label: str, key: str) -> str:
        return f"""
            <tr>
                <td class="tc"><span class="cb">{_("ca3_line_prefix")} {cadre}</span></td>
                <td><strong>{label}</strong></td>
                <td class="tr">{_fmt(lines[f'{key}_base_vente'])}</td>
                <td class="tr">{_fmt(lines[f'{key}_tva_vente'])}</td>
                <td class="tr">{_fmt(lines[f'{key}_base_remb'])}</td>
                <td class="tr">{_fmt(lines[f'{key}_tva_remb'])}</td>
                <td class="tr">{_fmt(lines[f'{key}_base_ht'])}</td>
                <td class="tr">{_fmt(lines[f'{key}_tva_due'])}</td>
            </tr>"""

    L08_ROW = _rate_row("08", _("ca3_row_l08_label"), "L08")

    L09_ROW = _rate_row("09", _("ca3_row_l09_label"), "L09") if has_l09 else ""

    LT6_ROW = _rate_row("T6", _("ca3_row_lt6_label"), "LT6") if has_lt6 else ""

    L9B_ROW = _rate_row("9B", _("ca3_row_l9b_label"), "L9B") if has_l9b else ""

    L17_MEMO_ROW = f"""
            <tr>
                <td class="tc"><span class="cb">{_("ca3_line_prefix")} 17</span></td>
                <td><strong>{_("ca3_row_l17_label")}</strong>
                    {_("ca3_row_l17_desc")}</td>
                <td class="tr">{_fmt(lines['B2_base_ht'])}</td>
                <td class="tr">{_fmt(lines['L17_tva_aic'])}</td>
                <td class="tr">—</td>
                <td class="tr">—</td>
                <td class="tr">{_fmt(lines['B2_base_ht'])}</td>
                <td class="tr">{_fmt(lines['L17_tva_aic'])}</td>
            </tr>""" if has_aic else ""

    DED_SECTION = ""
    if has_ded or has_aic:
        L19d = f"""
                <tr>
                    <td class="tc"><span class="cb">{_("ca3_line_prefix")} 19</span></td>
                    <td>{_("ca3_row_l19_label")}</td>
                    <td class="tr">{_fmt(lines['L19_tva_ded'])}</td>
                </tr>""" if lines["L19_tva_ded"] > 0 else ""
        L20d = f"""
                <tr>
                    <td class="tc"><span class="cb">{_("ca3_line_prefix")} 20</span></td>
                    <td>{_("ca3_row_l20_label")}
                        {'<br><small>' + _("ca3_row_l20_aic_small") + '</small>' if has_aic else ''}</td>
                    <td class="tr">{_fmt(lines['L20_tva_ded'])}</td>
                </tr>""" if lines["L20_tva_ded"] > 0 else ""
        L22d  = f"""
                <tr>
                    <td class="tc"><span class="cb">{_("ca3_line_prefix")} 22</span></td>
                    <td>{_("ca3_row_l22_label")}</td>
                    <td class="tr">{_fmt(lines['L22_credit'])}</td>
                </tr>""" if lines["L22_credit"] > 0 else ""

        NOTE_DED = ""
        if not has_ded and has_aic:
            NOTE_DED = f"""<tr><td colspan="3" style="font-style:italic;color:#7f8c8d;font-size:8.5pt;padding:6px 9px;">
                {_("ca3_ded_warning")}</td></tr>"""

        DED_SECTION = f"""
    <h2>{_("ca3_sec_c_title")}</h2>
    <table class="t">
        <thead>
            <tr>
                <th style="width:15%;">{_("ca3_col_cadre")}</th>
                <th style="width:60%;">{_("ca3_col_c_nature")}</th>
                <th style="width:25%;text-align:right;">{_("ca3_col_c_amount")}</th>
            </tr>
        </thead>
        <tbody>
            {L19d}{L20d}{L22d}{NOTE_DED}
            <tr class="tot">
                <td class="tc">-</td>
                <td>{_("ca3_total_deductible")}</td>
                <td class="tr">{_fmt(total_ded)}</td>
            </tr>
        </tbody>
    </table>"""

    SOLDE_SECTION = f"""
    <h2>{_("ca3_sec_d_title")}</h2>
    <div class="solde-box" style="color:{solde_color}; border-color:{solde_color};">
        {solde_label} : {_fmt(abs(solde))} EUR<br>
        <small style="font-weight:normal;font-size:9pt;">
            {_("ca3_solde_formula", aic=_fmt(lines['L17_tva_aic']), brute=_fmt(tva_brute_due_avec_aic), ded=_fmt(total_ded))}
        </small>
    </div>"""

    return f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <title>{_("ca3_report_title", company=company_name, period=period_label)}</title>
    <style>{CSS}</style>
</head>
<body>
    <div class="hdr-banner">
        <h1 class="title">{_("ca3_main_title")}</h1>
        <p class="subtitle">{_("ca3_subtitle", country=seller_country)}</p>
    </div>

    <div class="meta">
        <div class="meta-r">
            <div class="ml">{_("ca3_meta_company")}</div><div class="mv">{company_name}</div>
            <div class="ml">{_("ca3_meta_period")}</div><div class="mv">{period_label}</div>
        </div>
        <div class="meta-r">
            <div class="ml">{_("ca3_meta_siren")}</div><div class="mv">{siren}</div>
            <div class="ml">{_("ca3_meta_currency")}</div><div class="mv">{_("ca3_meta_euro")}</div>
        </div>
    </div>

    {OSS_BLOC}
    {AIC_BLOC}

    <h2>{_("ca3_sec_a_title")}</h2>
    <table class="t">
        <thead>
            <tr>
                <th style="width:12%;">{_("ca3_col_framework")}</th>
                <th style="width:34%;">{_("ca3_col_nature")}</th>
                <th style="width:18%;text-align:right;">{_("ca3_col_base_sales")}</th>
                <th style="width:18%;text-align:right;">{_("ca3_col_base_refunds_header")}</th>
                <th style="width:18%;text-align:right;">{_("ca3_col_base_net")}</th>
            </tr>
        </thead>
        <tbody>
            <tr>
                <td class="tc"><span class="cb">{_("ca3_box_prefix")} A1</span></td>
                <td>{_("ca3_row_a1_label", country=seller_country)}</td>
                <td class="tr">{_fmt(lines['A1_base_vente'])}</td>
                <td class="tr">{_fmt(lines['A1_base_remb'])}</td>
                <td class="tr">{_fmt(lines['A1_base_ht'])}</td>
            </tr>
            <tr>
                <td class="tc"><span class="cb">{_("ca3_box_prefix")} F2</span></td>
                <td>{_("ca3_row_f2_label", country=seller_country)}</td>
                <td class="tr">{_fmt(lines['F2_base_vente'])}</td>
                <td class="tr">{_fmt(lines['F2_base_remb'])}</td>
                <td class="tr">{_fmt(lines['F2_base_ht'])}</td>
            </tr>
            {B2_ROW}
            <tr>
                <td class="tc"><span class="cb">{_("ca3_box_prefix")} E1</span></td>
                <td>{_("ca3_row_e1_label", country=seller_country)}</td>
                <td class="tr">{_fmt(lines['E1_base_vente'])}</td>
                <td class="tr">{_fmt(lines['E1_base_remb'])}</td>
                <td class="tr">{_fmt(lines['E1_base_ht'])}</td>
            </tr>
            <tr class="tot">
                <td class="tc">—</td>
                <td>{_("ca3_total_ca")}</td>
                <td class="tr">{_fmt(lines['A1_base_vente'] + lines['F2_base_vente'] + lines['B2_base_ht'] + lines['E1_base_vente'])}</td>
                <td class="tr">{_fmt(lines['A1_base_remb'] + lines['F2_base_remb'] + lines['E1_base_remb'])}</td>
                <td class="tr">{_fmt(total_ca_ht)}</td>
            </tr>
        </tbody>
    </table>
    {f'<p style="font-size:8.5pt;color:#7f8c8d;margin:-8px 0 16px;">{_("ca3_remb_note")}</p>' if has_remb else ''}

    <h2>{_("ca3_sec_b_title")}</h2>
    <table class="t">
        <thead>
            <tr>
                <th style="width:9%;">{_("ca3_col_cadre")}</th>
                <th style="width:23%;">{_("ca3_col_section")}</th>
                <th style="width:13%;text-align:right;">{_("ca3_col_b_base_sales")}</th>
                <th style="width:13%;text-align:right;">{_("ca3_col_b_vat_sales")}</th>
                <th style="width:13%;text-align:right;">{_("ca3_col_b_base_refunds")}</th>
                <th style="width:13%;text-align:right;">{_("ca3_col_b_vat_refunds")}</th>
                <th style="width:13%;text-align:right;">{_("ca3_col_b_base_net")}</th>
                <th style="width:13%;text-align:right;">{_("ca3_col_b_vat_net")}</th>
            </tr>
        </thead>
        <tbody>
            {L08_ROW}{L09_ROW}{LT6_ROW}{L9B_ROW}{L17_MEMO_ROW}
            <tr class="tot">
                <td class="tc">—</td>
                <td>{_("ca3_total_aic_included")}</td>
                <td class="tr">{_fmt(base_vente_total)}</td>
                <td class="tr">{_fmt(tva_vente_total)}</td>
                <td class="tr">{_fmt(base_remb_total)}</td>
                <td class="tr">{_fmt(tva_remb_total)}</td>
                <td class="tr">{_fmt(base_net_total_avec_aic)}</td>
                <td class="tr">{_fmt(tva_brute_due_avec_aic)}</td>
            </tr>
            <tr class="tot">
                <td class="tc">—</td>
                <td colspan="4">{_("ca3_total_aic_deducted")}</td>
                <td class="tr">{_fmt(base_nette_hors_aic)}</td>
                <td class="tr">{_fmt(tva_nette_hors_aic)}</td>
            </tr>
        </tbody>
    </table>
    {f'<p style="font-size:8.5pt;color:#7f8c8d;margin:-8px 0 16px;">{_("ca3_vat_remb_note")}</p>' if has_remb else ''}
    <p style="font-size:8.5pt;color:#7f8c8d;margin:-8px 0 16px;">{_("ca3_aic_check_note")}</p>

    {DED_SECTION}
    {SOLDE_SECTION}

    <div class="notice">
        <strong>{_("ca3_notice_title")}</strong> {_("ca3_notice_text", country=seller_country)}
    </div>
</body>
</html>"""
