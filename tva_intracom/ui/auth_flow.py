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
import extra_streamlit_components as stx

from tva_intracom import auth as tva_auth
from tva_intracom.vies import (
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
        pour éviter une déconnexion (voir tva_intracom/auth.py :
        create_session_token / get_user_by_session_token). Sans ça, la
        redirection Stripe (navigation complète du navigateur) fait perdre la
        session Streamlit et forcerait à redemander un lien de connexion."""
        _tok = st.query_params.get("session_token", "")
        _qs = f"session_token={_tok}" if _tok else ""
        if extra_qs:
            _qs = f"{_qs}&{extra_qs}" if _qs else extra_qs
        return f"{self.app_base_url}/?{_qs}" if _qs else f"{self.app_base_url}/"

    def stripe_cancel_url(self) -> str:
        _tok = st.query_params.get("session_token", "")
        return f"{self.app_base_url}/?session_token={_tok}" if _tok else f"{self.app_base_url}/"


def ensure_cookie_manager() -> "stx.CookieManager":
    """Instancie le gestionnaire de cookies et exécute la maintenance
    ponctuelle (purge du cache VIES mal préfixé) une fois par session."""
    # Instanciation du gestionnaire de cookies
    # On utilise un délai pour laisser le temps au composant JS de s'initialiser
    cookie_manager = stx.CookieManager()

    # Petit délai d'attente pour l'initialisation du composant CookieManager au premier chargement
    if not cookie_manager.get_all():
        time.sleep(0.1)

    if "_malformed_vies_purged" not in st.session_state:
        try:
            _vies_purge_malformed_entries()
        except Exception:
            pass
        st.session_state["_malformed_vies_purged"] = True

    return cookie_manager


def run_auth_flow(cookie_manager: "stx.CookieManager") -> AuthContext:
    """Exécute le flux complet d'authentification.

    Bloque l'exécution du script (st.stop()) tant que l'utilisateur n'est
    pas authentifié — comportement identique à l'ancien bloc top-level
    de app.py. Retourne un AuthContext une fois la connexion établie.
    """
    # =========================================================================
    # AUTHENTIFICATION — magic link par e-mail (voir tva_intracom/auth.py)
    # =========================================================================
    if "auth_user" not in st.session_state:
        st.session_state["auth_user"] = None

    # ── Bypass d'authentification en développement local UNIQUEMENT ────────
    # Contrôlé par le secret LOCAL_DEV_BYPASS_AUTH, qui ne doit exister QUE dans
    # le fichier .streamlit/secrets.toml local (jamais commité, cf. .gitignore) —
    # jamais défini dans les secrets Streamlit Cloud de production. Permet de
    # développer sans dépendre de Resend (limité à une adresse en mode test),
    # tout en gardant la possibilité de tester plusieurs adresses/domaines
    # (utile pour la portée du cache VIES par domaine, et les quotas SIREN par
    # compte) : on saisit l'adresse de son choix, sans envoi réel de mail.
    try:
        _local_bypass = bool(st.secrets.get("LOCAL_DEV_BYPASS_AUTH", False))
    except Exception:
        _local_bypass = False

    # ── Restauration de session via Cookie (Conformité Amazon DPP) ──────────
    _cookie_token = cookie_manager.get("tva_session_token")

    if _cookie_token and st.session_state.get("auth_user") is None:
        _restored_user = tva_auth.get_user_by_session_token(_cookie_token)
        if _restored_user is not None:
            st.session_state["auth_user"] = _restored_user

    # ── Consommation du lien magique (seulement si pas déjà connecté) ──────
    _qp_token = st.query_params.get("login_token")
    if _qp_token:
        if st.session_state.get("auth_user") is None:
            # On demande une confirmation manuelle (clic) pour éviter que les robots
            # d'indexation de mails ne "consomment" le lien à usage unique avant l'utilisateur.
            st.info("👋 Bienvenue ! Cliquez sur le bouton ci-dessous pour finaliser votre connexion.")
            if st.button("🚀 Confirmer la connexion", key="confirm_magic_link"):
                # Récupération de l'IP pour le rate limiting : purement best-effort.
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
                    st.error(f"⛔ Erreur lors de la connexion : {e}")
                    _u = None

                if _u is not None:
                    st.session_state["auth_user"] = _u
                    _new_session_token = tva_auth.create_session_token(_u.id)

                    # On écrit le cookie
                    cookie_manager.set(
                        "tva_session_token",
                        _new_session_token,
                        expires_at=datetime.now() + timedelta(days=30),
                        key="set_cookie_on_login"
                    )
                    # On nettoie l'URL et on relance
                    st.query_params.clear()
                    st.rerun()
                else:
                    st.error("⛔ Lien de connexion invalide, expiré ou déjà consommé. Redemandez-en un ci-dessous.")
            st.stop()
        else:
            # L'utilisateur est déjà connecté (peut-être via le cookie juste avant)
            # On nettoie juste l'URL pour être propre
            st.query_params.clear()
            st.rerun()

    # Cas particulier : migration d'un ancien lien session_token vers cookie
    _qp_session_token = st.query_params.get("session_token")
    if _qp_session_token:
        cookie_manager.set("tva_session_token", _qp_session_token, expires_at=datetime.now() + timedelta(days=30))
        st.query_params.pop("session_token", None)
        st.rerun()

    # ── Consommation du callback Amazon SP-API OAuth ───────────────────────
    _spapi_code = st.query_params.get("spapi_oauth_code")
    _spapi_selling_partner_id = st.query_params.get("selling_partner_id")
    if _spapi_code and _spapi_selling_partner_id:
        from tva_intracom import amazon_spapi
        try:
            # 1. Échange du code contre les tokens
            _tokens = amazon_spapi.exchange_code_for_token(_spapi_code)
            _refresh_token = _tokens.get("refresh_token")
            _access_token = _tokens.get("access_token")

            if _refresh_token:
                # 2. On essaie de récupérer l'e-mail Amazon pour identifier/créer l'utilisateur
                # (Login with Amazon / LWA Profile)
                _amz_email = None
                try:
                    if _access_token:
                        _amz_email = amazon_spapi.get_seller_email(_access_token)
                except Exception:
                    pass

                _current_u = st.session_state.get("auth_user")

                # Cas A : Connexion (Login with Amazon) - On n'est pas encore connecté
                if _current_u is None and _amz_email:

                    _current_u = tva_auth.get_or_create_user(_amz_email)
                    st.session_state["auth_user"] = _current_u
                    _new_token = tva_auth.create_session_token(_current_u.id)
                    cookie_manager.set("tva_session_token", _new_token, expires_at=datetime.now() + timedelta(days=30))

                # Cas B : Liaison (L'utilisateur est déjà connecté, on lie juste le compte Amazon)
                if _current_u:
                    tva_auth.save_amazon_credentials(
                        _current_u.id, _spapi_selling_partner_id, _refresh_token
                    )
                    st.success(f"✅ Compte Amazon lié avec succès ({_current_u.email}) !")
                    time.sleep(1)
                else:
                    st.error("Impossible d'identifier votre compte Amazon. Connectez-vous d'abord par e-mail.")
            else:
                st.error("Erreur : Aucun refresh token reçu d'Amazon.")
        except Exception as _e:
            st.error(f"Erreur lors de la connexion Amazon : {_e}")

        # On nettoie l'URL
        st.query_params.clear()
        st.rerun()

    if st.session_state["auth_user"] is None:
        st.info("🔐 Connectez-vous pour utiliser le moteur de TVA.")

        if _local_bypass:
            st.warning("🛠️ Mode développement local — connexion directe, sans envoi de mail.")
            _dev_email = st.text_input("Adresse e-mail (n'importe laquelle, pour test)", key="dev_login_email_input")
            if st.button("Se connecter (dev)", key="btn_dev_login"):
                if _dev_email and "@" in _dev_email:
                    _dev_user = tva_auth.get_or_create_user(_dev_email)
                    st.session_state["auth_user"] = _dev_user
                    _dev_token = tva_auth.create_session_token(_dev_user.id)
                    cookie_manager.set(
                        "tva_session_token",
                        _dev_token,
                        expires_at=datetime.now() + timedelta(days=30)
                    )
                    st.rerun()
                else:
                    st.warning("Adresse e-mail invalide.")
            st.stop()

        _login_email = st.text_input("Adresse e-mail", key="login_email_input")
        _col_magic, _col_amazon = st.columns(2)

        if _col_magic.button("Recevoir un lien de connexion", key="btn_send_magic_link", use_container_width=True):
            if _login_email and "@" in _login_email:
                _token = tva_auth.create_magic_link(_login_email)
                _base_url = st.secrets.get("APP_BASE_URL", "https://tva-intracom-ue.streamlit.app")
                _login_url = f"{_base_url}/?login_token={_token}"
                try:
                    tva_auth.send_magic_link_email(_login_email, _login_url)
                    st.success(f"✅ Lien envoyé à {_login_email}.")
                except Exception as _mail_err:
                    st.error(f"⛔ Échec de l'envoi : {_mail_err}")
            else:
                st.warning("Adresse e-mail invalide.")

        with _col_amazon:
            from tva_intracom import amazon_spapi
            _state = secrets.token_hex(8)
            try:
                _auth_url = amazon_spapi.get_authorization_url(state=_state)
                st.link_button("🚀 Connexion via Amazon", _auth_url, use_container_width=True, type="primary")
            except Exception:
                st.error("Amazon non configuré.")

        st.stop()

    _current_user = st.session_state["auth_user"]
    _col_user, _col_logout = st.columns([5, 1])
    _col_user.caption(f"Connecté : {_current_user.email}")
    if _col_logout.button("🚪 Déconnexion", key="btn_logout"):
        st.session_state["auth_user"] = None
        try:
            # On vérifie si le cookie existe avant de tenter de le supprimer
            if "tva_session_token" in cookie_manager.get_all():
                cookie_manager.delete("tva_session_token")
        except Exception:
            pass
        st.query_params.clear()
        st.rerun()

    _app_base_url = st.secrets.get("APP_BASE_URL", "https://tva-intracom-ue.streamlit.app")

    # Portée du cache VIES (isolation compte/domaine — voir tva_intracom/vies.py)
    _vies_scope_id = _vies_resolve_scope_id(_current_user.email)

    return AuthContext(
        current_user=_current_user,
        cookie_manager=cookie_manager,
        app_base_url=_app_base_url,
        vies_scope_id=_vies_scope_id,
    )
