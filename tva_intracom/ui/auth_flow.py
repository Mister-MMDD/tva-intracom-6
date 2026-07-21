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
import secrets
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

import streamlit as st
import streamlit.components.v1 as components
from ..config import get_secret
from tva_intracom.i18n import _
import extra_streamlit_components as stx

from tva_intracom import auth as tva_auth
from tva_intracom import auth_supabase as tva_sb_auth
from tva_intracom.vies_engine import (
    resolve_scope_id as _vies_resolve_scope_id,
    purge_malformed_entries as _vies_purge_malformed_entries,
)


@dataclass
class AuthContext:
    """Contexte d'authentification résolu, transmis au reste de l'app."""

    current_user: Any                 # tva_intracom.auth.User
    cookie_manager: "stx.CookieManager"
    app_base_url: str
    vies_scope_id: str

    def stripe_success_url(self, extra_qs: str = "") -> str:
        """URL de retour post-paiement Stripe, avec le jeton de session courant
        pour éviter une déconnexion."""
        _tok = st.query_params.get("session_token", "")
        _qs = f"session_token={_tok}" if _tok else ""
        if extra_qs:
            _qs = f"{_qs}&{extra_qs}" if _qs else extra_qs
        return f"{self.app_base_url}/?{_qs}" if _qs else f"{self.app_base_url}/"

    def stripe_cancel_url(self) -> str:
        _tok = st.query_params.get("session_token", "")
        return f"{self.app_base_url}/?session_token={_tok}" if _tok else f"{self.app_base_url}/"


def _resolve_app_base_url() -> str:
    """Résout l'URL de base de l'application (pour les redirections OAuth/Stripe).
    Cherche dans st.secrets["APP_BASE_URL"], sinon tente une détection dynamique via les headers
    pour supporter plusieurs déploiements sans modification de code."""
    # 1. Secret Streamlit (prioritaire, permet de forcer une URL propre)
    _url = get_secret("APP_BASE_URL")
    if _url:
        return _url.rstrip("/")

    # 2. Détection dynamique via headers (robuste si le secret est absent)
    try:
        from streamlit.web.server.websocket_headers import _get_websocket_headers
        _headers = _get_websocket_headers()
        if _headers and "Host" in _headers:
            _host = _headers["Host"]
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
    _user = tva_auth.get_or_create_user(email)
    st.session_state["auth_user"] = _user
    st.session_state["manual_logout"] = False
    _token = tva_auth.create_session_token(_user.id)
    cookie_manager.set(
        "tva_session_token",
        _token,
        expires_at=datetime.now() + timedelta(days=30),
        key=f"set_cookie_{int(time.time())}", # Clé unique pour forcer l'update
    )


def ensure_cookie_manager() -> "stx.CookieManager":
    """Instancie le gestionnaire de cookies et exécute la maintenance
    ponctuelle (purge du cache VIES mal préfixé) une fois par session).

    On utilise désormais st.context.cookies pour la lecture des cookies,
    ce qui est synchrone et évite les déconnexions au rafraîchissement (F5).
    Le CookieManager reste nécessaire pour l'écriture (set/delete)."""
    cookie_manager = stx.CookieManager(key="tva_cookie_manager")

    if "_malformed_vies_purged" not in st.session_state:
        try:
            _vies_purge_malformed_entries()
        except Exception:
            pass
        st.session_state["_malformed_vies_purged"] = True

    return cookie_manager


