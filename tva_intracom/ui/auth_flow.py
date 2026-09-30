"""Flux d'authentification de l'application (extrait tel quel de app.py).

Regroupe :
  - l'instanciation du gestionnaire de cookies (extra_streamlit_components)
    et la purge ponctuelle du cache VIES mal préfixé (une fois par session) ;
  - la restauration de session via cookie ;
  - la consommation du lien magique (magic link) envoyé par e-mail ;
  - la migration d'un ancien lien `?session_token=` vers cookie ;
  - l'écran de connexion (bypass dev local, magic link, bouton Amazon) —
    bloque l'exécution (`st.stop()`) tant que l'utilisateur n'est pas
    authentifié, exactement comme le comportement d'origine ;
  - le bandeau "Connecté : ... / Déconnexion" une fois authentifié.

Usage dans app.py :

    from tva_intracom.ui.auth_flow import ensure_cookie_manager, run_auth_flow

    cookie_manager = ensure_cookie_manager()
    auth_ctx = run_auth_flow(cookie_manager)
    # auth_ctx.current_user, auth_ctx.app_base_url, auth_ctx.vies_scope_id
    # auth_ctx.stripe_success_url(...), auth_ctx.stripe_cancel_url()
"""

from __future__ import annotations

import base64
import hashlib
import html
import logging
import secrets
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Callable

import extra_streamlit_components as stx
import streamlit as st

from tva_intracom import auth as tva_auth
from tva_intracom import auth_supabase as tva_sb_auth
from tva_intracom import billing as tva_billing
from tva_intracom.i18n import _
from tva_intracom.vies_engine import (
    resolve_scope_id as _vies_resolve_scope_id,
    purge_malformed_entries as _vies_purge_malformed_entries,
)
from ..config import get_secret


_DB_CACHE_TTL_SECONDS = 20

# Clé de session du message « connexion refusée » (voir _finalize_login).
_LOGIN_ERROR_KEY = "_login_error"


def _cached_db_read(cache_key: str, fetch_fn, force: bool = False):
    """Copie volontaire de `sidebar.py::_cached_db_read` (même schéma de clé
    `_sb_dbcache_{cache_key}`, même TTL) plutôt qu'un import croisé entre
    modules UI : les deux se partagent naturellement le même cache en
    session_state (clé identique), sans lecture DB en double."""
    _skey = f"_sb_dbcache_{cache_key}"
    _cached = st.session_state.get(_skey)
    _now = time.time()
    if (not force) and _cached is not None and (_now - _cached[0]) < _DB_CACHE_TTL_SECONDS:
        return _cached[1]
    _value = fetch_fn()
    st.session_state[_skey] = (_now, _value)
    return _value


@dataclass
class AuthContext:
    """Contexte d'authentification résolu, transmis au reste de l'app."""

    current_user: Any                 # tva_intracom.auth.User
    cookie_manager: "stx.CookieManager"
    app_base_url: str
    vies_scope_id: str

    def stripe_success_url(self, extra_qs: str = "") -> str:
        """URL de retour post-paiement Stripe.

        BUGFIX (2026-09-09, sécurité) : embarquait auparavant le jeton de
        session (`session_token`) en clair dans l'URL. Ce jeton transite
        alors par le domaine Stripe (checkout.stripe.com), se retrouve dans
        l'historique du navigateur et peut fuiter via les en-têtes Referer
        ou les logs de journalisation tiers — exactement l'équivalent d'un
        vol de session s'il est intercepté. Il est inutile : le cookie
        `tva_session_token` (déjà posé, 30 jours, voir run_auth_flow) est
        renvoyé automatiquement par le navigateur au retour sur ce domaine
        et restaure la session (voir st.context.cookies, contrôlé EN
        PREMIER avant tout repli sur un éventuel paramètre d'URL)."""
        return f"{self.app_base_url}/?{extra_qs}" if extra_qs else f"{self.app_base_url}/"

    def stripe_cancel_url(self) -> str:
        """Voir stripe_success_url : même correctif (retrait du jeton de
        session de l'URL, inutile grâce au cookie persistant)."""
        return f"{self.app_base_url}/"


_TRUSTED_HOST_SUFFIXES = (
    ".streamlit.app",
    ".up.railway.app",
    ".railway.app",
)


def _is_trusted_host(host: str) -> bool:
    """Vérifie que le header Host correspond à un déploiement connu de
    l'application, avant de s'en servir pour construire une URL de
    redirection (voir note dans _resolve_app_base_url). N'accepte pas de
    port arbitraire ni d'userinfo (host:port ou user@host classiques d'un
    Host header falsifié) au-delà de ce qu'un nom d'hôte légitime contient."""
    _h = host.strip().lower()
    if not _h or any(c in _h for c in ("/", "\\", "@", " ")):
        return False
    _hostname = _h.split(":", 1)[0]
    if _hostname in ("localhost", "127.0.0.1"):
        return True
    return any(_hostname.endswith(suffix) for suffix in _TRUSTED_HOST_SUFFIXES)


