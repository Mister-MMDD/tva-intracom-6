"""Onglet "Détail ventes" (extrait tel quel de app.py, with tab_detail:).

Quatre sous-onglets : Ce que vous devez, Géré par des tiers, Ligne par
ligne, Remboursements.
"""

from __future__ import annotations

import pandas as pd
import streamlit as st

from tva_intracom.i18n import _
from tva_intracom.ui.formatting import _fmt, _gated_preview_table, _smart_money_df, \
    _render_filter_bar
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


# ─────────────────────────────────────────────────────────────────────────
# Construction des lignes — mise en cache
#
# Les 4 sous-onglets reconstruisaient chacun un DataFrame en itérant
# LIGNE PAR LIGNE sur `results`/`refund_results` (accès imbriqués
# Sale/VatResult, conversion Decimal->float, appel `_orig_currency_cols`)
# à CHAQUE rerun de l'app entière -- pas seulement lors d'une interaction
# dans cet onglet. `@st.fragment` isole bien les reruns déclenchés par un
# widget interne (tri, pagination, filtres), mais un rerun complet
# déclenché ailleurs (sidebar, autre onglet, changement de langue)
# réexécute ce fragment intégralement comme n'importe quelle fonction du
# script -- sur un fichier de 10-20k lignes, ce passage O(n) était refait
# en pure perte à chaque fois.
#
# `_build_rows_df` isole ce passage O(n) dans une fonction `st.cache_data`
# (mêmes garde-fous que les autres caches du projet : ttl=1800,
# max_entries=20), avec des clés FIXES non traduites (id/stock/dest/...) :
# aucune dépendance à la langue d'affichage, donc pas besoin de `lang`
# dans la clé de cache (contrairement à Visualisations, où les figures
# elles-mêmes portent des libellés traduits). `target_currency` reste
# dans la clé : c'est la seule chose qui détermine si les colonnes
# devise/montant d'origine sont renseignées (voir _orig_currency_cols).
# Le tri et le renommage des colonnes en libellés traduits restent faits
# À CHAQUE rerun, mais ce sont des opérations pandas vectorisées (tri,
# sélection/renommage de colonnes), largement moins coûteuses que la
# boucle Python ligne par ligne qu'elles remplacent.
# ─────────────────────────────────────────────────────────────────────────

@st.cache_data(show_spinner=False, ttl=1800, max_entries=20)
def _build_rows_df(_results: list, target_currency: str, calc_key, label: str) -> pd.DataFrame:
    """Construit le DataFrame brut (une ligne par vente, clés fixes non
    traduites) pour un ensemble de `VatResult` donné (ventes ou
    remboursements). Voir le commentaire ci-dessus pour le rationnel complet.

    `label` (ex: "sales", "refunds") est inclus dans la clé de cache pour
    éviter toute collision entre les deux listes de résultats.

    `vat_country` est gardé dans le DataFrame bien qu'il ne soit affiché
    dans aucun sous-onglet : c'est la clé utilisée par le tri "Pays"
    (distincte de `dest` = pays de destination de la vente).
    """
    rows = []
    for r in _results:
        dev, orig = _orig_currency_cols(r, target_currency)
        rows.append({
            "id": (r.sale.display_id or r.sale.sale_id),
            "stock": r.sale.stock_country,
            "dest": r.sale.buyer_country,
            "vat_country": r.vat_country,
            "ht": float(r.sale.amount_ht),
            "rate_pct": float(r.vat_rate),
            "vat": float(r.vat_amount),
            "canal": r.channel.value,
            "scenario": r.scenario.value,
            "collector": r.collector.value,
            "currency": dev,
            "orig": orig,
            "note": r.note,
        })
    return pd.DataFrame(rows)


def _finalize_df(df_slice: pd.DataFrame, labels: dict, include_collector: bool) -> pd.DataFrame:
    """Sélectionne l'ordre de colonnes final et renomme en libellés traduits.

    Opération pandas vectorisée (sélection + rename), pas de boucle Python
    -- appelée à chaque rerun mais négligeable comparée à la construction
    des lignes elle-même (mise en cache dans `_build_rows_df`).
    """
    cols_order = ["id", "stock", "dest", "ht", "rate_pct", "vat", "canal", "scenario"]
    if include_collector:
        cols_order.append("collector")
    cols_order += ["currency", "orig", "note"]
    return df_slice[cols_order].rename(columns=labels)


