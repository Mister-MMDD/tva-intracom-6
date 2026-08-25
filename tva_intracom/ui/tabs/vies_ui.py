"""Onglet "VIES" (extrait tel quel de app.py, with tab_vies:).

KPIs de validation VIES, classification manuelle des numéros non
vérifiés (st.fragment), overrides manuels persistés, reclassifications
B2B→B2C et export CSV du rapport d'audit VIES.
"""

from __future__ import annotations

import html

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from tva_intracom.i18n import _, country_label
from tva_intracom.ui.formatting import _gated_preview_table, _smart_money_df, \
    _render_filter_bar, _fmt
from tva_intracom.ui.tabs.context import TabContext
from tva_intracom.ui.calc_cache import CalcCacheState


@st.fragment
def render_manual_vies_classification() -> None:
    """Isole la classification manuelle en fragment pour ne pas rerun toute l'app."""
    ctx: TabContext = st.session_state["_tab_ctx"]
    vies_summary = ctx.vies_summary
    _vies_scope_id = ctx.vies_scope_id

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
                # BUGFIX (2026-08-25) : `_smo_apply` peut désormais lever
                # PermissionError si, ENTRE le chargement de cette page et ce
                # clic, un override a été créé entretemps pour un de ces
                # numéros par quelqu'un d'autre (rare mais possible en
                # cabinet multi-comptes) — un lecteur se retrouverait alors
                # à modifier un override existant sans le savoir. Sans ce
                # try/except, l'exception remontait non interceptée. On
                # informe simplement l'utilisateur de retenter (le state
                # `_vies_manual_overrides` n'est pas vidé, rien n'est perdu).
                try:
                    for _vat_key, _choice_val in _pending.items():
                        _smo_apply(_vies_scope_id, _vat_key, valid=(_choice_val == _("manual_valid")),
                                   acting_user_id=ctx.current_user_id)
                    st.session_state.pop("_vies_manual_overrides", None)
                    CalcCacheState.invalidate_calc()
                    st.success(_("vies_manual_class_success"))
                    st.rerun()
                except PermissionError:
                    st.error(_("vies_manual_class_race_error"))
        with _col_reset:
            if st.button(_("vies_manual_class_reset_btn")):
                st.session_state.pop("_vies_manual_overrides", None)
                st.rerun()