def run_auth_flow(cookie_manager: "stx.CookieManager") -> AuthContext:
    """Exécute le flux complet d'authentification."""
    if "auth_user" not in st.session_state:
        st.session_state["auth_user"] = None
    if "manual_logout" not in st.session_state:
        st.session_state["manual_logout"] = False

    _app_base_url_login = _resolve_app_base_url()

    # ── 1. Interception PRIORITAIRE du code OAuth (PKCE ou Implicit) ────────
    _qp = st.query_params
    _sb_code = _qp.get("code")
    _sb_provider = _qp.get("sb_provider")
    _sb_nonce = _qp.get("sb_nonce")
    _sb_access_token = _qp.get("access_token")
    _sb_type = _qp.get("type")
    _sb_error_code = _qp.get("error_code")
    _sb_error_desc = _qp.get("error_description")

    if st.session_state.get("auth_user") is None:
        # Cas A0 : Retour du lien "mot de passe oublié" (type=recovery) —
        # le token Supabase est valide pour changer le mot de passe, mais on
        # ne doit PAS l'utiliser pour connecter directement l'utilisateur
        # (sinon il n'a jamais l'occasion de saisir un nouveau mot de passe).
        if _sb_access_token and _sb_type == "recovery":
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
                        tva_sb_auth.update_user_password(_sb_access_token, _new_pwd)
                        st.success(_("password_updated_success"))
                        st.query_params.clear()
                    except Exception as _sb_err:
                        st.error(_("password_update_error", error=str(_sb_err)))
            st.stop()

        # Cas A : Jeton direct (Implicit flow / retour mail)
        if _sb_access_token:
            try:
                _sb_result = tva_sb_auth.get_user_from_access_token(_sb_access_token)
                _finalize_login(_sb_result.email, cookie_manager)
                st.query_params.clear()
                st.rerun()
            except Exception as _e:
                st.error(f"Erreur access_token: {str(_e)}")
                st.query_params.clear()

        # Cas B0 : Code présent mais SANS sb_provider/sb_nonce — Supabase a
        # tronqué la query string du redirect_to (n'arrive que via une entrée
        # wildcard de la Redirect URLs allowlist ; seule une correspondance
        # EXACTE préserve la query string, impossible ici puisque sb_nonce
        # change à chaque demande). On ne peut alors distinguer que le cas
        # "recovery" via la seule hypothèse restante : la dernière demande de
        # reset de mot de passe en attente (voir consume_latest_pkce_verifier_by_provider).
        if _sb_code and not _sb_provider:
            _b0_cache_key = "_sb_pkce_recovery_bare"
            _b0_cached = st.session_state.get(_b0_cache_key)
            _b0_access_token = None

            # 1. On cherche d'abord en session (robuste aux reruns Streamlit :
            #    chaque frappe/clic ré-exécute le script, et re-poster le même
            #    `code` à Supabase une 2e fois échoue avec "invalid flow state,
            #    no valid flow state found" car le flow_state est déjà consommé
            #    côté Supabase après le premier échange réussi).
            if _b0_cached and _b0_cached[0] == _sb_code:
                _b0_access_token = _b0_cached[1]
            else:
                _verifier = tva_auth.consume_latest_pkce_verifier_by_provider("recovery")
                if _verifier:
                    try:
                        _sb_result = tva_sb_auth.exchange_pkce_code(
                            _sb_code, _verifier, redirect_uri=_app_base_url_login
                        )
                        _b0_access_token = _sb_result.access_token
                        # Mis en session IMMÉDIATEMENT pour que les reruns
                        # suivants (déclenchés par les widgets ci-dessous)
                        # réutilisent ce jeton sans retourner échanger le code.
                        st.session_state[_b0_cache_key] = (_sb_code, _b0_access_token)
                    except Exception as _sb_err:
                        st.error(_("oauth_login_error", error=str(_sb_err)))
                        st.query_params.clear()

            if _b0_access_token:
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
                            tva_sb_auth.update_user_password(_b0_access_token, _new_pwd)
                            st.session_state.pop(_b0_cache_key, None)
                            st.success(_("password_updated_success"))
                            st.query_params.clear()
                        except Exception as _sb_err:
                            st.error(_("password_update_error", error=str(_sb_err)))
                st.stop()

        # Cas B : Code à échanger (PKCE flow / bouton login)
        elif _sb_code and _sb_provider:
            _cache_key = f"_sb_pkce_{_sb_provider}"
            _cached = st.session_state.get(_cache_key)
            _verifier = None
            
            # 1. On cherche d'abord en session (très robuste aux reruns)
            if _cached and _cached[0] == _sb_nonce:
                _verifier = _cached[1]
            
            # 2. Sinon on cherche en DB (cas d'une nouvelle session)
            _pkce_diag = None
            if not _verifier and _sb_nonce:
                try:
                    _verifier = tva_auth.consume_pkce_verifier(_sb_nonce, _sb_provider)
                except LookupError as _diag_err:
                    _pkce_diag = str(_diag_err)
                if _verifier:
                    # On le met IMMÉDIATEMENT en session pour que les reruns
                    # suivants (déclenchés par st.query_params ou cookies)
                    # le trouvent sans retourner en DB.
                    st.session_state[_cache_key] = (_sb_nonce, _verifier)
            
            if _verifier:
                try:
                    _redir = f"{_app_base_url_login}/?sb_provider={_sb_provider}&sb_nonce={_sb_nonce}"
                    _sb_result = tva_sb_auth.exchange_pkce_code(_sb_code, _verifier, redirect_uri=_redir)
                    if _sb_provider == "recovery":
                        # Retour du lien "mot de passe oublié" via PKCE : on a un
                        # jeton valide, mais on ne connecte PAS directement —
                        # l'utilisateur doit d'abord choisir son nouveau mot de
                        # passe (sinon il se retrouve connecté sans jamais avoir
                        # pu le changer).
                        st.session_state.pop(_cache_key, None)
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
                                    tva_sb_auth.update_user_password(_sb_result.access_token, _new_pwd)
                                    st.success(_("password_updated_success"))
                                    st.query_params.clear()
                                except Exception as _sb_err:
                                    st.error(_("password_update_error", error=str(_sb_err)))
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
                if _sb_nonce:
                    _diag_suffix = f" — diagnostic: {_pkce_diag}" if _pkce_diag else ""
                    st.error(f"{_('oauth_state_lost_error')} (prov={_sb_provider}, nonce={_sb_nonce[:8]}...){_diag_suffix}")
                    if st.button("Réessayer"):
                        st.query_params.clear()
                        st.rerun()
                    st.stop()

    # Cas C : Erreur spécifique (ex: email non vérifié)
    if _sb_error_code and st.session_state.get("auth_user") is None:
        if _sb_error_code == "provider_email_needs_verification":
            st.warning(_("oauth_email_verification_required"))
        else:
            st.error(f"Erreur OAuth ({_sb_error_code}): {_sb_error_desc or 'inconnue'}")
        
        if st.button(_("cancel_btn"), key="clear_oauth_error"):
            st.query_params.clear()
            st.rerun()
        st.stop()

    # ── 2. Conversion du fragment URL (#) en paramètres (?) ─────────────────
    if st.session_state.get("auth_user") is None:
        components.html(
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
            height=0,
        )

    try:
        _local_bypass = bool(get_secret("LOCAL_DEV_BYPASS_AUTH", False))
    except Exception:
        _local_bypass = False

    # ── Lecture des cookies ──────────────────────────────────────────────────
    # On privilégie st.context.cookies (synchrone, Streamlit 1.36+) pour éviter
    # les déconnexions au refresh. On garde CookieManager en fallback.
    try:
        _cookie_token = st.context.cookies.get("tva_session_token")
    except Exception:
        _cookie_token = cookie_manager.get("tva_session_token")

    # Si l'utilisateur a cliqué sur Déconnexion, on ignore le cookie pour cette session
    # Streamlit, même s'il n'a pas encore été effacé du navigateur.
    if st.session_state.get("manual_logout"):
        _cookie_token = None

    if _cookie_token and _cookie_token != "LOGGED_OUT" and st.session_state.get("auth_user") is None:
        _restored_user = tva_auth.get_user_by_session_token(_cookie_token)
        if _restored_user is not None:
            st.session_state["auth_user"] = _restored_user

    # ── Consommation du lien magique ────────────────────────────────────────
    _qp_token = st.query_params.get("login_token")
    if _qp_token:
        if st.session_state.get("auth_user") is None:
            st.info(_("magic_link_welcome"))
            if st.button(_("magic_link_confirm_btn"), key="confirm_magic_link"):
                _ip = "unknown"
                try:
                    from streamlit.web.server.websocket_headers import _get_websocket_headers  # type: ignore[import]
                    _headers = _get_websocket_headers()
                    if _headers:
                        _ip = _headers.get("X-Forwarded-For", _headers.get("Remote-Addr", "unknown")).split(",")[0]
                except Exception:
                    pass

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

    _qp_session_token = st.query_params.get("session_token")
    if _qp_session_token:
        cookie_manager.set("tva_session_token", _qp_session_token, expires_at=datetime.now() + timedelta(days=30))
        st.query_params.pop("session_token", None)
        st.rerun()

    # ── Interface de connexion non-authentifiée ────────────────────────────
    if st.session_state["auth_user"] is None:
        st.info(_("auth_required_info"))

        if _local_bypass:
            st.warning(_("dev_bypass_warning"))
            _dev_email = st.text_input(_("dev_email_label"), key="dev_login_email_input")
            if st.button(_("dev_login_btn"), key="btn_dev_login"):
                if _dev_email and "@" in _dev_email:
                    _finalize_login(_dev_email, cookie_manager)
                    st.rerun()
                else:
                    st.warning(_("invalid_email_warning"))
            st.stop()

        # ── Identification ──────────────────────────────────────────────────
        _login_email = st.text_input(_("email_label"), key="login_email_input")

        _tab_pwd, _tab_magic = st.tabs([_("password_signin_btn"), _("send_magic_link_btn")])

        with _tab_pwd:
            _login_password = st.text_input(_("password_label"), type="password", key="login_password_input")
            _col_signin, _col_signup = st.columns(2)

            if _col_signin.button(_("password_signin_btn"), key="btn_password_signin", use_container_width=True, type="primary"):
                if _login_email and "@" in _login_email and _login_password:
                    try:
                        _sb_res = tva_sb_auth.sign_in_with_password(_login_email, _login_password)
                        _finalize_login(_sb_res.email, cookie_manager)
                        st.rerun()
                    except Exception as _sb_err:
                        st.error(_("password_login_error", error=str(_sb_err)))
                else:
                    st.warning(_("invalid_email_warning"))

            if _col_signup.button(_("password_signup_btn"), key="btn_password_signup", use_container_width=True):
                if _login_email and "@" in _login_email and _login_password:
                    try:
                        _sb_res = tva_sb_auth.sign_up_with_password(_login_email, _login_password)
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
            with st.expander(_("forgot_password_btn")):
                st.caption(_("reset_password_instructions"))
                _reset_email = st.text_input(
                    _("email_label"), value=_login_email, key="reset_password_email_input"
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
                            _reset_redirect_to = _app_base_url_login
                            tva_sb_auth.reset_password_for_email(
                                _reset_email, redirect_to=_reset_redirect_to, code_challenge=_reset_challenge
                            )
                            st.success(_("reset_password_success"))
                        except Exception as _sb_err:
                            st.error(_("reset_password_error", error=str(_sb_err)))
                    else:
                        st.warning(_("invalid_email_warning"))

        with _tab_magic:
            st.caption(_("legacy_login_methods_caption"))
            if st.button(
                _("send_magic_link_btn"), key="btn_send_magic_link",
                use_container_width=True,
            ):
                if _login_email and "@" in _login_email:
                    try:
                        _magic_token = tva_auth.create_magic_link(_login_email)
                        _magic_url = f"{_app_base_url_login}/?login_token={_magic_token}"
                        tva_auth.send_magic_link_email(_login_email, _magic_url)
                        st.success(_("magic_link_sent_success", email=_login_email))
                    except Exception as _e:
                        st.error(_("magic_link_sent_error", error=str(_e)))
                else:
                    st.warning(_("invalid_email_warning"))

        # ── OAuth social (Google / GitHub / Amazon) — Supabase Auth ─
        st.caption(_("oauth_divider_label"))
        _col_google, _col_github, _col_amazon = st.columns(3)

        # ── Style officiel (logo + couleur) appliqué à st.link_button ──────
        # st.link_button est fiable pour sortir de l'iframe Streamlit Cloud
        # (contrairement à un <a> en HTML brut, cf. incident précédent), mais
        # n'a pas de paramètre pour un logo/une couleur de marque. On cible
        # donc chaque bouton via la classe "st-key-{key}" que Streamlit ajoute
        # automatiquement sur son conteneur, et on pose le logo en
        # background-image du <button> natif généré par Streamlit.
        st.markdown(
            f"""
            <style>
            .st-key-oauth_btn_google a[data-testid^="stBaseLinkButton"] {{
                background-color: #FFFFFF !important;
                color: #3C4043 !important;
                border: 1px solid #dadce0 !important;
                background-image: url('https://cdn.jsdelivr.net/gh/devicons/devicon@latest/icons/google/google-original.svg');
                background-repeat: no-repeat;
                background-position: 14px center;
                background-size: 18px 18px;
                padding-left: 38px !important;
            }}
            .st-key-oauth_btn_google a[data-testid^="stBaseLinkButton"] p {{ color: #3C4043 !important; }}
            .st-key-oauth_btn_github a[data-testid^="stBaseLinkButton"] {{
                background-color: #24292E !important;
                color: #FFFFFF !important;
                border: 1px solid #24292E !important;
                background-image: url('https://cdn.jsdelivr.net/gh/devicons/devicon@latest/icons/github/github-original.svg');
                background-repeat: no-repeat;
                background-position: 14px center;
                background-size: 18px 18px;
                padding-left: 38px !important;
            }}
            .st-key-oauth_btn_github a[data-testid^="stBaseLinkButton"] p {{ color: #FFFFFF !important; }}
            .st-key-oauth_btn_cognito a[data-testid^="stBaseLinkButton"] {{
                background-color: #FF9900 !important;
                color: #000000 !important;
                border: 1px solid #FF9900 !important;
                background-image: url('https://upload.wikimedia.org/wikipedia/commons/4/4a/Amazon_icon.svg');
                background-repeat: no-repeat;
                background-position: 14px center;
                background-size: 18px 18px;
                padding-left: 38px !important;
            }}
            .st-key-oauth_btn_cognito a[data-testid^="stBaseLinkButton"] p {{ color: #000000 !important; }}
            </style>
            """,
            unsafe_allow_html=True,
        )

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
                    _redirect_to = f"{_app_base_url_login}/?sb_provider={_provider}&sb_nonce={_nonce}"
                    _oauth_url = tva_sb_auth.build_oauth_authorize_url(_provider, _redirect_to, _verifier)

                    # st.link_button natif plutôt qu'un <a> en HTML brut : ce
                    # dernier s'est révélé peu fiable pour sortir de l'iframe
                    # Streamlit Cloud (clic sans effet, malgré un href valide et
                    # un survol fonctionnel) — st.link_button utilise le
                    # mécanisme de navigation propre à Streamlit, garanti de
                    # fonctionner dans cet environnement.
                    st.link_button(_(_label_key), _oauth_url, use_container_width=True,
                                    key=f"oauth_btn_{_provider}")
                except Exception as _oauth_render_err:
                    st.error(f"⛔ {_provider} : {_oauth_render_err}")
                    st.button(_(_label_key), key=f"btn_oauth_disabled_{_provider}", disabled=True,
                              use_container_width=True)

        st.stop()

    # ── Barre d'état connecté ──────────────────────────────────────────────
    _current_user = st.session_state["auth_user"]

    if _current_user is not None:
        _col_user, _col_logout = st.columns([5, 1])
        _col_user.caption(_("logged_in_as", email=_current_user.email))
        if _col_logout.button(_("logout_btn"), key="btn_logout"):
            # 1. Invalidation côté serveur
            try:
                _current_token = st.context.cookies.get("tva_session_token")
            except Exception:
                _current_token = cookie_manager.get("tva_session_token")
            if _current_token:
                try:
                    tva_auth.delete_session_token(_current_token)
                except Exception:
                    pass
            
            # 2. Nettoyage session locale
            st.session_state["auth_user"] = None
            st.session_state["manual_logout"] = True
            
            # 3. Suppression du cookie (asynchrone côté client)
            try:
                cookie_manager.delete("tva_session_token", key=f"logout_del_{int(time.time())}")
            except Exception:
                pass

            st.query_params.clear()
            st.rerun()

        _app_base_url = _resolve_app_base_url()
        _vies_scope_id = _vies_resolve_scope_id(_current_user.email)

        return AuthContext(
            current_user=_current_user,
            cookie_manager=cookie_manager,
            app_base_url=_app_base_url,
            vies_scope_id=_vies_scope_id,
        )

    st.stop()
