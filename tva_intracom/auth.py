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
import threading
import time
from dataclasses import dataclass
from typing import Optional

import psycopg2
import psycopg2.pool
import requests
import streamlit as st
from .config import get_secret
from .security import encrypt_data, decrypt_data

MAGIC_LINK_TTL_SECONDS = 15 * 60

# Jeton de session : distinct du lien magique. Contrairement à celui-ci
# (usage unique, 15 min, consommé par create_magic_link/consume_magic_link),
# ce jeton reste valable plusieurs jours et n'est PAS à usage unique — il sert
# uniquement à restaurer la session (st.session_state) après une navigation
# complète du navigateur (redirection Stripe post-paiement, F5), qui fait
# perdre la session Streamlit en mémoire. Il est porté dans l'URL
# (?session_token=...) et ne doit jamais être envoyé par e-mail.
SESSION_TOKEN_TTL_SECONDS = 30 * 24 * 60 * 60

_pool: Optional[psycopg2.pool.ThreadedConnectionPool] = None
_pool_lock = threading.Lock()


def _get_pool() -> psycopg2.pool.ThreadedConnectionPool:
    global _pool
    if _pool is None:
        with _pool_lock:
            if _pool is None:
                dsn = get_secret("SUPABASE_DB_URL")
                
                if not dsn:
                    raise RuntimeError(
                        "SUPABASE_DB_URL non définie — impossible de se connecter à la base "
                        "d'authentification. Configurez ce secret côté Streamlit Cloud et Vercel."
                    )
                # Utilisation de ThreadedConnectionPool pour la sécurité multi-thread de Streamlit
                new_pool = psycopg2.pool.ThreadedConnectionPool(1, 10, dsn, sslmode="require")
                _init_schema(new_pool)
                _pool = new_pool
    return _pool


def _run(fn):
    """Exécute fn(conn, cur) avec une connexion prise dans le pool, avec un
    retry unique si la connexion s'avère fermée côté serveur.
    """
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
            with _pool_lock:
                _pool = None  # force la recréation d'un pool neuf au prochain tour
    raise last_exc


