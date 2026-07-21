"""Export du recapitulatif, du detail des ventes et des remboursements au format Excel (.xlsx)."""

from __future__ import annotations

import re
from datetime import date as _date, timedelta
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from typing import List

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from .models import VatResult
from .report import ReportSummary, build_report
from .i18n import _
from .rates import COUNTRY_NAMES, COUNTRY_CURRENCIES
from . import ecb_rates

_COUNTRY_NAMES_XL = COUNTRY_NAMES

_CENT = Decimal("0.01")

def _round(amount: Decimal) -> Decimal:
    return amount.quantize(_CENT, rounding=ROUND_HALF_UP)


def _home_currency(seller_country: str) -> str:
    """Devise locale du pays d'origine du compte (rates.COUNTRY_CURRENCIES)."""
    return COUNTRY_CURRENCIES.get((seller_country or "FR").upper(), "EUR")


def _currency_format(currency_code: str) -> str:
    return f'#,##0.00 "{currency_code}"'


def _to_home_currency(amount: Decimal, currency_code: str, conv_date: _date) -> Decimal:
    """Convertit un montant (calculé en EUR par le moteur fiscal) vers la devise
    locale du pays d'origine, au taux BCE en vigueur à `conv_date` (taux spot au
    moment de la génération du rapport — indicatif : le montant légalement dû
    reste celui calculé en EUR par le moteur, cf. ca3_report.py / oss_xml.py).
    En cas d'indisponibilité du taux BCE, le montant EUR d'origine est renvoyé
    tel quel plutôt que de faire échouer l'export."""
    if not currency_code or currency_code.upper() == "EUR":
        return amount
    try:
        converted, _rate, _info = ecb_rates.convert_to_currency(
            amount, "EUR", currency_code, conv_date,
        )
        return converted
    except Exception:
        return amount

# Noms complets des pays pour l'affichage dans Excel
def _get_country_name(code: str) -> str:
    # On pourrait traduire COUNTRY_NAMES ici via i18n si on voulait
    # mais pour l'instant on garde la logique existante ou on utilise i18n
    # On va privilégier COUNTRY_NAMES qui est déjà complet.
    return COUNTRY_NAMES.get(code.upper(), code)

_HEADER_FONT_WHITE = Font(bold=True, size=11, color="FFFFFF")
_TITLE_FONT = Font(bold=True, size=12, color="1F497D")
_BOLD_FONT = Font(bold=True, size=11)

# Couleurs de chartes graphiques pour les onglets
_BLUE_HEADER_FILL = PatternFill(start_color="4472C4", end_color="4472C4", fill_type="solid")
_ORANGE_HEADER_FILL = PatternFill(start_color="ED7D31", end_color="ED7D31", fill_type="solid")
_LIGHT_GRAY_FILL = PatternFill(start_color="F2F2F2", end_color="F2F2F2", fill_type="solid")
_ALERT_FILL = PatternFill(start_color="FFC7CE", end_color="FFC7CE", fill_type="solid")
_ALERT_FONT = Font(bold=True, color="9C0006")

_EUR_FORMAT = '#,##0.00 "EUR"'
_PCT_FORMAT = '0.##"%"'


def _set_header(ws, row: int, headers: List[str], fill=_BLUE_HEADER_FILL) -> None:
    for col, text in enumerate(headers, 1):
        cell = ws.cell(row=row, column=col, value=text)
        cell.font = _HEADER_FONT_WHITE
        cell.fill = fill
        cell.alignment = Alignment(horizontal="center", vertical="center")


def _auto_width(ws) -> None:
    """Ajuste la largeur des colonnes au contenu réel.

    Bornée à ws.max_column / ws.max_row pour éviter d'itérer sur les milliers
    de cellules vides qu'openpyxl peut générer hors de la plage de données
    (comportement constaté sur des onglets > 10 000 lignes).
    """
    max_col = ws.max_column or 1
    max_row = ws.max_row or 1
    col_widths: dict[str, int] = {}

    for row in ws.iter_rows(min_row=1, max_row=max_row, max_col=max_col):
        for cell in row:
            if cell.value is None:
                continue
            col_letter = get_column_letter(cell.column)
            if isinstance(cell.value, (float, Decimal)):
                val_str = f"{cell.value:,.2f} EUR"
            else:
                val_str = str(cell.value)
            col_widths[col_letter] = max(col_widths.get(col_letter, 0), len(val_str))

    for col_letter, length in col_widths.items():
        ws.column_dimensions[col_letter].width = max(length + 4, 12)


def _write_recap(
        ws,
        summary: ReportSummary,
        hash_totals: dict | None = None,
        seller_country: str = "FR",
) -> None:
    ws.title = _("xl_tab_recap")

    ws.cell(row=1, column=1, value=_("xl_recap_title")).font = _TITLE_FONT
    ws.row_dimensions[1].height = 25

    # Devise locale du pays d'origine du compte (home_country) : les montants
    # calculés en EUR par le moteur fiscal sont convertis pour affichage, au
    # taux BCE du jour de génération du rapport (voir _to_home_currency).
    _currency = _home_currency(seller_country)
    _conv_date = _date.today()
    _fmt_home = _currency_format(_currency)

    def _conv(amount: Decimal) -> Decimal:
        return _to_home_currency(amount, _currency, _conv_date)

    if _currency != "EUR":
        _note = ws.cell(
            row=2, column=1,
            value=_("xl_recap_currency_note", currency=_currency, date=_conv_date.isoformat()),
        )
        _note.font = Font(italic=True, size=9, color="7f7f7f")
        ws.row_dimensions[2].height = 16

    # Entêtes de la grille de synthèse
    headers = [_("xl_recap_col_indicator"), _("xl_recap_col_gross"), _("xl_recap_col_refunds"), _("xl_recap_col_net")]
    _set_header(ws, 3, headers, fill=_BLUE_HEADER_FILL)
    ws.row_dimensions[3].height = 22

    _z = Decimal("0.00")
    ref_fr     = getattr(summary, "refund_fr_domestic_vat", _z)
    ref_amz    = getattr(summary, "refund_amazon_vat", _z)
    ref_tot_ht = getattr(summary, "refund_total_ht", _z)          # négatif
    ref_oss    = sum(summary.refund_oss_by_country.values(), _z) if getattr(summary, "refund_oss_by_country", None) else _z

    oss_brut    = sum(summary.oss_by_country.values(), _z) if summary.oss_by_country else _z
    local_brut  = sum(summary.local_by_country.values(), _z) if summary.local_by_country else _z
    ref_local   = sum(summary.refund_local_by_country.values(), _z) if getattr(summary, "refund_local_by_country", None) else _z

    # Libellé du poste "domestique pays d'origine" : dynamique dès que le
    # compte n'est pas rattaché à la France (home_country ≠ FR) — voir
    # README section "Pays d'origine du compte".
    if (seller_country or "FR").upper() == "FR":
        _home_label = _("xl_indicator_vat_fr")
    else:
        _home_label = _("xl_indicator_vat_home_generic", country=_get_country_name(seller_country))

    # [Libellé, Montant Brut (positif), Remboursements (négatif ou 0)]
    data_structure = [
        (_("xl_indicator_ca_ht"),          summary.total_ht,          ref_tot_ht),
        (_home_label,                      summary.fr_domestic_vat,   ref_fr),
        (_("xl_indicator_vat_oss"),        oss_brut,                  ref_oss),
        (_("xl_indicator_vat_amazon"),     summary.amazon_vat,        ref_amz),
        (_("xl_indicator_vat_local"),      local_brut,                ref_local),
        (_("xl_indicator_vat_import"),     summary.import_vat,        _z),
        (_("xl_indicator_b2b_exempt"),     summary.reverse_charge_ht, _z),
        (_("xl_indicator_export_exempt"),   summary.export_ht,         _z),
    ]

    current_row = 4
    # Mémoriser dynamiquement les numéros de ligne des postes qui entrent dans le
    # total vendeur : CA3 (TVA FR), OSS, et TVA Locale. Les indices hardcodés
    # casseraient silencieusement si on insère ou réordonne une ligne.
    _row_ca3: int | None = None
    _row_oss: int | None = None
    _row_local: int | None = None

    for idx, (label, brut_val, refund_val) in enumerate(data_structure):
        ws.cell(row=current_row, column=1, value=label)

        c_brut = ws.cell(row=current_row, column=2, value=float(_conv(brut_val)))
        c_brut.number_format = _fmt_home

        c_ref = ws.cell(row=current_row, column=3, value=float(_conv(refund_val)))
        c_ref.number_format = _fmt_home

        # Formule Excel pour le Net dynamique
        c_net = ws.cell(row=current_row, column=4, value=f"=B{current_row}+C{current_row}")
        c_net.number_format = _fmt_home
        c_net.font = _BOLD_FONT
        c_net.fill = _LIGHT_GRAY_FILL

        ws.row_dimensions[current_row].height = 18

        # Capturer les numéros de ligne des postes inclus dans le total vendeur
        if idx == 1: _row_ca3   = current_row  # TVA France CA3
        if idx == 2: _row_oss   = current_row  # TVA OSS
        if idx == 4: _row_local = current_row  # TVA Locale

        current_row += 1

    # Ligne de Total final "TVA net à payer par vous"
    current_row += 1
    ws.cell(row=current_row, column=1, value=_("xl_recap_total_remit")).font = _BOLD_FONT

    # Formules dynamiques : CA3 + OSS + Local (Amazon et Import exclus — collectés par tiers)
    _total_brut_formula  = f"=B{_row_ca3}+B{_row_oss}+B{_row_local}"
    _total_refund_formula = f"=C{_row_ca3}+C{_row_oss}+C{_row_local}"

    v_total_due = ws.cell(row=current_row, column=2, value=_total_brut_formula)
    v_total_due.number_format = _fmt_home
    v_total_due.font = _BOLD_FONT

    r_total_due = ws.cell(row=current_row, column=3, value=_total_refund_formula)
    r_total_due.number_format = _fmt_home
    r_total_due.font = _BOLD_FONT

    # Net à payer global final
    net_total_due = ws.cell(row=current_row, column=4, value=f"=B{current_row}+C{current_row}")
    net_total_due.number_format = _fmt_home
    net_total_due.font = _HEADER_FONT_WHITE
    net_total_due.fill = _ORANGE_HEADER_FILL
    ws.row_dimensions[current_row].height = 20

    # ── Contrôle de cohérence comptable (ht_by_bucket) ─────────────────────
    # Miroir de l'encart Streamlit (app.py) : ventilation HT exhaustive et
    # mutuellement exclusive par canal fiscal (report.py::ht_by_bucket),
    # calculée indépendamment du total ci-dessus. Permet au cabinet
    # comptable de retrouver le même contrôle d'intégrité dans le livrable
    # Excel, sans avoir accès à l'interface Streamlit.
    current_row += 3
    ws.cell(row=current_row, column=1, value=_("xl_audit_integrity_title")).font = _TITLE_FONT
    ws.row_dimensions[current_row].height = 22
    current_row += 1
    ws.cell(row=current_row, column=1,
            value=_("xl_audit_integrity_help"))
    current_row += 2

    _bucket_header_row = current_row
    _set_header(ws, _bucket_header_row, [_("xl_audit_col_channel"), _("xl_audit_col_control")], fill=_BLUE_HEADER_FILL)
    current_row += 1
    _bucket_first_data_row = current_row
    net_ht_by_bucket = getattr(summary, "net_ht_by_bucket", {})
    for _bucket_label_, _bucket_val in net_ht_by_bucket.items():
        if _bucket_val == 0:
            continue
        ws.cell(row=current_row, column=1, value=_bucket_label_)
        _c_bucket = ws.cell(row=current_row, column=2, value=float(_conv(_bucket_val)))
        _c_bucket.number_format = _fmt_home
        if _bucket_label_ == "Autre / non classé":
            ws.cell(row=current_row, column=1).font = _BOLD_FONT
            _c_bucket.fill = _ORANGE_HEADER_FILL
        current_row += 1
    _bucket_last_data_row = max(current_row - 1, _bucket_first_data_row)

    current_row += 1
    ws.cell(row=current_row, column=1, value=_("xl_audit_total_ht")).font = _BOLD_FONT
    _c_bucket_total = ws.cell(
        row=current_row, column=2,
        value=f"=SUM(B{_bucket_first_data_row}:B{_bucket_last_data_row})",
    )
    _c_bucket_total.number_format = _fmt_home
    _c_bucket_total.font = _BOLD_FONT

    current_row += 1
    ws.cell(row=current_row, column=1, value=_("xl_audit_declared_net_ht"))
    _declared_net_ht = float(_conv(summary.total_ht + summary.refund_total_ht))
    _c_declared = ws.cell(row=current_row, column=2, value=_declared_net_ht)
    _c_declared.number_format = _fmt_home

    current_row += 1
    ws.cell(row=current_row, column=1, value=_("xl_audit_reconciliation_gap")).font = _BOLD_FONT
    _c_delta = ws.cell(row=current_row, column=2, value=f"=B{current_row - 1}-B{current_row - 2}")
    _c_delta.number_format = _fmt_home
    _c_delta.font = _BOLD_FONT

    # --- Injection des Hash Totals techniques en fin de tableau ---
    if hash_totals:
        current_row += 2
        ws.cell(row=current_row, column=1, value=_("xl_audit_total_rows"))
        ws.cell(row=current_row, column=2, value=hash_totals.get("count", 0)).font = Font(name="Courier New")

        current_row += 1
        ws.cell(row=current_row, column=1, value=_("xl_audit_file_signature"))
        ws.cell(row=current_row, column=2, value=hash_totals.get("id_hash", 0)).font = Font(name="Courier New", bold=True)

    _auto_width(ws)


