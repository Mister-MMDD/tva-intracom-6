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

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)

from tva_intracom.ecb_rates import cache_info as ecb_cache_info
from tva_intracom.vies import (
    get_cache_stats as vies_cache_stats,
    purge_expired_cache,
    set_cache_ttl,
)
from tva_intracom.engine import ViesValidationSummary, compute_all, compute_all_with_vies
from tva_intracom.excel_report import export_xlsx
from tva_intracom.models import Scenario, BuyerType, Channel, Collector
from tva_intracom.report import build_report
from tva_intracom.oss_export import build_oss_excel, build_oss_csv
from tva_intracom.ca3_report import generate_ca3_html_report_v2  # (et autres imports nécessaires)
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
# SIDEBAR
# =============================================================================

# =============================================================================
# PAGE CONFIG + PURGE CACHE MAL-PREFIXÉ (une fois par session)
# =============================================================================
apply_theme()

from tva_intracom.ui.auth_flow import ensure_cookie_manager, run_auth_flow

cookie_manager = ensure_cookie_manager()

st.title("\U0001f1ea\U0001f1fa Moteur de TVA Intracommunautaire")

_auth_ctx = run_auth_flow(cookie_manager)
_current_user = _auth_ctx.current_user
_APP_BASE_URL = _auth_ctx.app_base_url
_vies_scope_id = _auth_ctx.vies_scope_id
_stripe_success_url = _auth_ctx.stripe_success_url
_stripe_cancel_url = _auth_ctx.stripe_cancel_url

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
# =============================================================================
# UPLOAD
# =============================================================================
uploaded_files = st.file_uploader(
    "Déposez vos fichiers ici (un ou plusieurs mois)",
    type=["csv","tsv","txt","xlsx","xls"],
    accept_multiple_files=True,
    help="CSV, TSV, ou Excel. Plusieurs fichiers pour agréger plusieurs mois.",
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
        st.warning(f"⚠️ **{len(_dup_names)} fichier(s) en double ignoré(s)** : "
            + ", ".join(f"`{n}`" for n in _dup_names))
    uploaded_files = _deduped

    all_sales, all_refunds, all_fc_transfers = [], [], []
    all_invoice_credit_notes = []
    all_stock_countries, all_warnings, all_platforms = set(), [], []
    total_rows_sum = skipped_rows_sum = 0
    file_summaries, tmp_paths, _parse_results = [], [], []

    for uploaded_file in uploaded_files:
        _ext = Path(uploaded_file.name).suffix or ".csv"
        with tempfile.NamedTemporaryFile(delete=False, suffix=_ext, mode="wb") as tmp:
            tmp.write(uploaded_file.getvalue())
            tmp_path = Path(tmp.name)
        tmp_paths.append(tmp_path)
        try:
            parse_result = None
            if "Amazon" in file_format:
                # Barre de progression : utile sur les gros rapports Amazon
                # (des centaines de milliers de lignes) où le parsing peut
                # sembler figer l'interface. Widgets détruits après usage.
                _progress_label = (
                    f"Analyse de {uploaded_file.name} (conversion devises BCE incluse)…"
                    if convert_fx else f"Analyse de {uploaded_file.name}…"
                )
                _progress_bar = st.progress(0.0, text=_progress_label)

                def _on_parse_progress(processed: int, total: int, _fname=uploaded_file.name) -> None:
                    pct = processed / total if total else 1.0
                    _suffix = " (conv. BCE incluse)" if convert_fx else ""
                    _progress_bar.progress(
                        min(pct, 1.0),
                        text=f"Analyse de {_fname} : {processed:,} / {total:,} lignes{_suffix}".replace(",", " "),
                    )

                parse_result = parser_amazon.load_amazon_report(
                    tmp_path, encoding=encoding, convert_currencies=convert_fx,
                    asin_to_category=asin_to_category,
                    progress_callback=_on_parse_progress,
                )
                _progress_bar.empty()
            elif "Mirakl" in file_format:
                parse_result = parser_mirakl.parse(tmp_path, encoding=encoding, convert_currencies=convert_fx)
            elif "Shopify" in file_format:
                parse_result = parser_shopify.parse(tmp_path, encoding=encoding, convert_currencies=convert_fx)
            elif "WooCommerce" in file_format:
                parse_result = parser_woocommerce.parse(tmp_path, encoding=encoding, convert_currencies=convert_fx)
            elif "AliExpress" in file_format:
                parse_result = parser_aliexpress.parse(tmp_path, encoding=encoding, convert_currencies=convert_fx)
            if parse_result is not None:
                platform = parse_result.platform or file_format.split("(")[0].strip()
                all_sales.extend(parse_result.sales); all_refunds.extend(parse_result.refunds)
                all_fc_transfers.extend(parse_result.fc_transfers)
                all_invoice_credit_notes.extend(getattr(parse_result, "invoice_credit_notes", []))
                all_stock_countries |= parse_result.stock_countries
                all_warnings.extend(parse_result.warnings); all_platforms.append(platform)
                total_rows_sum += parse_result.total_rows; skipped_rows_sum += parse_result.skipped_rows
                _parse_results.append(parse_result)
                file_summaries.append({
                    "Fichier": uploaded_file.name, "Source": platform,
                    "Ventes": len(parse_result.sales), "Remboursements": len(parse_result.refunds),
                    "FBA Trans.": len(parse_result.fc_transfers),
                    "Retours phys.": getattr(parse_result, "return_rows", 0),
                    "Invoices": getattr(parse_result, "invoice_rows", 0),
                    "Credit notes": getattr(parse_result, "credit_note_rows", 0),
                    "Lignes lues": parse_result.total_rows, "Ignorées": parse_result.skipped_rows
                })
        except Exception as e:
            st.error(f"Erreur sur **{uploaded_file.name}** : {e}")
            for p in tmp_paths: p.unlink(missing_ok=True)
            st.stop()

    platform_name = all_platforms[0] if all_platforms else file_format.split("(")[0].strip()
    unique_platforms = list(dict.fromkeys(all_platforms))
    _total_returns      = sum(getattr(pr, "return_rows", 0) for pr in _parse_results)
    _total_invoice      = sum(getattr(pr, "invoice_rows", 0) for pr in _parse_results)
    _total_credit_note  = sum(getattr(pr, "credit_note_rows", 0) for pr in _parse_results)
    _total_skipped      = sum(getattr(pr, "skipped_rows", 0) for pr in _parse_results)

    # Résumé import
    _return_part  = f", {_total_returns} retours physiques sans montant" if _total_returns else ""
    _invoice_part = f", {_total_invoice} invoice" if _total_invoice else ""
    _credit_part  = f", {_total_credit_note} credit_note" if _total_credit_note else ""
    _skip_part    = f", {_total_skipped} ignorées" if _total_skipped else ""

    if len(uploaded_files) == 1:
        fs = file_summaries[0]
        st.info(f"**Import {platform_name}** : {fs['Ventes']} ventes, {fs['Remboursements']} remb., "
                f"{len(all_fc_transfers)} transferts FBA{_return_part}{_invoice_part}{_credit_part}{_skip_part}.")
    else:
        st.success(f"**{len(uploaded_files)} fichiers agrégés** — {len(all_sales)} ventes, {len(all_refunds)} remb., "
                   f"{len(all_fc_transfers)} transferts FBA{_return_part}{_invoice_part}{_credit_part}{_skip_part} "
                   f"({total_rows_sum} lignes).")
        with st.expander(f"Détail par fichier ({len(uploaded_files)} fichiers)"):
            st.table(file_summaries)
        if len(unique_platforms) > 1:
            st.warning(f"⚠️ Sources différentes : {', '.join(unique_platforms)}. Vérifiez que ce mix est intentionnel.")
    if all_warnings:
        with st.expander(f"⚠️ Avertissements d'import ({len(all_warnings)})"):
            for w in all_warnings: st.text(w)

    all_period_mismatches = []
    for pr in _parse_results:
        all_period_mismatches.extend(getattr(pr, "period_mismatches", []))
    if all_period_mismatches:
        with st.expander(
            f"📅 Écarts de période commande / expédition ({len(all_period_mismatches)})",
            expanded=False,
        ):
            st.caption(
                "Commandes dont la date de commande et la date d'expédition (fait "
                "générateur retenu pour le calcul fiscal — art. 65 Dir. 2006/112/CE) "
                "ne tombent pas dans le même mois civil. La TVA a été calculée sur "
                "la date d'expédition. Vérifiez que cela ne fait pas basculer ces "
                "ventes dans une période de déclaration OSS/CA3 différente de celle "
                "attendue."
            )
            st.dataframe(
                pd.DataFrame([
                    {"ID vente": m["sale_id"], "Date commande": m["order_date"],
                     "Date expédition (retenue)": m["shipment_date"],
                     "HT (EUR)": float(m["amount_ht"])}
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
            with st.expander(f"💱 Taux de change BCE utilisés ({len(_fx_used)} devise(s))"):
                for (_ccy, _src), _info in sorted(_fx_used.items()):
                    _src_lbl = {"ecb": "BCE officiel", "fallback": "Taux Amazon (fallback BCE)", "eur": "EUR natif"}.get(_src, _src)
                    st.caption(f"**{_ccy}** : 1 EUR = {_info['rate']:.4f} {_ccy} — source : {_src_lbl} — période : {_info['date'] or '?'}")

    sales, refunds = all_sales, all_refunds

    try:
        if not sales:
            st.error("Aucune vente exploitable.")
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
                st.warning(f"⚠️ Devises non-EUR : {', '.join(sorted(foreign))} — Activez conversion BCE.")

        # === CALCUL (mis en cache dans session_state) ===
        _vies_retry_nonce = st.session_state.get("_vies_retry_nonce", 0)
        _cache_key = (
            tuple(f.name + str(f.size) for f in uploaded_files),
            enable_vies, convert_fx, file_format,
            tuple(sorted(asin_to_category.items())),
            ioss_number, seller_is_importer,
            tuple(sorted(countries_with_vat)),
            apply_fr_under_threshold,
            _vies_retry_nonce,
        )
        vies_summary = None
        if st.session_state.get("_calc_key") != _cache_key:
            vies_summary = None
            if enable_vies:
                _vies_progress_ph = st.empty()
                _vies_bar = st.progress(0.0, text="Vérification des numéros de TVA (VIES)…")

                def _vies_progress_cb(done: int, total: int) -> None:
                    if total <= 0:
                        return
                    _vies_bar.progress(
                        min(done / total, 1.0),
                        text=f"Vérification VIES : {done}/{total}",
                    )

                results, vies_summary, oss_summary = compute_all_with_vies(
                    sales, scope_id=_vies_scope_id, asin_to_category=asin_to_category,
                    on_invalid=on_invalid_behavior, marketplace_name=platform_name,
                    apply_fr_under_threshold=apply_fr_under_threshold,
                    refunds=refunds if refunds else None,
                    vies_progress_callback=_vies_progress_cb)
                _vies_bar.empty()
                _vies_progress_ph.empty()
                with st.spinner("Calcul TVA en cours..."):
                    refund_results = compute_all(refunds, marketplace_name=platform_name)[0] if refunds else []
                    summary = build_report(results, refund_results=refund_results or None)
            else:
                with st.spinner("Calcul TVA en cours..."):
                    results, oss_summary = compute_all(
                        sales, marketplace_name=platform_name, asin_to_category=asin_to_category,
                        apply_fr_under_threshold=apply_fr_under_threshold,
                        refunds=refunds if refunds else None)
                    refund_results = compute_all(refunds, marketplace_name=platform_name)[0] if refunds else []
                    summary = build_report(results, refund_results=refund_results or None)
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

        # Alertes VIES (numéros non vérifiés)
        if vies_summary and vies_summary.total_inconclusive > 0:
            st.error(
                f"🚨 **Attention : {vies_summary.total_inconclusive} numéro(s) de TVA n'ont pas pu être vérifiés auprès de VIES** "
                "(problème de connexion aux serveurs de l'UE). "
                "Allez dans l'onglet **🛡️ VIES** pour les classifier manuellement ou réessayer la vérification."
            )

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
        unregistered = all_stock_countries - set(countries_with_vat)
        pay_eu = {r.vat_country for r in results if r.channel.value == "LOCAL" and r.vat_country}
        unregistered_local = pay_eu - set(countries_with_vat)
        
        registration_needed = {}
        # 1. Stock detection
        for c in unregistered:
            if c: registration_needed.setdefault(c, {"stock": False, "sales": False, "ddp": False})["stock"] = True
        # 2. Local sales detection
        for c in unregistered_local:
            if c: registration_needed.setdefault(c, {"stock": False, "sales": False, "ddp": False})["sales"] = True
        # 3. DDP detection
        if seller_is_importer:
            _ddp_unrg = {r.vat_country for r in results
                if r.scenario.value == "IMPORT_SELLER_AS_IMPORTER"
                and r.vat_country != "FR" and r.vat_country not in countries_with_vat}
            for c in _ddp_unrg:
                if c: registration_needed.setdefault(c, {"stock": False, "sales": False, "ddp": False})["ddp"] = True

        if registration_needed:
            _reg_list = ", ".join(sorted(registration_needed.keys()))
            with st.expander(f"🚨 **Plan d'action Immatriculations : {_reg_list}**", expanded=True):
                st.write("Les pays suivants nécessitent une immatriculation TVA locale pour régulariser votre situation :")
                for c in sorted(registration_needed.keys()):
                    reasons = []
                    icons = ""
                    data = registration_needed[c]
                    if data["stock"]:
                        icons += "📦 "
                        reasons.append("Stock détecté (transferts FBA)")
                    if data["sales"]:
                        icons += "💰 "
                        reasons.append("Ventes locales taxables")
                    if data["ddp"]:
                        icons += "🛃 "
                        reasons.append("Importation DDP")
                    
                    st.markdown(f"- **{_country_label(c)} ({c})** : {icons} — *Raison : {' + '.join(reasons)}*")
                
                critical_blocking = [c for c in registration_needed if c in ["DE", "FR"]]
                if critical_blocking:
                    _c_list = " et ".join(f"**{_country_label(c)} ({c})**" for c in sorted(critical_blocking))
                    st.warning(
                        f"⚠️ **Attention : Risque de blocage Amazon**  \n"
                        f"Pour {_c_list}, Amazon bloque les comptes vendeurs "
                        "sans certificat de TVA valide. Régularisez votre situation au plus vite pour éviter toute interruption d'activité."
                    )

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
        <style>
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
            """accent : couleur hex (ex '#1f77b4' neutre, '#d97706' à faire, '#2ca02c' géré, '#d62728' alerte)"""
            title_attr = f' title="{help_text}"' if help_text else ""
            return f"""
            <div class="kpi-card" style="--kpi-accent:{accent}"{title_attr}>
                <div class="kpi-label">{label}</div>
                <div class="kpi-value">{value}</div>
            </div>
            """

        st.header("📊 Récapitulatif")
        c1, c2, c3, c4 = st.columns(4)

        ca_brut = float(summary.total_ht)
        ca_remb = float(getattr(summary, "refund_total_ht", 0))
        ca_net  = ca_brut + ca_remb

        with c1:
            st.markdown(_kpi_card("CA HT total", _fmt(ca_net), "#1f4e79",
                                  f"CA net de remboursements. Brut : {_fmt(ca_brut)} · Remb : {_fmt(ca_remb)}"), unsafe_allow_html=True)
        with c2:
            st.markdown(_kpi_card("TVA à reverser (vous)", _fmt(float(summary.total_you_owe)), "#d97706",
                                  "TVA France (CA3) + OSS + IOSS — à votre charge."), unsafe_allow_html=True)
        with c3:
            st.markdown(_kpi_card(f"TVA gérée par {platform_name}", _fmt(float(summary.amazon_vat)), "#2ca02c",
                                  "Collectée et reversée par Amazon (deemed supplier)."), unsafe_allow_html=True)
        with c4:
            if abs(total_ecarts_autres) > 0.05:
                st.markdown(_kpi_card("🚨 Écarts de taux Amazon", f"{total_ecarts_autres:+.2f} €", "#d62728"),
                            unsafe_allow_html=True)
                st.markdown('<span class="badge-alert">⚠ Erreur paramétrage</span>', unsafe_allow_html=True)
            else:
                st.markdown(_kpi_card("✅ Concordance Amazon", "0 €", "#2ca02c"), unsafe_allow_html=True)

        # =====================================================================
        # GATING BILLING — calculé AVANT les onglets (voir tva_intracom/ui/billing_gate.py)
        # =====================================================================
        from tva_intracom.ui.billing_gate import build_billing_gate

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
        )
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
            "💶 Déclarations",
            "📋 Détail ventes",
            "🛡️ VIES",
            "🔬 Audit Amazon",
            "📥 Téléchargements",
            "📊 Visualisations",
        ])


        # =====================================================================
        # CONSTRUCTION DU CONTEXTE PARTAGÉ + RENDU DES ONGLETS
        # =====================================================================
        from tva_intracom.ui.tabs.context import TabContext
        from tva_intracom.ui.tabs.declarations import render_declarations
        from tva_intracom.ui.tabs.detail_ventes import render_detail_ventes
        from tva_intracom.ui.tabs.vies import render_vies
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
        )

        with tab_decl:
            render_declarations(_tab_ctx)

        with tab_detail:
            render_detail_ventes(_tab_ctx)

        with tab_vies:
            render_vies(_tab_ctx)

        with tab_audit:
            render_audit(_tab_ctx)

        with tab_dl:
            render_telechargements(_tab_ctx)

        with tab_viz:
            render_visualisations(_tab_ctx)


    except Exception as exc:
        st.error(f"Erreur lors du traitement : {exc}")
        raise
    finally:
        for _p in tmp_paths: _p.unlink(missing_ok=True)

else:
    # Aucun fichier chargé (ou fichier retiré) : la période détectée d'un
    # run précédent ne doit pas rester affichée/exploitable dans la sidebar.
    st.session_state.pop("_period_label", None)
    st.markdown("---")
    col_a, col_b = st.columns([2,1])
    with col_a:
        st.markdown("""
            ### Comment utiliser

            1. **Configuration** : Renseignez votre **SIREN**, vos **numéros de TVA locaux** (France et Europe) ainsi que votre numéro **IOSS** (si applicable) dans la section **Entreprise & Paramètres** de la barre latérale.
            2. **Import Amazon** : Déposez votre rapport de transactions Amazon (**VAT Transactions Report** au format .tsv, .txt, .csv ou .xlsx) dans la zone de dépôt ci-dessus.
            3. **Vérification** : Le moteur calcule automatiquement la TVA due par pays, valide les numéros B2B via VIES et audite les collectes effectuées par Amazon.
            4. **Déclarations** : Consultez les résultats par onglet et téléchargez vos fichiers (XML OSS, rapport CA3, journal FEC, Excel d'audit complet).
        """)
