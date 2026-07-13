"""Onglet "Visualisations" (extrait tel quel de app.py, with tab_viz:).

TVA due par pays (barres empilées), répartition Vous/Amazon/Douane
(camembert), carte choroplèthe Europe, évolution mensuelle (CA/TVA),
répartition par scénario.
"""

from __future__ import annotations

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st
from tva_intracom.i18n import _

from tva_intracom.rates import COUNTRY_ISO3
from tva_intracom.ui.formatting import _country_label, _fmt, _get_conversion_rate
from tva_intracom.ui.tabs.context import TabContext


def render_visualisations(ctx: TabContext) -> None:
    """Rendu complet de l'onglet Visualisations."""
    results = ctx.results
    refund_results = ctx.refund_results
    summary = ctx.summary
    platform_name = ctx.platform_name
    _can_export = ctx.can_export

    # Devise cible du pays d'origine choisi (home_country) : tous les montants
    # ci-dessous sont calculés en EUR par le moteur fiscal et convertis ici
    # pour affichage (voir _get_conversion_rate, formatting.py).
    _currency, _rate = _get_conversion_rate()
    _currency_symbol = st.session_state.get("currency_symbol", "€")

    # Calcul des données nettes (Ventes + Remboursements) ventilées par type
    # Structure : { "FR": {"OSS": 0, "Local": 100}, "DE": {"OSS": 50, "Local": 0} }
    viz_data_by_country: dict[str, dict[str, float]] = {}

    # 1. TVA France (CA3)
    if summary.net_fr_domestic_vat != 0:
        viz_data_by_country.setdefault("FR", {})[_("viz_france_ca3")] = float(summary.net_fr_domestic_vat)

    # 2. TVA OSS
    # Note: On utilise summary.net_oss_by_country qui contient (Ventes + Remboursements)
    for c, a in summary.net_oss_by_country.items():
        if a != 0:
            viz_data_by_country.setdefault(c, {})[_("viz_oss_window")] = float(a)

    # 3. TVA Locale
    # Note: On utilise summary.net_local_by_country qui contient (Ventes + Remboursements)
    for c, a in summary.net_local_by_country.items():
        if a != 0:
            viz_data_by_country.setdefault(c, {})[_("viz_local_tax")] = float(a)

    # Total net par pays pour le tri et la carte
    vat_net_by_country = {c: sum(types.values()) for c, types in viz_data_by_country.items()}

    st.subheader(_("viz_vat_by_country_subheader"))
    if not _can_export:
        st.info(_("viz_locked_geography_info"))
    elif viz_data_by_country:
        # Préparation des données pour un Bar Chart empilé (Stacked Bar)
        # On trie par total décroissant
        sorted_countries = sorted(vat_net_by_country.keys(), key=lambda c: -vat_net_by_country[c])

        types = [_("viz_france_ca3"), _("viz_oss_window"), _("viz_local_tax")]
        colors = {
            _("viz_france_ca3"): "#2ca02c", 
            _("viz_oss_window"): "#1f77b4", 
            _("viz_local_tax"): "#9467bd"
        }

        fig_bar = go.Figure()
        for t in types:
            # Conversion EUR (devise de calcul interne) -> devise cible du
            # pays d'origine choisi, avant affichage (voir _get_conversion_rate).
            vals = [viz_data_by_country[c].get(t, 0) * _rate for c in sorted_countries]
            # On prépare les totaux par pays pour les afficher dans la bulle d'aide (tooltip)
            totals = [vat_net_by_country[c] * _rate for c in sorted_countries]
            
            if any(v != 0 for v in vals):
                symbol = st.session_state.get("currency_symbol", "€")
                fig_bar.add_trace(go.Bar(
                    name=t,
                    x=[_country_label(c) for c in sorted_countries],
                    y=vals,
                    customdata=totals,
                    # Modification de la bulle d'aide : une ligne pour le total pays, 
                    # une ligne pour le canal spécifique (CA3, OSS, etc.)
                    hovertemplate=(
                        _("viz_tooltip_pays") +
                        _("viz_tooltip_total_pays") +
                        _("viz_tooltip_canal") +
                        "<extra></extra>"
                    ).replace("€", symbol),
                    marker_color=colors.get(t),
                    text=[f"{v:,.2f}{symbol}" if v != 0 else "" for v in vals],
                    textposition="auto"
                ))

        fig_bar.update_layout(
            barmode='relative', # 'relative' permet d'empiler correctement les négatifs si besoin
            yaxis_title=_("viz_yaxis_vat_title"),
            height=450,
            margin=dict(t=40, b=40),
            # On place la légende en haut pour éviter la superposition avec la barre d'outils (modebar)
            legend=dict(orientation="h", yanchor="bottom", y=1.05, xanchor="center", x=0.5)
        )
        st.plotly_chart(fig_bar, use_container_width=True)

    st.divider()

    ch1, ch2 = st.columns(2)
    with ch1:
        st.subheader(_("viz_repartition_you_market_subheader", platform=platform_name))
        pie_l, pie_v, pie_c = [], [], []
        if float(summary.total_you_owe)>0: pie_l.append(_("viz_you")); pie_v.append(float(summary.total_you_owe) * _rate); pie_c.append("#2ca02c")
        if float(summary.amazon_vat)>0: pie_l.append(platform_name); pie_v.append(float(summary.amazon_vat) * _rate); pie_c.append("#ff7f0e")
        if float(summary.import_vat)>0: pie_l.append(_("viz_customs")); pie_v.append(float(summary.import_vat) * _rate); pie_c.append("#9467bd")
        if pie_v:
            fig_pie = go.Figure(go.Pie(labels=pie_l, values=pie_v,
                marker=dict(colors=pie_c), hole=0.4, textinfo="label+percent",
                hovertemplate=f"%{{label}} : %{{value:,.2f}} {_currency_symbol} (%{{percent}})<extra></extra>"))
            fig_pie.update_layout(height=400, margin=dict(t=20,b=20),
                legend=dict(orientation="h", yanchor="bottom", y=-0.1, xanchor="center", x=0.5))
            st.plotly_chart(fig_pie, use_container_width=True)

    with ch2:
        st.subheader(_("viz_map_subheader"))
        if not _can_export:
            st.info(_("viz_locked_map_info"))
        elif vat_net_by_country:
            map_data = [{"iso_alpha": COUNTRY_ISO3[c], "pays": _country_label(c), "tva": amt * _rate}
                for c, amt in vat_net_by_country.items() if c in COUNTRY_ISO3]
            if map_data:
                fig_map = px.choropleth(map_data, locations="iso_alpha", color="tva",
                    hover_name="pays", color_continuous_scale="YlOrRd", scope="europe",
                    labels={"tva": _("viz_map_label_vat")})
                fig_map.update_layout(
                    height=400, 
                    margin=dict(t=10,b=10,l=0,r=0),
                    coloraxis_colorbar=dict(
                        thicknessmode="pixels", thickness=15,
                        lenmode="pixels", len=200,
                        yanchor="middle", y=0.5,
                        xanchor="right", x=1.05 # On rapproche la barre de la carte
                    )
                )
                st.plotly_chart(fig_map, use_container_width=True)

    # ── B : Évolution temporelle ──────────────────────────────────────
    st.subheader(_("viz_evolution_subheader"))
    _monthly: dict = {}
    for r in results:
        _d = r.sale.transaction_date
        if _d and len(_d) >= 7:
            _ym = _d[:7]
            if _ym not in _monthly:
                _monthly[_ym] = {"CA HT": 0.0, "TVA due": 0.0, "Remb. HT": 0.0, "TVA remb.": 0.0}
            if r.sale.amount_ht > 0:
                _monthly[_ym]["CA HT"]   += float(r.sale.amount_ht)
                _monthly[_ym]["TVA due"]  += float(r.vat_amount)
    for r in (refund_results or []):
        _d = r.sale.transaction_date
        if _d and len(_d) >= 7:
            _ym = _d[:7]
            if _ym not in _monthly:
                _monthly[_ym] = {"CA HT": 0.0, "TVA due": 0.0, "Remb. HT": 0.0, "TVA remb.": 0.0}
            _monthly[_ym]["Remb. HT"]  += float(r.sale.amount_ht)   # négatif
            _monthly[_ym]["TVA remb."] += float(r.vat_amount)        # négatif

    if len(_monthly) >= 2:
        _months_sorted = sorted(_monthly.keys())
        _MOIS_MAP = {
            "01": _("jan"), "02": _("feb"), "03": _("mar"), "04": _("apr"),
            "05": _("may"), "06": _("jun"), "07": _("jul"), "08": _("aug"),
            "09": _("sep"), "10": _("oct"), "11": _("nov"), "12": _("dec")
        }
        def _mois_label(ym: str) -> str:
            y, m = ym.split("-")
            return f"{_MOIS_MAP.get(m, m)} {y}"
        
        _col_ca_sales = _("viz_evolution_ca_sales")
        _col_refunds_ht = _("viz_evolution_refunds_ht")
        _col_vat_net = _("viz_evolution_vat_net")
        _col_month = _("month_column_label")
        
        _df_monthly = pd.DataFrame([
            {_col_month: _mois_label(m),
             _col_ca_sales: _monthly[m]["CA HT"] * _rate,
             _col_refunds_ht: _monthly[m]["Remb. HT"] * _rate,
             _col_vat_net: (_monthly[m]["TVA due"] + _monthly[m]["TVA remb."]) * _rate}
            for m in _months_sorted
        ])
        _tviz1, _tviz2 = st.columns(2)
        with _tviz1:
            fig_time = go.Figure()
            fig_time.add_trace(go.Bar(
                name=_col_ca_sales, x=_df_monthly[_col_month],
                y=_df_monthly[_col_ca_sales], marker_color="#1f77b4",
                hovertemplate="%{x}<br>" + _col_ca_sales + f" : %{{y:,.2f}} {_currency_symbol}<extra></extra>",
            ))
            fig_time.add_trace(go.Bar(
                name=_col_refunds_ht, x=_df_monthly[_col_month],
                y=_df_monthly[_col_refunds_ht], marker_color="#d62728",
                hovertemplate="%{x}<br>" + _col_refunds_ht + f" : %{{y:,.2f}} {_currency_symbol}<extra></extra>",
            ))
            fig_time.add_trace(go.Scatter(
                name=_col_vat_net, x=_df_monthly[_col_month],
                y=_df_monthly[_col_vat_net], mode="lines+markers",
                line=dict(color="#ff7f0e", width=2), yaxis="y2",
                hovertemplate="%{x}<br>" + _col_vat_net + f" : %{{y:,.2f}} {_currency_symbol}<extra></extra>",
            ))
            fig_time.update_layout(
                barmode="relative", height=360,
                xaxis=dict(type="category"),
                yaxis=dict(title=_("viz_evolution_yaxis_ca"), tickformat=",.0f"),
                yaxis2=dict(title=_("viz_evolution_yaxis_vat"), overlaying="y", side="right",
                            showgrid=False, tickformat=",.0f"),
                legend=dict(orientation="h", y=1.08),
                margin=dict(t=40, b=40),
                hovermode="x unified",
            )
            st.plotly_chart(fig_time, use_container_width=True)
        with _tviz2:
            # ── F : Répartition par scénario ─────────────────────────
            st.markdown(_("viz_scenario_markdown"))
            _scen_counts: dict = {}
            _scen_ht: dict = {}
            for r in results:
                _sc = r.scenario.value
                _scen_counts[_sc] = _scen_counts.get(_sc, 0) + 1
                _scen_ht[_sc] = _scen_ht.get(_sc, 0.0) + float(r.sale.amount_ht)
            _scen_data = sorted(_scen_counts.items(), key=lambda x: -x[1])
            fig_scen = go.Figure()
            fig_scen.add_trace(go.Bar(
                name=_("viz_nb_transactions"),
                x=[s for s, _unused in _scen_data],
                y=[n for _unused, n in _scen_data],
                marker_color="#1f77b4",
                text=[str(n) for _unused, n in _scen_data],
                textposition="auto",
            ))
            fig_scen.update_layout(height=360, margin=dict(t=20, b=60),
                xaxis_tickangle=-30, yaxis_title=_("viz_nb_transactions"))
            st.plotly_chart(fig_scen, use_container_width=True)
            st.caption(" · ".join(
                _("viz_scen_caption", scen=s, n=n, ht=f"{_scen_ht.get(s, 0) * _rate:,.0f}", currency=_currency_symbol)
                for s, n in _scen_data
            ))
    elif _monthly:
        st.caption(_("viz_single_month_caption"))