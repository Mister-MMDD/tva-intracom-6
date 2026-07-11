"""Onglet "Téléchargements" (extrait tel quel de app.py, with tab_dl:).

Génère et propose tous les exports : rapport Excel complet, XML OSS +
Excel/CSV OSS, rapport CA3 HTML, récapitulatif B2B, déclarations locales
par pays (formats CSV pré-formatés Kennzahl/Casilla/...), export FEC.

Lit `ctx.oss_tva_net_total`, calculé par l'onglet Déclarations — voir
tva_intracom/ui/tabs/context.py pour le détail de cette dépendance
intentionnelle entre onglets.
"""

from __future__ import annotations

import tempfile
from decimal import Decimal

import streamlit as st
from tva_intracom.i18n import _

from tva_intracom.models import Scenario
from tva_intracom.ca3_report import generate_ca3_html_report_v2
from tva_intracom.excel_report import export_xlsx
from tva_intracom.fec_export import generate_fec_bytes
from tva_intracom.oss_export import (
    aggregate_oss_results,
    build_b2b_excel,
    build_oss_excel,
    find_oss_negative_buckets,
)
from tva_intracom.oss_xml import generate_oss_xml, preview_negative_bucket_suggestions
from tva_intracom.rates import COUNTRY_FISCAL_META
from tva_intracom.ui.formatting import _country_label, _fec_period_end_date
from tva_intracom.ui.tabs.context import TabContext


