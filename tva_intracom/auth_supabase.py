"""Authentification via Supabase Auth (GoTrue) — mot de passe + OAuth social.

Complète l'authentification historique par lien magique / compte Amazon
(tva_intracom/auth.py), qui reste en place mais désactivée dans l'écran de
connexion (voir ui/auth_flow.py, boutons grisés "en préparation").

Une fois authentifié via Supabase Auth, l'utilisateur est mappé sur un
tva_users local (par e-mail) via tva_auth.get_or_create_user() — la table
tva_users reste la source de vérité pour home_country/language/
display_currency/SIREN/etc. Supabase Auth ne sert que de vérificateur
d'identité (mot de passe, Google, Microsoft, GitHub).

Secrets requis (Streamlit Cloud / variables d'environnement) :
    SUPABASE_URL        — ex: https://xxxx.supabase.co
    SUPABASE_ANON_KEY    — clé publique "anon" du projet Supabase.
                            ⚠️ Ne JAMAIS utiliser la clé service_role ici :
                            cette clé anon est prévue pour être exposée
                            côté client, contrairement à SUPABASE_DB_URL
                            (accès direct Postgres, à garder secret).

Configuration côté tableau de bord Supabase (Authentication > Providers) :
    - Email : activer "Enable email provider" (mot de passe). Décider si
      "Confirm email" reste activé (l'utilisateur devra cliquer un lien
      envoyé par Supabase avant sa première connexion) ou non.
    - Google / Azure (= Microsoft, Entra ID) / GitHub : activer chaque
      provider, renseigner Client ID / Client Secret obtenus depuis la
      console du provider concerné, et déclarer l'URL de callback Supabase
      (affichée dans le panneau de configuration du provider, du type
      https://xxxx.supabase.co/auth/v1/callback) côté provider.
    - Amazon (Login with Amazon / LWA) : Authentication > Providers >
      "Add custom OAuth provider". Provider Identifier = "amazon" (utilisé
      ensuite en interne sous la forme "custom:amazon"). Configuration
      manuelle : Authorization URL = https://www.amazon.com/ap/oa,
      Token URL = https://api.amazon.com/auth/o2/token,
      Userinfo URL = https://api.amazon.com/user/profile, Scopes = "profile",
      Client ID / Client Secret = ceux de l'app Amazon LWA (Seller Central
      > Login with Amazon, PAS l'app SP-API utilisée par ailleurs pour la
      récupération des rapports). Coller la "Callback URL" affichée par
      Supabase dans la configuration Amazon LWA côté Seller Central.
    - Authentication > URL Configuration : ajouter l'URL de l'app
      (APP_BASE_URL) à la liste "Redirect URLs", sinon Supabase refusera la
      redirection post-connexion.
"""
from __future__ import annotations

import base64
import hashlib
import secrets as _secrets
from dataclasses import dataclass
from typing import Optional
from urllib.parse import urlencode

import requests

from .config import get_secret

# "azure" est l'identifiant de provider utilisé par Supabase pour Microsoft
# (Azure AD / Entra ID). "custom:amazon" correspond au Custom OAuth Provider
# "amazon" configuré manuellement côté tableau de bord Supabase.
# "custom:cognito" est utilisé pour Amazon Cognito via un Custom OAuth Provider.
OAUTH_PROVIDERS = {
    "google": "google",
    "microsoft": "azure",
    "github": "github",
    "cognito": "cognito",
}


def _base_url() -> str:
    url = get_secret("SUPABASE_URL")
    if not url:
        raise RuntimeError(
            "SUPABASE_URL non défini — configurez ce secret pour activer la "
            "connexion par mot de passe / OAuth Supabase."
        )
    return url.rstrip("/")


def _anon_key() -> str:
    key = get_secret("SUPABASE_ANON_KEY")
    if not key:
        raise RuntimeError(
            "SUPABASE_ANON_KEY non défini — configurez ce secret (clé anon, "
            "jamais service_role) pour activer la connexion Supabase Auth."
        )
    return key


def _headers() -> dict:
    key = _anon_key()
    return {"apikey": key, "Authorization": f"Bearer {key}", "Content-Type": "application/json"}


