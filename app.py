"""Application Streamlit — Moteur TVA Intracommunautaire."""
from __future__ import annotations
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))

import tempfile, re
import logging
import time
import os
from decimal import Decimal
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st
from datetime import datetime, timedelta
import math
import pandas as pd
from tva_intracom.historical_rates_widget import render_historical_rates_alert
from tva_intracom.i18n import _, init_i18n, language_selector

# Initialisation I18N
init_i18n()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)

from tva_intracom.ecb_rates import cache_info as ecb_cache_info
from tva_intracom.vies_engine import (
    get_cache_stats as vies_cache_stats,
    purge_expired_cache,
    set_cache_ttl,
)
from tva_intracom.engine import ViesValidationSummary, compute_all, compute_all_with_vies
from tva_intracom.excel_report import export_xlsx
from tva_intracom.models import Scenario, BuyerType, Channel, Collector
from tva_intracom.report import build_report
from tva_intracom.oss_export import build_oss_excel, build_oss_csv
from tva_intracom.ca3_report import generate_ca3_html_report_v2
from tva_intracom.fec_export import generate_fec_bytes
from tva_intracom.oss_xml import generate_oss_xml, preview_negative_bucket_suggestions
from tva_intracom.oss_export import aggregate_oss_results, find_oss_negative_buckets
from tva_intracom import billing as tva_billing

_ZERO = Decimal("0.00")
from tva_intracom.rates import (
    COUNTRY_NAMES,
    COUNTRY_ISO3,
    COUNTRY_FISCAL_META,
    STANDARD_VAT_RATES,
    is_eu,
)

from tva_intracom.ui.theme import apply_theme, _PLATFORM_OPTIONS
from tva_intracom.ui.formatting import (
    _country_label,
    _fmt,
    _money_col,
    _pct_col,
    _smart_money_df,
    _gated_preview_table,
    render_oss_threshold_bar,
)

# =============================================================================
# PAGE CONFIG + PURGE CACHE MAL-PREFIXÉ (une fois par session)
# =============================================================================
apply_theme()

from tva_intracom.ui.auth_flow import ensure_cookie_manager, run_auth_flow

cookie_manager = ensure_cookie_manager()

# Le sélecteur de langue doit être utilisable AVANT la connexion (écran de
# lien magique compris) — appelé ici, avant run_auth_flow()/st.stop(),
# contrairement à l'ancien emplacement (après l'auth) qui le rendait
# invisible tant que l'utilisateur n'était pas connecté.
language_selector()

st.title(f"🇪🇺 {_('title')}")

_auth_ctx = run_auth_flow(cookie_manager)
if _auth_ctx is None:
    st.stop()

_current_user = _auth_ctx.current_user
_APP_BASE_URL = _auth_ctx.app_base_url
_vies_scope_id = _auth_ctx.vies_scope_id
_stripe_success_url = _auth_ctx.stripe_success_url
_stripe_cancel_url = _auth_ctx.stripe_cancel_url

# --- Synchro langue <-> compte ---
# `language_selector()` (appelé plus haut, avant l'authentification, pour que
# l'écran de connexion lui-même soit localisé) ne connaît que la session
# Streamlit, pas encore le compte. Une fois l'utilisateur identifié :
# - Première fois que ce compte est vu dans cette session : on applique sa
#   langue sauvegardée (tva_users.language) si elle diffère de la langue de
#   session actuelle, puis on ne le refait plus (pour ne pas écraser un
#   changement manuel ultérieur de l'utilisateur dans la même session).
# - Sinon, si la langue de session a changé depuis (l'utilisateur vient
#   d'utiliser le sélecteur) : on persiste ce choix sur le compte.
from tva_intracom import auth as tva_auth
_sess_lang = st.session_state.get("language", "fr")
if st.session_state.get("_prefs_synced_user") != _current_user.id:
    if _current_user.language and _current_user.language != _sess_lang:
        st.session_state["language"] = _current_user.language
        st.session_state["_prefs_synced_user"] = _current_user.id
        st.rerun()
    st.session_state["_prefs_synced_user"] = _current_user.id
