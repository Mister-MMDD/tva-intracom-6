"""Authentification légère par e-mail (lien magique) — tva_intracom.

Backend Postgres (Supabase) — remplace la version SQLite locale : cette base
doit être lisible/écrivable à la fois depuis l'app Streamlit Cloud et depuis
la fonction serverless du webhook Stripe (voir billing.py), qui ne partagent
aucun disque.

Connexion : variable d'environnement SUPABASE_DB_URL (chaîne de connexion
Postgres complète, ex: postgresql://user:pass@host:5432/postgres). Jamais en
dur dans le code — à définir dans les secrets Streamlit Cloud ET dans les
variables d'environnement Vercel.

Dépendance ajoutée à requirements.txt : psycopg2-binary
"""
from __future__ import annotations

import os
import secrets
import time
from dataclasses import dataclass
from typing import Optional

import psycopg2
import psycopg2.pool
import requests
import streamlit as st

MAGIC_LINK_TTL_SECONDS = 15 * 60

_pool: Optional[psycopg2.pool.SimpleConnectionPool] = None


def _get_pool() -> psycopg2.pool.SimpleConnectionPool:
    global _pool
    if _pool is None:
        dsn = st.secrets.get("SUPABASE_DB_URL") or os.environ.get("SUPABASE_DB_URL")
        
        if not dsn:
            raise RuntimeError(
                "SUPABASE_DB_URL non définie — impossible de se connecter à la base "
                "d'authentification. Configurez ce secret côté Streamlit Cloud et Vercel."
            )
        _pool = psycopg2.pool.SimpleConnectionPool(1, 5, dsn)
        _init_schema()
    return _pool


def _init_schema() -> None:
    conn = _pool.getconn()
    try:
        with conn, conn.cursor() as cur:
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS tva_users (
                    id TEXT PRIMARY KEY,
                    email TEXT UNIQUE NOT NULL,
                    created_at DOUBLE PRECISION NOT NULL,
                    is_cabinet BOOLEAN NOT NULL DEFAULT FALSE,
                    cabinet_parent_id TEXT
                )
                """
            )
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS tva_magic_links (
                    token TEXT PRIMARY KEY,
                    email TEXT NOT NULL,
                    created_at DOUBLE PRECISION NOT NULL,
                    consumed BOOLEAN NOT NULL DEFAULT FALSE
                )
                """
            )
    finally:
        _pool.putconn(conn)


@dataclass
class User:
    id: str
    email: str
    is_cabinet: bool = False
    cabinet_parent_id: Optional[str] = None


def get_or_create_user(email: str) -> User:
    email = email.strip().lower()
    pool = _get_pool()
    conn = pool.getconn()
    try:
        with conn, conn.cursor() as cur:
            cur.execute(
                "SELECT id, email, is_cabinet, cabinet_parent_id FROM tva_users WHERE email=%s",
                (email,),
            )
            row = cur.fetchone()
            if row:
                return User(id=row[0], email=row[1], is_cabinet=bool(row[2]), cabinet_parent_id=row[3])
            user_id = secrets.token_hex(12)
            cur.execute(
                "INSERT INTO tva_users (id, email, created_at) VALUES (%s, %s, %s)",
                (user_id, email, time.time()),
            )
            return User(id=user_id, email=email)
    finally:
        pool.putconn(conn)


def create_magic_link(email: str) -> str:
    """Génère un jeton de connexion à usage unique. L'envoi de l'e-mail
    (provider transactionnel type Resend/Postmark) reste hors scope ici."""
    token = secrets.token_urlsafe(32)
    pool = _get_pool()
    conn = pool.getconn()
    try:
        with conn, conn.cursor() as cur:
            cur.execute(
                "INSERT INTO tva_magic_links (token, email, created_at) VALUES (%s, %s, %s)",
                (token, email.strip().lower(), time.time()),
            )
    finally:
        pool.putconn(conn)
    return token


def send_magic_link_email(email: str, login_url: str) -> None:
    """Envoie l'e-mail contenant le lien de connexion via l'API Resend
    (https://resend.com/docs/api-reference/emails/send-email).

    Nécessite deux secrets/variables d'environnement :
        RESEND_API_KEY    — clé API Resend
        RESEND_FROM_EMAIL — adresse d'expédition vérifiée dans Resend
                            (ex: "TVA Intracom <connexion@tondomaine.fr>")

    Utilise `requests`, déjà présent dans requirements.txt — aucune nouvelle
    dépendance nécessaire (pas besoin du SDK officiel `resend`).
    """
    api_key = os.environ.get("RESEND_API_KEY") or st.secrets.get("RESEND_API_KEY")
    from_email = os.environ.get("RESEND_FROM_EMAIL") or st.secrets.get("RESEND_FROM_EMAIL")
    if not api_key or not from_email:
        raise RuntimeError(
            "RESEND_API_KEY / RESEND_FROM_EMAIL non configurés — impossible d'envoyer "
            "le lien de connexion par e-mail."
        )
    response = requests.post(
        "https://api.resend.com/emails",
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json={
            "from": from_email,
            "to": [email],
            "subject": "Votre lien de connexion — TVA Intracom",
            "html": (
                "<p>Bonjour,</p>"
                "<p>Voici votre lien de connexion au moteur de TVA intracommunautaire "
                "(valable 15 minutes) :</p>"
                f'<p><a href="{login_url}">{login_url}</a></p>'
                "<p>Si vous n'êtes pas à l'origine de cette demande, ignorez cet e-mail.</p>"
            ),
        },
        timeout=10,
    )
    response.raise_for_status()


def consume_magic_link(token: str) -> Optional[User]:
    """Valide un jeton de connexion. Retourne None si invalide, expiré, ou déjà utilisé."""
    pool = _get_pool()
    conn = pool.getconn()
    try:
        with conn, conn.cursor() as cur:
            cur.execute(
                "SELECT email, created_at, consumed FROM tva_magic_links WHERE token=%s",
                (token,),
            )
            row = cur.fetchone()
            if not row:
                return None
            email, created_at, consumed = row
            if consumed or (time.time() - created_at) > MAGIC_LINK_TTL_SECONDS:
                return None
            cur.execute("UPDATE tva_magic_links SET consumed=TRUE WHERE token=%s", (token,))
    finally:
        pool.putconn(conn)
    return get_or_create_user(email)