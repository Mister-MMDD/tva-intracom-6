"""
Module de déclaration TVA locale — équivalent générique du CA3 pour les
pays UE autres que la France (immatriculations locales hors FR, canal
`Channel.LOCAL_REGISTRATION`).

⚠️ CE N'EST PAS UN FAC-SIMILÉ DU FORMULAIRE OFFICIEL DU PAYS CONCERNÉ.
Contrairement à `ca3_report.py` (qui reproduit fidèlement la numérotation
du Cerfa 3310-CA3-SD après vérification contre le PDF officiel), ce module
génère un rapport HTML au format et au style visuel harmonisés (même charte
que le CA3), mais avec des libellés ET une structure GÉNÉRIQUES :
« base imposable / TVA », ventilée par taux réellement présent dans les
données — pas de cases ou de lignes numérotées propres à chaque
administration fiscale nationale.

Pour un sous-ensemble de pays (voir `rates.LOCAL_VAT_BOX_CODES`), les codes
de case officiels connus (Kennzahl allemand, Casilla espagnole, Rubriek
néerlandaise...) sont affichés à titre indicatif dans une colonne dédiée —
mais ce mapping n'a pas fait l'objet de la même vérification exhaustive
contre un formulaire PDF officiel que le CA3 français. Si un pays précis
nécessite un rendu fidèle au formulaire réel (comme pour le CA3), fournir
le formulaire officiel (PDF/capture) pour l'ajouter correctement plutôt que
de se fier à ce rendu générique lors d'un dépôt réel.

Ce module ne calcule ni ne connaît :
- les seuils de périodicité propres à chaque pays (mensuel/trimestriel),
- les déductions locales (TVA sur achats locaux, immobilisations, etc.) —
  contrairement au CA3 qui les accepte en paramètres, aucune saisie n'est
  prévue ici : seule la TVA COLLECTÉE (issue des ventes Amazon) est
  présentée, jamais un solde net à payer.
"""

from __future__ import annotations

import logging
from decimal import Decimal, ROUND_HALF_UP
from typing import Dict, List, Optional

from tva_intracom.models import VatResult
from tva_intracom.rates import COUNTRY_FISCAL_META, COUNTRY_NAMES, LOCAL_VAT_BOX_CODES
from tva_intracom.i18n import _

logger = logging.getLogger(__name__)


def _round(amount: Decimal) -> Decimal:
    return amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def compute_local_vat_lines(
    results: List[VatResult],
    refund_results: Optional[List[VatResult]],
    vat_country: str,
) -> Dict:
    """Agrège les ventes/avoirs d'un pays donné (canal LOCAL_REGISTRATION),
    par taux de TVA réellement présent dans les données.

    Ne filtre PAS sur `seller_country` : ce module sert uniquement aux
    immatriculations locales hors pays d'établissement — la France utilise
    `ca3_report.py`, pas ce module.
    """
    vat_country = vat_country.upper()
    refund_results = refund_results or []

    sales = [r for r in results if r.channel.value == "LOCAL" and r.vat_country == vat_country]
    refunds = [r for r in refund_results if r.channel.value == "LOCAL" and r.vat_country == vat_country]

    by_rate: Dict[str, Dict[str, Decimal]] = {}

    def _bucket(rate_key: str) -> Dict[str, Decimal]:
        return by_rate.setdefault(rate_key, {
            "base_vente": Decimal("0"), "tva_vente": Decimal("0"), "nb_vente": 0,
            "base_remb": Decimal("0"), "tva_remb": Decimal("0"), "nb_remb": 0,
        })

    for r in sales:
        b = _bucket(str(r.vat_rate))
        b["base_vente"] += r.sale.amount_ht
        b["tva_vente"] += r.vat_amount
        b["nb_vente"] += 1

    for r in refunds:
        b = _bucket(str(r.vat_rate))
        b["base_remb"] += r.sale.amount_ht
        b["tva_remb"] += r.vat_amount
        b["nb_remb"] += 1

    for b in by_rate.values():
        b["base_net"] = _round(b["base_vente"] + b["base_remb"])
        b["tva_net"] = _round(b["tva_vente"] + b["tva_remb"])

    total_base_net = _round(sum((b["base_net"] for b in by_rate.values()), Decimal("0")))
    total_tva_net = _round(sum((b["tva_net"] for b in by_rate.values()), Decimal("0")))
    total_nb = sum(b["nb_vente"] + b["nb_remb"] for b in by_rate.values())

    return {
        "vat_country": vat_country,
        "by_rate": dict(sorted(by_rate.items(), key=lambda kv: -float(kv[0]) if kv[0].replace(".", "", 1).isdigit() else 0)),
        "total_base_net": total_base_net,
        "total_tva_net": total_tva_net,
        "total_nb": total_nb,
        "has_data": total_nb > 0,
    }


