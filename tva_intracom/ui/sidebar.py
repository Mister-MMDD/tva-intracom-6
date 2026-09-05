"""Rendu complet de la barre latérale (extrait tel quel de app.py).

Regroupe tous les accordéons de la sidebar :
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
import time
from dataclasses import dataclass, field
from typing import Any

import pandas as pd
import streamlit as st

from tva_intracom import auth as tva_auth
from tva_intracom import auth_supabase as tva_sb_auth
from tva_intracom import billing as tva_billing
from tva_intracom.i18n import _, country_label
from tva_intracom.rates import EU_COUNTRIES, COUNTRY_CURRENCIES, CURRENCY_SYMBOLS, \
    oss_threshold_in_currency
from tva_intracom.ui.rerun_utils import preserve_upload_rerun
from tva_intracom.ui.theme import _PLATFORM_OPTIONS
from tva_intracom.ui.display_mode import is_detailed
from tva_intracom.vies_engine import (
    get_cache_stats,
    purge_expired_cache,
    set_cache_ttl,
)


@st.cache_data(ttl=60, show_spinner=False)
def vies_cache_stats(scope_id: str) -> dict:
    """Wrapper cache (60s) autour de get_cache_stats.

    get_cache_stats exécute 3 SELECT COUNT(*) (dont un sur le cache global,
    potentiellement volumineux) — sans ce cache, ces requêtes tournaient à
    CHAQUE rerun Streamlit (chaque clic), y compris quand le st.expander()
    qui les affiche est replié (Streamlit exécute le corps du bloc `with`
    même fermé). Charge Supabase inutile pour un affichage informatif qui
    n'a pas besoin d'une précision à la seconde. Wrapper placé ici (et non
    dans vies_engine.py) pour ne pas introduire de dépendance Streamlit
    dure dans ce module (voir engine_note isolation : vies_engine.py doit
    rester utilisable hors contexte Streamlit — webhook/CLI).
    """
    return get_cache_stats(scope_id)


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
    ioss_own_number_active: bool = False


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


# ── Cache TTL des lectures Postgres répétées à chaque rerun ─────────────────
# `render_sidebar()` s'exécute intégralement à CHAQUE rerun Streamlit (tout
# widget cliqué n'importe où dans l'app), ce qui déclenchait sans cache un
# aller-retour Postgres (parfois une écriture, voir list_registered_sirens qui
# purge les retraits SIREN expirés) à chaque interaction, même quand rien
# n'a changé côté abonnement/SIREN/identifiants Amazon depuis la dernière
# lecture. `_cached_db_read` mémoïse ces lectures en session_state avec un
# TTL court (quelques secondes) — assez pour absorber une rafale
# d'interactions UI, assez court pour rester à jour après une action Stripe
# externe (paiement, changement de forfait). Invalidée immédiatement
# (force=True) après toute action de mutation locale (ajout/retrait SIREN,
# déconnexion Amazon...) pour refléter le changement sans attendre le TTL.
_DB_CACHE_TTL_SECONDS = 20


def _cached_db_read(cache_key: str, fetch_fn, force: bool = False):
    _skey = f"_sb_dbcache_{cache_key}"
    _cached = st.session_state.get(_skey)
    _now = time.time()
    if (not force) and _cached is not None and (_now - _cached[0]) < _DB_CACHE_TTL_SECONDS:
        return _cached[1]
    _value = fetch_fn()
    st.session_state[_skey] = (_now, _value)
    return _value


def _invalidate_db_cache(cache_key: str) -> None:
    st.session_state.pop(f"_sb_dbcache_{cache_key}", None)


@st.fragment
def _new_siren_form_fragment(*, current_user, home_country: str, siren_options: list[str]) -> None:
    """Formulaire de création d'un nouveau SIREN, isolé en fragment.

    BUGFIX : avant, ce formulaire vivait directement dans le corps de
    render_sidebar() — taper un caractère dans un champ, cocher une case ou
    ajouter un pays à la liste déclenchait un rerun COMPLET de toute la page
    (comportement Streamlit par défaut pour tout widget hors fragment),
    redessinant au passage les 6 onglets déjà affichés (tableaux, graphiques)
    même si aucune valeur enregistrée en base n'avait changé. Isolé ici, ces
    interactions ne redessinent plus que ce formulaire. Seul le clic sur
    "Enregistrer ce SIREN" déclenche un rerun complet (nécessaire pour
    recharger la liste des SIREN enregistrés et faire passer ce compte dans
    le cas "SIREN existant" au tour suivant).
    """
    # RÔLES (2026-08-24) : `_is_reader_new` grise l'ensemble du formulaire de
    # création pour un compte lecteur, par cohérence avec la vue "SIREN
    # existant" — même si, ce formulaire étant isolé en fragment et ses
    # valeurs non lues par le calcul tant qu'aucun SIREN n'est enregistré
    # (voir render_sidebar, valeurs par défaut posées AVANT l'appel à ce
    # fragment), il n'y avait ici aucun impact sur le résultat fiscal
    # affiché — seulement une possibilité de saisie sans effet, déjà bloquée
    # par le bouton "Enregistrer" désactivé plus bas.
    _is_reader_new = current_user.role == "reader"
    nom_entreprise   = st.text_input(_("company_name_label"), placeholder=f"ex: {_('default_company_name')}", key="nom_new", disabled=_is_reader_new)
    siren_entreprise = st.text_input(_("siren_number_label"), placeholder="ex: 123456789", key="siren_new", disabled=_is_reader_new)

    # ── Pays où la TVA locale est enregistrée : remonté juste sous le SIREN,
    # au-dessus d'IOSS/DDP/seuil OSS. Priorité fiscale : ces immatriculations
    # locales priment sur le régime DDP et les autres réglages (une TVA
    # locale déjà enregistrée dans un pays change la façon dont ce pays doit
    # être traité, indépendamment des toggles ci-dessous).
    countries_with_vat = st.multiselect(_("local_vat_countries_label"),
                                        options=sorted(list(EU_COUNTRIES)), default=["FR"], key="vat_countries_new",
                                        disabled=_is_reader_new)

    local_vat_numbers = {}
    _missing_vat_input = False
    if countries_with_vat:
        st.caption(_("local_vat_numbers_caption"))
        for ccode in sorted(countries_with_vat):
            _v = st.text_input(_("vat_number_for", country=ccode), key=f"vat_num_new_{ccode}",
                               placeholder=f"ex: {ccode}123456789", disabled=_is_reader_new)
            local_vat_numbers[ccode] = _v.strip()
            if not _v.strip():
                _missing_vat_input = True

    tva_fr = local_vat_numbers.get("FR", "")

    st.markdown("---")
    ioss_number = st.text_input(_("ioss_number_label"), placeholder="ex: IM1234567890", key="ioss_new",
                                help=_("ioss_help"), disabled=_is_reader_new)
    ioss_own_number_active = False
    if ioss_number.strip():
        ioss_own_number_active = st.toggle(
            _("ioss_own_number_active_label"), value=False, key="ioss_own_active_new",
            help=_("ioss_own_number_active_help", platform="Amazon"),
            disabled=_is_reader_new,
        )
    seller_is_importer = st.toggle(_("ddp_label"), value=False, key="ddp_new", disabled=_is_reader_new)
    apply_fr_under_threshold = st.toggle(_("oss_threshold_apply_label", country=home_country, limit=_oss_limit_label(home_country)), value=False, key="oss_thr_new", disabled=_is_reader_new)
    oss_threshold_exceeded_prev_year = st.toggle(
        _("oss_threshold_prev_year_label"), value=False, key="oss_thr_prevyear_new",
        help=_("oss_threshold_prev_year_help"), disabled=_is_reader_new,
    )
    if oss_threshold_exceeded_prev_year and apply_fr_under_threshold:
        st.caption("⚠️ " + _("oss_threshold_prev_year_help"))
        apply_fr_under_threshold = False

    if st.button(_("save_siren_btn"), key="btn_register_siren", disabled=(current_user.role == "reader")):
        if not siren_entreprise.strip():
            st.warning(_("siren_required"))
        elif siren_entreprise.strip() in siren_options:
            st.error(_("siren_already_registered", siren=siren_entreprise.strip()))
        elif not countries_with_vat:
            st.warning(_("at_least_one_vat_required"))
        elif _missing_vat_input:
            st.warning(_("missing_vat_numbers"))
        else:
            try:
                tva_billing.register_siren(
                    current_user.org_id, current_user.id, siren_entreprise.strip(),
                    nom_entreprise.strip(), tva_fr.strip(),
                    ioss_number=ioss_number.strip(),
                    seller_is_importer=seller_is_importer,
                    apply_fr_under_threshold=apply_fr_under_threshold,
                    countries_with_vat=",".join(countries_with_vat),
                    vat_numbers_json=json.dumps(local_vat_numbers),
                    oss_threshold_exceeded_prev_year=oss_threshold_exceeded_prev_year,
                    ioss_own_number_active=ioss_own_number_active,
                )
                st.success(_("siren_save_success"))
                _invalidate_db_cache(f"sirens_{current_user.org_id}")
                _invalidate_db_cache(f"siren_quota_{current_user.org_id}")
                # Fait pointer le sélecteur sur le SIREN qu'on vient de créer
                # (au lieu de laisser "+ Nouveau SIREN" sélectionné) : sans
                # cela, le rappel de verrouillage affiché au-dessus
                # d'"Identité & Paramètres TVA" (voir render_sidebar)
                # resterait affiché indéfiniment après l'enregistrement,
                # puisqu'il se base sur la valeur de ce même sélecteur.
                st.session_state["siren_select_box"] = siren_entreprise.strip()
                preserve_upload_rerun()  # rerun complet volontaire : il faut recharger _registered_sirens
            except Exception as _reg_err:
                st.error(_("siren_save_error", error=_reg_err))


@st.fragment
def _edit_siren_form_fragment(
        *, current_user, home_country: str, match: dict | None,
        siren_entreprise: str, nom_entreprise: str, tva_fr_fixed: str,
        existing_vats: dict, ioss_val: str,
        seller_is_importer: bool, apply_fr_under_threshold: bool,
        oss_threshold_exceeded_prev_year: bool, ioss_own_number_active: bool,
        countries_with_vat: list[str], new_vat_countries: list[str],
) -> None:
    """Formulaire d'édition d'un SIREN déjà enregistré, isolé en fragment.

    BUGFIX (2026-08-21) : les 4 toggles fiscaux (IOSS actif, DDP, seuil OSS,
    OSS N-1) et le multiselect des pays TVA vivaient auparavant DANS ce
    fragment. Un fragment isolé ne redessine QUE lui-même quand un de ses
    widgets change — `render_sidebar()` ne se relance pas, donc les valeurs
    "effectives" retournées par `SidebarResult` (et donc `_cache_key` dans
    app.py) restaient celles de `match` (dernière sauvegarde en base) tant
    que le bouton "Enregistrer les modifications" n'était pas cliqué :
    cocher/décocher ces toggles n'avait AUCUN effet visible, aucun
    recalcul. Ces widgets sont désormais rendus en LIVE dans le corps de
    render_sidebar() (voir juste avant l'appel à cette fonction) : leur
    valeur courante est passée ici en paramètre, déjà "vraie" pour le
    calcul en cours.

    BUGFIX (2026-08-23) : les champs de saisie des numéros de TVA pour les
    pays nouvellement ajoutés vivaient aussi DANS ce fragment, très loin en
    dessous du multiselect qui sélectionne ces mêmes pays (après IOSS/DDP/
    seuil OSS) — perdu de vue par l'utilisateur. Ils sont désormais rendus
    en LIVE juste après le multiselect (voir render_sidebar()), avec les
    mêmes clés `vat_num_edit_{pays}` : ce fragment se contente de RELIRE
    leur valeur courante via `st.session_state[key]` au moment de l'enregis-
    trement, sans les redessiner (éviterait un conflit de clé). Cela ne
    réintroduit PAS le bug ci-dessus : `st.session_state` est mis à jour de
    façon synchrone par Streamlit dès l'interaction, avant toute exécution
    de script — le lire ici, dans ce fragment exécuté après coup dans le
    même run, donne toujours la valeur à jour, y compris lors d'un rerun
    isolé à ce seul fragment (clic sur "Enregistrer").

    Ce fragment ne conserve que ce qui bénéficie réellement de l'isolation
    anti-rerun-par-frappe : la saisie du numéro IOSS non encore verrouillé
    (`ioss_val` vide — n'entre pas dans `_cache_key`, donc taper ici ne
    redessine plus que ce fragment sans déclencher de recalcul prématuré),
    et le bouton de sauvegarde.
    """
    # ⚠️ Verrouillage définitif : une fois un IOSS ou un numéro de TVA
    # enregistré pour ce SIREN, il n'est PLUS modifiable — seuls les champs
    # encore VIDES restent éditables, pour permettre d'ajouter un IOSS non
    # renseigné au départ, ou un numéro de TVA pour un nouveau pays ajouté à
    # la liste. But : ces numéros engagent fiscalement le compte
    # (déclarations déjà potentiellement transmises avec ces valeurs) — les
    # modifier après coup serait risqué.
    #
    # BUGFIX (2026-08-23) : le message se basait sur `not ioss_val`, donc
    # restait affiché indéfiniment tant qu'aucun IOSS n'était renseigné —
    # alors que l'IOSS est volontairement optionnel (beaucoup de comptes
    # n'en auront jamais). Il ne doit s'afficher que s'il y a réellement
    # quelque chose sur le point d'être verrouillé à CE prochain
    # enregistrement : un nouveau pays de TVA pas encore verrouillé
    # (`new_vat_countries`, déjà obligatoirement rempli avant sauvegarde,
    # voir `at_least_one_vat_required`), ou un numéro IOSS en cours de
    # frappe dans le champ ci-dessous (lu via `st.session_state["ioss_edit"]`
    # avant même que ce widget ne soit redessiné ce run-ci : sûr, Streamlit
    # synchronise session_state depuis l'interaction AVANT toute exécution
    # de script, indépendamment de l'ordre des lignes).
    _ioss_draft_typed = bool(not ioss_val and str(st.session_state.get(f"ioss_edit_{siren_entreprise}", "") or "").strip())
    if new_vat_countries or _ioss_draft_typed:
        st.warning(_("fiscal_fields_lock_warning"))
    elif ioss_val:
        st.caption(_("fiscal_fields_all_locked_caption"))
    elif existing_vats:
        st.caption(_("fiscal_fields_vat_locked_caption"))

    if ioss_val:
        st.caption(f"🔒 IOSS : **{ioss_val}** — {_('fiscal_field_locked_note')}")
        _draft_ioss_number = ioss_val
    else:
        # BUGFIX (2026-08-26) : clé scopée par SIREN (même raison que les
        # autres champs ci-dessus) — un brouillon d'IOSS tapé pour un SIREN
        # sans IOSS pouvait sinon réapparaître en changeant vers un autre
        # SIREN sans IOSS non plus.
        _draft_ioss_number = st.text_input(_("ioss_number_label"),
                                           placeholder="ex: IM1234567890",
                                           key=f"ioss_edit_{siren_entreprise}",
                                           help=_("ioss_help"))

    # ── Numéros de TVA : les pays déjà enregistrés sont affichés en lecture
    # seule dans render_sidebar() ; les pays NOUVELLEMENT ajoutés (pas
    # encore verrouillés) y sont aussi saisis désormais (juste après le
    # multiselect, voir BUGFIX 2026-08-23 ci-dessus) — on relit simplement
    # leur valeur courante ici via st.session_state, sans les redessiner.
    # BUGFIX (2026-08-26) : clé alignée avec le scoping par SIREN du widget
    # correspondant (voir render_sidebar(), `vat_num_edit_{siren}_{pays}`).
    _draft_local_vat_numbers = dict(existing_vats)
    _missing_vat_input = False
    for ccode in sorted(new_vat_countries):
        _v = str(st.session_state.get(f"vat_num_edit_{siren_entreprise}_{ccode}", "") or "")
        _draft_local_vat_numbers[ccode] = _v.strip()
        if not _v.strip():
            _missing_vat_input = True

    # Mise à jour de tva_fr pour le XML OSS (toujours basé sur le numéro FR)
    _draft_tva_fr = _draft_local_vat_numbers.get("FR", tva_fr_fixed)

    if st.button(_("save_changes_btn"), key="btn_update_siren", disabled=(current_user.role == "reader")):
        if not countries_with_vat:
            st.warning(_("at_least_one_vat_required"))
        elif _missing_vat_input:
            st.warning(_("missing_vat_numbers"))
        else:
            try:
                tva_billing.register_siren(
                    current_user.org_id, current_user.id, siren_entreprise.strip(),
                    nom_entreprise.strip(), _draft_tva_fr.strip(),
                    ioss_number=_draft_ioss_number.strip(),
                    seller_is_importer=seller_is_importer,
                    apply_fr_under_threshold=apply_fr_under_threshold,
                    countries_with_vat=",".join(countries_with_vat),
                    vat_numbers_json=json.dumps(_draft_local_vat_numbers),
                    oss_threshold_exceeded_prev_year=oss_threshold_exceeded_prev_year,
                    ioss_own_number_active=ioss_own_number_active,
                )
                st.success(_("update_success"))
                _invalidate_db_cache(f"sirens_{current_user.org_id}")
                _invalidate_db_cache(f"siren_quota_{current_user.org_id}")
                preserve_upload_rerun()  # rerun complet volontaire : il faut recharger _match à jour
            except Exception as _reg_err:
                st.error(_("update_error", error=_reg_err))


# Taille max acceptée pour un catalogue produits uploadé (Mo). Sans cette
# garde, un fichier très volumineux lu via pd.read_csv(engine='python')
# pouvait épuiser la mémoire du process Streamlit (partagé entre sessions
# sur Streamlit Cloud) — DoS involontaire ou malveillant.
_MAX_CATALOG_MB = 100


@st.cache_resource(show_spinner=False, ttl=1800, max_entries=20)
def _parse_catalog_bytes(file_bytes: bytes, filename: str) -> dict[str, str]:
    """Parse un catalogue ASIN → catégorie fiscale depuis son contenu brut.

    Mis en cache par contenu (`file_bytes` fait partie de la clé de hash) :
    tant que l'utilisateur ne change pas de fichier, ce parsing ne s'exécute
    qu'une seule fois, au lieu d'être refait à chaque rerun Streamlit
    (changement de widget, etc.).

    `st.cache_resource` (et non `st.cache_data`) : le dict retourné n'est
    JAMAIS muté après sa construction (uniquement des `.get()` en aval, dans
    `engine.py`/`loader.py`) — `cache_resource` partage donc la même
    instance mémoire entre toutes les sessions au lieu d'en renvoyer une
    copie par appel. Pour un catalogue de 20k+ ASIN et plusieurs sessions
    utilisateur simultanées, ça évite une copie complète du dict par
    session (RAM divisée par le nombre de sessions actives).

    IMPORTANT (mémoire) : ce cache est GLOBAL au process (partagé entre
    toutes les sessions), donc jamais purgé par le logout ni par le retrait
    d'un fichier en session_state. Sans borne, un nouveau catalogue uploadé
    par n'importe quel utilisateur créait une entrée permanente. `ttl=1800`
    + `max_entries=20` évitent la croissance illimitée.
    """
    import io
    buf = io.BytesIO(file_bytes)
    if filename.endswith(".tsv"):
        df_cat = pd.read_csv(buf, sep="\t")
    else:
        # CSV/TXT : on tente de détecter le séparateur (comportement
        # simplifié par rapport au chargeur principal de fichiers de ventes).
        df_cat = pd.read_csv(buf, sep=None, engine="python")
    df_cat.columns = [c.strip().upper() for c in df_cat.columns]
    asin_col = next((c for c in df_cat.columns if "ASIN" in c), None)
    cat_col = next((c for c in df_cat.columns if "PRODUCT-TAX-CODE" in c or "TAX-CODE" in c), None)
    if not cat_col:
        cat_col = next((c for c in df_cat.columns if any(k in c for k in ["TAX", "GROUP", "CODE", "TYPE"])), None)
    if asin_col and cat_col:
        import sys
        # PERF RAM (voir README - évolution.md) : les ASIN du catalogue sont
        # comparés/utilisés comme clé pour retrouver les mêmes ASIN déjà
        # internés côté Sale (models.py, `self.asin`). Sans sys.intern() ici,
        # chaque ASIN existe deux fois en mémoire (chaîne catalogue distincte
        # de la chaîne Sale) au lieu de partager le même objet str.
        return {
            sys.intern(str(a).strip().upper()): sys.intern(str(c).strip().upper())
            for a, c in zip(df_cat[asin_col], df_cat[cat_col]) if pd.notna(a) and pd.notna(c)
        }
    return {}


@st.dialog(title=_("account_privacy_header"))
def _render_account_dialog(_current_user) -> None:
    """Compte & Confidentialité, dans une modale plutôt que dans le corps de
    la sidebar (voir appel dans render_sidebar). Contenu strictement
    inchangé (mot de passe / export RGPD / suppression de compte), seul
    l'emplacement change.
    """
    st.markdown(f"**{_('onboarding_restart_title')}**")
    st.caption(_("onboarding_restart_help"))
    if st.button(_("onboarding_restart_btn"), key="btn_restart_onboarding"):
        from tva_intracom.ui.onboarding import restart_onboarding
        restart_onboarding()
        preserve_upload_rerun()

    st.divider()
    st.markdown(f"**{_('account_change_password_title')}**")
    st.caption(_("account_change_password_help"))

    _current_pwd = st.text_input(
        _("current_password_label"), type="password", key="chg_pwd_current",
    )
    _new_pwd_1 = st.text_input(
        _("new_password_label"), type="password", key="chg_pwd_new",
    )
    _new_pwd_2 = st.text_input(
        _("confirm_new_password_label"), type="password", key="chg_pwd_confirm",
    )
    if st.button(_("change_password_btn"), key="btn_change_password"):
        if not _current_pwd or not _new_pwd_1:
            st.warning(_("change_password_missing_fields_warning"))
        elif _new_pwd_1 != _new_pwd_2:
            st.warning(_("change_password_mismatch_warning"))
        else:
            try:
                # On vérifie le mot de passe actuel en tentant une
                # authentification par mot de passe (sign_in) — cela
                # renvoie un access_token valide pour l'utilisateur
                # courant, qu'on utilise ensuite pour poser le nouveau
                # mot de passe. Pas de session Supabase persistée
                # ailleurs dans l'app (voir auth_flow.py) : ce jeton
                # n'est utilisé qu'ici, immédiatement, puis jeté.
                _sb_result = tva_sb_auth.sign_in_with_password(_current_user.email, _current_pwd)
                tva_sb_auth.update_user_password(_sb_result.access_token, _new_pwd_1)
                st.success(_("change_password_success"))
            except Exception as _chg_pwd_err:
                _msg = str(_chg_pwd_err)
                if "400" in _msg or "invalid" in _msg.lower() or "credentials" in _msg.lower():
                    st.error(_("change_password_wrong_current"))
                else:
                    st.error(_("change_password_error", error=_msg))

    st.divider()
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
    # RÔLES (2026-08-25) : un lecteur ne peut pas supprimer son propre
    # compte — seul l'administrateur de l'organisation gère les départs
    # (via tva_intracom/ui/admin.py, qui appelle déjà tva_auth.delete_account
    # pour un AUTRE membre). Masqué plutôt que désactivé, comme les autres
    # actions réservées à l'admin dans cette bascule org_id. Protection
    # serveur en complément : voir tva_auth.delete_account.
    if _current_user.role != "admin":
        st.caption(_("delete_account_reader_info"))
        return
    st.warning(_("delete_account_warning"))

    # Double confirmation pour la suppression
    if "confirm_delete_account" not in st.session_state:
        st.session_state["confirm_delete_account"] = False

    if not st.session_state["confirm_delete_account"]:
        if st.button(_("delete_account_btn"), key="btn_pre_delete_account"):
            st.session_state["confirm_delete_account"] = True
            preserve_upload_rerun()
    else:
        st.error(_("delete_account_final_confirmation"))
        _col1, _col2 = st.columns(2)
        if _col1.button(_("cancel_btn"), key="btn_cancel_delete"):
            st.session_state["confirm_delete_account"] = False
            preserve_upload_rerun()
        if _col2.button(_("confirm_delete_btn"), key="btn_confirm_delete", type="primary"):
            try:
                tva_auth.delete_account(_current_user.id, acting_user_id=_current_user.id)
                st.session_state["auth_user"] = None
                st.session_state["manual_logout"] = True
                st.success(_("account_deleted_success"))
                time.sleep(0.5)
                st.rerun()
            except PermissionError as _perm_err:
                st.error(str(_perm_err))
            except Exception as _del_err:
                st.error(f"Erreur lors de la suppression : {_del_err}")


def render_sidebar(auth_ctx, *, pulse_target: str | None = None) -> SidebarResult:
    """Affiche la sidebar complète et retourne les paramètres résolus.

    Args:
        auth_ctx: AuthContext (voir tva_intracom.ui.auth_flow) — fournit
                  current_user, vies_scope_id, stripe_success_url/cancel_url.
        pulse_target: "entreprise" | "vies_ttl" | None — élément de
                  l'onboarding "Lighthouse" à mettre en avant visuellement
                  (voir ui/onboarding.py::compute_pulse_target et
                  ui/theme.py pour le CSS). Calculé au run PRÉCÉDENT :
                  purement décoratif, ne conditionne aucune valeur
                  retournée ni aucun calcul.

    BUGFIX (bascule SIREN suite à conflit de rattachement compte Amazon) :
    `st.session_state["siren_select_box"]` ne peut être écrit QUE avant que
    le widget `st.selectbox(key="siren_select_box")` plus bas dans cette
    fonction n'ait été instancié pour ce run — Streamlit lève
    StreamlitAPIException sinon. `render_account_link_panel()` (appelé bien
    après render_sidebar() dans app.py, une fois les résultats calculés) ne
    peut donc pas écrire directement sur cette clé : il dépose son intention
    dans `_pending_siren_switch`, puis demande un rerun. On consomme ce
    tampon ici, tout en tout début de fonction — donc avant l'instanciation
    du selectbox — ce qui rend l'écriture licite.
    """
    _pending_siren_switch = st.session_state.pop("_pending_siren_switch", None)
    if _pending_siren_switch is not None:
        st.session_state["siren_select_box"] = _pending_siren_switch

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
            format_func=lambda c: f"{country_label(c)} ({c})",
            key="home_country_select",
            help=_("home_country_help"),
        )
        if home_country != _current_home:
            tva_auth.set_home_country(_current_user.id, home_country)
            _current_user.home_country = home_country
            preserve_upload_rerun()

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
        # Sélecteur de plateforme masqué : seul Amazon est géré pour le
        # moment (voir _PLATFORM_OPTIONS, ui/theme.py). On fixe la valeur
        # directement plutôt que d'afficher un choix à une seule option.
        file_format = _PLATFORM_OPTIONS[0]

        # ── Entreprise & Paramètres ───────────────────────────────────────────────
        # Ces paramètres sont liés au SIREN sélectionné et sauvegardés en base.
        ioss_number = ""
        seller_is_importer = False
        apply_fr_under_threshold = False
        ioss_own_number_active = False
        countries_with_vat = ["FR"]
        nom_entreprise = ""
        siren_entreprise = ""
        tva_fr = ""
        local_vat_numbers: dict[str, str] = {}
        enable_vies = True
        on_invalid_behavior = "reclassify"
        convert_fx = True

        if pulse_target == "entreprise":
            with st.container(key="onb_pulse_entreprise"):
                pass
        # Rôles (2026-08-23) : un compte lecteur ne peut pas modifier
        # SIREN/TVA/paramètres entreprise — contrôle UI (champs désactivés)
        # + contrôle serveur (voir billing._require_write_access), la vraie
        # protection restant côté serveur.
        _is_reader = _current_user.role == "reader"

        with st.expander(_("company_header"), expanded=True):
            if _is_reader:
                st.info(_("readonly_account_banner"))
            # ── Section "Période fiscale" retirée (2026-08-21, voir README -
            # évolution.md) : strictement redondante avec le status bar
            # affiché sous le titre (app.py, `_status_bar_period_label`), qui
            # montre déjà la période détectée. `oss_period` reste figé à
            # "__auto__" (aucun sélecteur manuel n'existait réellement ici,
            # seul l'affichage informatif est retiré) — ne pas réintroduire de
            # logique de sélection de période sans concertation avec le
            # cabinet comptable.
            oss_period = "__auto__"

            # ── Rappel de verrouillage (uniquement en création de SIREN) ──
            # Lecture anticipée du statut, AVANT le sélecteur de SIREN rendu
            # plus bas, pour pouvoir afficher le rappel au-dessus du titre
            # "Identité & Paramètres TVA" comme demandé. `_cached_db_read`
            # mémoïse en session_state (TTL 20s, voir plus haut) : cet appel
            # anticipé ne déclenche PAS de requête DB supplémentaire, il
            # réutilise la même valeur que la lecture "officielle" un peu
            # plus bas.
            #
            # Avec au moins un SIREN déjà enregistré, la valeur du
            # sélecteur lue ici est celle du run PRÉCÉDENT (le sélecteur
            # n'a pas encore été redessiné ce run-ci) — même principe que
            # `pulse_target`/`_period_label_shown_by_sidebar` ailleurs dans
            # ce fichier : un rappel visuel tolère un décalage d'un run,
            # aucune donnée fiscale n'en dépend. Le sélecteur est forcé sur
            # le SIREN nouvellement créé juste après l'enregistrement (voir
            # `_new_siren_form_fragment`), ce qui fait disparaître ce rappel
            # dès le run suivant plutôt que de rester affiché indéfiniment.
            try:
                _registered_sirens_early = _cached_db_read(
                    f"sirens_{_current_user.org_id}",
                    lambda: tva_billing.list_registered_sirens(_current_user.org_id),
                )
            except Exception:
                _registered_sirens_early = []
            _new_siren_label_early = _("new_siren_option")
            _creating_new_siren = (
                not _registered_sirens_early
                or st.session_state.get("siren_select_box") == _new_siren_label_early
            )
            if _creating_new_siren:
                st.warning(_("fiscal_fields_lock_warning_new"))

            st.markdown(f"**{_('identity_vat_params_title')}**")
            try:
                _registered_sirens = _cached_db_read(
                    f"sirens_{_current_user.org_id}",
                    lambda: tva_billing.list_registered_sirens(_current_user.org_id),
                )
                _siren_quota_status = _cached_db_read(
                    f"siren_quota_{_current_user.org_id}",
                    lambda: tva_billing.get_siren_quota_status(_current_user.org_id),
                )
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
            # RECHERCHE (2026-08-25) : st.selectbox filtre déjà nativement
            # les options à la frappe (composant BaseWeb Select) — pas
            # besoin d'un champ de recherche séparé, qui ajouterait un état
            # supplémentaire à synchroniser. Le `help` rend simplement cette
            # capacité découvrable pour un cabinet avec de nombreux clients.
            # Liste elle-même triée par ordre alphabétique côté
            # list_registered_sirens() (billing.py).
            _siren_choice = st.selectbox(
                _("siren_client_label"),
                options=_siren_options + [_new_siren_label],
                index=0 if _siren_options else 0,
                format_func=lambda v: _siren_label_by_value.get(v, v),
                key="siren_select_box",
                help=_("siren_select_search_help"),
            ) if _siren_options else _new_siren_label

            if _siren_choice == _new_siren_label:
                _can_add_siren, _siren_quota_msg = (True, "")
                try:
                    _can_add_siren, _siren_quota_msg = tva_billing.can_register_new_siren(_current_user.org_id)
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
                    ioss_own_number_active = _registered_sirens[0].get("ioss_own_number_active") or False
                    if _registered_sirens[0].get("oss_threshold_exceeded_prev_year"):
                        apply_fr_under_threshold = False
                    _countries_raw = _registered_sirens[0].get("countries_with_vat") or "FR"
                    countries_with_vat = [c.strip().upper() for c in _countries_raw.split(",") if c.strip()]
                    try:
                        local_vat_numbers = json.loads(_registered_sirens[0].get("vat_numbers_json") or "{}")
                    except Exception:
                        local_vat_numbers = {}
                else:
                    # Valeurs EFFECTIVES par défaut tant qu'aucun SIREN n'a
                    # été enregistré — indépendantes des widgets ci-dessous
                    # (isolés dans _new_siren_form_fragment) : évite qu'une
                    # simple frappe dans le formulaire de création ne
                    # déclenche un rerun complet de toute la page.
                    nom_entreprise, siren_entreprise, tva_fr = "", "", ""
                    ioss_number, seller_is_importer, apply_fr_under_threshold = "", False, False
                    ioss_own_number_active = False
                    countries_with_vat, local_vat_numbers = ["FR"], {}

                    _new_siren_form_fragment(
                        current_user=_current_user, home_country=home_country,
                        siren_options=_siren_options,
                    )
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

                _ioss_val = _match.get("ioss_number") or ""
                _countries_raw = _match.get("countries_with_vat") or "FR" if _match else "FR"
                _default_vat_countries = [c.strip().upper() for c in _countries_raw.split(",") if c.strip()]

                ioss_number = _ioss_val

                # ── Pays où la TVA locale est enregistrée : remonté juste
                # sous l'identité, au-dessus d'IOSS/DDP/seuil OSS. Priorité
                # fiscale : ces immatriculations locales priment sur le
                # régime DDP et les autres réglages. Zone d'AJOUT, pas de
                # gestion de stock (voir README - évolution.md) — les pays
                # déjà verrouillés (numéro enregistré) sont résumés en une
                # seule ligne compacte, le multiselect ne sert qu'à ajouter
                # un nouveau pays pas encore enregistré (ou à en retirer un,
                # verrouillé ou non, de la liste active).
                # BUGFIX (2026-08-26) : la clé de ce widget était statique
                # ("vat_countries_edit"), partagée par TOUS les SIREN. Une
                # fois qu'une valeur existe dans st.session_state pour cette
                # clé, Streamlit ignore `default=` aux runs suivants et
                # réaffiche la valeur mémorisée — donc changer de SIREN (ou
                # revenir d'une création de SIREN) réaffichait la liste de
                # pays du SIREN précédemment actif au lieu de celle du SIREN
                # sélectionné. La clé est désormais scopée par `_siren_choice`
                # pour forcer Streamlit à traiter chaque SIREN comme un
                # widget distinct, ce qui réapplique bien `default=` (donc
                # les données réelles de CE SIREN) à chaque changement.
                countries_with_vat = st.multiselect(
                    _("local_vat_countries_label"),
                    options=sorted(list(EU_COUNTRIES)),
                    default=_default_vat_countries,
                    key=f"vat_countries_edit_{_siren_choice}",
                    disabled=_is_reader,
                )
                _locked_selected = sorted(c for c in countries_with_vat if _existing_vats.get(c))
                _new_vat_countries = sorted(c for c in countries_with_vat if not _existing_vats.get(c))
                if _locked_selected:
                    st.caption(
                        "🔒 " + " · ".join(f"{c} {_existing_vats[c]}" for c in _locked_selected)
                        + f" — {_('fiscal_field_locked_note')}"
                    )

                # Saisie des numéros de TVA pour les pays NOUVELLEMENT ajoutés
                # (pas encore verrouillés) : rendue ICI, juste sous le
                # multiselect, plutôt que dans `_edit_siren_form_fragment`
                # (rendu bien plus bas, après IOSS/DDP/seuil OSS) — c'était la
                # cause du champ "Numéro de TVA FR" retrouvé tout en bas de
                # panneau alors que le pays est sélectionné ici. Ces champs
                # restent des `st.text_input` normaux (hors fragment) : leur
                # valeur est lue par `_edit_siren_form_fragment` via
                # `st.session_state[key]` au moment de l'enregistrement (la
                # clé du widget), donc aucune perte de saisie malgré le
                # découplage — voir commentaire dans ce fragment.
                # BUGFIX (2026-08-26) : clé scopée par SIREN pour la même
                # raison que le multiselect ci-dessus — sinon un brouillon de
                # numéro de TVA tapé pour le SIREN A pouvait réapparaître en
                # changeant vers le SIREN B si celui-ci a le même pays à
                # compléter.
                if _new_vat_countries:
                    st.caption(_("local_vat_numbers_caption"))
                    for _ccode in _new_vat_countries:
                        st.text_input(_("vat_number_for", country=_ccode),
                                      key=f"vat_num_edit_{_siren_choice}_{_ccode}",
                                      placeholder=f"ex: {_ccode}123456789",
                                      disabled=_is_reader)

                tva_fr = _tva_fr_fixed
                local_vat_numbers = {c: _existing_vats[c] for c in _locked_selected}

                st.markdown("---")
                st.markdown(f"**{_('fiscal_params_title')}**")

                # ── Toggles fiscaux : LIVE, hors fragment ──────────────────────
                # BUGFIX (2026-08-21, voir README - évolution.md) : ces widgets
                # vivaient auparavant dans _edit_siren_form_fragment (isolé).
                # Un fragment ne redessinant que lui-même, cocher/décocher ici
                # n'avait jamais d'effet sur `_cache_key` (app.py) tant que
                # "Enregistrer les modifications" n'était pas cliqué : IOSS,
                # DDP et seuil OSS semblaient ne "rien faire". Rendus ici, en
                # dehors du fragment, leur valeur courante est immédiatement
                # celle utilisée pour le calcul — un clic déclenche un rerun
                # complet (coût attendu et voulu : ces réglages changent le
                # résultat fiscal, contrairement à la frappe d'un nom ou d'un
                # numéro de TVA, qui reste isolée dans le fragment).
                # RÔLES (2026-08-24) : ces toggles sont LIVE (hors fragment,
                # voir commentaire ci-dessus) — sans `disabled=_is_reader`,
                # un compte lecteur pouvait les basculer et voir le calcul
                # fiscal affiché changer immédiatement, sans passer par
                # "Enregistrer" (lui-même déjà désactivé pour les lecteurs).
                # Contrairement aux pays TVA locale (juste au-dessus, sans
                # grand impact tant que non enregistré), CES réglages
                # (IOSS/DDP/seuil OSS) changent directement le résultat
                # fiscal affiché/exporté pendant la session — critique à
                # verrouiller, pas seulement à la sauvegarde.
                # BUGFIX (2026-08-26) : ces 4 toggles utilisaient des clés
                # STATIQUES ("ioss_own_active_view", "ddp_view", "oss_thr_view",
                # "oss_thr_prevyear_view"), partagées par tous les SIREN. Comme
                # pour le multiselect ci-dessus, `value=` est ignoré par
                # Streamlit dès qu'une valeur existe déjà en session_state
                # pour cette clé — donc en changeant de SIREN, les toggles
                # affichés pouvaient rester ceux du SIREN précédemment
                # sélectionné au lieu de refléter `match` (l'état réel en base
                # pour LE SIREN affiché). Impact critique signalé : un compte
                # lecteur changeant de SIREN pouvait voir un DDP ou un seuil
                # 10k€ qui ne correspondait pas au SIREN réellement affiché,
                # sans qu'il y ait moyen de s'en rendre compte à l'écran.
                # Les clés sont désormais scopées par `_siren_choice`, ce qui
                # force Streamlit à réappliquer `value=` (donc l'état réel en
                # base) à chaque changement de SIREN.
                ioss_own_number_active = False
                if _ioss_val:
                    ioss_own_number_active = st.toggle(
                        _("ioss_own_number_active_label"),
                        value=_match.get("ioss_own_number_active") or False if _match else False,
                        key=f"ioss_own_active_view_{_siren_choice}",
                        help=_("ioss_own_number_active_help", platform="Amazon"),
                        disabled=_is_reader,
                    )

                seller_is_importer = st.toggle(
                    _("ddp_label"),
                    value=_match.get("seller_is_importer") or False if _match else False,
                    key=f"ddp_view_{_siren_choice}",
                    disabled=_is_reader,
                )
                apply_fr_under_threshold = st.toggle(
                    _("oss_threshold_apply_label", country=home_country, limit=_oss_limit_label(home_country)),
                    value=_match.get("apply_fr_under_threshold") or False if _match else False,
                    key=f"oss_thr_view_{_siren_choice}",
                    disabled=_is_reader,
                )
                oss_threshold_exceeded_prev_year = st.toggle(
                    _("oss_threshold_prev_year_label"),
                    value=_match.get("oss_threshold_exceeded_prev_year") or False if _match else False,
                    key=f"oss_thr_prevyear_view_{_siren_choice}",
                    help=_("oss_threshold_prev_year_help"),
                    disabled=_is_reader,
                )
                if oss_threshold_exceeded_prev_year and apply_fr_under_threshold:
                    st.caption("⚠️ " + _("oss_threshold_prev_year_help"))
                    apply_fr_under_threshold = False

                _edit_siren_form_fragment(
                    current_user=_current_user, home_country=home_country,
                    match=_match, siren_entreprise=siren_entreprise, nom_entreprise=nom_entreprise,
                    tva_fr_fixed=_tva_fr_fixed, existing_vats=_existing_vats, ioss_val=_ioss_val,
                    seller_is_importer=seller_is_importer,
                    apply_fr_under_threshold=apply_fr_under_threshold,
                    oss_threshold_exceeded_prev_year=oss_threshold_exceeded_prev_year,
                    ioss_own_number_active=ioss_own_number_active,
                    countries_with_vat=countries_with_vat,
                    new_vat_countries=_new_vat_countries,
                )


                # Option de retrait du SIREN (toujours visible si déjà enregistré)
                # RÔLES (2026-08-25) : masqué pour un compte lecteur (`_is_reader`)
                # plutôt que désactivé — un lecteur n'a rien à voir/faire ici,
                # cette action restant du ressort de l'administrateur de
                # l'organisation. `_require_write_access` (billing.py) reste la
                # vraie protection côté serveur ; ce masquage évite seulement le
                # PermissionError non catché qui remontait jusqu'ici en UI.
                if _match and not _is_reader:
                    st.divider()
                    if _match.get("pending_removal_at"):
                        import datetime as _dt
                        _eff_date = _dt.datetime.fromtimestamp(_match["pending_removal_at"]).strftime("%d/%m/%Y")
                        st.warning(_("removal_pending", date=_eff_date))
                        if st.button(_("cancel_removal_btn"), key=f"btn_cancel_removal_{siren_entreprise}", width="stretch"):
                            tva_billing.cancel_siren_removal(_current_user.org_id, _current_user.id, siren_entreprise)
                            _invalidate_db_cache(f"sirens_{_current_user.org_id}")
                            _invalidate_db_cache(f"siren_quota_{_current_user.org_id}")
                            preserve_upload_rerun()
                    else:
                        if st.button(_("remove_siren_btn"), key=f"btn_remove_entreprise_{siren_entreprise}",
                                     help=_("remove_siren_help"),
                                     width="stretch"):
                            # On autorise le retrait même si c'est le dernier (l'utilisateur peut vouloir arrêter)
                            try:
                                _eff = tva_billing.request_siren_removal(_current_user.org_id, _current_user.id, siren_entreprise)
                            except PermissionError as _lock_err:
                                # Statut "Achat" (2026-09-05) : SIREN verrouillé pour un
                                # compte PAYG n'ayant jamais souscrit d'abonnement.
                                st.error(str(_lock_err))
                            else:
                                _invalidate_db_cache(f"sirens_{_current_user.org_id}")
                                _invalidate_db_cache(f"siren_quota_{_current_user.org_id}")
                                import datetime as _dt
                                if _eff <= time.time() + 5:
                                    st.success(_("remove_success"))
                                else:
                                    st.info(_("remove_scheduled", date=_dt.datetime.fromtimestamp(_eff).strftime('%d/%m/%Y')))
                                preserve_upload_rerun()

        # ── Abonnements & forfaits ────────────────────────────────────────────────
        # RÔLES (2026-08-25) : bloc entier masqué pour un compte lecteur — abonnement
        # Stripe, crédits PAYG et grille tarifaire sont désormais partagés au niveau
        # de l'organisation (org_id) ; un lecteur n'a ni le droit de les modifier
        # (portail Stripe, retrait SIREN cabinet) ni besoin de les consulter — seul
        # l'administrateur de l'organisation gère ces réglages.
        if tva_auth.is_admin(_current_user):
            with st.expander(_("billing_header"), expanded=True):
                _sub_status = None
                try:
                    _sub_status = _cached_db_read(
                        f"sub_status_{_current_user.org_id}",
                        lambda: tva_billing.get_subscription_status(_current_user.org_id),
                    )
                except Exception as _sub_err:
                    st.caption(_("sub_status_unavailable", error=_sub_err))

                _plan_label = {"business": _("plan_pro"), "cabinet": _("plan_cabinet")}.get(
                    _sub_status.plan if _sub_status else None, _sub_status.plan if _sub_status else "—")

                # Statut "Achat" (2026-09-05) : compte n'ayant jamais souscrit
                # d'abonnement mais ayant déjà effectué un achat PAYG — affiché
                # distinctement d'un compte gratuit n'ayant jamais payé.
                _account_status = None
                if not (_sub_status and _sub_status.active):
                    try:
                        _account_status = _cached_db_read(
                            f"account_status_{_current_user.org_id}",
                            lambda: tva_billing.get_account_status(_current_user.org_id),
                        )
                    except Exception:
                        _account_status = None
                    if _account_status == tva_billing.ACCOUNT_STATUS_ACHAT:
                        st.info(f"**{_('plan_achat')}** — {_('plan_achat_desc')}")
                _interval_label = {"month": _("interval_monthly"), "year": _("interval_yearly")}.get(
                    _sub_status.billing_interval if _sub_status else None, "")

                if _sub_status and _sub_status.active:
                    st.success(_("sub_active_msg", plan=_plan_label, interval=_interval_label)
                               + (f" — {_sub_status.siren_quantity} SIREN" if _sub_status.plan == "cabinet" else ""))

                    # Downgrade différé (Subscription Schedule Stripe, 2026-08-16) :
                    # un changement de plan à venir en fin de période est signalé
                    # ici, distinctement du plan actif ci-dessus qui reste
                    # inchangé jusqu'à la date effective.
                    if _sub_status.scheduled_plan and _sub_status.scheduled_change_at:
                        _sched_plan_label = {"business": _("plan_pro"), "cabinet": _("plan_cabinet")}.get(
                            _sub_status.scheduled_plan, _sub_status.scheduled_plan)
                        import datetime as _dt
                        st.info(
                            _("sub_scheduled_change_msg",
                              plan=_sched_plan_label,
                              date=_dt.datetime.fromtimestamp(_sub_status.scheduled_change_at).strftime("%d/%m/%Y"))
                        )

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
                                if _c2.button(_("remove_btn"), key=f"btn_remove_{_r['siren']}", width="stretch"):
                                    _eff = tva_billing.request_siren_removal(_current_user.org_id, _current_user.id, _r["siren"])
                                    _invalidate_db_cache(f"sirens_{_current_user.org_id}")
                                    _invalidate_db_cache(f"siren_quota_{_current_user.org_id}")
                                    import datetime as _dt
                                    st.info(_("remove_scheduled", date=_dt.datetime.fromtimestamp(_eff).strftime('%d/%m/%Y')))
                                    preserve_upload_rerun()

                    # Session de portail Stripe générée UNIQUEMENT au clic — avant
                    # ce correctif, `create_billing_portal_session()` (un appel
                    # réseau à l'API Stripe, pas juste une lecture DB) était
                    # exécuté à chaque rerun de toute l'app, que l'utilisateur
                    # ait ou non l'intention de gérer son abonnement. Même
                    # pattern que le bouton PAYG ci-dessous (cache de l'URL en
                    # session_state entre le 1er clic qui la génère et le 2e qui
                    # y navigue réellement).
                    if st.button(_("manage_sub_stripe_btn"), key="btn_open_billing_portal"):
                        try:
                            st.session_state["_billing_portal_url"] = tva_billing.create_billing_portal_session(
                                _current_user.org_id,
                                return_url=_stripe_cancel_url(),
                                acting_user_id=_current_user.id,
                            )
                        except Exception as _portal_err:
                            st.session_state.pop("_billing_portal_url", None)
                            st.error(_("sub_status_unavailable", error=_portal_err))
                    if st.session_state.get("_billing_portal_url"):
                        st.link_button(_("continue_to_payment_btn"), st.session_state["_billing_portal_url"])

                # ── Crédits PAYG (Achats uniques) ─────────────────────────────────────
                try:
                    _credits = _cached_db_read(
                        f"purchased_credits_{_current_user.org_id}",
                        lambda: tva_billing.list_purchased_credits(_current_user.org_id),
                    )
                    if _credits:
                        st.markdown("---")
                        st.markdown(f"**{_('unlocked_periods_title')}**")
                        for _c in _credits:
                            from datetime import datetime as _dt
                            _at = _dt.fromtimestamp(_c["at"]).strftime("%d/%m/%Y")
                            st.caption(f"✅ **{_c['period']}** — {_('purchased_at', date=_at)}")
                except Exception as _credit_err:
                    st.caption(_("purchase_history_unavailable", error=_credit_err))
                if True:
                    # CORRECTIF 2026-09-01 (audit) : tout ce qui suit à cette
                    # indentation (bannière Premium, grille tarifaire, boutons
                    # d'abonnement/achat ponctuel — plusieurs blocs, pas un
                    # seul) vivait auparavant dans le `else:` du try/except
                    # ci-dessus, dont le seul rôle est de lire l'historique des
                    # crédits déjà achetés. Une panne transitoire de CETTE
                    # lecture seule (aléa Supabase, réseau...), sans rapport
                    # avec le statut d'abonnement lui-même, faisait donc
                    # disparaître TOUT le chemin de conversion pour un compte
                    # non premium, sans aucun message expliquant pourquoi
                    # (juste "historique d'achat indisponible" puis plus
                    # rien). `if True:` (plutôt qu'un `else:`) rend ce bloc
                    # inconditionnel vis-à-vis du try/except, SANS ré-indenter
                    # les ~230 lignes qui suivent (risque de transcription sur
                    # un bloc de cette taille) : il ne dépend plus que de
                    # `_sub_status`, déjà résolu plus haut.
                    if not (_sub_status and _sub_status.active):
                        if _sub_status and _sub_status.status:
                            # Abonnement existant mais inactif (annulé/expiré) : état actuel
                            # affiché pour information, sans historique complet.
                            st.warning(_("last_sub_msg", plan=_plan_label, status=_sub_status.status)
                                       + (f" ({_('expired_at', date=__import__('datetime').datetime.fromtimestamp(_sub_status.current_period_end).strftime('%d/%m/%Y'))})"
                                          if _sub_status.current_period_end else ""))

                        # ── Bannière d'incitation Premium (utilisateurs gratuits) ───────
                        st.markdown(
                            f"""
                            <div style="
                                background-color: #EEEDFE;
                                border-radius: 12px;
                                padding: 14px 16px;
                                margin-bottom: 12px;
                            ">
                                <p style="margin: 0 0 4px; font-size: 13px; font-weight: 600; color: #26215C;">
                                    {_("premium_banner_title")}
                                </p>
                                <p style="margin: 0; font-size: 12px; color: #3C3489;">
                                    {_("premium_banner_body")}
                                </p>
                            </div>
                            """,
                            unsafe_allow_html=True,
                        )
                        st.caption(_("billing_caption"))

                        with st.expander(_("pricing_grid_expander"), expanded=False):
                            # NOTE (2026-08-30, correctif) : ces deux appels passaient
                            # auparavant par `_cached_db_read` (cache session_state,
                            # TTL 20s) — un TTL bien trop court pour des données Stripe
                            # peu volatiles, alors que `get_pricing_grid` et
                            # `list_available_promotions` ont DÉJÀ leur propre cache
                            # `@st.cache_data(ttl=600)` dans billing.py. Comme le corps
                            # d'un `st.expander` s'exécute à CHAQUE rerun complet même
                            # replié, le TTL 20s provoquait un aller-retour Stripe
                            # (Charge/PromotionCode/Coupon/Price, ~7 requêtes) à chaque
                            # rerun complet espacé de plus de 20s (ex. juste avant le
                            # traitement d'un upload) pour tout compte non abonné. On
                            # appelle désormais directement les fonctions déjà cachées
                            # à 600s, sans passer par le cache 20s.
                            try:
                                _grid = tva_billing.get_pricing_grid(_current_user.org_id)
                            except Exception as _grid_err:
                                _grid = None
                                st.caption(_("pricing_grid_unavailable", error=_grid_err))

                            if _grid:
                                try:
                                    _promotions = tva_billing.list_available_promotions(_current_user.org_id)
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
                                    # BUGFIX (2026-09-04) : la clé de cache incluait
                                    # seulement la période — voir même correctif dans
                                    # ui/billing_gate.py::get_payg_checkout_url. Le
                                    # SIREN doit aussi être scellé dans la metadata
                                    # Stripe pour que le crédit octroyé ne débloque
                                    # que ce SIREN (voir create_payg_checkout_session/
                                    # billing.has_export_credit).
                                    _payg_cache_key = f"_stripe_checkout_url::{_detected_period_for_payg}::{siren_entreprise}"
                                    if _payg_cache_key not in st.session_state:
                                        st.session_state[_payg_cache_key] = tva_billing.create_payg_checkout_session(
                                            org_id=_current_user.org_id, acting_user_id=_current_user.id,
                                            email=_current_user.email,
                                            period_label=_detected_period_for_payg,
                                            success_url=_stripe_success_url("export_ok=1"),
                                            cancel_url=_stripe_cancel_url(),
                                            siren=siren_entreprise,
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
                                    org_id=_current_user.org_id, acting_user_id=_current_user.id,
                                    email=_current_user.email,
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
                                    org_id=_current_user.org_id, acting_user_id=_current_user.id,
                                    email=_current_user.email,
                                    plan="cabinet", interval=_interval_code,
                                    quantity=int(_cabinet_qty),
                                    success_url=_stripe_success_url("export_ok=1"),
                                    cancel_url=_stripe_cancel_url(),
                                )
                                st.link_button(_("continue_to_payment_btn"), _url)
                            except Exception as _cab_err:
                                st.error(f"Erreur : {_cab_err}")

        # ── Catalogue Produits ────────────────────────────────────────────────────
        # Fonctionnalité avancée (taux réduits par ASIN) — masquée en mode
        # Simple (voir tva_intracom/ui/display_mode.py).
        #
        # BUGFIX (2026-08-21) : `asin_to_category` alimente `_cache_key`
        # (calc_key, voir app.py) — le fixer à {} par défaut à chaque fois
        # que ce bloc n'est pas rendu (mode Simple) changeait `calc_key`
        # dès qu'on quittait le mode Détaillé après avoir chargé un
        # catalogue, ce qui déclenchait un recalcul complet à CHAQUE
        # bascule de mode (et faisait perdre le catalogue déjà chargé,
        # widget file_uploader non rendu = state non conservé sans clé
        # explicite). Le dict parsé est désormais mis en cache dans
        # session_state, relu ici quel que soit le mode, pour que le
        # toggle Simple/Détaillé ne touche plus jamais au calcul.
        asin_to_category = st.session_state.get("_asin_catalog_data", {})
        if is_detailed():
            with st.expander(_("catalog_header"), expanded=False):
                catalog_file = st.file_uploader(_("catalog_upload"),
                                                type=["csv","tsv","txt"],
                                                help=_("catalog_help"),
                                                key="catalog_file_uploader")
                if catalog_file is not None:
                    _size_mb = catalog_file.size / (1024 * 1024)
                    if _size_mb > _MAX_CATALOG_MB:
                        st.error(_("catalog_too_large", size_mb=_size_mb, max_mb=_MAX_CATALOG_MB))
                    else:
                        try:
                            _parsed_catalog = _parse_catalog_bytes(catalog_file.getvalue(), catalog_file.name)
                            if _parsed_catalog:
                                asin_to_category = _parsed_catalog
                                st.session_state["_asin_catalog_data"] = asin_to_category
                                st.success(_("catalog_success", count=len(asin_to_category)))
                        except Exception as e:
                            st.error(_("catalog_error", error=e))

        # ── Cache VIES ────────────────────────────────────────────────────────────
        # RÔLES (2026-08-25) : bloc entier masqué pour un compte lecteur — le TTL
        # (partagé par toute l'organisation, modifiable par l'admin seul), les stats
        # de volumétrie VIES et le certificat PDF "compte entier" ne concernent/ne
        # regardent que l'administrateur de l'organisation. Un lecteur garde accès
        # au certificat VIES par fichier importé via l'onglet VIES (vies_ui.py).
        if tva_auth.is_admin(_current_user):
            # BUGFIX (2026-08-22) : la durée de validité du cache VIES (slider
            # TTL) est une donnée que l'utilisateur doit voir/régler dès la
            # prise en main (checklist d'onboarding) — l'expander lui-même
            # reste donc toujours visible, y compris en mode Simple. Seuls les
            # réglages avancés (stats détaillées, purge, certificat PDF)
            # restent réservés au mode Détaillé. N'alimente aucun champ de
            # SidebarResult : masquage partiel sans risque de variable non
            # définie plus bas.
            if pulse_target == "vies_ttl":
                with st.container(key="onb_pulse_vies"):
                    pass
            with st.expander(_("cache_vies_header"), expanded=True):
                try:
                    _cs = vies_cache_stats(_vies_scope_id)
                    # Plafond réduit de 365 à 30 jours (2026-08-23) : une donnée
                    # VIES valide il y a plusieurs mois n'a plus de valeur
                    # probante fiscalement. `value` est bornée au même plafond
                    # pour ne pas planter st.slider() si un scope avait déjà une
                    # valeur > 30 enregistrée avant ce changement (le TTL réel
                    # stocké n'est PAS modifié tant que l'utilisateur ne
                    # retouche pas le slider — seul l'affichage est clampé).
                    _ttl_max_days = 30
                    _ttl_current = min(_cs["ttl_days"], _ttl_max_days)
                    _ttl_days = st.slider(_("ttl_cache_slider"), min_value=1, max_value=_ttl_max_days,
                                          value=_ttl_current, step=1,
                                          help=_("ttl_cache_help"))
                    if _ttl_days != _cs["ttl_days"]:
                        set_cache_ttl(_vies_scope_id, _ttl_days, acting_user_id=_current_user.id)
                        vies_cache_stats.clear()
                        preserve_upload_rerun()
                    if is_detailed():
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
                                n = purge_expired_cache(_vies_scope_id, acting_user_id=_current_user.id)
                                st.success(_("purge_success", count=n))
                                preserve_upload_rerun()

                        # ── Certificat de Validité VIES (PDF) ──
                        # Bouton de génération globale uniquement
                        st.divider()
                        st.markdown(f"**{_('vies_certificate_expander')}**")
                        st.caption(_("vies_certificate_caption"))
                        _cert_history_mode_sb = st.checkbox(
                            _("vies_certificate_history_checkbox"),
                            key="vies_cert_history_mode_sidebar",
                        )
                        if st.button(_("vies_certificate_btn"), key="btn_gen_vies_certificate_sidebar"):
                            try:
                                if _cert_history_mode_sb:
                                    from tva_intracom.vies_engine import get_scope_vies_history_flat
                                    from tva_intracom.vies_certificate import generate_vies_history_pdf
                                    _history_rows = get_scope_vies_history_flat(_vies_scope_id, full_vats=None)
                                    _pdf_bytes = generate_vies_history_pdf(
                                        _history_rows,
                                        company_name=nom_entreprise or _("default_company_name"),
                                        siren=siren_entreprise or "",
                                        scope_id=_vies_scope_id,
                                        period_label=_("vies_certificate_full_history"),
                                        country_label_fn=country_label,
                                        translator=_,
                                    )
                                    _is_empty_sb = not _history_rows
                                else:
                                    from tva_intracom.vies_engine import get_scope_vies_snapshot
                                    from tva_intracom.vies_certificate import generate_vies_certificate_pdf
                                    _snapshot = get_scope_vies_snapshot(_vies_scope_id)
                                    _pdf_bytes = generate_vies_certificate_pdf(
                                        _snapshot,
                                        company_name=nom_entreprise or _("default_company_name"),
                                        siren=siren_entreprise or "",
                                        scope_id=_vies_scope_id,
                                        period_label=_("vies_certificate_full_history"),
                                        country_label_fn=country_label,
                                        translator=_,
                                    )
                                    _is_empty_sb = not _snapshot
                                st.session_state["_vies_certificate_pdf_sidebar"] = _pdf_bytes
                                st.session_state["_vies_certificate_history_mode_sidebar"] = _cert_history_mode_sb
                                if _is_empty_sb:
                                    st.info(_("vies_certificate_history_empty_info") if _cert_history_mode_sb else _("vies_certificate_empty_info"))
                            except Exception as _cert_err:
                                st.error(_("vies_certificate_error", error=_cert_err))

                        if st.session_state.get("_vies_certificate_pdf_sidebar"):
                            _suffix_sb = "complet_historique" if st.session_state.get("_vies_certificate_history_mode_sidebar") else "complet"
                            st.download_button(
                                _("vies_certificate_dl_btn"),
                                data=st.session_state["_vies_certificate_pdf_sidebar"],
                                file_name=_("vies_certificate_filename", company=f"{nom_entreprise or 'Export'}_{_suffix_sb}"),
                                mime="application/pdf",
                                type="primary",
                                width="stretch",
                            )
                except Exception as _e:
                    st.caption(_("cache_unavailable", error=_e))

        # ── Paramètres du fichier ─────────────────────────────────────────────────
        # "utf-8" couvre l'immense majorité des exports Amazon — réglage
        # avancé masqué en mode Simple.
        #
        # BUGFIX (2026-08-21) : même classe de bug que le catalogue
        # ci-dessus — `encoding` alimente `parse_key` (voir app.py) ; le
        # re-fixer à "utf-8" par défaut à chaque run où l'expander n'est
        # pas rendu aurait fait perdre un encodage explicitement choisi
        # (ex. "latin-1") dès la bascule vers le mode Simple, et forcé un
        # RE-PARSING complet des fichiers à chaque bascule de mode. La
        # valeur choisie est donc mise en cache dans session_state.
        encoding = st.session_state.get("_file_encoding_choice", "utf-8")
        if is_detailed():
            with st.expander(_("file_params_header"), expanded=False):
                encoding = st.selectbox(_("file_encoding"), ["utf-8","latin-1","cp1252"],
                                        index=["utf-8","latin-1","cp1252"].index(encoding),
                                        key="file_encoding_select")
                st.session_state["_file_encoding_choice"] = encoding

        # ── Compte & Confidentialité ──────────────────────────────────────────────
        # Sorti du corps de la sidebar (validé) : accessible via un bouton
        # discret + st.dialog plutôt qu'un expander permanent — mot de passe,
        # export RGPD, suppression de compte n'ont pas leur place dans un
        # panneau consulté à chaque session de calcul. Contenu inchangé,
        # déplacé tel quel dans _render_account_dialog() (voir plus haut
        # dans ce fichier).
        if st.button(_("account_privacy_header"), key="btn_open_account_dialog", width="stretch"):
            _render_account_dialog(_current_user)

        # ── Administration (rôles & whitelist d'e-mails) ─────────────────────────
        # Réservé aux comptes admin — voir tva_intracom/ui/admin.py. Le
        # contrôle d'accès se fait ici (le bouton n'est simplement pas
        # affiché aux lecteurs) ; depuis le 2026-08-26, les fonctions serveur
        # appelées par admin.py (set_user_role, add/remove_allowed_email)
        # revérifient elles-mêmes is_admin(acting_user) en défense en
        # profondeur — voir leurs docstrings dans auth.py.
        if tva_auth.is_admin(_current_user):
            if st.button(_("admin_module_header"), key="btn_open_admin_dialog", width="stretch"):
                from tva_intracom.ui.admin import render_admin_dialog
                render_admin_dialog(_current_user)

        # ── Support ───────────────────────────────────────────────────────────────
        st.divider()
        st.markdown(f"**{_('contact_support')}**")
        st.caption("support@tvacalculator.eu")
        st.markdown(f"[{_('website_label')}](https://www.tvacalculator.eu/)")

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
        ioss_own_number_active=ioss_own_number_active,
    )