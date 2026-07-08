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

# Jeton de session : distinct du lien magique. Contrairement à celui-ci
# (usage unique, 15 min, consommé par create_magic_link/consume_magic_link),
# ce jeton reste valable plusieurs jours et n'est PAS à usage unique — il sert
# uniquement à restaurer la session (st.session_state) après une navigation
# complète du navigateur (redirection Stripe post-paiement, F5), qui fait
# perdre la session Streamlit en mémoire. Il est porté dans l'URL
# (?session_token=...) et ne doit jamais être envoyé par e-mail.
SESSION_TOKEN_TTL_SECONDS = 30 * 24 * 60 * 60

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
        _pool = psycopg2.pool.SimpleConnectionPool(1, 5, dsn, sslmode="require")
        _init_schema()
    return _pool


def _run(fn):
    """Exécute fn(conn, cur) avec une connexion prise dans le pool, avec un
    retry unique si la connexion s'avère fermée côté serveur.

    Contexte : le pool (`_pool`) est un objet global qui survit à toutes les
    reruns du script tant que le process Python tourne (Streamlit local en
    particulier). Le connecteur Supabase utilisé ici (port 6543, pooler
    PgBouncer en mode transaction) recycle agressivement les connexions
    inactives côté serveur — psycopg2.pool ne le détecte pas tant qu'on n'a
    pas essayé de s'en servir, d'où `psycopg2.InterfaceError: connection
    already closed` après un moment d'inactivité (typiquement après un F5 en
    localhost, session Streamlit restée ouverte sans requête depuis un
    moment). On jette alors tout le pool et on en recrée un pour retenter
    une fois, plutôt que de laisser planter la page."""
    global _pool
    last_exc: Exception | None = None
    for _attempt in range(2):
        pool = _get_pool()
        conn = pool.getconn()
        try:
            with conn, conn.cursor() as cur:
                result = fn(conn, cur)
            pool.putconn(conn)
            return result
        except (psycopg2.InterfaceError, psycopg2.OperationalError) as exc:
            last_exc = exc
            try:
                pool.putconn(conn, close=True)
            except Exception:
                pass
            _pool = None  # force la recréation d'un pool neuf au prochain tour
    raise last_exc


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
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS tva_session_tokens (
                    token TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    created_at DOUBLE PRECISION NOT NULL
                )
                """
            )
            # Table pour la protection brute-force (DPP Amazon)
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS tva_failed_logins (
                    ip_hash TEXT NOT NULL,
                    attempt_at DOUBLE PRECISION NOT NULL
                )
                """
            )
            cur.execute("CREATE INDEX IF NOT EXISTS idx_failed_logins_at ON tva_failed_logins(attempt_at)")
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

    def _fn(conn, cur):
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

    return _run(_fn)


def create_magic_link(email: str) -> str:
    """Génère un jeton de connexion à usage unique. L'envoi de l'e-mail
    (provider transactionnel type Resend/Postmark) reste hors scope ici."""
    token = secrets.token_urlsafe(32)
    _email = email.strip().lower()

    def _fn(conn, cur):
        cur.execute(
            "INSERT INTO tva_magic_links (token, email, created_at) VALUES (%s, %s, %s)",
            (token, _email, time.time()),
        )

    _run(_fn)
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


def consume_magic_link(token: str, ip_address: str = "unknown") -> Optional[User]:
    """Valide un jeton de connexion. Retourne None si invalide, expiré, ou déjà utilisé.
    Inclut une protection brute-force (DPP Amazon)."""
    import hashlib
    ip_hash = hashlib.sha256(ip_address.encode()).hexdigest()

    def _fn(conn, cur):
        # 1. Vérifier le brute-force : max 5 échecs en 5 minutes pour cet IP hash
        cutoff = time.time() - 300
        cur.execute(
            "SELECT COUNT(*) FROM tva_failed_logins WHERE ip_hash=%s AND attempt_at > %s",
            (ip_hash, cutoff)
        )
        failed_count = cur.fetchone()[0]
        if failed_count >= 5:
            return "rate_limited"

        # 2. Vérifier le token
        cur.execute(
            "SELECT email, created_at, consumed FROM tva_magic_links WHERE token=%s",
            (token,),
        )
        row = cur.fetchone()
        
        if not row:
            # Enregistrer l'échec
            cur.execute("INSERT INTO tva_failed_logins (ip_hash, attempt_at) VALUES (%s, %s)", (ip_hash, time.time()))
            conn.commit()
            return None
            
        email, created_at, consumed = row
        if consumed or (time.time() - created_at) > MAGIC_LINK_TTL_SECONDS:
            cur.execute("INSERT INTO tva_failed_logins (ip_hash, attempt_at) VALUES (%s, %s)", (ip_hash, time.time()))
            conn.commit()
            return None
            
        # Succès : on nettoie les anciens échecs pour cet IP et on marque consommé
        cur.execute("DELETE FROM tva_failed_logins WHERE ip_hash=%s", (ip_hash,))
        cur.execute("UPDATE tva_magic_links SET consumed=TRUE WHERE token=%s", (token,))
        return email

    res = _run(_fn)
    if res == "rate_limited":
        raise PermissionError("Trop de tentatives de connexion. Réessayez dans 5 minutes.")
    if not res:
        return None
    return get_or_create_user(res)


def create_session_token(user_id: str) -> str:
    """Génère un jeton de session longue durée (30 jours), réutilisable
    (contrairement au lien magique), destiné à être porté dans l'URL pour
    restaurer la connexion après une redirection externe (paiement Stripe)
    ou un rafraîchissement de page — sans consommer un nouveau lien magique
    à usage unique (limité côté Resend en mode test)."""
    token = secrets.token_urlsafe(32)

    def _fn(conn, cur):
        cur.execute(
            "INSERT INTO tva_session_tokens (token, user_id, created_at) VALUES (%s, %s, %s)",
            (token, user_id, time.time()),
        )

    _run(_fn)
    return token


def get_user_by_session_token(token: str) -> Optional[User]:
    """Retourne l'utilisateur associé à un jeton de session valide (non
    expiré), sans le consommer — il reste utilisable jusqu'à expiration."""
    def _fetch_token(conn, cur):
        cur.execute(
            "SELECT user_id, created_at FROM tva_session_tokens WHERE token=%s",
            (token,),
        )
        return cur.fetchone()

    row = _run(_fetch_token)
    if not row:
        return None
    user_id, created_at = row
    if (time.time() - created_at) > SESSION_TOKEN_TTL_SECONDS:
        return None

    def _fetch_user(conn, cur):
        cur.execute(
            "SELECT id, email, is_cabinet, cabinet_parent_id FROM tva_users WHERE id=%s",
            (user_id,),
        )
        return cur.fetchone()

    urow = _run(_fetch_user)
    if not urow:
        return None
    return User(id=urow[0], email=urow[1], is_cabinet=bool(urow[2]), cabinet_parent_id=urow[3])