def _init_schema(pool: psycopg2.pool.AbstractConnectionPool) -> None:
    conn = pool.getconn()
    try:
        with conn, conn.cursor() as cur:
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS tva_users (
                    id TEXT PRIMARY KEY,
                    email TEXT UNIQUE NOT NULL,
                    created_at DOUBLE PRECISION NOT NULL,
                    is_cabinet BOOLEAN NOT NULL DEFAULT FALSE,
                    cabinet_parent_id TEXT,
                    home_country TEXT NOT NULL DEFAULT 'FR',
                    language TEXT NOT NULL DEFAULT 'fr',
                    display_currency TEXT NOT NULL DEFAULT 'DEFAULT'
                )
                """
            )
            # Ajout rétro-compatible pour les bases déjà existantes (le CREATE
            # TABLE IF NOT EXISTS ci-dessus ne modifie pas une table déjà créée
            # par une version antérieure du schéma).
            cur.execute(
                "ALTER TABLE tva_users ADD COLUMN IF NOT EXISTS home_country TEXT NOT NULL DEFAULT 'FR'"
            )
            cur.execute(
                "ALTER TABLE tva_users ADD COLUMN IF NOT EXISTS language TEXT NOT NULL DEFAULT 'fr'"
            )
            cur.execute(
                "ALTER TABLE tva_users ADD COLUMN IF NOT EXISTS display_currency TEXT NOT NULL DEFAULT 'DEFAULT'"
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
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS tva_amazon_credentials (
                    user_id TEXT PRIMARY KEY,
                    selling_partner_id TEXT NOT NULL,
                    refresh_token TEXT NOT NULL,
                    created_at DOUBLE PRECISION NOT NULL,
                    updated_at DOUBLE PRECISION NOT NULL
                )
                """
            )
            # Verifiers PKCE pour l'auth Supabase (Google/Microsoft/GitHub/
            # Amazon) : stockage serveur plutôt que cookie navigateur — le
            # cookie posé depuis l'iframe du composant extra_streamlit_components
            # ne s'est pas montré fiable pour survivre à la redirection OAuth
            # externe. Le nonce voyage dans l'URL de redirection (`redirect_to`),
            # pas le verifier lui-même.
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS tva_oauth_pkce (
                    nonce TEXT PRIMARY KEY,
                    provider TEXT NOT NULL,
                    verifier TEXT NOT NULL,
                    created_at DOUBLE PRECISION NOT NULL
                )
                """
            )
    finally:
        pool.putconn(conn)


@dataclass
class User:
    id: str
    email: str
    is_cabinet: bool = False
    cabinet_parent_id: Optional[str] = None
    home_country: str = "FR"
    language: str = "fr"
    display_currency: str = "DEFAULT"


def get_or_create_user(email: str) -> User:
    email = email.strip().lower()

    def _fn(conn, cur):
        cur.execute(
            "SELECT id, email, is_cabinet, cabinet_parent_id, home_country, language, display_currency FROM tva_users WHERE email=%s",
            (email,),
        )
        row = cur.fetchone()
        if row:
            return User(id=row[0], email=row[1], is_cabinet=bool(row[2]), cabinet_parent_id=row[3],
                        home_country=row[4] or "FR", language=row[5] or "fr", display_currency=row[6] or "DEFAULT")
        user_id = secrets.token_hex(12)
        cur.execute(
            "INSERT INTO tva_users (id, email, created_at) VALUES (%s, %s, %s)",
            (user_id, email, time.time()),
        )
        return User(id=user_id, email=email)

    return _run(_fn)


def set_home_country(user_id: str, country: str) -> None:
    """Met à jour le pays d'origine (établissement) du compte — réglage
    global, pas par SIREN (voir sidebar.py, section Entreprise & Paramètres).
    """
    country = (country or "FR").strip().upper()

    def _fn(conn, cur):
        cur.execute(
            "UPDATE tva_users SET home_country=%s WHERE id=%s",
            (country, user_id),
        )

    _run(_fn)


def set_language(user_id: str, language: str) -> None:
    """Met à jour la langue préférée du compte — réglage global, persisté
    pour être restaurée automatiquement à la prochaine connexion (voir
    i18n.py::language_selector() et app.py, synchro post-authentification)."""
    language = (language or "fr").strip().lower()

    def _fn(conn, cur):
        cur.execute(
            "UPDATE tva_users SET language=%s WHERE id=%s",
            (language, user_id),
        )

    _run(_fn)


def set_display_currency(user_id: str, currency: str) -> None:
    """Met à jour la devise d'affichage préférée du compte (voir
    sidebar.py, sélecteur sous le pays d'origine). "DEFAULT" signifie :
    utiliser la devise du pays d'origine choisi (comportement historique)."""
    currency = (currency or "DEFAULT").strip().upper()

    def _fn(conn, cur):
        cur.execute(
            "UPDATE tva_users SET display_currency=%s WHERE id=%s",
            (currency, user_id),
        )

    _run(_fn)


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
    api_key = get_secret("RESEND_API_KEY")
    from_email = get_secret("RESEND_FROM_EMAIL")
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
            "SELECT id, email, is_cabinet, cabinet_parent_id, home_country, language, display_currency FROM tva_users WHERE id=%s",
            (user_id,),
        )
        return cur.fetchone()

    urow = _run(_fetch_user)
    if not urow:
        return None
    return User(id=urow[0], email=urow[1], is_cabinet=bool(urow[2]), cabinet_parent_id=urow[3],
                home_country=urow[4] or "FR", language=urow[5] or "fr", display_currency=urow[6] or "DEFAULT")


def save_amazon_credentials(user_id: str, selling_partner_id: str, refresh_token: str) -> None:
    """Persiste les identifiants Amazon SP-API. Le refresh_token est chiffré
    (Fernet, security.py) avant écriture — conformité Amazon DPP."""
    _encrypted_refresh_token = encrypt_data(refresh_token)

    def _fn(conn, cur):
        now = time.time()
        cur.execute(
            """
            INSERT INTO tva_amazon_credentials (user_id, selling_partner_id, refresh_token, created_at, updated_at)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (user_id) DO UPDATE SET
                selling_partner_id = EXCLUDED.selling_partner_id,
                refresh_token = EXCLUDED.refresh_token,
                updated_at = EXCLUDED.updated_at
            """,
            (user_id, selling_partner_id, _encrypted_refresh_token, now, now),
        )

    _run(_fn)