elif _current_user.language != _sess_lang:
    tva_auth.set_language(_current_user.id, _sess_lang)
    _current_user.language = _sess_lang

from tva_intracom.ui.sidebar import render_sidebar

_sb = render_sidebar(_auth_ctx)
file_format = _sb.file_format
enable_vies = _sb.enable_vies
on_invalid_behavior = _sb.on_invalid_behavior
convert_fx = _sb.convert_fx
encoding = _sb.encoding
asin_to_category = _sb.asin_to_category
ioss_number = _sb.ioss_number
seller_is_importer = _sb.seller_is_importer
apply_fr_under_threshold = _sb.apply_fr_under_threshold
countries_with_vat = _sb.countries_with_vat
nom_entreprise = _sb.nom_entreprise
siren_entreprise = _sb.siren_entreprise
tva_fr = _sb.tva_fr
local_vat_numbers = _sb.local_vat_numbers
oss_period = _sb.oss_period
_siren_quota_status = _sb.siren_quota_status
home_country = _sb.home_country
display_currency = _sb.display_currency

# --- Configuration de la monnaie de référence ---
# `display_currency` (sélecteur sous le pays d'origine, voir ui/sidebar.py)
# permet de choisir une devise d'affichage indépendante du pays d'origine —
# ex. rester en EUR tout en ayant choisi la Pologne comme pays d'origine pour
# la classification fiscale. "DEFAULT" retombe sur la devise du pays
# d'origine choisi (comportement historique). N'affecte jamais la devise de
# calcul du moteur (toujours EUR) ni les déclarations légales.
from tva_intracom.rates import COUNTRY_CURRENCIES, CURRENCY_SYMBOLS
if display_currency and display_currency != "DEFAULT":
    target_currency = display_currency
else:
    target_currency = COUNTRY_CURRENCIES.get(home_country, "EUR")
currency_symbol = CURRENCY_SYMBOLS.get(target_currency, "€")
st.session_state["target_currency"] = target_currency
st.session_state["currency_symbol"] = currency_symbol

# =============================================================================
# UPLOAD
# =============================================================================
uploaded_files = st.file_uploader(
    _("upload_label"),
    type=["csv","tsv","txt","xlsx","xls"],
    accept_multiple_files=True,
    help=_("upload_help"),
)

