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

import hashlib
import logging
import secrets
import threading
import time
from dataclasses import dataclass
from typing import Optional

import requests

from .config import get_secret
from .database import NonPoolingConnectionPool, get_shared_pool, reset_shared_pool, run_with_retry
from . import database as _database
from .security import encrypt_data, decrypt_data

logger = logging.getLogger(__name__)

MAGIC_LINK_TTL_SECONDS = 15 * 60

# Durée du jeton de session (7 jours avec renouvellement glissant) :
# `get_user_by_session_token()` retarde `created_at` à chaque usage réussi.
SESSION_TOKEN_TTL_SECONDS = 7 * 24 * 60 * 60

_pool_lock = threading.Lock()
_schema_ready = False


def _get_pool() -> "NonPoolingConnectionPool":
    """Retourne le pool PARTAGÉ (database.get_shared_pool) — voir database.py
    pour la justification du partage entre auth.py/billing.py/ecb_rates.py/
    vies_engine.py. `_init_schema()` reste propre à ce module et n'est jouée
    qu'une fois (indépendamment du fait que le pool lui-même soit déjà
    initialisé par un autre module)."""
    global _schema_ready
    dsn = get_secret("SUPABASE_DB_URL")
    if not dsn:
        raise RuntimeError(
            "SUPABASE_DB_URL non définie — impossible de se connecter à la base "
            "d'authentification. Configurez ce secret côté Streamlit Cloud et Vercel."
        )
    pool = get_shared_pool(dsn)
    if not _schema_ready:
        with _pool_lock:
            if not _schema_ready:
                _init_schema(pool)
                _schema_ready = True
    return pool


def close_idle_connections() -> None:
    """Appelé par app.py au tout début de CHAQUE run, avant l'auth : ferme
    la connexion partagée que CE thread avait ouverte lors du run précédent
    (voir docstring de `database.NonPoolingConnectionPool`). Délègue au pool
    partagé (database.close_idle_connections) — idempotent si billing.py/
    ecb_rates.py/vies_engine.py l'ont déjà fermée dans le même run."""
    _database.close_idle_connections()


def _run(fn):
    """Exécute fn(conn, cur) avec une connexion prise dans le pool, avec un
    retry unique si la connexion s'avère fermée côté serveur.
    """
    return run_with_retry(_get_pool, fn, on_retry=reset_shared_pool)


