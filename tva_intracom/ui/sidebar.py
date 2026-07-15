"""Rendu complet de la barre latérale (extrait tel quel de app.py).

Regroupe tous les accordéons de la sidebar :
  - Connexion Amazon SP-API
  - Validation & Devises (toggles toujours actifs)
  - Cache VIES (TTL, stats, purge)
  - Paramètres du fichier (encodage)
  - Catalogue Produits (taux réduits par ASIN)
  - Entreprise & Paramètres (SIREN, IOSS, DDP, seuil OSS, TVA locales)
  - Abonnements & forfaits (Stripe : PAYG, Pro, Cabinet)

Usage dans app.py :

    from tva_intracom.ui.sidebar import render_sidebar

    sb = render_sidebar(_auth_ctx)
    # sb.file_format, sb.enable_vies, sb.convert_fx, sb.encoding,
    # sb.asin_to_category, sb.ioss_number, sb.seller_is_importer,
    # sb.apply_fr_under_threshold, sb.countries_with_vat,
    # sb.nom_entreprise, sb.siren_entreprise, sb.tva_fr,
    # sb.local_vat_numbers, sb.oss_period, sb.on_invalid_behavior
"""

from __future__ import annotations

import json
import secrets
import time
from dataclasses import dataclass, field
from typing import Any

import pandas as pd
import streamlit as st
from tva_intracom.i18n import _

from tva_intracom import auth as tva_auth
from tva_intracom import billing as tva_billing
from tva_intracom.rates import EU_COUNTRIES, COUNTRY_NAMES, COUNTRY_CURRENCIES, CURRENCY_SYMBOLS, oss_threshold_in_currency
from tva_intracom.vies_engine import (
    get_cache_stats as vies_cache_stats,
    purge_expired_cache,
    set_cache_ttl,
)
from tva_intracom.ui.theme import _PLATFORM_OPTIONS


@dataclass
class SidebarResult:
    """Toutes les valeurs produites par la sidebar, consommées ensuite par
    app.py (calcul, exports, gating billing)."""

    file_format: str
    enable_vies: bool
    on_invalid_behavior: str
    convert_fx: bool
    encoding: str
    asin_to_category: dict[str, str]
    ioss_number: str
    seller_is_importer: bool
    apply_fr_under_threshold: bool
    countries_with_vat: list[str]
    nom_entreprise: str
    siren_entreprise: str
    tva_fr: str
    local_vat_numbers: dict[str, str] = field(default_factory=dict)
    oss_period: str = "__auto__"
    siren_quota_status: Any = None
    home_country: str = "FR"
    display_currency: str = "DEFAULT"


def _oss_limit_label(home_country: str) -> str:
    """Libellé du seuil OSS (10 000 EUR) dans la devise du pays d'origine —
    contre-valeur nationale FIXE si publiée (rates.OSS_THRESHOLD_FIXED_EQUIVALENTS),
    sinon '10 000 EUR' tel quel (pas de conversion au taux du jour ici : ce
    libellé sert uniquement d'étiquette d'option, pas d'affichage financier
    précis)."""
    _cur = COUNTRY_CURRENCIES.get((home_country or "FR").upper(), "EUR")
    _sym = CURRENCY_SYMBOLS.get(_cur, "€")
    _val = oss_threshold_in_currency(_cur)
    return f"{_val:,.0f} {_sym}".replace(",", " ")


