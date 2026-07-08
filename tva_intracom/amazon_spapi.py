"""Intégration Amazon Selling Partner API (SP-API) — OAuth 2.0 & Reports."""
from __future__ import annotations

import os
import time
import requests
import streamlit as st
from typing import Optional

def get_authorization_url(state: str) -> str:
    """Génère l'URL pour rediriger l'utilisateur vers Amazon Seller Central."""
    app_id = st.secrets.get("AMAZON_APP_ID")
    if not app_id:
        raise RuntimeError("AMAZON_APP_ID non configuré dans les secrets.")
    
    # URL de base pour l'Europe. À adapter si besoin pour d'autres régions.
    base_url = "https://sellercentral-europe.amazon.com/apps/authorize/consent"
    return f"{base_url}?application_id={app_id}&state={state}&version=beta"

def exchange_code_for_token(code: str) -> dict:
    """Échange le code d'autorisation (spapi_oauth_code) contre un refresh token."""
    client_id = st.secrets.get("AMAZON_CLIENT_ID")
    client_secret = st.secrets.get("AMAZON_CLIENT_SECRET")
    
    if not client_id or not client_secret:
        raise RuntimeError("AMAZON_CLIENT_ID ou AMAZON_CLIENT_SECRET non configurés.")

    url = "https://api.amazon.com/auth/o2/token"
    data = {
        "grant_type": "authorization_code",
        "code": code,
        "client_id": client_id,
        "client_secret": client_secret,
    }
    
    response = requests.post(url, data=data)
    response.raise_for_status()
    return response.json()

def get_access_token(refresh_token: str) -> str:
    """Utilise le refresh token pour obtenir un access token temporaire."""
    client_id = st.secrets.get("AMAZON_CLIENT_ID")
    client_secret = st.secrets.get("AMAZON_CLIENT_SECRET")
    
    url = "https://api.amazon.com/auth/o2/token"
    data = {
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
        "client_id": client_id,
        "client_secret": client_secret,
    }
    
    response = requests.post(url, data=data)
    response.raise_for_status()
    return response.json()["access_token"]


def get_seller_email(access_token: str) -> str:
    """Récupère l'adresse e-mail du vendeur via le endpoint LWA Profile.
    Note : Nécessite que le scope 'profile' soit autorisé dans la config LWA."""
    url = "https://api.amazon.com/user/profile"
    headers = {"Authorization": f"Bearer {access_token}"}
    response = requests.get(url, headers=headers)
    response.raise_for_status()
    # Retourne typiquement {"user_id": "...", "email": "...", "name": "..."}
    return response.json().get("email")

# Note: La suite de l'implémentation (récupération des rapports) nécessitera
# l'usage de endpoints spécifiques de SP-API (Reports API v2021-06-30).