def get_amazon_credentials(user_id: str) -> Optional[dict]:
    """Retourne les identifiants Amazon SP-API, avec le refresh_token
    déchiffré. `decrypt_data` retombe silencieusement sur la valeur brute si
    elle n'était pas chiffrée (transition depuis d'anciennes lignes stockées
    en clair avant ce correctif)."""
    def _fn(conn, cur):
        cur.execute(
            "SELECT selling_partner_id, refresh_token FROM tva_amazon_credentials WHERE user_id=%s",
            (user_id,),
        )
        row = cur.fetchone()
        if row:
            return {"selling_partner_id": row[0], "refresh_token": decrypt_data(row[1])}
        return None

    return _run(_fn)


def delete_amazon_credentials(user_id: str) -> None:
    def _fn(conn, cur):
        cur.execute("DELETE FROM tva_amazon_credentials WHERE user_id=%s", (user_id,))

    _run(_fn)


def save_pkce_verifier(nonce: str, provider: str, verifier: str) -> None:
    """Stocke côté serveur le code_verifier PKCE d'une tentative de connexion
    OAuth Supabase (Google/Microsoft/GitHub/Amazon), le temps de l'aller-retour
    vers le fournisseur. Purge au passage les entrées de plus de 15 minutes."""
    def _fn(conn, cur):
        now = time.time()
        cur.execute("DELETE FROM tva_oauth_pkce WHERE created_at < %s", (now - 15 * 60,))
        cur.execute(
            """
            INSERT INTO tva_oauth_pkce (nonce, provider, verifier, created_at)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (nonce) DO UPDATE SET
                provider = EXCLUDED.provider,
                verifier = EXCLUDED.verifier,
                created_at = EXCLUDED.created_at
            """,
            (nonce, provider, verifier, now),
        )

    _run(_fn)


def consume_pkce_verifier(nonce: str, provider: str) -> Optional[str]:
    """Récupère puis supprime immédiatement (usage unique) le code_verifier
    associé à ce nonce/provider. Retourne None si absent ou expiré (>15 min)."""
    def _fn(conn, cur):
        cur.execute(
            "SELECT verifier FROM tva_oauth_pkce WHERE nonce=%s AND provider=%s AND created_at >= %s",
            (nonce, provider, time.time() - 15 * 60),
        )
        row = cur.fetchone()
        cur.execute("DELETE FROM tva_oauth_pkce WHERE nonce=%s", (nonce,))
        return row[0] if row else None

    return _run(_fn)


def debug_pkce_diagnostic(nonce: str) -> dict:
    """🔧 DEBUG TEMPORAIRE — à retirer une fois le bug résolu.
    Inspecte l'état réel de tva_oauth_pkce : nombre total de lignes, la ligne
    exacte pour ce nonce (si présente, quel que soit son âge), et le DSN
    (hôte + nom de base uniquement, jamais les identifiants) réellement
    utilisé par ce process — pour vérifier qu'on lit/écrit bien la même base
    des deux côtés de la redirection OAuth."""
    def _fn(conn, cur):
        cur.execute("SELECT COUNT(*) FROM tva_oauth_pkce")
        _total = cur.fetchone()[0]
        cur.execute("SELECT nonce, provider, created_at FROM tva_oauth_pkce WHERE nonce=%s", (nonce,))
        _row = cur.fetchone()
        cur.execute("SELECT current_database(), inet_server_addr()")
        _db_info = cur.fetchone()
        return {
            "total_rows_table": _total,
            "row_for_this_nonce": _row,
            "database": _db_info[0] if _db_info else None,
            "server_addr": str(_db_info[1]) if _db_info else None,
        }

    try:
        return _run(_fn)
    except Exception as e:
        return {"error": str(e)}