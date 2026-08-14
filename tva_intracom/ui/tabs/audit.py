"""Onglet "Audit Amazon" (extrait tel quel de app.py, with tab_audit:).

Deux sous-onglets : Écarts TVA Amazon (5 catégories : taux, VIES, UK,
autoliquidation art.194, TVA Amazon manquante) et Mouvements stock FBA.
"""

from __future__ import annotations

from decimal import Decimal

import pandas as pd
import streamlit as st

from tva_intracom.i18n import _
from tva_intracom.mem_utils import heavy_cache_data
from tva_intracom.ui.formatting import _gated_preview_table, _smart_money_df, _render_filter_bar, \
    _fmt
from tva_intracom.ui.tabs.context import TabContext


@heavy_cache_data(show_spinner=False, ttl=1800, max_entries=20)
def _aggregate_fba_local_sales(_all_sales: list, calc_key) -> dict:
    """Agrège les ventes locales hors pays d'origine (stock == destination,
    hors FR) par pays de stock : nombre de ventes + HT total.

    Mis en cache par `calc_key` (même clé que le reste des onglets) :
    `all_sales` peut contenir des dizaines de milliers de lignes, et cette
    liste + boucle tournait auparavant à chaque rerun du fragment Audit,
    même pour une interaction sans rapport avec ce sous-onglet FBA (filtre
    sur le sous-onglet "Écarts TVA", changement de sous-onglet, etc.).

    `countries_with_vat` n'entre PAS dans cette agrégation (ni dans la clé
    de cache) : il ne sert qu'à classer les pays déjà agrégés en "à risque"
    / "conforme" ensuite, à la volée, hors cache -- ce paramètre peut
    changer sans que `calc_key` change.
    """
    local_sales_outside_fr = [s for s in _all_sales if s.stock_country == s.buyer_country and s.stock_country != "FR"]
    by_c: dict[str, dict] = {}
    for s in local_sales_outside_fr:
        _acc = by_c.setdefault(s.stock_country, {"nb": 0, "ht": 0.0})
        _acc["nb"] += 1
        _acc["ht"] += float(s.amount_ht)
    return by_c