def _init_schema(pool: NonPoolingConnectionPool) -> None:
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
                    display_currency TEXT NOT NULL DEFAULT 'DEFAULT',
                    display_mode TEXT NOT NULL DEFAULT 'simple',
                    onboarding_seen BOOLEAN NOT NULL DEFAULT FALSE,
                    org_id TEXT,
                    role TEXT NOT NULL DEFAULT 'admin'
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
                "ALTER TABLE tva_users ADD COLUMN IF NOT EXISTS display_mode TEXT NOT NULL DEFAULT 'simple'"
            )
            cur.execute(
                "ALTER TABLE tva_users ADD COLUMN IF NOT EXISTS onboarding_seen BOOLEAN NOT NULL DEFAULT FALSE"
            )
            cur.execute(
                "ALTER TABLE tva_users ADD COLUMN IF NOT EXISTS org_id TEXT"
            )
            cur.execute(
                "ALTER TABLE tva_users ADD COLUMN IF NOT EXISTS role TEXT NOT NULL DEFAULT 'admin'"
            )
            # Verrouillage de compte après échecs multiples (audit sécurité 2026-09-22, ÉLEVÉ #8)
            cur.execute(
                "ALTER TABLE tva_users ADD COLUMN IF NOT EXISTS locked_until DOUBLE PRECISION"
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
            # consumed_at : permet une consommation idempotente. Un premier
            # SELECT+DELETE immédiat cassait toute requête en double (rerun
            # Streamlit, requête réseau dupliquée) car la 2e requête ne
            # retrouvait plus rien et affichait "session perdue" même quand
            # la 1re avait réussi. On marque désormais la ligne comme
            # consommée au lieu de la supprimer, et on retolère un nouveau
            # passage sur le même nonce dans une courte fenêtre de grâce.
            cur.execute(
                "ALTER TABLE tva_oauth_pkce ADD COLUMN IF NOT EXISTS consumed_at DOUBLE PRECISION"
            )
    finally:
        pool.putconn(conn)


@dataclass
class User:
    id: str
    email: str
    is_cabinet: bool = False
    cabinet_parent_id: str | None = None
    home_country: str = "FR"
    language: str = "fr"
    display_currency: str = "DEFAULT"
    display_mode: str = "simple"
    onboarding_seen: bool = False
    org_id: str = ""
    role: str = "admin"


_USER_SELECT_COLS = (
    "id, email, is_cabinet, cabinet_parent_id, home_country, language, display_currency, "
    "display_mode, onboarding_seen, org_id, role"
)


def _row_to_user(row) -> User:
    return User(
        id=row[0], email=row[1], is_cabinet=bool(row[2]), cabinet_parent_id=row[3],
        home_country=row[4] or "FR", language=row[5] or "fr", display_currency=row[6] or "DEFAULT",
        display_mode=row[7] or "simple", onboarding_seen=bool(row[8]),
        org_id=row[9] or "", role=row[10] or "admin",
    )


def resolve_org_id(email: str) -> str:
    """Détermine l'organisation d'un compte à partir de son e-mail — même
    principe que vies_engine.resolve_scope_id (réutilise la même liste de
    domaines de messagerie personnelle) : domaine professionnel → tous les
    collaborateurs partagent la même organisation (whitelist, rôles) ;
    domaine public (gmail.com, outlook.com...) → l'organisation est le
    compte lui-même (toujours admin de son propre compte, jamais de
    whitelist ni de verrouillage)."""
    from .vies_engine import PERSONAL_EMAIL_DOMAINS

    email = (email or "").strip().lower()
    if "@" not in email:
        _org_id = f"solo:{email or 'inconnu'}"
        logger.debug("[auth] resolve_org_id(...) -> (pas de domaine)")
        return _org_id
    domain = email.rsplit("@", 1)[1]
    if domain in PERSONAL_EMAIL_DOMAINS:
        _org_id = f"solo:{email}"
        logger.debug("[auth] resolve_org_id(...) -> (domaine public %s)", domain)
        return _org_id
    _org_id = f"domain:{domain}"
    logger.debug("[auth] resolve_org_id")
    return _org_id


def is_admin(user: "User") -> bool:
    return user.role == "admin"


def is_solo_org(org_id: str) -> bool:
    return org_id.startswith("solo:")


def validate_email_strict(email: str) -> tuple[bool, Optional[str]]:
    """Valide strictement le format de l'e-mail.
    
    Retourne (is_valid, error_message) où:
    - is_valid: True si l'e-mail est valide, False sinon
    - error_message: Message d'erreur si non valide, chaîne vide sinon
    
    Utilise email-validator pour une validation stricte selon RFC 5322.
    Si email-validator n'est pas disponible, utilise une validation regex
    basique comme fallback.
    """
    email = (email or "").strip().lower()
    
    try:
        from email_validator import validate_email, EmailNotValidError
        try:
            validate_email(email, check_deliverability=False)
            return True, ""
        except EmailNotValidError as e:
            return False, f"Format d'e-mail invalide ({e})"
    except ImportError:
        # Fallback: validation regex basique
        import re
        pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
        if not re.match(pattern, email):
            return False, "Format d'e-mail invalide"
        return True, ""


def can_signup(email: str) -> tuple[bool, Optional[str]]:
    """Contrôle SANS effet de bord (aucune écriture) si cet e-mail peut créer
    un compte MAINTENANT : à appeler par l'UI avant de déclencher l'envoi
    d'un e-mail de confirmation Supabase ou d'un lien magique, pour ne pas
    faire croire à une inscription en cours alors qu'elle sera refusée au
    moment de la première connexion réelle (voir get_or_create_user, qui
    reste la vérification faisant foi). Retourne (True, None) si autorisé,
    (False, message) sinon."""
    email = (email or "").strip().lower()
    
    # Validation stricte de l'e-mail (audit sécurité 2026-09-22, ÉLEVÉ #5)
    is_valid, error_msg = validate_email_strict(email)
    if not is_valid:
        return False, f"Format d'e-mail invalide : {error_msg}"
    
    org_id = resolve_org_id(email)

    def _fn(conn, cur):
        cur.execute("SELECT 1 FROM tva_users WHERE email=%s", (email,))
        if cur.fetchone():
            return True  # compte déjà existant : pas une inscription, Supabase gère le doublon
        cur.execute("SELECT locked_at FROM tva_orgs WHERE org_id=%s", (org_id,))
        row = cur.fetchone()
        if row is None or row[0] is None or is_solo_org(org_id):
            return True
        cur.execute(
            "SELECT 1 FROM tva_org_allowed_emails WHERE org_id=%s AND email=%s",
            (org_id, email),
        )
        return cur.fetchone() is not None

    allowed = _run(_fn)
    if allowed:
        return True, None
    return False, _SIGNUP_BLOCKED_MESSAGE


_SIGNUP_BLOCKED_MESSAGE = (
    "L'administrateur de votre entreprise a restreint l'accès à cette "
    "application. Il doit d'abord autoriser votre adresse e-mail depuis "
    "Sidebar → 🛡️ Administration avant que vous puissiez créer un compte."
)


def get_or_create_user(email: str) -> User:
    """Retourne le compte existant pour cet e-mail (toujours autorisé,
    quel que soit l'état de verrouillage de l'organisation — un compte déjà
    créé n'est jamais bloqué a posteriori), ou en crée un nouveau.

    Création d'un NOUVEAU compte :
    - organisation pas encore verrouillée (aucun abonnement payant souscrit
      pour ce domaine) : inscription libre, le nouveau compte devient admin
      (permet de tester le SIREN/l'app avant tout paiement) ;
    - organisation verrouillée : l'e-mail doit figurer dans
      `tva_org_allowed_emails` (ajouté par un admin, voir ui/admin.py), sinon
      lève `PermissionError` — la création de compte est refusée.
    - domaine public (gmail.com...) : jamais de verrouillage, toujours admin
      de son propre compte (organisation "solo").
    """
    email = email.strip().lower()
    
    # Validation stricte de l'e-mail (audit sécurité 2026-09-22, ÉLEVÉ #5)
    is_valid, error_msg = validate_email_strict(email)
    if not is_valid:
        raise ValueError(f"Format d'e-mail invalide : {error_msg}")
    
    org_id = resolve_org_id(email)
    logger.info("[auth] get_or_create_user")

    def _fn(conn, cur):
        cur.execute(
            f"SELECT {_USER_SELECT_COLS} FROM tva_users WHERE email=%s",
            (email,),
        )
        row = cur.fetchone()
        if row:
            _existing = _row_to_user(row)
            logger.info(
                "[auth] Compte existant : role=%r",
                _existing.role,
            )
            return _existing

        cur.execute("SELECT locked_at FROM tva_orgs WHERE org_id=%s", (org_id,))
        org_row = cur.fetchone()

        if org_row is None:
            logger.info("[auth] Nouvelle organisation (bootstrap)")
            cur.execute(
                "INSERT INTO tva_orgs (org_id, locked_at, created_at) VALUES (%s, NULL, %s)",
                (org_id, time.time()),
            )
            role = "admin"
        elif org_row[0] is None or is_solo_org(org_id):
            logger.info(
                "[auth] Organisation non verrouillée (ou solo) — inscription libre",
            )
            role = "admin"
        else:
            cur.execute(
                "SELECT role FROM tva_org_allowed_emails WHERE org_id=%s AND email=%s",
                (org_id, email),
            )
            allowed_row = cur.fetchone()
            if not allowed_row:
                logger.warning(
                    "[auth] Inscription REFUSÉE — organisation verrouillée, "
                    "e-mail absent de la whitelist.",
                )
                raise PermissionError(_SIGNUP_BLOCKED_MESSAGE)
            role = allowed_row[0] or "reader"
            logger.info(
                "[auth] Inscription autorisée (organisation verrouillée, rôle whitelisté=%r)",
                role,
            )

        user_id = secrets.token_hex(12)
        cur.execute(
            "INSERT INTO tva_users (id, email, created_at, org_id, role, display_mode) VALUES (%s, %s, %s, %s, %s, 'simple')",
            (user_id, email, time.time(), org_id, role),
        )
        conn.commit()
        return User(id=user_id, email=email, org_id=org_id, role=role)

    return _run(_fn)


def lock_org_for_user(user_id: str) -> None:
    """Verrouille l'organisation de cet utilisateur lors de son 1er
    abonnement payant : il reste (ou devient) admin, tous les AUTRES comptes
    du même domaine basculent en lecture seule — écrase tout rôle antérieur.
    Idempotent : ne fait rien si l'organisation est déjà verrouillée (un
    renouvellement ou changement de plan ne doit pas réinitialiser des rôles
    déjà réajustés manuellement par l'admin). Sans effet pour une
    organisation "solo" (domaine public) — un seul compte, déjà admin.

    CORRECTIF (2026-08-26) : ce check-then-act (SELECT locked_at puis
    INSERT/UPDATE) souffrait d'une race condition — deux webhooks Stripe
    concurrents pour deux comptes DIFFÉRENTS du même domaine professionnel
    (deux premiers paiements quasi simultanés dans la même organisation)
    pouvaient tous deux lire `locked_at IS NULL` avant que l'un des deux ne
    commit, aboutissant à ce que le dernier à committer "gagne" et écrase
    le rôle admin de l'autre (UPDATE ... SET role='reader' WHERE org_id=%s
    AND id<>%s). Verrou avisé Postgres transactionnel
    (`pg_advisory_xact_lock`, scope hashé sur org_id) ajouté ci-dessous :
    il sérialise les appels concurrents pour un même org_id — la deuxième
    transaction attend que la première commit (et libère le verrou) avant
    de faire son propre SELECT locked_at, qui la voit alors déjà verrouillée
    et no-op correctement. Verrou automatiquement libéré au commit/rollback
    de la transaction, pas besoin de UNLOCK explicite."""
    def _fn(conn, cur):
        cur.execute("SELECT org_id, email FROM tva_users WHERE id=%s", (user_id,))
        row = cur.fetchone()
        if not row:
            logger.warning("[auth] lock_org_for_user : utilisateur introuvable.")
            return
        org_id, email = row
        if is_solo_org(org_id):
            logger.info("[auth] lock_org_for_user : organisation solo, aucun verrouillage.")
            return

        # Verrou avisé transactionnel scopé à org_id : bloque ici si une
        # autre transaction est déjà en train de verrouiller CETTE org
        # (voir docstring ci-dessus) — se libère automatiquement au commit.
        cur.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (org_id,))

        cur.execute("SELECT locked_at FROM tva_orgs WHERE org_id=%s", (org_id,))
        org_row = cur.fetchone()
        if org_row and org_row[0] is not None:
            logger.info("[auth] lock_org_for_user : organisation déjà verrouillée, no-op.")
            return  # déjà verrouillée : ne pas écraser les rôles ajustés depuis

        logger.info("[auth] Verrouillage de l'organisation")
        now = time.time()
        cur.execute(
            """
            INSERT INTO tva_orgs (org_id, locked_at, created_at) VALUES (%s, %s, %s)
            ON CONFLICT (org_id) DO UPDATE SET locked_at = EXCLUDED.locked_at
            """,
            (org_id, now, now),
        )
        cur.execute("UPDATE tva_users SET role='admin' WHERE id=%s", (user_id,))
        cur.execute("UPDATE tva_users SET role='reader' WHERE org_id=%s AND id<>%s", (org_id, user_id))
        cur.execute(
            """
            INSERT INTO tva_org_allowed_emails (org_id, email, role, added_by, created_at)
            VALUES (%s, %s, 'admin', %s, %s)
            ON CONFLICT (org_id, email) DO UPDATE SET role='admin'
            """,
            (org_id, email, user_id, now),
        )
        conn.commit()

    _run(_fn)