def _resolve_app_base_url() -> str:
    """Résout l'URL de base de l'application (pour les redirections OAuth/Stripe).
    Cherche dans st.secrets["APP_BASE_URL"], sinon tente une détection dynamique via les headers
    pour supporter plusieurs déploiements sans modification de code."""
    # 1. Secret Streamlit (prioritaire, permet de forcer une URL propre)
    _url = get_secret("APP_BASE_URL")
    if _url:
        return _url.rstrip("/")

    # 2. Détection dynamique via headers (robuste si le secret est absent)
    #
    # SÉCURITÉ (Open Redirect) : le header "Host" est fourni par le client et
    # peut être falsifié (selon la configuration du reverse proxy en amont).
    # Faire aveuglément confiance à sa valeur pour construire les URLs de
    # redirection OAuth/Stripe permettrait à un attaquant d'envoyer un Host
    # forgé afin de rediriger la victime vers un domaine tiers après connexion
    # ou paiement. On ne fait donc confiance au header Host QUE s'il
    # correspond à un motif de déploiement connu (localhost en dev, domaines
    # Railway/Streamlit Cloud attendus) ; sinon on ignore le header et on
    # tombe sur le fallback historique (étape 3) plutôt que de faire
    # confiance à une valeur arbitraire.
    try:
        _host = st.context.headers.get("Host")
        if _host and _is_trusted_host(_host):
            # Si on est sur localhost, on reste en http, sinon on assume https (Streamlit Cloud)
            _proto = "http" if "localhost" in _host or "127.0.0.1" in _host else "https"
            return f"{_proto}://{_host}"
    except Exception:
        pass

    # 3. Fallback historique (pour ne pas casser le comportement si tout échoue)
    return "https://tva-intracom-ue.streamlit.app"


def _finalize_login(email: str, cookie_manager: "stx.CookieManager") -> None:
    """Mappe un e-mail authentifié (mot de passe ou OAuth Supabase) sur un
    tva_users local, ouvre la session applicative (session_state + cookie
    30 jours), exactement comme le faisait historiquement le lien magique."""
    try:
        _user = tva_auth.get_or_create_user(email)
    except PermissionError as _e:
        # BUGFIX (2026-09-29) : le message était affiché par st.error() puis
        # immédiatement perdu, car TOUS les appelants (mot de passe, lien magique,
        # OAuth, mode dev) enchaînent un st.rerun() : l'utilisateur ne voyait rien
        # et restait sur l'écran de connexion sans explication. Le message est
        # désormais mémorisé en session et affiché (une seule fois) par
        # _render_login_screen au rerun suivant. Ligne d'origine conservée :
        # st.error(f"⛔ {_e}")
        st.session_state[_LOGIN_ERROR_KEY] = f"⛔ {_e}"
        return
    st.session_state["auth_user"] = _user
    st.session_state["manual_logout"] = False
    _token = tva_auth.create_session_token(_user.id)
    # Utilisation d'une date d'expiration stable
    _expires = datetime.now() + timedelta(days=30)
    cookie_manager.set(
        "tva_session_token",
        _token,
        expires_at=_expires,
    )
    # Nettoyage immédiat pour éviter les boucles au refresh
    st.query_params.clear()
    time.sleep(0.3) 
    st.rerun()


def ensure_cookie_manager() -> "stx.CookieManager":
    """Instancie le gestionnaire de cookies et exécute la maintenance
    ponctuelle (purge du cache VIES mal préfixé) une fois par session)."""
    cookie_manager = stx.CookieManager(key="tva_cookie_manager")

    # Mécanisme de synchronisation robuste pour le rafraîchissement (F5)
    if st.session_state.get("auth_user") is None and not st.session_state.get("manual_logout"):
        # On vérifie si on a déjà essayé de synchroniser les cookies dans cette session
        _attempts = st.session_state.get("_cookie_sync_attempts", 0)
        
        # Si on ne trouve rien dans st.context.cookies et qu'on n'a pas fini les tentatives
        if "tva_session_token" not in st.context.cookies and 0 <= _attempts < 3:
            st.session_state["_cookie_sync_attempts"] = _attempts + 1
            # On demande une lecture au composant (provoquera un rerun une fois reçu)
            cookie_manager.get_all()
            time.sleep(0.15)
            st.rerun()
        elif _attempts >= 3:
            # On a épuisé les tentatives, on marque comme "terminé" pour ne plus bloquer
            st.session_state["_cookie_sync_attempts"] = -1

    if "_malformed_vies_purged" not in st.session_state:
        try:
            _vies_purge_malformed_entries()
        except Exception:
            pass
        st.session_state["_malformed_vies_purged"] = True

    return cookie_manager


# BUGFIX (2026-09-29) : ce CSS contenait des accolades DOUBLÉES ({{ ... }}) — héritage
# d'une f-string — alors que la chaîne est ordinaire : les 6 règles étaient invalides
# (0 déclaration valide) et ni le logo ni la couleur de marque des boutons Google /
# GitHub / Amazon ne s'appliquaient. Accolades simples ci-dessous.
_OAUTH_BUTTONS_CSS = """
            <style>
            .st-key-oauth_btn_google a[data-testid^="stBaseLinkButton"] {
                background-color: #FFFFFF !important;
                color: #3C4043 !important;
                border: 1px solid #dadce0 !important;
                background-image: url('https://cdn.jsdelivr.net/gh/devicons/devicon@latest/icons/google/google-original.svg');
                background-repeat: no-repeat;
                background-position: 14px center;
                background-size: 18px 18px;
                padding-left: 38px !important;
            }
            .st-key-oauth_btn_google a[data-testid^="stBaseLinkButton"] p { color: #3C4043 !important; }
            .st-key-oauth_btn_github a[data-testid^="stBaseLinkButton"] {
                background-color: #24292E !important;
                color: #FFFFFF !important;
                border: 1px solid #24292E !important;
                background-image: url('https://cdn.jsdelivr.net/gh/devicons/devicon@latest/icons/github/github-original.svg');
                background-repeat: no-repeat;
                background-position: 14px center;
                background-size: 18px 18px;
                padding-left: 38px !important;
            }
            .st-key-oauth_btn_github a[data-testid^="stBaseLinkButton"] p { color: #FFFFFF !important; }
            .st-key-oauth_btn_cognito a[data-testid^="stBaseLinkButton"] {
                background-color: #FF9900 !important;
                color: #000000 !important;
                border: 1px solid #FF9900 !important;
                background-image: url('https://upload.wikimedia.org/wikipedia/commons/4/4a/Amazon_icon.svg');
                background-repeat: no-repeat;
                background-position: 14px center;
                background-size: 18px 18px;
                padding-left: 38px !important;
            }
            .st-key-oauth_btn_cognito a[data-testid^="stBaseLinkButton"] p { color: #000000 !important; }
            </style>
            """