@st.fragment
def render_audit() -> None:
    """Rendu complet de l'onglet Audit Amazon.

    Décoré en `@st.fragment` (comme `detail_ventes.py`) : une interaction
    locale à cet onglet (filtre, changement de sous-onglet) ne redéclenche
    plus le rerun de toute l'app.

    IMPORTANT (mémoire) : `ctx` n'est plus reçu en paramètre mais lu depuis
    `st.session_state["_tab_ctx"]` -- voir la docstring de
    `render_detail_ventes` (detail_ventes.py) pour l'explication complète :
    Streamlit retenait sinon `ctx` (et donc `all_sales`/`results`) au niveau
    interne du fragment, indépendamment de `session_state`, ce qui causait
    une fuite mémoire survivant au logout.
    """
    ctx: TabContext = st.session_state["_tab_ctx"]
    results = ctx.results
    _can_export = ctx.can_export
    _gated_download = ctx.gated_download
    all_fc_transfers = ctx.all_fc_transfers
    all_sales = ctx.all_sales
    countries_with_vat = ctx.countries_with_vat
    enable_vies = ctx.enable_vies
    nom_entreprise = ctx.nom_entreprise
    period_label = ctx.period_label
    vies_summary = ctx.vies_summary

    # Devise cible du pays d'origine choisi (home_country) — utilisée pour les
    # libellés de colonnes dynamiques dans les deux sous-onglets ci-dessous.
    _target_currency = st.session_state.get("target_currency", "EUR")

    audit_sub1, audit_sub2 = st.tabs([
        _("subtab_amazon_gaps"),
        _("subtab_fba_inventory"),
    ])

    with audit_sub1:
        has_amazon_vat = any(getattr(r.sale,"amazon_vat_amount",Decimal("0"))>0 for r in results)
        if not has_amazon_vat:
            st.info(_("no_amazon_vat_info"))
        else:
            _vies_affected_ids = getattr(vies_summary, "vies_affected_sale_ids", set()) if vies_summary else set()
            _vies_rc_ids_app: set[str] = set()
            _dom_rc_ids_app:  set[str] = set()
            if vies_summary and hasattr(vies_summary, "reclassifications"):
                for _rc in vies_summary.reclassifications:
                    if getattr(_rc, "is_domestic_reverse_charge", False): _dom_rc_ids_app.add(_rc.sale_id)
                    else: _vies_rc_ids_app.add(_rc.sale_id)
            from tva_intracom.rates import DOMESTIC_REVERSE_CHARGE_COUNTRIES as _DRC_APP
            from tva_intracom.models import BuyerType as _BT_APP

            # Libellés de colonnes dynamiques (devise cible) : voir en-tête de fonction.
            _lbl_ht = _("col_ht_eur", currency=_target_currency)
            _lbl_tva_amz = _("col_tva_amz_eur", currency=_target_currency)
            _lbl_tva_mot = _("col_tva_moteur_eur", currency=_target_currency)
            _lbl_gap = _("col_gap_eur", currency=_target_currency)

            # Mémoïsation sur calc_key (+ devise cible et enable_vies, qui
            # influencent respectivement les libellés de colonnes baked-in
            # dans row_d et la présence des reclassifications VIES) : cette
            # boucle parcourt TOUS les résultats (pas seulement les écarts),
            # coûteux à refaire à chaque interaction sans rapport (filtre,
            # changement de sous-onglet FBA...).
            _lang = st.session_state.get("language", "fr")
            _audit_cache_key = (ctx.calc_key, _target_currency, enable_vies, _lang)
            if ctx.calc_key is not None and st.session_state.get("_audit_cats_cache_key") == _audit_cache_key:
                (ecarts_vies_tab, ecarts_b2b_dom_tab, ecarts_gb_tab,
                 ecarts_autres_tab, ecarts_amz_manquante_tab, nb_arrondis) = st.session_state["_audit_cats_cache_val"]
            else:
                ecarts_vies_tab, ecarts_b2b_dom_tab, ecarts_gb_tab, ecarts_autres_tab, ecarts_amz_manquante_tab = [], [], [], [], []
                nb_arrondis = 0
                for r in results:
                    tva_amazon = float(getattr(r.sale,"amazon_vat_amount",Decimal("0")))
                    tva_moteur = float(r.vat_amount)
                    if tva_amazon==0 and tva_moteur==0: continue
                    ecart = tva_amazon - tva_moteur
                    if abs(ecart)<=0.05:
                        if abs(ecart)>0: nb_arrondis+=1
                        continue
                    # row_d n'est construit qu'à partir d'ici : sur un fichier
                    # sans écart significatif (cas courant sur un rapport
                    # propre), on évite d'allouer/peupler un dict à 11 clés
                    # par ligne pour le jeter immédiatement au `continue`
                    # ci-dessus (ex: 100k lignes sans écart = 100k dicts
                    # inutiles avant ce correctif).
                    row_d = {
                        _("vies_col_id"): (r.sale.display_id or r.sale.sale_id),
                        _("col_stock_dest"): f"{r.sale.stock_country}→{r.sale.buyer_country}",
                        _("col_dest"): r.sale.buyer_country,
                        _("col_scenario"): r.scenario.value,
                        _lbl_ht: float(r.sale.amount_ht),
                        _lbl_tva_amz: round(tva_amazon, 2),
                        _lbl_tva_mot: round(tva_moteur, 2),
                        _lbl_gap: round(ecart, 2),
                        _("col_vat_rate_amazon"): round(tva_amazon / float(r.sale.amount_ht) * 100, 2) if r.sale.amount_ht else 0,
                        _("col_vat_rate_engine"): float(r.vat_rate),
                        _("col_channel"): r.channel.value
                    }
                    _dep = r.sale.stock_country; _arr = r.sale.buyer_country; _sid = str(r.sale.sale_id)
                    _is_b2b = (r.sale.buyer_type == _BT_APP.B2B)
                    if _dep == "GB" or _arr == "GB": ecarts_gb_tab.append(row_d)
                    elif _sid in _vies_rc_ids_app or (_sid, r.sale.amount_ht) in _vies_affected_ids: ecarts_vies_tab.append(row_d)
                    elif _sid in _dom_rc_ids_app or (_is_b2b and _arr in _DRC_APP and tva_moteur == 0 and tva_amazon > 0): ecarts_b2b_dom_tab.append(row_d)
                    elif tva_amazon == 0 and tva_moteur > 0: ecarts_amz_manquante_tab.append(row_d)
                    else: ecarts_autres_tab.append(row_d)
                if ctx.calc_key is not None:
                    st.session_state["_audit_cats_cache_key"] = _audit_cache_key
                    st.session_state["_audit_cats_cache_val"] = (
                        ecarts_vies_tab, ecarts_b2b_dom_tab, ecarts_gb_tab,
                        ecarts_autres_tab, ecarts_amz_manquante_tab, nb_arrondis)

            # Amélioration 4 : helper formatage uniforme pour tous les sous-onglets audit
            def _audit_df(rows, key_suffix: str):
                """Affiche un tableau d'écarts avec formatage smart monétaire, taux, et
                pagination (comme detail_ventes.py) — sans quoi un compte avec
                plusieurs milliers d'écarts renverrait l'intégralité du tableau à
                chaque rerun, filtres compris (recalcul de tri/unique() sur tout)."""
                if not rows:
                    return
                _df_full = pd.DataFrame(rows)
                _df_filt = _render_filter_bar(_df_full, key_suffix)

                _ps = st.select_slider(_("rows_per_page_label"), options=[100, 250, 500, 1000, _("rows_all")],
                                       value=250, key=f"page_size_{key_suffix}")
                _n = len(_df_filt)
                _lim = _n if _ps == _("rows_all") else int(_ps)
                st.caption(_("results_count_caption", count=_n,
                             filtered=(_("results_filtered_tag") if _n < len(_df_full) else ''),
                             visible=min(_lim, _n)))
                _df_page = _df_filt.head(_lim).copy()

                _cfg = _smart_money_df(_df_page,
                                       money_cols=[_lbl_ht, _lbl_tva_amz, _lbl_tva_mot, _lbl_gap],
                                       pct_cols=[_("col_rate_amz_pct"), _("col_rate_moteur_pct")])
                _gated_preview_table(_df_page, _can_export, column_config=_cfg, total_count=_n)

            sub1, sub2, sub3, sub4, sub5 = st.tabs([
                _("audit_tab_rate_gaps", count=len(ecarts_autres_tab)),
                _("audit_tab_vies_risk", count=len(ecarts_vies_tab)),
                _("audit_tab_uk", count=len(ecarts_gb_tab)),
                _("audit_tab_art194", count=len(ecarts_b2b_dom_tab)),
                _("audit_tab_missing_amz", count=len(ecarts_amz_manquante_tab)),
            ])
            with sub1:
                if ecarts_autres_tab:
                    total = sum(r[_lbl_gap] for r in ecarts_autres_tab)
                    st.error(_("audit_taux_error", count=len(ecarts_autres_tab), total=_fmt(total)))
                    _audit_df(ecarts_autres_tab, "audit_taux")
                else:
                    st.success(_("audit_taux_success"))
            with sub2:
                if not enable_vies: st.info(_("audit_vies_info"))
                elif ecarts_vies_tab:
                    total = sum(r[_lbl_gap] for r in ecarts_vies_tab)
                    st.error(_("audit_vies_error", amount=_fmt(abs(total))))
                    _audit_df(ecarts_vies_tab, "audit_vies")
                else:
                    st.success(_("audit_vies_success"))
            with sub3:
                st.info(_("audit_uk_info"))
                if ecarts_gb_tab:
                    st.metric(_("audit_uk_metric"), _fmt(sum(r[_lbl_gap] for r in ecarts_gb_tab)))
                    _audit_df(ecarts_gb_tab, "audit_gb")
                else:
                    st.success(_("audit_uk_success"))
            with sub4:
                st.info(_("audit_art194_info"))
                if ecarts_b2b_dom_tab:
                    total = sum(r[_lbl_gap] for r in ecarts_b2b_dom_tab)
                    st.metric(_("audit_art194_metric"), _fmt(abs(total)))
                    _audit_df(ecarts_b2b_dom_tab, "audit_art194")
                else:
                    st.success(_("audit_art194_success"))
            with sub5:
                st.info(_("audit_manquante_info"))
                if ecarts_amz_manquante_tab:
                    total = sum(r[_lbl_gap] for r in ecarts_amz_manquante_tab)
                    st.metric(_("audit_manquante_metric"), _fmt(abs(total)))
                    _audit_df(ecarts_amz_manquante_tab, "audit_manquante")
                    import io as _io2, csv as _csv2
                    _buf2 = _io2.StringIO(); _w2 = _csv2.writer(_buf2, delimiter=";")
                    _w2.writerow([_("vies_col_id"),_("col_stock_dest"),_("col_scenario"),_lbl_ht,_lbl_tva_amz,_lbl_tva_mot,_lbl_gap])
                    for _rw in ecarts_amz_manquante_tab:
                        _w2.writerow([_rw[_("vies_col_id")],_rw[_("col_stock_dest")],_rw[_("col_scenario")],
                                      str(_rw[_lbl_ht]).replace(".",","),str(_rw[_lbl_tva_amz]).replace(".",","),
                                      str(_rw[_lbl_tva_mot]).replace(".",","),str(_rw[_lbl_gap]).replace(".",",")])
                    _gated_download(_("audit_dl_manquante_btn"),
                                    data=("\ufeff"+_buf2.getvalue()).encode("utf-8"),
                                    file_name=_("audit_dl_manquante_filename", company=nom_entreprise, period=period_label), mime="text/csv")
                else:
                    st.success(_("audit_manquante_success"))
            if nb_arrondis > 0:
                st.caption(_("audit_rounding_caption", count=nb_arrondis))

    with audit_sub2:
        st.subheader(_("audit_fba_header"))
        by_c = _aggregate_fba_local_sales(all_sales, ctx.calc_key)
        if by_c:
            at_risk = [c for c in by_c if c not in countries_with_vat]
            ok = [c for c in by_c if c in countries_with_vat]
            if at_risk: st.error(_("audit_local_sales_error", countries=', '.join(at_risk)))
            if ok: st.success(_("audit_local_sales_success", countries=', '.join(ok)))
            _df_loc = pd.DataFrame([{"ID": c, "Dest":c, _("type_column_label"):c, _("col_sales_count"):d["nb"], _("col_volume_ht_eur", currency=_target_currency):round(d["ht"],2),
                                     _("col_status"):_("audit_status_ok") if c in countries_with_vat else _("audit_status_required")}
                                    for c,d in by_c.items()])
            _df_loc_filt = _render_filter_bar(_df_loc, "stock_loc")
            _loc_cfg = _smart_money_df(_df_loc_filt, money_cols=[_("col_volume_ht_eur", currency=_target_currency)])
            _gated_preview_table(_df_loc_filt, _can_export, column_config=_loc_cfg, total_count=len(_df_loc_filt))
        if all_fc_transfers:
            st.caption(_("audit_fba_count_caption", count=len(all_fc_transfers)))
            with st.expander(_("audit_fba_expander")):
                _df_fc = pd.DataFrame(all_fc_transfers)
                if "ID" not in _df_fc.columns and "transaction_id" in _df_fc.columns:
                    _df_fc["ID"] = _df_fc["transaction_id"]
                # On adapte pour le filtre
                if "Dest" not in _df_fc.columns and "arrival_country" in _df_fc.columns:
                    _df_fc["Dest"] = _df_fc["arrival_country"]
                _df_fc_filt = _render_filter_bar(_df_fc, "fba_transfers")
                _ps_fc = st.select_slider(_("rows_per_page_label"), options=[100, 250, 500, 1000, _("rows_all")],
                                          value=250, key="page_size_fba_transfers")
                _n_fc = len(_df_fc_filt)
                _lim_fc = _n_fc if _ps_fc == _("rows_all") else int(_ps_fc)
                st.caption(_("results_count_caption", count=_n_fc,
                             filtered=(_("results_filtered_tag") if _n_fc < len(_df_fc) else ''),
                             visible=min(_lim_fc, _n_fc)))
                _gated_preview_table(_df_fc_filt.head(_lim_fc).copy(), _can_export, total_count=_n_fc)
        else:
            st.info(_("audit_fba_none"))