def is_org_locked(org_id: str) -> bool:
    def _fn(conn, cur):
        cur.execute("SELECT locked_at FROM tva_orgs WHERE org_id=%s", (org_id,))
        row = cur.fetchone()
        return bool(row and row[0] is not None)

    return _run(_fn)


def list_org_members(org_id: str) -> list["User"]:
    """Liste les comptes de l'organisation — pour le module admin."""
    def _fn(conn, cur):
        cur.execute(
            f"SELECT {_USER_SELECT_COLS} FROM tva_users WHERE org_id=%s ORDER BY created_at",
            (org_id,),
        )
        return cur.fetchall()

    rows = _run(_fn)
    return [_row_to_user(r) for r in rows]


def list_allowed_emails(org_id: str) -> list[dict]:
    def _fn(conn, cur):
        cur.execute(
            "SELECT email, role, added_by, created_at FROM tva_org_allowed_emails "
            "WHERE org_id=%s ORDER BY created_at",
            (org_id,),
        )
        return cur.fetchall()

    rows = _run(_fn)
    return [{"email": r[0], "role": r[1], "added_by": r[2], "created_at": r[3]} for r in rows]


def add_allowed_email(org_id: str, email: str, role: str, added_by: str) -> None:
    """Ajoute (ou met à jour) une adresse autorisée à créer un compte sur
    cette organisation, avec le rôle choisi par l'admin (case à cocher
    admin/lecteur dans ui/admin.py).

    RÔLES (2026-08-26) : `added_by` sert déjà à identifier qui déclenche
    l'ajout (traçabilité) — on en profite pour vérifier ici que ce compte
    est bien admin, même pattern que `set_user_role()`/`delete_account()`.
    Repéré lors de l'audit systématique "tous les blocages UI ont-ils un
    verrouillage serveur ?" : seul le gating UI (ui/admin.py, ouvert
    uniquement si `is_admin(current_user)`) protégeait cette fonction
    jusqu'ici.
    """
    _acting_user = get_user_by_id(added_by)
    if not _acting_user or not is_admin(_acting_user):
        raise PermissionError(
            "Seul un administrateur de l'organisation peut ajouter une adresse autorisée."
        )

    email = email.strip().lower()
    role = "admin" if role == "admin" else "reader"

    def _fn(conn, cur):
        cur.execute(
            """
            INSERT INTO tva_org_allowed_emails (org_id, email, role, added_by, created_at)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (org_id, email) DO UPDATE SET role=EXCLUDED.role, added_by=EXCLUDED.added_by
            """,
            (org_id, email, role, added_by, time.time()),
        )
        conn.commit()

    _run(_fn)