def _write_details_tab(ws, tab_title: str, results_list: List, is_refund_tab: bool = False) -> None:
    ws.title = tab_title

    headers = [
        _("xl_col_tx_id"), _("xl_col_date"), _("xl_col_from"), _("xl_col_to"), _("xl_col_buyer_type"),
        _("xl_col_amount_ht"), _("xl_col_scenario"), _("xl_col_vat_country"), _("xl_col_vat_rate"), _("xl_col_vat_amount"),
        _("xl_col_vat_amazon"), _("xl_col_vat_gap"),
        _("xl_col_collector"), _("xl_col_channel"), _("xl_col_note")
    ]

    header_fill = _ORANGE_HEADER_FILL if is_refund_tab else _BLUE_HEADER_FILL
    _set_header(ws, 1, headers, fill=header_fill)
    ws.row_dimensions[1].height = 22

    for i, r in enumerate(results_list, 2):
        # -- SÉCURITÉ : On détecte si on a un objet VatResult complet ou juste un objet Sale --
        if hasattr(r, "sale"):
            # Cas normal : c'est un VatResult
            sale = r.sale
            scenario_val = str(r.scenario.value)
            vat_rate = r.vat_rate
            vat_amount = r.vat_amount
            collector = r.collector.value
            channel = r.channel.value
            note = r.note
        else:
            # Cas dégradé : c'est juste un objet Sale
            sale = r
            scenario_val = "REFUND"
            vat_rate = 0.0
            vat_amount = 0.0
            collector = "N/A"
            channel = "N/A"
            note = "Remboursement (source brute)"

        ws.cell(row=i, column=1, value=str(getattr(sale, "display_id", "") or sale.sale_id))
        ws.cell(row=i, column=2, value=str(sale.transaction_date))
        ws.cell(row=i, column=3, value=str(sale.stock_country))
        ws.cell(row=i, column=4, value=str(sale.buyer_country))
        ws.cell(row=i, column=5, value=str(sale.buyer_type.value))

        ws.cell(row=i, column=6, value=float(sale.amount_ht)).number_format = _EUR_FORMAT
        ws.cell(row=i, column=7, value=scenario_val)
        # Pays de taxe : disponible sur VatResult, "-" uniquement en mode degrade (Sale brut)
        _vat_country = getattr(r, "vat_country", "-") if hasattr(r, "vat_country") else "-"
        ws.cell(row=i, column=8, value=_vat_country or "-")
        ws.cell(row=i, column=9, value=float(vat_rate)).number_format = _PCT_FORMAT
        ws.cell(row=i, column=10, value=float(vat_amount)).number_format = _EUR_FORMAT
        # Colonnes TVA Amazon et ecart (uniquement si donnee disponible, sinon 0)
        _amz_vat = float(getattr(sale, "amazon_vat_amount", Decimal("0")))
        _ecart   = round(_amz_vat - float(vat_amount), 2)
        ws.cell(row=i, column=11, value=_amz_vat).number_format = _EUR_FORMAT
        c_ecart = ws.cell(row=i, column=12, value=_ecart)
        c_ecart.number_format = _EUR_FORMAT
        # Colorier en rouge si ecart significatif (> 0.05 EUR)
        if abs(_ecart) > 0.05:
            c_ecart.fill = PatternFill(start_color="FCE4D6", end_color="FCE4D6", fill_type="solid")
        ws.cell(row=i, column=13, value=str(collector))
        ws.cell(row=i, column=14, value=str(channel))
        ws.cell(row=i, column=15, value=str(note))

        ws.row_dimensions[i].height = 18

    _auto_width(ws)


