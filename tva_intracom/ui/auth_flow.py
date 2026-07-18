"""Flux d'authentification de l'application (extrait tel quel de app.py).

Regroupe :
  - l'instanciation du gestionnaire de cookies (extra_streamlit_components)
    et la purge ponctuelle du cache VIES mal préfixé (une fois par session) ;
  - la restauration de session via cookie ;
  - la consommation du lien magique (magic link) envoyé par e-mail ;
  - la migration d'un ancien lien `?session_token=` vers cookie ;
  - la consommation du callback OAuth Amazon SP-API ;
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


def get_or_create_spapi_oauth_state() -> str:
    """Génère (ou réutilise) le paramètre `state` de la demande d'autorisation
    Amazon SP-API et le conserve en session pour vérification au retour du
    callback (protection CSRF standard sur un flux OAuth).

    Un nouveau `state` n'est généré que s'il n'y en a pas déjà un "en attente"
    en session — évite d'en émettre un nouveau à chaque rerun Streamlit tant
    que l'utilisateur n'a pas cliqué sur le bouton de connexion Amazon.
    """
    if "_spapi_oauth_state" not in st.session_state:
        st.session_state["_spapi_oauth_state"] = secrets.token_urlsafe(24)
    return st.session_state["_spapi_oauth_state"]


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
    ponctuelle (purge du cache VIES mal préfixé) une fois par session."""
    cookie_manager = stx.CookieManager(key="tva_cookie_manager")

    if not cookie_manager.get_all(key="ensure_cookies"):
        time.sleep(0.1)

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

    # ── 1. Interception PRIORITAIRE du code OAuth (PKCE ou Implicit) ────────
    _qp = st.query_params
    _sb_code = _qp.get("code")
    _sb_provider = _qp.get("sb_provider")
    _sb_nonce = _qp.get("sb_nonce")
    _sb_access_token = _qp.get("access_token")
    _sb_error_code = _qp.get("error_code")
    _sb_error_desc = _qp.get("error_description")

    if st.session_state.get("auth_user") is None:
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
                    _sb_result = tva_sb_auth.exchange_pkce_code(_sb_code, _verifier)
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

    # ── Lecture unique des cookies pour tout le reste de la fonction ────────
    # ⚠️ Avec extra_streamlit_components, chaque `key` distincte passée à
    # get_all()/get() crée une INSTANCE DE COMPOSANT SÉPARÉE, qui doit se
    # remonter et se resynchroniser indépendamment avec le navigateur. Utiliser
    # des clés différentes à plusieurs endroits (ce qui était le cas
    # auparavant : "ensure_cookies", "sb_oauth_cookies", clé absente...)
    # provoque des lectures désynchronisées : une instance peut ne pas encore
    # avoir reçu les cookies du navigateur alors qu'une autre les a déjà.
    # C'était la cause du "Session de connexion expirée ou perdue" après
    # retour de Google : le verifier PKCE était bien posé en cookie, mais lu
    # via une instance de composant qui n'avait pas fini de se synchroniser.
    _all_cookies = cookie_manager.get_all(key="tva_all_cookies")

    # ── Restauration de session via Cookie ──────────────────────────────────
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

    # ── Consommation du callback Amazon SP-API OAuth ───────────────────────
    _spapi_code = st.query_params.get("spapi_oauth_code")
    _spapi_selling_partner_id = st.query_params.get("selling_partner_id")
    if _spapi_code and _spapi_selling_partner_id:
        _returned_state = st.query_params.get("state")
        _expected_state = st.session_state.pop("_spapi_oauth_state", None)
        # Le state est à usage unique : on le retire de la session dès qu'on
        # le lit, qu'il soit valide ou non, pour empêcher toute réutilisation.
        if not _expected_state or not _returned_state or _returned_state != _expected_state:
            st.error(_("amazon_oauth_state_mismatch_error"))
            st.query_params.clear()
            st.stop()

        from tva_intracom import amazon_spapi
        try:
            _tokens = amazon_spapi.exchange_code_for_token(_spapi_code)
            _refresh_token = _tokens.get("refresh_token")
            _access_token = _tokens.get("access_token")

            if _refresh_token:
                _amz_email = None
                try:
                    if _access_token:
                        _amz_email = amazon_spapi.get_seller_email(_access_token)
                except Exception:
                    pass

                _current_u = st.session_state.get("auth_user")

                if _current_u is None and _amz_email:
                    _finalize_login(_amz_email, cookie_manager)
                    _current_u = st.session_state.get("auth_user")

                if _current_u:
                    tva_auth.save_amazon_credentials(
                        _current_u.id, _spapi_selling_partner_id, _refresh_token
                    )
                    st.success(_("amazon_linked_success", email=_current_u.email))
                    time.sleep(1)
                else:
                    st.error(_("amazon_linked_error"))
            else:
                st.error(_("amazon_no_refresh_token"))
        except Exception as _e:
            st.error(_("amazon_auth_general_error", error=str(_e)))

        st.query_params.clear()
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

        _app_base_url_login = get_secret("APP_BASE_URL", "https://tva-intracom-ue.streamlit.app")

        # ── Mot de passe (Supabase Auth) ────────────────────────────────────
        _login_email = st.text_input(_("email_label"), key="login_email_input")
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

        # ── OAuth social (Google / Microsoft / GitHub / Amazon) — Supabase Auth ─
        # Le code_verifier PKCE est stocké côté serveur (Postgres,
        # tva_oauth_pkce), retrouvé au retour via un nonce transmis dans
        # `redirect_to` — plus fiable qu'un cookie posé depuis l'iframe du
        # composant extra_streamlit_components, qui ne survivait pas de
        # façon fiable à la redirection externe.
        #
        # ⚠️ Streamlit ré-exécute tout le script à chaque interaction (frappe
        # dans les champs mot de passe ci-dessus, etc.). Générer un nonce/
        # verifier NEUF à chaque rerun (comme avant) créait une ligne DB à
        # chaque fois et pouvait laisser le lien affiché dans le navigateur
        # pointer vers un nonce déjà remplacé par un plus récent au moment du
        # clic. On met donc en cache le couple (nonce, verifier) en
        # session_state — une seule écriture DB par provider tant que le
        # login n'a pas abouti.
        st.caption(_("oauth_divider_label"))
        _col_google, _col_microsoft, _col_github, _col_amazon = st.columns(4)
        for _col, _provider, _label_key in (
            (_col_google, "google", "oauth_google_btn"),
            (_col_microsoft, "microsoft", "oauth_microsoft_btn"),
            (_col_github, "github", "oauth_github_btn"),
            (_col_amazon, "cognito", "cognito_login_btn"),
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
                    st.link_button(_(_label_key), _oauth_url, use_container_width=True)
                except Exception:
                    st.button(_(_label_key), key=f"btn_oauth_disabled_{_provider}", use_container_width=True, disabled=True)

        st.divider()

        # ── Lien magique : en préparation ───────────────────────────────────
        # Reste dans le code (tva_auth.create_magic_link / send_magic_link_email)
        # mais désactivé côté écran de connexion le temps de finaliser sa
        # bascule éventuelle vers Supabase Auth (magic link natif Supabase) —
        # voir README. La connexion Amazon (ci-dessus) est en revanche
        # pleinement fonctionnelle via le Custom OAuth Provider Supabase.
        st.caption(_("legacy_login_methods_caption"))
        st.button(
            _("send_magic_link_btn"), key="btn_send_magic_link_disabled",
            use_container_width=True, disabled=True, help=_("coming_soon_help"),
        )

        st.stop()

    # ── Barre d'état connecté ──────────────────────────────────────────────
    _current_user = st.session_state["auth_user"]

    if _current_user is not None:
        _col_user, _col_logout = st.columns([5, 1])
        _col_user.caption(_("logged_in_as", email=_current_user.email))
        if _col_logout.button(_("logout_btn"), key="btn_logout"):
            # 1. Invalidation côté serveur
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

        _app_base_url = get_secret("APP_BASE_URL", "https://tva-intracom-ue.streamlit.app")
        _vies_scope_id = _vies_resolve_scope_id(_current_user.email)

        return AuthContext(
            current_user=_current_user,
            cookie_manager=cookie_manager,
            app_base_url=_app_base_url,
            vies_scope_id=_vies_scope_id,
        )

    st.stop()