def _raise_for_supabase_error(resp: requests.Response) -> None:
    if resp.status_code >= 400:
        try:
            payload = resp.json()
            msg = payload.get("error_description") or payload.get("msg") or payload.get("error") or resp.text
        except Exception:
            msg = resp.text
        raise RuntimeError(msg)


@dataclass
class SupabaseAuthResult:
    email: str
    access_token: str
    refresh_token: Optional[str] = None


# ── Mot de passe ─────────────────────────────────────────────────────────────

def sign_up_with_password(email: str, password: str) -> SupabaseAuthResult:
    """Crée un compte Supabase Auth par mot de passe. Selon la configuration
    du projet ("Confirm email"), l'utilisateur peut devoir valider son
    adresse via un e-mail Supabase avant de pouvoir se connecter — dans ce
    cas `access_token` est vide dans la réponse."""
    resp = requests.post(
        f"{_base_url()}/auth/v1/signup",
        headers=_headers(),
        json={"email": email.strip().lower(), "password": password},
        timeout=10,
    )
    _raise_for_supabase_error(resp)
    data = resp.json()
    return SupabaseAuthResult(
        email=email.strip().lower(),
        access_token=data.get("access_token", "") or "",
        refresh_token=data.get("refresh_token"),
    )


def sign_in_with_password(email: str, password: str) -> SupabaseAuthResult:
    resp = requests.post(
        f"{_base_url()}/auth/v1/token?grant_type=password",
        headers=_headers(),
        json={"email": email.strip().lower(), "password": password},
        timeout=10,
    )
    _raise_for_supabase_error(resp)
    data = resp.json()
    return SupabaseAuthResult(
        email=email.strip().lower(),
        access_token=data["access_token"],
        refresh_token=data.get("refresh_token"),
    )


# ── OAuth (Google / Microsoft / GitHub) — flux PKCE ─────────────────────────
#
# Le mode "implicit" de Supabase Auth renvoie les jetons dans le fragment
# d'URL (#access_token=...), invisible côté serveur (Streamlit ne peut pas le
# lire, un fragment n'est jamais envoyé au serveur). On utilise donc le mode
# PKCE : Supabase redirige avec un `?code=` classique en paramètre de
# requête, échangeable côté serveur contre une session.

def new_code_verifier() -> str:
    return base64.urlsafe_b64encode(_secrets.token_bytes(48)).decode().rstrip("=")


def build_oauth_authorize_url(provider: str, redirect_to: str, code_verifier: str) -> str:
    if provider not in OAUTH_PROVIDERS:
        raise ValueError(f"Provider OAuth non supporté : {provider}")
    challenge = base64.urlsafe_b64encode(hashlib.sha256(code_verifier.encode()).digest()).decode().rstrip("=")
    params = {
        "provider": OAUTH_PROVIDERS[provider],
        "redirect_to": redirect_to,
        "code_challenge": challenge,
        "code_challenge_method": "s256",
    }
    return f"{_base_url()}/auth/v1/authorize?{urlencode(params)}"


def exchange_pkce_code(code: str, code_verifier: str) -> SupabaseAuthResult:
    resp = requests.post(
        f"{_base_url()}/auth/v1/token?grant_type=pkce",
        headers=_headers(),
        json={"auth_code": code, "code_verifier": code_verifier},
        timeout=10,
    )
    _raise_for_supabase_error(resp)
    data = resp.json()
    access_token = data["access_token"]

    user_resp = requests.get(
        f"{_base_url()}/auth/v1/user",
        headers={"apikey": _anon_key(), "Authorization": f"Bearer {access_token}"},
        timeout=10,
    )
    _raise_for_supabase_error(user_resp)
    email = (user_resp.json().get("email") or "").strip().lower()
    if not email:
        raise RuntimeError(
            "Le fournisseur OAuth n'a pas renvoyé d'adresse e-mail — vérifiez "
            "que le scope 'email' est bien demandé côté configuration du provider."
        )
    return SupabaseAuthResult(email=email, access_token=access_token, refresh_token=data.get("refresh_token"))
