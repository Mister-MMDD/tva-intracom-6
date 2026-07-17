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


def _orig_currency_cols(r, target_currency: str) -> tuple[str, object]:
    """Colonnes 'Devise' / 'Montant orig.' d'une vente.

    Affichées dès que la devise de transaction d'origine diffère de la devise
    cible actuellement choisie (home_country) — et non plus seulement quand
    elle diffère de l'EUR : si le compte est réglé sur une devise cible non-EUR,
    une vente réalisée en EUR est elle aussi une conversion pertinente à tracer
    pour l'audit (le HT/TVA affichés sont alors dans une autre devise que la
    vente d'origine). Le montant est pré-formaté dans SA PROPRE devise
    d'origine (symbole explicite -> _fmt n'applique aucune conversion), car
    chaque ligne peut avoir une devise de transaction différente.
    """
    if r.sale.original_currency and r.sale.original_currency != target_currency:
        return r.sale.original_currency, _fmt(r.sale.original_amount, symbol=r.sale.original_currency)
    return "", ""


@st.fragment
def render_detail_ventes(ctx: TabContext) -> None:
    """Rendu complet de l'onglet Détail ventes.

    Décoré en `@st.fragment` : un changement de widget à l'intérieur de cet
    onglet (curseur "lignes par page", filtres, tri...) ne redéclenche plus
    le rerun de TOUTE l'application (KPIs, gating billing, onglets VIES /
    Audit / Visualisations...) — seul ce fragment se rejoue. Gain net sur
    gros volumes (5-20k lignes) où le reste de l'app (5 graphiques Plotly,
    appels VIES, etc.) n'a aucune raison de se recalculer juste parce qu'on
    veut voir 500 lignes au lieu de 250.
    """
    results = ctx.results
    refund_results = ctx.refund_results
    _can_export = ctx.can_export

    # Devise cible du pays d'origine choisi (home_country) : les libellés de
    # colonnes HT/TVA affichent cette devise plutôt que "(EUR)" en dur — les
    # montants eux-mêmes sont convertis par _fmt (voir formatting.py).
    _target_currency = st.session_state.get("target_currency", "EUR")
    _lbl_ht = _("col_ht_eur", currency=_target_currency)
    _lbl_vat = _("col_vat_eur", currency=_target_currency)
    _lbl_orig = _("col_orig_amount")
    _orig_cfg = {_lbl_orig: st.column_config.TextColumn(_lbl_orig)}

    # Traductions des en-têtes de colonnes hoistées HORS des boucles
    # ligne-par-ligne ci-dessous : elles sont identiques pour toutes les
    # lignes, donc les calculer une fois ici plutôt qu'à chaque itération
    # évite ~8 lookups i18n x nombre de lignes x 4 sous-onglets (jusqu'à
    # ~640 000 appels superflus par rerun sur un fichier de 20k lignes).
    _c_stock = _("col_stock")
    _c_dest = _("col_dest")
    _c_rate_pct = _("col_rate_pct")
    _c_canal = _("col_canal")
    _c_scenario = _("col_scenario")
    _c_currency = _("col_currency")
    _c_note = _("col_note")
    _c_collector = _("col_collector")

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
        _your_rows = []
        for r in your_results:
            _dev, _orig = _orig_currency_cols(r, _target_currency)
            _your_rows.append({
                "ID":(r.sale.display_id or r.sale.sale_id), _c_stock:r.sale.stock_country, _c_dest:r.sale.buyer_country,
                _lbl_ht:float(r.sale.amount_ht), _c_rate_pct:float(r.vat_rate),
                _lbl_vat:float(r.vat_amount), _c_canal:r.channel.value, _c_scenario:r.scenario.value,
                _c_currency:_dev,
                _lbl_orig:_orig,
                _c_note:r.note})
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
            money_cols=[_lbl_ht, _lbl_vat],
            pct_cols=[_("col_rate_pct")],
            note_cols=[_("col_note")],
            existing_config=_orig_cfg)
        _gated_preview_table(_your_df, _can_export, column_config=_your_cfg)

    with sub_b:
        st.caption(_("subtab_managed_by_tiers_caption"))
        third_results = [r for r in results if r.collector.value != "SELLER"]
        _third_rows = [{
            "ID":(r.sale.display_id or r.sale.sale_id), _c_stock:r.sale.stock_country, _c_dest:r.sale.buyer_country,
            _lbl_ht:float(r.sale.amount_ht), _c_scenario:r.scenario.value,
            _c_collector:r.collector.value, _c_canal:r.channel.value}
            for r in third_results]
        _third_df_full = pd.DataFrame(_third_rows)
        
        # Filtres
        _third_df_filt = _render_filter_bar(_third_df_full, "third")
        
        _third_df = _third_df_filt.copy()
        _third_cfg = _smart_money_df(_third_df, money_cols=[_lbl_ht])
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
        _all_rows = []
        for r in all_sorted:
            _dev, _orig = _orig_currency_cols(r, _target_currency)
            _all_rows.append({
                "ID":(r.sale.display_id or r.sale.sale_id), _c_stock:r.sale.stock_country, _c_dest:r.sale.buyer_country,
                _lbl_ht:float(r.sale.amount_ht), _c_scenario:r.scenario.value,
                _c_rate_pct:float(r.vat_rate), _lbl_vat:float(r.vat_amount),
                _c_canal:r.channel.value,
                _c_currency:_dev,
                _lbl_orig:_orig,
                _c_note:r.note})
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
            money_cols=[_lbl_ht, _lbl_vat],
            pct_cols=[_("col_rate_pct")],
            note_cols=[_("col_note")],
            existing_config=_orig_cfg)
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
                "ID":(r.sale.display_id or r.sale.sale_id), _c_stock:r.sale.stock_country, _c_dest:r.sale.buyer_country,
                _lbl_ht:float(r.sale.amount_ht), _c_scenario:r.scenario.value,
                _c_rate_pct:float(r.vat_rate), _lbl_vat:float(r.vat_amount),
                _c_canal:r.channel.value}
                for r in ref_sorted]
            _ref_df_full = pd.DataFrame(_ref_rows)
            
            # Filtres
            _ref_df_filt = _render_filter_bar(_ref_df_full, "refund")
            
            _ref_df = _ref_df_filt.copy()
            _ref_cfg = _smart_money_df(_ref_df,
                money_cols=[_lbl_ht, _lbl_vat],
                pct_cols=[_("col_rate_pct")])
            _gated_preview_table(_ref_df, _can_export, column_config=_ref_cfg)