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

from tva_intracom.i18n import _, country_label
from tva_intracom.mem_utils import heavy_cache_data
from tva_intracom.rates import COUNTRY_ISO3
from tva_intracom.ui.formatting import _get_conversion_rate
from tva_intracom.ui.tabs.context import TabContext


@heavy_cache_data(show_spinner=False, ttl=1800, max_entries=20)
def _aggregate_viz_raw(_results: list, _refund_results: list, calc_key) -> dict:
    """Agrégats bruts (EUR, clés fixes non traduites) pour l'évolution
    mensuelle et la répartition par scénario de l'onglet Visualisations.

    Mis en cache par `calc_key` (même clé que le calcul moteur, voir
    context.py / app.py) : le cache n'est invalidé QUE quand les résultats
    sous-jacents changent réellement, pas à chaque rerun Streamlit déclenché
    par un widget local (sélecteur de devise, changement de langue, autre
    onglet, etc.). Les arguments `_results`/`_refund_results` sont préfixés
    d'un underscore pour indiquer à st.cache_data de ne PAS tenter de les
    hacher (potentiellement coûteux/impossible sur des objets métier) ;
    seule `calc_key` sert de clé de cache.

    IMPORTANT (mémoire) : `st.cache_data` est un cache GLOBAL au process,
    partagé par toutes les sessions Streamlit -- il n'est PAS vidé par le
    nettoyage de `st.session_state` (ni au logout, ni au retrait d'un
    fichier). Sans borne, chaque nouveau `calc_key` (= chaque nouveau jeu
    de données testé, par n'importe quel utilisateur) créait une entrée
    permanente, jamais évincée avant un redémarrage du process : c'était
    la vraie cause de la RAM qui ne redescendait jamais sur Railway,
    malgré le nettoyage de session_state. `ttl=1800` (30 min) +
    `max_entries=20` (éviction LRU) bornent la taille du cache.

    Important : ne jamais mettre en cache ici de libellés traduits (_())
    ni de montants déjà convertis en devise d'affichage (* _rate) -- ces
    deux éléments peuvent changer sans que `calc_key` change, ce qui
    rendrait le résultat caché obsolète de façon silencieuse. La ventilation
    TVA/pays (viz_data_by_country) n'a pas besoin de ce cache : elle vient
    de `summary.net_oss_by_country`/`net_local_by_country`, déjà des
    agrégats précalculés par le moteur, pas de `results` brut.
    """
    sales_rows = []
    scen_counts: dict[str, int] = {}
    scen_ht: dict[str, float] = {}
    # Un seul passage sur `results` : construit à la fois les lignes pour
    # l'agrégation mensuelle des ventes ET les compteurs par scénario
    # (auparavant deux boucles séparées).
    for r in _results:
        scen = r.scenario.value
        ht = float(r.sale.amount_ht)
        scen_counts[scen] = scen_counts.get(scen, 0) + 1
        scen_ht[scen] = scen_ht.get(scen, 0.0) + ht
        d = r.sale.transaction_date
        if d and len(d) >= 7 and ht > 0:
            sales_rows.append((d[:7], ht, float(r.vat_amount)))

    refund_rows = [
        (r.sale.transaction_date[:7], float(r.sale.amount_ht), float(r.vat_amount))
        for r in _refund_results
        if r.sale.transaction_date and len(r.sale.transaction_date) >= 7
    ]

    sales_df = (pd.DataFrame(sales_rows, columns=["ym", "ht", "vat"])
                  .groupby("ym")[["ht", "vat"]].sum()) if sales_rows else pd.DataFrame(columns=["ht", "vat"])
    refunds_df = (pd.DataFrame(refund_rows, columns=["ym", "ht", "vat"])
                    .groupby("ym")[["ht", "vat"]].sum()) if refund_rows else pd.DataFrame(columns=["ht", "vat"])

    all_months = sorted(set(sales_df.index) | set(refunds_df.index))
    monthly_df = pd.DataFrame(index=all_months)
    monthly_df["CA HT"] = sales_df["ht"].reindex(all_months).fillna(0.0)
    monthly_df["TVA due"] = sales_df["vat"].reindex(all_months).fillna(0.0)
    monthly_df["Remb. HT"] = refunds_df["ht"].reindex(all_months).fillna(0.0)
    monthly_df["TVA remb."] = refunds_df["vat"].reindex(all_months).fillna(0.0)

    scen_data = sorted(scen_counts.items(), key=lambda x: -x[1])

    return {
        "monthly_df": monthly_df,
        "months": all_months,
        "scen_data": scen_data,
        "scen_ht": scen_ht,
    }


