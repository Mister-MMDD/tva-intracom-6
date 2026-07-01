"""
Module CA3 v3 — Déclaration nationale française TVA (Cerfa n°3310-CA3-SD).

Améliorations v3 vs v2 :
- Ligne 08 : Acquisitions intracommunautaires assimilées (AIC FBA, art. 17
  Dir. 2006/112/CE) calculées depuis les mouvements de stock FC Transfer.
- Section C : Déductions — TVA déductible sur immobilisations (ligne 20 ded.),
  TVA déductible sur autres biens/services (ligne 21 ded.), crédit de taxe
  de la période précédente (ligne 27).  Ces montants ne peuvent pas être
  déduits automatiquement depuis les fichiers Amazon (données d'achats
  indisponibles) : l'utilisateur les saisit comme paramètres.
- Section D : Solde net à payer / crédit à reporter.
- Note informative ligne 3A (opérations OSS déclarées sur portail séparé).
"""

from __future__ import annotations

import logging
from decimal import Decimal, ROUND_HALF_UP
from typing import List, Dict, Optional

from tva_intracom.models import VatResult, Scenario

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

def compute_ca3_lines_v3(
    results: List[VatResult],
    refund_results: Optional[List[VatResult]] = None,
    all_fc_transfers: Optional[list] = None,
    tva_deductible_immos:    Decimal = Decimal("0.00"),
    tva_deductible_autres:   Decimal = Decimal("0.00"),
    credit_periode_precedente: Decimal = Decimal("0.00"),
    seller_country: str = "FR",
) -> Dict[str, Decimal]:
    """Calcule les montants des lignes du formulaire Cerfa CA3.

    Lignes calculées automatiquement :
      01  Ventes domestiques imposables (net avoirs)
      02  Livraisons intracommunautaires B2B exonérées (départ seller_country)
      08  Acquisitions intracommunautaires assimilées (AIC FBA, art. 17 Dir.)
      14  Exportations hors UE (départ seller_country)
      20  Base + TVA taux normal 20 %
      22  Base + TVA taux réduit 5,5 %
      24  Base + TVA taux super-réduit 2,1 %
      25  Base + TVA taux intermédiaire 10 %

    Lignes saisies par l'utilisateur (données d'achat indisponibles depuis Amazon) :
      20d TVA déductible sur immobilisations
      21d TVA déductible sur autres biens et services
      27  Crédit de taxe de la période précédente
    """
    lines: Dict[str, Decimal] = {
        "01_base_ht":   Decimal("0.00"),
        "02_base_ht":   Decimal("0.00"),
        "08_base_ht":   Decimal("0.00"),
        "08_tva_aic":   Decimal("0.00"),
        "14_base_ht":   Decimal("0.00"),
        "20_base_ht":   Decimal("0.00"),
        "20_tva_due":   Decimal("0.00"),
        "22_base_ht":   Decimal("0.00"),
        "22_tva_due":   Decimal("0.00"),
        "24_base_ht":   Decimal("0.00"),
        "24_tva_due":   Decimal("0.00"),
        "25_base_ht":   Decimal("0.00"),
        "25_tva_due":   Decimal("0.00"),
        # Déductions (saisies)
        "20d_tva_ded":  _round(tva_deductible_immos),
        "21d_tva_ded":  _round(tva_deductible_autres),
        "27_credit":    _round(credit_periode_precedente),
    }

    def _aggregate(res: VatResult) -> None:
        stock_from_seller = res.sale.stock_country == seller_country.upper()
        buyer_in_seller   = res.sale.buyer_country == seller_country.upper()

        if res.scenario == Scenario.DOMESTIC and buyer_in_seller and stock_from_seller:
            amt  = res.sale.amount_ht
            tva  = res.vat_amount
            rate = res.vat_rate
            lines["01_base_ht"] += amt
            if rate in (Decimal("20"), Decimal("20.00")):
                lines["20_base_ht"] += amt; lines["20_tva_due"] += tva
            elif rate in (Decimal("5.5"), Decimal("5.50")):
                lines["22_base_ht"] += amt; lines["22_tva_due"] += tva
            elif rate in (Decimal("2.1"), Decimal("2.10")):
                lines["24_base_ht"] += amt; lines["24_tva_due"] += tva
            elif rate in (Decimal("10"), Decimal("10.00")):
                lines["25_base_ht"] += amt; lines["25_tva_due"] += tva
            else:
                logger.warning("CA3 v3 : taux %.2f%% non mappé (sale_id=%s) → ligne 20.",
                               float(rate), res.sale.sale_id)
                lines["20_base_ht"] += amt; lines["20_tva_due"] += tva

        elif res.scenario == Scenario.B2B_REVERSE_CHARGE and stock_from_seller:
            lines["02_base_ht"] += res.sale.amount_ht

        elif res.scenario == Scenario.EXPORT and stock_from_seller:
            lines["14_base_ht"] += res.sale.amount_ht

    for res in results:
        _aggregate(res)
    for res in (refund_results or []):
        _aggregate(res)

    # Ligne 08 : AIC depuis les FC Transfers entrant
    if all_fc_transfers:
        b, t = _compute_aic_from_fc_transfers(all_fc_transfers, results, seller_country)
        lines["08_base_ht"] = b
        lines["08_tva_aic"] = t

    for k in lines:
        lines[k] = _round(lines[k])

    return lines


