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

from tva_intracom import auth as tva_auth
from tva_intracom import billing as tva_billing
from tva_intracom.rates import EU_COUNTRIES
from tva_intracom.vies import (
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
        st.header("\u2699\ufe0f Options")

        # Rappel pour le thème si l'utilisateur ne le trouve plus
        st.caption("🎨 **Thème** : Accessible via le menu `⋮` > `Settings` > `Theme`.")
        file_format = st.radio("Plateforme source", _PLATFORM_OPTIONS, index=0)

        # ── Connexion Amazon SP-API ───────────────────────────────────────────────
        with st.expander("🔗 Connexion Amazon SP-API", expanded=False):
            _amz_creds = tva_auth.get_amazon_credentials(_current_user.id)
            if _amz_creds:
                st.success(f"✅ Connecté à Amazon (ID: {_amz_creds['selling_partner_id']})")
                if st.button("🔌 Déconnecter Amazon", key="btn_disconnect_amazon"):
                    tva_auth.delete_amazon_credentials(_current_user.id)
                    st.rerun()
            else:
                st.info("Autorisez l'accès à vos données Amazon pour automatiser l'import.")
                # On génère un 'state' pour sécuriser l'OAuth (optionnel mais recommandé)
                _state = secrets.token_hex(8)
                from tva_intracom import amazon_spapi
                try:
                    _auth_url = amazon_spapi.get_authorization_url(state=_state)
                    st.link_button("🚀 Connecter mon compte Amazon", _auth_url)
                except Exception as _err:
                    st.error(f"Erreur configuration : {_err}")

        # ── Validation & Devises ──────────────────────────────────────────────────
        with st.expander("\U0001f50d Validation & Devises", expanded=False):
            # Fonctions toujours actives sur ce compte — cases grisées et
            # verrouillées (disabled=True) pour informer l'utilisateur qu'elles
            # sont bien activées, sans lui laisser la possibilité de les désactiver.
            st.checkbox("Valider les numéros TVA B2B via VIES", value=True, disabled=True,
                help="Toujours activé : interroge les serveurs de l'UE pour vérifier chaque numéro de TVA B2B.")
            enable_vies = True
            on_invalid_behavior = "reclassify"
            st.checkbox("Convertir devises via taux BCE", value=True, disabled=True,
                help="Toujours activé : convertit automatiquement les montants non-EUR au taux BCE.")
            convert_fx = True

        # ── Cache VIES ────────────────────────────────────────────────────────────
        with st.expander("\U0001f5c4\ufe0f Cache VIES", expanded=False):
            try:
                _cs = vies_cache_stats(_vies_scope_id)
                _ttl_days = st.slider("TTL du cache (jours)", min_value=7, max_value=365,
                    value=_cs["ttl_days"], step=7,
                    help="Durée avant revalidation automatique d'un numéro de TVA auprès de VIES.")
                if _ttl_days != _cs["ttl_days"]:
                    set_cache_ttl(_ttl_days)
                    st.rerun()
                _c1, _c2, _c3 = st.columns(3)
                _c1.metric("Total", _cs["total"])
                _c2.metric("✅ Frais", _cs["fresh"])
                _c3.metric("⏳ Expirés", _cs["expired"])
                if _cs["total"] > 0:
                    st.caption(
                        f"Valides : {_cs['valid']} · Invalides : {_cs['invalid']} · "
                        f"Vérifié au plus tôt : {(_cs['oldest_check'] or '—')[:10]}")
                if _cs.get("manual_total", 0) > 0:
                    st.markdown("**🖊️ Classifications manuelles**")
                    _m1, _m2 = st.columns(2)
                    _m1.metric("✅ Valides (B2B)", _cs["manual_valid"])
                    _m2.metric("❌ Invalides (B2C)", _cs["manual_invalid"])
                if _cs["expired"] > 0:
                    if st.button(f"🗑️ Purger {_cs['expired']} entrée(s) expirée(s)", key="purge_vies_cache"):
                        n = purge_expired_cache(_vies_scope_id)
                        st.success(f"{n} entrée(s) supprimée(s).")
                        st.rerun()
            except Exception as _e:
                st.caption(f"Cache VIES indisponible : {_e}")

        # ── Paramètres du fichier ─────────────────────────────────────────────────
        with st.expander("\U0001f4e6 Paramètres du fichier", expanded=False):
            encoding = st.selectbox("Encodage du fichier", ["utf-8","latin-1","cp1252"], index=0)

        # ── Catalogue Produits ────────────────────────────────────────────────────
        with st.expander("\U0001f4cb Catalogue Produits (taux réduits)", expanded=False):
            catalog_file = st.file_uploader("Importer le catalogue Amazon",
                type=["csv","tsv","txt","xlsx"],
                help="Colonnes ASIN + PRODUCT-TAX-CODE pour taux réduits.")
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
                        st.success(f"\U0001f4c8 {len(asin_to_category)} ASIN mappés.")
                except Exception as e:
                    st.error(f"Erreur catalogue : {e}")

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

        with st.expander("\U0001f4c5 Entreprise & Paramètres", expanded=False):
            st.markdown("**Période fiscale**")
            st.caption("La période est auto-détectée depuis les dates de vos transactions.")

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
                        f"✅ Période détectée : **{_detected}**  \n"
                        f"<small style='color:grey'>{_sd[0][:10]} → {_sd[-1][:10]}</small>",
                        unsafe_allow_html=True,
                    )
            elif not _sidebar_results:
                st.caption("_(aucune donnée chargée)_")

            oss_period = "__auto__"

            st.divider()
            st.markdown("**Identité & Paramètres TVA**")
            try:
                _registered_sirens = tva_billing.list_registered_sirens(_current_user.id)
                _siren_quota_status = tva_billing.get_siren_quota_status(_current_user.id)
            except Exception as _siren_list_err:
                _registered_sirens = []
                _siren_quota_status = None
                st.caption(f"⚠️ Liste des SIREN indisponible : {_siren_list_err}")

            _siren_over_quota = bool(_siren_quota_status and _siren_quota_status.blocked)
            if _siren_over_quota:
                st.error(
                    f"🔒 Ce compte a {_siren_quota_status.registered_count} SIREN enregistrés "
                    f"pour un quota de {_siren_quota_status.quota}. Tous les exports sont "
                    f"bloqués tant que {_siren_quota_status.over_quota_by} SIREN n'ont pas été "
                    "retirés (section **💳 Abonnements & forfaits** ci-dessous)."
                )

            _siren_options = [r["siren"] for r in _registered_sirens]
            _siren_label_by_value = {
                r["siren"]: f"{r['company_name'] or '(sans nom)'} — {r['siren']}"
                for r in _registered_sirens
            }
            _siren_label_by_value["➕ Nouveau SIREN…"] = "➕ Nouveau SIREN…"
            _siren_choice = st.selectbox(
                "SIREN client",
                options=_siren_options + ["➕ Nouveau SIREN…"],
                index=0 if _siren_options else 0,
                format_func=lambda v: _siren_label_by_value.get(v, v),
            ) if _siren_options else "➕ Nouveau SIREN…"

            if _siren_choice == "➕ Nouveau SIREN…":
                _can_add_siren, _siren_quota_msg = (True, "")
                try:
                    _can_add_siren, _siren_quota_msg = tva_billing.can_register_new_siren(_current_user.id)
                except Exception as _quota_err:
                    _can_add_siren, _siren_quota_msg = True, ""
                    st.caption(f"⚠️ Vérification du quota indisponible : {_quota_err}")

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
                    nom_entreprise   = st.text_input("Nom de l'entreprise", "Mon Entreprise E-commerce", key="nom_new")
                    siren_entreprise = st.text_input("Numéro SIREN", "123456789", key="siren_new")

                    st.markdown("---")
                    ioss_number = st.text_input("Numéro IOSS propre (optionnel)", placeholder="ex: IM1234567890", key="ioss_new",
                        help="Si renseigné, les imports B2C ≤ 150 € hors marketplace seront traités en IOSS_DIRECT.")
                    seller_is_importer = st.toggle("Vendeur = importateur officiel (DDP)", value=False, key="ddp_new")
                    apply_fr_under_threshold = st.toggle("Appliquer TVA FR sous le seuil OSS (10 000 €)", value=False, key="oss_thr_new")
                    countries_with_vat = st.multiselect("Pays où vous avez un numéro TVA local",
                        options=sorted(list(EU_COUNTRIES)), default=["FR"], key="vat_countries_new")

                    local_vat_numbers = {}
                    _missing_vat_input = False
                    if countries_with_vat:
                        st.caption("Numéros de TVA locaux (obligatoires) :")
                        for ccode in sorted(countries_with_vat):
                            _v = st.text_input(f"Numéro de TVA {ccode}", key=f"vat_num_new_{ccode}",
                                               placeholder=f"ex: {ccode}123456789")
                            local_vat_numbers[ccode] = _v.strip()
                            if not _v.strip():
                                _missing_vat_input = True

                    tva_fr = local_vat_numbers.get("FR", "")

                    if st.button("💾 Enregistrer ce SIREN", key="btn_register_siren"):
                        if not siren_entreprise.strip():
                            st.warning("Le numéro SIREN est requis.")
                        elif siren_entreprise.strip() in _siren_options:
                            st.error(f"🚫 Le SIREN {siren_entreprise.strip()} est déjà enregistré sur ce compte.")
                        elif _missing_vat_input:
                            st.warning("Veuillez remplir le numéro de TVA pour chaque pays sélectionné.")
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
                                st.success("✅ SIREN enregistré.")
                                st.rerun()
                            except Exception as _reg_err:
                                st.error(f"Erreur d'enregistrement : {_reg_err}")
            else:
                _match = next((r for r in _registered_sirens if r["siren"] == _siren_choice), None)
                nom_entreprise   = _match["company_name"] if _match else ""
                siren_entreprise = _match["siren"] if _match else ""

                # Affichage de l'identité (fixe)
                st.markdown(f"🏢 **{nom_entreprise}**")
                st.caption(f"SIREN : **{siren_entreprise}**")

                try:
                    _existing_vats = json.loads(_match.get("vat_numbers_json") or "{}") if _match else {}
                except Exception:
                    _existing_vats = {}

                _tva_fr_fixed = _existing_vats.get("FR") or _match.get("tva_number") or ""
                if _tva_fr_fixed:
                    st.caption(f"TVA FR : **{_tva_fr_fixed}**")

                st.markdown("---")
                st.markdown("**Paramètres fiscaux**")

                # Option pour déverrouiller la modification des numéros déjà enregistrés
                allow_edit_ids = st.checkbox("🔓 Modifier les identifiants (IOSS, TVA)", value=False, help="Cochez pour modifier les numéros déjà enregistrés.")

                # IOSS
                _ioss_val = _match.get("ioss_number") or ""
                if _ioss_val and not allow_edit_ids:
                    st.caption(f"IOSS : **{_ioss_val}**")
                    ioss_number = _ioss_val
                else:
                    ioss_number = st.text_input("Numéro IOSS propre (optionnel)",
                        value=_ioss_val,
                        placeholder="ex: IM1234567890", key="ioss_edit")

                seller_is_importer = st.toggle("Vendeur = importateur officiel (DDP)", value=_match.get("seller_is_importer") or False if _match else False, key="ddp_edit")
                apply_fr_under_threshold = st.toggle("Appliquer TVA FR sous le seuil OSS (10 000 €)", value=_match.get("apply_fr_under_threshold") or False if _match else False, key="oss_thr_edit")

                _countries_raw = _match.get("countries_with_vat") or "FR" if _match else "FR"
                _default_vat_countries = [c.strip().upper() for c in _countries_raw.split(",") if c.strip()]

                countries_with_vat = st.multiselect("Pays où vous avez un numéro TVA local",
                    options=sorted(list(EU_COUNTRIES)), default=_default_vat_countries, key="vat_countries_edit")

                local_vat_numbers = {}
                _missing_vat_input = False
                if countries_with_vat:
                    st.caption("Numéros de TVA locaux :")
                    for ccode in sorted(countries_with_vat):
                        _val = _existing_vats.get(ccode, "")
                        if _val and not allow_edit_ids:
                            st.caption(f"✅ {ccode} : **{_val}**")
                            local_vat_numbers[ccode] = _val
                        else:
                            _v = st.text_input(f"Numéro de TVA {ccode}",
                                               value=_val,
                                               key=f"vat_num_edit_{ccode}",
                                               placeholder=f"ex: {ccode}123456789")
                            local_vat_numbers[ccode] = _v.strip()
                            if not _v.strip():
                                _missing_vat_input = True

                # Mise à jour de tva_fr pour le XML OSS (toujours basé sur le numéro FR)
                tva_fr = local_vat_numbers.get("FR", _tva_fr_fixed)

                if st.button("💾 Enregistrer les modifications", key="btn_update_siren"):
                    if _missing_vat_input:
                        st.warning("Veuillez remplir le numéro de TVA pour chaque pays sélectionné.")
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
                            st.success("✅ Paramètres mis à jour.")
                            st.rerun()
                        except Exception as _reg_err:
                            st.error(f"Erreur de mise à jour : {_reg_err}")

                # Option de retrait du SIREN (toujours visible si déjà enregistré)
                if _match:
                    st.divider()
                    if _match.get("pending_removal_at"):
                        import datetime as _dt
                        _eff_date = _dt.datetime.fromtimestamp(_match["pending_removal_at"]).strftime("%d/%m/%Y")
                        st.warning(f"⏳ Retrait programmé le {_eff_date}.")
                        if st.button("↩️ Annuler le retrait", key=f"btn_cancel_removal_{siren_entreprise}", use_container_width=True):
                            tva_billing.cancel_siren_removal(_current_user.id, siren_entreprise)
                            st.rerun()
                    else:
                        if st.button("🗑️ Retirer ce SIREN", key=f"btn_remove_entreprise_{siren_entreprise}",
                                    help="Le retrait sera effectif à la fin de la période de facturation en cours.",
                                    use_container_width=True):
                            # On autorise le retrait même si c'est le dernier (l'utilisateur peut vouloir arrêter)
                            _eff = tva_billing.request_siren_removal(_current_user.id, siren_entreprise)
                            import datetime as _dt
                            if _eff <= time.time() + 5:
                                st.success("✅ SIREN retiré.")
                            else:
                                st.info(f"Retrait programmé le {_dt.datetime.fromtimestamp(_eff).strftime('%d/%m/%Y')}.")
                            st.rerun()

        # ── Abonnements & forfaits ────────────────────────────────────────────────
        with st.expander("\U0001f4b3 Abonnements & forfaits", expanded=True):
            _sub_status = None
            try:
                _sub_status = tva_billing.get_subscription_status(_current_user.id)
            except Exception as _sub_err:
                st.caption(f"⚠️ Statut d'abonnement indisponible : {_sub_err}")

            _plan_label = {"business": "Pro", "cabinet": "Cabinet"}.get(
                _sub_status.plan if _sub_status else None, _sub_status.plan if _sub_status else "—")
            _interval_label = {"month": "mensuel", "year": "annuel"}.get(
                _sub_status.billing_interval if _sub_status else None, "")

            if _sub_status and _sub_status.active:
                st.success(f"✅ Abonnement **{_plan_label}** actif ({_interval_label})"
                    + (f" — {_sub_status.siren_quantity} SIREN" if _sub_status.plan == "cabinet" else ""))

                # Gestion des SIREN pour un abonnement Cabinet (ajout via la section
                # Entreprise, retrait différé ici, effectif à la date anniversaire).
                if _sub_status.plan == "cabinet" and _registered_sirens:
                    st.markdown("**SIREN gérés par cet abonnement**")
                    for _r in _registered_sirens:
                        _c1, _c2 = st.columns([2, 1])
                        _label = f"{_r['company_name'] or '(sans nom)'} — {_r['siren']}"
                        if _r.get("pending_removal_at"):
                            _c1.caption(f"{_label} · ⏳ retrait programmé")
                        else:
                            _c1.caption(_label)
                            if _c2.button("Retirer", key=f"btn_remove_{_r['siren']}", use_container_width=True):
                                _eff = tva_billing.request_siren_removal(_current_user.id, _r["siren"])
                                import datetime as _dt
                                st.info(f"Retrait programmé le {_dt.datetime.fromtimestamp(_eff).strftime('%d/%m/%Y')}.")
                                st.rerun()

                try:
                    _portal_url = tva_billing.create_billing_portal_session(
                        _current_user.id,
                        return_url=_stripe_cancel_url(),
                    )
                    st.link_button("Gérer mon abonnement (Stripe)", _portal_url)
                except Exception:
                    pass

            # ── Crédits PAYG (Achats uniques) ─────────────────────────────────────
            try:
                _credits = tva_billing.list_purchased_credits(_current_user.id)
                if _credits:
                    st.markdown("---")
                    st.markdown("**🔓 Périodes débloquées (Achats uniques)**")
                    for _c in _credits:
                        from datetime import datetime as _dt
                        _at = _dt.fromtimestamp(_c["at"]).strftime("%d/%m/%Y")
                        st.caption(f"✅ **{_c['period']}** — acheté le {_at}")
            except Exception as _credit_err:
                st.caption(f"⚠️ Historique d'achats indisponible : {_credit_err}")
            else:
                if _sub_status and _sub_status.status:
                    # Abonnement existant mais inactif (annulé/expiré) : état actuel
                    # affiché pour information, sans historique complet.
                    st.warning(f"⏹️ Dernier abonnement **{_plan_label}** — statut : {_sub_status.status}"
                        + (f" (expiré le {__import__('datetime').datetime.fromtimestamp(_sub_status.current_period_end).strftime('%d/%m/%Y')})"
                           if _sub_status.current_period_end else ""))

                st.caption(
                    "Achat unique par déclaration, ou abonnement illimité — "
                    "mensuel ou annuel."
                )

                with st.expander("📋 Voir la grille tarifaire", expanded=False):
                    try:
                        _grid = tva_billing.get_pricing_grid(_current_user.id)
                    except Exception as _grid_err:
                        _grid = None
                        st.caption(f"⚠️ Grille tarifaire indisponible : {_grid_err}")

                    if _grid:
                        try:
                            _promotions = tva_billing.list_available_promotions(_current_user.id)
                        except Exception as _promo_list_err:
                            _promotions = []
                            st.error(f"Codes promotionnels indisponibles : {_promo_list_err}")

                        if _promotions:
                            st.markdown("**Codes promotionnels disponibles**")
                            for _promo_item in _promotions:
                                if _promo_item.get("percent_off") is not None:
                                    _reduc = f"{_promo_item['percent_off']:g}%"
                                elif _promo_item.get("amount_off") is not None:
                                    _reduc = f"{_promo_item['amount_off']:.2f} {(_promo_item.get('currency') or 'eur').upper()}"
                                else:
                                    _reduc = "—"

                                _conditions = []
                                if _promo_item.get("first_time_only"):
                                    _conditions.append("1ère commande uniquement")
                                if _promo_item.get("minimum_amount") is not None:
                                    _conditions.append(
                                        f"montant min. {_promo_item['minimum_amount']:.2f} "
                                        f"{(_promo_item.get('minimum_amount_currency') or 'eur').upper()}"
                                    )
                                if _promo_item.get("stock_remaining") is not None:
                                    _conditions.append(f"{_promo_item['stock_remaining']} code(s) restant(s)")
                                if _promo_item.get("expires_at"):
                                    import datetime as _dt
                                    _conditions.append(
                                        "valable jusqu'au "
                                        + _dt.datetime.fromtimestamp(_promo_item["expires_at"]).strftime("%d/%m/%Y")
                                    )
                                _conditions_txt = " · ".join(_conditions) if _conditions else "sans condition particulière"

                                _eligible = _promo_item.get("eligible")
                                if _eligible is True:
                                    st.success(f"✅ **{_promo_item['code']}** — {_reduc} — {_conditions_txt}")
                                elif _eligible is False:
                                    _reasons_txt = ", ".join(_promo_item.get("ineligible_reasons", []))
                                    st.warning(f"❌ **{_promo_item['code']}** — {_reduc} — {_conditions_txt} (non éligible : {_reasons_txt})")
                                else:
                                    st.markdown(f"- **{_promo_item['code']}** — {_reduc} — {_conditions_txt}")

                        if _grid.get("payg"):
                            _p = _grid["payg"]
                            _payg_label = _p.get("name") or "Achat unique"
                            if _p.get("discounted_amount") is not None:
                                st.markdown(
                                    f"**{_payg_label}** — "
                                    f"<span style='text-decoration:line-through;color:gray'>{_p['amount']:.2f} {_p['currency'].upper()}</span> "
                                    f"&nbsp;→&nbsp; <span style='color:#2ca02c;font-weight:bold'>{_p['discounted_amount']:.2f} {_p['currency'].upper()}</span> "
                                    f"({_p['discount_label']}, code {_p['discount_code']}) / déclaration",
                                    unsafe_allow_html=True,
                                )
                            else:
                                st.markdown(f"**{_payg_label}** — {_p['amount']:.2f} "
                                    f"{_p['currency'].upper()} / déclaration")

                        if _grid.get("business"):
                            _biz_lines = []
                            _biz_label = None
                            for _iv, _lbl in (("month", "mois"), ("year", "an")):
                                _b = _grid["business"].get(_iv)
                                if _b and _b["amount"] is not None:
                                    if _biz_label is None:
                                        _biz_label = _b.get("name") or "Pro"
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
                            for _iv, _lbl in (("month", "mensuel"), ("year", "annuel")):
                                _c = _grid["cabinet"].get(_iv)
                                if not _c or not _c.get("tiers"):
                                    continue
                                _cab_label = _c.get("name") or "Cabinet"
                                st.markdown(f"**{_cab_label} — {_lbl}** (min. 3 SIREN)")
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
                                                f"({_t['discount_label']}, code {_t['discount_code']}) / SIREN"
                                            )
                                        else:
                                            _price_txt = f"{_t['unit_amount']:.2f} {_c['currency'].upper()} / SIREN"
                                    else:
                                        _price_txt = "—"
                                    if _t.get("flat_amount") is not None:
                                        _price_txt += f" (+ {_t['flat_amount']:.2f} {_c['currency'].upper()} fixe)"
                                    _rows.append({"SIREN gérés": _range, "Tarif": _price_txt})
                                    _prev_bound = _up_to if _up_to is not None else _prev_bound
                                # st.dataframe n'interprète pas le HTML (barré/couleur). On utilise st.markdown
                                # avec l'export HTML du DataFrame pour conserver le formattage.
                                st.markdown(
                                    pd.DataFrame(_rows).to_html(escape=False, index=False, classes="cabinet-table"),
                                    unsafe_allow_html=True
                                )

                if not (_sub_status and _sub_status.active):
                    _detected_period_for_payg = st.session_state.get("_period_label", "")
                    st.markdown("**Achat unique** — une déclaration, la période détectée dans votre fichier")
                    if not _detected_period_for_payg:
                        st.caption(
                            "📂 Importez d'abord un fichier (zone de dépôt principale) pour "
                            "que la période à débloquer soit détectée."
                        )
                    else:
                        st.caption(f"📅 Période détectée qui sera débloquée : **{_detected_period_for_payg}**")
                        if st.button("Acheter cette période — Achat unique", key="btn_payg_sidebar"):
                            try:
                                _payg_cache_key = f"_stripe_checkout_url::{_detected_period_for_payg}"
                                if _payg_cache_key not in st.session_state:
                                    st.session_state[_payg_cache_key] = tva_billing.create_payg_checkout_session(
                                        user_id=_current_user.id, email=_current_user.email,
                                        period_label=_detected_period_for_payg,
                                        success_url=_stripe_success_url("export_ok=1"),
                                        cancel_url=_stripe_cancel_url(),
                                    )
                                st.link_button("→ Continuer vers le paiement", st.session_state[_payg_cache_key])
                            except Exception as _payg_err:
                                st.session_state.pop(_payg_cache_key, None)
                                st.error(f"Erreur : {_payg_err}")

                    _sub_interval = st.radio("Facturation", ["Mensuel", "Annuel"],
                        horizontal=True, key="sub_interval_choice")
                    _interval_code = "month" if _sub_interval == "Mensuel" else "year"

                    st.markdown("**Pro** — accès illimité, 1 SIREN")
                    if st.button("S'abonner — Pro", key="btn_sub_business"):
                        try:
                            _url = tva_billing.create_subscription_checkout_session(
                                user_id=_current_user.id, email=_current_user.email,
                                plan="business", interval=_interval_code,
                                success_url=_stripe_success_url("export_ok=1"),
                                cancel_url=_stripe_cancel_url(),
                            )
                            st.link_button("→ Continuer vers le paiement", _url)
                        except Exception as _biz_err:
                            st.error(f"Erreur : {_biz_err}")

                    st.markdown("**Cabinet** — accès illimité, tarif dégressif par SIREN géré (3 SIREN minimum)")
                    _cabinet_qty = st.number_input("Nombre de SIREN gérés", min_value=3, max_value=500,
                        value=max(3, _siren_quota_status.registered_count if _siren_quota_status else 3), step=1,
                        key="cabinet_siren_qty",
                        help="Minimum 3 SIREN for the forfait Cabinet. Doit couvrir au moins "
                             "le nombre de SIREN déjà enregistrés sur ce compte.")
                    if st.button("S'abonner — Cabinet", key="btn_sub_cabinet"):
                        try:
                            _url = tva_billing.create_subscription_checkout_session(
                                user_id=_current_user.id, email=_current_user.email,
                                plan="cabinet", interval=_interval_code,
                                quantity=int(_cabinet_qty),
                                success_url=_stripe_success_url("export_ok=1"),
                                cancel_url=_stripe_cancel_url(),
                            )
                            st.link_button("→ Continuer vers le paiement", _url)
                        except Exception as _cab_err:
                            st.error(f"Erreur : {_cab_err}")

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
    )