# ─────────────────────────────────────────────────────────────────────────
# Construction des figures Plotly — mises en cache
#
# `_aggregate_viz_raw` (ci-dessus) mettait déjà en cache l'AGRÉGATION des
# données, mais la construction des objets `go.Figure` elle-même (traces,
# hovertemplate, mise en forme) était refaite à CHAQUE rerun Streamlit,
# y compris ceux déclenchés par une interaction sur un tout autre onglet
# (Détail ventes, Audit, etc.) : `st.tabs()` réexécute le corps de tous
# les onglets à chaque passage, qu'ils soient visibles ou non.
#
# Les 4 fonctions ci-dessous mettent donc en cache les figures elles-mêmes,
# avec les mêmes garde-fous que `_aggregate_viz_raw` (ttl=1800, max_entries
# bornés pour ne pas faire grossir indéfiniment le cache process-global).
# Comme ces figures dépendent aussi de la devise affichée et de la langue
# (libellés traduits, taux de conversion) -- deux éléments qui peuvent
# changer sans que `calc_key` change -- ces deux paramètres sont explicite-
# ment inclus dans la clé de cache de chaque fonction.
# ─────────────────────────────────────────────────────────────────────────

@heavy_cache_data(show_spinner=False, ttl=1800, max_entries=20)
def _build_fig_bar(
    viz_data_by_country: dict, vat_net_by_country: dict,
    rate: float, currency_symbol: str, lang: str, calc_key=None,
) -> "go.Figure":
    """Construit la figure barres empilées (TVA par pays)."""
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
        # PERF (2026-09-03) : arrondi à 2 décimales avant sérialisation JSON
        # vers le navigateur (Streamlit sérialise les figures Plotly en
        # JSON) -- sans ça, la conversion de devise (`* rate`) produit des
        # flottants à 15+ décimales inutiles pour un montant en euros/devise.
        vals = [round(viz_data_by_country[c].get(t, 0) * rate, 2) for c in sorted_countries]
        # On prépare les totaux par pays pour les afficher dans la bulle d'aide (tooltip)
        totals = [round(vat_net_by_country[c] * rate, 2) for c in sorted_countries]

        if any(v != 0 for v in vals):
            fig_bar.add_trace(go.Bar(
                name=t,
                x=[country_label(c) for c in sorted_countries],
                y=vals,
                customdata=totals,
                # Une ligne pour le total pays, une ligne pour le canal
                # spécifique (CA3, OSS, etc.)
                hovertemplate=(
                        _("viz_tooltip_pays") +
                        _("viz_tooltip_total_pays") +
                        _("viz_tooltip_canal") +
                        "<extra></extra>"
                ).replace("€", currency_symbol),
                marker_color=colors.get(t),
                text=[f"{v:,.2f}{currency_symbol}" if v != 0 else "" for v in vals],
                textposition="auto"
            ))

    fig_bar.update_layout(
        barmode='relative',  # 'relative' permet d'empiler correctement les négatifs si besoin
        yaxis_title=_("viz_yaxis_vat_title"),
        height=450,
        margin=dict(t=40, b=40),
        # On place la légende en haut pour éviter la superposition avec la barre d'outils (modebar)
        legend=dict(orientation="h", yanchor="bottom", y=1.05, xanchor="center", x=0.5)
    )
    return fig_bar