def render_sidebar(auth_ctx) -> SidebarResult:
    """Affiche la sidebar complète et retourne les paramètres résolus.

    Args:
        auth_ctx: AuthContext (voir tva_intracom.ui.auth_flow) — fournit
                  current_user, vies_scope_id, stripe_success_url/cancel_url.
    """
    _current_user = auth_ctx.current_user
    _vies_scope_id = auth_ctx.vies_scope_id
    _stripe_success_url = auth_ctx.stripe_success_url
    _stripe_cancel_url = auth_ctx.stripe_cancel_url

    with st.sidebar:
        st.header(_("options_header"))

        # ── Pays d'origine (établissement du vendeur) ──────────────────
        # Réglage GLOBAL au compte (pas par SIREN) — conditionne la
        # classification domestique/locale du moteur fiscal (engine.py,
        # sale.seller_country) et l'ordre d'affichage des déclarations
        # (déclaration du pays d'origine en premier, reste en "local").
        # Persisté en base (tva_users.home_country), voir auth.py.
        _home_countries = sorted(EU_COUNTRIES)
        _current_home = getattr(_current_user, "home_country", "FR") or "FR"
        try:
            _home_index = _home_countries.index(_current_home)
        except ValueError:
            _home_index = _home_countries.index("FR") if "FR" in _home_countries else 0
        home_country = st.selectbox(
            _("home_country_label"),
            options=_home_countries,
            index=_home_index,
            format_func=lambda c: f"{COUNTRY_NAMES.get(c, c)} ({c})",
            key="home_country_select",
            help=_("home_country_help"),
        )
        if home_country != _current_home:
            tva_auth.set_home_country(_current_user.id, home_country)
            _current_user.home_country = home_country
            st.rerun()

        # ── Devise d'affichage ──────────────────────────────────────────
        # Indépendante du pays d'origine : par défaut, l'affichage utilise la
        # devise du pays d'origine choisi ci-dessus (FR -> EUR, PL -> PLN...),
        # mais l'utilisateur peut choisir n'importe quelle devise UE (+ GBP)
        # pour la présentation, sans que cela n'affecte la classification
        # fiscale ni les déclarations légales (toujours en EUR, voir README
        # section "Devise d'affichage locale"). Persisté en base
        # (tva_users.display_currency), comme `home_country`.
        _currency_options = ["DEFAULT"] + sorted(set(COUNTRY_CURRENCIES.values()))
        _current_display_choice = getattr(_current_user, "display_currency", "DEFAULT") or "DEFAULT"
        try:
            _cur_idx = _currency_options.index(_current_display_choice)
        except ValueError:
            _cur_idx = 0

        def _currency_option_label(code: str, _home=home_country) -> str:
            if code == "DEFAULT":
                _home_cur = COUNTRY_CURRENCIES.get((_home or "FR").upper(), "EUR")
                return _("display_currency_default_label", currency=_home_cur)
            return f"{code} ({CURRENCY_SYMBOLS.get(code, code)})"

        display_currency = st.selectbox(
            _("display_currency_label"),
            options=_currency_options,
            index=_cur_idx,
            format_func=_currency_option_label,
            key="display_currency_select",
            help=_("display_currency_help"),
        )
        if display_currency != _current_display_choice:
            tva_auth.set_display_currency(_current_user.id, display_currency)
            _current_user.display_currency = display_currency
        st.session_state["display_currency_choice"] = display_currency

        # Rappel pour le thème si l'utilisateur ne le trouve plus
        st.caption(_("theme_caption"))
        file_format = st.radio(_("platform_source"), _PLATFORM_OPTIONS, index=0)

        # ── Connexion Amazon SP-API ───────────────────────────────────────────────
        with st.expander(_("amazon_conn_header"), expanded=False):
            _amz_creds = tva_auth.get_amazon_credentials(_current_user.id)
            if _amz_creds:
                st.success(_("amazon_connected", id=_amz_creds['selling_partner_id']))
                if st.button(_("amazon_disconnect_btn"), key="btn_disconnect_amazon"):
                    tva_auth.delete_amazon_credentials(_current_user.id)
                    st.rerun()
            else:
                st.info(_("amazon_info_auth"))
                # On génère un 'state' pour sécuriser l'OAuth (optionnel mais recommandé)
                _state = secrets.token_hex(8)
                from tva_intracom import amazon_spapi
                try:
                    _auth_url = amazon_spapi.get_authorization_url(state=_state)
                    st.link_button(_("amazon_connect_btn"), _auth_url)
                except Exception as _err:
                    st.error(_("amazon_config_err", error=_err))

        # ── Validation & Devises ──────────────────────────────────────────────────
        with st.expander(_("validation_devise_header"), expanded=False):
            # Fonctions toujours actives sur ce compte — cases grisées et
            # verrouillées (disabled=True) pour informer l'utilisateur qu'elles
            # sont bien activées, sans lui laisser la possibilité de les désactiver.
            st.checkbox(_("vies_checkbox"), value=True, disabled=True,
                help=_("vies_help"))
            enable_vies = True
            on_invalid_behavior = "reclassify"
            st.checkbox(_("fx_checkbox"), value=True, disabled=True,
                help=_("fx_help"))
            convert_fx = True

        # ── Cache VIES ────────────────────────────────────────────────────────────
        with st.expander(_("cache_vies_header"), expanded=False):
            try:
                _cs = vies_cache_stats(_vies_scope_id)
                _ttl_days = st.slider(_("ttl_cache_slider"), min_value=7, max_value=365,
                    value=_cs["ttl_days"], step=7,
                    help=_("ttl_cache_help"))
                if _ttl_days != _cs["ttl_days"]:
                    set_cache_ttl(_ttl_days)
                    st.rerun()
                _c1, _c2, _c3 = st.columns(3)
                _c1.metric(_("total"), _cs["total"])
                _c2.metric(_("fresh"), _cs["fresh"])
                _c3.metric(_("expired"), _cs["expired"])
                if _cs["total"] > 0:
                    st.caption(
                        f"{_('valid')} : {_cs['valid']} · {_('invalid')} : {_cs['invalid']} · "
                        f"{_('oldest_check')} : {(_cs['oldest_check'] or '—')[:10]}")
                if _cs.get("manual_total", 0) > 0:
                    st.markdown(f"**{_('manual_classifications')}**")
                    _m1, _m2 = st.columns(2)
                    _m1.metric(_("manual_valid"), _cs["manual_valid"])
                    _m2.metric(_("manual_invalid"), _cs["manual_invalid"])
                if _cs["expired"] > 0:
                    if st.button(_("purge_expired_btn", count=_cs['expired']), key="purge_vies_cache"):
                        n = purge_expired_cache(_vies_scope_id)
                        st.success(_("purge_success", count=n))
                        st.rerun()
            except Exception as _e:
                st.caption(_("cache_unavailable", error=_e))

        # ── Paramètres du fichier ─────────────────────────────────────────────────
        with st.expander(_("file_params_header"), expanded=False):
            encoding = st.selectbox(_("file_encoding"), ["utf-8","latin-1","cp1252"], index=0)

        # ── Catalogue Produits ────────────────────────────────────────────────────
        with st.expander(_("catalog_header"), expanded=False):
            catalog_file = st.file_uploader(_("catalog_upload"),
                type=["csv","tsv","txt","xlsx"],
                help=_("catalog_help"))
            asin_to_category = {}
            if catalog_file is not None:
                try:
                    if catalog_file.name.endswith(".xlsx"):
                        df_cat = pd.read_excel(catalog_file)
                    elif catalog_file.name.endswith(".csv"):
                        df_cat = pd.read_csv(catalog_file)
                    else:
                        df_cat = pd.read_csv(catalog_file, sep="\t")
                    df_cat.columns = [c.strip().upper() for c in df_cat.columns]
                    asin_col = next((c for c in df_cat.columns if "ASIN" in c), None)
                    cat_col  = next((c for c in df_cat.columns if "PRODUCT-TAX-CODE" in c or "TAX-CODE" in c), None)
                    if not cat_col:
                        cat_col = next((c for c in df_cat.columns if any(k in c for k in ["TAX","GROUP","CODE","TYPE"])), None)
                    if asin_col and cat_col:
                        asin_to_category = {str(a).strip().upper(): str(c).strip().upper()
                            for a, c in zip(df_cat[asin_col], df_cat[cat_col]) if pd.notna(a) and pd.notna(c)}
                        st.success(_("catalog_success", count=len(asin_to_category)))
                except Exception as e:
                    st.error(_("catalog_error", error=e))

        # ── Entreprise & Paramètres ───────────────────────────────────────────────
        # Ces paramètres sont liés au SIREN sélectionné et sauvegardés en base.
        ioss_number = ""
        seller_is_importer = False
        apply_fr_under_threshold = False
        countries_with_vat = ["FR"]
        nom_entreprise = ""
        siren_entreprise = ""
        tva_fr = ""
        local_vat_numbers: dict[str, str] = {}

        with st.expander(_("company_header"), expanded=False):
            st.markdown(f"**{_('fiscal_period_title')}**")
            st.caption(_("fiscal_period_caption"))

            # ── Affichage de la période auto-détectée (depuis session_state) ──
            _sidebar_results = st.session_state.get("_results", [])
            if _sidebar_results:
                _sd = sorted(
                    r.sale.transaction_date for r in _sidebar_results
                    if r.sale.transaction_date and len(r.sale.transaction_date) >= 7
                )
                if _sd:
                    from datetime import datetime as _dt2
                    _sd_min = _dt2.fromisoformat(_sd[0][:10])
                    _sd_max = _dt2.fromisoformat(_sd[-1][:10])
                    _sy, _sm = _sd_min.year, _sd_min.month
                    _ey, _em = _sd_max.year, _sd_max.month
                    if _sy != _ey:
                        _detected = f"{_sy}-{_ey}"
                    elif _sm == _em:
                        _detected = f"{_sy}-{_sm:02d}"
                    elif _sm == 1 and _em == 12:
                        _detected = str(_sy)
                    elif _sm == 1 and _em == 6:
                        _detected = f"{_sy}-S1"
                    elif _sm == 7 and _em == 12:
                        _detected = f"{_sy}-S2"
                    else:
                        _qmin = (_sm - 1) // 3 + 1
                        _qmax = (_em - 1) // 3 + 1
                        _detected = f"{_sy}-Q{_qmin}" if _qmin == _qmax else f"{_sy}-Q{_qmin}_Q{_qmax}"
                    st.markdown(
                        _("fiscal_period_detected", period=_detected, start=_sd[0][:10], end=_sd[-1][:10]),
                        unsafe_allow_html=True,
                    )
            elif not _sidebar_results:
                st.caption(_("fiscal_period_none"))

            oss_period = "__auto__"

            st.divider()
            st.markdown(f"**{_('identity_vat_params_title')}**")
            try:
                _registered_sirens = tva_billing.list_registered_sirens(_current_user.id)
                _siren_quota_status = tva_billing.get_siren_quota_status(_current_user.id)
            except Exception as _siren_list_err:
                _registered_sirens = []
                _siren_quota_status = None
                st.caption(_("siren_list_unavailable", error=_siren_list_err))

            _siren_over_quota = bool(_siren_quota_status and _siren_quota_status.blocked)
            if _siren_over_quota:
                st.error(_("siren_quota_blocked", count=_siren_quota_status.registered_count, quota=_siren_quota_status.quota, over=_siren_quota_status.over_quota_by))

            _siren_options = [r["siren"] for r in _registered_sirens]
            _new_siren_label = _("new_siren_option")
            _siren_label_by_value = {
                r["siren"]: f"{r['company_name'] or _('no_name')} — {r['siren']}"
                for r in _registered_sirens
            }
            _siren_label_by_value[_new_siren_label] = _new_siren_label
            _siren_choice = st.selectbox(
                _("siren_client_label"),
                options=_siren_options + [_new_siren_label],
                index=0 if _siren_options else 0,
                format_func=lambda v: _siren_label_by_value.get(v, v),
                key="siren_select_box",
            ) if _siren_options else _new_siren_label

            if _siren_choice == _new_siren_label:
                _can_add_siren, _siren_quota_msg = (True, "")
                try:
                    _can_add_siren, _siren_quota_msg = tva_billing.can_register_new_siren(_current_user.id)
                except Exception as _quota_err:
                    _can_add_siren, _siren_quota_msg = True, ""
                    st.caption(_("quota_check_unavailable", error=_quota_err))

                if not _can_add_siren:
                    st.error(f"🔒 {_siren_quota_msg}")
                    nom_entreprise = _registered_sirens[0]["company_name"] if _registered_sirens else ""
                    siren_entreprise = _registered_sirens[0]["siren"] if _registered_sirens else ""
                    tva_fr = _registered_sirens[0]["tva_number"] if _registered_sirens else ""
                    ioss_number = _registered_sirens[0].get("ioss_number") or ""
                    seller_is_importer = _registered_sirens[0].get("seller_is_importer") or False
                    apply_fr_under_threshold = _registered_sirens[0].get("apply_fr_under_threshold") or False
                    _countries_raw = _registered_sirens[0].get("countries_with_vat") or "FR"
                    countries_with_vat = [c.strip().upper() for c in _countries_raw.split(",") if c.strip()]
                    try:
                        local_vat_numbers = json.loads(_registered_sirens[0].get("vat_numbers_json") or "{}")
                    except Exception:
                        local_vat_numbers = {}
                else:
                    nom_entreprise   = st.text_input(_("company_name_label"), _("default_company_name"), key="nom_new")
                    siren_entreprise = st.text_input(_("siren_number_label"), "123456789", key="siren_new")

                    st.markdown("---")
                    ioss_number = st.text_input(_("ioss_number_label"), placeholder="ex: IM1234567890", key="ioss_new",
                        help=_("ioss_help"))
                    seller_is_importer = st.toggle(_("ddp_label"), value=False, key="ddp_new")
                    apply_fr_under_threshold = st.toggle(_("oss_threshold_apply_label", limit=_oss_limit_label(home_country)), value=False, key="oss_thr_new")
                    countries_with_vat = st.multiselect(_("local_vat_countries_label"),
                        options=sorted(list(EU_COUNTRIES)), default=["FR"], key="vat_countries_new")

                    local_vat_numbers = {}
                    _missing_vat_input = False
                    if countries_with_vat:
                        st.caption(_("local_vat_numbers_caption"))
                        for ccode in sorted(countries_with_vat):
                            _v = st.text_input(_("vat_number_for", country=ccode), key=f"vat_num_new_{ccode}",
                                               placeholder=f"ex: {ccode}123456789")
                            local_vat_numbers[ccode] = _v.strip()
                            if not _v.strip():
                                _missing_vat_input = True

                    tva_fr = local_vat_numbers.get("FR", "")

                    if st.button(_("save_siren_btn"), key="btn_register_siren"):
                        if not siren_entreprise.strip():
                            st.warning(_("siren_required"))
                        elif siren_entreprise.strip() in _siren_options:
                            st.error(_("siren_already_registered", siren=siren_entreprise.strip()))
                        elif _missing_vat_input:
                            st.warning(_("missing_vat_numbers"))
                        else:
                            try:
                                tva_billing.register_siren(
                                    _current_user.id, siren_entreprise.strip(),
                                    nom_entreprise.strip(), tva_fr.strip(),
                                    ioss_number=ioss_number.strip(),
                                    seller_is_importer=seller_is_importer,
                                    apply_fr_under_threshold=apply_fr_under_threshold,
                                    countries_with_vat=",".join(countries_with_vat),
                                    vat_numbers_json=json.dumps(local_vat_numbers)
                                )
                                st.success(_("siren_save_success"))
                                st.rerun()
                            except Exception as _reg_err:
                                st.error(_("siren_save_error", error=_reg_err))
            else:
                _match = next((r for r in _registered_sirens if r["siren"] == _siren_choice), None)
                nom_entreprise   = _match["company_name"] if _match else ""
                siren_entreprise = _match["siren"] if _match else ""

                # Affichage de l'identité (fixe)
                st.markdown(f"🏢 **{nom_entreprise}**")
                st.caption(f"{_('siren_label')} : **{siren_entreprise}**")

                try:
                    _existing_vats = json.loads(_match.get("vat_numbers_json") or "{}") if _match else {}
                except Exception:
                    _existing_vats = {}

                _tva_fr_fixed = _existing_vats.get("FR") or _match.get("tva_number") or ""
                if _tva_fr_fixed:
                    st.caption(f"{_('tva_fr_label')} : **{_tva_fr_fixed}**")

                st.markdown("---")
                st.markdown(f"**{_('fiscal_params_title')}**")

                # Option pour déverrouiller la modification des numéros déjà enregistrés
                allow_edit_ids = st.checkbox(_("edit_ids_checkbox"), value=False, help=_("edit_ids_help"))

                # IOSS
                _ioss_val = _match.get("ioss_number") or ""
                if _ioss_val and not allow_edit_ids:
                    st.caption(f"IOSS : **{_ioss_val}**")
                    ioss_number = _ioss_val
                else:
                    ioss_number = st.text_input(_("ioss_number_label"),
                        value=_ioss_val,
                        placeholder="ex: IM1234567890", key="ioss_edit")

                seller_is_importer = st.toggle(_("ddp_label"), value=_match.get("seller_is_importer") or False if _match else False, key="ddp_edit")
                apply_fr_under_threshold = st.toggle(_("oss_threshold_apply_label", limit=_oss_limit_label(home_country)), value=_match.get("apply_fr_under_threshold") or False if _match else False, key="oss_thr_edit")

                _countries_raw = _match.get("countries_with_vat") or "FR" if _match else "FR"
                _default_vat_countries = [c.strip().upper() for c in _countries_raw.split(",") if c.strip()]

                countries_with_vat = st.multiselect(_("local_vat_countries_label"),
                    options=sorted(list(EU_COUNTRIES)), default=_default_vat_countries, key="vat_countries_edit")

                local_vat_numbers = {}
                _missing_vat_input = False
                if countries_with_vat:
                    st.caption(_("local_vat_numbers_caption"))
                    for ccode in sorted(countries_with_vat):
                        _val = _existing_vats.get(ccode, "")
                        if _val and not allow_edit_ids:
                            st.caption(f"✅ {ccode} : **{_val}**")
                            local_vat_numbers[ccode] = _val
                        else:
                            _v = st.text_input(_("vat_number_for", country=ccode),
                                               value=_val,
                                               key=f"vat_num_edit_{ccode}",
                                               placeholder=f"ex: {ccode}123456789")
                            local_vat_numbers[ccode] = _v.strip()
                            if not _v.strip():
                                _missing_vat_input = True

                # Mise à jour de tva_fr pour le XML OSS (toujours basé sur le numéro FR)
                tva_fr = local_vat_numbers.get("FR", _tva_fr_fixed)

                if st.button(_("save_changes_btn"), key="btn_update_siren"):
                    if _missing_vat_input:
                        st.warning(_("missing_vat_numbers"))
                    else:
                        try:
                            tva_billing.register_siren(
                                _current_user.id, siren_entreprise.strip(),
                                nom_entreprise.strip(), tva_fr.strip(),
                                ioss_number=ioss_number.strip(),
                                seller_is_importer=seller_is_importer,
                                apply_fr_under_threshold=apply_fr_under_threshold,
                                countries_with_vat=",".join(countries_with_vat),
                                vat_numbers_json=json.dumps(local_vat_numbers)
                            )
                            st.success(_("update_success"))
                            st.rerun()
                        except Exception as _reg_err:
                            st.error(_("update_error", error=_reg_err))

                # Option de retrait du SIREN (toujours visible si déjà enregistré)
                if _match:
                    st.divider()
                    if _match.get("pending_removal_at"):
                        import datetime as _dt
                        _eff_date = _dt.datetime.fromtimestamp(_match["pending_removal_at"]).strftime("%d/%m/%Y")
                        st.warning(_("removal_pending", date=_eff_date))
                        if st.button(_("cancel_removal_btn"), key=f"btn_cancel_removal_{siren_entreprise}", use_container_width=True):
                            tva_billing.cancel_siren_removal(_current_user.id, siren_entreprise)
                            st.rerun()
                    else:
                        if st.button(_("remove_siren_btn"), key=f"btn_remove_entreprise_{siren_entreprise}",
                                    help=_("remove_siren_help"),
                                    use_container_width=True):
                            # On autorise le retrait même si c'est le dernier (l'utilisateur peut vouloir arrêter)
                            _eff = tva_billing.request_siren_removal(_current_user.id, siren_entreprise)
                            import datetime as _dt
                            if _eff <= time.time() + 5:
                                st.success(_("remove_success"))
                            else:
                                st.info(_("remove_scheduled", date=_dt.datetime.fromtimestamp(_eff).strftime('%d/%m/%Y')))
                            st.rerun()

        # ── Abonnements & forfaits ────────────────────────────────────────────────
        with st.expander(_("billing_header"), expanded=True):
            _sub_status = None
            try:
                _sub_status = tva_billing.get_subscription_status(_current_user.id)
            except Exception as _sub_err:
                st.caption(_("sub_status_unavailable", error=_sub_err))

            _plan_label = {"business": _("plan_pro"), "cabinet": _("plan_cabinet")}.get(
                _sub_status.plan if _sub_status else None, _sub_status.plan if _sub_status else "—")
            _interval_label = {"month": _("interval_monthly"), "year": _("interval_yearly")}.get(
                _sub_status.billing_interval if _sub_status else None, "")

            if _sub_status and _sub_status.active:
                st.success(_("sub_active_msg", plan=_plan_label, interval=_interval_label)
                    + (f" — {_sub_status.siren_quantity} SIREN" if _sub_status.plan == "cabinet" else ""))

                # Gestion des SIREN pour un abonnement Cabinet (ajout via la section
                # Entreprise, retrait différé ici, effectif à la date anniversaire).
                if _sub_status.plan == "cabinet" and _registered_sirens:
                    st.markdown(f"**{_('sirens_managed_title')}**")
                    for _r in _registered_sirens:
                        _c1, _c2 = st.columns([2, 1])
                        _label = f"{_r['company_name'] or _('no_name')} — {_r['siren']}"
                        if _r.get("pending_removal_at"):
                            _c1.caption(f"{_label} · {_('removal_scheduled_short')}")
                        else:
                            _c1.caption(_label)
                            if _c2.button(_("remove_btn"), key=f"btn_remove_{_r['siren']}", use_container_width=True):
                                _eff = tva_billing.request_siren_removal(_current_user.id, _r["siren"])
                                import datetime as _dt
                                st.info(_("remove_scheduled", date=_dt.datetime.fromtimestamp(_eff).strftime('%d/%m/%Y')))
                                st.rerun()

                try:
                    _portal_url = tva_billing.create_billing_portal_session(
                        _current_user.id,
                        return_url=_stripe_cancel_url(),
                    )
                    st.link_button(_("manage_sub_stripe_btn"), _portal_url)
                except Exception:
                    pass

            # ── Crédits PAYG (Achats uniques) ─────────────────────────────────────
            try:
                _credits = tva_billing.list_purchased_credits(_current_user.id)
                if _credits:
                    st.markdown("---")
                    st.markdown(f"**{_('unlocked_periods_title')}**")
                    for _c in _credits:
                        from datetime import datetime as _dt
                        _at = _dt.fromtimestamp(_c["at"]).strftime("%d/%m/%Y")
                        st.caption(f"✅ **{_c['period']}** — {_('purchased_at', date=_at)}")
            except Exception as _credit_err:
                st.caption(_("purchase_history_unavailable", error=_credit_err))
            else:
                if _sub_status and _sub_status.status:
                    # Abonnement existant mais inactif (annulé/expiré) : état actuel
                    # affiché pour information, sans historique complet.
                    st.warning(_("last_sub_msg", plan=_plan_label, status=_sub_status.status)
                        + (f" ({_('expired_at', date=__import__('datetime').datetime.fromtimestamp(_sub_status.current_period_end).strftime('%d/%m/%Y'))})"
                           if _sub_status.current_period_end else ""))

                st.caption(_("billing_caption"))

                with st.expander(_("pricing_grid_expander"), expanded=False):
                    try:
                        _grid = tva_billing.get_pricing_grid(_current_user.id)
                    except Exception as _grid_err:
                        _grid = None
                        st.caption(_("pricing_grid_unavailable", error=_grid_err))

                    if _grid:
                        try:
                            _promotions = tva_billing.list_available_promotions(_current_user.id)
                        except Exception as _promo_list_err:
                            _promotions = []
                            st.error(_("promo_codes_unavailable", error=_promo_list_err))

                        if _promotions:
                            st.markdown(f"**{_('available_promo_codes_title')}**")
                            for _promo_item in _promotions:
                                if _promo_item.get("percent_off") is not None:
                                    _reduc = f"{_promo_item['percent_off']:g}%"
                                elif _promo_item.get("amount_off") is not None:
                                    _reduc = f"{_promo_item['amount_off']:.2f} {(_promo_item.get('currency') or 'eur').upper()}"
                                else:
                                    _reduc = "—"

                                _conditions = []
                                if _promo_item.get("first_time_only"):
                                    _conditions.append(_("promo_first_time"))
                                if _promo_item.get("minimum_amount") is not None:
                                    _conditions.append(
                                        _("promo_min_amount", amount=_promo_item['minimum_amount'], currency=(_promo_item.get('minimum_amount_currency') or 'eur').upper())
                                    )
                                if _promo_item.get("stock_remaining") is not None:
                                    _conditions.append(_("promo_stock_remaining", count=_promo_item['stock_remaining']))
                                if _promo_item.get("expires_at"):
                                    import datetime as _dt
                                    _conditions.append(
                                        _("promo_expires_at", date=_dt.datetime.fromtimestamp(_promo_item["expires_at"]).strftime("%d/%m/%Y"))
                                    )
                                _conditions_txt = " · ".join(_conditions) if _conditions else _("promo_no_conditions")

                                _eligible = _promo_item.get("eligible")
                                if _eligible is True:
                                    st.success(f"✅ **{_promo_item['code']}** — {_reduc} — {_conditions_txt}")
                                elif _eligible is False:
                                    _reasons_txt = ", ".join(_promo_item.get("ineligible_reasons", []))
                                    st.warning(_("promo_ineligible_msg", code=_promo_item['code'], reduc=_reduc, conditions=_conditions_txt, reasons=_reasons_txt))
                                else:
                                    st.markdown(f"- **{_promo_item['code']}** — {_reduc} — {_conditions_txt}")

                        if _grid.get("payg"):
                            _p = _grid["payg"]
                            _payg_label = _p.get("name") or _("payg_label_default")
                            if _p.get("discounted_amount") is not None:
                                st.markdown(
                                    f"**{_payg_label}** — "
                                    f"<span style='text-decoration:line-through;color:gray'>{_p['amount']:.2f} {_p['currency'].upper()}</span> "
                                    f"&nbsp;→&nbsp; <span style='color:#2ca02c;font-weight:bold'>{_p['discounted_amount']:.2f} {_p['currency'].upper()}</span> "
                                    f"({_p['discount_label']}, code {_p['discount_code']}) / {_('per_declaration')}",
                                    unsafe_allow_html=True,
                                )
                            else:
                                st.markdown(f"**{_payg_label}** — {_p['amount']:.2f} "
                                    f"{_p['currency'].upper()} / {_('per_declaration')}")

                        if _grid.get("business"):
                            _biz_lines = []
                            _biz_label = None
                            for _iv, _lbl in (("month", _("per_month")), ("year", _("per_year"))):
                                _b = _grid["business"].get(_iv)
                                if _b and _b["amount"] is not None:
                                    if _biz_label is None:
                                        _biz_label = _b.get("name") or _("plan_pro")
                                    if _b.get("discounted_amount") is not None:
                                        _biz_lines.append(
                                            f"<span style='text-decoration:line-through;color:gray'>{_b['amount']:.2f} {_b['currency'].upper()}</span> "
                                            f"→ <span style='color:#2ca02c;font-weight:bold'>{_b['discounted_amount']:.2f} {_b['currency'].upper()}</span> "
                                            f"({_b['discount_label']}, code {_b['discount_code']}) / {_lbl}"
                                        )
                                    else:
                                        _biz_lines.append(f"{_b['amount']:.2f} {_b['currency'].upper()} / {_lbl}")
                            if _biz_lines:
                                st.markdown(f"**{_biz_label}** (1 SIREN) — " + " · ".join(_biz_lines), unsafe_allow_html=True)

                        if _grid.get("cabinet"):
                            st.markdown("""
                                <style>
                                .cabinet-table { width: 100%; border-collapse: collapse; margin-bottom: 1.5rem; }
                                .cabinet-table th { text-align: left; padding: 8px; border-bottom: 2px solid rgba(250, 250, 250, 0.2); background-color: rgba(250, 250, 250, 0.05); }
                                .cabinet-table td { padding: 8px; border-bottom: 1px solid rgba(250, 250, 250, 0.1); }
                                </style>
                            """, unsafe_allow_html=True)
                            for _iv, _lbl in (("month", _("billing_monthly")), ("year", _("billing_yearly"))):
                                _c = _grid["cabinet"].get(_iv)
                                if not _c or not _c.get("tiers"):
                                    continue
                                _cab_label = _c.get("name") or _("plan_cabinet")
                                st.markdown(f"**{_cab_label} — {_lbl}** ({_('min_3_sirens')})")
                                _rows = []
                                _prev_bound = 0
                                for _t in _c["tiers"]:
                                    _up_to = _t["up_to"]
                                    _range = f"{_prev_bound + 1} – {_up_to}" if _up_to is not None else f"{_prev_bound + 1}+"
                                    if _t["unit_amount"] is not None:
                                        if _t.get("discounted_unit_amount") is not None:
                                            _price_txt = (
                                                f"<span style='text-decoration:line-through;color:gray'>{_t['unit_amount']:.2f} {_c['currency'].upper()}</span> "
                                                f"→ <span style='color:#2ca02c;font-weight:bold'>{_t['discounted_unit_amount']:.2f} {_c['currency'].upper()}</span> "
                                                f"({_t['discount_label']}, code {_t['discount_code']}) / {_('siren_label')}"
                                            )
                                        else:
                                            _price_txt = f"{_t['unit_amount']:.2f} {_c['currency'].upper()} / {_('siren_label')}"
                                    else:
                                        _price_txt = "—"
                                    if _t.get("flat_amount") is not None:
                                        _price_txt += f" (+ {_t['flat_amount']:.2f} {_c['currency'].upper()} {_('fixed_amount')})"
                                    _rows.append({_("col_managed_sirens"): _range, _("col_price"): _price_txt})
                                    _prev_bound = _up_to if _up_to is not None else _prev_bound
                                # st.dataframe n'interprète pas le HTML (barré/couleur). On utilise st.markdown
                                # avec l'export HTML du DataFrame pour conserver le formattage.
                                st.markdown(
                                    pd.DataFrame(_rows).to_html(escape=False, index=False, classes="cabinet-table"),
                                    unsafe_allow_html=True
                                )

                if not (_sub_status and _sub_status.active):
                    _detected_period_for_payg = st.session_state.get("_period_label", "")
                    st.markdown(f"**{_('payg_title')}** — {_('payg_subtitle')}")
                    if not _detected_period_for_payg:
                        st.caption(_("payg_no_period_warning"))
                    else:
                        st.caption(_("payg_detected_period_msg", period=_detected_period_for_payg))
                        if st.button(_("payg_buy_btn"), key="btn_payg_sidebar"):
                            try:
                                _payg_cache_key = f"_stripe_checkout_url::{_detected_period_for_payg}"
                                if _payg_cache_key not in st.session_state:
                                    st.session_state[_payg_cache_key] = tva_billing.create_payg_checkout_session(
                                        user_id=_current_user.id, email=_current_user.email,
                                        period_label=_detected_period_for_payg,
                                        success_url=_stripe_success_url("export_ok=1"),
                                        cancel_url=_stripe_cancel_url(),
                                    )
                                st.link_button(_("continue_to_payment_btn"), st.session_state[_payg_cache_key])
                            except Exception as _payg_err:
                                st.session_state.pop(_payg_cache_key, None)
                                st.error(f"Erreur : {_payg_err}")

                    _sub_interval = st.radio(_("billing_interval_label"), [_("billing_monthly_choice"), _("billing_yearly_choice")],
                        horizontal=True, key="sub_interval_choice")
                    _interval_code = "month" if _sub_interval == _("billing_monthly_choice") else "year"

                    st.markdown(f"**{_('plan_pro')}** — {_('plan_pro_desc')}")
                    if st.button(_("subscribe_pro_btn"), key="btn_sub_business"):
                        try:
                            _url = tva_billing.create_subscription_checkout_session(
                                user_id=_current_user.id, email=_current_user.email,
                                plan="business", interval=_interval_code,
                                success_url=_stripe_success_url("export_ok=1"),
                                cancel_url=_stripe_cancel_url(),
                            )
                            st.link_button(_("continue_to_payment_btn"), _url)
                        except Exception as _biz_err:
                            st.error(f"Erreur : {_biz_err}")

                    st.markdown(f"**{_('plan_cabinet')}** — {_('plan_cabinet_desc')}")
                    _cabinet_qty = st.number_input(_("managed_sirens_qty_label"), min_value=3, max_value=500,
                        value=max(3, _siren_quota_status.registered_count if _siren_quota_status else 3), step=1,
                        key="cabinet_siren_qty",
                        help=_("managed_sirens_qty_help"))
                    if st.button(_("subscribe_cabinet_btn"), key="btn_sub_cabinet"):
                        try:
                            _url = tva_billing.create_subscription_checkout_session(
                                user_id=_current_user.id, email=_current_user.email,
                                plan="cabinet", interval=_interval_code,
                                quantity=int(_cabinet_qty),
                                success_url=_stripe_success_url("export_ok=1"),
                                cancel_url=_stripe_cancel_url(),
                            )
                            st.link_button(_("continue_to_payment_btn"), _url)
                        except Exception as _cab_err:
                            st.error(f"Erreur : {_cab_err}")

        # ── Compte & Confidentialité ──────────────────────────────────────────────
        with st.expander(_("account_privacy_header"), expanded=False):
            st.markdown(f"**{_('data_portability_title')}**")
            st.caption(_("data_portability_help"))
            
            if st.button(_("export_data_btn"), key="btn_export_user_data"):
                try:
                    data = tva_auth.export_all_user_data(_current_user.id)
                    json_str = json.dumps(data, indent=2, ensure_ascii=False)
                    st.download_button(
                        label=_("download_export_btn"),
                        data=json_str,
                        file_name=f"export_donnees_tva_{_current_user.id}.json",
                        mime="application/json",
                    )
                except Exception as _exp_err:
                    st.error(f"Erreur lors de l'export : {_exp_err}")
            
            st.divider()
            st.markdown(f"**{_('delete_account_title')}**")
            st.warning(_("delete_account_warning"))
            
            # Double confirmation pour la suppression
            if "confirm_delete_account" not in st.session_state:
                st.session_state["confirm_delete_account"] = False
            
            if not st.session_state["confirm_delete_account"]:
                if st.button(_("delete_account_btn"), key="btn_pre_delete_account"):
                    st.session_state["confirm_delete_account"] = True
                    st.rerun()
            else:
                st.error(_("delete_account_final_confirmation"))
                _col1, _col2 = st.columns(2)
                if _col1.button(_("cancel_btn"), key="btn_cancel_delete"):
                    st.session_state["confirm_delete_account"] = False
                    st.rerun()
                if _col2.button(_("confirm_delete_btn"), key="btn_confirm_delete", type="primary"):
                    try:
                        tva_auth.delete_account(_current_user.id)
                        st.session_state["auth_user"] = None
                        st.session_state["manual_logout"] = True
                        st.success(_("account_deleted_success"))
                        time.sleep(2)
                        st.rerun()
                    except Exception as _del_err:
                        st.error(f"Erreur lors de la suppression : {_del_err}")

    return SidebarResult(
        file_format=file_format,
        enable_vies=enable_vies,
        on_invalid_behavior=on_invalid_behavior,
        convert_fx=convert_fx,
        encoding=encoding,
        asin_to_category=asin_to_category,
        ioss_number=ioss_number,
        seller_is_importer=seller_is_importer,
        apply_fr_under_threshold=apply_fr_under_threshold,
        countries_with_vat=countries_with_vat,
        nom_entreprise=nom_entreprise,
        siren_entreprise=siren_entreprise,
        tva_fr=tva_fr,
        local_vat_numbers=local_vat_numbers,
        oss_period=oss_period,
        siren_quota_status=_siren_quota_status,
        home_country=home_country,
        display_currency=display_currency,
    )