"""Onglet "Détail ventes" (extrait tel quel de app.py, with tab_detail:).

Quatre sous-onglets : Ce que vous devez, Géré par des tiers, Ligne par
ligne, Remboursements.
"""

from __future__ import annotations

import pandas as pd
import streamlit as st
from tva_intracom.i18n import _

from tva_intracom.ui.formatting import _fmt, _gated_preview_table, _smart_money_df, _render_filter_bar
from tva_intracom.ui.tabs.context import TabContext


def render_detail_ventes(ctx: TabContext) -> None:
    """Rendu complet de l'onglet Détail ventes."""
    results = ctx.results
    refund_results = ctx.refund_results
    _can_export = ctx.can_export

    sub_a, sub_b, sub_c, sub_d = st.tabs([
        _("subtab_what_you_owe"), _("subtab_managed_by_tiers"), _("subtab_row_by_row"),
        _("subtab_refunds", count=len(refund_results or [])),
    ])

    with sub_a:
        st.caption(_("what_you_owe_caption"))
        your_results = [r for r in results if r.collector.value == "SELLER"]
        _sort_opts = {
            _("sort_country"): "Pays",
            _("sort_rate"): "Taux",
            _("sort_ht"): "HT"
        }
        sort_yours_lbl = st.radio(_("sort_by_label"), list(_sort_opts.keys()), horizontal=True, key="sort_yours")
        sort_yours = _sort_opts[sort_yours_lbl]
        if sort_yours == "Pays": your_results.sort(key=lambda r: r.vat_country)
        elif sort_yours == "Taux": your_results.sort(key=lambda r: -r.vat_rate)
        else: your_results.sort(key=lambda r: -r.sale.amount_ht)
        _your_rows = [{
            "ID":(r.sale.display_id or r.sale.sale_id), _("col_stock"):r.sale.stock_country, _("col_dest"):r.sale.buyer_country,
            _("col_ht_eur"):float(r.sale.amount_ht), _("col_rate_pct"):float(r.vat_rate),
            _("col_vat_eur"):float(r.vat_amount), _("col_canal"):r.channel.value, _("col_scenario"):r.scenario.value,
            _("col_currency"):r.sale.original_currency if r.sale.original_currency != "EUR" else "",
            _("col_orig_amount"):float(r.sale.original_amount) if r.sale.original_currency != "EUR" else None,
            _("col_note"):r.note}
            for r in your_results]
        _your_df_full = pd.DataFrame(_your_rows)
        
        # Filtres
        _your_df_filt = _render_filter_bar(_your_df_full, "your")

        # Pagination
        _ps_your = st.select_slider(_("rows_per_page_label"), options=[100, 250, 500, 1000, _("rows_all")],
            value=250, key="page_size_your")
        _n_your = len(_your_df_filt)
        _lim_your = _n_your if _ps_your == _("rows_all") else int(_ps_your)
        st.caption(_("results_count_caption", count=_n_your, filtered=(_("results_filtered_tag") if _n_your < len(_your_df_full) else ''), visible=min(_lim_your, _n_your)))
        
        _your_df = _your_df_filt.head(_lim_your).copy()
        _your_cfg = _smart_money_df(_your_df,
            money_cols=[_("col_ht_eur"), _("col_vat_eur"), _("col_orig_amount")],
            pct_cols=[_("col_rate_pct")],
            note_cols=[_("col_note")])
        _gated_preview_table(_your_df, _can_export, column_config=_your_cfg)

    with sub_b:
        st.caption(_("subtab_managed_by_tiers_caption"))
        third_results = [r for r in results if r.collector.value != "SELLER"]
        _third_rows = [{
            "ID":(r.sale.display_id or r.sale.sale_id), _("col_stock"):r.sale.stock_country, _("col_dest"):r.sale.buyer_country,
            _("col_ht_eur"):float(r.sale.amount_ht), _("col_scenario"):r.scenario.value,
            _("col_collector"):r.collector.value, _("col_canal"):r.channel.value}
            for r in third_results]
        _third_df_full = pd.DataFrame(_third_rows)
        
        # Filtres
        _third_df_filt = _render_filter_bar(_third_df_full, "third")
        
        _third_df = _third_df_filt.copy()
        _third_cfg = _smart_money_df(_third_df, money_cols=[_("col_ht_eur")])
        _gated_preview_table(_third_df, _can_export, column_config=_third_cfg)

    with sub_c:
        st.caption(_("subtab_row_by_row_caption"))
        _sort_all_opts = {
            _("sort_country"): "Pays",
            _("sort_rate"): "Taux",
            _("sort_ht"): "HT"
        }
        sort_all_lbl = st.radio(_("sort_by_label"), list(_sort_all_opts.keys()), horizontal=True, key="sort_all")
        sort_all = _sort_all_opts[sort_all_lbl]
        all_sorted = sorted(results,
            key=lambda r: r.vat_country if sort_all=="Pays" else (-r.vat_rate if sort_all=="Taux" else -r.sale.amount_ht))
        _all_rows = [{
            "ID":(r.sale.display_id or r.sale.sale_id), _("col_stock"):r.sale.stock_country, _("col_dest"):r.sale.buyer_country,
            _("col_ht_eur"):float(r.sale.amount_ht), _("col_scenario"):r.scenario.value,
            _("col_rate_pct"):float(r.vat_rate), _("col_vat_eur"):float(r.vat_amount),
            _("col_canal"):r.channel.value,
            _("col_currency"):r.sale.original_currency if r.sale.original_currency != "EUR" else "",
            _("col_orig_amount"):float(r.sale.original_amount) if r.sale.original_currency != "EUR" else None,
            _("col_note"):r.note}
            for r in all_sorted]
        _all_df_full = pd.DataFrame(_all_rows)

        # Filtres
        _all_df_filt = _render_filter_bar(_all_df_full, "all")

        # Pagination
        _page_size_all = st.select_slider(_("rows_per_page_label"), options=[100, 250, 500, 1000, _("rows_all")],
            value=250, key="page_size_all")
        _n_all = len(_all_df_filt)
        _limit_all = _n_all if _page_size_all == _("rows_all") else int(_page_size_all)
        st.caption(_("results_count_caption", count=_n_all, filtered=(_("results_filtered_tag") if _n_all < len(_all_df_full) else ''), visible=min(_limit_all, _n_all)))

        _all_df_page = _all_df_filt.head(_limit_all).copy()
        _all_cfg = _smart_money_df(_all_df_page,
            money_cols=[_("col_ht_eur"), _("col_vat_eur"), _("col_orig_amount")],
            pct_cols=[_("col_rate_pct")],
            note_cols=[_("col_note")])
        _gated_preview_table(_all_df_page, _can_export, column_config=_all_cfg)

    with sub_d:
        if not refund_results:
            st.info(_("no_refunds_info"))
        else:
            _ref_ht  = sum(float(r.sale.amount_ht) for r in refund_results)
            _ref_tva = sum(float(r.vat_amount)     for r in refund_results)
            ra, rb, rc = st.columns(3)
            ra.metric(_("kpi_refunds"), len(refund_results))
            rb.metric(_("kpi_ht_refunded"), _fmt(_ref_ht))
            rc.metric(_("kpi_vat_restituted"), _fmt(_ref_tva))
            _sort_ref_opts = {
                _("sort_country"): "Pays",
                _("sort_rate"): "Taux",
                _("sort_ht"): "HT"
            }
            sort_ref_lbl = st.radio(_("sort_by_label"), list(_sort_ref_opts.keys()), horizontal=True, key="sort_ref")
            sort_ref = _sort_ref_opts[sort_ref_lbl]
            ref_sorted = sorted(refund_results,
                key=lambda r: r.vat_country if sort_ref=="Pays" else (-r.vat_rate if sort_ref=="Taux" else r.sale.amount_ht))
            _ref_rows = [{
                "ID":(r.sale.display_id or r.sale.sale_id), _("col_stock"):r.sale.stock_country, _("col_dest"):r.sale.buyer_country,
                _("col_ht_eur"):float(r.sale.amount_ht), _("col_scenario"):r.scenario.value,
                _("col_rate_pct"):float(r.vat_rate), _("col_vat_eur"):float(r.vat_amount),
                _("col_canal"):r.channel.value}
                for r in ref_sorted]
            _ref_df_full = pd.DataFrame(_ref_rows)
            
            # Filtres
            _ref_df_filt = _render_filter_bar(_ref_df_full, "refund")
            
            _ref_df = _ref_df_filt.copy()
            _ref_cfg = _smart_money_df(_ref_df,
                money_cols=[_("col_ht_eur"), _("col_vat_eur")],
                pct_cols=[_("col_rate_pct")])
            _gated_preview_table(_ref_df, _can_export, column_config=_ref_cfg)
