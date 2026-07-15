"""Onglet "VIES" (extrait tel quel de app.py, with tab_vies:).

KPIs de validation VIES, classification manuelle des numéros non
vérifiés (st.fragment), overrides manuels persistés, reclassifications
B2B→B2C et export CSV du rapport d'audit VIES.
"""

from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from tva_intracom.i18n import _

from tva_intracom.ui.formatting import _country_label, _gated_preview_table, _smart_money_df, _render_filter_bar, _fmt
from tva_intracom.ui.tabs.context import TabContext


def render_vies(ctx: TabContext) -> None:
    """Rendu complet de l'onglet VIES."""
    _can_export = ctx.can_export
    _gated_download = ctx.gated_download
    _vies_retry_nonce = ctx.vies_retry_nonce
    _vies_scope_id = ctx.vies_scope_id
    enable_vies = ctx.enable_vies
    nom_entreprise = ctx.nom_entreprise
    period_label = ctx.period_label
    vies_summary = ctx.vies_summary

    if not enable_vies:
        st.info(_("vies_tab_enable_info"))
    elif vies_summary is None or vies_summary.total_checked == 0:
        st.info(_("vies_tab_no_b2b_info"))
    else:
        # KPIs VIES
        v1, v2, v3, v4, v5 = st.columns(5)
        v1.metric(_("vies_kpi_verified_nums"), vies_summary.total_checked)
        v2.metric(_("vies_kpi_valid"), vies_summary.total_valid)
        v3.metric(_("vies_kpi_invalid"), vies_summary.total_invalid,
            delta=f"-{vies_summary.total_invalid}" if vies_summary.total_invalid else None, delta_color="inverse")
        v4.metric(_("vies_kpi_unverified"), vies_summary.total_inconclusive,
            delta=f"{vies_summary.total_inconclusive}" if vies_summary.total_inconclusive else None, delta_color="off")
        v5.metric(_("vies_kpi_recovered_vat"), f"{float(vies_summary.fraud_avoided_amount):,.2f} €")

        # Inconclus
        if vies_summary.total_inconclusive > 0:
            st.warning(_("vies_unverified_warning", count=vies_summary.total_inconclusive))
            if vies_summary.total_inconclusive == vies_summary.total_checked:
                st.error(_("vies_unverified_all_error"))
                if st.button(_("vies_test_btn"), key="test_vies_conn"):
                    from tva_intracom.vies_engine import check_vat
                    with st.spinner(_("vies_testing")):
                        test_res = check_vat("FR", "40303265045")
                    if test_res.valid:
                        st.success(_("vies_test_ok", name=test_res.name))
                    else:
                        st.error(_("vies_test_fail", valid=test_res.valid, error=test_res.error))

            @st.fragment
            def render_manual_vies_classification():
                _details = getattr(vies_summary, "inconclusive_vat_details", None)
                if _details:
                    _inc_entries = [{"vat": d["vat"], "country": d.get("country", d["vat"][:2]),
                        "sale_ids": (d.get("display_ids") or d.get("sale_ids", []))} for d in _details]
                else:
                    _inc_entries = [{"vat": v, "country": v[:2], "sale_ids": []}
                        for v in vies_summary.inconclusive_vats]
                _overrides: dict = st.session_state.get("_vies_manual_overrides", {})

                with st.expander(_("vies_manual_class_title"), expanded=True):
                    st.caption(_("vies_manual_class_caption"))
                    _changed = False
                    for _entry in _inc_entries:
                        _vat = _entry["vat"]; _country = _entry["country"]; _sale_ids = _entry["sale_ids"]
                        _label = f"**{_vat}** ({_country})"
                        if _sale_ids:
                            _label += f" — vente(s) : {', '.join(_sale_ids[:3])}"
                            if len(_sale_ids) > 3: _label += f" +{len(_sale_ids)-3}"
                        _current = _overrides.get(_vat, _("vies_manual_class_not_classified"))
                        _col_label, _col_sel, _col_badge = st.columns([3, 2, 1])
                        _col_label.markdown(_label)
                        _choice = _col_sel.selectbox(_("vies_manual_class_status"),
                            options=[_("vies_manual_class_not_classified"), _("manual_valid"), _("manual_invalid")],
                            index=[_("vies_manual_class_not_classified"), _("manual_valid"), _("manual_invalid")].index(_current),
                            key=f"vies_override_{_vat}", label_visibility="collapsed")
                        _col_badge.markdown("🆕" if _choice != _current else "")
                        if _choice != _current:
                            _overrides[_vat] = _choice; _changed = True
                    if _changed:
                        st.session_state["_vies_manual_overrides"] = _overrides
                        st.rerun(scope="fragment")

                    _pending = {v: c for v, c in _overrides.items() if c != _("vies_manual_class_not_classified")}
                    st.caption(_("vies_manual_class_nums_classified", count=len(_pending), total=len(_inc_entries)))
                    _col_apply, _col_reset = st.columns([2, 1])
                    with _col_apply:
                        if _pending and st.button(_("vies_manual_class_apply_btn"), type="primary"):
                            from tva_intracom.vies_engine import set_manual_override as _smo_apply
                            for _vat_key, _choice_val in _pending.items():
                                _smo_apply(_vies_scope_id, _vat_key, valid=(_choice_val == _("manual_valid")))
                            st.session_state.pop("_vies_manual_overrides", None)
                            st.session_state.pop("_calc_key", None)
                            st.success(_("vies_manual_class_success"))
                            st.rerun()
                    with _col_reset:
                        if st.button(_("vies_manual_class_reset_btn")):
                            st.session_state.pop("_vies_manual_overrides", None)
                            st.rerun()

            render_manual_vies_classification()

        if st.button(_("vies_reverify_btn"), key="retry_vies_btn"):
            st.session_state["_vies_retry_nonce"] = _vies_retry_nonce + 1
            st.rerun()

        # Overrides manuels en base (toujours accessible, replié par défaut)
        try:
            from tva_intracom.vies_engine import (
                set_manual_override as _smo_edit,
                delete_manual_override as _dmo_edit,
                get_manual_overrides_full as _gmo_full,
                CACHE_TTL_DAYS as _VIES_TTL_B,
                _is_expired as _vies_is_expired_b,
            )
            _existing_overrides_b = _gmo_full(_vies_scope_id)
        except Exception:
            _existing_overrides_b = []
            _VIES_TTL_B = 90

        if _existing_overrides_b:
            _nb_expired_b = sum(
                1 for _v, _iv, _sa in _existing_overrides_b if _vies_is_expired_b(_sa)
            )
            with st.expander(
                _("vies_manual_class_expander", count=len(_existing_overrides_b))
                + (_("vies_manual_class_exp_expired", count=_nb_expired_b) if _nb_expired_b else ""),
                expanded=bool(_nb_expired_b),
            ):
                st.caption(_("vies_manual_class_exp_caption", ttl=_VIES_TTL_B))
                for _ov_vat2, _ov_valid2, _ov_date2 in _existing_overrides_b:
                    _ov_date_str2 = (_ov_date2 or "")[:10]
                    _ov_expired2 = _vies_is_expired_b(_ov_date2)
                    _oc1b, _oc2b, _oc3b, _oc4b = st.columns([3, 2, 1, 1])
                    _ov_badge2 = _("vies_manual_class_exp_expired_badge") if _ov_expired2 else ""

                    _ov_label2 = f"**{_ov_vat2}**"
                    # On affiche les ventes du fichier actuel concernées par cet override (si présentes)
                    _ov_sales2 = []
                    if vies_summary and hasattr(vies_summary, "vat_to_display_ids"):
                        _ov_sales2 = vies_summary.vat_to_display_ids.get(_ov_vat2, [])
                    if _ov_sales2:
                        _ov_sales_str = ", ".join(_ov_sales2[:3])
                        if len(_ov_sales2) > 3:
                            _ov_sales_str += f" +{len(_ov_sales2)-3}"
                        _ov_label2 += _("vies_manual_class_exp_sales", sales=_ov_sales_str)

                    _oc1b.markdown(
                        f"{_ov_label2}  \n<small style='color:grey'>{_ov_date_str2}{_ov_badge2}</small>",
                        unsafe_allow_html=True)
                    _ov_new2 = _oc2b.selectbox(_("vies_manual_class_status"),
                        options=[_("manual_valid"), _("manual_invalid")],
                        index=0 if _ov_valid2 else 1,
                        key=f"edit_override_b_{_ov_vat2}", label_visibility="collapsed")
                    if _oc3b.button("💾", key=f"save_override_b_{_ov_vat2}", help=_("vies_manual_class_exp_save_help")):
                        _smo_edit(_vies_scope_id, _ov_vat2, valid=(_ov_new2 == _("manual_valid")))
                        st.session_state.pop("_calc_key", None)
                        st.success(f"{_ov_vat2} → {_ov_new2}")
                        st.rerun()
                    if _oc4b.button("🗑️", key=f"del_override_b_{_ov_vat2}", help=_("vies_manual_class_exp_del_help")):
                        try:
                            _dmo_edit(_vies_scope_id, _ov_vat2)
                            st.session_state.pop("_calc_key", None)
                            st.success(_("vies_manual_class_exp_del_success", vat=_ov_vat2))
                            st.rerun()
                        except Exception as _del_err2:
                            st.error(f"Erreur : {_del_err2}")

        # Reclassifications VIES
        if vies_summary.reclassifications:
            avec_delta = [r for r in vies_summary.reclassifications if r.vat_delta > 0]
            dom_rc     = [r for r in vies_summary.reclassifications if getattr(r, "is_domestic_reverse_charge", False)]
            dom_taxe   = [r for r in vies_summary.reclassifications if r.vat_delta <= 0 and not getattr(r, "is_domestic_reverse_charge", False)]
            st.success(_("vies_success_reclassified", count=len(vies_summary.reclassifications), amount=_fmt(float(vies_summary.fraud_avoided_amount))))
            if dom_rc:
                st.info(_("vies_info_reverse_charge", count=len(dom_rc)))
            if dom_taxe:
                st.info(_("vies_info_zero_impact", count=len(dom_taxe)))

            def _vies_statut(r):
                if getattr(r, "is_domestic_reverse_charge", False): return _("vies_status_reverse_charge")
                elif r.vat_delta <= 0: return _("vies_status_already_taxed")
                return _("vies_status_recovered")

            def _vies_explication(r):
                if getattr(r, "is_domestic_reverse_charge", False):
                    return _("vies_expl_reverse_charge", country=r.buyer_country)
                elif r.vat_delta <= 0: return _("vies_expl_already_taxed")
                return _("vies_expl_cross_border")

            fraud_data = [{_("vies_col_id"): (getattr(r, "display_id", "") or r.sale_id), _("vies_col_rejected_vat"): r.buyer_vat_number,
                _("vies_col_origin"): _country_label(getattr(r, "stock_country", "")),
                _("vies_col_dest"): _country_label(r.buyer_country), _("vies_col_ht"): float(r.amount_ht),
                _("vies_col_recovered_vat"): float(r.vat_avoided),
                _("vies_col_status"): _vies_statut(r), _("vies_col_expl"): _vies_explication(r)}
                for r in vies_summary.reclassifications]

            filtre = st.radio(_("vies_filter_label"), [_("vies_filter_all"), _("vies_filter_recovered"), _("vies_filter_reverse_charge"), _("vies_filter_zero_impact")], horizontal=True)
            if filtre == _("vies_filter_recovered"):   display = [d for d in fraud_data if _("vies_status_recovered") in d[_("vies_col_status")]]
            elif filtre == _("vies_filter_reverse_charge"): display = [d for d in fraud_data if _("vies_status_reverse_charge") in d[_("vies_col_status")]]
            elif filtre == _("vies_filter_zero_impact"):      display = [d for d in fraud_data if _("vies_status_already_taxed") in d[_("vies_col_status")]]
            else: display = fraud_data
            
            _fraud_df_full = pd.DataFrame(display)
            _fraud_df_filt = _render_filter_bar(_fraud_df_full, "vies_reclass")
            
            _fraud_cfg = _smart_money_df(_fraud_df_filt,
                money_cols=[_("vies_col_ht"), _("vies_col_recovered_vat")],
                note_cols=[_("vies_col_rejected_vat"), _("vies_col_id")])
            _gated_preview_table(_fraud_df_filt, _can_export, column_config=_fraud_cfg)

            if avec_delta:
                by_c = {}
                for r in avec_delta:
                    _c_lbl = _country_label(r.buyer_country)
                    by_c[_c_lbl] = by_c.get(_c_lbl,0) + float(r.vat_avoided)
                fig_f = go.Figure(go.Bar(x=list(by_c.keys()), y=list(by_c.values()),
                    marker_color="#d62728", text=[f"{v:,.2f}€" for v in by_c.values()], textposition="auto"))
                fig_f.update_layout(title=_("vies_chart_title"), yaxis_title=_("vies_chart_yaxis"), height=280, margin=dict(t=40,b=30))
                st.plotly_chart(fig_f, use_container_width=True)

            import io as _io, csv as _csv
            buf = _io.StringIO(); w = _csv.writer(buf, delimiter=";")
            w.writerow([_("vies_col_id"), _("vies_col_rejected_vat"), _("vies_col_origin"), _("vies_col_dest"), _("vies_col_ht"), _("vies_col_recovered_vat"), _("vies_col_status"), _("vies_col_expl")])
            for r in vies_summary.reclassifications:
                if getattr(r, "is_domestic_reverse_charge", False):
                    statut_csv = _("vies_status_reverse_charge"); expl_csv = _("vies_expl_reverse_charge", country=r.buyer_country)
                elif r.vat_delta <= 0:
                    statut_csv = _("vies_status_already_taxed"); expl_csv = _("vies_expl_already_taxed")
                else:
                    statut_csv = _("vies_status_recovered"); expl_csv = _("vies_expl_cross_border")
                w.writerow([(getattr(r, "display_id", "") or r.sale_id), r.buyer_vat_number, _country_label(getattr(r, "stock_country", "")), _country_label(r.buyer_country),
                    str(r.amount_ht).replace(".",","), str(r.vat_avoided).replace(".",","),
                    statut_csv, expl_csv])
            _gated_download(_("vies_dl_btn"),
                data=("\ufeff"+buf.getvalue()).encode("utf-8"),
                file_name=_( "vies_dl_filename", company=nom_entreprise, period=period_label), mime="text/csv")
        elif vies_summary.total_inconclusive:
            st.info(_("vies_info_no_invalid"))
        else:
            st.success(_("vies_success_all_valid"))