def _write_audit_tab(ws, results: list, vies_affected_sale_ids: set | None = None, vies_summary=None) -> None:
    """Onglet Audit — deux sections :

    1. Réconciliation agrégée : sous-totaux par (nature, pays destination) avec
       écart absolu et % — identifie les catégories systématiquement décalées.
    2. Détail ligne par ligne : chaque vente avec écart > 0.05 € (ou flux GB).
    """
    from collections import defaultdict

    vies_affected_sale_ids = vies_affected_sale_ids or set()
    domestic_rc_sale_ids: set[str] = set()
    if vies_summary and hasattr(vies_summary, "reclassifications"):
        for rc in vies_summary.reclassifications:
            if getattr(rc, "is_domestic_reverse_charge", False):
                domestic_rc_sale_ids.add(rc.sale_id)

    def _nature(r) -> str:
        dep = getattr(r.sale, "stock_country", "")
        arr = getattr(r.sale, "buyer_country", "")
        sid = str(r.sale.sale_id)
        tva_amazon = float(getattr(r.sale, "amazon_vat_amount", Decimal("0")))
        tva_moteur = float(r.vat_amount)
        if dep == "GB" or arr == "GB":
            return _("xl_audit_nature_gb")
        if id(r.sale) in vies_affected_sale_ids and tva_amazon == 0:
            return _("xl_audit_nature_vies")
        if sid in domestic_rc_sale_ids or (tva_moteur == 0 and tva_amazon > 0 and dep == arr):
            return _("xl_audit_nature_art194")
        return _("xl_audit_nature_taux")

    # ── Section 1 : Réconciliation agrégée ──────────────────────────────
    ws.title = _("xl_tab_audit")
    ws.cell(row=1, column=1,
            value=_("xl_audit_agg_title")).font = _TITLE_FONT
    ws.row_dimensions[1].height = 24
    ws.cell(row=2, column=1,
            value=_("xl_audit_agg_help")).font = Font(italic=True, size=9, color="595959")
    ws.row_dimensions[2].height = 18
    ws.row_dimensions[3].height = 8

    agg: dict[tuple[str, str], dict] = defaultdict(lambda: {
        "n": 0, "ht": Decimal("0"), "amz": Decimal("0"), "mot": Decimal("0")
    })
    detail_rows = []

    for r in results:
        dep = getattr(r.sale, "stock_country", "")
        arr = getattr(r.sale, "buyer_country", "")
        tva_amz = Decimal(str(round(float(getattr(r.sale, "amazon_vat_amount", Decimal("0"))), 2)))
        tva_mot = Decimal(str(round(float(r.vat_amount), 2)))
        ecart   = tva_amz - tva_mot
        is_gb   = dep == "GB" or arr == "GB"
        nat     = _nature(r)

        if is_gb or abs(float(ecart)) > 0.05:
            agg[(nat, arr)]["n"]   += 1
            agg[(nat, arr)]["ht"]  += r.sale.amount_ht
            agg[(nat, arr)]["amz"] += tva_amz
            agg[(nat, arr)]["mot"] += tva_mot
            detail_rows.append((r, nat, dep, arr, tva_amz, tva_mot, ecart))

    _set_header(ws, 4, [
        _("xl_audit_col_nature"), _("xl_audit_col_dest"),
        _("xl_audit_col_count"), _("xl_audit_col_ca_ht"),
        _("xl_audit_col_vat_amz"), _("xl_audit_col_vat_mot"),
        _("xl_audit_col_gap_abs"), _("xl_audit_col_gap_pct"), _("xl_audit_col_risk"),
    ], fill=_ORANGE_HEADER_FILL)
    ws.row_dimensions[4].height = 22

    row = 5
    for (nat, arr), d in sorted(agg.items()):
        ecart_abs = d["amz"] - d["mot"]
        pct = (ecart_abs / d["mot"] * 100) if d["mot"] != 0 else Decimal("0")
        risque = (_("xl_risk_high") if abs(float(pct)) > 10
                  else _("xl_risk_medium") if abs(float(pct)) > 3
        else _("xl_risk_low"))
        ws.cell(row=row, column=1, value=nat)
        ws.cell(row=row, column=2, value=f"{_get_country_name(arr)} ({arr})")
        ws.cell(row=row, column=3, value=d["n"])
        ws.cell(row=row, column=4, value=float(d["ht"])).number_format = _EUR_FORMAT
        ws.cell(row=row, column=5, value=float(d["amz"])).number_format = _EUR_FORMAT
        ws.cell(row=row, column=6, value=float(d["mot"])).number_format = _EUR_FORMAT
        c_e = ws.cell(row=row, column=7, value=float(ecart_abs))
        c_e.number_format = _EUR_FORMAT
        c_e.font = Font(bold=True, color="C00000" if abs(float(ecart_abs)) > 1 else "000000")
        c_p = ws.cell(row=row, column=8, value=float(_round(pct)))
        c_p.number_format = '0.0"%"'
        ws.cell(row=row, column=9, value=risque)
        ws.row_dimensions[row].height = 18
        row += 1

    if row == 5:
        ws.cell(row=5, column=1, value=_("xl_no_gap_detected")).font = Font(italic=True)
        row = 6

    # ── Section 2 : Détail ligne par ligne ──────────────────────────────
    row += 2
    ws.cell(row=row, column=1,
            value=_("xl_audit_detail_title")).font = Font(bold=True, size=11, color="1F497D")
    ws.row_dimensions[row].height = 20
    row += 1
    _set_header(ws, row, [
        _("xl_detail_col_sale_id"), _("xl_detail_col_nature"), _("xl_detail_col_flow"),
        _("xl_detail_col_scenario"), _("xl_detail_col_ht"),
        _("xl_detail_col_vat_amz"), _("xl_detail_col_vat_mot"), _("xl_detail_col_gap"),
    ])
    ws.row_dimensions[row].height = 22
    row += 1

    for r, nat, dep, arr, tva_amz, tva_mot, ecart in detail_rows:
        ws.cell(row=row, column=1, value=str(getattr(r.sale, "display_id", "") or r.sale.sale_id))
        ws.cell(row=row, column=2, value=nat)
        ws.cell(row=row, column=3, value=f"{dep}→{arr}")
        ws.cell(row=row, column=4, value=str(r.scenario.value))
        ws.cell(row=row, column=5, value=float(r.sale.amount_ht)).number_format = _EUR_FORMAT
        ws.cell(row=row, column=6, value=float(tva_amz)).number_format = _EUR_FORMAT
        ws.cell(row=row, column=7, value=float(tva_mot)).number_format = _EUR_FORMAT
        c = ws.cell(row=row, column=8, value=float(ecart))
        c.number_format = _EUR_FORMAT
        ws.row_dimensions[row].height = 18
        row += 1

    if not detail_rows:
        ws.cell(row=row, column=1, value=_("xl_no_line_gap")).font = Font(italic=True)

    _auto_width(ws)


def _write_vies_history_tab(ws, results: list, scope_id: str) -> None:
    """Onglet Historique VIES : piste d'audit de chaque vérification effectuée."""
    from .vies_engine import get_vies_history_bulk

    ws.title = _("xl_tab_vies")
    _set_header(ws, 1, [
        _("xl_vies_col_vat"), _("xl_vies_col_checked_at"), _("xl_vies_col_status"),
        _("xl_vies_col_country"), _("xl_vies_col_name"), _("xl_vies_col_error")
    ])
    ws.row_dimensions[1].height = 22

    seen_vats: set[str] = set()
    for r in results:
        vat = getattr(r.sale, "buyer_vat_number", "")
        if vat:
            seen_vats.add(vat)

    history_by_vat = get_vies_history_bulk(scope_id, sorted(seen_vats))

    row = 2
    for vat in sorted(seen_vats):
        history = history_by_vat.get(vat, [])
        if not history:
            continue
        for entry in history:
            ws.cell(row=row, column=1, value=vat)
            ws.cell(row=row, column=2, value=entry["checked_at"])
            ws.cell(row=row, column=3, value=_("xl_vies_status_valid") if entry["valid"] else _("xl_vies_status_invalid"))
            ws.cell(row=row, column=4, value=entry["country_code"])
            ws.cell(row=row, column=5, value=entry["name"])
            ws.cell(row=row, column=6, value=entry["error"])
            ws.row_dimensions[row].height = 16
            row += 1

    if row == 2:
        ws.cell(row=2, column=1, value=_("xl_vies_no_history"))
    _auto_width(ws)


