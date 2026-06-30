"""
Module de calcul et de génération du rapport de contrôle pour la déclaration nationale française (CA3) - Version 2.
Prend en compte la ventilation automatique entre le Taux Normal (20%) et le Taux Réduit (5,5%).
"""

from __future__ import annotations

import logging
from decimal import Decimal, ROUND_HALF_UP
from typing import List, Dict, Any, Optional
from tva_intracom.models import VatResult, Scenario

logger = logging.getLogger(__name__)

def _round(amount: Decimal) -> Decimal:
    return amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

def compute_ca3_lines_v2(
    results: List[VatResult],
    refund_results: List[VatResult] | None = None,
) -> Dict[str, Decimal]:
    """
    Calcule les montants associés aux lignes du formulaire Cerfa CA3 (France).

    Périmètre : UNIQUEMENT les opérations avec stock_country == "FR".
    Les ventes domestiques depuis un stock étranger (ex: stock DE → acheteur FR)
    relèvent de la déclaration TVA locale du pays de stockage, pas de la CA3 FR.

    Lignes modélisées :
    - Ligne 01 : Ventes/prestations imposables en France (base nette des avoirs)
    - Ligne 02 : Livraisons intracommunautaires B2B exonérées (départ FR)
    - Ligne 14 : Exportations hors UE (départ FR)
    - Ligne 20 : Base et TVA due au taux normal (20%)
    - Ligne 22 : Base et TVA due au taux réduit (5,5%)
    - Ligne 25 : Base et TVA due au taux intermédiaire (10%)

    Args:
        results: VatResult des ventes.
        refund_results: VatResult des avoirs (montants négatifs). Déduits des
                        bases CA3 pour obtenir des montants nets déclarables.
    """
    lines = {
        "01_base_ht": Decimal("0.00"),  # Chiffre d'affaires imposable global France (net avoirs)
        "02_base_ht": Decimal("0.00"),  # Livraisons intracommunautaires
        "14_base_ht": Decimal("0.00"),  # Exportations
        "20_base_ht": Decimal("0.00"),  # Base imposable à 20%
        "20_tva_due": Decimal("0.00"),  # TVA collectée à 20%
        "22_base_ht": Decimal("0.00"),  # Base imposable à 5,5%
        "22_tva_due": Decimal("0.00"),  # TVA collectée à 5,5%
        "24_base_ht": Decimal("0.00"),  # Base imposable à 2,1% (médicaments remboursables, presse)
        "24_tva_due": Decimal("0.00"),  # TVA collectée à 2,1%
        "25_base_ht": Decimal("0.00"),  # Base imposable à 10%
        "25_tva_due": Decimal("0.00"),  # TVA collectée à 10%
    }

    def _aggregate(res: VatResult) -> None:
        """Ventile un VatResult (vente ou avoir) dans les lignes CA3."""
        # Seules les opérations depuis le stock FR relèvent de la CA3 FR.
        stock_from_fr = res.sale.stock_country == "FR"
        buyer_in_fr   = res.sale.buyer_country == "FR"

        # 1. Opérations imposables en France (DOMESTIC depuis stock FR)
        if res.scenario == Scenario.DOMESTIC and buyer_in_fr and stock_from_fr:
            amount_ht  = res.sale.amount_ht
            vat_amount = res.vat_amount
            rate       = res.vat_rate

            lines["01_base_ht"] += amount_ht

            if rate in (Decimal("20"), Decimal("20.00")):
                lines["20_base_ht"] += amount_ht
                lines["20_tva_due"] += vat_amount
            elif rate in (Decimal("5.5"), Decimal("5.50")):
                lines["22_base_ht"] += amount_ht
                lines["22_tva_due"] += vat_amount
            elif rate in (Decimal("2.1"), Decimal("2.10")):
                # Ligne 24 : taux super-réduit 2,1% (médicaments remboursables, presse en ligne)
                lines["24_base_ht"] += amount_ht
                lines["24_tva_due"] += vat_amount
            elif rate in (Decimal("10"), Decimal("10.00")):
                lines["25_base_ht"] += amount_ht
                lines["25_tva_due"] += vat_amount
            else:
                # Taux non reconnu → ligne 20 par sécurité + log
                logger.warning(
                    "CA3 : taux %.2f%% non mappé sur une ligne Cerfa "
                    "(sale_id=%s) — imputé en ligne 20.",
                    float(rate), res.sale.sale_id,
                )
                lines["20_base_ht"] += amount_ht
                lines["20_tva_due"] += vat_amount

        # 2. Livraisons intracommunautaires exonérées (B2B départ FR vers UE)
        elif res.scenario == Scenario.B2B_REVERSE_CHARGE and stock_from_fr:
            lines["02_base_ht"] += res.sale.amount_ht

        # 3. Exportations hors UE (départ FR)
        elif res.scenario == Scenario.EXPORT and stock_from_fr:
            lines["14_base_ht"] += res.sale.amount_ht

    # Ventes
    for res in results:
        _aggregate(res)

    # Avoirs — déduits des mêmes lignes (montants négatifs → réduction des bases)
    for res in (refund_results or []):
        _aggregate(res)

    # Arrondir toutes les valeurs finales
    for key in lines:
        lines[key] = _round(lines[key])

    return lines

