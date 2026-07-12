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
from tva_intracom.rates import COUNTRY_FISCAL_META, LOCAL_VAT_BOX_CODES
from tva_intracom.local_vat_report import generate_local_vat_html_report
from tva_intracom.ui.formatting import _country_label, _fec_period_end_date, _fmt
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
    home_country = getattr(ctx, "home_country", "FR") or "FR"

    results_net = results + (refund_results or [])

    # period_label, _can_export, _gated_download et _get_payg_checkout_url
    # sont tous calculés/définis plus haut (avant les onglets) — voir bloc
    # « GATING BILLING » — pour être également utilisables dans les autres
    # onglets (Déclarations, VIES, Audit Amazon).
    if _period_detected_range:
        st.info(_("period_detected_info", period=period_label, start=_period_detected_range[0], end=_period_detected_range[1]))

    if not _can_export and period_label:
        st.warning(_("period_gated_warning", period=period_label, suffix=_unlock_label_suffix))

    st.subheader(_("tab_downloads"))
    with st.container():
        with st.spinner(_("dl_generation_excel")):
            with tempfile.NamedTemporaryFile(delete=False, suffix=".xlsx") as xlsx_tmp:
                _vies_ids = getattr(vies_summary, "vies_affected_sale_ids", set()) if vies_summary else set()
                xlsx_path = export_xlsx(results, xlsx_tmp.name, scope_id=_vies_scope_id, summary=summary,
                    refund_results=refund_results, all_fc_transfers=all_fc_transfers,
                    vies_affected_sale_ids=_vies_ids, vies_summary=vies_summary,
                    countries_with_vat=countries_with_vat,
                    period=period_label, seller_country=home_country,
                    invoice_credit_notes=all_invoice_credit_notes)
            with open(xlsx_path,"rb") as f: xlsx_bytes = f.read()

        # ── ZONE TÉLÉCHARGEMENTS ──────────────────────────────────────
        st.divider()

        # 1. Rapport principal — pleine largeur, bouton primaire
        st.markdown(_("dl_audit_header"))
        _gated_download(
            _("dl_main_report_btn"),
            data=xlsx_bytes,
            file_name=_("dl_main_report_filename", company=nom_entreprise, period=period_label),
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            type="primary", use_container_width=True,
        )
        st.caption(_("dl_main_report_caption"))

        st.divider()

        # 2. Guichet Unique OSS
        st.markdown(_("dl_oss_header"))
        oss_results_dl = [r for r in results_net if r.scenario == Scenario.OSS_B2C]
        if oss_results_dl:
            st.caption(_("dl_oss_caption"))

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
                    with st.expander(_("dl_oss_negative_expander"), expanded=True):
                        for s in _suggestions:
                            _lbl = f"{_country_label(s.bucket.departure)} → {_country_label(s.bucket.arrival)} ({s.bucket.vat_rate}%)"
                            if s.matched:
                                _origins = ", ".join(sorted({m.origin_period for m in s.matched}))
                                st.markdown(_("dl_oss_negative_matched", label=_lbl, count=len(s.matched), origins=_origins))
                            if s.unmatched_count:
                                st.markdown(_("dl_oss_negative_unmatched", label=_lbl, count=s.unmatched_count, ht=f"{float(s.unmatched_ht):,.2f}"))
                        _confirm_corrections = st.checkbox(_("dl_oss_confirm_corrections"), key="confirm_oss_corrections")

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

        # 3. Déclaration du pays d'origine (établissement du vendeur)
        # — CA3 (Cerfa) si le pays d'origine est la France (seul cas où le
        # fac-similé Cerfa a été vérifié — voir ca3_report.py), sinon le
        # rapport HTML générique (local_vat_report.py) pour CE pays.
        # Aucun impact sur l'OSS : ce bloc ne touche que le canal
        # DOMESTIC/FR_DOMESTIC, pas Channel.OSS.
        if home_country == "FR":
            st.markdown(_("france_ca3_header"))
            st.caption(_("france_ca3_caption"))
            ca3_html = generate_ca3_html_report_v2(
                results=results, refund_results=refund_results, company_name=nom_entreprise, siren=siren_entreprise,
                period_label=period_label, all_fc_transfers=all_fc_transfers, seller_country="FR",
            )
            _gated_download(_("dl_ca3_html_btn"), data=ca3_html.encode("utf-8"), file_name=_("dl_ca3_html_filename", company=nom_entreprise, period=period_label), mime="text/html", use_container_width=True)
        else:
            st.markdown(_("home_country_declaration_header", country=_country_label(home_country)))
            st.caption(_("home_country_declaration_caption"))
            _home_html = generate_local_vat_html_report(
                results=results, refund_results=refund_results, vat_country=home_country,
                company_name=nom_entreprise, siren=siren_entreprise,
                period_label=period_label, seller_country=home_country,
            )
            _gated_download(
                _("dl_local_html_btn", country=_country_label(home_country)),
                data=_home_html.encode("utf-8"),
                file_name=_("dl_local_html_filename", country=home_country, company=nom_entreprise, period=period_label),
                mime="text/html", use_container_width=True,
            )

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

        # 5. Déclarations Locales (hors pays d'origine)
        st.markdown(_("local_declarations_header"))
        st.caption(_("local_declarations_home_note", country=_country_label(home_country)))
        _local_tax_data = [r for r in results_net if r.channel.value == "LOCAL" and r.vat_country]
        if not _local_tax_data:
            st.info(_("no_local_sales_info"))
        else:
            st.caption(_("local_declarations_caption"))
            _local_countries = sorted({r.vat_country for r in _local_tax_data})
            export_country = st.selectbox(_("dl_select_country_label"), _local_countries, format_func=lambda c: f"{_country_label(c)} ({c})", key="dl_country_select")

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
                fmt_map = LOCAL_VAT_BOX_CODES  # source unique — voir tva_intracom/rates.py
                if country == home_country:
                    w.writerow(["Base HT","Taux (%)","TVA","ID vente","Canal"])
                    for r in country_results:
                        w.writerow([str(r.sale.amount_ht).replace(".",","),str(r.vat_rate).replace(".",","),str(r.vat_amount).replace(".",","),(r.sale.display_id or r.sale.sale_id),r.channel.value])
                    w.writerow([]); w.writerow([f"TOTAL TVA {home_country}",str(summary.net_fr_domestic_vat).replace(".",",")])
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
            # export_country provient du pool "LOCAL" (jamais home_country,
            # par construction du moteur — voir engine.py) : la branche
            # summary.net_fr_domestic_vat ne peut être atteinte que si
            # home_country == "FR" ET qu'une valeur "FR" apparaît malgré
            # tout dans ce pool, ce qui n'arrive jamais. Gardée par
            # défensivité uniquement.
            if export_country == home_country:
                country_vat = float(summary.net_fr_domestic_vat)
            else:
                country_vat = float(summary.net_local_by_country.get(export_country, 0))

            m1, m2, m3 = st.columns(3)
            m1.metric(_("dl_local_vat_due_metric", country=_country_label(export_country)), _fmt(country_vat))
            m2.metric(_("dl_standard_rate_metric"), meta_sel[3])
            m3.metric(_("dl_reduced_rate_metric"), meta_sel[4])
            c1, c2 = st.columns(2)
            with c1:
                _gated_download(_("dl_local_csv_btn", country=_country_label(export_country)), data=_build_local_csv(export_country), file_name=_("dl_local_csv_filename", country=export_country, company=nom_entreprise, period=period_label), mime="text/csv", use_container_width=True)
            with c2:
                if export_country != home_country:
                    _local_html = generate_local_vat_html_report(
                        results=results, refund_results=refund_results, vat_country=export_country,
                        company_name=nom_entreprise, siren=siren_entreprise,
                        period_label=period_label, seller_country=home_country,
                    )
                    _gated_download(
                        _("dl_local_html_btn", country=_country_label(export_country)),
                        data=_local_html.encode("utf-8"),
                        file_name=_("dl_local_html_filename", country=export_country, company=nom_entreprise, period=period_label),
                        mime="text/html", use_container_width=True,
                    )
            st.caption(_("local_vat_html_caption"))

        st.divider()

        # 6. Comptabilité
        st.markdown(_("dl_fec_header"))
        st.caption(_("dl_fec_caption"))
        _fec_ecriture_date = _fec_period_end_date(period_label)
        fec_bytes = generate_fec_bytes(results_net, period=period_label, ecriture_date=_fec_ecriture_date, piece_ref=_("dl_fec_piece_ref", period=period_label))
        _gated_download(_("dl_fec_btn"), data=fec_bytes, file_name=_("dl_fec_filename", company=nom_entreprise, period=period_label), mime="text/plain", use_container_width=True)