@heavy_cache_data(show_spinner=False, ttl=1800, max_entries=20)
def _build_fig_pie(
    total_you_owe: float, amazon_vat: float, import_vat: float,
    rate: float, currency_symbol: str, platform_name: str, lang: str, calc_key=None,
) -> "go.Figure | None":
    """Construit le camembert Vous/Plateforme/Douane."""
    pie_l, pie_v, pie_c = [], [], []
    if total_you_owe > 0:
        pie_l.append(_("viz_you")); pie_v.append(round(total_you_owe * rate, 2)); pie_c.append("#2ca02c")
    if amazon_vat > 0:
        pie_l.append(platform_name); pie_v.append(round(amazon_vat * rate, 2)); pie_c.append("#ff7f0e")
    if import_vat > 0:
        pie_l.append(_("viz_customs")); pie_v.append(round(import_vat * rate, 2)); pie_c.append("#9467bd")
    if not pie_v:
        return None

    fig_pie = go.Figure(go.Pie(labels=pie_l, values=pie_v,
                               marker=dict(colors=pie_c), hole=0.4, textinfo="label+percent",
                               hovertemplate=f"%{{label}} : %{{value:,.2f}} {currency_symbol} (%{{percent}})<extra></extra>"))
    fig_pie.update_layout(height=400, margin=dict(t=20, b=20),
                          legend=dict(orientation="h", yanchor="bottom", y=-0.1, xanchor="center", x=0.5))
    return fig_pie


@heavy_cache_data(show_spinner=False, ttl=1800, max_entries=20)
def _build_fig_map(vat_net_by_country: dict, rate: float, lang: str, calc_key=None) -> "go.Figure | None":
    """Construit la carte choroplèthe Europe."""
    map_data = [{"iso_alpha": COUNTRY_ISO3[c], "pays": country_label(c), "tva": round(amt * rate, 2)}
                for c, amt in vat_net_by_country.items() if c in COUNTRY_ISO3]
    if not map_data:
        return None

    fig_map = px.choropleth(map_data, locations="iso_alpha", color="tva",
                            hover_name="pays", color_continuous_scale="YlOrRd", scope="europe",
                            labels={"tva": _("viz_map_label_vat")})
    fig_map.update_layout(
        height=400,
        # Marge droite réservée explicitement à la légende (au lieu
        # de compter sur x=1.05 seul) : sur un écran/conteneur
        # étroit, use_container_width redimensionne toute la
        # figure et la légende finissait par chevaucher la carte
        # au lieu de rester à droite.
        margin=dict(t=10, b=10, l=0, r=90),
        coloraxis_colorbar=dict(
            thicknessmode="pixels", thickness=15,
            lenmode="pixels", len=200,
            yanchor="middle", y=0.5,
            xanchor="left", x=1.02,
            # Fond + bordure + couleur de texte fixes (indépendants
            # du thème sombre de l'app) : le fond de la carte
            # (scope="europe") reste blanc, et le texte de la
            # légende héritait sinon d'une couleur claire adaptée
            # au thème sombre — illisible en blanc sur blanc.
            bgcolor="rgba(255,255,255,0.9)",
            bordercolor="#cccccc", borderwidth=1,
            tickfont=dict(color="#1f1f1f"),
            title=dict(font=dict(color="#1f1f1f")),
        )
    )
    return fig_map


