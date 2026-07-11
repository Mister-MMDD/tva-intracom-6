"""Onglet "Audit Amazon" (extrait tel quel de app.py, with tab_audit:).

Deux sous-onglets : Écarts TVA Amazon (5 catégories : taux, VIES, UK,
autoliquidation art.194, TVA Amazon manquante) et Mouvements stock FBA.
"""

from __future__ import annotations

from decimal import Decimal

import pandas as pd
import streamlit as st

from tva_intracom.ui.formatting import _gated_preview_table, _smart_money_df, _render_filter_bar
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
        "⚖️ Écarts TVA Amazon",
        "📦 Mouvements stock FBA",
    ])

    with audit_sub1:
        has_amazon_vat = any(getattr(r.sale,"amazon_vat_amount",Decimal("0"))>0 for r in results)
        if not has_amazon_vat:
            st.info("ℹ️ Aucune TVA Amazon disponible. Cet onglet nécessite le format Amazon 3 (2024+).")
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
                    money_cols=["HT (EUR)", "TVA Amazon (EUR)", "TVA moteur (EUR)", "Écart (EUR)"],
                    pct_cols=["Taux Amazon (%)", "Taux moteur (%)"])
                _gated_preview_table(_df_filt, _can_export, column_config=_cfg)

            sub1, sub2, sub3, sub4, sub5 = st.tabs([
                f"⚙️ Écarts de taux ({len(ecarts_autres_tab)})",
                f"🚨 Risque VIES ({len(ecarts_vies_tab)})",
                f"🇬🇧 Royaume-Uni ({len(ecarts_gb_tab)})",
                f"♻️ Autoliquidation art.194 ({len(ecarts_b2b_dom_tab)})",
                f"⚠️ TVA Amazon manquante ({len(ecarts_amz_manquante_tab)})",
            ])
            with sub1:
                if ecarts_autres_tab:
                    total = sum(r["Écart (EUR)"] for r in ecarts_autres_tab)
                    st.error(f"🚨 **{len(ecarts_autres_tab)} écart(s)** — Total : {total:+.2f} EUR. Ouvrez un ticket Amazon Seller Central.")
                    _audit_df(ecarts_autres_tab, "audit_taux")
                else:
                    st.success("✅ Aucun écart de paramétrage de taux.")
            with sub2:
                if not enable_vies: st.info("ℹ️ Activez VIES pour auditer les numéros B2B.")
                elif ecarts_vies_tab:
                    total = sum(r["Écart (EUR)"] for r in ecarts_vies_tab)
                    st.error(f"Risque fiscal (requalification VIES) : {abs(total):,.2f} EUR")
                    _audit_df(ecarts_vies_tab, "audit_vies")
                else:
                    st.success("✅ Aucun risque VIES détecté.")
            with sub3:
                st.info("💡 TVA britannique collectée par Amazon depuis le Brexit. Normal, hors déclarations UE.")
                if ecarts_gb_tab:
                    st.metric("Écart technique UK", f"{sum(r['Écart (EUR)'] for r in ecarts_gb_tab):,.2f} EUR")
                    _audit_df(ecarts_gb_tab, "audit_gb")
                else:
                    st.success("✅ Aucune transaction UK.")
            with sub4:
                st.info("💡 Ventes B2B où l'acheteur autoliquide la TVA (art.194 dir.2006/112/CE — ES, IT, PL, CZ…). Amazon collecte, le moteur calcule 0€. Normal.")
                if ecarts_b2b_dom_tab:
                    total = sum(r["Écart (EUR)"] for r in ecarts_b2b_dom_tab)
                    st.metric("TVA collectée par Amazon (autoliquidation)", f"{abs(total):,.2f} EUR")
                    _audit_df(ecarts_b2b_dom_tab, "audit_art194")
                else:
                    st.success("✅ Aucune vente en autoliquidation avec écart.")
            with sub5:
                st.info("⚠️ Le moteur calcule une TVA due mais Amazon n'a rien collecté (0€). Vérifier le paramétrage Amazon.")
                if ecarts_amz_manquante_tab:
                    total = sum(r["Écart (EUR)"] for r in ecarts_amz_manquante_tab)
                    st.metric("TVA potentiellement manquante", f"{abs(total):,.2f} EUR")
                    _audit_df(ecarts_amz_manquante_tab, "audit_manquante")
                    import io as _io2, csv as _csv2
                    _buf2 = _io2.StringIO(); _w2 = _csv2.writer(_buf2, delimiter=";")
                    _w2.writerow(["ID vente","Stock→Dest","Scenario","HT (EUR)","TVA Amazon (EUR)","TVA moteur (EUR)","Ecart (EUR)"])
                    for _rw in ecarts_amz_manquante_tab:
                        _w2.writerow([_rw["ID"],_rw["Stock→Dest"],_rw["Scénario"],
                            str(_rw["HT (EUR)"]).replace(".",","),str(_rw["TVA Amazon (EUR)"]).replace(".",","),
                            str(_rw["TVA moteur (EUR)"]).replace(".",","),str(_rw["Écart (EUR)"]).replace(".",",")])
                    _gated_download("⬇️ Exporter TVA Amazon manquante (.csv)",
                        data=("\ufeff"+_buf2.getvalue()).encode("utf-8"),
                        file_name=f"Écarts TVA Amazon Manquante - {nom_entreprise} - {period_label}.csv", mime="text/csv")
                else:
                    st.success("✅ Amazon collecte correctement la TVA sur toutes les ventes.")
            if nb_arrondis > 0:
                st.caption(f"ℹ️ {nb_arrondis} micro-écart(s) d'arrondi (<= 0.05 EUR) masqués.")

    with audit_sub2:
        st.subheader("Mouvements de stock inter-entrepôts (FC_Transfer / Inbound)")
        local_sales_outside_fr = [s for s in all_sales if s.stock_country==s.buyer_country and s.stock_country!="FR"]
        if local_sales_outside_fr:
            by_c = {}
            for s in local_sales_outside_fr:
                by_c.setdefault(s.stock_country,{"nb":0,"ht":0.0})
                by_c[s.stock_country]["nb"]+=1; by_c[s.stock_country]["ht"]+=float(s.amount_ht)
            at_risk = [c for c in by_c if c not in countries_with_vat]
            ok = [c for c in by_c if c in countries_with_vat]
            if at_risk: st.error(f"🚨 Ventes locales sans immatriculation : **{', '.join(at_risk)}**")
            if ok: st.success(f"✅ Pays couverts : **{', '.join(ok)}**")
            _df_loc = pd.DataFrame([{"ID": c, "Dest":c, "Pays":c,"Ventes":d["nb"],"Volume HT (EUR)":round(d["ht"],2),
                "Statut":"✅ OK" if c in countries_with_vat else "🚨 Immatriculation requise"}
                for c,d in by_c.items()])
            _df_loc_filt = _render_filter_bar(_df_loc, "stock_loc")
            _loc_cfg = _smart_money_df(_df_loc_filt, money_cols=["Volume HT (EUR)"])
            _gated_preview_table(_df_loc_filt, _can_export, column_config=_loc_cfg)
        if all_fc_transfers:
            st.caption(f"{len(all_fc_transfers)} transfert(s) FC détecté(s).")
            with st.expander("Voir les transferts FBA"):
                _df_fc = pd.DataFrame(all_fc_transfers)
                if "ID" not in _df_fc.columns and "transaction_id" in _df_fc.columns:
                    _df_fc["ID"] = _df_fc["transaction_id"]
                # On adapte pour le filtre
                if "Dest" not in _df_fc.columns and "arrival_country" in _df_fc.columns:
                    _df_fc["Dest"] = _df_fc["arrival_country"]
                _df_fc_filt = _render_filter_bar(_df_fc, "fba_transfers")
                _gated_preview_table(_df_fc_filt, _can_export)
        else:
            st.info("Aucun transfert FC détecté.")