def _write_intrastat_tab(
        ws,
        all_fc_transfers: list,
        results: list,
        seller_country: str = "FR",
) -> None:
    """Onglet Intrastat / EMEBI (statistique) — aide au remplissage de la déclaration."""
    from .rates import intrastat_emebi_threshold_for_year

    ws.title = _("xl_tab_intrastat")
    GREEN_FILL = PatternFill(start_color="375623", end_color="375623", fill_type="solid")

    ws.cell(row=1, column=1, value=_("xl_intrastat_title")).font = _TITLE_FONT
    ws.row_dimensions[1].height = 25

    # Année de référence pour le seuil : année en cours au moment de la génération.
    _current_year = _date.today().year
    _seuil_annee_ref, _seuil_confirme = intrastat_emebi_threshold_for_year(_current_year)
    _seuil_warning = (
        "" if _seuil_confirme else
        _("xl_intrastat_unconfirmed_warning", year=_current_year)
    )

    # Note légale
    note = ws.cell(row=2, column=1, value=_("xl_intrastat_note", seller_country=seller_country, year=_current_year, threshold=_seuil_annee_ref, warning=_seuil_warning))
    note.font = Font(italic=True, size=10, color="C00000")
    ws.row_dimensions[2].height = 30
    ws.merge_cells(start_row=2, start_column=1, end_row=2, end_column=13)
    ws.row_dimensions[3].height = 8

    # Calcul du prix moyen HT par ASIN
    asin_avg = _build_asin_avg_price(results)

    # Agrégation des transferts par (départ, arrivée, ASIN, mois)
    from collections import defaultdict
    flux: dict[tuple, dict] = defaultdict(lambda: {"qty": 0, "nb": 0, "designation": ""})
    for t in all_fc_transfers:
        tx_id_unused, date_str, asin, designation, dep, arr, qty = _parse_fc_transfer(t)
        if not dep or not arr:
            continue
        mois = date_str[:7] if date_str else "—"
        key = (dep, arr, asin, mois)
        flux[key]["qty"]          += qty
        flux[key]["nb"]           += 1
        flux[key]["designation"]  = flux[key]["designation"] or designation

    # ── Jauge de seuil annuel (EMEBI) ───────────────────────────────────
    seuil_par_annee: dict[str, dict] = defaultdict(lambda: {"intro": Decimal("0"), "expe": Decimal("0")})
    for (dep, arr, asin, mois), data in flux.items():
        annee = mois[:4] if mois and mois != "—" else "—"
        avg = asin_avg.get(asin, Decimal("0"))
        valeur = _round(Decimal(str(data["qty"])) * avg) if avg else Decimal("0")
        if arr == seller_country:
            seuil_par_annee[annee]["intro"] += valeur
        if dep == seller_country:
            seuil_par_annee[annee]["expe"] += valeur

    current_row = 4
    if seuil_par_annee:
        ws.cell(row=current_row, column=1, value=_("xl_intrastat_seuil_title")).font = Font(bold=True, size=11, color="C00000")
        current_row += 1
        _set_header(ws, current_row, [
            _("xl_intrastat_col_year"), _("xl_intrastat_col_sens"), _("xl_intrastat_col_cumul"),
            _("xl_intrastat_col_threshold"), _("xl_intrastat_col_pct"), _("xl_intrastat_col_status"),
        ], fill=PatternFill(start_color="C00000", end_color="C00000", fill_type="solid"))
        current_row += 1
        any_unconfirmed = False
        for annee in sorted(seuil_par_annee):
            try:
                seuil_annee, confirme = intrastat_emebi_threshold_for_year(int(annee))
            except ValueError:
                seuil_annee, confirme = _seuil_annee_ref, _seuil_confirme
            any_unconfirmed = any_unconfirmed or not confirme
            for sens_label, key_sens in [(_("xl_intrastat_introductions"), "intro"), (_("xl_intrastat_dispatches"), "expe")]:
                cumul = seuil_par_annee[annee][key_sens]
                pct = float(cumul / seuil_annee * 100) if seuil_annee else 0.0
                statut = (_("xl_intrastat_status_exceeded") if pct >= 100
                          else _("xl_intrastat_status_near") if pct >= 80
                else _("xl_intrastat_status_ok"))
                if not confirme:
                    statut += _("xl_intrastat_status_unconfirmed")
                ws.cell(row=current_row, column=1, value=annee)
                ws.cell(row=current_row, column=2, value=sens_label)
                c_v = ws.cell(row=current_row, column=3, value=float(cumul))
                c_v.number_format = _EUR_FORMAT
                c_s = ws.cell(row=current_row, column=4, value=float(seuil_annee))
                c_s.number_format = _EUR_FORMAT
                c_p = ws.cell(row=current_row, column=5, value=round(pct, 1))
                c_p.number_format = '0.0"%"'
                c_p.font = Font(bold=True, color="C00000" if pct >= 100 else ("ED7D31" if pct >= 80 else "375623"))
                ws.cell(row=current_row, column=6, value=statut)
                ws.row_dimensions[current_row].height = 18
                current_row += 1
        cap = ws.cell(row=current_row, column=1,
                      value=_("xl_intrastat_footer", unconfirmed=(_("xl_intrastat_unconfirmed_footer") if any_unconfirmed else "")))
        cap.font = Font(italic=True, size=9, color="7f7f7f")
        current_row += 2
    else:
        ws.cell(row=current_row, column=1, value=_("xl_intrastat_no_transfer")).font = Font(italic=True)
        current_row += 2

    # ── Détail introductions / expéditions (UE → seller_country) ────────
    for flow_label_key, is_intro in [
        ("xl_intrastat_intro_label", True),
        ("xl_intrastat_expe_label", False),
    ]:
        ws.cell(row=current_row, column=1, value=_(flow_label_key, country=seller_country)).font = Font(bold=True, size=11, color="375623")
        current_row += 1
        _set_header(ws, current_row, [
            _("xl_intrastat_col_period"), _("xl_intrastat_col_origin"), _("xl_intrastat_col_dest_cc"),
            _("xl_intrastat_col_flow_code"), _("xl_intrastat_col_nature_tx"),
            _("xl_intrastat_col_asin"), _("xl_intrastat_col_desc"),
            _("xl_intrastat_col_cn8"), _("xl_intrastat_col_qty"), _("xl_intrastat_col_mass"),
            _("xl_intrastat_col_val_stat"), _("xl_intrastat_col_delivery"), _("xl_intrastat_col_remark"),
        ], fill=GREEN_FILL)
        ws.row_dimensions[current_row].height = 22
        current_row += 1

        rows_written = 0
        sens = _("Intro") if is_intro else _("Expé")
        for (dep, arr, asin, mois), data in sorted(flux.items()):
            if is_intro and arr != seller_country:
                continue
            if not is_intro and dep != seller_country:
                continue

            qty    = data["qty"]
            desc   = data["designation"][:80] if data["designation"] else ""
            avg    = asin_avg.get(asin, Decimal("0"))
            valeur = _round(Decimal(str(qty)) * avg) if avg else Decimal("0")

            ws.cell(row=current_row, column=1,  value=mois)
            ws.cell(row=current_row, column=2,  value=f"{_get_country_name(dep)} ({dep})")
            ws.cell(row=current_row, column=3,  value=f"{_get_country_name(arr)} ({arr})")
            ws.cell(row=current_row, column=4,  value=sens)
            ws.cell(row=current_row, column=5,  value=_("xl_intrastat_transfer_desc"))
            ws.cell(row=current_row, column=6,  value=asin)
            ws.cell(row=current_row, column=7,  value=desc)
            ws.cell(row=current_row, column=8,  value=_("xl_intrastat_to_complete"))
            ws.cell(row=current_row, column=9,  value=qty)
            ws.cell(row=current_row, column=10, value=_("xl_intrastat_to_complete"))
            c_v = ws.cell(row=current_row, column=11, value=float(valeur))
            c_v.number_format = _EUR_FORMAT
            ws.cell(row=current_row, column=12, value="DAP / DDP")
            ws.cell(row=current_row, column=13, value=_("xl_intrastat_estimated_val_remark"))
            ws.row_dimensions[current_row].height = 18
            current_row += 1
            rows_written += 1

        if rows_written == 0:
            ws.cell(row=current_row, column=1, value=_("xl_intrastat_no_flow_detected", sens=sens))
            current_row += 1
        current_row += 2

    _auto_width(ws)


def _next_working_day(d: _date) -> _date:
    """Retourne d si c'est un jour ouvrable, sinon le lundi suivant."""
    while d.weekday() >= 5:
        d += timedelta(days=1)
    return d