@heavy_cache_data(show_spinner=False, ttl=1800, max_entries=20)
def _build_fig_time_scen(
    monthly_records: tuple, scen_data: tuple, scen_ht: dict,
    rate: float, currency_symbol: str, lang: str, calc_key=None,
) -> tuple:
    """Construit la figure d'évolution mensuelle + la figure par scénario.

    `monthly_records` est un tuple de tuples `(mois_label, ca, remb, vat)`
    et `scen_data` un tuple de `(scenario, nb_transactions)` -- déjà
    hashables tels quels par st.cache_data (contrairement au DataFrame
    original), dérivés juste avant l'appel à partir de `_aggregate_viz_raw`.
    """
    _col_ca_sales = _("viz_evolution_ca_sales")
    _col_refunds_ht = _("viz_evolution_refunds_ht")
    _col_vat_net = _("viz_evolution_vat_net")
    _col_month = _("month_column_label")

    _df_monthly = pd.DataFrame([
        {_col_month: m_label, _col_ca_sales: ca, _col_refunds_ht: remb, _col_vat_net: vat}
        for (m_label, ca, remb, vat) in monthly_records
    ])

    fig_time = go.Figure()
    fig_time.add_trace(go.Bar(
        name=_col_ca_sales, x=_df_monthly[_col_month],
        y=_df_monthly[_col_ca_sales], marker_color="#1f77b4",
        hovertemplate="%{x}<br>" + _col_ca_sales + f" : %{{y:,.2f}} {currency_symbol}<extra></extra>",
    ))
    fig_time.add_trace(go.Bar(
        name=_col_refunds_ht, x=_df_monthly[_col_month],
        y=_df_monthly[_col_refunds_ht], marker_color="#d62728",
        hovertemplate="%{x}<br>" + _col_refunds_ht + f" : %{{y:,.2f}} {currency_symbol}<extra></extra>",
    ))
    fig_time.add_trace(go.Scatter(
        name=_col_vat_net, x=_df_monthly[_col_month],
        y=_df_monthly[_col_vat_net], mode="lines+markers",
        line=dict(color="#ff7f0e", width=2), yaxis="y2",
        hovertemplate="%{x}<br>" + _col_vat_net + f" : %{{y:,.2f}} {currency_symbol}<extra></extra>",
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

    fig_scen = go.Figure()
    fig_scen.add_trace(go.Bar(
        name=_("viz_nb_transactions"),
        x=[s for s, _unused in scen_data],
        y=[n for _unused, n in scen_data],
        marker_color="#1f77b4",
        text=[str(n) for _unused, n in scen_data],
        textposition="auto",
    ))
    fig_scen.update_layout(height=360, margin=dict(t=20, b=60),
                           xaxis_tickangle=-30, yaxis_title=_("viz_nb_transactions"))

    return fig_time, fig_scen


@st.fragment
def render_visualisations() -> None:
    """Rendu complet de l'onglet Visualisations.

    Décoré en `@st.fragment`, comme `render_detail_ventes()` / `render_audit()`
    / `render_telechargements()` : un rerun déclenché par une interaction sur
    un AUTRE onglet ne rejoue plus ce fragment, qui reconstruisait sinon 5
    figures Plotly à chaque passage même quand l'utilisateur regardait un
    tout autre onglet (c'était le seul des 6 onglets principaux sans cette
    isolation).

    IMPORTANT (mémoire) : comme pour `render_detail_ventes()`, `ctx` n'est
    PAS reçu en paramètre mais lu depuis `st.session_state["_tab_ctx"]` à
    l'intérieur du corps de la fonction. Streamlit retient, au niveau
    session interne, les arguments du dernier appel d'une fonction
    `@st.fragment` -- si `ctx` (qui porte `results`/`refund_results`,
    potentiellement des milliers d'objets `VatResult`) était passé en
    argument, Streamlit le garderait vivant indéfiniment, MÊME après un
    `st.session_state.clear()` au logout (fuite mémoire documentée ailleurs
    dans ce projet, voir docstring de `render_detail_ventes`).
    """
    ctx: TabContext = st.session_state["_tab_ctx"]
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
    _lang = st.session_state.get("language", "fr")

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
        # Figure construite (et mise en cache) par calc_key + devise + langue :
        # voir _build_fig_bar plus haut dans ce fichier pour le rationnel.
        fig_bar = _build_fig_bar(
            viz_data_by_country, vat_net_by_country, _rate, _currency_symbol, _lang, ctx.calc_key,
        )
        st.plotly_chart(fig_bar, width="stretch")

    st.divider()

    ch1, ch2 = st.columns(2)
    with ch1:
        st.subheader(_("viz_repartition_you_market_subheader", platform=platform_name))
        fig_pie = _build_fig_pie(
            float(summary.total_you_owe), float(summary.amazon_vat), float(summary.import_vat),
            _rate, _currency_symbol, platform_name, _lang, ctx.calc_key,
        )
        if fig_pie is not None:
            st.plotly_chart(fig_pie, width="stretch")

    with ch2:
        st.subheader(_("viz_map_subheader"))
        if not _can_export:
            st.info(_("viz_locked_map_info"))
        elif vat_net_by_country:
            fig_map = _build_fig_map(vat_net_by_country, _rate, _lang, ctx.calc_key)
            if fig_map is not None:
                st.plotly_chart(fig_map, width="stretch")

    # ── B : Évolution temporelle ──────────────────────────────────────
    st.subheader(_("viz_evolution_subheader"))

    # Agrégation mensuelle + scénarios : un seul passage sur `results`,
    # mis en cache par ctx.calc_key (voir _aggregate_viz_raw) pour ne pas
    # tout recalculer à chaque rerun Streamlit (changement de devise,
    # interaction sur un autre widget, etc.) quand les résultats sous-
    # jacents n'ont pas changé.
    _agg = _aggregate_viz_raw(results, refund_results or [], ctx.calc_key)
    _monthly_df = _agg["monthly_df"]
    _all_months = _agg["months"]

    if len(_monthly_df) >= 2:
        _months_sorted = _all_months
        _MOIS_MAP = {
            "01": _("jan"), "02": _("feb"), "03": _("mar"), "04": _("apr"),
            "05": _("may"), "06": _("jun"), "07": _("jul"), "08": _("aug"),
            "09": _("sep"), "10": _("oct"), "11": _("nov"), "12": _("dec")
        }
        def _mois_label(ym: str) -> str:
            y, m = ym.split("-")
            return f"{_MOIS_MAP.get(m, m)} {y}"

        # Records hashables (tuple de tuples) dérivés de _monthly_df, pour
        # pouvoir passer par le cache de _build_fig_time_scen (un DataFrame
        # n'est pas hashable tel quel par st.cache_data).
        _monthly_records = tuple(
            (
                _mois_label(m),
                round(float(_monthly_df.at[m, "CA HT"]) * _rate, 2),
                round(float(_monthly_df.at[m, "Remb. HT"]) * _rate, 2),
                round((float(_monthly_df.at[m, "TVA due"]) + float(_monthly_df.at[m, "TVA remb."])) * _rate, 2),
            )
            for m in _months_sorted
        )
        _scen_data = tuple(_agg["scen_data"])
        _scen_ht = _agg["scen_ht"]

        _tviz1, _tviz2 = st.columns(2)

        fig_time, fig_scen = _build_fig_time_scen(
            _monthly_records, _scen_data, _scen_ht, _rate, _currency_symbol, _lang, ctx.calc_key,
        )

        with _tviz1:
            st.plotly_chart(fig_time, width="stretch")
        with _tviz2:
            # ── F : Répartition par scénario ─────────────────────────
            st.markdown(_("viz_scenario_markdown"))
            st.plotly_chart(fig_scen, width="stretch")
            st.caption(" · ".join(
                _("viz_scen_caption", scen=s, n=n, ht=f"{_scen_ht.get(s, 0) * _rate:,.0f}", currency=_currency_symbol)
                for s, n in _scen_data
            ))
    elif len(_monthly_df):
        st.caption(_("viz_single_month_caption"))
