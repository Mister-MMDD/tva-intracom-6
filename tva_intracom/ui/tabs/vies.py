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

from tva_intracom.ui.formatting import _country_label, _gated_preview_table, _smart_money_df, _render_filter_bar
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
        st.info("ℹ️ Activez la validation VIES dans la barre latérale pour accéder à cet onglet.")
    elif vies_summary is None or vies_summary.total_checked == 0:
        st.info("ℹ️ Aucun numéro B2B détecté dans ce fichier.")
    else:
        # KPIs VIES
        v1, v2, v3, v4, v5 = st.columns(5)
        v1.metric("Numéros vérifiés", vies_summary.total_checked)
        v2.metric("✅ Valides", vies_summary.total_valid)
        v3.metric("❌ Invalides", vies_summary.total_invalid,
            delta=f"-{vies_summary.total_invalid}" if vies_summary.total_invalid else None, delta_color="inverse")
        v4.metric("⚠️ Non vérifiés (erreur serveur)", vies_summary.total_inconclusive,
            delta=f"{vies_summary.total_inconclusive}" if vies_summary.total_inconclusive else None, delta_color="off")
        v5.metric("TVA récupérée via VIES", f"{float(vies_summary.fraud_avoided_amount):,.2f} €")

        # Inconclus
        if vies_summary.total_inconclusive > 0:
            st.warning(f"⚠️ **{vies_summary.total_inconclusive} numéro(s)** non vérifiés (serveur VIES indisponible).")
            if vies_summary.total_inconclusive == vies_summary.total_checked:
                st.error("🚫 **100% des numéros sont non vérifiés.** Problème de connectivité probable vers ec.europa.eu.")
                if st.button("🧪 Tester la connexion à VIES", key="test_vies_conn"):
                    from tva_intracom.vies import check_vat
                    with st.spinner("Test en cours..."):
                        test_res = check_vat("FR", "40303265045")
                    if test_res.valid:
                        st.success(f"✅ Connexion VIES OK : {test_res.name}")
                    else:
                        st.error(f"❌ Échec : valid={test_res.valid}, error={test_res.error!r}")

        # Inconclus
        if vies_summary.total_inconclusive > 0:
            st.warning(f"⚠️ **{vies_summary.total_inconclusive} numéro(s)** non vérifiés (serveur VIES indisponible).")
            if vies_summary.total_inconclusive == vies_summary.total_checked:
                st.error("🚫 **100% des numéros sont non vérifiés.** Problème de connectivité probable vers ec.europa.eu.")
                if st.button("🧪 Tester la connexion à VIES", key="test_vies_conn"):
                    from tva_intracom.vies import check_vat
                    with st.spinner("Test en cours..."):
                        test_res = check_vat("FR", "40303265045")
                    if test_res.valid:
                        st.success(f"✅ Connexion VIES OK : {test_res.name}")
                    else:
                        st.error(f"❌ Échec : valid={test_res.valid}, error={test_res.error!r}")

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

                with st.expander("🔎 Classifier manuellement les numéros non vérifiés", expanded=True):
                    st.caption("Indiquez Valide (B2B exonéré) ou Invalide (B2C, TVA due) pour chaque numéro non vérifié.")
                    _changed = False
                    for _entry in _inc_entries:
                        _vat = _entry["vat"]; _country = _entry["country"]; _sale_ids = _entry["sale_ids"]
                        _label = f"**{_vat}** ({_country})"
                        if _sale_ids:
                            _label += f" — vente(s) : {', '.join(_sale_ids[:3])}"
                            if len(_sale_ids) > 3: _label += f" +{len(_sale_ids)-3}"
                        _current = _overrides.get(_vat, "⏳ Non classifié")
                        _col_label, _col_sel, _col_badge = st.columns([3, 2, 1])
                        _col_label.markdown(_label)
                        _choice = _col_sel.selectbox("Statut",
                            options=["⏳ Non classifié", "✅ Valide (B2B)", "❌ Invalide (B2C)"],
                            index=["⏳ Non classifié", "✅ Valide (B2B)", "❌ Invalide (B2C)"].index(_current),
                            key=f"vies_override_{_vat}", label_visibility="collapsed")
                        _col_badge.markdown("🆕" if _choice != _current else "")
                        if _choice != _current:
                            _overrides[_vat] = _choice; _changed = True
                    if _changed:
                        st.session_state["_vies_manual_overrides"] = _overrides
                        st.rerun(scope="fragment")

                    _pending = {v: c for v, c in _overrides.items() if c != "⏳ Non classifié"}
                    st.caption(f"**{len(_pending)} / {len(_inc_entries)}** numéros classifiés manuellement.")
                    _col_apply, _col_reset = st.columns([2, 1])
                    with _col_apply:
                        if _pending and st.button("💾 Appliquer les classifications et recalculer", type="primary"):
                            from tva_intracom.vies import set_manual_override as _smo_apply
                            for _vat_key, _choice_val in _pending.items():
                                _smo_apply(_vies_scope_id, _vat_key, valid=(_choice_val == "✅ Valide (B2B)"))
                            st.session_state.pop("_vies_manual_overrides", None)
                            st.session_state.pop("_calc_key", None)
                            st.success("Classification appliquée — recalcul en cours…")
                            st.rerun()
                    with _col_reset:
                        if st.button("↩️ Réinitialiser"):
                            st.session_state.pop("_vies_manual_overrides", None)
                            st.rerun()

            render_manual_vies_classification()

        if st.button("🔄 Revérifier les numéros VIES en erreur", key="retry_vies_btn"):
            st.session_state["_vies_retry_nonce"] = _vies_retry_nonce + 1
            st.rerun()

        # Overrides manuels en base (toujours accessible, replié par défaut)
        try:
            from tva_intracom.vies import (
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
                f"🖊️ Classifications manuelles enregistrées ({len(_existing_overrides_b)})"
                + (f" — ⚠️ {_nb_expired_b} expirée(s)" if _nb_expired_b else ""),
                expanded=bool(_nb_expired_b),
            ):
                st.caption(
                    "Ces classifications persistent entre les sessions. Modifiez ou "
                    "supprimez chaque entrée pour corriger une erreur. Un override posé "
                    f"il y a plus de {_VIES_TTL_B} jours (même délai que le cache VIES) "
                    "n'est plus appliqué automatiquement au calcul — le moteur revient à "
                    "une validation VIES normale. Revalidez-le (bouton 💾) pour le "
                    "réactiver."
                )
                for _ov_vat2, _ov_valid2, _ov_date2 in _existing_overrides_b:
                    _ov_date_str2 = (_ov_date2 or "")[:10]
                    _ov_expired2 = _vies_is_expired_b(_ov_date2)
                    _oc1b, _oc2b, _oc3b, _oc4b = st.columns([3, 2, 1, 1])
                    _ov_badge2 = " · <b style='color:#d97706'>⚠️ expiré, ignoré</b>" if _ov_expired2 else ""

                    _ov_label2 = f"**{_ov_vat2}**"
                    # On affiche les ventes du fichier actuel concernées par cet override (si présentes)
                    _ov_sales2 = []
                    if vies_summary and hasattr(vies_summary, "vat_to_display_ids"):
                        _ov_sales2 = vies_summary.vat_to_display_ids.get(_ov_vat2, [])
                    if _ov_sales2:
                        _ov_label2 += f" — vente(s) : {', '.join(_ov_sales2[:3])}"
                        if len(_ov_sales2) > 3:
                            _ov_label2 += f" +{len(_ov_sales2)-3}"

                    _oc1b.markdown(
                        f"{_ov_label2}  \n<small style='color:grey'>{_ov_date_str2}{_ov_badge2}</small>",
                        unsafe_allow_html=True)
                    _ov_new2 = _oc2b.selectbox("Statut",
                        options=["✅ Valide (B2B)", "❌ Invalide (B2C)"],
                        index=0 if _ov_valid2 else 1,
                        key=f"edit_override_b_{_ov_vat2}", label_visibility="collapsed")
                    if _oc3b.button("💾", key=f"save_override_b_{_ov_vat2}", help="Enregistrer (revalide et réinitialise le délai)"):
                        _smo_edit(_vies_scope_id, _ov_vat2, valid=(_ov_new2 == "✅ Valide (B2B)"))
                        st.session_state.pop("_calc_key", None)
                        st.success(f"{_ov_vat2} → {_ov_new2}")
                        st.rerun()
                    if _oc4b.button("🗑️", key=f"del_override_b_{_ov_vat2}", help="Supprimer (retour VIES)"):
                        try:
                            _dmo_edit(_vies_scope_id, _ov_vat2)
                            st.session_state.pop("_calc_key", None)
                            st.success(f"Override supprimé pour {_ov_vat2}.")
                            st.rerun()
                        except Exception as _del_err2:
                            st.error(f"Erreur : {_del_err2}")

        # Reclassifications VIES
        if vies_summary.reclassifications:
            avec_delta = [r for r in vies_summary.reclassifications if r.vat_delta > 0]
            dom_rc     = [r for r in vies_summary.reclassifications if getattr(r, "is_domestic_reverse_charge", False)]
            dom_taxe   = [r for r in vies_summary.reclassifications if r.vat_delta <= 0 and not getattr(r, "is_domestic_reverse_charge", False)]
            st.success(f"🛡️ **{len(vies_summary.reclassifications)} vente(s) reclassifiée(s)** B2B→B2C. "
                       f"TVA supplémentaire : **{float(vies_summary.fraud_avoided_amount):,.2f} €**")
            if dom_rc:
                st.info(f"ℹ️ **{len(dom_rc)} vente(s) en autoliquidation nationale** (art.194) — acheteur reverse la TVA directement.")
            if dom_taxe:
                st.info(f"ℹ️ **{len(dom_taxe)} vente(s) à impact nul** — TVA déjà due, pas de double imposition.")

            def _vies_statut(r):
                if getattr(r, "is_domestic_reverse_charge", False): return "♻️ Autoliquidation nationale"
                elif r.vat_delta <= 0: return "✅ Déjà taxé (domestic)"
                return "💰 TVA récupérée"

            def _vies_explication(r):
                if getattr(r, "is_domestic_reverse_charge", False):
                    return f"Domestic {r.buyer_country} — Art.194"
                elif r.vat_delta <= 0: return "Domestic — TVA due dans les 2 cas"
                return "Cross-border — exonération évitée"

            fraud_data = [{"ID": (getattr(r, "display_id", "") or r.sale_id), "N° TVA rejeté": r.buyer_vat_number,
                "Dest": _country_label(r.buyer_country), "HT (EUR)": float(r.amount_ht),
                "TVA récupérée (EUR)": float(r.vat_avoided),
                "Statut": _vies_statut(r), "Explication": _vies_explication(r)}
                for r in vies_summary.reclassifications]

            filtre = st.radio("Afficher", ["Toutes","TVA récupérée","Autoliquidation","Impact nul"], horizontal=True)
            if filtre == "TVA récupérée":   display = [d for d in fraud_data if "💰" in d["Statut"]]
            elif filtre == "Autoliquidation": display = [d for d in fraud_data if "♻️" in d["Statut"]]
            elif filtre == "Impact nul":      display = [d for d in fraud_data if "✅" in d["Statut"]]
            else: display = fraud_data
            
            _fraud_df_full = pd.DataFrame(display)
            _fraud_df_filt = _render_filter_bar(_fraud_df_full, "vies_reclass")
            
            _fraud_cfg = _smart_money_df(_fraud_df_filt,
                money_cols=["HT (EUR)", "TVA récupérée (EUR)"])
            _gated_preview_table(_fraud_df_filt, _can_export, column_config=_fraud_cfg)

            if avec_delta:
                by_c = {}
                for r in avec_delta:
                    by_c[_country_label(r.buyer_country)] = by_c.get(_country_label(r.buyer_country),0) + float(r.vat_avoided)
                fig_f = go.Figure(go.Bar(x=list(by_c.keys()), y=list(by_c.values()),
                    marker_color="#d62728", text=[f"{v:,.2f}€" for v in by_c.values()], textposition="auto"))
                fig_f.update_layout(title="TVA récupérée par pays", yaxis_title="Montant (EUR)", height=280, margin=dict(t=40,b=30))
                st.plotly_chart(fig_f, use_container_width=True)

            import io as _io, csv as _csv
            buf = _io.StringIO(); w = _csv.writer(buf, delimiter=";")
            w.writerow(["Vente","N TVA rejete","Pays","HT (EUR)","TVA recuperee (EUR)","Statut","Explication"])
            for r in vies_summary.reclassifications:
                if getattr(r, "is_domestic_reverse_charge", False):
                    statut_csv = "Autoliquidation nationale"; expl_csv = f"Domestic {r.buyer_country} — Art.194"
                elif r.vat_delta <= 0:
                    statut_csv = "Deja taxe (domestic)"; expl_csv = "Domestic"
                else:
                    statut_csv = "TVA recuperee"; expl_csv = "Cross-border"
                w.writerow([(getattr(r, "display_id", "") or r.sale_id), r.buyer_vat_number, _country_label(r.buyer_country),
                    str(r.amount_ht).replace(".",","), str(r.vat_avoided).replace(".",","),
                    statut_csv, expl_csv])
            _gated_download("⬇️ Exporter rapport VIES (.csv)",
                data=("\ufeff"+buf.getvalue()).encode("utf-8"),
                file_name=f"Rapport Audit VIES - {nom_entreprise} - {period_label}.csv", mime="text/csv")
        elif vies_summary.total_inconclusive:
            st.info("ℹ️ Aucun numéro invalide confirmé pour le moment (certains restent à vérifier).")
        else:
            st.success("✅ Tous les numéros de TVA B2B sont valides.")
