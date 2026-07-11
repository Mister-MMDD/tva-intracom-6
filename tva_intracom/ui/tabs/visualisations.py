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

from tva_intracom.rates import COUNTRY_ISO3
from tva_intracom.ui.formatting import _country_label
from tva_intracom.ui.tabs.context import TabContext


def render_visualisations(ctx: TabContext) -> None:
    """Rendu complet de l'onglet Visualisations."""
    results = ctx.results
    refund_results = ctx.refund_results
    summary = ctx.summary
    platform_name = ctx.platform_name
    _can_export = ctx.can_export

    # Calcul des données nettes (Ventes + Remboursements) ventilées par type
    # Structure : { "FR": {"OSS": 0, "Local": 100}, "DE": {"OSS": 50, "Local": 0} }
    viz_data_by_country: dict[str, dict[str, float]] = {}

    # 1. TVA France (CA3)
    if summary.net_fr_domestic_vat != 0:
        viz_data_by_country.setdefault("FR", {})["France (CA3)"] = float(summary.net_fr_domestic_vat)

    # 2. TVA OSS
    # Note: On utilise summary.net_oss_by_country qui contient (Ventes + Remboursements)
    for c, a in summary.net_oss_by_country.items():
        if a != 0:
            viz_data_by_country.setdefault(c, {})["Guichet OSS"] = float(a)

    # 3. TVA Locale
    # Note: On utilise summary.net_local_by_country qui contient (Ventes + Remboursements)
    for c, a in summary.net_local_by_country.items():
        if a != 0:
            viz_data_by_country.setdefault(c, {})["Fisc local"] = float(a)

    # Total net par pays pour le tri et la carte
    vat_net_by_country = {c: sum(types.values()) for c, types in viz_data_by_country.items()}

    st.subheader("TVA due par pays (Net)")
    if not _can_export:
        st.info("🔒 Détail par pays verrouillé. Débloquez la période pour visualiser la répartition géographique.")
    elif viz_data_by_country:
        # Préparation des données pour un Bar Chart empilé (Stacked Bar)
        # On trie par total décroissant
        sorted_countries = sorted(vat_net_by_country.keys(), key=lambda c: -vat_net_by_country[c])

        types = ["France (CA3)", "Guichet OSS", "Fisc local"]
        colors = {"France (CA3)": "#2ca02c", "Guichet OSS": "#1f77b4", "Fisc local": "#9467bd"}

        fig_bar = go.Figure()
        for t in types:
            vals = [viz_data_by_country[c].get(t, 0) for c in sorted_countries]
            # On prépare les totaux par pays pour les afficher dans la bulle d'aide (tooltip)
            totals = [vat_net_by_country[c] for c in sorted_countries]
            
            if any(v != 0 for v in vals):
                fig_bar.add_trace(go.Bar(
                    name=t,
                    x=[_country_label(c) for c in sorted_countries],
                    y=vals,
                    customdata=totals,
                    # Modification de la bulle d'aide : une ligne pour le total pays, 
                    # une ligne pour le canal spécifique (CA3, OSS, etc.)
                    hovertemplate=(
                        "<b>%{x}</b><br>"
                        "Total pays : %{customdata:,.2f} €<br>"
                        "%{fullData.name} : %{y:,.2f} €"
                        "<extra></extra>"
                    ),
                    marker_color=colors.get(t),
                    text=[f"{v:,.2f}€" if v != 0 else "" for v in vals],
                    textposition="auto"
                ))

        fig_bar.update_layout(
            barmode='relative', # 'relative' permet d'empiler correctement les négatifs si besoin
            yaxis_title="Montant TVA Net (EUR)",
            height=450,
            margin=dict(t=40, b=40),
            # On place la légende en haut pour éviter la superposition avec la barre d'outils (modebar)
            legend=dict(orientation="h", yanchor="bottom", y=1.05, xanchor="center", x=0.5)
        )
        st.plotly_chart(fig_bar, use_container_width=True)

    st.divider()

    ch1, ch2 = st.columns(2)
    with ch1:
        st.subheader(f"Répartition : Vous vs {platform_name}")
        pie_l, pie_v, pie_c = [], [], []
        if float(summary.total_you_owe)>0: pie_l.append("Vous"); pie_v.append(float(summary.total_you_owe)); pie_c.append("#2ca02c")
        if float(summary.amazon_vat)>0: pie_l.append(platform_name); pie_v.append(float(summary.amazon_vat)); pie_c.append("#ff7f0e")
        if float(summary.import_vat)>0: pie_l.append("Douane"); pie_v.append(float(summary.import_vat)); pie_c.append("#9467bd")
        if pie_v:
            fig_pie = go.Figure(go.Pie(labels=pie_l, values=pie_v,
                marker=dict(colors=pie_c), hole=0.4, textinfo="label+percent"))
            fig_pie.update_layout(height=400, margin=dict(t=20,b=20),
                legend=dict(orientation="h", yanchor="bottom", y=-0.1, xanchor="center", x=0.5))
            st.plotly_chart(fig_pie, use_container_width=True)

    with ch2:
        st.subheader("🗺️ Carte de la TVA en Europe (Net)")
        if not _can_export:
            st.info("🔒 Carte interactive verrouillée. Débloquez la période pour visualiser les zones fiscales.")
        elif vat_net_by_country:
            map_data = [{"iso_alpha": COUNTRY_ISO3[c], "pays": _country_label(c), "tva": amt}
                for c, amt in vat_net_by_country.items() if c in COUNTRY_ISO3]
            if map_data:
                fig_map = px.choropleth(map_data, locations="iso_alpha", color="tva",
                    hover_name="pays", color_continuous_scale="YlOrRd", scope="europe",
                    labels={"tva": "TVA Nette (EUR)"})
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
    st.subheader("📅 Évolution mensuelle")
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
        _MOIS_FR = {"01":"Jan","02":"Fév","03":"Mar","04":"Avr","05":"Mai","06":"Juin",
                    "07":"Juil","08":"Août","09":"Sep","10":"Oct","11":"Nov","12":"Déc"}
        def _mois_label(ym: str) -> str:
            y, m = ym.split("-")
            return f"{_MOIS_FR.get(m, m)} {y}"
        _df_monthly = pd.DataFrame([
            {"Mois": _mois_label(m),
             "CA HT ventes": _monthly[m]["CA HT"],
             "Remb. HT": _monthly[m]["Remb. HT"],
             "TVA nette": _monthly[m]["TVA due"] + _monthly[m]["TVA remb."]}
            for m in _months_sorted
        ])
        _tviz1, _tviz2 = st.columns(2)
        with _tviz1:
            fig_time = go.Figure()
            fig_time.add_trace(go.Bar(
                name="CA HT ventes", x=_df_monthly["Mois"],
                y=_df_monthly["CA HT ventes"], marker_color="#1f77b4",
                hovertemplate="%{x}<br>CA HT : %{y:,.2f} €<extra></extra>",
            ))
            fig_time.add_trace(go.Bar(
                name="Remb. HT", x=_df_monthly["Mois"],
                y=_df_monthly["Remb. HT"], marker_color="#d62728",
                hovertemplate="%{x}<br>Remb. : %{y:,.2f} €<extra></extra>",
            ))
            fig_time.add_trace(go.Scatter(
                name="TVA nette", x=_df_monthly["Mois"],
                y=_df_monthly["TVA nette"], mode="lines+markers",
                line=dict(color="#ff7f0e", width=2), yaxis="y2",
                hovertemplate="%{x}<br>TVA nette : %{y:,.2f} €<extra></extra>",
            ))
            fig_time.update_layout(
                barmode="relative", height=360,
                xaxis=dict(type="category"),
                yaxis=dict(title="CA HT (EUR)", tickformat=",.0f"),
                yaxis2=dict(title="TVA (EUR)", overlaying="y", side="right",
                            showgrid=False, tickformat=",.0f"),
                legend=dict(orientation="h", y=1.08),
                margin=dict(t=40, b=40),
                hovermode="x unified",
            )
            st.plotly_chart(fig_time, use_container_width=True)
        with _tviz2:
            # ── F : Répartition par scénario ─────────────────────────
            st.markdown("**Répartition par scénario**")
            _scen_counts: dict = {}
            _scen_ht: dict = {}
            for r in results:
                _sc = r.scenario.value
                _scen_counts[_sc] = _scen_counts.get(_sc, 0) + 1
                _scen_ht[_sc] = _scen_ht.get(_sc, 0.0) + float(r.sale.amount_ht)
            _scen_data = sorted(_scen_counts.items(), key=lambda x: -x[1])
            fig_scen = go.Figure()
            fig_scen.add_trace(go.Bar(
                name="Nb transactions",
                x=[s for s, _ in _scen_data],
                y=[n for _, n in _scen_data],
                marker_color="#1f77b4",
                text=[str(n) for _, n in _scen_data],
                textposition="auto",
            ))
            fig_scen.update_layout(height=360, margin=dict(t=20, b=60),
                xaxis_tickangle=-30, yaxis_title="Nb transactions")
            st.plotly_chart(fig_scen, use_container_width=True)
            st.caption(" · ".join(
                f"**{s}** : {n} tx · {_scen_ht.get(s, 0):,.0f} € HT"
                for s, n in _scen_data
            ))
    elif _monthly:
        st.caption("_(données sur 1 seul mois — graphique temporel non pertinent)_")