@dataclass(frozen=True)
class _OAuthParams:
    """Paramètres d'URL d'un retour OAuth / lien de récupération, lus UNE seule fois
    au début du traitement (avant qu'une des étapes ne purge `st.query_params`)."""

    code: str | None
    provider: str | None
    nonce: str | None
    access_token: str | None
    link_type: str | None
    error_code: str | None
    error_description: str | None

    @classmethod
    def from_query_params(cls) -> "_OAuthParams":
        _qp = st.query_params
        return cls(
            code=_qp.get("code"),
            provider=_qp.get("sb_provider"),
            nonce=_qp.get("sb_nonce"),
            access_token=_qp.get("access_token"),
            link_type=_qp.get("type"),
            error_code=_qp.get("error_code"),
            error_description=_qp.get("error_description"),
        )



def _restore_session_from_cookie(cookie_manager: "stx.CookieManager") -> None:
    """Restauration de session PRIORITAIRE : cookie (st.context puis composant), puis
    paramètre d'URL `session_token` en dernier recours."""
    _cookie_token = st.context.cookies.get("tva_session_token")
    if not _cookie_token:
        # Fallback sur le composant si st.context n'a pas encore le header
        try:
            _cookie_token = cookie_manager.get("tva_session_token")
        except Exception:
            _cookie_token = None

    # Fallback ultime sur les paramètres d'URL (utile après redirection Stripe/OAuth)
    if not _cookie_token:
        _cookie_token = st.query_params.get("session_token")

    if _cookie_token:
        _cookie_token = str(_cookie_token).strip('"')

    if _cookie_token and _cookie_token != "LOGGED_OUT" and st.session_state.get("auth_user") is None and not st.session_state.get("manual_logout"):
        _restored_user = tva_auth.get_user_by_session_token(_cookie_token)
        if _restored_user is not None:
            st.session_state["auth_user"] = _restored_user
            # Nettoyage si on vient de restaurer via URL
            if "session_token" in st.query_params:
                st.query_params.pop("session_token")
                st.rerun()
        else:
            # DEBUG DISCRET : Le cookie existe mais la session est invalide en DB
            st.sidebar.caption("⚠️ " + _("auth_session_expired"))


def _render_password_reset_form(access_token: str, on_success: Callable[[], None] | None = None) -> None:
    """Formulaire « nouveau mot de passe » commun aux 3 chemins de récupération
    (jeton direct A0, code sans provider B0, PKCE provider=recovery).

    `on_success` est appelé juste après la mise à jour réussie, AVANT le message de
    succès (B0 y purge son jeton en cache). L'appelant fait ensuite `st.stop()`.
    """
    st.subheader(_("reset_password_title"))
    _new_pwd = st.text_input(
        _("new_password_label"), type="password", key="reset_new_password_input"
    )
    _new_pwd_confirm = st.text_input(
        _("new_password_label"), type="password", key="reset_new_password_confirm_input"
    )
    if st.button(_("update_password_btn"), key="btn_update_password", type="primary"):
        if not _new_pwd or _new_pwd != _new_pwd_confirm:
            st.warning(_("invalid_email_warning"))
        else:
            try:
                tva_sb_auth.update_user_password(access_token, _new_pwd)
                if on_success is not None:
                    on_success()
                st.success(_("password_updated_success"))
                st.query_params.clear()
            except Exception as _sb_err:
                st.error(_("password_update_error", error=str(_sb_err)))



def _handle_direct_access_token(access_token: str, cookie_manager: "stx.CookieManager") -> None:
    """Cas A : jeton direct (Implicit flow / retour mail)."""
    try:
        _sb_result = tva_sb_auth.get_user_from_access_token(access_token)
        _finalize_login(_sb_result.email, cookie_manager)
        st.query_params.clear()
        st.rerun()
    except Exception as _e:
        st.error(_("oauth_access_token_error", error=str(_e)))
        st.query_params.clear()


