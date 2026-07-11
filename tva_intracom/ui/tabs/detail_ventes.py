"""Onglet "Détail ventes" (extrait tel quel de app.py, with tab_detail:).

Quatre sous-onglets : Ce que vous devez, Géré par des tiers, Ligne par
ligne, Remboursements.
"""

from __future__ import annotations

import pandas as pd
import streamlit as st

from tva_intracom.ui.formatting import _fmt, _gated_preview_table, _smart_money_df, _render_filter_bar
from tva_intracom.ui.tabs.context import TabContext


def render_detail_ventes(ctx: TabContext) -> None:
    """Rendu complet de l'onglet Détail ventes."""
    results = ctx.results
    refund_results = ctx.refund_results
    _can_export = ctx.can_export

    sub_a, sub_b, sub_c, sub_d = st.tabs([
        "💸 Ce que vous devez", "🤝 Géré par des tiers", "📄 Ligne par ligne",
        f"🔄 Remboursements ({len(refund_results or [])})",
    ])

    with sub_a:
        st.caption("Ventes dont vous êtes responsable de la TVA.")
        your_results = [r for r in results if r.collector.value == "SELLER"]
        sort_yours = st.radio("Trier par", ["Pays","Taux","HT"], horizontal=True, key="sort_yours")
        if sort_yours == "Pays": your_results.sort(key=lambda r: r.vat_country)
        elif sort_yours == "Taux": your_results.sort(key=lambda r: -r.vat_rate)
        else: your_results.sort(key=lambda r: -r.sale.amount_ht)
        _your_rows = [{
            "ID":(r.sale.display_id or r.sale.sale_id), "Stock":r.sale.stock_country, "Dest":r.sale.buyer_country,
            "HT (EUR)":float(r.sale.amount_ht), "Taux %":float(r.vat_rate),
            "TVA (EUR)":float(r.vat_amount), "Canal":r.channel.value, "Scénario":r.scenario.value,
            "Devise":r.sale.original_currency if r.sale.original_currency != "EUR" else "",
            "Montant orig.":float(r.sale.original_amount) if r.sale.original_currency != "EUR" else None,
            "Note":r.note}
            for r in your_results]
        _your_df_full = pd.DataFrame(_your_rows)
        
        # Filtres
        _your_df_filt = _render_filter_bar(_your_df_full, "your")

        # Pagination
        _ps_your = st.select_slider("Lignes par page", options=[100, 250, 500, 1000, "Toutes"],
            value=250, key="page_size_your")
        _n_your = len(_your_df_filt)
        _lim_your = _n_your if _ps_your == "Toutes" else int(_ps_your)
        st.caption(f"{_n_your} ligne(s) {'(filtrées)' if _n_your < len(_your_df_full) else ''} — affichage : {min(_lim_your, _n_your)}")
        
        _your_df = _your_df_filt.head(_lim_your).copy()
        _your_cfg = _smart_money_df(_your_df,
            money_cols=["HT (EUR)", "TVA (EUR)", "Montant orig."],
            pct_cols=["Taux %"],
            note_cols=["Note"])
        _gated_preview_table(_your_df, _can_export, column_config=_your_cfg)

    with sub_b:
        st.caption("Ventes dont Amazon ou la douane collecte la TVA.")
        third_results = [r for r in results if r.collector.value != "SELLER"]
        _third_rows = [{
            "ID":(r.sale.display_id or r.sale.sale_id), "Stock":r.sale.stock_country, "Dest":r.sale.buyer_country,
            "HT (EUR)":float(r.sale.amount_ht), "Scénario":r.scenario.value,
            "Collecteur":r.collector.value, "Canal":r.channel.value}
            for r in third_results]
        _third_df_full = pd.DataFrame(_third_rows)
        
        # Filtres
        _third_df_filt = _render_filter_bar(_third_df_full, "third")
        
        _third_df = _third_df_filt.copy()
        _third_cfg = _smart_money_df(_third_df, money_cols=["HT (EUR)"])
        _gated_preview_table(_third_df, _can_export, column_config=_third_cfg)

    with sub_c:
        st.caption("Toutes les ventes, ligne par ligne.")
        sort_all = st.radio("Trier par", ["Pays","Taux","HT"], horizontal=True, key="sort_all")
        all_sorted = sorted(results,
            key=lambda r: r.vat_country if sort_all=="Pays" else (-r.vat_rate if sort_all=="Taux" else -r.sale.amount_ht))
        _all_rows = [{
            "ID":(r.sale.display_id or r.sale.sale_id), "Stock":r.sale.stock_country, "Dest":r.sale.buyer_country,
            "HT (EUR)":float(r.sale.amount_ht), "Scénario":r.scenario.value,
            "Taux %":float(r.vat_rate), "TVA (EUR)":float(r.vat_amount),
            "Canal":r.channel.value,
            "Devise":r.sale.original_currency if r.sale.original_currency != "EUR" else "",
            "Montant orig.":float(r.sale.original_amount) if r.sale.original_currency != "EUR" else None,
            "Note":r.note}
            for r in all_sorted]
        _all_df_full = pd.DataFrame(_all_rows)

        # Filtres
        _all_df_filt = _render_filter_bar(_all_df_full, "all")

        # Pagination
        _page_size_all = st.select_slider("Lignes par page", options=[100, 250, 500, 1000, "Toutes"],
            value=250, key="page_size_all")
        _n_all = len(_all_df_filt)
        _limit_all = _n_all if _page_size_all == "Toutes" else int(_page_size_all)
        st.caption(f"{_n_all} ligne(s) {'(filtrées)' if _n_all < len(_all_df_full) else ''} — affichage : {min(_limit_all, _n_all)}")

        _all_df_page = _all_df_filt.head(_limit_all).copy()
        _all_cfg = _smart_money_df(_all_df_page,
            money_cols=["HT (EUR)", "TVA (EUR)", "Montant orig."],
            pct_cols=["Taux %"],
            note_cols=["Note"])
        _gated_preview_table(_all_df_page, _can_export, column_config=_all_cfg)

    with sub_d:
        if not refund_results:
            st.info("ℹ️ Aucun remboursement dans ce fichier.")
        else:
            _ref_ht  = sum(float(r.sale.amount_ht) for r in refund_results)
            _ref_tva = sum(float(r.vat_amount)     for r in refund_results)
            ra, rb, rc = st.columns(3)
            ra.metric("Remboursements", len(refund_results))
            rb.metric("HT total remboursé", _fmt(_ref_ht))
            rc.metric("TVA restituée", _fmt(_ref_tva))
            sort_ref = st.radio("Trier par", ["Pays","Taux","HT"], horizontal=True, key="sort_ref")
            ref_sorted = sorted(refund_results,
                key=lambda r: r.vat_country if sort_ref=="Pays" else (-r.vat_rate if sort_ref=="Taux" else r.sale.amount_ht))
            _ref_rows = [{
                "ID":(r.sale.display_id or r.sale.sale_id), "Stock":r.sale.stock_country, "Dest":r.sale.buyer_country,
                "HT (EUR)":float(r.sale.amount_ht), "Scénario":r.scenario.value,
                "Taux %":float(r.vat_rate), "TVA (EUR)":float(r.vat_amount),
                "Canal":r.channel.value}
                for r in ref_sorted]
            _ref_df_full = pd.DataFrame(_ref_rows)
            
            # Filtres
            _ref_df_filt = _render_filter_bar(_ref_df_full, "refund")
            
            _ref_df = _ref_df_filt.copy()
            _ref_cfg = _smart_money_df(_ref_df,
                money_cols=["HT (EUR)", "TVA (EUR)"],
                pct_cols=["Taux %"])
            _gated_preview_table(_ref_df, _can_export, column_config=_ref_cfg)