if uploaded_files:
    from tva_intracom.parsers import ParseResult
    from tva_intracom.parsers import amazon as parser_amazon
    from tva_intracom.parsers import mirakl as parser_mirakl
    from tva_intracom.parsers import shopify as parser_shopify
    from tva_intracom.parsers import woocommerce as parser_woocommerce
    from tva_intracom.parsers import aliexpress as parser_aliexpress

    # Déduplication silencieuse
    _seen_file_keys: set = set()
    _deduped: list = []
    _dup_names: list = []
    for _f in uploaded_files:
        _fkey = (_f.name, _f.size)
        if _fkey in _seen_file_keys:
            _dup_names.append(_f.name)
        else:
            _seen_file_keys.add(_fkey)
            _deduped.append(_f)
    if _dup_names:
        st.warning(_("duplicate_files_warning", count=len(_dup_names), files=", ".join(f"`{n}`" for n in _dup_names)))
    uploaded_files = _deduped

    # Cache de l'analyse des fichiers (indépendant du cache de calcul TVA plus
    # bas) : Streamlit ré-exécute tout le script à chaque interaction widget
    # (rerun), ce qui relançait sans le vouloir toute la boucle de parsing —
    # invisible sur un petit fichier, mais doublant le temps de chargement sur
    # un gros fichier. On ne ré-analyse que si les fichiers ou les options
    # d'import (pays d'origine, encodage, conversion devise, format,
    # catalogue ASIN) ont réellement changé.
    _parse_cache_key = (
        tuple(sorted((f.name, f.size) for f in uploaded_files)),
        home_country, encoding, convert_fx, file_format,
        tuple(sorted(asin_to_category.items())) if asin_to_category else None,
    )

    if st.session_state.get("_parse_cache_key") == _parse_cache_key:
        _cached = st.session_state["_parse_cache_data"]
        (all_sales, all_refunds, all_fc_transfers, all_invoice_credit_notes,
         all_stock_countries, all_account_identifiers, all_warnings, all_platforms,
         total_rows_sum, skipped_rows_sum, file_summaries, _parse_results) = _cached
        tmp_paths: list = []
    else:
        all_sales, all_refunds, all_fc_transfers = [], [], []
        all_invoice_credit_notes = []
        all_stock_countries, all_warnings, all_platforms = set(), [], []
        # Identifiants de compte Amazon (UNIQUE_ACCOUNT_IDENTIFIER) rencontrés dans
        # les fichiers importés — utilisés pour le gating anti-abus SIREN
        # (voir tva_intracom/ui/billing_gate.py). Vide pour les autres plateformes.
        all_account_identifiers: set = set()
        total_rows_sum = skipped_rows_sum = 0
        file_summaries, tmp_paths, _parse_results = [], [], []

        # Placeholder stable pour éviter les sauts d'interface pendant l'analyse des fichiers
        parse_progress_ph = st.empty()

        for uploaded_file in uploaded_files:
            _ext = Path(uploaded_file.name).suffix or ".csv"
            with tempfile.NamedTemporaryFile(delete=False, suffix=_ext, mode="wb") as tmp:
                tmp.write(uploaded_file.getvalue())
                tmp_path = Path(tmp.name)
            tmp_paths.append(tmp_path)
            try:
                parse_result = None
                if "Amazon" in file_format:
                    _progress_label = (
                        _("analysis_progress", name=uploaded_file.name)
                        if convert_fx else _("analysis_progress_simple", name=uploaded_file.name)
                    )
                    _progress_bar = parse_progress_ph.progress(0.0, text=_progress_label)

                    def _on_parse_progress(processed: int, total: int, _fname=uploaded_file.name) -> None:
                        pct = processed / total if total else 1.0
                        _suffix = f" ({_('fx_conv_suffix')})" if convert_fx else ""
                        _progress_bar.progress(
                            min(pct, 1.0),
                            text=_("analysis_progress_count", name=_fname, processed=f"{processed:,}".replace(",", " "), total=f"{total:,}".replace(",", " "), suffix=_suffix),
                        )

                    parse_result = parser_amazon.load_amazon_report(
                        tmp_path, seller_country=home_country, encoding=encoding, convert_currencies=convert_fx,
                        asin_to_category=asin_to_category,
                        progress_callback=_on_parse_progress,
                    )
                    parse_progress_ph.empty()
                elif "Mirakl" in file_format:
                    parse_result = parser_mirakl.parse(tmp_path, seller_country=home_country, encoding=encoding, convert_currencies=convert_fx)
                elif "Shopify" in file_format:
                    parse_result = parser_shopify.parse(tmp_path, seller_country=home_country, encoding=encoding, convert_currencies=convert_fx)
                elif "WooCommerce" in file_format:
                    parse_result = parser_woocommerce.parse(tmp_path, seller_country=home_country, encoding=encoding, convert_currencies=convert_fx)
                elif "AliExpress" in file_format:
                    parse_result = parser_aliexpress.parse(tmp_path, seller_country=home_country, encoding=encoding, convert_currencies=convert_fx)
                if parse_result is not None:
                    platform = parse_result.platform or file_format.split("(")[0].strip()
                    all_sales.extend(parse_result.sales); all_refunds.extend(parse_result.refunds)
                    all_fc_transfers.extend(parse_result.fc_transfers)
                    all_invoice_credit_notes.extend(getattr(parse_result, "invoice_credit_notes", []))
                    all_stock_countries |= parse_result.stock_countries
                    all_account_identifiers |= getattr(parse_result, "account_identifiers", set())
                    all_warnings.extend(parse_result.warnings); all_platforms.append(platform)
                    total_rows_sum += parse_result.total_rows; skipped_rows_sum += parse_result.skipped_rows
                    _parse_results.append(parse_result)
                    file_summaries.append({
                        _("col_file"): uploaded_file.name, _("col_source"): platform,
                        _("col_sales"): len(parse_result.sales), _("col_refunds"): len(parse_result.refunds),
                        _("col_fba_trans"): len(parse_result.fc_transfers),
                        _("col_phys_returns"): getattr(parse_result, "return_rows", 0),
                        _("col_invoices"): getattr(parse_result, "invoice_rows", 0),
                        _("col_credit_notes"): getattr(parse_result, "credit_note_rows", 0),
                        _("col_rows_read"): parse_result.total_rows, _("col_ignored"): parse_result.skipped_rows
                    })
            except Exception as e:
                st.error(f"Erreur sur **{uploaded_file.name}** : {e}")
                for p in tmp_paths: p.unlink(missing_ok=True)
                st.stop()

        st.session_state["_parse_cache_key"] = _parse_cache_key
        st.session_state["_parse_cache_data"] = (
            all_sales, all_refunds, all_fc_transfers, all_invoice_credit_notes,
            all_stock_countries, all_account_identifiers, all_warnings, all_platforms,
            total_rows_sum, skipped_rows_sum, file_summaries, _parse_results,
        )

    platform_name = all_platforms[0] if all_platforms else file_format.split("(")[0].strip()
    unique_platforms = list(dict.fromkeys(all_platforms))
    _total_returns      = sum(getattr(pr, "return_rows", 0) for pr in _parse_results)
    _total_invoice      = sum(getattr(pr, "invoice_rows", 0) for pr in _parse_results)
    _total_credit_note  = sum(getattr(pr, "credit_note_rows", 0) for pr in _parse_results)
    _total_skipped      = sum(getattr(pr, "skipped_rows", 0) for pr in _parse_results)

    # Résumé import
    _return_part  = _("summary_part_returns", count=_total_returns) if _total_returns else ""
    _invoice_part = _("summary_part_invoices", count=_total_invoice) if _total_invoice else ""
    _credit_part  = _("summary_part_credits", count=_total_credit_note) if _total_credit_note else ""
    _skip_part    = _("summary_part_skipped", count=_total_skipped) if _total_skipped else ""

    if len(uploaded_files) == 1:
        fs = file_summaries[0]
        st.info(_("import_summary_single", platform=platform_name, sales=fs[_('col_sales')], refunds=fs[_('col_refunds')], fc=len(all_fc_transfers), returns=_return_part, invoices=_invoice_part, credits=_credit_part, skipped=_skip_part))
    else:
        st.success(_("import_summary_multi", count=len(uploaded_files), sales=len(all_sales), refunds=len(all_refunds), fc=len(all_fc_transfers), returns=_return_part, invoices=_invoice_part, credits=_credit_part, skipped=_skip_part, total_rows=total_rows_sum))
        with st.expander(_("file_detail_expander", count=len(uploaded_files))):
            st.table(file_summaries)
        if len(unique_platforms) > 1:
            st.warning(_("different_sources_warning", sources=', '.join(unique_platforms)))
    if all_warnings:
        with st.expander(_("import_warnings_header", count=len(all_warnings))):
            for w in all_warnings: st.text(w)

    all_period_mismatches = []
    for pr in _parse_results:
        all_period_mismatches.extend(getattr(pr, "period_mismatches", []))
    if all_period_mismatches:
        with st.expander(
                _("period_mismatch_title", count=len(all_period_mismatches)),
                expanded=False,
        ):
            st.caption(_("period_mismatch_caption"))
            st.dataframe(
                pd.DataFrame([
                    {_("period_mismatch_col_id"): m["sale_id"], _("period_mismatch_col_order"): m["order_date"],
                     _("period_mismatch_col_shipment"): m["shipment_date"],
                     _("period_mismatch_col_amount"): float(m["amount_ht"])}
                    for m in all_period_mismatches
                ]),
                use_container_width=True, hide_index=True,
            )

    # Résumé des taux de change BCE effectivement utilisés
    if convert_fx:
        _fx_used: dict = {}
        for _s in all_sales:
            if _s.original_currency and _s.original_currency != "EUR" and _s.exchange_rate:
                _k = (_s.original_currency, getattr(_s, "exchange_rate_source", "?"))
                if _k not in _fx_used:
                    _fx_used[_k] = {"rate": float(_s.exchange_rate), "date": _s.transaction_date[:7] if _s.transaction_date else ""}
        if _fx_used:
            with st.expander(_("bce_rates_title", count=len(_fx_used))):
                for (_ccy, _src), _info in sorted(_fx_used.items()):
                    _src_lbl = {_("bce_source_official"): "BCE officiel", "fallback": _("bce_source_fallback"), "eur": _("bce_source_native")}.get(_src, _src)
                    st.caption(f"**{_ccy}** : 1 EUR = {_info['rate']:.4f} {_ccy} — source : {_src_lbl} — période : {_info['date'] or '?'}")

    sales, refunds = all_sales, all_refunds

    try:
        if not sales:
            st.error(_("no_sale_error"))
            st.stop()

        import dataclasses as _dc
        if ioss_number or seller_is_importer:
            sales = [_dc.replace(s,
                                 ioss_number=ioss_number.strip() if ioss_number else s.ioss_number,
                                 seller_is_importer=seller_is_importer if seller_is_importer else s.seller_is_importer)
                     for s in sales]

        if not convert_fx:
            foreign = {s.original_currency for s in sales if s.original_currency and s.original_currency != "EUR"}
            if foreign:
                st.warning(_("foreign_currency_warning", currencies=', '.join(sorted(foreign))))

        # === CALCUL (mis en cache dans session_state) ===
        _vies_retry_nonce = st.session_state.get("_vies_retry_nonce", 0)
        _cache_key = (
            tuple(f.name + str(f.size) for f in uploaded_files),
            enable_vies, convert_fx, file_format,
            tuple(sorted(asin_to_category.items())),
            ioss_number, seller_is_importer,
            tuple(sorted(countries_with_vat)),
            apply_fr_under_threshold,
            home_country,
            _vies_retry_nonce,
        )

        calc_progress_ph = st.empty()

        vies_summary = None
        if st.session_state.get("_calc_key") != _cache_key:
            vies_summary = None
            if enable_vies:
                with calc_progress_ph.container():
                    _vies_bar = st.progress(0.0, text=_("calc_progress_vies"))

                    def _vies_progress_cb(done: int, total: int) -> None:
                        if total <= 0:
                            return
                        _vies_bar.progress(
                            min(done / total, 1.0),
                            text=_("calc_progress_vies_count", done=done, total=total),
                        )

                    results, vies_summary, oss_summary = compute_all_with_vies(
                        sales, scope_id=_vies_scope_id, asin_to_category=asin_to_category,
                        on_invalid=on_invalid_behavior, marketplace_name=platform_name,
                        apply_fr_under_threshold=apply_fr_under_threshold,
                        refunds=refunds if refunds else None,
                        vies_progress_callback=_vies_progress_cb)

                calc_progress_ph.empty()
                with calc_progress_ph.container():
                    with st.spinner(_("calc_progress_vat")):
                        refund_results = compute_all(refunds, marketplace_name=platform_name)[0] if refunds else []
                        summary = build_report(results, refund_results=refund_results or None)
                calc_progress_ph.empty()
            else:
                with calc_progress_ph.container():
                    with st.spinner(_("calc_progress_vat")):
                        results, oss_summary = compute_all(
                            sales, marketplace_name=platform_name, asin_to_category=asin_to_category,
                            apply_fr_under_threshold=apply_fr_under_threshold,
                            refunds=refunds if refunds else None)
                        refund_results = compute_all(refunds, marketplace_name=platform_name)[0] if refunds else []
                        summary = build_report(results, refund_results=refund_results or None)
                calc_progress_ph.empty()
            st.session_state["_calc_key"]       = _cache_key
            st.session_state["_results"]        = results
            st.session_state["_refund_results"] = refund_results
            st.session_state["_summary"]        = summary
            st.session_state["_vies_summary"]   = vies_summary
            st.session_state["_oss_summary"]    = oss_summary
        else:
            results        = st.session_state["_results"]
            refund_results = st.session_state["_refund_results"]
            summary        = st.session_state["_summary"]
            vies_summary   = st.session_state["_vies_summary"]
            oss_summary    = st.session_state["_oss_summary"]

        if vies_summary and vies_summary.total_inconclusive > 0:
            st.error(_("vies_inconclusive_error", count=vies_summary.total_inconclusive))

        # Segmentation écarts pour KPI
        _vies_ids_kpi     = getattr(vies_summary, 'vies_affected_sale_ids', set()) if vies_summary else set()
        _vies_rc_ids_kpi:  set[str] = set()
        _dom_rc_ids_kpi:   set[str] = set()
        if vies_summary and hasattr(vies_summary, "reclassifications"):
            for _rc_kpi in vies_summary.reclassifications:
                if getattr(_rc_kpi, "is_domestic_reverse_charge", False):
                    _dom_rc_ids_kpi.add(_rc_kpi.sale_id)
                else:
                    _vies_rc_ids_kpi.add(_rc_kpi.sale_id)
        from tva_intracom.rates import DOMESTIC_REVERSE_CHARGE_COUNTRIES as _DRC_KPI
        from tva_intracom.models import BuyerType as _BT_KPI
        ecarts_autres = []
        for _r in results:
            _tva_amz = float(getattr(_r.sale, 'amazon_vat_amount', Decimal('0')))
            _tva_mot = float(_r.vat_amount)
            _ecart_kpi = _tva_amz - _tva_mot
            if abs(_ecart_kpi) <= 0.05: continue
            if _r.sale.stock_country == 'GB' or _r.sale.buyer_country == 'GB': continue
            _sid_kpi = str(_r.sale.sale_id)
            if _sid_kpi in _vies_rc_ids_kpi or id(_r.sale) in _vies_ids_kpi: continue
            if _sid_kpi in _dom_rc_ids_kpi or (_r.sale.buyer_type == _BT_KPI.B2B and _r.sale.buyer_country in _DRC_KPI and _tva_mot == 0 and _tva_amz > 0): continue
            if _tva_amz == 0 and _tva_mot > 0: continue
            ecarts_autres.append((_r, _ecart_kpi))
        total_ecarts_autres = sum(d for _, d in ecarts_autres)

        # =====================================================================
        # ALERTES — toujours en haut, conditionnelles
        # =====================================================================
        render_historical_rates_alert(results)
        render_oss_threshold_bar(oss_summary)

        # Immatriculations requises
        # BUGFIX : un stock situé hors UE (US, GB post-Brexit, CH, CN, un
        # entrepôt 3PL non-UE...) ne crée aucune obligation d'immatriculation
        # TVA intracommunautaire — seul un stock dans un AUTRE État membre UE
        # que le pays d'origine du compte le fait. `all_stock_countries` était
        # utilisé tel quel, sans filtre UE, ce qui réclamait à tort un numéro
        # de TVA local (et bloquait le téléchargement) pour du stock hors UE.
        unregistered = {
            c for c in all_stock_countries if c and is_eu(c) and c != home_country
        } - set(countries_with_vat)
        pay_eu = {r.vat_country for r in results if r.channel.value == "LOCAL" and r.vat_country}
        unregistered_local = pay_eu - set(countries_with_vat)

        registration_needed = {}
        for c in unregistered:
            if c: registration_needed.setdefault(c, {"stock": False, "sales": False, "ddp": False})["stock"] = True
        for c in unregistered_local:
            if c: registration_needed.setdefault(c, {"stock": False, "sales": False, "ddp": False})["sales"] = True
        if seller_is_importer:
            _ddp_unrg = {r.vat_country for r in results
                         if r.scenario.value == "IMPORT_SELLER_AS_IMPORTER"
                         and r.vat_country != "FR" and r.vat_country not in countries_with_vat}
            for c in _ddp_unrg:
                if c: registration_needed.setdefault(c, {"stock": False, "sales": False, "ddp": False})["ddp"] = True

        if registration_needed:
            _reg_list = ", ".join(sorted(registration_needed.keys()))
            with st.expander(_("action_plan_title", countries=_reg_list), expanded=True):
                st.write(_("action_plan_intro"))
                for c in sorted(registration_needed.keys()):
                    reasons = []
                    icons = ""
                    data = registration_needed[c]
                    if data["stock"]:
                        icons += "📦 "
                        reasons.append(_("action_reason_stock"))
                    if data["sales"]:
                        icons += "💰 "
                        reasons.append(_("action_reason_sales"))
                    if data["ddp"]:
                        icons += "🛃 "
                        reasons.append(_("action_reason_ddp"))

                    st.markdown(f"- **{_country_label(c)} ({c})** : {icons} — *Raison : {' + '.join(reasons)}*")

                critical_blocking = [c for c in registration_needed if c in ["DE", home_country]]
                if critical_blocking:
                    _c_list = " et ".join(f"**{_country_label(c)} ({c})**" for c in sorted(critical_blocking))
                    st.warning(_("amazon_blocking_warning", countries=_c_list))

        # =====================================================================
        # KPIs — toujours visibles
        # =====================================================================
        st.markdown("""
        <style>
        .kpi-card {
            border-radius: 10px;
            padding: 14px 18px;
            background-color: var(--secondary-background-color);
            border: 1px solid color-mix(in srgb, var(--primary-color) 15%, transparent);
            border-left: 4px solid var(--kpi-accent, var(--primary-color));
            box-shadow: 0 1px 3px color-mix(in srgb, var(--primary-color) 8%, transparent);
        }
        .kpi-label {
            font-size: 0.8rem;
            opacity: 0.7;
            margin-bottom: 4px;
        }
        .kpi-value {
            font-size: 1.6rem;
            font-weight: 700;
        }
        .badge-alert {
            display: inline-block;
            background-color: color-mix(in srgb, #d62728 15%, transparent);
            color: #d62728;
            border-radius: 999px;
            padding: 3px 12px;
            font-size: 0.78rem;
            font-weight: 600;
            margin-top: 6px;
        }
        </style>
        """, unsafe_allow_html=True)

        def _kpi_card(label: str, value: str, accent: str, help_text: str = "") -> str:
            title_attr = f' title="{help_text}"' if help_text else ""
            return f"""
            <div class="kpi-card" style="--kpi-accent:{accent}"{title_attr}>
                <div class="kpi-label">{label}</div>
                <div class="kpi-value">{value}</div>
            </div>
            """

        # =====================================================================
        # TABLEAU DE BORD
        # =====================================================================
        with st.container():
            st.header(_("recapitulatif_header"))
            c1, c2, c3, c4 = st.columns(4)

            ca_brut = float(summary.total_ht)
            ca_remb = float(getattr(summary, "refund_total_ht", 0))
            ca_net  = ca_brut + ca_remb

            with c1:
                st.markdown(_kpi_card(_("kpi_ca_ht"), _fmt(ca_net), "#1f4e79",
                                      _("kpi_ca_ht_help", gross=_fmt(ca_brut), refunds=_fmt(ca_remb))), unsafe_allow_html=True)
            with c2:
                st.markdown(_kpi_card(_("kpi_vat_you_owe"), _fmt(float(summary.total_you_owe)), "#d97706",
                                      _("kpi_vat_you_owe_help")), unsafe_allow_html=True)
            with c3:
                st.markdown(_kpi_card(_("kpi_vat_amazon", platform=platform_name), _fmt(float(summary.amazon_vat)), "#2ca02c",
                                      _("kpi_vat_amazon_help", platform=platform_name)), unsafe_allow_html=True)
            with c4:
                if abs(total_ecarts_autres) > 0.05:
                    _sign = "+" if total_ecarts_autres >= 0 else ""
                    st.markdown(_kpi_card(_("amazon_config_error", platform=platform_name), f"{_sign}{_fmt(total_ecarts_autres)}", "#d62728"),
                                unsafe_allow_html=True)
                    st.markdown(f'<span class="badge-alert">{_("config_error_badge")}</span>', unsafe_allow_html=True)
                else:
                    st.markdown(_kpi_card(_("amazon_config_success", platform=platform_name), _fmt(0), "#2ca02c"), unsafe_allow_html=True)

        # =====================================================================
        # GATING BILLING
        # =====================================================================
        from tva_intracom.ui.billing_gate import build_billing_gate, render_account_link_panel

        _gate = build_billing_gate(
            results=results, oss_period=oss_period, cache_key=_cache_key,
            current_user=_current_user, siren_entreprise=siren_entreprise,
            siren_quota_status=_siren_quota_status,
            all_stock_countries=all_stock_countries, pay_eu=pay_eu,
            seller_is_importer=seller_is_importer,
            local_vat_numbers=local_vat_numbers, ioss_number=ioss_number,
            vies_summary=vies_summary,
            stripe_success_url=_stripe_success_url,
            stripe_cancel_url=_stripe_cancel_url,
            vies_scope_id=_vies_scope_id,
            all_account_identifiers=all_account_identifiers,
            nom_entreprise=nom_entreprise,
            home_country=home_country,
        )
        render_account_link_panel(_gate)
        period_label = _gate.period_label
        _period_detected_range = _gate.period_detected_range
        _can_export = _gate.can_export
        _quota_status = _gate.quota_status
        _compliance_blocked = _gate.compliance_blocked
        _missing_vats = _gate.missing_vats
        _ioss_missing = _gate.ioss_missing
        _unlock_label_suffix = _gate.unlock_label_suffix
        _gated_download = _gate.gated_download
        _get_payg_checkout_url = _gate.get_payg_checkout_url

        # =====================================================================
        # ONGLETS PRINCIPAUX
        # =====================================================================
        tab_decl, tab_detail, tab_vies, tab_audit, tab_dl, tab_viz = st.tabs([
            _("tab_declarations"),
            _("tab_sales_detail"),
            _("tab_vies"),
            _("tab_amazon_audit", platform=platform_name),
            _("tab_downloads"),
            _("tab_visualizations"),
        ])

        # =====================================================================
        # CONSTRUCTION DU CONTEXTE PARTAGÉ + RENDU DES ONGLETS
        # =====================================================================
        from tva_intracom.ui.tabs.context import TabContext
        from tva_intracom.ui.tabs.declarations import render_declarations
        from tva_intracom.ui.tabs.detail_ventes import render_detail_ventes
        from tva_intracom.ui.tabs.vies_ui import render_vies
        from tva_intracom.ui.tabs.audit import render_audit
        from tva_intracom.ui.tabs.telechargements import render_telechargements
        from tva_intracom.ui.tabs.visualisations import render_visualisations

        _tab_ctx = TabContext(
            results=results,
            refund_results=refund_results,
            summary=summary,
            vies_summary=vies_summary,
            oss_summary=oss_summary,
            period_label=period_label,
            period_detected_range=_period_detected_range,
            can_export=_can_export,
            gated_download=_gated_download,
            unlock_label_suffix=_unlock_label_suffix,
            vies_scope_id=_vies_scope_id,
            vies_retry_nonce=_vies_retry_nonce,
            enable_vies=enable_vies,
            nom_entreprise=nom_entreprise,
            siren_entreprise=siren_entreprise,
            tva_fr=tva_fr,
            countries_with_vat=countries_with_vat,
            local_vat_numbers=local_vat_numbers,
            all_fc_transfers=all_fc_transfers,
            all_invoice_credit_notes=all_invoice_credit_notes,
            all_sales=all_sales,
            platform_name=platform_name,
            home_country=home_country,
        )

        with tab_decl: render_declarations(_tab_ctx)
        with tab_detail: render_detail_ventes(_tab_ctx)
        with tab_vies: render_vies(_tab_ctx)
        with tab_audit: render_audit(_tab_ctx)
        with tab_dl: render_telechargements(_tab_ctx)
        with tab_viz: render_visualisations(_tab_ctx)

    except Exception as exc:
        st.error(_("processing_error", error=exc))
        raise
    finally:
        for _p in tmp_paths: _p.unlink(missing_ok=True)

else:
    st.session_state.pop("_period_label", None)
    st.markdown("---")
    col_a, col_b = st.columns([2,1])
    with col_a:
        st.markdown(f"""
            ### {_('how_to_use_title')}

            {_('how_to_use_step1')}
            {_('how_to_use_step2')}
            {_('how_to_use_step3')}
            {_('how_to_use_step4')}
        """)