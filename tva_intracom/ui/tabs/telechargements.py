"""Onglet "Téléchargements" (extrait tel quel de app.py, with tab_dl:).

Génère et propose tous les exports : rapport Excel complet, XML OSS +
Excel/CSV OSS, rapport CA3 HTML, récapitulatif B2B, déclarations locales
par pays (formats CSV pré-formatés Kennzahl/Casilla/...), export FEC.

Lit `ctx.oss_tva_net_total`, calculé par l'onglet Déclarations — voir
tva_intracom/ui/tabs/context.py pour le détail de cette dépendance
intentionnelle entre onglets.
"""

from __future__ import annotations

import gc
import os
import tempfile
from decimal import Decimal

import streamlit as st

from tva_intracom.ca3_report import generate_ca3_html_report_v2
from tva_intracom.excel_report import export_xlsx
from tva_intracom.fec_export import generate_fec_bytes
from tva_intracom.i18n import _, country_label
from tva_intracom.local_vat_report import generate_local_vat_html_report
from tva_intracom.models import Scenario
from tva_intracom.oss_export import (
    aggregate_oss_results,
    build_b2b_excel,
    build_ioss_excel,
    build_oss_excel,
    find_oss_negative_buckets,
)
from tva_intracom.oss_xml import generate_oss_xml, preview_negative_bucket_suggestions
from tva_intracom.rates import COUNTRY_FISCAL_META, LOCAL_VAT_BOX_CODES
from tva_intracom.ui.formatting import _fec_period_end_date, _fmt
from tva_intracom.ui.tabs.context import TabContext
from tva_intracom.ui.display_mode import is_detailed