def _handle_bare_recovery_code(code: str, app_base_url: str) -> None:
    """Cas B0 : code présent mais SANS sb_provider/sb_nonce (voir commentaires ci-dessous)."""
    # Cas B0 : Code présent mais SANS sb_provider/sb_nonce — Supabase a
    # tronqué la query string du redirect_to (n'arrive que via une entrée
    # wildcard de la Redirect URLs allowlist ; seule une correspondance
    # EXACTE préserve la query string, impossible ici puisque sb_nonce
    # change à chaque demande). On ne peut alors distinguer que le cas
    # "recovery" via la seule hypothèse restante : la dernière demande de
    # reset de mot de passe en attente (voir consume_latest_pkce_verifier_by_provider).
    _b0_cache_key = "_sb_pkce_recovery_bare"
    _b0_cached = st.session_state.get(_b0_cache_key)
    _b0_access_token = None

    # 1. On cherche d'abord en session (robuste aux reruns Streamlit :
    #    chaque frappe/clic ré-exécute le script, et re-poster le même
    #    `code` à Supabase une 2e fois échoue avec "invalid flow state,
    #    no valid flow state found" car le flow_state est déjà consommé
    #    côté Supabase après le premier échange réussi).
    if _b0_cached and _b0_cached[0] == code:
        _b0_access_token = _b0_cached[1]
    else:
        # BUGFIX (fiabilité, voir README - évolution.md et docstring
        # de consume_latest_pkce_verifiers_by_provider dans auth.py) :
        # on essaie chaque candidat récent (du plus récent au plus
        # ancien) au lieu d'un seul "dernier jeton" — nécessaire dès
        # que deux resets de mot de passe se chevauchent dans la
        # même fenêtre de 15 minutes. PKCE valide cryptographiquement
        # le couple (code, verifier) côté Supabase : au plus un seul
        # candidat peut réussir, essayer les autres n'introduit
        # aucun risque de sécurité (juste des tentatives en trop en
        # cas de collision).
        _candidates = tva_auth.consume_latest_pkce_verifiers_by_provider("recovery")
        _last_err = None
        for _verifier in _candidates:
            try:
                _sb_result = tva_sb_auth.exchange_pkce_code(
                    code, _verifier, redirect_uri=app_base_url
                )
                _b0_access_token = _sb_result.access_token
                # Mis en session IMMÉDIATEMENT pour que les reruns
                # suivants (déclenchés par les widgets ci-dessous)
                # réutilisent ce jeton sans retourner échanger le code.
                st.session_state[_b0_cache_key] = (code, _b0_access_token)
                break
            except Exception as _sb_err:
                _last_err = _sb_err
                continue
        if not _b0_access_token and _last_err is not None:
            st.error(_("oauth_login_error", error=str(_last_err)))
            st.query_params.clear()

    if _b0_access_token:
        _render_password_reset_form(
            _b0_access_token, on_success=lambda: st.session_state.pop(_b0_cache_key, None),
        )
        st.stop()


def _handle_pkce_code(code: str, provider: str, nonce: str | None,
                      cookie_manager: "stx.CookieManager", app_base_url: str) -> None:
    """Cas B : code à échanger (PKCE flow / bouton de connexion OAuth)."""
    _cache_key = f"_sb_pkce_{provider}"
    _cached = st.session_state.get(_cache_key)
    _verifier = None

    # 1. On cherche d'abord en session (très robuste aux reruns)
    if _cached and _cached[0] == nonce:
        _verifier = _cached[1]

    # 2. Sinon on cherche en DB (cas d'une nouvelle session)
    _pkce_diag = None
    if not _verifier and nonce:
        try:
            _verifier = tva_auth.consume_pkce_verifier(nonce, provider)
        except LookupError as _diag_err:
            _pkce_diag = str(_diag_err)
        if _verifier:
            # On le met IMMÉDIATEMENT en session pour que les reruns
            # suivants (déclenchés par st.query_params ou cookies)
            # le trouvent sans retourner en DB.
            st.session_state[_cache_key] = (nonce, _verifier)

    if _verifier:
        try:
            _redir = f"{app_base_url}/?sb_provider={provider}&sb_nonce={nonce}"
            _sb_result = tva_sb_auth.exchange_pkce_code(code, _verifier, redirect_uri=_redir)
            if provider == "recovery":
                # Retour du lien "mot de passe oublié" via PKCE : on a un
                # jeton valide, mais on ne connecte PAS directement —
                # l'utilisateur doit d'abord choisir son nouveau mot de
                # passe (sinon il se retrouve connecté sans jamais avoir
                # pu le changer).
                st.session_state.pop(_cache_key, None)
                _render_password_reset_form(_sb_result.access_token)
                st.stop()
            _finalize_login(_sb_result.email, cookie_manager)
            # Nettoyage complet
            st.session_state.pop(_cache_key, None)
            st.query_params.clear()
            st.rerun()
        except Exception as _sb_err:
            st.error(_("oauth_login_error", error=str(_sb_err)))
            st.query_params.clear()
    else:
        # Si on n'a plus de verifier du tout (déjà consommé ou perdu)
        if nonce:
            _diag_suffix = f" — diagnostic: {_pkce_diag}" if _pkce_diag else ""
            st.error(f"{_('oauth_state_lost_error')} (prov={provider}, nonce={nonce[:8]}...){_diag_suffix}")
            if st.button(_("retry_btn")):
                st.query_params.clear()
                st.rerun()
            st.stop()


def _handle_oauth_return(params: _OAuthParams, cookie_manager: "stx.CookieManager",
                         app_base_url: str) -> None:
    """Interception du code OAuth (PKCE ou Implicit) : ordre des cas A0 → A → B0/B inchangé."""
    _qp = st.query_params
    # On n'intercepte les paramètres de connexion QUE si on n'est pas déjà authentifié.
    # Si on est déjà logué (via cookie), on nettoie juste l'URL si elle contient des restes d'OAuth.
    if st.session_state.get("auth_user") is not None:
        if any(k in _qp for k in ["code", "access_token", "login_token", "sb_provider"]):
            st.query_params.clear()
            st.rerun()
    else:
        # Cas A0 : Retour du lien "mot de passe oublié" (type=recovery) —
        # le token Supabase est valide pour changer le mot de passe, mais on
        # ne doit PAS l'utiliser pour connecter directement l'utilisateur
        # (sinon il n'a jamais l'occasion de saisir un nouveau mot de passe).
        if params.access_token and params.link_type == "recovery":
            _render_password_reset_form(params.access_token)
            st.stop()

        # Cas A : Jeton direct (Implicit flow / retour mail)
        if params.access_token:
            _handle_direct_access_token(params.access_token, cookie_manager)

        # Cas B0 / B : voir _handle_bare_recovery_code et _handle_pkce_code
        if params.code and not params.provider:
            _handle_bare_recovery_code(params.code, app_base_url)
        elif params.code and params.provider:
            _handle_pkce_code(params.code, params.provider, params.nonce, cookie_manager, app_base_url)