def _deadline_oss(ref_date: _date) -> _date:
    """Délai OSS : fin du mois suivant la fin du trimestre."""
    q_end_month = ((ref_date.month - 1) // 3 * 3) + 3  # dernier mois du trimestre courant
    year = ref_date.year
    if q_end_month > 12:
        q_end_month -= 12
        year += 1
    # Fin du mois suivant
    if q_end_month == 12:
        return _date(year + 1, 1, 31)
    elif q_end_month + 1 == 12:
        return _date(year, 12, 31)
    else:
        # Dernier jour du mois suivant
        import calendar
        last_day = calendar.monthrange(year, q_end_month + 1)[1]
        return _date(year, q_end_month + 1, last_day)


def _write_calendar_tab(
        ws,
        results: list,
        all_fc_transfers: list,
        period: str = "",
        seller_country: str = "FR",
) -> None:
    """Onglet Calendrier Fiscal — prochaines échéances déduites des données traitées."""
    ws.title = _("xl_tab_calendar")
    PURPLE_FILL = PatternFill(start_color="6B3FA0", end_color="6B3FA0", fill_type="solid")
    GREEN_FILL  = PatternFill(start_color="375623", end_color="375623", fill_type="solid")
    ORANGE_FILL = _ORANGE_HEADER_FILL
    RED_FILL    = PatternFill(start_color="C00000", end_color="C00000", fill_type="solid")
    today       = _date.today()

    ws.cell(row=1, column=1, value=_("xl_cal_title")).font = _TITLE_FONT
    ws.row_dimensions[1].height = 25
    ws.cell(row=2, column=1,
            value=_("xl_cal_meta", date=today.isoformat(), country=seller_country, period=(period or _("xl_cal_unspecified")))).font = Font(italic=True, size=10, color="595959")
    ws.row_dimensions[2].height = 20
    ws.row_dimensions[3].height = 8

    _set_header(ws, 4, [
        _("xl_cal_col_channel"), _("xl_cal_col_obligation"), _("xl_cal_col_ref_period"),
        _("xl_cal_col_deadline"), _("xl_cal_col_remaining"), _("xl_cal_col_status"),
        _("xl_cal_col_portal"), _("xl_cal_col_legal"),
    ], fill=PURPLE_FILL)
    ws.row_dimensions[4].height = 22

    row = 5

    def _write_row(canal, obligation, periode_ref, deadline, portail, base_legale, fill):
        nonlocal row
        jours = (deadline - today).days
        statut = _("xl_cal_status_upcoming") if jours > 7 else (_("xl_cal_status_urgent") if jours >= 0 else _("xl_cal_status_overdue"))
        ws.cell(row=row, column=1, value=canal).fill = fill
        ws.cell(row=row, column=1).font = Font(bold=True, color="FFFFFF")
        ws.cell(row=row, column=2, value=obligation)
        ws.cell(row=row, column=3, value=periode_ref)
        ws.cell(row=row, column=4, value=deadline.isoformat())
        c_jours = ws.cell(row=row, column=5, value=jours)
        c_jours.font = Font(bold=True,
                            color="C00000" if jours < 0 else ("ED7D31" if jours <= 7 else "375623"))
        ws.cell(row=row, column=6, value=statut)
        ws.cell(row=row, column=7, value=portail)
        ws.cell(row=row, column=8, value=base_legale)
        ws.row_dimensions[row].height = 18
        row += 1

    # ── 1. OSS ────────────────────────────────────────────────────────────
    import calendar as _cal
    import re as _re

    oss_quarters: list[tuple[int, int]] = []   # liste de (année, trimestre) couverts

    if period:
        p = period.strip().upper().replace("T", "Q")
        m = _re.fullmatch(r"(\d{4})-Q([1-4])", p)
        if m:
            oss_quarters = [(int(m.group(1)), int(m.group(2)))]
        else:
            # Multi-trimestres / annuel → générer tous les trimestres
            yr_m = _re.fullmatch(r"(\d{4})", p)
            if yr_m:
                oss_quarters = [(int(yr_m.group(1)), q) for q in range(1, 5)]

    # Compléter depuis les dates de ventes OSS si période non reconnue
    if not oss_quarters:
        from .models import Scenario
        seen_qy: set[tuple[int, int]] = set()
        for r in results:
            if r.scenario not in (Scenario.OSS_B2C,):
                continue
            d = (r.sale.transaction_date or "")[:10]
            try:
                yr, mo = int(d[:4]), int(d[5:7])
                seen_qy.add((yr, (mo - 1) // 3 + 1))
            except (ValueError, IndexError):
                pass
        oss_quarters = sorted(seen_qy)

    for yr, q in oss_quarters:
        q_end_month = q * 3
        last_q_day  = _date(yr, q_end_month, _cal.monthrange(yr, q_end_month)[1])
        deadline    = _deadline_oss(last_q_day)
        _write_row(
            "OSS",
            _("xl_cal_oss_task"),
            f"T{q} {yr}",
            deadline,
            "guichet-entreprises.fr / portail OSS DGFIP",
            "Art. 369 sexdecies & septdecies Dir. 2006/112/CE",
            BLUE_FILL := _BLUE_HEADER_FILL,
        )

    # ── 2. CA3 (TVA locale France) ────────────────────────────────────────
    from .models import Channel
    ca3_months: set[tuple[int, int]] = set()
    for r in results:
        if r.channel != Channel.FR_DOMESTIC:
            continue
        d = (r.sale.transaction_date or "")[:10]
        try:
            ca3_months.add((int(d[:4]), int(d[5:7])))
        except (ValueError, IndexError):
            pass
    for yr, mo in sorted(ca3_months):
        next_mo = mo + 1 if mo < 12 else 1
        next_yr = yr if mo < 12 else yr + 1
        deadline = _date(next_yr, next_mo, 24)
        if (seller_country or "FR").upper() == "FR":
            _canal_label = "CA3 / TVA FR"
            _task_label = _("xl_cal_ca3_task")
        else:
            _canal_label = f"TVA {seller_country}"
            _task_label = _("xl_cal_home_task", country=seller_country)
        _write_row(
            _canal_label,
            _task_label,
            f"{yr}-{mo:02d}",
            deadline,
            "impots.gouv.fr (espace professionnel) → Déclarer → TVA" if (seller_country or "FR").upper() == "FR"
            else _("xl_cal_local_portal_generic"),
            "Art. 287 CGI — régime normal mensuel" if (seller_country or "FR").upper() == "FR"
            else _("xl_cal_local_legal_generic"),
            ORANGE_FILL,
        )

    # ── 3. Intrastat ─────────────────────────────────────────────────────
    intrastat_months: set[tuple[int, int]] = set()
    for t in all_fc_transfers:
        tx_id_unused, date_str, asin_unused, desc_unused, dep, arr, qty_unused = _parse_fc_transfer(t)
        if not dep or not arr:
            continue
        if dep != seller_country and arr != seller_country:
            continue
        d = (date_str or "")[:10]
        try:
            intrastat_months.add((int(d[:4]), int(d[5:7])))
        except (ValueError, IndexError):
            pass
    for yr, mo in sorted(intrastat_months):
        next_mo = mo + 1 if mo < 12 else 1
        next_yr = yr if mo < 12 else yr + 1
        # 10e jour ouvré du mois suivant
        d_start  = _date(next_yr, next_mo, 1)
        ouvre    = 0
        d_limit  = d_start
        while ouvre < 10:
            if d_limit.weekday() < 5:
                ouvre += 1
            if ouvre < 10:
                d_limit += timedelta(days=1)
        _write_row(
            "EMEBI (Intrastat)",
            _("Enquête statistique EMEBI {country} (introductions + expéditions, sous réserve de seuil — voir onglet dédié)", country=seller_country),
            f"{yr}-{mo:02d}",
            d_limit,
            "pro.douane.gouv.fr → EMEBI/Intrastat",
            "Art. 7 Règl. UE 2019/2152 — 10e jour ouvré du mois suivant",
            GREEN_FILL,
        )

    # ── 4. Relevé TVA intracom (ESL) ─────────────────────────────────────
    esl_months: set[tuple[int, int]] = set()
    from .models import Scenario as _Scen
    for r in results:
        if r.scenario not in (_Scen.B2B_REVERSE_CHARGE,):
            continue
        d = (r.sale.transaction_date or "")[:10]
        try:
            esl_months.add((int(d[:4]), int(d[5:7])))
        except (ValueError, IndexError):
            pass
    for yr, mo in sorted(esl_months):
        next_mo = mo + 1 if mo < 12 else 1
        next_yr = yr if mo < 12 else yr + 1
        deadline = _date(next_yr, next_mo, 24)
        _write_row(
            _("xl_cal_esl_task"),
            _("xl_cal_esl_desc"),
            f"{yr}-{mo:02d}",
            deadline,
            "impots.gouv.fr → DES (Déclaration Européenne de Services) / ESL",
            "Art. 289 B CGI — même délai que CA3",
            RED_FILL,
        )

    if row == 5:
        ws.cell(row=5, column=1, value=_("xl_cal_no_deadline")).font = Font(italic=True)

    _auto_width(ws)


def _parse_fc_transfer(t: dict) -> tuple[str, str, str, str, str, str, int]:
    """Extrait les champs normalisés d'une ligne FC transfer (multi-format).

    Retourne (tx_id, date_str, asin, designation, dep, arr, qty).
    """
    # Transaction ID
    tx_id = (
            t.get("TRANSACTION_EVENT_ID") or t.get("transaction_event_id") or
            t.get("ACTIVITY_TRANSACTION_ID") or t.get("activity_transaction_id") or ""
    )
    # Date
    date_str = (
            t.get("TRANSACTION_COMPLETE_DATE") or t.get("transaction_complete_date") or
            t.get("TAX_CALCULATION_DATE") or t.get("tax_calculation_date") or ""
    )[:10]
    # ASIN
    asin = (t.get("ASIN") or t.get("asin") or "").strip()
    # Désignation
    designation = (
            t.get("ITEM_DESCRIPTION") or t.get("item_description") or
            t.get("item_name") or ""
    )
    # Pays départ / arrivée
    dep = (
            t.get("DEPARTURE_COUNTRY") or t.get("departure_country") or
            t.get("SALE_DEPART_COUNTRY") or t.get("sale_depart_country") or ""
    ).strip().upper()
    arr = (
            t.get("ARRIVAL_COUNTRY") or t.get("arrival_country") or
            t.get("SALE_ARRIVAL_COUNTRY") or t.get("sale_arrival_country") or ""
    ).strip().upper()
    # Quantité
    try:
        qty = int(float(t.get("QTY") or t.get("qty") or 1))
    except (ValueError, TypeError):
        qty = 1

    return tx_id, date_str, asin, str(designation), dep, arr, qty


def _build_asin_avg_price(results: list) -> dict[str, Decimal]:
    """Calcule le prix de vente HT moyen par ASIN à partir des VatResult de ventes.

    Utilisé comme approximation de la base imposable AIC (valeur d'achat inconnue).
    Seules les ventes avec montant > 0 sont prises en compte (exclut remboursements).
    """
    totals: dict[str, list[Decimal]] = {}
    for r in results:
        asin = getattr(r.sale, "asin", "").strip()
        amt  = r.sale.amount_ht
        if asin and amt > Decimal("0"):
            totals.setdefault(asin, []).append(amt)
    return {
        asin: sum(amounts, Decimal("0")) / Decimal(str(len(amounts)))
        for asin, amounts in totals.items()
        if amounts
    }


def _write_fba_transfers_tab(ws, all_fc_transfers: list) -> None:
    """Onglet Mouvements Stock FBA — détail de chaque transfert."""
    ws.title = _("xl_tab_fba")
    _set_header(ws, 1, [
        _("xl_fba_col_tx_id"), _("xl_fba_col_date"), _("xl_fba_col_asin"), _("xl_fba_col_desc"),
        _("xl_fba_col_qty"), _("xl_fba_col_dep"), _("xl_fba_col_arr"), _("xl_fba_col_type"),
    ], fill=_ORANGE_HEADER_FILL)
    ws.row_dimensions[1].height = 22

    if not all_fc_transfers:
        ws.cell(row=2, column=1, value=_("xl_fba_none"))
        _auto_width(ws)
        return

    for i, t in enumerate(all_fc_transfers, 2):
        tx_id, date_str, asin, designation, dep, arr, qty = _parse_fc_transfer(t)
        tx_type = (t.get("TRANSACTION_TYPE") or t.get("transaction_type") or "FC_TRANSFER").upper()
        ws.cell(row=i, column=1, value=tx_id)
        ws.cell(row=i, column=2, value=date_str)
        ws.cell(row=i, column=3, value=asin)
        ws.cell(row=i, column=4, value=designation)
        ws.cell(row=i, column=5, value=qty)
        ws.cell(row=i, column=6, value=dep or "—")
        ws.cell(row=i, column=7, value=arr or "—")
        ws.cell(row=i, column=8, value=tx_type)
        ws.row_dimensions[i].height = 18

    _auto_width(ws)


def _write_fba_aic_tab(
        ws,
        all_fc_transfers: list,
        results: list,
        countries_with_vat: list[str] | None = None,
) -> None:
    """Onglet Analyse AIC (Acquisitions Intracommunautaires assimilées).

    Pour chaque flux pays_départ → pays_arrivée où le vendeur est immatriculé
    dans les DEUX pays, calcule une estimation de la TVA AIC à autodéclarer :

        Base AIC estimée  = Σ (qté × prix_vente_moyen_HT_par_ASIN)
        TVA AIC estimée   = Base × taux_standard_pays_arrivée

    ⚠ La base légale AIC est la valeur d'ACHAT (art. 83 directive 2006/112/CE).
    Amazon ne fournissant pas cette donnée, on utilise le prix de vente HT moyen
    comme approximation par excès (prudente, généralement acceptée en pratique).
    Remplacer par le prix d'achat réel si disponible.

    Les flux sans immatriculation dans l'un des deux pays sont listés en
    section "Flux non concernés" pour mémoire.
    """
    from .rates import vat_rate as _vat_rate, STANDARD_VAT_RATES

    ws.title = "Analyse AIC FBA"
    countries_with_vat = [c.upper() for c in (countries_with_vat or [])]

    # --- Prix moyen HT par ASIN depuis les ventes ---
    asin_avg = _build_asin_avg_price(results)

    # --- Agrégation par (départ, arrivée, asin) ---
    from collections import defaultdict
    flux_asin: dict[tuple[str, str, str], dict] = defaultdict(lambda: {
        "designation": "", "qty": 0, "nb_transfers": 0,
    })
    flux_summary: dict[tuple[str, str], dict] = defaultdict(lambda: {
        "nb_transfers": 0, "asins": set(),
    })

    for t in all_fc_transfers:
        _, _, asin, designation, dep, arr, qty = _parse_fc_transfer(t)
        if not dep or not arr:
            continue
        key = (dep, arr, asin)
        flux_asin[key]["qty"]          += qty
        flux_asin[key]["nb_transfers"] += 1
        flux_asin[key]["designation"]   = flux_asin[key]["designation"] or designation
        flux_summary[(dep, arr)]["nb_transfers"] += 1
        flux_summary[(dep, arr)]["asins"].add(asin)

    # Séparer flux "à déclarer" (vendeur immatriculé dep ET arr) vs "non concernés"
    flux_actifs   = {k: v for k, v in flux_summary.items()
                     if k[0] in countries_with_vat and k[1] in countries_with_vat}
    flux_inactifs = {k: v for k, v in flux_summary.items()
                     if k not in flux_actifs}

    # ----------------------------------------------------------------
    # En-tête de l'onglet
    # ----------------------------------------------------------------
    ws.cell(row=1, column=1,
            value="ANALYSE DES ACQUISITIONS INTRACOMMUNAUTAIRES ASSIMILÉES (FC TRANSFERS)").font = _TITLE_FONT
    ws.row_dimensions[1].height = 25

    note_cell = ws.cell(row=2, column=1, value=(
        "⚠ Base AIC estimée = prix de vente HT moyen (Amazon ne fournit pas le prix d'achat). "
        "Approximation par excès — remplacer par le coût d'achat réel si disponible (art. 83 dir. 2006/112/CE)."
    ))
    note_cell.font = Font(italic=True, size=10, color="C00000")
    ws.row_dimensions[2].height = 30

    current_row = 4

    # ----------------------------------------------------------------
    # Section 1 : Flux actifs (immatriculation dans les deux pays)
    # ----------------------------------------------------------------
    ws.cell(row=current_row, column=1,
            value="FLUX AVEC IMMATRICULATION DANS LES DEUX PAYS — AIC À DÉCLARER").font = Font(bold=True, size=11, color="C00000")
    current_row += 1

    if not flux_actifs:
        ws.cell(row=current_row, column=1,
                value="Aucun flux ne nécessite de déclaration AIC (immatriculations croisées insuffisantes).")
        current_row += 2
    else:
        # En-tête détail ASIN
        _set_header(ws, current_row, [
            "Départ", "Arrivée",
            "ASIN", "Désignation",
            "Qté transférée", "Prix vente moy. HT (€)",
            "Base AIC estimée (€)", "Taux TVA arrivée (%)",
            "TVA AIC estimée (€)", "Statut",
        ], fill=_BLUE_HEADER_FILL)
        ws.row_dimensions[current_row].height = 22
        current_row += 1

        # Regrouper par flux pour les totaux
        flux_totaux: dict[tuple[str, str], dict] = defaultdict(
            lambda: {"base": Decimal("0"), "tva": Decimal("0")}
        )

        for (dep, arr, asin), data in sorted(flux_asin.items()):
            if (dep, arr) not in flux_actifs:
                continue

            qty         = data["qty"]
            designation = data["designation"]
            avg_price   = asin_avg.get(asin, Decimal("0"))
            base_aic    = _round(Decimal(str(qty)) * avg_price)
            taux_arr    = _vat_rate(arr, "STANDARD") if arr in STANDARD_VAT_RATES else Decimal("0")
            tva_aic     = _round(base_aic * taux_arr / Decimal("100"))
            statut      = "✅ Immatriculé" if (dep in countries_with_vat and arr in countries_with_vat) else "🚨 Vérifier"

            flux_totaux[(dep, arr)]["base"] += base_aic
            flux_totaux[(dep, arr)]["tva"]  += tva_aic

            ws.cell(row=current_row, column=1, value=f"{_COUNTRY_NAMES_XL.get(dep, dep)} ({dep})")
            ws.cell(row=current_row, column=2, value=f"{_COUNTRY_NAMES_XL.get(arr, arr)} ({arr})")
            ws.cell(row=current_row, column=3, value=asin)
            ws.cell(row=current_row, column=4, value=designation[:80])
            ws.cell(row=current_row, column=5, value=qty)
            ws.cell(row=current_row, column=6, value=float(avg_price)).number_format = _EUR_FORMAT
            ws.cell(row=current_row, column=7, value=float(base_aic)).number_format = _EUR_FORMAT
            ws.cell(row=current_row, column=8, value=float(taux_arr)).number_format = _PCT_FORMAT
            c_tva = ws.cell(row=current_row, column=9, value=float(tva_aic))
            c_tva.number_format = _EUR_FORMAT
            c_tva.font = _BOLD_FONT
            ws.cell(row=current_row, column=10, value=statut)
            ws.row_dimensions[current_row].height = 18
            current_row += 1

        # Lignes de sous-total par flux
        current_row += 1
        ws.cell(row=current_row, column=1, value="SOUS-TOTAUX PAR FLUX").font = Font(bold=True, size=10)
        current_row += 1
        _set_header(ws, current_row, [
            "Flux (Départ → Arrivée)", "Nb transferts", "Nb ASIN",
            "Base AIC totale estimée (€)", "TVA AIC totale estimée (€)",
            "Référence légale", "Action requise",
        ], fill=_BLUE_HEADER_FILL)
        ws.row_dimensions[current_row].height = 22
        current_row += 1

        for (dep, arr) in sorted(flux_actifs):
            nb_t  = flux_actifs[(dep, arr)]["nb_transfers"]
            nb_a  = len(flux_actifs[(dep, arr)]["asins"])
            base  = flux_totaux[(dep, arr)]["base"]
            tva   = flux_totaux[(dep, arr)]["tva"]
            ref   = f"AIC art. 17 dir. 2006/112/CE — déclarer en TVA {arr}"
            action = f"Inclure {float(tva):,.2f} € en TVA {arr} (autodéclaration)"
            ws.cell(row=current_row, column=1,
                    value=f"{_COUNTRY_NAMES_XL.get(dep, dep)} → {_COUNTRY_NAMES_XL.get(arr, arr)}")
            ws.cell(row=current_row, column=2, value=nb_t)
            ws.cell(row=current_row, column=3, value=nb_a)
            c_b = ws.cell(row=current_row, column=4, value=float(base))
            c_b.number_format = _EUR_FORMAT
            c_b.font = _BOLD_FONT
            c_t = ws.cell(row=current_row, column=5, value=float(tva))
            c_t.number_format = _EUR_FORMAT
            c_t.font = _HEADER_FONT_WHITE
            c_t.fill = _ORANGE_HEADER_FILL
            ws.cell(row=current_row, column=6, value=ref)
            ws.cell(row=current_row, column=7, value=action)
            ws.row_dimensions[current_row].height = 20
            current_row += 1

    current_row += 2

    # ----------------------------------------------------------------
    # Section 2 : Flux sans double immatriculation (pour mémoire)
    # ----------------------------------------------------------------
    ws.cell(row=current_row, column=1,
            value="FLUX SANS IMMATRICULATION CROISÉE — POUR MÉMOIRE (Amazon gère)").font = Font(bold=True, size=11, color="808080")
    current_row += 1

    if not flux_inactifs:
        ws.cell(row=current_row, column=1, value="—")
        current_row += 1
    else:
        _set_header(ws, current_row, [
            "Départ", "Arrivée", "Nb transferts", "Nb ASIN distincts",
            "Immat. départ", "Immat. arrivée", "Observation",
        ], fill=PatternFill(start_color="A6A6A6", end_color="A6A6A6", fill_type="solid"))
        ws.row_dimensions[current_row].height = 22
        current_row += 1

        for (dep, arr) in sorted(flux_inactifs):
            nb_t = flux_inactifs[(dep, arr)]["nb_transfers"]
            nb_a = len(flux_inactifs[(dep, arr)]["asins"])
            imm_dep = "✅" if dep in countries_with_vat else "—"
            imm_arr = "✅" if arr in countries_with_vat else "—"
            if dep not in countries_with_vat and arr not in countries_with_vat:
                obs = "Aucune immatriculation — Amazon gère l'AIC"
            elif dep in countries_with_vat:
                obs = f"LIC à déclarer côté {dep} (case exonérations)"
            else:
                obs = f"Vérifier immatriculation {arr}"
            ws.cell(row=current_row, column=1, value=f"{_COUNTRY_NAMES_XL.get(dep, dep)} ({dep})")
            ws.cell(row=current_row, column=2, value=f"{_COUNTRY_NAMES_XL.get(arr, arr)} ({arr})")
            ws.cell(row=current_row, column=3, value=nb_t)
            ws.cell(row=current_row, column=4, value=nb_a)
            ws.cell(row=current_row, column=5, value=imm_dep)
            ws.cell(row=current_row, column=6, value=imm_arr)
            ws.cell(row=current_row, column=7, value=obs)
            ws.row_dimensions[current_row].height = 18
            current_row += 1

    _auto_width(ws)


def _month_label(month_key: str) -> str:
    """Formate une clé "YYYY-MM" en libellé colonne lisible "MM/YYYY"."""
    y, _sep, m = month_key.partition("-")
    return f"{m}/{y}" if m else month_key


def _write_section_group_row(ws, row: int, month_start_col: int, n_months: int, total_start_col: int, n_total_cols: int, fill) -> None:
    """Écrit une ligne de regroupement au-dessus des en-têtes de colonnes :
    un libellé fusionné sur les colonnes mois ("Détail mensuel (net)") et un
    libellé fusionné sur les colonnes de total période ("Total période").
    Ne fait rien pour la partie mensuelle si n_months == 0.
    """
    if n_months:
        first, last = month_start_col, month_start_col + n_months - 1
        ws.merge_cells(start_row=row, start_column=first, end_row=row, end_column=last)
        c = ws.cell(row=row, column=first, value=_("xl_monthly_section_label"))
        c.font = _HEADER_FONT_WHITE
        c.fill = fill
        c.alignment = Alignment(horizontal="center", vertical="center")

    first, last = total_start_col, total_start_col + n_total_cols - 1
    ws.merge_cells(start_row=row, start_column=first, end_row=row, end_column=last)
    c = ws.cell(row=row, column=first, value=_("xl_period_section_label"))
    c.font = _HEADER_FONT_WHITE
    c.fill = fill
    c.alignment = Alignment(horizontal="center", vertical="center")
    ws.row_dimensions[row].height = 18


def _write_oss_tab(ws, summary: ReportSummary) -> None:
    """Onglet OSS détaillé : mois par mois (net) puis Brut / Remboursements / Net
    (total période) par pays de destination."""
    ws.title = _("xl_tab_oss")

    ws.cell(row=1, column=1, value=_("xl_oss_title")).font = _TITLE_FONT
    ws.row_dimensions[1].height = 25

    _z = Decimal("0.00")
    all_countries = sorted(
        set(summary.oss_by_country) | set(getattr(summary, "refund_oss_by_country", {}))
    )
    by_country_month = getattr(summary, "oss_by_country_month", {}) or {}
    months = sorted({m for per_country in by_country_month.values() for m in per_country})

    # Colonnes : Pays, Code, [mois...] (net seul), Brut, Remboursements, Net (total période)
    month_start_col = 3
    total_start_col = month_start_col + len(months)

    group_row = 3
    header_row = 4
    _write_section_group_row(ws, group_row, month_start_col, len(months), total_start_col, 3, fill=_BLUE_HEADER_FILL)

    headers = [_("xl_oss_col_country"), _("xl_oss_col_code")]
    headers += [_month_label(m) for m in months]
    headers += [_("xl_oss_col_vat_gross"), _("xl_oss_col_vat_refunds"), _("xl_oss_col_vat_net")]
    _set_header(ws, header_row, headers, fill=_BLUE_HEADER_FILL)
    ws.row_dimensions[header_row].height = 22

    row = header_row + 1
    for country in all_countries:
        brut   = summary.oss_by_country.get(country, _z)
        refund = summary.refund_oss_by_country.get(country, _z) if getattr(summary, "refund_oss_by_country", None) else _z
        net    = brut + refund

        ws.cell(row=row, column=1, value=_get_country_name(country))
        ws.cell(row=row, column=2, value=country)

        month_values = by_country_month.get(country, {})
        for i, m in enumerate(months):
            c_month = ws.cell(row=row, column=month_start_col + i, value=float(month_values.get(m, _z)))
            c_month.number_format = _EUR_FORMAT

        col_brut, col_ref, col_net = total_start_col, total_start_col + 1, total_start_col + 2
        letter_brut, letter_ref, letter_net = get_column_letter(col_brut), get_column_letter(col_ref), get_column_letter(col_net)

        c_brut = ws.cell(row=row, column=col_brut, value=float(brut))
        c_brut.number_format = _EUR_FORMAT

        c_ref = ws.cell(row=row, column=col_ref, value=float(refund))
        c_ref.number_format = _EUR_FORMAT

        c_net = ws.cell(row=row, column=col_net, value=f"={letter_brut}{row}+{letter_ref}{row}")
        c_net.number_format = _EUR_FORMAT
        c_net.font = _BOLD_FONT
        c_net.fill = _LIGHT_GRAY_FILL

        ws.row_dimensions[row].height = 18
        row += 1

    # Ligne de total
    col_brut, col_ref, col_net = total_start_col, total_start_col + 1, total_start_col + 2
    letter_brut, letter_ref, letter_net = get_column_letter(col_brut), get_column_letter(col_ref), get_column_letter(col_net)
    row += 1
    ws.cell(row=row, column=1, value=_("xl_oss_total")).font = _BOLD_FONT
    for i in range(len(months)):
        col = month_start_col + i
        letter = get_column_letter(col)
        c = ws.cell(row=row, column=col, value=f"=SUM({letter}{header_row+1}:{letter}{row-2})")
        c.number_format = _EUR_FORMAT
        c.font = _HEADER_FONT_WHITE
        c.fill = _BLUE_HEADER_FILL
    for col, formula in [
        (col_brut, f"=SUM({letter_brut}{header_row+1}:{letter_brut}{row-2})"),
        (col_ref, f"=SUM({letter_ref}{header_row+1}:{letter_ref}{row-2})"),
        (col_net, f"={letter_brut}{row}+{letter_ref}{row}"),
    ]:
        c = ws.cell(row=row, column=col, value=formula)
        c.number_format = _EUR_FORMAT
        c.font = _HEADER_FONT_WHITE
        c.fill = _BLUE_HEADER_FILL
    ws.row_dimensions[row].height = 20

    _auto_width(ws)





def _write_local_tab(ws, summary: ReportSummary, countries_with_vat: list | None = None) -> None:
    """Onglet TVA locale par pays (immatriculation locale hors OSS) : mois par
    mois (net) puis Brut / Remboursements / Net (total période) et statut."""
    ws.title = _("xl_tab_local")
    countries_with_vat = {c.upper() for c in (countries_with_vat or [])}

    ws.cell(row=1, column=1, value=_("xl_local_title")).font = _TITLE_FONT
    ws.row_dimensions[1].height = 25

    _z = Decimal("0.00")
    local = summary.local_by_country or {}
    refund_local = getattr(summary, "refund_local_by_country", {}) or {}
    all_countries = sorted(set(local) | set(refund_local))
    unregistered = [c for c in all_countries if c not in countries_with_vat]
    by_country_month = getattr(summary, "local_by_country_month", {}) or {}
    months = sorted({m for per_country in by_country_month.values() for m in per_country})

    header_row = 4
    if unregistered:
        ws.cell(row=2, column=1, value=_("xl_local_unregistered_warning", countries=", ".join(unregistered))).font = _ALERT_FONT
        ws.cell(row=2, column=1).fill = _ALERT_FILL
        ws.row_dimensions[2].height = 18

    # Colonnes : Pays, Code, [mois...] (net seul), Brut, Remboursements, Net (total période), Statut
    month_start_col = 3
    total_start_col = month_start_col + len(months)
    col_brut, col_ref, col_net, col_status = total_start_col, total_start_col + 1, total_start_col + 2, total_start_col + 3
    letter_brut, letter_ref = get_column_letter(col_brut), get_column_letter(col_ref)

    group_row = header_row - 1
    _write_section_group_row(ws, group_row, month_start_col, len(months), total_start_col, 3, fill=_ORANGE_HEADER_FILL)

    headers = [_("xl_local_col_country"), _("xl_local_col_code")]
    headers += [_month_label(m) for m in months]
    headers += [_("xl_local_col_vat_due"), _("xl_local_col_vat_refunds"), _("xl_local_col_vat_net"), _("xl_local_col_status")]
    _set_header(ws, header_row, headers, fill=_ORANGE_HEADER_FILL)
    ws.row_dimensions[header_row].height = 22

    row = header_row + 1
    for country in all_countries:
        brut   = local.get(country, _z)
        refund = refund_local.get(country, _z)
        is_registered = country in countries_with_vat

        ws.cell(row=row, column=1, value=_get_country_name(country))
        ws.cell(row=row, column=2, value=country)

        month_values = by_country_month.get(country, {})
        for i, m in enumerate(months):
            c_month = ws.cell(row=row, column=month_start_col + i, value=float(month_values.get(m, _z)))
            c_month.number_format = _EUR_FORMAT

        for col, val in [(col_brut, float(brut)), (col_ref, float(refund))]:
            c = ws.cell(row=row, column=col, value=val)
            c.number_format = _EUR_FORMAT

        c_net = ws.cell(row=row, column=col_net, value=f"={letter_brut}{row}+{letter_ref}{row}")
        c_net.number_format = _EUR_FORMAT
        c_net.font = _BOLD_FONT
        c_net.fill = _LIGHT_GRAY_FILL

        c_status = ws.cell(
            row=row, column=col_status,
            value=_("xl_local_status_registered") if is_registered else _("xl_local_status_unconfirmed"),
        )
        if not is_registered:
            c_status.font = _ALERT_FONT
            c_status.fill = _ALERT_FILL

        ws.row_dimensions[row].height = 18
        row += 1

    # Total
    row += 1
    ws.cell(row=row, column=1, value=_("xl_local_total")).font = _BOLD_FONT
    for i in range(len(months)):
        col = month_start_col + i
        letter = get_column_letter(col)
        c = ws.cell(row=row, column=col, value=f"=SUM({letter}{header_row+1}:{letter}{row-2})")
        c.number_format = _EUR_FORMAT
        c.font = _HEADER_FONT_WHITE
        c.fill = _ORANGE_HEADER_FILL
    for col, formula in [
        (col_brut, f"=SUM({letter_brut}{header_row+1}:{letter_brut}{row-2})"),
        (col_ref, f"=SUM({letter_ref}{header_row+1}:{letter_ref}{row-2})"),
        (col_net, f"={letter_brut}{row}+{letter_ref}{row}"),
    ]:
        c = ws.cell(row=row, column=col, value=formula)
        c.number_format = _EUR_FORMAT
        c.font = _HEADER_FONT_WHITE
        c.fill = _ORANGE_HEADER_FILL
    ws.row_dimensions[row].height = 20

    _auto_width(ws)


def _write_invoice_creditnote_tab(ws, invoice_credit_notes: list) -> None:
    """Onglet INVOICE / CREDIT_NOTE."""
    ws.title = _("xl_tab_invoice_cn")

    ws.cell(row=1, column=1, value=_("xl_inv_cn_title")).font = _TITLE_FONT
    ws.row_dimensions[1].height = 25
    ws.cell(row=2, column=1, value=_("xl_inv_cn_help"))
    ws.row_dimensions[2].height = 18

    headers = [_("xl_inv_cn_col_type"), _("xl_inv_cn_col_date"), _("xl_inv_cn_col_market"), _("xl_inv_cn_col_program"), _("xl_inv_cn_col_ref"), _("xl_inv_cn_col_ht"), _("xl_inv_cn_col_vat"), _("xl_inv_cn_col_currency")]
    _set_header(ws, 4, headers, fill=_BLUE_HEADER_FILL)
    ws.row_dimensions[4].height = 22

    if not invoice_credit_notes:
        ws.cell(row=5, column=1, value=_("xl_inv_cn_none"))
        _auto_width(ws)
        return

    row = 5
    total_ht = Decimal("0.00")
    total_vat = Decimal("0.00")
    for entry in invoice_credit_notes:
        ws.cell(row=row, column=1, value=entry.get("kind", ""))
        ws.cell(row=row, column=2, value=entry.get("date", ""))
        ws.cell(row=row, column=3, value=entry.get("marketplace", ""))
        ws.cell(row=row, column=4, value=entry.get("program_type", ""))
        ws.cell(row=row, column=5, value=entry.get("reference", ""))

        amount_ht = entry.get("amount_ht", Decimal("0")) or Decimal("0")
        vat_amount = entry.get("vat_amount", Decimal("0")) or Decimal("0")

        c_ht = ws.cell(row=row, column=6, value=float(amount_ht))
        c_ht.number_format = _EUR_FORMAT
        c_vat = ws.cell(row=row, column=7, value=float(vat_amount))
        c_vat.number_format = _EUR_FORMAT
        ws.cell(row=row, column=8, value=entry.get("currency", "EUR"))

        total_ht += amount_ht
        total_vat += vat_amount
        ws.row_dimensions[row].height = 18
        row += 1

    row += 1
    ws.cell(row=row, column=1, value=_("xl_total")).font = _BOLD_FONT
    c_ht = ws.cell(row=row, column=6, value=float(_round(total_ht)))
    c_ht.number_format = _EUR_FORMAT
    c_ht.font = _HEADER_FONT_WHITE
    c_ht.fill = _BLUE_HEADER_FILL
    c_vat = ws.cell(row=row, column=7, value=float(_round(total_vat)))
    c_vat.number_format = _EUR_FORMAT
    c_vat.font = _HEADER_FONT_WHITE
    c_vat.fill = _BLUE_HEADER_FILL
    ws.row_dimensions[row].height = 20

    _auto_width(ws)


def export_xlsx(
        results: List[VatResult],
        output_path: str | Path,
        scope_id: str,
        summary: ReportSummary | None = None,
        refund_results: List[VatResult] | None = None,
        all_fc_transfers: list | None = None,
        vies_affected_sale_ids: set | None = None,
        vies_summary=None,
        countries_with_vat: list[str] | None = None,
        period: str = "",
        seller_country: str = "FR",
        invoice_credit_notes: list | None = None,
) -> Path:
    """Genere le fichier Excel complet avec tous les onglets.

    Args:
        scope_id: portée de cache VIES du compte appelant (voir
                  vies.resolve_scope_id) — transmise à l'onglet Historique
                  VIES pour n'afficher que les vérifications de ce compte.
    """

    if summary is None:
        summary = build_report(results)

    # Calcul des Hash Totals (Contrôle d'intégrité technique)
    all_rows = results + (refund_results or [])
    hash_totals = {
        "count": len(all_rows),
        "abs_ht": sum((abs(r.sale.amount_ht) for r in all_rows), Decimal("0.00")),
        "vat": sum((abs(r.vat_amount) for r in all_rows), Decimal("0.00")),
        "id_hash": 0,
        "net_ht_check": sum((r.sale.amount_ht for r in all_rows), Decimal("0.00")),
    }
    for r in all_rows:
        # Somme numérique des IDs pour détecter les doublons ou omissions
        raw_id = re.sub(r"\D", "", str(r.sale.sale_id))
        if raw_id:
            # On prend les 6 derniers chiffres pour plus de précision
            hash_totals["id_hash"] += int(raw_id[-6:])

    wb = Workbook()

    # 1. Page de synthèse
    ws_recap = wb.active
    _write_recap(ws_recap, summary, hash_totals=hash_totals, seller_country=seller_country)

    # 2. Séparation ventes / remboursements
    # Si refund_results est passé explicitement par app.py (cas normal), on fait
    # confiance à cette séparation : results = ventes uniquement, refund_results = avoirs.
    # On filtre quand même results pour écarter d'éventuels résidus négatifs qui
    # auraient glissé (défense en profondeur), mais on n'ajoute PAS refund_results
    # une deuxième fois s'il est déjà fourni — ce serait un doublon.
    sales_results = []
    refunds_from_results = []  # avoirs détectés dans results (cas mixte ou CLI sans séparation)

    for r in results:
        tx_type  = str(getattr(r.sale, "transaction_type", "")).upper()
        sale_id  = str(getattr(r.sale, "sale_id", "")).upper()
        is_refund = getattr(r.sale, "is_refund", False)

        if tx_type == "REFUND" or is_refund or r.sale.amount_ht < 0 or "REFUND" in sale_id:
            refunds_from_results.append(r)
        else:
            sales_results.append(r)

    # Construire la liste finale des remboursements sans doublon :
    # - Si refund_results fourni explicitement → on l'utilise en priorité et on
    #   ignore refunds_from_results (ils sont déjà dans refund_results).
    # - Sinon (CLI, appel direct) → on utilise ce qu'on a extrait de results.
    if refund_results:
        refunds_results_to_write = list(refund_results)
    else:
        refunds_results_to_write = refunds_from_results

    # 4. Onglet Détail Ventes
    ws_sales = wb.create_sheet()
    _write_details_tab(ws_sales, "Detail ventes", sales_results, is_refund_tab=False)

    # 5. Onglet Détail Remboursements
    ws_refunds = wb.create_sheet()
    _write_details_tab(ws_refunds, "Detail remboursements", refunds_results_to_write, is_refund_tab=True)

    # 6. Onglet OSS détaillé par pays
    if summary.oss_by_country or getattr(summary, "refund_oss_by_country", None):
        ws_oss = wb.create_sheet()
        _write_oss_tab(ws_oss, summary)

    # 7. Onglet TVA locale par pays
    if summary.local_by_country or getattr(summary, "refund_local_by_country", None):
        ws_local = wb.create_sheet()
        _write_local_tab(ws_local, summary, countries_with_vat)

    # 8. Onglet Audit Ecarts Amazon
    ws_audit = wb.create_sheet("Audit Ecarts Amazon")
    _write_audit_tab(ws_audit, results, vies_affected_sale_ids, vies_summary=vies_summary)

    # 8bis. Onglet Historique VIES (piste d'audit — preuve de bonne foi)
    ws_vies_hist = wb.create_sheet("Historique VIES")
    _write_vies_history_tab(ws_vies_hist, results + (refund_results or []), scope_id)

    # 9. Onglet Analyse AIC FBA (synthèse fiscale des transferts)
    ws_aic = wb.create_sheet("Analyse AIC FBA")
    _write_fba_aic_tab(ws_aic, all_fc_transfers or [], results, countries_with_vat)

    # 10. Onglet Transferts FBA Détail (liste brute)
    ws_fba = wb.create_sheet("Transferts FBA Détail")
    _write_fba_transfers_tab(ws_fba, all_fc_transfers or [])

    # 11. Onglet Intrastat / DEB (aide au remplissage)
    ws_intrastat = wb.create_sheet("Intrastat (EMEBI)")
    _write_intrastat_tab(ws_intrastat, all_fc_transfers or [], results, seller_country=seller_country)

    # 11bis. Onglet INVOICE / CREDIT_NOTE (écritures Amazon hors ventes)
    if invoice_credit_notes:
        ws_inv_cn = wb.create_sheet()
        _write_invoice_creditnote_tab(ws_inv_cn, invoice_credit_notes)

    # 12. Onglet Calendrier fiscal (échéances déduites des données)
    ws_cal = wb.create_sheet("Calendrier Fiscal")
    _write_calendar_tab(
        ws_cal, results, all_fc_transfers or [],
        period=period, seller_country=seller_country,
                         )

    # 13. Sauvegarde sur disque
    p = Path(output_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    wb.save(str(p))
    return p