@st.fragment
def render_detail_ventes() -> None:
    """Rendu complet de l'onglet Détail ventes.

    Décoré en `@st.fragment` : un changement de widget à l'intérieur de cet
    onglet (curseur "lignes par page", filtres, tri...) ne redéclenche plus
    le rerun de TOUTE l'application (KPIs, gating billing, onglets VIES /
    Audit / Visualisations...) — seul ce fragment se rejoue. Gain net sur
    gros volumes (5-20k lignes) où le reste de l'app (5 graphiques Plotly,
    appels VIES, etc.) n'a aucune raison de se recalculer juste parce qu'on
    veut voir 500 lignes au lieu de 250.

    IMPORTANT (mémoire) : `ctx` n'est PLUS reçu en paramètre. Streamlit
    retient, au niveau de la session interne (indépendamment de
    `st.session_state`), les arguments du dernier appel d'une fonction
    `@st.fragment` -- nécessaire pour pouvoir rejouer CE fragment seul sur
    interaction locale. Si `ctx` (qui porte `results`/`all_sales`, donc
    potentiellement des milliers d'objets `Sale`/`VatResult`) était passé
    en argument, Streamlit le gardait vivant indéfiniment, MÊME après un
    `st.session_state.clear()` au logout -- c'était la cause de la fuite
    mémoire observée (RAM qui ne redescendait jamais). En lisant `ctx`
    depuis `st.session_state["_tab_ctx"]` À L'INTÉRIEUR du corps de la
    fonction, Streamlit ne retient plus, pour ce fragment, qu'un appel
    sans argument lourd : la seule référence vivante à `ctx` est celle de
    `st.session_state`, qui est bien libérée au logout.
    """
    ctx: TabContext = st.session_state["_tab_ctx"]
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
    _orig_cfg = {_lbl_orig: st.column_config.TextColumn(_lbl_orig, width="small")}

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

    _labels = {
        "id": "ID", "stock": _c_stock, "dest": _c_dest, "ht": _lbl_ht,
        "rate_pct": _c_rate_pct, "vat": _lbl_vat, "canal": _c_canal,
        "scenario": _c_scenario, "collector": _c_collector,
        "currency": _c_currency, "orig": _lbl_orig, "note": _c_note,
    }

    sub_a, sub_b, sub_c, sub_d, sub_e = st.tabs([
        _("subtab_what_you_owe"), _("subtab_exemptions"), _("subtab_managed_by_tiers"), _("subtab_row_by_row"),
        _("subtab_refunds", count=len(refund_results or [])),
    ])

    # Construction des DataFrames bruts (mise en cache par calc_key + devise,
    # voir _build_rows_df) : un seul passage O(n) sur `results` et un sur
    # `refund_results`, partagés par les 5 sous-onglets ci-dessous.
    _sales_df_raw = _build_rows_df(results, _target_currency, ctx.calc_key, "sales")
    _refund_df_raw = _build_rows_df(refund_results or [], _target_currency, ctx.calc_key, "refunds")

    _sort_opts = {
        _("sort_country"): "Pays",
        _("sort_rate"): "Taux",
        _("sort_ht"): "HT"
    }

    with sub_a:
        st.caption(_("what_you_owe_caption"))
        _your_raw = _sales_df_raw[(_sales_df_raw["collector"] == "SELLER") & (_sales_df_raw["vat"] > 0)]
        sort_yours_lbl = st.radio(_("sort_by_label"), list(_sort_opts.keys()), horizontal=True, key="sort_yours")
        sort_yours = _sort_opts[sort_yours_lbl]
        if sort_yours == "Pays": _your_raw = _your_raw.sort_values("vat_country")
        elif sort_yours == "Taux": _your_raw = _your_raw.sort_values("rate_pct", ascending=False)
        else: _your_raw = _your_raw.sort_values("ht", ascending=False)
        _your_df_full = _finalize_df(_your_raw, _labels, include_collector=False)

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
        _gated_preview_table(_your_df, _can_export, column_config=_your_cfg, total_count=_n_your)

    with sub_b:
        st.caption(_("subtab_exemptions_caption"))
        # Exonérations : 
        # 1. Vendeur responsable mais TVA à 0 (ex: Export, ou option sous seuil)
        # 2. Acheteur responsable (Reverse Charge) SAUF si c'est un import standard
        _exempt_raw = _sales_df_raw[
            ((_sales_df_raw["collector"] == "SELLER") & (_sales_df_raw["vat"] <= 0)) |
            ((_sales_df_raw["collector"] == "BUYER") & (_sales_df_raw["scenario"] != "IMPORT_STANDARD"))
        ]
        sort_exempt_lbl = st.radio(_("sort_by_label"), list(_sort_opts.keys()), horizontal=True, key="sort_exempt")
        sort_exempt = _sort_opts[sort_exempt_lbl]
        if sort_exempt == "Pays": _exempt_raw = _exempt_raw.sort_values("vat_country")
        elif sort_exempt == "Taux": _exempt_raw = _exempt_raw.sort_values("rate_pct", ascending=False)
        else: _exempt_raw = _exempt_raw.sort_values("ht", ascending=False)
        _exempt_df_full = _finalize_df(_exempt_raw, _labels, include_collector=False)

        # Filtres
        _exempt_df_filt = _render_filter_bar(_exempt_df_full, "exempt")

        # Pagination
        _ps_exempt = st.select_slider(_("rows_per_page_label"), options=[100, 250, 500, 1000, _("rows_all")],
            value=250, key="page_size_exempt")
        _n_exempt = len(_exempt_df_filt)
        _lim_exempt = _n_exempt if _ps_exempt == _("rows_all") else int(_ps_exempt)
        st.caption(_("results_count_caption", count=_n_exempt, filtered=(_("results_filtered_tag") if _n_exempt < len(_exempt_df_full) else ''), visible=min(_lim_exempt, _n_exempt)))

        _exempt_df = _exempt_df_filt.head(_lim_exempt).copy()
        _exempt_cfg = _smart_money_df(_exempt_df,
            money_cols=[_lbl_ht, _lbl_vat],
            pct_cols=[_("col_rate_pct")],
            note_cols=[_("col_note")],
            existing_config=_orig_cfg)
        _gated_preview_table(_exempt_df, _can_export, column_config=_exempt_cfg, total_count=_n_exempt)

    with sub_c:
        st.caption(_("subtab_managed_by_tiers_caption"))
        st.info(_("subtab_managed_by_tiers_note"))
        # Géré par des tiers :
        # 1. Collecté par la plateforme (Amazon Deemed Supplier)
        # 2. Import standard (TVA douane payée par l'acheteur/transporteur)
        _third_raw = _sales_df_raw[
            (_sales_df_raw["collector"] == "AMAZON") |
            ((_sales_df_raw["collector"] == "BUYER") & (_sales_df_raw["scenario"] == "IMPORT_STANDARD"))
        ]
        _third_df_full = _finalize_df(_third_raw, _labels, include_collector=True)

        # Filtres
        _third_df_filt = _render_filter_bar(_third_df_full, "third")

        _third_df = _third_df_filt.copy()
        _third_cfg = _smart_money_df(_third_df,
            money_cols=[_lbl_ht, _lbl_vat],
            pct_cols=[_("col_rate_pct")],
            note_cols=[_("col_note")],
            existing_config=_orig_cfg)
        _gated_preview_table(_third_df, _can_export, column_config=_third_cfg, total_count=len(_third_df_filt))

    with sub_d:
        st.caption(_("subtab_row_by_row_caption"))
        sort_all_lbl = st.radio(_("sort_by_label"), list(_sort_opts.keys()), horizontal=True, key="sort_all")
        sort_all = _sort_opts[sort_all_lbl]
        _all_raw = _sales_df_raw
        if sort_all == "Pays": _all_raw = _all_raw.sort_values("vat_country")
        elif sort_all == "Taux": _all_raw = _all_raw.sort_values("rate_pct", ascending=False)
        else: _all_raw = _all_raw.sort_values("ht", ascending=False)
        _all_df_full = _finalize_df(_all_raw, _labels, include_collector=False)

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
        _gated_preview_table(_all_df_page, _can_export, column_config=_all_cfg, total_count=_n_all)

    with sub_e:
        if not refund_results:
            st.info(_("no_refunds_info"))
        else:
            _ref_ht  = sum(float(r.sale.amount_ht) for r in refund_results)
            _ref_tva = sum(float(r.vat_amount)     for r in refund_results)
            ra, rb, rc = st.columns(3)
            ra.metric(_("kpi_refunds"), len(refund_results))
            rb.metric(_("kpi_ht_refunded"), _fmt(_ref_ht))
            rc.metric(_("kpi_vat_restituted"), _fmt(_ref_tva))
            sort_ref_lbl = st.radio(_("sort_by_label"), list(_sort_opts.keys()), horizontal=True, key="sort_ref")
            sort_ref = _sort_opts[sort_ref_lbl]
            _ref_raw = _refund_df_raw
            if sort_ref == "Pays": _ref_raw = _ref_raw.sort_values("vat_country")
            elif sort_ref == "Taux": _ref_raw = _ref_raw.sort_values("rate_pct", ascending=False)
            else: _ref_raw = _ref_raw.sort_values("ht", ascending=True)
            _ref_df_full = _finalize_df(_ref_raw, _labels, include_collector=False)

            # Filtres
            _ref_df_filt = _render_filter_bar(_ref_df_full, "refund")

            _ref_df = _ref_df_filt.copy()
            _ref_cfg = _smart_money_df(_ref_df,
                money_cols=[_lbl_ht, _lbl_vat],
                pct_cols=[_("col_rate_pct")],
                note_cols=[_("col_note")],
                existing_config=_orig_cfg)
            _gated_preview_table(_ref_df, _can_export, column_config=_ref_cfg, total_count=len(_ref_df_filt))