def generate_ca3_html_report_v2(
    results: List[VatResult],
    company_name: str,
    siren: str,
    period_label: str,
    refund_results: List[VatResult] | None = None,
) -> str:
    """
    Génère le rapport HTML/CSS de contrôle de la liasse fiscale CA3.
    Supporte les taux 20%, 10% (ligne 25) et 5,5% (ligne 22).
    Les avoirs sont déduits des bases pour produire des montants nets déclarables.
    """
    lines = compute_ca3_lines_v2(results, refund_results=refund_results)
    total_ca_ht = lines["01_base_ht"] + lines["02_base_ht"] + lines["14_base_ht"]
    total_tva_due = lines["20_tva_due"] + lines["22_tva_due"] + lines["24_tva_due"] + lines["25_tva_due"]
    
    html = f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <title>Rapport de Contrôle - Déclaration CA3 V2</title>
    <style>
        @page {{
            size: A4;
            margin: 20mm 15mm;
            background-color: #ffffff;
            @bottom-right {{
                content: "Page " counter(page) " sur " counter(pages);
                font-family: Arial, sans-serif;
                font-size: 8pt;
                color: #7f8c8d;
            }}
        }}
        * {{
            box-sizing: border-box;
        }}
        body {{
            font-family: Arial, sans-serif;
            color: #2c3e50;
            line-height: 1.4;
            margin: 0;
            padding: 0;
            font-size: 10pt;
        }}
        .header-banner {{
            border-bottom: 3px solid #1f4e79;
            padding-bottom: 10px;
            margin-bottom: 25px;
        }}
        .header-table {{
            display: table;
            width: 100%;
            margin-bottom: 15px;
        }}
        .header-row {{
            display: table-row;
        }}
        .header-cell {{
            display: table-cell;
            vertical-align: top;
        }}
        .title {{
            font-size: 18pt;
            font-weight: bold;
            color: #1f4e79;
            margin: 0 0 5px 0;
        }}
        .subtitle {{
            font-size: 10pt;
            color: #7f8c8d;
            margin: 0;
            text-transform: uppercase;
            letter-spacing: 1px;
        }}
        .meta-box {{
            background-color: #f8f9fa;
            border: 1px solid #e9ecef;
            padding: 12px;
            margin-bottom: 25px;
            border-radius: 4px;
        }}
        .meta-table {{
            display: table;
            width: 100%;
        }}
        .meta-row {{
            display: table-row;
        }}
        .meta-label {{
            display: table-cell;
            font-weight: bold;
            color: #495057;
            padding: 4px 10px 4px 0;
            width: 25%;
        }}
        .meta-value {{
            display: table-cell;
            color: #212529;
            padding: 4px 0;
        }}
        h2 {{
            font-size: 12pt;
            color: #1f4e79;
            border-left: 4px solid #1f4e79;
            padding-left: 8px;
            margin-top: 25px;
            margin-bottom: 15px;
            text-transform: uppercase;
        }}
        table.data-table {{
            width: 100%;
            border-collapse: collapse;
            margin-bottom: 20px;
        }}
        table.data-table th {{
            background-color: #1f4e79;
            color: #ffffff;
            font-weight: bold;
            text-align: left;
            padding: 8px 10px;
            font-size: 9.5pt;
            border: 1px solid #1f4e79;
        }}
        table.data-table td {{
            padding: 8px 10px;
            border: 1px solid #dee2e6;
            font-size: 9.5pt;
        }}
        table.data-table tr:nth-child(even) td {{
            background-color: #f8f9fa;
        }}
        .text-right {{
            text-align: right !important;
        }}
        .text-center {{
            text-align: center !important;
        }}
        .code-box {{
            background-color: #e9ecef;
            font-weight: bold;
            color: #495057;
            font-family: monospace;
            padding: 2px 6px;
            border-radius: 3px;
        }}
        .total-row td {{
            font-weight: bold;
            background-color: #eaeded !important;
            border-top: 2px solid #1f4e79 !important;
        }}
        .notice {{
            font-size: 8.5pt;
            color: #7f8c8d;
            margin-top: 30px;
            padding: 10px;
            border-top: 1px solid #dee2e6;
        }}
    </style>