def _handle_oauth_error_params(params: _OAuthParams) -> None:
    """Cas C : erreur renvoyée par le fournisseur OAuth (ex : e-mail non vérifié)."""
    if params.error_code and st.session_state.get("auth_user") is None:
        if params.error_code == "provider_email_needs_verification":
            st.warning(_("oauth_email_verification_required"))
        else:
            st.error(_("oauth_generic_error", code=params.error_code, desc=params.error_description or _("unknown_error_desc")))
        
        if st.button(_("cancel_btn"), key="clear_oauth_error"):
            st.query_params.clear()
            st.rerun()
        st.stop()


def _render_fragment_bridge() -> None:
    """Conversion du fragment URL (#) en paramètres (?) — les jetons Supabase arrivent en fragment."""
    if st.session_state.get("auth_user") is None:
        st.iframe(
            """
            <script>
            var hash = window.parent.location.hash || window.location.hash;
            if (hash && (hash.includes('access_token=') || hash.includes('error='))) {
                var params = new URLSearchParams(hash.substring(1));
                var currUrl = new URL(window.parent.location.href);
                params.forEach((value, key) => { currUrl.searchParams.set(key, value); });
                currUrl.hash = "";
                window.parent.location.href = currUrl.toString();
            }
            </script>
            """,
            height="content",
        )


def _is_local_dev_bypass() -> bool:
    """Vrai si le secret LOCAL_DEV_BYPASS_AUTH est activé (développement local uniquement)."""
    try:
        return bool(get_secret("LOCAL_DEV_BYPASS_AUTH", False))
    except Exception:
        return False


def _handle_magic_link_param(cookie_manager: "stx.CookieManager") -> None:
    """Consommation du lien magique (`?login_token=`)."""
    _qp_token = st.query_params.get("login_token")
    if _qp_token:
        if st.session_state.get("auth_user") is None:
            st.info(_("magic_link_welcome"))
            if st.button(_("magic_link_confirm_btn"), key="confirm_magic_link"):
                _ip = st.context.ip_address or "unknown"

                try:
                    _u = tva_auth.consume_magic_link(_qp_token, ip_address=_ip)
                except PermissionError as e:
                    st.error(f"⛔ {e}")
                    _u = None
                except Exception as e:
                    st.error(_("magic_link_error", error=str(e)))
                    _u = None

                if _u is not None:
                    _finalize_login(_u.email, cookie_manager)
                    st.query_params.clear()
                    st.rerun()
                else:
                    st.error(_("magic_link_invalid"))
            st.stop()
        else:
            st.query_params.clear()
            st.rerun()


def _handle_session_token_param(cookie_manager: "stx.CookieManager") -> None:
    """Jeton de session passé en URL (`?session_token=`) : posé en cookie puis retiré de l'URL."""
    _qp_session_token = st.query_params.get("session_token")
    if _qp_session_token:
        cookie_manager.set("tva_session_token", _qp_session_token, expires_at=datetime.now() + timedelta(days=30))
        st.query_params.pop("session_token", None)
        st.rerun()


def _render_dev_bypass_login(cookie_manager: "stx.CookieManager") -> None:
    """Connexion sans mot de passe (LOCAL_DEV_BYPASS_AUTH) — développement local uniquement."""
    st.warning(_("dev_bypass_warning"))
    _dev_email = st.text_input(_("dev_email_label"), key="dev_login_email_input")
    if st.button(_("dev_login_btn"), key="btn_dev_login"):
        if _dev_email and "@" in _dev_email:
            _finalize_login(_dev_email, cookie_manager)
            st.rerun()
        else:
            st.warning(_("invalid_email_warning"))
    st.stop()


def _render_forgot_password(login_email: str, app_base_url: str) -> None:
    """Mot de passe oublié : envoi du lien de réinitialisation (PKCE, provider « recovery »)."""
    with st.expander(_("forgot_password_btn")):
        st.caption(_("reset_password_instructions"))
        _reset_email = st.text_input(
            _("email_label"), value=login_email, key="reset_password_email_input"
        )
        if st.button(_("forgot_password_btn"), key="btn_send_reset_password"):
            if _reset_email and "@" in _reset_email:
                try:
                    _reset_nonce = secrets.token_urlsafe(24)
                    _reset_verifier = tva_sb_auth.new_code_verifier()
                    tva_auth.save_pkce_verifier(_reset_nonce, "recovery", _reset_verifier)
                    _reset_challenge = base64.urlsafe_b64encode(
                        hashlib.sha256(_reset_verifier.encode()).digest()
                    ).decode().rstrip("=")
                    _reset_redirect_to = app_base_url
                    tva_sb_auth.reset_password_for_email(
                        _reset_email, redirect_to=_reset_redirect_to, code_challenge=_reset_challenge
                    )
                    st.success(_("reset_password_success"))
                except Exception as _sb_err:
                    st.error(_("reset_password_error", error=str(_sb_err)))
            else:
                st.warning(_("invalid_email_warning"))