# ---------------------------------------------------------------------------
# Protection brute-force (rate-limiting)
# ---------------------------------------------------------------------------

# Configuration du rate-limiting
_FAILED_LOGIN_MAX_ATTEMPTS = 5  # Max 5 tentatives
_FAILED_LOGIN_WINDOW_SECONDS = 900  # Fenêtre de 15 minutes


def _get_client_ip_hash() -> str:
    """Génère un hash de l'IP du client pour le rate-limiting.
    
    Note: Dans un environnement Streamlit, l'IP réelle n'est pas directement accessible.
    On utilise une combinaison de l'IP (si disponible via headers) et d'un identifiant
    de session pour limiter les abus.
    """
    import streamlit as st
    import hashlib
    
    # Essayer de récupérer l'IP depuis les headers (si derrière un reverse proxy)
    ip = st.context.headers.get("X-Forwarded-For", st.context.headers.get("X-Real-IP", "unknown"))
    
    # Si l'IP n'est pas disponible, utiliser un identifiant de session
    if ip == "unknown":
        ip = f"session:{st.session_state.get('session_id', 'anonymous')}"
    
    # Hasher l'IP pour éviter de stocker l'IP en clair
    return hashlib.sha256(ip.encode()).hexdigest()


def check_rate_limit(ip_hash: str) -> tuple[bool, str]:
    """Vérifie si l'IP a dépassé la limite de tentatives de connexion.
    
    Retourne (allowed, message) où:
    - allowed: True si l'IP est autorisée à continuer, False sinon
    - message: Message d'erreur si non autorisé, chaîne vide sinon
    """
    def _fn(conn, cur):
        # Compter les tentatives dans la fenêtre de temps
        cur.execute("""
            SELECT COUNT(*) FROM tva_failed_logins
            WHERE ip_hash=%s AND attempt_at > %s
        """, (ip_hash, time.time() - _FAILED_LOGIN_WINDOW_SECONDS))
        count = cur.fetchone()[0]
        
        if count >= _FAILED_LOGIN_MAX_ATTEMPTS:
            return False, f"Trop de tentatives de connexion. Réessayez dans {_FAILED_LOGIN_WINDOW_SECONDS // 60} minutes."
        return True, ""
    
    return _run(_fn)


def record_failed_login(ip_hash: str) -> None:
    """Enregistre une tentative de connexion échouée."""
    def _fn(conn, cur):
        cur.execute(
            "INSERT INTO tva_failed_logins (ip_hash, attempt_at) VALUES (%s, %s)",
            (ip_hash, time.time())
        )
        conn.commit()
    
    _run(_fn)


def clear_failed_logins(ip_hash: str) -> None:
    """Nettoie les tentatives échouées pour une IP après une connexion réussie."""
    def _fn(conn, cur):
        cur.execute(
            "DELETE FROM tva_failed_logins WHERE ip_hash=%s",
            (ip_hash,)
        )
        conn.commit()
    
    _run(_fn)


def cleanup_old_failed_logins() -> None:
    """Nettoie les anciennes entrées de tentatives échouées (plus de 24h).
    
    Cette fonction devrait être appelée périodiquement (ex: via un job cron)
    pour éviter que la table ne grossisse indéfiniment.
    """
    def _fn(conn, cur):
        cur.execute(
            "DELETE FROM tva_failed_logins WHERE attempt_at < %s",
            (time.time() - 86400,)  # 24 heures
        )
        conn.commit()
    
    _run(_fn)