</head>
<body>

    <div class="header-banner">
        <div class="header-table">
            <div class="header-row">
                <div class="header-cell">
                    <h1 class="title">Rapport de Contrôle TVA (Multi-Taux)</h1>
                    <p class="subtitle">Pré-remplissage de la Déclaration Nationale CA3 — V2</p>
                </div>
                <div class="header-cell text-right" style="vertical-align: bottom;">
                    <span style="font-size: 11pt; font-weight: bold; color: #1f4e79;">Formulaire National (FR)</span>
                </div>
            </div>
        </div>
    </div>

    <div class="meta-box">
        <div class="meta-table">
            <div class="meta-row">
                <div class="meta-label">Entreprise :</div>
                <div class="meta-value">{company_name}</div>
                <div class="meta-label">Période fiscale :</div>
                <div class="meta-value">{period_label}</div>
            </div>
            <div class="meta-row">
                <div class="meta-label">SIREN :</div>
                <div class="meta-value">{siren}</div>
                <div class="meta-label">Devise de travail :</div>
                <div class="meta-value">Euro (EUR)</div>
            </div>
        </div>
    </div>

    <h2>A. Opérations Imposables - Chiffre d'Affaires HT</h2>
    <table class="data-table">
        <thead>
            <tr>
                <th style="width: 15%;">Cadre Cerfa</th>
                <th style="width: 55%;">Nature des opérations</th>
                <th style="width: 30%; text-align: right;">Base HT calculée (EUR)</th>
            </tr>
        </thead>
        <tbody>
            <tr>
                <td class="text-center"><span class="code-box">Ligne 01</span></td>
                <td>Ventes, prestations de services nationales (Total imposable France)</td>
                <td class="text-right">{lines["01_base_ht"]:,.2f}</td>
            </tr>
            <tr>
                <td class="text-center"><span class="code-box">Ligne 02</span></td>
                <td>Livraisons intracommunautaires vers un assujetti (B2B exonéré)</td>
                <td class="text-right">{lines["02_base_ht"]:,.2f}</td>
            </tr>
            <tr>
                <td class="text-center"><span class="code-box">Ligne 14</span></td>
                <td>Exportations hors Union Européenne (Ventes pays tiers)</td>
                <td class="text-right">{lines["14_base_ht"]:,.2f}</td>
            </tr>
            <tr class="total-row">
                <td class="text-center">-</td>
                <td>TOTAL CHIFFRE D'AFFAIRES NET (avoirs déduits)</td>
                <td class="text-right">{total_ca_ht:,.2f}</td>
            </tr>
        </tbody>
    </table>

    <h2>B. Calcul de la TVA due (Ventilation par taux)</h2>
    <table class="data-table">
        <thead>
            <tr>
                <th style="width: 15%;">Cadre Cerfa</th>
                <th style="width: 35%;">Section d'imposition</th>
                <th style="width: 25%; text-align: right;">Base imposable (EUR)</th>
                <th style="width: 25%; text-align: right;">TVA due calculée (EUR)</th>
            </tr>
        </thead>
        <tbody>
            <tr>
                <td class="text-center"><span class="code-box">Ligne 20</span></td>
                <td><strong>Taux normal 20 %</strong></td>
                <td class="text-right">{lines["20_base_ht"]:,.2f}</td>
                <td class="text-right">{lines["20_tva_due"]:,.2f}</td>
            </tr>
            <tr>
                <td class="text-center"><span class="code-box">Ligne 25</span></td>
                <td><strong>Taux intermédiaire 10 %</strong> (restauration, hébergement...)</td>
                <td class="text-right">{lines["25_base_ht"]:,.2f}</td>
                <td class="text-right">{lines["25_tva_due"]:,.2f}</td>
            </tr>
            <tr>
                <td class="text-center"><span class="code-box">Ligne 22</span></td>
                <td><strong>Taux réduit 5,5 %</strong> (alimentation, livres, médicaments...)</td>
                <td class="text-right">{lines["22_base_ht"]:,.2f}</td>
                <td class="text-right">{lines["22_tva_due"]:,.2f}</td>
            </tr>
            <tr>
                <td class="text-center"><span class="code-box">Ligne 24</span></td>
                <td><strong>Taux super-réduit 2,1 %</strong> (médicaments remboursables, presse en ligne)</td>
                <td class="text-right">{lines["24_base_ht"]:,.2f}</td>
                <td class="text-right">{lines["24_tva_due"]:,.2f}</td>
            </tr>
            <tr class="total-row">
                <td class="text-center">-</td>
                <td colspan="2">TOTAL TVA BRUTE DUE</td>
                <td class="text-right">{total_tva_due:,.2f}</td>
            </tr>
        </tbody>
    </table>

    <div class="notice">
        <strong>Notice d'imputation :</strong> Ce relevé informatique isole strictement le marché intérieur français. L'ensemble des transactions B2C vers les autres États membres de l'UE est exclu de ce tableau et fait l'objet d'un export distinct vers le fichier XML Guichet Unique (OSS).
    </div>

</body>
</html>
"""
    return html