def generate_local_vat_html_report(
    results: List[VatResult],
    refund_results: Optional[List[VatResult]],
    vat_country: str,
    company_name: str,
    siren: str,
    period_label: str,
    seller_country: str = "FR",
) -> str:
    """Génère le rapport HTML générique de contrôle TVA locale pour un pays
    non-FR. Même charte visuelle que le CA3 (`ca3_report.py`), structure
    volontairement plus simple (pas de cases numérotées officielles)."""

    vat_country = vat_country.upper()
    lines = compute_local_vat_lines(results, refund_results, vat_country)

    country_label = COUNTRY_NAMES.get(vat_country, vat_country)
    meta = COUNTRY_FISCAL_META.get(
        vat_country,
        (f"Déclaration TVA — {country_label}", "Base imposable", "TVA", "—", "—"),
    )
    decl_name, lbl_base, lbl_tax, rate_std, rate_red = meta
    box_meta = LOCAL_VAT_BOX_CODES.get(vat_country)
    has_box_codes = box_meta is not None

    def _fmt(v: Decimal) -> str:
        return f"{v:,.2f}"

    def _box_for_rate(rate_key: str) -> str:
        if not has_box_codes:
            return "—"
        _, mapping = box_meta
        val = mapping.get(rate_key)
        if val is None:
            return "—"
        code, _desc = val if isinstance(val, tuple) else ("—", val)
        return code

    rows_html = ""
    for rate_key, b in lines["by_rate"].items():
        rows_html += f"""
        <tr>
            <td class="tc"><span class="cb">{_box_for_rate(rate_key)}</span></td>
            <td>{lbl_base} — {_("local_vat_rate_label")} {rate_key}%</td>
            <td class="tr">{_fmt(b['base_vente'])}</td>
            <td class="tr">{_fmt(b['base_remb'])}</td>
            <td class="tr">{_fmt(b['base_net'])}</td>
            <td class="tr">{_fmt(b['tva_net'])}</td>
            <td class="tc">{b['nb_vente'] + b['nb_remb']}</td>
        </tr>"""

    if not lines["has_data"]:
        rows_html = f"""
        <tr><td colspan="7" class="tc" style="color:#7f8c8d;padding:16px;">
            {_("local_vat_no_data", country=country_label, period=period_label)}
        </td></tr>"""

    box_col_note = (
        f'<p class="notice" style="margin-top:6px;">{_("local_vat_box_codes_note")}</p>'
        if has_box_codes else
        f'<p class="notice" style="margin-top:6px;">{_("local_vat_no_box_codes_note")}</p>'
    )

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
        .warn-note { background:#fff3cd; border:1px solid #ffc107; padding:10px 12px;
                     border-radius:4px; font-size:9pt; margin-bottom:16px; }
        .notice { font-size:8pt; color:#7f8c8d; margin-top:28px; padding:10px;
                  border-top:1px solid #dee2e6; }
    """

    return f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <title>{_("local_vat_report_title", country=country_label, company=company_name, period=period_label)}</title>
    <style>{CSS}</style>
</head>
<body>
    <div class="hdr-banner">
        <p class="title">{_("local_vat_main_title", country=country_label)}</p>
        <p class="subtitle">{decl_name}</p>
    </div>

    <div class="warn-note">
        {_("local_vat_generic_warning")}
    </div>

    <div class="meta">
        <div class="meta-r">
            <div class="ml">{_("ca3_meta_company")}</div><div class="mv">{company_name}</div>
            <div class="ml">{_("ca3_meta_period")}</div><div class="mv">{period_label}</div>
        </div>
        <div class="meta-r">
            <div class="ml">{_("ca3_meta_siren")}</div><div class="mv">{siren or "—"}</div>
            <div class="ml">{_("local_vat_meta_country")}</div><div class="mv">{country_label} ({vat_country})</div>
        </div>
        <div class="meta-r">
            <div class="ml">{_("local_vat_meta_rate_std")}</div><div class="mv">{rate_std}</div>
            <div class="ml">{_("local_vat_meta_rate_red")}</div><div class="mv">{rate_red}</div>
        </div>
    </div>

    <h2>{_("local_vat_sec_title")}</h2>
    <table class="t">
        <tr>
            <th>{_("local_vat_col_box")}</th>
            <th>{lbl_base} / {lbl_tax}</th>
            <th>{_("ca3_col_base_sales")}</th>
            <th>{_("ca3_col_base_refunds_header")}</th>
            <th>{lbl_base} ({_("local_vat_net")})</th>
            <th>{lbl_tax} ({_("local_vat_net")})</th>
            <th>{_("local_vat_col_nb")}</th>
        </tr>
        {rows_html}
        <tr class="tot">
            <td colspan="4">{_("local_vat_total_row")}</td>
            <td class="tr">{_fmt(lines['total_base_net'])}</td>
            <td class="tr">{_fmt(lines['total_tva_net'])}</td>
            <td class="tc">{lines['total_nb']}</td>
        </tr>
    </table>
    {box_col_note}

    <p class="notice">
        {_("local_vat_footer_notice", country=country_label)}
    </p>
</body>
</html>"""