# ---------------------------------------------------------------------------
# Verrouillage de compte après échecs multiples
# ---------------------------------------------------------------------------

_ACCOUNT_LOCK_DURATION_SECONDS = 3600  # 1 heure de verrouillage
_ACCOUNT_LOCK_THRESHOLD = 10  # Verrouiller après 10 échecs


def check_account_locked(email: str) -> tuple[bool, Optional[float]]:
    """Vérifie si un compte est verrouillé.
    
    Retourne (is_locked, locked_until) où:
    - is_locked: True si le compte est verrouillé, False sinon
    - locked_until: Timestamp du déverrouillage si verrouillé, None sinon
    """
    def _fn(conn, cur):
        cur.execute(
            "SELECT locked_until FROM tva_users WHERE email=%s",
            (email,)
        )
        row = cur.fetchone()
        if not row or row[0] is None:
            return False, None
        
        locked_until = row[0]
        if time.time() < locked_until:
            return True, locked_until
        
        # Le verrouillage a expiré, on le nettoie
        cur.execute(
            "UPDATE tva_users SET locked_until=NULL WHERE email=%s",
            (email,)
        )
        conn.commit()
        return False, None
    
    return _run(_fn)


def lock_account_temporarily(email: str, duration_seconds: int = _ACCOUNT_LOCK_DURATION_SECONDS) -> None:
    """Verrouille temporairement un compte après échecs multiples.
    
    Args:
        email: Adresse e-mail du compte à verrouiller
        duration_seconds: Durée du verrouillage en secondes (défaut: 1 heure)
    """
    def _fn(conn, cur):
        cur.execute(
            "UPDATE tva_users SET locked_until=%s WHERE email=%s",
            (time.time() + duration_seconds, email)
        )
        conn.commit()
    
    _run(_fn)


def increment_failed_login_count(email: str) -> int:
    """Incrémente le compteur d'échecs de connexion pour un e-mail.
    
    Retourne le nombre total d'échecs après incrémentation.
    Si le seuil est atteint, verrouille le compte temporairement.
    
    Cette fonction utilise la table tva_failed_logins existante mais avec
    un scope par e-mail plutôt que par IP pour le verrouillage de compte.
    """
    email_hash = hashlib.sha256(email.encode()).hexdigest()
    
    def _fn(conn, cur):
        # Compter les échecs pour cet e-mail dans la dernière heure
        cur.execute(
            "SELECT COUNT(*) FROM tva_failed_logins WHERE ip_hash=%s AND attempt_at > %s",
            (email_hash, time.time() - 3600)
        )
        count = cur.fetchone()[0]
        
        # Enregistrer cet échec
        cur.execute(
            "INSERT INTO tva_failed_logins (ip_hash, attempt_at) VALUES (%s, %s)",
            (email_hash, time.time())
        )
        conn.commit()
        
        # Vérifier si on doit verrouiller le compte
        if count >= _ACCOUNT_LOCK_THRESHOLD:
            lock_account_temporarily(email)
            logger.warning(f"[auth] Compte verrouillé pour {email} après {count + 1} échecs")
        
        return count + 1
    
    return _run(_fn)


def remove_allowed_email(org_id: str, email: str, acting_user_id: str | None = None) -> None:
    """Retire une adresse de la liste des e-mails autorisés à créer un
    compte pour cette organisation.

    RÔLES (2026-08-26) : `acting_user_id` optionnel (défaut None,
    rétrocompatible) — même pattern que `set_user_role()`. Repéré lors de
    l'audit systématique "tous les blocages UI ont-ils un verrouillage
    serveur ?" : seul le gating UI protégeait cette fonction jusqu'ici.
    """
    if acting_user_id is not None:
        _acting_user = get_user_by_id(acting_user_id)
        if not _acting_user or not is_admin(_acting_user):
            raise PermissionError(
                "Seul un administrateur de l'organisation peut retirer une adresse autorisée."
            )

    email = email.strip().lower()

    def _fn(conn, cur):
        cur.execute("DELETE FROM tva_org_allowed_emails WHERE org_id=%s AND email=%s", (org_id, email))
        conn.commit()

    _run(_fn)


def set_user_role(user_id: str, role: str, acting_user_id: str | None = None) -> None:
    """Change le rôle d'un compte existant.

    RÔLES (2026-08-26) : `acting_user_id` identifie qui déclenche ce
    changement — même pattern que `delete_account()`. Seul un compte
    `role="admin"` peut promouvoir/rétrograder un autre membre ; contrôle
    déjà fait côté UI (ui/admin.py::render_admin_dialog n'est ouvert que si
    `is_admin(current_user)`) mais dupliqué ici, serveur, car c'était
    jusqu'ici la seule fonction de mutation de rôle/organisation sans
    second verrou serveur (contrairement à `delete_account`,
    `billing._require_write_access`, `vies_engine.set_manual_override`) —
    un oubli identifié lors du checkup du 2026-08-26, corrigé ici.
    `acting_user_id=None` : rétrocompatibilité (scripts internes/tests
    appelant directement cette fonction) — aucune vérification dans ce cas,
    comportement inchangé.
    """
    if acting_user_id is not None:
        _acting_user = get_user_by_id(acting_user_id)
        if not _acting_user or not is_admin(_acting_user):
            raise PermissionError(
                "Seul un administrateur de l'organisation peut modifier le rôle d'un compte."
            )

    role = "admin" if role == "admin" else "reader"

    def _fn(conn, cur):
        cur.execute("UPDATE tva_users SET role=%s WHERE id=%s", (role, user_id))
        conn.commit()

    _run(_fn)


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