def _render_password_tab(cookie_manager: "stx.CookieManager", login_email: str, app_base_url: str) -> None:
    """Onglet mot de passe : connexion, inscription, puis « mot de passe oublié »."""
    _login_password = st.text_input(_("password_label"), type="password", key="login_password_input")
    _col_signin, _col_signup = st.columns(2)

    if _col_signin.button(_("password_signin_btn"), key="btn_password_signin", width="stretch", type="primary"):
        if login_email and "@" in login_email and _login_password:
            try:
                _sb_res = tva_sb_auth.sign_in_with_password(login_email, _login_password)
                _finalize_login(_sb_res.email, cookie_manager)
                st.rerun()
            except Exception as _sb_err:
                st.error(_("password_login_error", error=str(_sb_err)))
        else:
            st.warning(_("invalid_email_warning"))

    if _col_signup.button(_("password_signup_btn"), key="btn_password_signup", width="stretch"):
        if login_email and "@" in login_email and _login_password:
            _allowed, _reason = tva_auth.can_signup(login_email)
            if not _allowed:
                st.error(f"⛔ {_reason}")
            else:
                try:
                    _sb_res = tva_sb_auth.sign_up_with_password(login_email, _login_password)
                    if _sb_res.access_token:
                        _finalize_login(_sb_res.email, cookie_manager)
                        st.rerun()
                    else:
                        st.success(_("password_signup_confirm_email_info"))
                except Exception as _sb_err:
                    st.error(_("password_login_error", error=str(_sb_err)))
        else:
            st.warning(_("invalid_email_warning"))

    # ── Mot de passe oublié ─────────────────────────────────────────────
    _render_forgot_password(login_email, app_base_url)


def _render_magic_link_tab(login_email: str, app_base_url: str) -> None:
    """Onglet lien magique (méthode historique) : envoi du lien par e-mail."""
    st.caption(_("legacy_login_methods_caption"))
    if st.button(
        _("send_magic_link_btn"), key="btn_send_magic_link",
        width="stretch",
    ):
        if login_email and "@" in login_email:
            _allowed, _reason = tva_auth.can_signup(login_email)
            if not _allowed:
                st.error(f"⛔ {_reason}")
            else:
                try:
                    _magic_token = tva_auth.create_magic_link(login_email)
                    _magic_url = f"{app_base_url}/?login_token={_magic_token}"
                    tva_auth.send_magic_link_email(login_email, _magic_url)
                    st.success(_("magic_link_sent_success", email=login_email))
                except Exception as _e:
                    st.error(_("magic_link_sent_error", error=str(_e)))
        else:
            st.warning(_("invalid_email_warning"))


def _render_oauth_buttons(app_base_url: str) -> None:
    """Boutons OAuth sociaux (Google / GitHub / Amazon) — Supabase Auth, PKCE."""
    st.caption(_("oauth_divider_label"))
    _col_google, _col_github, _col_amazon = st.columns(3)


    # ── Style officiel (logo + couleur) appliqué à st.link_button ──────
    # st.link_button est fiable pour sortir de l'iframe Streamlit Cloud
    # (contrairement à un <a> en HTML brut, cf. incident précédent), mais
    # n'a pas de paramètre pour un logo/une couleur de marque. On cible
    # donc chaque bouton via la classe "st-key-{key}" que Streamlit ajoute
    # automatiquement sur son conteneur, et on pose le logo en
    # background-image du <button> natif généré par Streamlit.
    st.markdown(_OAUTH_BUTTONS_CSS, unsafe_allow_html=True)

    for _col, _provider, _label_key, _icon_url, _bg, _text in (
            (
                    _col_google,
                    "google",
                    "oauth_google_btn",
                    "https://cdn.jsdelivr.net/gh/devicons/devicon@latest/icons/google/google-original.svg",
                    "#FFFFFF",
                    "#000000"
            ),
            (
                    _col_github,
                    "github",
                    "oauth_github_btn",
                    "https://cdn.jsdelivr.net/gh/devicons/devicon@latest/icons/github/github-original.svg",
                    "#24292E",
                    "#FFFFFF"
            ),
            (
                    _col_amazon,
                    "cognito",
                    "amazon_login_btn",
                    "https://upload.wikimedia.org/wikipedia/commons/4/4a/Amazon_icon.svg",
                    "#FF9900",
                    "#000000"
            ),
    ):
        with _col:
            try:
                _cache_key = f"_sb_pkce_{_provider}"
                _cached = st.session_state.get(_cache_key)
                if _cached:
                    _nonce, _verifier = _cached
                else:
                    _nonce = secrets.token_urlsafe(24)
                    _verifier = tva_sb_auth.new_code_verifier()
                    tva_auth.save_pkce_verifier(_nonce, _provider, _verifier)
                    st.session_state[_cache_key] = (_nonce, _verifier)
                _redirect_to = f"{app_base_url}/?sb_provider={_provider}&sb_nonce={_nonce}"
                _oauth_url = tva_sb_auth.build_oauth_authorize_url(_provider, _redirect_to, _verifier)

                # st.link_button natif plutôt qu'un <a> en HTML brut : ce
                # dernier s'est révélé peu fiable pour sortir de l'iframe
                # Streamlit Cloud (clic sans effet, malgré un href valide et
                # un survol fonctionnel) — st.link_button utilise le
                # mécanisme de navigation propre à Streamlit, garanti de
                # fonctionner dans cet environnement.
                st.link_button(_(_label_key), _oauth_url, width="stretch",
                                key=f"oauth_btn_{_provider}")
            except Exception as _oauth_render_err:
                st.error(f"⛔ {_provider} : {_oauth_render_err}")
                st.button(_(_label_key), key=f"btn_oauth_disabled_{_provider}", disabled=True,
                          width="stretch")


