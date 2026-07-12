"""Onglet "Audit Amazon" (extrait tel quel de app.py, with tab_audit:).

Deux sous-onglets : Écarts TVA Amazon (5 catégories : taux, VIES, UK,
autoliquidation art.194, TVA Amazon manquante) et Mouvements stock FBA.
"""

from __future__ import annotations

from decimal import Decimal

import pandas as pd
import streamlit as st
from tva_intracom.i18n import _

from tva_intracom.ui.formatting import _gated_preview_table, _smart_money_df, _render_filter_bar, _fmt
from tva_intracom.ui.tabs.context import TabContext


def render_audit(ctx: TabContext) -> None:
    """Rendu complet de l'onglet Audit Amazon."""
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
            ecarts_vies_tab, ecarts_b2b_dom_tab, ecarts_gb_tab, ecarts_autres_tab, ecarts_amz_manquante_tab = [], [], [], [], []
            nb_arrondis = 0
            for r in results:
                tva_amazon = float(getattr(r.sale,"amazon_vat_amount",Decimal("0")))
                tva_moteur = float(r.vat_amount)
                if tva_amazon==0 and tva_moteur==0: continue
                ecart = tva_amazon - tva_moteur
                row_d = {"ID":(r.sale.display_id or r.sale.sale_id),
                    "Stock→Dest":f"{r.sale.stock_country}→{r.sale.buyer_country}",
                    "Dest": r.sale.buyer_country,
                    "Scénario":r.scenario.value,"HT (EUR)":float(r.sale.amount_ht),
                    "TVA Amazon (EUR)":round(tva_amazon,2),"TVA moteur (EUR)":round(tva_moteur,2),
                    "Écart (EUR)":round(ecart,2),
                    "Taux Amazon (%)":round(tva_amazon/float(r.sale.amount_ht)*100,2) if r.sale.amount_ht else 0,
                    "Taux moteur (%)":float(r.vat_rate),
                    "Canal": r.channel.value}
                if abs(ecart)<=0.05:
                    if abs(ecart)>0: nb_arrondis+=1
                    continue
                _dep = r.sale.stock_country; _arr = r.sale.buyer_country; _sid = str(r.sale.sale_id)
                _is_b2b = (r.sale.buyer_type == _BT_APP.B2B)
                if _dep == "GB" or _arr == "GB": ecarts_gb_tab.append(row_d)
                elif _sid in _vies_rc_ids_app or id(r.sale) in _vies_affected_ids: ecarts_vies_tab.append(row_d)
                elif _sid in _dom_rc_ids_app or (_is_b2b and _arr in _DRC_APP and tva_moteur == 0 and tva_amazon > 0): ecarts_b2b_dom_tab.append(row_d)
                elif tva_amazon == 0 and tva_moteur > 0: ecarts_amz_manquante_tab.append(row_d)
                else: ecarts_autres_tab.append(row_d)

            # Amélioration 4 : helper formatage uniforme pour tous les sous-onglets audit
            def _audit_df(rows, key_suffix: str):
                """Affiche un tableau d'écarts avec formatage smart monétaire et taux."""
                if not rows:
                    return
                _df_full = pd.DataFrame(rows)
                _df_filt = _render_filter_bar(_df_full, key_suffix)
                _cfg = _smart_money_df(_df_filt,
                    money_cols=[_("col_ht_eur"), _("col_tva_amz_eur"), _("col_tva_moteur_eur"), _("col_gap_eur")],
                    pct_cols=[_("col_rate_amz_pct"), _("col_rate_moteur_pct")])
                _gated_preview_table(_df_filt, _can_export, column_config=_cfg)

            sub1, sub2, sub3, sub4, sub5 = st.tabs([
                _("audit_tab_rate_gaps", count=len(ecarts_autres_tab)),
                _("audit_tab_vies_risk", count=len(ecarts_vies_tab)),
                _("audit_tab_uk", count=len(ecarts_gb_tab)),
                _("audit_tab_art194", count=len(ecarts_b2b_dom_tab)),
                _("audit_tab_missing_amz", count=len(ecarts_amz_manquante_tab)),
            ])
            with sub1:
                if ecarts_autres_tab:
                    total = sum(r[_("col_gap_eur")] for r in ecarts_autres_tab)
                    st.error(_("audit_taux_error", count=len(ecarts_autres_tab), total=_fmt(total)))
                    _audit_df(ecarts_autres_tab, "audit_taux")
                else:
                    st.success(_("audit_taux_success"))
            with sub2:
                if not enable_vies: st.info(_("audit_vies_info"))
                elif ecarts_vies_tab:
                    total = sum(r[_("col_gap_eur")] for r in ecarts_vies_tab)
                    st.error(_("audit_vies_error", amount=_fmt(abs(total))))
                    _audit_df(ecarts_vies_tab, "audit_vies")
                else:
                    st.success(_("audit_vies_success"))
            with sub3:
                st.info(_("audit_uk_info"))
                if ecarts_gb_tab:
                    st.metric(_("audit_uk_metric"), _fmt(sum(r[_('col_gap_eur')] for r in ecarts_gb_tab)))
                    _audit_df(ecarts_gb_tab, "audit_gb")
                else:
                    st.success(_("audit_uk_success"))
            with sub4:
                st.info(_("audit_art194_info"))
                if ecarts_b2b_dom_tab:
                    total = sum(r[_("col_gap_eur")] for r in ecarts_b2b_dom_tab)
                    st.metric(_("audit_art194_metric"), _fmt(abs(total)))
                    _audit_df(ecarts_b2b_dom_tab, "audit_art194")
                else:
                    st.success(_("audit_art194_success"))
            with sub5:
                st.info(_("audit_manquante_info"))
                if ecarts_amz_manquante_tab:
                    total = sum(r[_("col_gap_eur")] for r in ecarts_amz_manquante_tab)
                    st.metric(_("audit_manquante_metric"), _fmt(abs(total)))
                    st.metric(_("audit_manquante_metric"), f"{abs(total):,.2f} EUR")
                    _audit_df(ecarts_amz_manquante_tab, "audit_manquante")
                    import io as _io2, csv as _csv2
                    _buf2 = _io2.StringIO(); _w2 = _csv2.writer(_buf2, delimiter=";")
                    _w2.writerow([_("vies_col_id"),_("col_stock_dest"),_("col_scenario"),_("col_ht_eur"),_("col_tva_amz_eur"),_("col_tva_moteur_eur"),_("col_gap_eur")])
                    for _rw in ecarts_amz_manquante_tab:
                        _w2.writerow([_rw["ID"],_rw[_("col_stock_dest")],_rw[_("col_scenario")],
                            str(_rw[_("col_ht_eur")]).replace(".",","),str(_rw[_("col_tva_amz_eur")]).replace(".",","),
                            str(_rw[_("col_tva_moteur_eur")]).replace(".",","),str(_rw[_("col_gap_eur")]).replace(".",",")])
                    _gated_download(_("audit_dl_manquante_btn"),
                        data=("\ufeff"+_buf2.getvalue()).encode("utf-8"),
                        file_name=_("audit_dl_manquante_filename", company=nom_entreprise, period=period_label), mime="text/csv")
                else:
                    st.success(_("audit_manquante_success"))
            if nb_arrondis > 0:
                st.caption(_("audit_rounding_caption", count=nb_arrondis))

    with audit_sub2:
        st.subheader(_("audit_fba_header"))
        local_sales_outside_fr = [s for s in all_sales if s.stock_country==s.buyer_country and s.stock_country!="FR"]
        if local_sales_outside_fr:
            by_c = {}
            for s in local_sales_outside_fr:
                by_c.setdefault(s.stock_country,{"nb":0,"ht":0.0})
                by_c[s.stock_country]["nb"]+=1; by_c[s.stock_country]["ht"]+=float(s.amount_ht)
            at_risk = [c for c in by_c if c not in countries_with_vat]
            ok = [c for c in by_c if c in countries_with_vat]
            if at_risk: st.error(_("audit_local_sales_error", countries=', '.join(at_risk)))
            if ok: st.success(_("audit_local_sales_success", countries=', '.join(ok)))
            _df_loc = pd.DataFrame([{"ID": c, "Dest":c, _("type_column_label"):c, _("col_sales_count"):d["nb"], _("col_volume_ht_eur"):round(d["ht"],2),
                _("col_status"):_("audit_status_ok") if c in countries_with_vat else _("audit_status_required")}
                for c,d in by_c.items()])
            _df_loc_filt = _render_filter_bar(_df_loc, "stock_loc")
            _loc_cfg = _smart_money_df(_df_loc_filt, money_cols=[_("col_volume_ht_eur")])
            _gated_preview_table(_df_loc_filt, _can_export, column_config=_loc_cfg)
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
                _gated_preview_table(_df_fc_filt, _can_export)
        else:
            st.info(_("audit_fba_none"))