def set_display_mode(user_id: str, mode: str) -> None:
    """Met à jour le mode d'affichage préféré (Simple/Détaillé — voir
    tva_intracom/ui/display_mode.py), pour être restauré automatiquement à
    la prochaine connexion, même logique de synchro que set_language()."""
    mode = "detaille" if mode == "detaille" else "simple"

    def _fn(conn, cur):
        cur.execute(
            "UPDATE tva_users SET display_mode=%s WHERE id=%s",
            (mode, user_id),
        )

    _run(_fn)


def set_onboarding_seen(user_id: str, seen: bool) -> None:
    """Marque (ou remet à zéro) le flag d'onboarding pour ce compte — voir
    tva_intracom/ui/onboarding.py. `seen=False` sert au bouton "Relancer la
    visite guidée" (Compte & Confidentialité, sidebar.py)."""

    def _fn(conn, cur):
        cur.execute(
            "UPDATE tva_users SET onboarding_seen=%s WHERE id=%s",
            (bool(seen), user_id),
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
    Inclut une protection brute-force (DPP Amazon) avec rate-limiting."""
    import hashlib
    ip_hash = hashlib.sha256(ip_address.encode()).hexdigest()

    # Vérifier le rate-limiting
    allowed, message = check_rate_limit(ip_hash)
    if not allowed:
        logger.warning("[auth] Rate-limit activé pour IP hash %s", ip_hash)
        raise PermissionError(message)

    def _fn(conn, cur):
        # Vérifier le token
        cur.execute(
            "SELECT email, created_at, consumed FROM tva_magic_links WHERE token=%s",
            (token,),
        )
        row = cur.fetchone()

        if not row:
            # Enregistrer l'échec (IP + email pour le verrouillage de compte)
            record_failed_login(ip_hash)
            return None

        email, created_at, consumed = row
        if consumed or (time.time() - created_at) > MAGIC_LINK_TTL_SECONDS:
            # Enregistrer l'échec et incrémenter le compteur pour l'e-mail
            record_failed_login(ip_hash)
            increment_failed_login_count(email)
            return None

        # Vérifier si le compte est verrouillé
        is_locked, locked_until = check_account_locked(email)
        if is_locked:
            logger.warning("[auth] Compte verrouillé pour %s jusqu'à %s", email, locked_until)
            return None

        # Succès : on nettoie les anciens échecs pour cet IP et on marque consommé
        clear_failed_logins(ip_hash)
        cur.execute("UPDATE tva_magic_links SET consumed=TRUE WHERE token=%s", (token,))
        conn.commit()
        return email

    res = _run(_fn)
    if not res:
        return None
    return get_or_create_user(res)


def get_user_by_id(user_id: str) -> Optional[User]:
    """Retourne l'utilisateur associé à un ID, sans passer par un jeton."""
    def _fetch_user(conn, cur):
        cur.execute(
            f"SELECT {_USER_SELECT_COLS} FROM tva_users WHERE id=%s",
            (user_id,),
        )
        return cur.fetchone()

    urow = _run(_fetch_user)
    if not urow:
        return None
    return _row_to_user(urow)


def delete_account(user_id: str, acting_user_id: str | None = None) -> None:
    """Supprime définitivement un compte utilisateur et les données associées
    (RGPD). Supprime les abonnements Stripe, les identifiants Amazon chiffrés,
    les SIREN.

    RÔLES (2026-08-25) : `acting_user_id` identifie qui déclenche CETTE
    suppression — qu'il s'agisse d'un lecteur supprimant son propre compte
    (sidebar.py, "Suppression du compte") ou d'un admin supprimant un AUTRE
    membre de l'organisation (ui/admin.py). Dans les deux cas, seul un
    compte `role="admin"` peut effectivement supprimer un compte — un
    lecteur ne peut pas se retirer lui-même de l'organisation, contrôle
    déjà fait côté UI (sidebar.py masque le bloc) mais dupliqué ici,
    serveur, même pattern que `billing._require_write_access`.
    `acting_user_id=None` : rétrocompatibilité (scripts internes/tests
    appelant directement cette fonction sans notion d'utilisateur agissant)
    — aucune vérification de rôle dans ce cas, comportement inchangé.

    Cas particulier de l'historique VIES (si scope privé "user:<email>") :
    conformément à l'art. 17.3.b du RGPD (obligation légale prévalant sur le
    droit à l'effacement), `vies_check_history` n'est PAS supprimé — cette
    piste d'audit justifie d'éventuelles exonérations B2B en cas de contrôle
    fiscal et est déjà retenue 365 jours en fonctionnement normal (voir
    `vies_engine._db_delete_expired_scope`). Elle est seulement pseudonymisée
    (le scope_id, qui contient l'e-mail en clair, est remplacé par un
    identifiant haché non réversible) puis purgée automatiquement à
    l'échéance des 365 jours par la purge périodique habituelle. Le cache
    privé (`vies_scope_cache`) et les overrides manuels, eux, sont bien
    supprimés immédiatement — voir `vies_engine.delete_all_scope_data`.
    """
    from .billing import delete_user_billing_data
    from .vies_engine import delete_all_scope_data, resolve_scope_id

    if acting_user_id is not None:
        _acting_user = get_user_by_id(acting_user_id)
        if not _acting_user or not is_admin(_acting_user):
            raise PermissionError(
                "Seul un administrateur de l'organisation peut supprimer un compte."
            )

    user = get_user_by_id(user_id)
    if not user:
        return

    # 1. Facturation & Stripe
    delete_user_billing_data(user_id)

    # 2. VIES (seulement si scope privé user:email) — pseudonymisation de
    # l'historique + suppression du cache/overrides, voir docstring ci-dessus.
    scope_id = resolve_scope_id(user.email)
    if scope_id.startswith("user:"):
        delete_all_scope_data(scope_id)

    # 3. Authentification & Credentials
    def _fn(conn, cur):
        cur.execute("DELETE FROM tva_amazon_credentials WHERE user_id=%s", (user_id,))
        cur.execute("DELETE FROM tva_session_tokens WHERE user_id=%s", (user_id,))
        cur.execute("DELETE FROM tva_magic_links WHERE email=%s", (user.email,))
        # Pas de purge de tva_oauth_pkce ici : cette table n'a pas de user_id
        # (nonce/provider/verifier uniquement) et une suppression sur un
        # critère temporel générique supprimerait les flux PKCE d'AUTRES
        # utilisateurs en cours de connexion OAuth. Le nettoyage périodique
        # (fenêtre de 15 min) est déjà assuré indépendamment par
        # save_pkce_verifier() à chaque nouvelle tentative de connexion.

        # Appel de la fonction SQL SECURITY DEFINER pour supprimer de auth.users
        # car le SDK client ne peut pas le faire lui-même.
        cur.execute("SELECT delete_user_auth_by_email(%s)", (user.email,))

        cur.execute("DELETE FROM tva_users WHERE id=%s", (user_id,))
        conn.commit()

    _run(_fn)


def export_all_user_data(user_id: str) -> dict:
    """Récupère l'intégralité des données d'un utilisateur pour export (RGPD)."""
    from .billing import export_user_billing_data
    from .vies_engine import export_scope_data, resolve_scope_id

    user = get_user_by_id(user_id)
    if not user:
        return {}

    billing_data = export_user_billing_data(user_id)

    scope_id = resolve_scope_id(user.email)
    vies_data = export_scope_data(scope_id)

    def _fetch_auth_data(conn, cur):
        cur.execute("SELECT selling_partner_id, created_at, updated_at FROM tva_amazon_credentials WHERE user_id=%s", (user_id,))
        amz = cur.fetchone()
        return {
            "amazon_credentials": {
                "selling_partner_id": amz[0],
                "created_at": amz[1],
                "updated_at": amz[2]
            } if amz else None
        }

    auth_data = _run(_fetch_auth_data)

    return {
        "user_profile": {
            "id": user.id,
            "email": user.email,
            "home_country": user.home_country,
            "language": user.language,
            "display_currency": user.display_currency,
        },
        "billing": billing_data,
        "vies": vies_data,
        "auth": auth_data,
        "exported_at": time.time()
    }


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
    expiré), sans le consommer — il reste utilisable jusqu'à expiration.

    Renouvellement glissant : chaque restauration réussie recule `created_at` à
    maintenant. Un jeton non réutilisé pendant 7 jours pleins expire."""
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

    def _touch(conn, cur):
        cur.execute(
            "UPDATE tva_session_tokens SET created_at=%s WHERE token=%s",
            (time.time(), token),
        )
        conn.commit()

    try:
        _run(_touch)
    except Exception:
        # Le renouvellement est un confort (éviter une reconnexion
        # hebdomadaire) — un échec ne doit jamais bloquer la restauration
        # de session elle-même, dont le résultat est déjà déterminé ci-dessus.
        logger.warning("Échec du renouvellement glissant du jeton de session (non bloquant).", exc_info=True)

    return get_user_by_id(user_id)


def delete_session_token(token: str) -> None:
    """Supprime un jeton de session (déconnexion)."""
    def _fn(conn, cur):
        cur.execute("DELETE FROM tva_session_tokens WHERE token=%s", (token,))
        conn.commit()

    _run(_fn)


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
    """Retourne les identifiants Amazon SP-API, avec le refresh_token déchiffré.
    
    Toute valeur non conforme au format Fernet lève une erreur (security.decrypt_data).
    """
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
        conn.commit()

    _run(_fn)


def consume_pkce_verifier(nonce: str, provider: str) -> Optional[str]:
    """Récupère le code_verifier associé à ce nonce/provider. Idempotent :
    au lieu de supprimer la ligne immédiatement, on la marque `consumed_at`
    et on continue de renvoyer le même verifier pendant une courte fenêtre
    de grâce (30s) — tolère une requête dupliquée (rerun Streamlit, retry
    réseau) qui arriverait juste après une consommation réussie.

    Si le lookup strict (nonce+provider+fraîcheur) échoue, un second lookup
    diagnostique (nonce seul, sans filtre) permet de savoir *pourquoi* :
    absent, provider différent, ou expiré — l'info est incluse dans
    l'exception pour affichage dans le message d'erreur."""
    GRACE_SECONDS = 30

    def _fn(conn, cur):
        now = time.time()
        cur.execute(
            "SELECT verifier, consumed_at FROM tva_oauth_pkce WHERE nonce=%s AND provider=%s AND created_at >= %s",
            (nonce, provider, now - 15 * 60),
        )
        row = cur.fetchone()
        if row:
            verifier, consumed_at = row
            if consumed_at is None or (now - consumed_at) <= GRACE_SECONDS:
                cur.execute(
                    "UPDATE tva_oauth_pkce SET consumed_at=%s WHERE nonce=%s",
                    (now, nonce),
                )
                conn.commit()
                return verifier
            return None  # consommé depuis trop longtemps : vraiment expiré

        # Rien trouvé avec le filtre strict : diagnostic pour comprendre pourquoi.
        cur.execute(
            "SELECT provider, created_at, consumed_at FROM tva_oauth_pkce WHERE nonce=%s",
            (nonce,),
        )
        diag_row = cur.fetchone()
        if diag_row is None:
            raise LookupError(f"nonce introuvable en base (provider attendu={provider})")
        diag_provider, diag_created_at, diag_consumed_at = diag_row
        if diag_provider != provider:
            raise LookupError(f"nonce trouvé mais provider différent en base ({diag_provider!r} != {provider!r})")
        age = now - diag_created_at
        raise LookupError(f"nonce trouvé mais expiré (créé il y a {age:.0f}s, consumed_at={diag_consumed_at})")

    try:
        return _run(_fn)
    except LookupError:
        raise
    except Exception:
        # Avant : toute erreur DB (pas seulement "nonce introuvable") était
        # traduite silencieusement en "pas de verifier trouvé" → un vrai
        # problème de connexion pendant un callback OAuth devenait un échec
        # de login silencieux, sans trace. On logue désormais (sans changer
        # le comportement de retour, pour ne pas casser le flux OAuth).
        logger.warning(
            "consume_pkce_verifier: erreur DB inattendue (nonce=%s, provider=%s)",
            nonce, provider, exc_info=True,
        )
        return None


def consume_latest_pkce_verifier_by_provider(provider: str, max_age_seconds: int = 15 * 60) -> Optional[str]:
    """Récupère le code_verifier PKCE le plus récent pour ce provider, SANS filtrer
    sur le nonce.

    Utilisé pour le flux "mot de passe oublié" : Supabase tronque la query string
    du `redirect_to` (perte de `sb_provider`/`sb_nonce`) dès que l'URL n'est
    autorisée que via une entrée wildcard de la liste blanche (et non une
    correspondance exacte). Le lien de retour n'expose donc plus que `?code=...`,
    sans nonce exploitable — on retombe sur "la dernière demande de recovery
    en attente" (hypothèse raisonnable : un seul utilisateur redemande rarement
    plusieurs reset en parallèle dans la fenêtre de 15 minutes).

    Même logique idempotente que `consume_pkce_verifier` (fenêtre de grâce de
    30s pour tolérer un rerun/retry Streamlit).

    Fonction de compatibilité historique (compat_shim) : l'appelant direct
    recommande d'utiliser la version cascade `consume_latest_pkce_verifiers_by_provider`.
    """
    GRACE_SECONDS = 30

    def _fn(conn, cur):
        now = time.time()
        cur.execute(
            """
            SELECT nonce, verifier, consumed_at FROM tva_oauth_pkce
            WHERE provider=%s AND created_at >= %s
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (provider, now - max_age_seconds),
        )
        row = cur.fetchone()
        if not row:
            return None
        nonce, verifier, consumed_at = row
        if consumed_at is not None and (now - consumed_at) > GRACE_SECONDS:
            return None  # consommé depuis trop longtemps : vraiment expiré
        cur.execute(
            "UPDATE tva_oauth_pkce SET consumed_at=%s WHERE nonce=%s",
            (now, nonce),
        )
        conn.commit()
        return verifier

    try:
        return _run(_fn)
    except Exception:
        # Voir commentaire équivalent dans consume_pkce_verifier() : on logue
        # désormais les erreurs DB inattendues (flux "mot de passe oublié")
        # au lieu de les avaler silencieusement.
        logger.warning(
            "consume_latest_pkce_verifier_by_provider: erreur DB inattendue (provider=%s)",
            provider, exc_info=True,
        )
        return None