@st.fragment
def render_telechargements() -> None:
    """Rendu complet de l'onglet Téléchargements.

    IMPORTANT (mémoire) : `ctx` n'est plus reçu en paramètre mais lu depuis
    `st.session_state["_tab_ctx"]` -- voir la docstring de
    `render_detail_ventes` (detail_ventes.py) pour l'explication complète
    (fuite mémoire liée à la rétention des arguments d'un `@st.fragment`
    par Streamlit, indépendamment de `session_state`).

    `ctx.oss_tva_net_total`, écrit par `render_declarations()` (non
    fragmenté, appelé avant celui-ci dans app.py sur le MÊME objet stocké
    dans `st.session_state["_tab_ctx"]`), reste donc visible ici sans
    changement de comportement : c'est la même instance d'objet, seule sa
    provenance (paramètre -> lookup session_state) change.
    """
    ctx: TabContext = st.session_state["_tab_ctx"]
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
    # Couplage inter-onglets intentionnel (voir tabs/context.py) : ce module
    # suppose que render_declarations(ctx) a déjà tourné dans ce rerun et y a
    # écrit oss_tva_net_total. Si l'ordre des onglets est un jour changé dans
    # app.py, cette hypothèse casse silencieusement (valeur None utilisée
    # telle quelle dans l'export CSV local FR) — on préfère une erreur
    # explicite immédiate à un export silencieusement incorrect.
    assert ctx.oss_tva_net_total is not None, (
        "ctx.oss_tva_net_total est None : render_declarations(ctx) doit être "
        "appelé avant render_telechargements() dans app.py (voir tabs/context.py)."
    )
    _oss_tva_net_total = ctx.oss_tva_net_total
    home_country = getattr(ctx, "home_country", "FR") or "FR"

    # ── Cache des exports coûteux ───────────────────────────────────────────
    _dl_cache_key = (
        ctx.calc_key, nom_entreprise, siren_entreprise, tva_fr,
        tuple(sorted(local_vat_numbers.items())) if local_vat_numbers else None,
        ctx.target_currency,
    )

    # BUGFIX (RAM) : Si la clé de cache change, on supprime immédiatement les
    # anciens artefacts binaires du session_state pour libérer la mémoire,
    # sinon ils restent stockés tant que l'utilisateur ne regénère pas tout.
    if st.session_state.get("_dl_active_cache_key") != _dl_cache_key:
        for k in list(st.session_state.keys()):
            if k.startswith("_dl_artifact_") or k.startswith("_oss_preview_"):
                del st.session_state[k]
        st.session_state["_dl_active_cache_key"] = _dl_cache_key

    # ── Génération paresseuse (à la demande) des exports coûteux ───────────
    # BUGFIX (perf) : auparavant, TOUS les exports (Excel principal, OSS
    # XML+Excel, CA3/local HTML, B2B, FEC) étaient construits en RAM à CHAQUE
    # calcul, y compris pour les comptes gratuits/non débloqués qui ne
    # peuvent de toute façon rien télécharger depuis cet onglet (seul le
    # certificat VIES, géré séparément dans vies_ui.py, leur est accessible).
    # `_gated_download` ne fait que masquer le bouton a posteriori — les
    # bytes déjà construits étaient donc jetés sans jamais avoir servi.
    #
    # Nouveau comportement :
    #   - compte non débloqué (`_can_export=False`) : on ne construit RIEN,
    #     `_lazy_artifact` retourne toujours None immédiatement.
    #   - compte débloqué : rien n'est construit tant que l'utilisateur n'a
    #     pas cliqué sur "Générer ce rapport" pour CET artefact précis (le
    #     cache `_dl_cache_key` évite ensuite de reconstruire tant que le
    #     calcul TVA / l'identité de l'entreprise ne changent pas réellement).
    def _lazy_artifact(name: str, builder, label: str = "dl_generate_btn", spinner_label: str | None = None, **label_kwargs):
        if not _can_export:
            return None
        _skey = f"_dl_artifact_{name}"
        _cached = st.session_state.get(_skey)
        if _cached is not None and _cached[0] == _dl_cache_key:
            return _cached[1]
        # NOTE (aspect visuel) : `type="secondary"` (défaut) reprend le style
        # sombre/texte clair des boutons de téléchargement (`st.download_button`,
        # jugé plus lisible en thème sombre que `type="primary"`, trop clair).
        if st.button(_(label, **label_kwargs), key=f"_gen_btn_{name}", width="stretch", type="secondary"):
            with st.spinner(spinner_label or _("dl_generating_generic")):
                _value = builder()
            st.session_state[_skey] = (_dl_cache_key, _value)
            # BUGFIX (bouton "Générer" qui ne disparaît pas toujours) :
            # `st.button()` a déjà été rendu à l'écran AVANT qu'on sache ici
            # qu'il a été cliqué. Sans rerun, ce même passage de script
            # affiche donc à la fois le bouton "Générer" (déjà dessiné) ET,
            # juste en dessous, le bouton de téléchargement nouvellement
            # disponible — le bouton "Générer" ne disparaissait qu'au
            # prochain rerun (autre interaction). On force ici un rerun
            # immédiat, cantonné à ce fragment (`scope="fragment"`, sans
            # impact sur le reste de la page), pour que ce même passage
            # relise le cache et n'affiche plus que le bouton de
            # téléchargement dès ce clic.
            st.rerun(scope="fragment")
        return None

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
        # RAM : On évite de créer results_net (copie de liste) trop tôt ou
        # systématiquement. On l'encapsule dans une fonction pour ne la créer
        # que si un export OSS, B2B ou FEC est réellement demandé.
        #
        # PERF (voir README - évolution.md) : mémoïsée pour ce rendu de
        # l'onglet via une closure — `results + refund_results` alloue une
        # nouvelle liste de ~100k références à chaque appel ; plusieurs
        # sections (aperçu OSS, correctifs négatifs, exports) l'appellent
        # sans qu'un bouton soit forcément cliqué, ce qui recréait la même
        # liste géante plusieurs fois par run pour rien. Un simple cache "un
        # seul slot" suffit ici (pas de session_state : la fonction ne vit
        # que le temps de CE rendu de l'onglet).
        _results_net_cache: list | None = None
        def _get_results_net():
            nonlocal _results_net_cache
            if _results_net_cache is None:
                _results_net_cache = results + (refund_results or [])
            return _results_net_cache

        def _build_main_xlsx():
            xlsx_path = None
            try:
                with tempfile.NamedTemporaryFile(delete=False, suffix=".xlsx") as xlsx_tmp:
                    xlsx_path = xlsx_tmp.name
                _vies_ids = getattr(vies_summary, "vies_affected_sale_ids", set()) if vies_summary else set()
                export_xlsx(results, xlsx_path, scope_id=_vies_scope_id, summary=summary,
                            refund_results=refund_results, all_fc_transfers=all_fc_transfers,
                            vies_affected_sale_ids=_vies_ids, vies_summary=vies_summary,
                            countries_with_vat=countries_with_vat,
                            period=period_label, seller_country=home_country,
                            display_currency=ctx.target_currency,
                            invoice_credit_notes=all_invoice_credit_notes)
                with open(xlsx_path, "rb") as f:
                    return f.read()
            finally:
                if xlsx_path and os.path.exists(xlsx_path):
                    try:
                        os.remove(xlsx_path)
                    except Exception:
                        pass
                gc.collect()  # Libération forcée après gros export Excel

        # ── ZONE TÉLÉCHARGEMENTS ──────────────────────────────────────
        st.divider()

        # 1. Rapport principal — pleine largeur, style secondaire (sombre,
        # cohérent avec tous les autres boutons de téléchargement)
        st.markdown(_("dl_audit_header"))
        xlsx_bytes = _lazy_artifact("main_xlsx", _build_main_xlsx, label="dl_generate_main_btn", spinner_label=_("dl_generation_excel"))
        if not _can_export:
            # Paywall Stripe — data=b"" n'est jamais lu par gated_download
            # dans cette branche (voir billing_gate.py), et le fichier n'a
            # jamais été construit.
            _gated_download(
                _("dl_main_report_btn"), data=b"",
                file_name=_("dl_main_report_filename", company=nom_entreprise, period=period_label),
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
        elif xlsx_bytes is not None:
            _gated_download(
                _("dl_main_report_btn"), data=xlsx_bytes,
                file_name=_("dl_main_report_filename", company=nom_entreprise, period=period_label),
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
        # else : compte débloqué mais rapport pas encore généré — le bouton
        # "Générer ce rapport" est déjà affiché par _lazy_artifact ci-dessus.
        st.caption(_("dl_main_report_caption"))

        st.divider()

        # 2. Guichet Unique OSS
        st.markdown(_("dl_oss_header"))
        if any(r.scenario == Scenario.OSS_B2C for r in results):
            st.caption(_("dl_oss_caption"))

            # Ligne XML (Prioritaire)
            # ── Détection en amont des soldes OSS négatifs ──────────
            # Optimisation : Cache de l'agrégation pour éviter le recalcul sur rerun fragment
            _oss_agg_key = f"_oss_preview_{ctx.calc_key}"
            if _oss_agg_key not in st.session_state:
                _res_net = _get_results_net()
                _oss_agg_preview = aggregate_oss_results(_res_net, period=period_label)
                _negative_buckets = find_oss_negative_buckets(_oss_agg_preview)
                st.session_state[_oss_agg_key] = (_oss_agg_preview, _negative_buckets)
            else:
                _oss_agg_preview, _negative_buckets = st.session_state[_oss_agg_key]

            _confirm_corrections = st.session_state.get("confirm_oss_corrections", False)

            if _negative_buckets and _can_export:
                _res_net = _get_results_net()
                _suggestions = preview_negative_bucket_suggestions(_res_net, period_label)
                _any_matched = any(s.matched for s in _suggestions)
                if _any_matched:
                    with st.expander(_("dl_oss_negative_expander"), expanded=True):
                        for s in _suggestions:
                            _lbl = f"{country_label(s.bucket.departure)} → {country_label(s.bucket.arrival)} ({s.bucket.vat_rate}%)"
                            if s.matched:
                                _origins = ", ".join(sorted({m.origin_period for m in s.matched}))
                                st.markdown(_("dl_oss_negative_matched", label=_lbl, count=len(s.matched), origins=_origins))
                            if s.unmatched_count:
                                st.markdown(_("dl_oss_negative_unmatched", label=_lbl, count=s.unmatched_count, ht=f"{float(s.unmatched_ht):,.2f}"))
                        _confirm_corrections = st.checkbox(_("dl_oss_confirm_corrections"), key="confirm_oss_corrections")

            # BUGFIX (fiabilité fiscale) : auparavant, une ValueError levée par
            # generate_oss_xml() pour solde négatif non résolu (voir garde-fou
            # dans oss_xml.py) était capturée silencieusement et l'appel était
            # immédiatement relancé avec ignore_negatives=True. Le XML produit
            # contenait alors des montants négatifs dans le corps principal de
            # la déclaration (TaxableAmount/VatAmountIssued) — techniquement
            # généré, mais fiscalement invalide et rejeté par le portail OSS —
            # sans que l'utilisateur soit informé qu'une correction avait été
            # ignorée. Nouveau comportement : ignore_negatives n'est plus
            # jamais utilisé depuis l'UI. Si des soldes négatifs restent
            # bloquants après tentative de rattachement automatique, AUCUN XML
            # n'est généré et l'erreur détaillée (pays/taux/montants concernés)
            # est affichée en clair et reste visible tant que le point n'est
            # pas résolu (rattachement complété, ou avoirs corrigés en amont).
            _oss_xml_error_key = "_dl_artifact_oss_xml_error"

            def _build_oss_xml():
                _res_net = _get_results_net()
                try:
                    _xml_bytes = generate_oss_xml(results=_res_net, seller_vat=tva_fr, period=period_label, local_vat_numbers=local_vat_numbers, confirm_corrections=_confirm_corrections)
                except ValueError as _exc:
                    st.session_state[_oss_xml_error_key] = str(_exc)
                    return None
                st.session_state.pop(_oss_xml_error_key, None)
                return _xml_bytes

            # On inclut _confirm_corrections dans la clé de cache car le XML change selon cette option
            oss_xml_bytes = _lazy_artifact(f"oss_xml_{_confirm_corrections}", _build_oss_xml, label="dl_generate_oss_xml_btn")

            _oss_xml_error = st.session_state.get(_oss_xml_error_key)
            if _oss_xml_error:
                st.error(_("dl_oss_xml_blocked_error", detail=_oss_xml_error))

            if not _can_export:
                _gated_download(_("dl_xml_oss_btn"), data=b"", file_name=_("dl_xml_oss_filename", company=nom_entreprise, period=period_label), mime="application/xml")
            elif oss_xml_bytes:
                _gated_download(_("dl_xml_oss_btn"), data=oss_xml_bytes, file_name=_("dl_xml_oss_filename", company=nom_entreprise, period=period_label), mime="application/xml")

            # Ligne Excel (Détail)
            def _build_oss_xlsx():
                oss_xlsx_path = None
                try:
                    with tempfile.NamedTemporaryFile(delete=False, suffix=".xlsx") as oss_tmp:
                        oss_xlsx_path = oss_tmp.name
                    build_oss_excel(_get_results_net(), oss_xlsx_path, period=period_label)
                    with open(oss_xlsx_path, "rb") as f:
                        return f.read()
                finally:
                    if oss_xlsx_path and os.path.exists(oss_xlsx_path):
                        try:
                            os.remove(oss_xlsx_path)
                        except Exception:
                            pass
                    gc.collect()

            oss_xlsx_bytes = _lazy_artifact("oss_xlsx", _build_oss_xlsx, label="dl_generate_oss_xlsx_btn")
            if not _can_export:
                _gated_download(_("dl_xlsx_oss_btn"), data=b"", file_name=_("dl_xlsx_oss_filename", company=nom_entreprise, period=period_label), mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
            elif oss_xlsx_bytes is not None:
                _gated_download(_("dl_xlsx_oss_btn"), data=oss_xlsx_bytes, file_name=_("dl_xlsx_oss_filename", company=nom_entreprise, period=period_label), mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        else:
            st.info(_("no_oss_sales_info"))

        # 2 bis. Guichet Unique IOSS — export SÉPARÉ de l'OSS (correctif
        # 2026-08-09 : ces ventes n'apparaissent plus jamais dans l'export
        # OSS ci-dessus). Périodicité MENSUELLE, pas trimestrielle — le
        # `period_label` affiché ici est celui de l'app (généralement
        # trimestriel pour l'OSS) : à titre indicatif uniquement, l'utilisateur
        # doit re-word/découper par mois s'il traite plusieurs mois à la fois.
        #
        # Section "avancée" : repliée (masquée entièrement) en mode Simple
        # si aucune vente IOSS n'est présente, pour ne pas encombrer le
        # scroll d'un vendeur qui ne fait pas d'IOSS. Toujours visible dès
        # que des données réelles existent, quel que soit le mode.
        _has_ioss_sales = any(r.scenario == Scenario.IOSS_DIRECT for r in results)
        if is_detailed() or _has_ioss_sales:
            st.divider()
            st.markdown(_("dl_ioss_header"))
            if _has_ioss_sales:
                st.caption(_("dl_ioss_caption"))

                def _build_ioss_xlsx():
                    ioss_xlsx_path = None
                    try:
                        with tempfile.NamedTemporaryFile(delete=False, suffix=".xlsx") as ioss_tmp:
                            ioss_xlsx_path = ioss_tmp.name
                        build_ioss_excel(_get_results_net(), ioss_xlsx_path, period=period_label)
                        with open(ioss_xlsx_path, "rb") as f:
                            return f.read()
                    finally:
                        if ioss_xlsx_path and os.path.exists(ioss_xlsx_path):
                            try:
                                os.remove(ioss_xlsx_path)
                            except Exception:
                                pass
                        gc.collect()

                ioss_xlsx_bytes = _lazy_artifact("ioss_xlsx", _build_ioss_xlsx, label="dl_generate_ioss_xlsx_btn")
                if not _can_export:
                    _gated_download(_("dl_xlsx_ioss_btn"), data=b"", file_name=_("dl_xlsx_ioss_filename", company=nom_entreprise, period=period_label), mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
                elif ioss_xlsx_bytes is not None:
                    _gated_download(_("dl_xlsx_ioss_btn"), data=ioss_xlsx_bytes, file_name=_("dl_xlsx_ioss_filename", company=nom_entreprise, period=period_label), mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
            else:
                st.info(_("no_ioss_sales_info"))

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
            def _build_ca3_html():
                return generate_ca3_html_report_v2(
                    results=results, refund_results=refund_results, company_name=nom_entreprise, siren=siren_entreprise,
                    period_label=period_label, all_fc_transfers=all_fc_transfers, seller_country="FR",
                ).encode("utf-8")
            ca3_html_bytes = _lazy_artifact("ca3_html", _build_ca3_html, label="dl_generate_ca3_btn")
            if not _can_export:
                _gated_download(_("dl_ca3_html_btn"), data=b"", file_name=_("dl_ca3_html_filename", company=nom_entreprise, period=period_label), mime="text/html")
            elif ca3_html_bytes is not None:
                _gated_download(_("dl_ca3_html_btn"), data=ca3_html_bytes, file_name=_("dl_ca3_html_filename", company=nom_entreprise, period=period_label), mime="text/html")
        else:
            st.markdown(_("home_country_declaration_header", country=country_label(home_country)))
            st.caption(_("home_country_declaration_caption"))
            def _build_home_html():
                return generate_local_vat_html_report(
                    results=results, refund_results=refund_results, vat_country=home_country,
                    company_name=nom_entreprise, siren=siren_entreprise,
                    period_label=period_label, seller_country=home_country,
                ).encode("utf-8")
            _home_html_bytes = _lazy_artifact("home_html", _build_home_html, label="dl_generate_home_html_btn", country=country_label(home_country))
            _home_filename = _("dl_local_html_filename", country=home_country, company=nom_entreprise, period=period_label)
            _home_label = _("dl_local_html_btn", country=country_label(home_country))
            if not _can_export:
                _gated_download(_home_label, data=b"", file_name=_home_filename, mime="text/html")
            elif _home_html_bytes is not None:
                _gated_download(_home_label, data=_home_html_bytes, file_name=_home_filename, mime="text/html")

        # 4. Livraisons B2B — section "avancée" : masquée en mode Simple si
        # aucune vente B2B (même logique que la section IOSS ci-dessus).
        _has_b2b_sales = any(r.scenario == Scenario.B2B_REVERSE_CHARGE for r in results)
        if is_detailed() or _has_b2b_sales:
            st.divider()
            st.markdown(_("b2b_deliveries_header"))
            if _has_b2b_sales:
                st.caption(_("b2b_deliveries_caption", count=len([r for r in results if r.scenario == Scenario.B2B_REVERSE_CHARGE]), ht=f"{float(summary.reverse_charge_ht):,.2f}"))
                def _build_b2b_xlsx():
                    b2b_xlsx_path = None
                    try:
                        with tempfile.NamedTemporaryFile(delete=False, suffix=".xlsx") as b2b_tmp2:
                            b2b_xlsx_path = b2b_tmp2.name
                        build_b2b_excel(_get_results_net(), b2b_xlsx_path, period=period_label)
                        with open(b2b_xlsx_path, "rb") as f:
                            return f.read()
                    finally:
                        if b2b_xlsx_path and os.path.exists(b2b_xlsx_path):
                            try:
                                os.remove(b2b_xlsx_path)
                            except Exception:
                                pass
                        gc.collect()
                b2b_xlsx_bytes = _lazy_artifact("b2b_xlsx", _build_b2b_xlsx, label="dl_generate_b2b_btn")
                _b2b_filename = _("dl_xlsx_b2b_filename", company=nom_entreprise, period=period_label)
                if not _can_export:
                    _gated_download(_("dl_xlsx_b2b_btn"), data=b"", file_name=_b2b_filename, mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
                elif b2b_xlsx_bytes is not None:
                    _gated_download(_("dl_xlsx_b2b_btn"), data=b2b_xlsx_bytes, file_name=_b2b_filename, mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
            else:
                st.info(_("no_b2b_sales_info"))

        st.divider()

        # 5. Déclarations Locales (hors pays d'origine) — section
        # "avancée" : repliée si aucune vente locale hors pays d'origine
        # n'est présente ET qu'on est en mode Simple (même logique IOSS/B2B).
        _local_countries = sorted({r.vat_country for r in results if r.channel.value == "LOCAL" and r.vat_country})
        if is_detailed() or _local_countries:
            st.markdown(_("local_declarations_header"))
            st.caption(_("local_declarations_home_note", country=country_label(home_country)))
            if not _local_countries:
                st.info(_("no_local_sales_info"))
            else:
                export_country = st.selectbox(_("dl_select_country_label"), _local_countries, format_func=lambda c: f"{country_label(c)} ({c})", key="dl_country_select")

                def _build_local_csv(country):
                    import io as _il, csv as _cl
                    from collections import defaultdict as _dd
                    buf = _il.StringIO(); w = _cl.writer(buf, delimiter=";")
                    period_lbl = period_label or "Periode non renseignee"
                    meta = COUNTRY_FISCAL_META.get(country, (f"Declaration TVA {country_label(country)}", "Base HT", "TVA", "—", "—"))
                    decl_name, lbl_base, lbl_tax, rate_std, rate_red = meta
                    _res_net = _get_results_net()
                    country_results = [r for r in _res_net if r.vat_country == country and r.channel.value in ("LOCAL", "FR_DOMESTIC")]
                    by_rate = _dd(lambda: {"base": Decimal("0"), "tva": Decimal("0"), "nb": 0})
                    for r in country_results:
                        by_rate[str(r.vat_rate)]["base"] += r.sale.amount_ht
                        by_rate[str(r.vat_rate)]["tva"]  += r.vat_amount
                        by_rate[str(r.vat_rate)]["nb"]   += 1
                    w.writerow([f"{decl_name} — {period_lbl}"])
                    w.writerow([f"Pays : {country_label(country)} ({country}) | Standard : {rate_std} | Reduit : {rate_red}"])
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
                # BUGFIX : ce montant (TVA due pour le pays sélectionné) était
                # affiché en clair même pour un compte non premium/non débloqué
                # pour cette période — alors que le même chiffre est masqué dans
                # l'onglet Déclarations (voir declarations.py, `locked_premium`).
                # On applique le même masquage ici, par cohérence : la valeur ne
                # doit être visible qu'une fois l'export réellement débloqué.
                # Message spécifique à la vraie raison du blocage (paiement,
                # rattachement compte, SIREN, quota) — voir
                # billing_gate.preview_lock_message().
                _lock_msg = ctx.lock_message
                m1.metric(_("dl_local_vat_due_metric", country=country_label(export_country)),
                          _fmt(country_vat) if _can_export else _lock_msg)
                m2.metric(_("dl_standard_rate_metric"), meta_sel[3])
                m3.metric(_("dl_reduced_rate_metric"), meta_sel[4])
                c1, c2 = st.columns(2)
                with c1:
                    _local_csv_filename = _("dl_local_csv_filename", country=export_country, company=nom_entreprise, period=period_label)
                    _local_csv_label = _("dl_local_csv_btn", country=country_label(export_country))
                
                    # Performance : Utilisation du cache lazy pour le CSV local
                    csv_bytes = _lazy_artifact(f"local_csv_{export_country}", lambda: _build_local_csv(export_country), label="dl_generate_local_csv_btn", country=country_label(export_country))
                
                    if not _can_export:
                        _gated_download(_local_csv_label, data=b"", file_name=_local_csv_filename, mime="text/csv")
                    elif csv_bytes is not None:
                        _gated_download(_local_csv_label, data=csv_bytes, file_name=_local_csv_filename, mime="text/csv")
                with c2:
                    if export_country != home_country:
                        _local_html_filename = _("dl_local_html_filename", country=export_country, company=nom_entreprise, period=period_label)
                        _local_html_label = _("dl_local_html_btn", country=country_label(export_country))
                    
                        # Performance : Utilisation du cache lazy pour le HTML local
                        def _build_local_html():
                            return generate_local_vat_html_report(
                                results=results, refund_results=refund_results, vat_country=export_country,
                                company_name=nom_entreprise, siren=siren_entreprise,
                                period_label=period_label, seller_country=home_country,
                            ).encode("utf-8")
                    
                        html_bytes = _lazy_artifact(f"local_html_{export_country}", _build_local_html, label="dl_generate_local_html_btn", country=country_label(export_country))
                    
                        if not _can_export:
                            _gated_download(_local_html_label, data=b"", file_name=_local_html_filename, mime="text/html")
                        elif html_bytes is not None:
                            _gated_download(_local_html_label, data=html_bytes, file_name=_local_html_filename, mime="text/html")

        st.divider()

        # 6. Comptabilité
        st.markdown(_("dl_fec_header"))
        st.caption(_("dl_fec_caption"))
        _fec_ecriture_date = _fec_period_end_date(period_label)
        def _build_fec_bytes():
            return generate_fec_bytes(_get_results_net(), period=period_label, ecriture_date=_fec_ecriture_date, piece_ref=_("dl_fec_piece_ref", period=period_label))
        fec_bytes = _lazy_artifact("fec_bytes", _build_fec_bytes, label="dl_generate_fec_btn")
        _fec_filename = _("dl_fec_filename", company=nom_entreprise, period=period_label)
        if not _can_export:
            _gated_download(_("dl_fec_btn"), data=b"", file_name=_fec_filename, mime="text/plain")
        elif fec_bytes is not None:
            _gated_download(_("dl_fec_btn"), data=fec_bytes, file_name=_fec_filename, mime="text/plain")