def _render_login_screen(cookie_manager: "stx.CookieManager", app_base_url: str, local_bypass: bool) -> None:
    """Interface de connexion non authentifiée. Ne retourne jamais : se termine par st.stop()."""
    # Si on est en train d'attendre les cookies (attempts > 0), on n'affiche pas encore l'écran de login.
    # Si attempts est -1 (fini) ou 0 (pas commencé), on affiche.
    if st.session_state.get("_cookie_sync_attempts", 0) > 0:
        st.warning(_("magic_link_welcome")) # "Veuillez patienter..."
        st.stop()

    # Motif du refus de connexion mémorisé par _finalize_login (affiché une seule fois).
    _login_error = st.session_state.pop(_LOGIN_ERROR_KEY, None)
    if _login_error:
        st.error(_login_error)

    st.info(_("auth_required_info"))
    st.caption(f"[{_('website_label')}](https://www.tvacalculator.eu/)")

    if local_bypass:
        _render_dev_bypass_login(cookie_manager)

    # ── Identification ──────────────────────────────────────────────────
    login_email = st.text_input(_("email_label"), key="login_email_input")

    _tab_pwd, _tab_magic = st.tabs([_("password_signin_btn"), _("send_magic_link_btn")])

    with _tab_pwd:
        _render_password_tab(cookie_manager, login_email, app_base_url)

    with _tab_magic:
        _render_magic_link_tab(login_email, app_base_url)

    # ── OAuth social (Google / GitHub / Amazon) — Supabase Auth ─
    _render_oauth_buttons(app_base_url)

    st.stop()


def _perform_logout(cookie_manager: "stx.CookieManager") -> None:
    """Déconnexion : invalidation serveur, purge de la session locale, cookie, rerun."""
    # 1. Invalidation côté serveur
    try:
        _current_token = st.context.cookies.get("tva_session_token")
    except Exception:
        _current_token = None
    if not _current_token:
        try:
            _current_token = cookie_manager.get("tva_session_token")
        except Exception:
            _current_token = None
    if _current_token:
        _current_token = str(_current_token).strip('"')
    if _current_token and _current_token != "LOGGED_OUT":
        try:
            tva_auth.delete_session_token(_current_token)
        except Exception:
            pass

    # 2. Nettoyage agressif de la session locale pour libérer la RAM
    # On ne garde que le strict minimum pour éviter les fuites.
    _LOGOUT_WHITELIST = {"manual_logout", "language"}
    for key in list(st.session_state.keys()):
        if key not in _LOGOUT_WHITELIST:
            del st.session_state[key]

    st.session_state["manual_logout"] = True

    # Force le ramasse-miettes ET la restitution de la mémoire à
    # l'OS (st.cache_data.clear() + gc.collect() + jemalloc purge)
    from tva_intracom.mem_utils import release_memory
    release_memory()

    # 3. Suppression du cookie (asynchrone côté client)
    try:
        # On écrase la valeur et on expire le cookie immédiatement
        cookie_manager.set("tva_session_token", "LOGGED_OUT", expires_at=datetime.now() - timedelta(days=1))
        cookie_manager.delete("tva_session_token")
    except Exception:
        pass

    # Temps de pause crucial pour que le composant JS ait le temps
    # d'envoyer l'ordre au navigateur avant le rerun.
    time.sleep(0.5)
    st.query_params.clear()
    st.rerun()