def render_telechargements(ctx: TabContext) -> None:
    """Rendu complet de l'onglet Téléchargements."""
    results = ctx.results
    refund_results = ctx.refund_results
    summary = ctx.summary
    vies_summary = ctx.vies_summary
    period_label = ctx.period_label
    _period_detected_range = ctx.period_detected_range
    _can_export = ctx.can_export
    _gated_download = ctx.gated_download
    _unlock_label_suffix = ctx.unlock_label_suffix
    _vies_scope_id = ctx.vies_scope_id
    nom_entreprise = ctx.nom_entreprise
    siren_entreprise = ctx.siren_entreprise
    tva_fr = ctx.tva_fr
    countries_with_vat = ctx.countries_with_vat
    local_vat_numbers = ctx.local_vat_numbers
    all_fc_transfers = ctx.all_fc_transfers
    all_invoice_credit_notes = ctx.all_invoice_credit_notes
    _oss_tva_net_total = ctx.oss_tva_net_total

    results_net = results + (refund_results or [])

    # period_label, _can_export, _gated_download et _get_payg_checkout_url
    # sont tous calculés/définis plus haut (avant les onglets) — voir bloc
    # « GATING BILLING » — pour être également utilisables dans les autres
    # onglets (Déclarations, VIES, Audit Amazon).
    if _period_detected_range:
        st.info(f"📅 Période auto-détectée : **{period_label}** "
                f"(transactions du {_period_detected_range[0]} au {_period_detected_range[1]}). "
        )

    if not _can_export and period_label:
        st.warning(
            f"🔒 Les exports de la période **{period_label}** ne sont pas encore "
            f"débloqués. Cliquez sur un bouton d'export ci-dessous pour être "
            f"redirigé directement vers le paiement Stripe ({_unlock_label_suffix})."
        )

    st.subheader("📥 Téléchargements")
    with st.container():
        with st.spinner("Génération du fichier Excel (tous onglets)…"):
            with tempfile.NamedTemporaryFile(delete=False, suffix=".xlsx") as xlsx_tmp:
                _vies_ids = getattr(vies_summary, "vies_affected_sale_ids", set()) if vies_summary else set()
                xlsx_path = export_xlsx(results, xlsx_tmp.name, scope_id=_vies_scope_id, summary=summary,
                    refund_results=refund_results, all_fc_transfers=all_fc_transfers,
                    vies_affected_sale_ids=_vies_ids, vies_summary=vies_summary,
                    countries_with_vat=countries_with_vat,
                    period=period_label, seller_country="FR",
                    invoice_credit_notes=all_invoice_credit_notes)
            with open(xlsx_path,"rb") as f: xlsx_bytes = f.read()

        # ── ZONE TÉLÉCHARGEMENTS ──────────────────────────────────────
        st.divider()

        # 1. Rapport principal — pleine largeur, bouton primaire
        st.markdown("#### 📊 Contrôle & Audit")
        _gated_download(
            "📊 Rapport complet (.xlsx)",
            data=xlsx_bytes,
            file_name=f"Rapport TVA intracommunautaire principal - {nom_entreprise} - {period_label}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            type="primary", use_container_width=True,
        )
        st.caption("Contient tous les onglets : calculs détaillés, VIES, transferts FBA et audit.")

        st.divider()

        # 2. Guichet Unique OSS
        st.markdown("#### 🇪🇺 Guichet Unique (OSS)")
        oss_results_dl = [r for r in results_net if r.scenario == Scenario.OSS_B2C]
        if oss_results_dl:
            st.caption("Fichiers pour la déclaration trimestrielle OSS (ventes B2C cross-border).")

            # Ligne XML (Prioritaire)
            # ── Détection en amont des soldes OSS négatifs ──────────
            _oss_agg_preview = aggregate_oss_results(results_net, period=period_label)
            _negative_buckets = find_oss_negative_buckets(_oss_agg_preview)
            _confirm_corrections = False
            if _negative_buckets:
                _suggestions = preview_negative_bucket_suggestions(results_net, period_label)
                _all_resolved = bool(_suggestions) and all(s.fully_resolved for s in _suggestions)
                _any_matched = any(s.matched for s in _suggestions)
                if _any_matched:
                    with st.expander("🔎 Rattachement automatique d'avoirs détecté", expanded=True):
                        for s in _suggestions:
                            _lbl = f"{_country_label(s.bucket.departure)} → {_country_label(s.bucket.arrival)} ({s.bucket.vat_rate}%)"
                            if s.matched:
                                _origins = ", ".join(sorted({m.origin_period for m in s.matched}))
                                st.markdown(f"**{_lbl}** — {len(s.matched)} avoir(s) rattaché(s) (période d'origine : {_origins}).")
                            if s.unmatched_count:
                                st.markdown(f"⚠️ **{_lbl}** — {s.unmatched_count} avoir(s) sans origine (HT {float(s.unmatched_ht):,.2f} €).")
                        _confirm_corrections = st.checkbox("✅ Inclure le bloc de correction automatique dans le XML", key="confirm_oss_corrections")

            try:
                oss_xml_bytes = generate_oss_xml(results=results_net, seller_vat=tva_fr, period=period_label, local_vat_numbers=local_vat_numbers, confirm_corrections=_confirm_corrections)
            except ValueError:
                oss_xml_bytes = generate_oss_xml(results=results_net, seller_vat=tva_fr, period=period_label, local_vat_numbers=local_vat_numbers, confirm_corrections=_confirm_corrections, ignore_negatives=True)

            if oss_xml_bytes:
                _gated_download(_("dl_xml_oss_btn"), data=oss_xml_bytes, file_name=_("dl_xml_oss_filename", company=nom_entreprise, period=period_label), mime="application/xml", use_container_width=True, type="primary")

            # Ligne Excel (Détail)
            with tempfile.NamedTemporaryFile(delete=False, suffix=".xlsx") as oss_tmp:
                oss_xlsx_path = build_oss_excel(results_net, oss_tmp.name, period=period_label)
            with open(oss_xlsx_path,"rb") as f: oss_xlsx_bytes = f.read()
            _gated_download(_("dl_xlsx_oss_btn"), data=oss_xlsx_bytes, file_name=_("dl_xlsx_oss_filename", company=nom_entreprise, period=period_label), mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", use_container_width=True)
        else:
            st.info(_("no_oss_sales_info"))

        st.divider()

        # 3. France CA3
        st.markdown(_("france_ca3_header"))
        st.caption(_("france_ca3_caption"))
        ca3_html = generate_ca3_html_report_v2(
            results=results, refund_results=refund_results, company_name=nom_entreprise, siren=siren_entreprise,
            period_label=period_label, all_fc_transfers=all_fc_transfers, seller_country="FR",
        )
        _gated_download(_("dl_ca3_html_btn"), data=ca3_html.encode("utf-8"), file_name=_("dl_ca3_html_filename", company=nom_entreprise, period=period_label), mime="text/html", use_container_width=True)

        st.divider()

        # 4. Livraisons B2B
        st.markdown(_("b2b_deliveries_header"))
        b2b_results_dl = [r for r in results_net if r.scenario == Scenario.B2B_REVERSE_CHARGE]
        if b2b_results_dl:
            st.caption(_("b2b_deliveries_caption", count=len(b2b_results_dl), ht=f"{float(summary.reverse_charge_ht):,.2f}"))
            with tempfile.NamedTemporaryFile(delete=False, suffix=".xlsx") as b2b_tmp:
                b2b_xlsx_path = build_b2b_excel(results_net, b2b_tmp.name, period=period_label)
            with open(b2b_xlsx_path, "rb") as f: b2b_xlsx_bytes = f.read()
            _gated_download(_("dl_xlsx_b2b_btn"), data=b2b_xlsx_bytes, file_name=_("dl_xlsx_b2b_filename", company=nom_entreprise, period=period_label), mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", use_container_width=True)
        else:
            st.info(_("no_b2b_sales_info"))

        st.divider()

        # 5. Déclarations Locales (hors FR)
        st.markdown(_("local_declarations_header"))
        _local_tax_data = [r for r in results_net if r.channel.value == "LOCAL" and r.vat_country]
        if not _local_tax_data:
            st.info(_("no_local_sales_info"))
        else:
            st.caption(_("local_declarations_caption"))
            _local_countries = sorted({r.vat_country for r in _local_tax_data})
            export_country = st.selectbox("Sélectionnez le pays à exporter", _local_countries, format_func=lambda c: f"{_country_label(c)} ({c})", key="dl_country_select")

            def _build_local_csv(country):
                import io as _il, csv as _cl
                from collections import defaultdict as _dd
                buf = _il.StringIO(); w = _cl.writer(buf, delimiter=";")
                period_lbl = period_label or "Periode non renseignee"
                meta = COUNTRY_FISCAL_META.get(country, (f"Declaration TVA {_country_label(country)}", "Base HT", "TVA", "—", "—"))
                decl_name, lbl_base, lbl_tax, rate_std, rate_red = meta
                country_results = [r for r in results_net if r.vat_country == country and r.channel.value in ("LOCAL", "FR_DOMESTIC")]
                by_rate = _dd(lambda: {"base": Decimal("0"), "tva": Decimal("0"), "nb": 0})
                for r in country_results:
                    by_rate[str(r.vat_rate)]["base"] += r.sale.amount_ht
                    by_rate[str(r.vat_rate)]["tva"]  += r.vat_amount
                    by_rate[str(r.vat_rate)]["nb"]   += 1
                w.writerow([f"{decl_name} — {period_lbl}"])
                w.writerow([f"Pays : {_country_label(country)} ({country}) | Standard : {rate_std} | Reduit : {rate_red}"])
                w.writerow([])
                fmt_map = {
                    "DE": (["Kennzahl","Bezeichnung","Base (EUR)","TVA (EUR)","Nb"], {"19":("81","19%"),"7":("86","7%")}),
                    "ES": (["Casilla","Concepto","Base (EUR)","TVA (EUR)","Nb"], {"21":("01","21%"),"10":("03","10%"),"4":("05","4%")}),
                    "IT": (["Aliquota","Descrizione","Base (EUR)","TVA (EUR)","N."], {"22":"22%","10":"10%","4":"4%"}),
                    "PL": (["Pole","Opis","Base","TVA","Liczba"], {"23":("K_19","23%"),"8":("K_17","8%"),"5":("K_15","5%")}),
                    "NL": (["Rubriek","Omschrijving","Base (EUR)","TVA (EUR)","Antal"], {"21":("1a","21%"),"9":("1b","9%")}),
                    "BE": (["Grille","Description","Base (EUR)","TVA (EUR)","Nb"], {"21":("03","21%"),"12":("02","12%"),"6":("01","6%")}),
                    "PT": (["Campo","Descricao","Base (EUR)","TVA (EUR)","N."], {"23":("1","23%"),"13":("2","13%"),"6":("3","6%")}),
                    "SE": (["Ruta","Beskrivning","Base","TVA","Antal"], {"25":("05","25%"),"12":("06","12%"),"6":("07","6%")}),
                    "AT": (["Kennzahl","Bezeichnung","Base (EUR)","TVA (EUR)","Anz."], {"20":("022","20%"),"10":("029","10%"),"13":("006","13%")}),
                    "CZ": (["Radek","Popis","Base","TVA","Pocet"], {"21":("1","21%"),"12":("2","12%")}),
                    "RO": (["Rand","Descriere","Base","TVA","Nr."], {"19":("9","19%"),"9":("10","9%"),"5":("11","5%")}),
                    "HU": (["Sor","Megnevezes","Base","TVA","Db"], {"27":("B2","27%"),"18":("C2","18%"),"5":("D2","5%")}),
                    "IE": (["Box","Description","Base (EUR)","TVA (EUR)","Count"], {"23":("T1","23%"),"9":("T1","9%"),"0":("E1","0%")}),
                }
                if country == "FR":
                    w.writerow(["Base HT","Taux (%)","TVA","ID vente","Canal"])
                    for r in country_results:
                        w.writerow([str(r.sale.amount_ht).replace(".",","),str(r.vat_rate).replace(".",","),str(r.vat_amount).replace(".",","),(r.sale.display_id or r.sale.sale_id),r.channel.value])
                    w.writerow([]); w.writerow(["TOTAL TVA FR",str(summary.net_fr_domestic_vat).replace(".",",")])
                    w.writerow(["TOTAL OSS",str(_oss_tva_net_total).replace(".",",")])
                elif country in fmt_map:
                    headers, mapping = fmt_map[country]
                    w.writerow(headers)
                    for rk, d in sorted(by_rate.items(), key=lambda x: -float(x[0])):
                        if mapping:
                            val = mapping.get(rk, ("", rk+"%"))
                            code, desc = val if isinstance(val, tuple) else (rk, val)
                        else:
                            code, desc = "", rk+"%"
                        w.writerow([code,desc,str(d["base"]).replace(".",","),str(d["tva"]).replace(".",","),d["nb"]])
                    w.writerow(["","TOTAL","",str(sum(d["tva"] for d in by_rate.values())).replace(".",",")])
                else:
                    w.writerow([lbl_base+" (EUR)","Taux (%)","TVA (EUR)","Nb","ID vente","Date"])
                    for r in country_results:
                        w.writerow([str(r.sale.amount_ht).replace(".",","),str(r.vat_rate).replace(".",","),str(r.vat_amount).replace(".",","),1,(r.sale.display_id or r.sale.sale_id),r.sale.transaction_date])
                    w.writerow([]); w.writerow(["TOTAL TVA","",str(sum(d["tva"] for d in by_rate.values())).replace(".",",")])
                w.writerow([]); w.writerow(["--- Détail ---"])
                w.writerow(["ID vente","Date","Base HT (EUR)","Taux (%)","TVA (EUR)","Canal","Pays dest."])
                for r in country_results:
                    w.writerow([(r.sale.display_id or r.sale.sale_id),r.sale.transaction_date,str(r.sale.amount_ht).replace(".",","),str(r.vat_rate).replace(".",","),str(r.vat_amount).replace(".",","),r.channel.value,r.sale.buyer_country])
                return ("\ufeff"+buf.getvalue()).encode("utf-8")

            meta_sel = COUNTRY_FISCAL_META.get(export_country, ("", "", "", "—", "—"))
            if export_country == "FR":
                country_vat = float(summary.net_fr_domestic_vat)
            else:
                country_vat = float(summary.net_local_by_country.get(export_country, 0))

            m1, m2, m3 = st.columns(3)
            m1.metric(f"TVA due — {_country_label(export_country)}", f"{country_vat:,.2f} EUR")
            m2.metric("Taux standard", meta_sel[3])
            m3.metric("Taux réduit", meta_sel[4])
            _gated_download(f"📥 Télécharger Déclaration {_country_label(export_country)} (.csv)", data=_build_local_csv(export_country), file_name=f"Déclaration TVA {_country_label(export_country)} - {nom_entreprise} - {period_label}.csv", mime="text/csv", use_container_width=True)

        st.divider()

        # 6. Comptabilité
        st.markdown("#### 📒 Comptabilité")
        st.caption("Export au format FEC (séparateur tabulation) pour import dans votre logiciel comptable.")
        _fec_ecriture_date = _fec_period_end_date(period_label)
        fec_bytes = generate_fec_bytes(results_net, period=period_label, ecriture_date=_fec_ecriture_date, piece_ref=f"Import Amazon {period_label}")
        _gated_download("📥 Journal des ventes (FEC .txt)", data=fec_bytes, file_name=f"Export Comptable FEC - {nom_entreprise} - {period_label}.txt", mime="text/plain", use_container_width=True)