# ---------------------------------------------------------------------------
# Génération HTML
# ---------------------------------------------------------------------------

def generate_ca3_html_report_v3(
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

    lines = compute_ca3_lines_v3(
        results, refund_results,
        all_fc_transfers=all_fc_transfers,
        tva_deductible_immos=tva_deductible_immos,
        tva_deductible_autres=tva_deductible_autres,
        credit_periode_precedente=credit_periode_precedente,
        seller_country=seller_country,
    )

    total_ca_ht   = lines["01_base_ht"] + lines["02_base_ht"] + lines["08_base_ht"] + lines["14_base_ht"]
    tva_brute_due = lines["20_tva_due"] + lines["22_tva_due"] + lines["24_tva_due"] + lines["25_tva_due"]
    # Ligne 08 : AIC → TVA AIC collectée ET déductible simultanément (art. 272 CGI).
    # L'effet net en trésorerie est 0 mais les deux montants doivent apparaître.
    tva_brute_due_avec_aic = tva_brute_due + lines["08_tva_aic"]

    total_ded = lines["20d_tva_ded"] + lines["21d_tva_ded"] + lines["27_credit"] + lines["08_tva_aic"]
    # Note : TVA AIC déduite au même montant que perçue → pas d'impact net.

    solde = _round(tva_brute_due_avec_aic - total_ded)
    solde_label = ("SOLDE À PAYER" if solde >= 0 else "CRÉDIT À REPORTER")
    solde_color = ("#C00000" if solde >= 0 else "#375623")

    oss_base = sum(
        r.sale.amount_ht for r in results
        if r.scenario.name == "OSS_B2C" and r.sale.stock_country == seller_country.upper()
    )
    oss_tva = sum(
        r.vat_amount for r in results
        if r.scenario.name == "OSS_B2C" and r.sale.stock_country == seller_country.upper()
    )

    has_aic  = lines["08_base_ht"] > 0
    has_l22  = lines["22_base_ht"] > 0
    has_l24  = lines["24_base_ht"] > 0
    has_l25  = lines["25_base_ht"] > 0
    has_ded  = any(lines[k] > 0 for k in ("20d_tva_ded", "21d_tva_ded", "27_credit"))

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
            <strong>ℹ️ Ligne 3A — Opérations OSS (informatif) :</strong>
            Ces opérations B2C intra-UE (départ {seller_country}) sont déclarées
            <strong>séparément sur le portail OSS</strong> et n'apparaissent pas dans les lignes
            imposables de la CA3. Elles figurent ici à titre de rapprochement uniquement.<br>
            Base HT OSS : <strong>{_fmt(oss_base)} €</strong> —
            TVA OSS déclarée : <strong>{_fmt(oss_tva)} €</strong>
        </div>"""

    AIC_BLOC = ""
    if has_aic:
        AIC_BLOC = f"""
        <div class="aic-note">
            <strong>ℹ️ Ligne 08 — AIC assimilées (transferts FBA entrant en {seller_country}) :</strong>
            Ces acquisitions intracommunautaires assimilées (art. 17 Dir. 2006/112/CE) génèrent
            une TVA collectée <em>et</em> déductible simultanément (effet net nul en trésorerie,
            art. 272 CGI). La base et la TVA apparaissent dans les sections B et C ci-dessous.
            ⚠ Valeur estimée = prix de vente moyen HT × qté (valeur d'achat réelle non disponible
            depuis les fichiers Amazon — art. 83 Dir. impose la valeur d'achat).
        </div>"""

    L08_ROW = ""
    if has_aic:
        L08_ROW = f"""
            <tr>
                <td class="tc"><span class="cb">Ligne 08</span></td>
                <td>Acquisitions intracommunautaires assimilées — transferts stock FBA entrant {seller_country}
                    <br><small>(base estimée — valeur d'achat réelle à substituer)</small></td>
                <td class="tr">{_fmt(lines['08_base_ht'])}</td>
            </tr>"""

    L22_ROW = f"""
            <tr>
                <td class="tc"><span class="cb">Ligne 22</span></td>
                <td><strong>Taux réduit 5,5 %</strong> (alimentation, livres, médicaments…)</td>
                <td class="tr">{_fmt(lines['22_base_ht'])}</td>
                <td class="tr">{_fmt(lines['22_tva_due'])}</td>
            </tr>""" if has_l22 else ""

    L24_ROW = f"""
            <tr>
                <td class="tc"><span class="cb">Ligne 24</span></td>
                <td><strong>Taux super-réduit 2,1 %</strong> (médicaments remboursables, presse en ligne)</td>
                <td class="tr">{_fmt(lines['24_base_ht'])}</td>
                <td class="tr">{_fmt(lines['24_tva_due'])}</td>
            </tr>""" if has_l24 else ""

    L25_ROW = f"""
            <tr>
                <td class="tc"><span class="cb">Ligne 25</span></td>
                <td><strong>Taux intermédiaire 10 %</strong> (restauration, hébergement…)</td>
                <td class="tr">{_fmt(lines['25_base_ht'])}</td>
                <td class="tr">{_fmt(lines['25_tva_due'])}</td>
            </tr>""" if has_l25 else ""

    L08_TVA_ROW = f"""
            <tr>
                <td class="tc"><span class="cb">Ligne 08</span></td>
                <td><strong>TVA sur AIC assimilées</strong> (collectée = déductible, art. 272 CGI)</td>
                <td class="tr">{_fmt(lines['08_base_ht'])}</td>
                <td class="tr">{_fmt(lines['08_tva_aic'])}</td>
            </tr>""" if has_aic else ""

    DED_SECTION = ""
    if has_ded or has_aic:
        L20d = f"""
                <tr>
                    <td class="tc"><span class="cb">Ligne 20</span></td>
                    <td>TVA déductible sur immobilisations</td>
                    <td class="tr">{_fmt(lines['20d_tva_ded'])}</td>
                </tr>""" if lines["20d_tva_ded"] > 0 else ""
        L21d = f"""
                <tr>
                    <td class="tc"><span class="cb">Ligne 21</span></td>
                    <td>TVA déductible sur autres biens et services (achats, frais…)</td>
                    <td class="tr">{_fmt(lines['21d_tva_ded'])}</td>
                </tr>""" if lines["21d_tva_ded"] > 0 else ""
        L27  = f"""
                <tr>
                    <td class="tc"><span class="cb">Ligne 27</span></td>
                    <td>Crédit de taxe de la période précédente</td>
                    <td class="tr">{_fmt(lines['27_credit'])}</td>
                </tr>""" if lines["27_credit"] > 0 else ""
        L08d = f"""
                <tr>
                    <td class="tc"><span class="cb">Ligne 08</span></td>
                    <td>TVA déductible sur AIC assimilées (égale à la TVA collectée, art. 272 CGI)</td>
                    <td class="tr">{_fmt(lines['08_tva_aic'])}</td>
                </tr>""" if has_aic else ""

        NOTE_DED = ""
        if not has_ded and has_aic:
            NOTE_DED = """<tr><td colspan="3" style="font-style:italic;color:#7f8c8d;font-size:8.5pt;padding:6px 9px;">
                ⚠ TVA déductible sur achats et immobilisations non renseignée
                (données non disponibles depuis les fichiers Amazon). À compléter manuellement
                en passant les paramètres tva_deductible_immos, tva_deductible_autres et
                credit_periode_precedente.</td></tr>"""

        DED_SECTION = f"""
    <h2>C. TVA Déductible</h2>
    <table class="t">
        <thead>
            <tr>
                <th style="width:15%;">Cadre Cerfa</th>
                <th style="width:60%;">Nature de la déduction</th>
                <th style="width:25%;text-align:right;">Montant (EUR)</th>
            </tr>
        </thead>
        <tbody>
            {L20d}{L21d}{L27}{L08d}{NOTE_DED}
            <tr class="tot">
                <td class="tc">-</td>
                <td>TOTAL TVA DÉDUCTIBLE</td>
                <td class="tr">{_fmt(total_ded)}</td>
            </tr>
        </tbody>
    </table>"""

    SOLDE_SECTION = f"""
    <h2>D. Solde net</h2>
    <div class="solde-box" style="color:{solde_color}; border-color:{solde_color};">
        {solde_label} : {_fmt(abs(solde))} EUR<br>
        <small style="font-weight:normal;font-size:9pt;">
            (TVA brute {_fmt(tva_brute_due_avec_aic)} EUR − Total déductions {_fmt(total_ded)} EUR)
        </small>
    </div>"""

    return f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <title>Rapport CA3 v3 — {company_name} — {period_label}</title>
    <style>{CSS}</style>
</head>
<body>
    <div class="hdr-banner">
        <h1 class="title">Rapport de Contrôle TVA — Déclaration CA3 v3</h1>
        <p class="subtitle">Pré-remplissage Cerfa n°3310-CA3-SD — Marché national {seller_country}</p>
    </div>

    <div class="meta">
        <div class="meta-r">
            <div class="ml">Entreprise :</div><div class="mv">{company_name}</div>
            <div class="ml">Période fiscale :</div><div class="mv">{period_label}</div>
        </div>
        <div class="meta-r">
            <div class="ml">SIREN :</div><div class="mv">{siren}</div>
            <div class="ml">Devise :</div><div class="mv">Euro (EUR)</div>
        </div>
    </div>

    {OSS_BLOC}
    {AIC_BLOC}

    <h2>A. Opérations imposables — Chiffre d'affaires HT</h2>
    <table class="t">
        <thead>
            <tr>
                <th style="width:15%;">Cadre Cerfa</th>
                <th style="width:55%;">Nature des opérations</th>
                <th style="width:30%;text-align:right;">Base HT (EUR)</th>
            </tr>
        </thead>
        <tbody>
            <tr>
                <td class="tc"><span class="cb">Ligne 01</span></td>
                <td>Ventes / prestations imposables en {seller_country} (net avoirs)</td>
                <td class="tr">{_fmt(lines['01_base_ht'])}</td>
            </tr>
            <tr>
                <td class="tc"><span class="cb">Ligne 02</span></td>
                <td>Livraisons intracommunautaires B2B exonérées (départ {seller_country})</td>
                <td class="tr">{_fmt(lines['02_base_ht'])}</td>
            </tr>
            {L08_ROW}
            <tr>
                <td class="tc"><span class="cb">Ligne 14</span></td>
                <td>Exportations hors Union Européenne (départ {seller_country})</td>
                <td class="tr">{_fmt(lines['14_base_ht'])}</td>
            </tr>
            <tr class="tot">
                <td class="tc">—</td>
                <td>TOTAL CHIFFRE D'AFFAIRES NET (avoirs déduits)</td>
                <td class="tr">{_fmt(total_ca_ht)}</td>
            </tr>
        </tbody>
    </table>

    <h2>B. TVA due — ventilation par taux</h2>
    <table class="t">
        <thead>
            <tr>
                <th style="width:15%;">Cadre Cerfa</th>
                <th style="width:35%;">Section d'imposition</th>
                <th style="width:25%;text-align:right;">Base imposable (EUR)</th>
                <th style="width:25%;text-align:right;">TVA due (EUR)</th>
            </tr>
        </thead>
        <tbody>
            <tr>
                <td class="tc"><span class="cb">Ligne 20</span></td>
                <td><strong>Taux normal 20 %</strong></td>
                <td class="tr">{_fmt(lines['20_base_ht'])}</td>
                <td class="tr">{_fmt(lines['20_tva_due'])}</td>
            </tr>
            {L25_ROW}{L22_ROW}{L24_ROW}{L08_TVA_ROW}
            <tr class="tot">
                <td class="tc">—</td>
                <td colspan="2">TOTAL TVA BRUTE DUE</td>
                <td class="tr">{_fmt(tva_brute_due_avec_aic)}</td>
            </tr>
        </tbody>
    </table>

    {DED_SECTION}
    {SOLDE_SECTION}

    <div class="notice">
        <strong>Notice :</strong> Ce relevé isole strictement le marché national {seller_country}.
        Les opérations OSS B2C intra-UE font l'objet d'une déclaration séparée sur le portail
        guichet-entreprises.fr. La TVA sur AIC (ligne 08) est à la fois collectée et déductible
        (effet net nul). Les montants TVA déductible sur achats/immobilisations sont à compléter
        par l'utilisateur (non disponibles depuis les fichiers de transactions Amazon). Ce document
        ne remplace pas un conseil fiscal professionnel.
    </div>
</body>
</html>"""