def _render_account_bar(current_user: Any, cookie_manager: "stx.CookieManager") -> None:
    """Barre d'état connecté (badge e-mail) et bouton de déconnexion."""
    _col_user, _col_logout = st.columns([5, 1])
    # Badge de plan (gratuit / pro / cabinet) à côté de l'email — même
    # source de vérité que l'expander "Abonnements & forfaits" de la
    # sidebar (tva_billing.get_subscription_status, dérivé du price_id
    # Stripe actif). Mémoïsé via le même cache que sidebar.py (clé
    # partagée `sub_status_{org_id}` — ORG_ID 2026-08-24, abonnement
    # partagé par toute l'organisation) : pas de lecture DB en double.
    # DÉSACTIVÉ (passage au don, voir README - évolution.md) : le badge
    # de plan (Gratuit/Pro/Cabinet/Achat) et les 2 lectures DB associées
    # (sub_status, account_status) n'ont plus de sens dans un modèle 100%
    # don — en particulier le statut "Achat", qui restera affiché
    # indéfiniment pour toute organisation ayant fait un achat PAYG avant
    # ce changement, alors qu'aucun abonnement ne peut plus être souscrit
    # pour en sortir. Badge figé sur "Gratuit" pour tout le monde ;
    # logique d'origine conservée en commentaire pour réactivation.
    _acct_plan_label = _("plan_free")
    _acct_plan_css = "plan-free"
    # try:
    #     _acct_sub_status = _cached_db_read(
    #         f"sub_status_{current_user.org_id}",
    #         lambda: tva_billing.get_subscription_status(current_user.org_id),
    #     )
    # except Exception:
    #     _acct_sub_status = None
    #
    # # Statut "Achat" (2026-09-05) : même source de vérité et même clé de
    # # cache que le badge équivalent de sidebar.py (`account_status_{org_id}`)
    # # — un compte PAYG sans abonnement ne doit plus s'afficher comme
    # # "Gratuit" ici, cf. verrouillage SIREN désormais appliqué à ce statut.
    # # Uniquement calculé quand nécessaire (pas d'abonnement actif) pour
    # # éviter une lecture DB superflue sur les comptes payants.
    # _acct_status = None
    # if not (_acct_sub_status and _acct_sub_status.active):
    #     try:
    #         _acct_status = _cached_db_read(
    #             f"account_status_{current_user.org_id}",
    #             lambda: tva_billing.get_account_status(current_user.org_id),
    #         )
    #     except Exception:
    #         _acct_status = None
    #
    # if _acct_status == tva_billing.ACCOUNT_STATUS_ACHAT:
    #     _acct_plan_label = _("plan_achat")
    #     _acct_plan_css = "plan-achat"
    # else:
    #     _acct_plan_label = {"business": _("plan_pro"), "cabinet": _("plan_cabinet")}.get(
    #         _acct_sub_status.plan if (_acct_sub_status and _acct_sub_status.active) else None,
    #         _("plan_free"),
    #     )
    #     _acct_plan_css = {"business": "plan-business", "cabinet": "plan-cabinet"}.get(
    #         _acct_sub_status.plan if (_acct_sub_status and _acct_sub_status.active) else None,
    #         "plan-free",
    #     )
    _col_user.markdown(
        f"""<div class="account-badge">
                <span class="account-badge-dot"></span>
                <span class="account-badge-email">{html.escape(current_user.email)}</span>
            </div>""",
        unsafe_allow_html=True,
    )
    if _col_logout.button(_("logout_btn"), key="btn_logout"):
        _perform_logout(cookie_manager)


def _org_lock_catchup(current_user: Any) -> None:
    """Verrouillage rétroactif de l'organisation (une seule fois par session Streamlit)."""
    # Verrouillage rétroactif (2026-08-23, correctif) : un compte qui
    # avait DÉJÀ un abonnement payant actif AVANT l'introduction des
    # rôles ne déclenche `lock_org_for_user` que via le webhook Stripe
    # d'un NOUVEL abonnement — jamais rétroactivement. Le premier essai
    # (dans _finalize_login) ratait le cas très fréquent d'une session
    # restaurée par COOKIE (30 jours), qui ne passe jamais par
    # _finalize_login. Centralisé ici à la place, une seule fois par
    # session Streamlit (flag session_state) puisque ce point est
    # traversé à CHAQUE rerun, quel que soit le chemin d'authentification
    # (cookie, mot de passe, OAuth, lien magique).
    if not st.session_state.get("_org_lock_catchup_done"):
        st.session_state["_org_lock_catchup_done"] = True
        try:
            _locked_already = tva_auth.is_org_locked(current_user.org_id)
            _is_solo = tva_auth.is_solo_org(current_user.org_id)
            _sub_active = tva_billing.get_subscription_status(current_user.org_id).active
            logging.getLogger("tva_intracom.auth_flow").info(
                "[org_lock_catchup] solo=%s locked=%s sub_active=%s",
                _is_solo, _locked_already, _sub_active,
            )
            if not _is_solo and not _locked_already and _sub_active:
                tva_auth.lock_org_for_user(current_user.id)
        except Exception:
            logging.getLogger("tva_intracom.auth_flow").warning(
                "[org_lock_catchup] échec", exc_info=True,
            )


def run_auth_flow(cookie_manager: "stx.CookieManager") -> AuthContext:
    """Exécute le flux complet d'authentification."""
    if "auth_user" not in st.session_state:
        st.session_state["auth_user"] = None
    if "manual_logout" not in st.session_state:
        st.session_state["manual_logout"] = False

    _app_base_url_login = _resolve_app_base_url()

    _oauth_params = _OAuthParams.from_query_params()

    _restore_session_from_cookie(cookie_manager)

    # ── 1. Interception du code OAuth (PKCE ou Implicit) ────────────────────
    # (Les paramètres d'URL sont lus UNE fois avant tout traitement : voir _OAuthParams.)
    _handle_oauth_return(_oauth_params, cookie_manager, _app_base_url_login)

    # Cas C : Erreur spécifique (ex: email non vérifié)
    if _oauth_params.error_code and st.session_state.get("auth_user") is None:
        _handle_oauth_error_params(_oauth_params)

    # ── Conversion du fragment URL (#) en paramètres (?) ─────────────────
    _render_fragment_bridge()

    _local_bypass = _is_local_dev_bypass()

    # ── Consommation du lien magique ────────────────────────────────────────
    _handle_magic_link_param(cookie_manager)

    _handle_session_token_param(cookie_manager)

    # ── Interface de connexion non-authentifiée ────────────────────────────
    if st.session_state["auth_user"] is None:
        _render_login_screen(cookie_manager, _app_base_url_login, _local_bypass)

    # ── Barre d'état connecté ──────────────────────────────────────────────
    _current_user = st.session_state["auth_user"]

    if _current_user is not None:
        _render_account_bar(_current_user, cookie_manager)

        _app_base_url = _resolve_app_base_url()
        _vies_scope_id = _vies_resolve_scope_id(_current_user.email)

        _org_lock_catchup(_current_user)

        return AuthContext(
            current_user=_current_user,
            cookie_manager=cookie_manager,
            app_base_url=_app_base_url,
            vies_scope_id=_vies_scope_id,
        )

    st.stop()