def consume_latest_pkce_verifiers_by_provider(
        provider: str, max_age_seconds: int = 15 * 60, limit: int = 5,
) -> list[str]:
    """Variante "cascade" de `consume_latest_pkce_verifier_by_provider` :
    renvoie jusqu'à `limit` code_verifiers candidats (du plus récent au plus
    ancien), au lieu d'un seul.

    Résolution des demandes concurrentes : permet d'essayer l'échange PKCE
    avec plusieurs verifiers candidats si plusieurs resets simultanés ont lieu.
    """
    GRACE_SECONDS = 30

    def _fn(conn, cur):
        now = time.time()
        cur.execute(
            """
            SELECT nonce, verifier, consumed_at FROM tva_oauth_pkce
            WHERE provider=%s AND created_at >= %s
              AND (consumed_at IS NULL OR %s - consumed_at <= %s)
            ORDER BY created_at DESC
            LIMIT %s
            """,
            (provider, now - max_age_seconds, now, GRACE_SECONDS, limit),
        )
        rows = cur.fetchall()
        if not rows:
            return []
        nonces = [r[0] for r in rows]
        verifiers = [r[1] for r in rows]
        cur.execute(
            "UPDATE tva_oauth_pkce SET consumed_at=%s WHERE nonce = ANY(%s)",
            (now, nonces),
        )
        conn.commit()
        return verifiers

    try:
        return _run(_fn) or []
    except Exception:
        logger.warning(
            "consume_latest_pkce_verifiers_by_provider: erreur DB inattendue (provider=%s)",
            provider, exc_info=True,
        )
        return []


def purge_old_pkce_entries(older_than_seconds: int = 15 * 60) -> None:
    """Nettoyage périodique des vieilles entrées PKCE (consommées ou non)."""
    def _fn(conn, cur):
        cur.execute(
            "DELETE FROM tva_oauth_pkce WHERE created_at < %s",
            (time.time() - older_than_seconds,),
        )
        conn.commit()

    _run(_fn)