def render_vies(ctx: TabContext) -> None:
    """Rendu complet de l'onglet VIES."""
    # Purge automatique des expirés pour ce compte au chargement de l'onglet
    # (évite d'avoir à cliquer manuellement sur le bouton de purge en sidebar)
    if f"vies_auto_purged_{ctx.vies_scope_id}" not in st.session_state:
        from tva_intracom.vies_engine import purge_expired_cache
        try:
            purge_expired_cache(ctx.vies_scope_id)
            st.session_state[f"vies_auto_purged_{ctx.vies_scope_id}"] = True
        except Exception:
            pass

    _can_export = ctx.can_export
    _gated_download = ctx.gated_download
    _vies_retry_nonce = ctx.vies_retry_nonce
    _vies_scope_id = ctx.vies_scope_id
    enable_vies = ctx.enable_vies
    nom_entreprise = ctx.nom_entreprise
    siren_entreprise = ctx.siren_entreprise
    period_label = ctx.period_label
    vies_summary = ctx.vies_summary

    # ── Certificat de Validité VIES (PDF) ────────────────────────────────
    # Photographie complète de l'historique de vérification VIES connu par ce
    # scope (pas seulement les numéros du fichier importé aujourd'hui) — sert
    # de preuve de bonne foi en cas de contrôle fiscal (voir docstring de
    # vies_certificate.py). Affiché même si enable_vies est désactivé ou si
    # aucun B2B n'a été détecté dans le fichier courant : la piste d'audit,
    # elle, peut contenir des vérifications issues d'imports précédents.
    with st.expander(_("vies_certificate_expander"), expanded=False):
        st.caption(_("vies_certificate_caption"))

        # RÔLES (2026-08-25) : le scope "account" (photographie de tout le
        # cache VIES de l'organisation, via get_scope_vies_snapshot /
        # get_scope_vies_history_flat) est réservé à l'administrateur — un
        # lecteur n'a pas à voir/exporter la base VIES entière du cabinet.
        # Le scope "file" (numéros du fichier importé aujourd'hui) reste
        # accessible à tous, avec l'option historique.
        if ctx.is_admin:
            _cert_scope = st.radio(
                _("vies_certificate_scope_label"),
                options=["file", "account"],
                format_func=lambda v: _("vies_certificate_scope_file") if v == "file" else _("vies_certificate_scope_account"),
                key="vies_cert_scope",
                horizontal=True,
            )
            st.caption(_("vies_certificate_scope_account_hint") if _cert_scope == "account" else _("vies_certificate_scope_file_hint"))
        else:
            _cert_scope = "file"
            st.caption(_("vies_certificate_scope_file_hint"))

        _cert_history_mode = st.checkbox(
            _("vies_certificate_history_checkbox"),
            key="vies_cert_history_mode",
        )
        st.caption(_("vies_certificate_history_hint"))

        if st.button(_("vies_certificate_btn"), key="btn_gen_vies_certificate"):
            try:
                from tva_intracom.vies_engine import normalize_full_vat

                # Périmètre "fichier importé" : utile pour un cabinet
                # comptable qui suit plusieurs clients dans le même scope —
                # ne garder que les n° de TVA B2B présents dans les ventes
                # actuellement chargées, pas tout l'historique du
                # compte/cabinet. Ventes + avoirs : un n° de TVA peut
                # n'apparaître que sur un avoir (remboursement d'une vente
                # d'une période antérieure non présente dans le lot importé
                # aujourd'hui). Sans les avoirs ici, ce numéro est écarté du
                # certificat en mode "Fichier" alors qu'il est bien compté
                # dans le KPI affiché à l'écran (engine.py,
                # vies_summary.total_checked, qui lui boucle sur ventes +
                # avoirs) et bien présent dans le cache scope (mode
                # "Compte") — même correctif que
                # excel_report.py::_write_vies_history_tab.
                _file_vat_ids = None
                if _cert_scope == "file":
                    _file_vat_ids = set()
                    for _r in ctx.results + (ctx.refund_results or []):
                        _bvn = getattr(_r.sale, "buyer_vat_number", None)
                        if _bvn:
                            _file_vat_ids.add(normalize_full_vat(_bvn, _r.sale.buyer_country))

                if _cert_history_mode:
                    from tva_intracom.vies_engine import get_scope_vies_history_flat
                    from tva_intracom.vies_certificate import generate_vies_history_pdf

                    _history_rows = get_scope_vies_history_flat(
                        _vies_scope_id,
                        full_vats=sorted(_file_vat_ids) if _file_vat_ids is not None else None,
                    )
                    _pdf_bytes = generate_vies_history_pdf(
                        _history_rows,
                        company_name=nom_entreprise,
                        siren=siren_entreprise,
                        scope_id=_vies_scope_id,
                        period_label=period_label if _cert_scope == "file" else _("vies_certificate_full_history"),
                        country_label_fn=country_label,
                        translator=_,
                    )
                    _is_empty = not _history_rows
                else:
                    from tva_intracom.vies_engine import get_scope_vies_snapshot
                    from tva_intracom.vies_certificate import generate_vies_certificate_pdf

                    _snapshot = get_scope_vies_snapshot(_vies_scope_id)
                    if _file_vat_ids is not None:
                        _snapshot = [row for row in _snapshot if row["vat_id"] in _file_vat_ids]
                    _pdf_bytes = generate_vies_certificate_pdf(
                        _snapshot,
                        company_name=nom_entreprise,
                        siren=siren_entreprise,
                        scope_id=_vies_scope_id,
                        period_label=period_label if _cert_scope == "file" else _("vies_certificate_full_history"),
                        country_label_fn=country_label,
                        translator=_,
                    )
                    _is_empty = not _snapshot

                st.session_state["_vies_certificate_pdf"] = _pdf_bytes
                st.session_state["_vies_certificate_scope"] = _cert_scope
                st.session_state["_vies_certificate_history_mode"] = _cert_history_mode
                if _is_empty:
                    st.info(_("vies_certificate_history_empty_info") if _cert_history_mode else _("vies_certificate_empty_info"))
            except Exception as _cert_err:
                st.error(_("vies_certificate_error", error=_cert_err))

        if st.session_state.get("_vies_certificate_pdf"):
            _cert_suffix = "fichier" if st.session_state.get("_vies_certificate_scope") == "file" else "compte"
            if st.session_state.get("_vies_certificate_history_mode"):
                _cert_suffix += "_historique"
            # Le certificat VIES est gratuit au téléchargement (preuve de bonne foi)
            st.download_button(
                _("vies_certificate_dl_btn"),
                data=st.session_state["_vies_certificate_pdf"],
                file_name=_("vies_certificate_filename", company=f"{nom_entreprise}_{_cert_suffix}"),
                mime="application/pdf",
                type="primary",
            )

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
        # NB : total_not_auto_verified (pas total_inconclusive seul) inclut
        # aussi les replis sur cache périmé (stale_fallback_count) — sans ça
        # une panne VIES en cours de calcul serait invisible dans ce KPI
        # alors même que les ventes concernées sont traitées par sécurité
        # comme B2C, exactement comme un inconclusif classique.
        v4.metric(_("vies_kpi_unverified"), vies_summary.total_not_auto_verified,
            delta=f"{vies_summary.total_not_auto_verified}" if vies_summary.total_not_auto_verified else None, delta_color="off")
        v5.metric(_("vies_kpi_recovered_vat"), f"{float(vies_summary.fraud_avoided_amount):,.2f} €")

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

            render_manual_vies_classification()

            # ── Ré-essai VIES automatique en arrière-plan ────────────────
            # Déclenché UNIQUEMENT ici (juste après un calcul qui laisse des
            # inconclusifs) ou via le clic manuel plus bas (bump du nonce
            # existant) — jamais sur une minuterie. job_id déterministe
            # (scope + ensemble exact de numéros inconclusifs) : un rerun
            # Streamlit qui repasse par ici pendant que le job tourne ne
            # relance donc jamais un second thread pour le même lot, et un
            # nouveau lot (après recalcul) obtient naturellement un nouveau
            # job_id — voir background_calc.start_vies_retry_loop.
            from tva_intracom.ui.background_calc import (
                start_vies_retry_loop, vies_retry_job_id as _vies_retry_jid_fn,
                get_job_state, is_job_done, render_job_progress, clear_job,
            )

            _vies_retry_vat_ids = list(vies_summary.inconclusive_vats)
            _vies_retry_jid = (
                _vies_retry_jid_fn(_vies_scope_id, _vies_retry_vat_ids)
                if _vies_retry_vat_ids else None
            )

            if _vies_retry_jid and get_job_state(_vies_retry_jid) is None:
                start_vies_retry_loop(_vies_scope_id, _vies_retry_vat_ids)

            _vies_retry_running = (
                _vies_retry_jid is not None and not is_job_done(_vies_retry_jid)
            )

            if _vies_retry_running:
                # Bouton désactivé par sécurité tant que la boucle auto tourne
                # (évite un second retry_vats_batch concurrent sur le même lot).
                st.button(_("vies_reverify_btn"), key="retry_vies_btn", disabled=True)
                st.caption(_("vies_retry_in_progress"))
                render_job_progress(_vies_retry_jid, _("vies_retry_progress_label"))
            else:
                _vies_retry_state = get_job_state(_vies_retry_jid) if _vies_retry_jid else None
                _vies_retry_result = _vies_retry_state.result if _vies_retry_state else None
                if _vies_retry_result and _vies_retry_result.get("resolved", 0) > 0:
                    st.info(_("vies_retry_done_info", count=_vies_retry_result["resolved"]))

                if st.button(_("vies_reverify_btn"), key="retry_vies_btn"):
                    if _vies_retry_jid:
                        clear_job(_vies_retry_jid)
                    CalcCacheState.save_vies_retry_nonce(_vies_retry_nonce + 1)
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
            # Garantie explicite (avant : implicite via l'atomicité de
            # l'import ci-dessus + le garde-fou `if _existing_overrides_b:`
            # plus bas, qui rendait ces 3 noms inutilisés dans ce chemin —
            # correct au runtime mais fragile aux futures évolutions, et
            # signalé par l'IDE comme "referenced before assignment").
            _smo_edit = _dmo_edit = None
            _vies_is_expired_b = lambda *_a, **_kw: True

        # On ne garde que les overrides concernant un numéro de TVA présent
        # dans le fichier actuellement chargé — pas tout l'historique du
        # compte/cabinet (qui peut couvrir d'autres clients/imports).
        _current_file_vats_b = set(
            getattr(vies_summary, "vat_to_display_ids", {}) or {}
        )
        _existing_overrides_b = [
            (_v, _iv, _sa) for (_v, _iv, _sa) in _existing_overrides_b
            if _v in _current_file_vats_b
        ]

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

                    # SÉCURITÉ (XSS) : _ov_vat2 vient de la base des overrides (numéro TVA
                    # normalisé, peu à risque) mais _ov_sales2 provient de
                    # vat_to_display_ids, qui reflète les display_id/sale_id du fichier
                    # Amazon importé — donnée NON FIABLE. Ce bloc passe par
                    # unsafe_allow_html=True : sans échappement, un display_id du type
                    # <img src=x onerror=...> s'exécuterait dans le navigateur de la
                    # personne consultant ce rapport. html.escape() sur toutes les
                    # variables injectées dans ce markdown, y compris _ov_vat2 et
                    # _ov_date_str2 par prudence (défense en profondeur, coût nul).
                    _ov_label2 = f"**{html.escape(str(_ov_vat2))}**"
                    # On affiche les ventes du fichier actuel concernées par cet override (si présentes)
                    _ov_sales2 = []
                    if vies_summary and hasattr(vies_summary, "vat_to_display_ids"):
                        _ov_sales2 = vies_summary.vat_to_display_ids.get(_ov_vat2, [])
                    if _ov_sales2:
                        _ov_sales_str = ", ".join(html.escape(str(s)) for s in _ov_sales2[:3])
                        if len(_ov_sales2) > 3:
                            _ov_sales_str += f" +{len(_ov_sales2)-3}"
                        _ov_label2 += _("vies_manual_class_exp_sales", sales=_ov_sales_str)

                    _oc1b.markdown(
                        f"{_ov_label2}  \n<small style='color:grey'>{html.escape(_ov_date_str2)}{_ov_badge2}</small>",
                        unsafe_allow_html=True)
                    # RÔLES (2026-08-25) : un lecteur peut proposer une
                    # classification pour un n° de TVA PAS ENCORE classifié
                    # (bloc render_manual_vies_classification ci-dessus —
                    # ces numéros n'ont par construction aucun override
                    # existant, voir engine.py::compute_all_with_vies qui
                    # retire du calcul "inconclusifs" tout n° déjà couvert
                    # par un override), mais ne peut pas modifier ou
                    # supprimer un override DÉJÀ enregistré — seul
                    # l'administrateur de l'organisation le peut (donnée
                    # partagée par tout le cabinet).
                    _ov_new2 = _oc2b.selectbox(_("vies_manual_class_status"),
                        options=[_("manual_valid"), _("manual_invalid")],
                        index=0 if _ov_valid2 else 1,
                        key=f"edit_override_b_{_ov_vat2}", label_visibility="collapsed",
                        disabled=not ctx.is_admin)
                    if _oc3b.button("💾", key=f"save_override_b_{_ov_vat2}",
                                    help=_("vies_manual_class_exp_save_help"),
                                    disabled=not ctx.is_admin):
                        try:
                            _smo_edit(_vies_scope_id, _ov_vat2, valid=(_ov_new2 == _("manual_valid")),
                                      acting_user_id=ctx.current_user_id)
                            CalcCacheState.invalidate_calc()
                            st.success(f"{_ov_vat2} → {_ov_new2}")
                            st.rerun()
                        except PermissionError as _perm_err2:
                            st.error(str(_perm_err2))
                    if _oc4b.button("🗑️", key=f"del_override_b_{_ov_vat2}",
                                    help=_("vies_manual_class_exp_del_help"),
                                    disabled=not ctx.is_admin):
                        try:
                            _dmo_edit(_vies_scope_id, _ov_vat2, acting_user_id=ctx.current_user_id)
                            CalcCacheState.invalidate_calc()
                            st.success(_("vies_manual_class_exp_del_success", vat=_ov_vat2))
                            st.rerun()
                        except Exception as _del_err2:
                            st.error(f"Erreur : {_del_err2}")

        # Reclassifications VIES
        if vies_summary.reclassifications:
            # Les NIF/identifiants fiscaux nationaux ne sont PAS des n° de TVA
            # rejetés par VIES — ce n'est même pas un numéro qui a été soumis
            # à VIES. On les sépare du tableau "N° TVA rejeté" pour ne pas
            # induire en erreur (cf. is_national_tax_id sur ViesReclassification).
            true_rejections = [r for r in vies_summary.reclassifications if not getattr(r, "is_national_tax_id", False)]
            national_ids = [r for r in vies_summary.reclassifications if getattr(r, "is_national_tax_id", False)]

            avec_delta = [r for r in vies_summary.reclassifications if r.vat_delta > 0]
            dom_rc     = [r for r in vies_summary.reclassifications if getattr(r, "is_domestic_reverse_charge", False)]
            dom_taxe   = [r for r in vies_summary.reclassifications if r.vat_delta <= 0 and not getattr(r, "is_domestic_reverse_charge", False)]
            _is_detailed = st.session_state.get("display_mode") == "detaille"
            
            if _is_detailed:
                st.success(_("vies_success_reclassified", count=len(avec_delta), amount=_fmt(float(vies_summary.fraud_avoided_amount))))
                if dom_rc:
                    st.info(_("vies_info_reverse_charge", count=len(dom_rc)))
                if dom_taxe:
                    st.info(_("vies_info_zero_impact", count=len(dom_taxe)))
                if national_ids:
                    # Ces numéros ne sont PAS des n° de TVA intracommunautaire et
                    # ne sont jamais envoyés à VIES — affichés à part pour ne pas
                    # être confondus avec les vraies vérifications VIES (valid/
                    # invalid/inconclusive) ci-dessus.
                    st.info(_("vies_info_national_id", count=len(national_ids)))

            def _vies_statut(r):
                if getattr(r, "is_domestic_reverse_charge", False): return _("vies_status_reverse_charge")
                elif r.vat_delta <= 0: return _("vies_status_already_taxed")
                return _("vies_status_recovered")

            def _vies_explication(r):
                if getattr(r, "is_domestic_reverse_charge", False):
                    return _("vies_expl_reverse_charge", country=r.buyer_country)
                elif r.vat_delta <= 0: return _("vies_expl_already_taxed")
                elif getattr(r, "taxed_at_departure", False):
                    return _("vies_expl_cross_border_departure", country=getattr(r, "stock_country", ""))
                return _("vies_expl_cross_border_destination", country=r.buyer_country)

            if true_rejections:
                fraud_data = [{_("vies_col_id"): (getattr(r, "display_id", "") or r.sale_id), _("vies_col_rejected_vat"): r.buyer_vat_number,
                    _("vies_col_origin"): country_label(getattr(r, "stock_country", "")),
                    _("vies_col_dest"): country_label(r.buyer_country), _("vies_col_ht"): float(r.amount_ht),
                    _("vies_col_recovered_vat"): float(r.vat_avoided),
                    _("vies_col_status"): _vies_statut(r),
                    _("col_scenario"): getattr(r, "scenario", ""),
                    _("vies_col_expl"): _vies_explication(r)}
                    for r in true_rejections]

                filtre = st.radio(_("vies_filter_label"), [_("vies_filter_all"), _("vies_filter_recovered"), _("vies_filter_reverse_charge"), _("vies_filter_zero_impact")], horizontal=True)
                if filtre == _("vies_filter_recovered"):   display = [d for d in fraud_data if _("vies_status_recovered") in d[_("vies_col_status")]]
                elif filtre == _("vies_filter_reverse_charge"): display = [d for d in fraud_data if _("vies_status_reverse_charge") in d[_("vies_col_status")]]
                elif filtre == _("vies_filter_zero_impact"):      display = [d for d in fraud_data if _("vies_status_already_taxed") in d[_("vies_col_status")]]
                else: display = fraud_data
                
                _fraud_df_full = pd.DataFrame(display)
                _fraud_df_filt = _render_filter_bar(_fraud_df_full, "vies_reclass")
                
                _fraud_cfg = _smart_money_df(_fraud_df_filt,
                    money_cols=[_("vies_col_ht"), _("vies_col_recovered_vat")],
                    note_cols=[_("vies_col_rejected_vat"), _("vies_col_id"), _("col_scenario"), _("vies_col_expl")])
                _gated_preview_table(_fraud_df_filt, _can_export, column_config=_fraud_cfg, total_count=len(_fraud_df_filt),
                                     exclude_safe_cols=[_("vies_col_id"), _("vies_col_dest")])

            if national_ids:
                with st.expander(_("vies_national_id_expander", count=len(national_ids))):
                    _nat_data = [{
                        _("vies_col_id"): (getattr(r, "display_id", "") or r.sale_id),
                        _("vies_col_national_id"): r.buyer_vat_number,
                        _("vies_col_origin"): country_label(getattr(r, "stock_country", "")),
                        _("vies_col_dest"): country_label(r.buyer_country),
                        _("vies_col_ht"): float(r.amount_ht),
                        _("vies_col_recovered_vat"): float(r.vat_avoided),
                        _("vies_col_status"): _vies_statut(r),
                        _("col_scenario"): getattr(r, "scenario", ""),
                        _("vies_col_expl"): _vies_explication(r),
                    } for r in national_ids]
                    _nat_df = pd.DataFrame(_nat_data)
                    _nat_cfg = _smart_money_df(_nat_df,
                        money_cols=[_("vies_col_ht"), _("vies_col_recovered_vat")],
                        note_cols=[_("vies_col_national_id"), _("vies_col_id"), _("col_scenario"), _("vies_col_expl")])
                    # BUGFIX (2026-08-16) : ce tableau des identifiants nationaux NIF
                    # (non soumis à VIES) était affiché en clair via st.dataframe, sans
                    # passer par _gated_preview_table — contrairement au tableau
                    # "N° TVA rejeté" juste au-dessus (_can_export appliqué ligne 356).
                    # Un compte gratuit avait donc accès complet à cette donnée
                    # sensible (identifiant fiscal acheteur) alors que le reste de
                    # l'audit VIES est bridé. Même pattern que partout ailleurs :
                    # quelques lignes visibles, le reste verrouillé tant que la
                    # période n'est pas débloquée.
                    _gated_preview_table(_nat_df, _can_export, column_config=_nat_cfg, total_count=len(_nat_df),
                                         exclude_safe_cols=[_("vies_col_id"), _("vies_col_dest")])

            if avec_delta:
                by_c = {}
                for r in avec_delta:
                    _c_lbl = country_label(r.buyer_country)
                    by_c[_c_lbl] = by_c.get(_c_lbl,0) + float(r.vat_avoided)
                fig_f = go.Figure(go.Bar(x=list(by_c.keys()), y=list(by_c.values()),
                    marker_color="#d62728", text=[f"{v:,.2f}€" for v in by_c.values()], textposition="auto"))
                fig_f.update_layout(title=_("vies_chart_title"), yaxis_title=_("vies_chart_yaxis"), height=280, margin=dict(t=40,b=30))
                st.plotly_chart(fig_f, width="stretch")

            import io as _io, csv as _csv
            buf = _io.StringIO(); w = _csv.writer(buf, delimiter=";")
            w.writerow([_("vies_col_id"), _("vies_col_type"), _("vies_col_rejected_vat"), _("vies_col_origin"), _("vies_col_dest"), _("vies_col_ht"), _("vies_col_recovered_vat"), _("vies_col_status"), _("col_scenario"), _("vies_col_expl")])
            for r in vies_summary.reclassifications:
                is_nif = getattr(r, "is_national_tax_id", False)
                type_lbl = "NIF" if is_nif else "VIES"
                if getattr(r, "is_domestic_reverse_charge", False):
                    statut_csv = _("vies_status_reverse_charge"); expl_csv = _("vies_expl_reverse_charge", country=r.buyer_country)
                elif r.vat_delta <= 0:
                    statut_csv = _("vies_status_already_taxed"); expl_csv = _("vies_expl_already_taxed")
                elif getattr(r, "taxed_at_departure", False):
                    statut_csv = _("vies_status_recovered")
                    expl_csv = _("vies_expl_cross_border_departure", country=getattr(r, "stock_country", ""))
                else:
                    statut_csv = _("vies_status_recovered")
                    expl_csv = _("vies_expl_cross_border_destination", country=r.buyer_country)
                w.writerow([(getattr(r, "display_id", "") or r.sale_id), type_lbl, r.buyer_vat_number, country_label(getattr(r, "stock_country", "")), country_label(r.buyer_country),
                    str(r.amount_ht).replace(".",","), str(r.vat_avoided).replace(".",","),
                    statut_csv, getattr(r, "scenario", ""), expl_csv])
            _gated_download(_("vies_dl_btn"),
                data=("\ufeff"+buf.getvalue()).encode("utf-8"),
                file_name=_( "vies_dl_filename", company=nom_entreprise, period=period_label), mime="text/csv")
        elif vies_summary.total_inconclusive or vies_summary.stale_fallback_count:
            st.info(_("vies_info_no_invalid"))
        else:
            st.success(_("vies_success_all_valid"))
