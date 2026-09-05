"""Facturation & quotas Stripe — tva_intracom.

Backend Postgres (Supabase).

Forfaits disponibles :
    - PAYG      : achat unique d'une période fiscale (crédit d'export).
    - business  : abonnement "Pro" — accès illimité, 1 seul SIREN.
    - cabinet   : abonnement "Cabinet" — accès illimité, paliers tarifaires
                  Stripe (tiered pricing) basés sur la quantité choisie au
                  Checkout, qui correspond au nombre de SIREN gérés.

Les abonnements Pro et Cabinet existent chacun en mensuel et en annuel : ce
sont deux Price Stripe distincts pour un même Product (le product_id Stripe
n'intervient pas côté code, seul le price_id compte pour le Checkout).
"""
from __future__ import annotations

import logging
import os
import secrets
import time
from dataclasses import dataclass
from typing import Optional

import threading

# IMPORTANT : streamlit n'est PAS installé dans l'environnement serverless
# Vercel qui charge ce module isolément pour le webhook Stripe (voir
# vercel_webhook/api/stripe_webhook.py) — volontairement, pour rester léger.
# Sans ce garde-fou, `import streamlit as st` plantait tout le webhook avec
# ModuleNotFoundError avant même d'exécuter la moindre ligne de logique
# métier (bug constaté en prod le 02/08/2026, paiement Stripe validé côté
# Stripe mais webhook en 500 côté Vercel). Même pattern déjà en place dans
# tva_intracom/config.py pour get_secret().
#
# Seul usage de `st` dans ce module : le décorateur `@st.cache_data` sur 3
# fonctions (get_subscription_status, list_available_promotions,
# get_pricing_grid) — utile uniquement côté app Streamlit (mise en cache
# entre reruns UI). Côté webhook Vercel (process court, une invocation par
# requête), ce cache n'a de toute façon aucune utilité : le shim ci-dessous
# fournit un décorateur no-op transparent (fonction appelée normalement,
# sans mise en cache) quand streamlit est absent.
try:
    import streamlit as st
except ImportError:
    class _NoOpCacheData:
        """Substitut minimal de st.cache_data : exécute la fonction décorée
        normalement (aucun cache), sans changer sa signature d'appel.
        Expose aussi `.clear()` (no-op) pour matcher l'API réelle de
        st.cache_data — nécessaire car list_registered_sirens.clear() est
        appelé après register_siren/request_siren_removal/
        cancel_siren_removal, y compris dans ce contexte sans Streamlit."""
        def __call__(self, *dargs, **dkwargs):
            def _decorator(fn):
                def _wrapper(*args, **kwargs):
                    return fn(*args, **kwargs)
                _wrapper.clear = lambda: None
                return _wrapper
            # Supporte @st.cache_data et @st.cache_data(ttl=..., show_spinner=...)
            if dargs and callable(dargs[0]) and not dkwargs:
                return _decorator(dargs[0])
            return _decorator

    class _StreamlitShim:
        cache_data = _NoOpCacheData()

    st = _StreamlitShim()

logger = logging.getLogger(__name__)

try:
    import stripe  # type: ignore
except ImportError:
    stripe = None

from .database import NonPoolingConnectionPool, get_shared_pool, reset_shared_pool, run_with_retry
from . import database as _database
from .security import encrypt_data as _enc, decrypt_data as _dec
from .config import get_secret


def _env(key: str, default: str = "") -> str:
    """Lit une variable de configuration : priorité à st.secrets (Streamlit
    Cloud), repli sur os.environ (Vercel, ou variable d'env classique)."""
    return get_secret(key, default)


PRICE_PAYG_EXPORT = os.environ.get("STRIPE_PRICE_PAYG_EXPORT", "")

# Abonnements : 1 price_id par (plan, intervalle). Résolus dynamiquement via
# _env() au moment de l'appel (voir create_subscription_checkout_session),
# et non figés ici à l'import du module.

# Quota de SIREN distincts pour le plan "business" (Pro). Le quota du plan
# "cabinet" est dynamique : il vaut la quantité Stripe achetée
# (tva_subscriptions.siren_quantity).
_BUSINESS_SIREN_QUOTA = 1
_CABINET_MIN_QUANTITY = 3

_pool_lock = threading.Lock()
_schema_ready = False


def _safe_get(obj, key, default=None):
    """Accès sécurisé à une clé, compatible dict classique ET objets Stripe
    (stripe.stripe_object.StripeObject des versions récentes du SDK, qui ne
    supportent pas .get() comme un dict — provoque AttributeError: get)."""
    try:
        return obj[key]
    except (KeyError, TypeError, IndexError):
        return default


def _stripe_configured() -> bool:
    key = _env("STRIPE_SECRET_KEY")
    if not key or stripe is None:
        return False
    stripe.api_key = key
    return True


def _get_pool() -> "NonPoolingConnectionPool":
    """Retourne le pool PARTAGÉ (database.get_shared_pool) — voir database.py.
    `_init_schema()` reste propre à billing.py et n'est jouée qu'une fois."""
    global _schema_ready
    dsn = _env("SUPABASE_DB_URL")
    if not dsn:
        raise RuntimeError(
            "SUPABASE_DB_URL non définie — impossible de se connecter à la base."
        )
    pool = get_shared_pool(dsn)
    if not _schema_ready:
        with _pool_lock:
            if not _schema_ready:
                # IMPORTANT : `_schema_ready` DOIT être mis à True AVANT
                # l'appel à _init_schema() ci-dessous, pas après. _init_schema()
                # appelle en interne _run(_fn), qui rappelle _get_pool() — si
                # `_schema_ready` est encore False à ce moment-là, cet appel
                # récursif retente d'acquérir `_pool_lock` (déjà tenu par ce
                # même thread) et provoque un DEADLOCK (threading.Lock n'est
                # pas réentrant). Même correctif que l'ancien bug pool-based
                # vécu en production le 02/08/2026 (voir post-mortem), transposé
                # au flag de schéma depuis le passage au pool partagé.
                _schema_ready = True
                _init_schema()
    return pool


def close_idle_connections() -> None:
    """Appelé par app.py au tout début de CHAQUE run, avant l'auth : ferme
    la connexion partagée que CE thread avait ouverte lors du run précédent.
    Délègue au pool partagé (database.close_idle_connections) — idempotent
    si auth.py/ecb_rates.py/vies_engine.py l'ont déjà fermée dans ce run."""
    _database.close_idle_connections()


def _run(fn):
    """Exécute fn(conn, cur) avec une connexion prise dans le pool, avec un
    retry unique si la connexion s'avère fermée côté serveur.

    Même correctif que tva_intracom/auth.py : le pool global survit à toutes
    les reruns tant que le process tourne, et le pooler Supabase (PgBouncer,
    mode transaction) recycle agressivement les connexions inactives côté
    serveur — d'où `psycopg2.InterfaceError: connection already closed`
    après un moment d'inactivité. On jette le pool et on en recrée un neuf
    pour retenter une fois plutôt que de laisser planter la requête."""
    return run_with_retry(_get_pool, fn, on_retry=reset_shared_pool)


def _init_schema() -> None:
    def _fn(conn, cur):
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS tva_customers (
                user_id TEXT PRIMARY KEY,
                stripe_customer_id TEXT UNIQUE NOT NULL
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS tva_subscriptions (
                user_id TEXT PRIMARY KEY,
                stripe_subscription_id TEXT NOT NULL,
                status TEXT NOT NULL,
                plan TEXT NOT NULL,
                current_period_end DOUBLE PRECISION NOT NULL,
                updated_at DOUBLE PRECISION NOT NULL
            )
            """
        )
        # Colonnes ajoutées pour les forfaits Pro/Cabinet (mensuel/annuel,
        # quantité de SIREN pour le palier Cabinet). ADD COLUMN IF NOT
        # EXISTS est idempotent — sûr à ré-exécuter à chaque déploiement.
        cur.execute(
            "ALTER TABLE tva_subscriptions ADD COLUMN IF NOT EXISTS billing_interval TEXT"
        )
        cur.execute(
            "ALTER TABLE tva_subscriptions ADD COLUMN IF NOT EXISTS siren_quantity INTEGER"
        )
        # Downgrade différé (Subscription Schedules Stripe, ajout 2026-08-16) :
        # un changement de plan planifié (effectif à la fin de la période en
        # cours, pour éviter les avoirs) n'affecte PAS `plan`/`billing_interval`
        # tout de suite — ces 3 colonnes stockent l'info du changement à venir,
        # uniquement pour affichage utilisateur (cf. sidebar.py). Elles sont
        # effacées dès que la planification s'achève ou est annulée (voir
        # handle_stripe_webhook_event, events subscription_schedule.*).
        cur.execute(
            "ALTER TABLE tva_subscriptions ADD COLUMN IF NOT EXISTS scheduled_plan TEXT"
        )
        cur.execute(
            "ALTER TABLE tva_subscriptions ADD COLUMN IF NOT EXISTS scheduled_billing_interval TEXT"
        )
        cur.execute(
            "ALTER TABLE tva_subscriptions ADD COLUMN IF NOT EXISTS scheduled_change_at DOUBLE PRECISION"
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS tva_export_credits (
                user_id TEXT NOT NULL,
                period_label TEXT NOT NULL,
                purchased_at DOUBLE PRECISION NOT NULL,
                stripe_payment_intent_id TEXT,
                PRIMARY KEY (user_id, period_label)
            )
            """
        )
        # BUGFIX (2026-09-04) : un crédit PAYG n'était scellé qu'à (org_id,
        # period_label) — aucune notion de SIREN. Deux fichiers de vente sur
        # la même période mais rattachés à des SIREN différents (donc à des
        # clients différents) débloquaient tous les deux l'export dès qu'UN
        # SEUL crédit avait été acheté pour cette période, alors qu'un
        # paiement à l'unité doit couvrir un SIREN précis. Colonne ajoutée
        # avec défaut '' (chaîne vide, pas NULL — impossible dans une clé
        # primaire) : les crédits déjà achetés avant ce correctif restent
        # valables pour n'importe quel SIREN de l'org (voir has_export_credit),
        # par rétrocompatibilité — seuls les nouveaux crédits sont scellés à
        # un SIREN précis dès leur achat (voir create_payg_checkout_session /
        # _fulfill_checkout_session).
        cur.execute(
            "ALTER TABLE tva_export_credits ADD COLUMN IF NOT EXISTS siren TEXT NOT NULL DEFAULT ''"
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS tva_siren_registrations (
                user_id TEXT NOT NULL,
                siren TEXT NOT NULL,
                company_name TEXT,
                tva_number TEXT,
                first_used_at DOUBLE PRECISION NOT NULL,
                PRIMARY KEY (user_id, siren)
            )
            """
        )
        # Retrait différé (lazy deletion) : un SIREN marqué en attente de
        # retrait reste utilisable jusqu'à sa date d'échéance (date
        # anniversaire de l'abonnement au moment de la demande), pour
        # éviter les abus (ajout/retrait à volonté en cours de période).
        cur.execute(
            "ALTER TABLE tva_siren_registrations ADD COLUMN IF NOT EXISTS pending_removal_at DOUBLE PRECISION"
        )
        # Nouveaux paramètres d'import liés au SIREN
        cur.execute("ALTER TABLE tva_siren_registrations ADD COLUMN IF NOT EXISTS ioss_number TEXT")
        cur.execute("ALTER TABLE tva_siren_registrations ADD COLUMN IF NOT EXISTS seller_is_importer BOOLEAN DEFAULT FALSE")
        cur.execute("ALTER TABLE tva_siren_registrations ADD COLUMN IF NOT EXISTS apply_fr_under_threshold BOOLEAN DEFAULT FALSE")
        cur.execute("ALTER TABLE tva_siren_registrations ADD COLUMN IF NOT EXISTS countries_with_vat TEXT")
        cur.execute("ALTER TABLE tva_siren_registrations ADD COLUMN IF NOT EXISTS vat_numbers_json TEXT")
        cur.execute("ALTER TABLE tva_siren_registrations ADD COLUMN IF NOT EXISTS oss_threshold_exceeded_prev_year BOOLEAN DEFAULT FALSE")
        cur.execute("ALTER TABLE tva_siren_registrations ADD COLUMN IF NOT EXISTS ioss_own_number_active BOOLEAN DEFAULT FALSE")
        # Liaison compte Amazon (UNIQUE_ACCOUNT_IDENTIFIER) <-> SIREN — anti-abus :
        # empêche d'exporter le fichier d'un client sous le SIREN payé d'un
        # autre. Scope_id = même portée que le cache VIES (vies_engine.resolve_scope_id) :
        # partagée entre tous les utilisateurs d'un même cabinet (domaine pro),
        # isolée par compte pour les domaines grand public (gmail...). Un même
        # identifiant ne peut être lié qu'à un seul SIREN dans un scope donné
        # (PK), un SIREN peut en revanche posséder plusieurs identifiants.
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS tva_account_siren_links (
                scope_id TEXT NOT NULL,
                account_identifier TEXT NOT NULL,
                siren TEXT NOT NULL,
                linked_at DOUBLE PRECISION NOT NULL,
                PRIMARY KEY (scope_id, account_identifier)
            )
            """
        )
        conn.commit()

        _migrate_billing_to_org_id(cur)
        conn.commit()

    _run(_fn)


def _migrate_billing_to_org_id(cur) -> None:
    """Migration 2026-08-24 : abonnement, clients Stripe, crédits PAYG et
    SIREN passent de `user_id` à `org_id` comme clé de partage (voir
    README - évolution.md). Un même org_id (cabinet multi-comptes, résolu
    par domaine e-mail — voir auth.resolve_org_id) partage désormais UN
    SEUL abonnement, UN SEUL client Stripe, les mêmes crédits PAYG et la
    même liste de SIREN entre tous ses membres, au lieu d'un jeu de données
    isolé par compte individuel.

    Idempotent et sûre à ré-exécuter à chaque démarrage :
    - ADD COLUMN IF NOT EXISTS pour org_id/added_by_user_id ;
    - backfill uniquement des lignes où org_id est encore NULL ;
    - le changement de PRIMARY KEY est gardé par un test sur
      information_schema (ne s'exécute qu'une fois, la 1ère fois où la
      contrainte "..._org_pkey" n'existe pas encore).

    Diagnostic préalable (2026-08-24, scripts/diag_org_migration.py) sur la
    base de production : une seule organisation multi-comptes
    ('domain:yy.com', 2 comptes), aucun conflit (pas de double abonnement
    actif, pas de double client Stripe, pas de SIREN dupliqué entre
    comptes) — la fusion ci-dessous ne perd donc aucune donnée pour l'état
    actuel de la base. Si de nouveaux comptes multi-utilisateurs sont créés
    entre le diagnostic et le déploiement de cette migration, relancer le
    script de diagnostic avant de déployer.
    """
    for table in ("tva_customers", "tva_subscriptions", "tva_export_credits", "tva_siren_registrations"):
        cur.execute(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS org_id TEXT")
        # BACKFILL (2026-09-03) : manquant jusqu'ici alors que le docstring de
        # cette fonction l'annonçait déjà — sans lui, le ALTER COLUMN ... SET
        # NOT NULL plus bas échoue dès qu'il existe des lignes existantes
        # (org_id encore NULL juste après l'ADD COLUMN), ce qui fait échouer
        # toute la transaction et annule même l'ADD COLUMN (rollback), d'où
        # l'erreur "column org_id does not exist" observée en production sur
        # la branche main malgré un déploiement en apparence identique à dev.
        cur.execute(f"UPDATE {table} SET org_id = user_id WHERE org_id IS NULL")

    # NOTE : la colonne `user_id` existante N'EST PAS retirée. Elle change
    # juste de rôle : de clé primaire, elle devient une colonne d'audit
    # ("qui a effectué la dernière écriture") — toujours alimentée par les
    # fonctions d'écriture ci-dessous (register_siren, grant_export_credit,
    # _upsert_subscription...), qui reçoivent désormais un paramètre
    # `acting_user_id` distinct de la clé `org_id`.

    # Migration PRIMARY KEY (idempotente)
    cur.execute(
        "ALTER TABLE tva_customers ALTER COLUMN org_id SET NOT NULL"
    )
    cur.execute(
        "ALTER TABLE tva_subscriptions ALTER COLUMN org_id SET NOT NULL"
    )
    cur.execute(
        "ALTER TABLE tva_export_credits ALTER COLUMN org_id SET NOT NULL"
    )
    cur.execute(
        "ALTER TABLE tva_siren_registrations ALTER COLUMN org_id SET NOT NULL"
    )

    # Bascule de la PRIMARY KEY : user_id -> org_id (ou (org_id, siren) pour
    # les tables composites). Gardée par l'existence de la nouvelle
    # contrainte pour rester idempotente sans tenter un DROP/ADD à chaque
    # démarrage.
    #
    # BUGFIX (2026-09-04) : tva_export_credits est sorti de cette boucle
    # générique. Sa PK a une 3e étape (org_pkey -> siren_pkey, voir juste en
    # dessous) : au 2e démarrage après cette 3e étape, le test d'idempotence
    # ci-dessous (qui ne connaît que new_pk="tva_export_credits_org_pkey")
    # ne trouvait plus cette contrainte — remplacée entre-temps par
    # siren_pkey — et retentait un ADD CONSTRAINT org_pkey en plus de la PK
    # déjà en place, ce que Postgres refuse ("multiple primary keys for
    # table ... are not allowed"). La migration complète de cette table est
    # donc gérée dans un seul bloc dédié, qui connaît toute la chaîne des
    # noms de contrainte possibles.
    _pk_migrations = [
        ("tva_customers", "tva_customers_pkey", "tva_customers_org_pkey", "(org_id)"),
        ("tva_subscriptions", "tva_subscriptions_pkey", "tva_subscriptions_org_pkey", "(org_id)"),
        ("tva_siren_registrations", "tva_siren_registrations_pkey", "tva_siren_registrations_org_pkey", "(org_id, siren)"),
    ]
    for table, old_pk, new_pk, key_cols in _pk_migrations:
        cur.execute(
            "SELECT 1 FROM information_schema.table_constraints WHERE table_name=%s AND constraint_name=%s",
            (table, new_pk),
        )
        if cur.fetchone():
            continue  # déjà migré
        # NOTE : les tables tva_customers/tva_subscriptions ont aujourd'hui
        # une PK sur user_id SEUL, donc un unique compte par table. Puisque
        # le diagnostic n'a trouvé aucun cas de 2 comptes de la même org
        # ayant chacun leur propre client Stripe/abonnement, regrouper sur
        # org_id ne crée pas de doublon de clé ici. Si un futur diagnostic
        # trouvait un tel cas, il faudrait fusionner manuellement (choisir
        # quel abonnement garder) AVANT de relancer cette migration.
        cur.execute(f"ALTER TABLE {table} DROP CONSTRAINT IF EXISTS {old_pk}")
        cur.execute(f"ALTER TABLE {table} ADD CONSTRAINT {new_pk} PRIMARY KEY {key_cols}")

    # tva_export_credits : chaîne complète user_id -> (org_id, period_label)
    # -> (org_id, period_label, siren) gérée ici en une seule étape
    # idempotente, indépendamment d'où en est la table (jamais migrée,
    # migrée seulement jusqu'à org_pkey, ou déjà à siren_pkey). `siren` vaut
    # '' par défaut pour toutes les lignes existantes : (org_id,
    # period_label) était déjà unique, donc élargir à (org_id, period_label,
    # siren='') ne peut pas créer de doublon.
    cur.execute(
        "SELECT 1 FROM information_schema.table_constraints "
        "WHERE table_name='tva_export_credits' AND constraint_name='tva_export_credits_siren_pkey'"
    )
    if not cur.fetchone():
        cur.execute("ALTER TABLE tva_export_credits DROP CONSTRAINT IF EXISTS tva_export_credits_pkey")
        cur.execute("ALTER TABLE tva_export_credits DROP CONSTRAINT IF EXISTS tva_export_credits_org_pkey")
        cur.execute(
            "ALTER TABLE tva_export_credits ADD CONSTRAINT tva_export_credits_siren_pkey "
            "PRIMARY KEY (org_id, period_label, siren)"
        )


def _org_id_for_user(user_id: str) -> Optional[str]:
    """Lit org_id depuis tva_users pour ce compte. None si le compte n'existe
    plus côté tva_users (ex. déjà supprimé par auth.delete_account avant
    l'appel à delete_user_billing_data)."""
    def _fn(conn, cur):
        cur.execute("SELECT org_id FROM tva_users WHERE id=%s", (user_id,))
        row = cur.fetchone()
        return row[0] if row else None

    return _run(_fn)


def _other_org_members_count(org_id: str, excluding_user_id: str) -> int:
    def _fn(conn, cur):
        cur.execute(
            "SELECT COUNT(*) FROM tva_users WHERE org_id=%s AND id<>%s",
            (org_id, excluding_user_id),
        )
        return cur.fetchone()[0]

    return _run(_fn)


def delete_user_billing_data(user_id: str) -> None:
    """Supprime les données de facturation liées à ce compte.

    ORG_ID (2026-08-24) : abonnement, client Stripe, crédits PAYG et SIREN
    sont désormais PARTAGÉS par toute l'organisation (org_id), plus
    propriété exclusive d'un seul user_id. Supprimer un compte qui n'est
    PAS le dernier membre de son organisation ne doit donc PLUS supprimer
    ces données partagées — ça couperait l'accès payant des collègues
    restants. On ne supprime réellement l'abonnement/le client
    Stripe/les SIREN que si ce compte est le DERNIER membre de son org.
    Dans tous les cas, le compte lui-même (tva_users) reste supprimé par
    auth.delete_account, appelant de cette fonction — non modifié ici."""
    org_id = _org_id_for_user(user_id)
    if org_id is None:
        logger.info(
            "delete_user_billing_data: org_id introuvable pour user_id=%s "
            "(compte déjà absent de tva_users) — rien à faire.", user_id,
        )
        return

    if _other_org_members_count(org_id, excluding_user_id=user_id) > 0:
        logger.info(
            "delete_user_billing_data: org_id=%s a d'autres membres — "
            "abonnement/client Stripe/SIREN partagés CONSERVÉS pour eux "
            "(seul le compte user_id=%s est supprimé, par auth.delete_account).",
            org_id, user_id,
        )
        return

    # Dernier membre de l'organisation : on peut supprimer les données de
    # facturation partagées, elles ne servent plus à personne.
    customer_id = _existing_stripe_customer_id(org_id)
    if customer_id and _stripe_configured():
        try:
            stripe.Customer.delete(customer_id)
        except stripe.error.InvalidRequestError as exc:
            # Cas bénin attendu : le client n'existe déjà plus côté Stripe
            # (ex. suppression manuelle préalable, ou double appel).
            logger.info(
                "Suppression Stripe du client %s : déjà absent côté Stripe (%s).",
                customer_id, exc,
            )
        except Exception as exc:
            # Tout autre échec (réseau, auth, rate limit...) est un vrai
            # risque : si le client Stripe n'est pas supprimé, son
            # abonnement peut continuer à être facturé alors que
            # l'utilisateur pense ses données supprimées (risque RGPD +
            # facturation indue). On logue en erreur mais on NE bloque PAS
            # la suppression des données locales, qui reste demandée
            # explicitement par l'utilisateur.
            logger.error(
                "Échec de la suppression du client Stripe %s lors de la suppression "
                "du dernier compte (user_id=%s) de l'organisation org_id=%s : %s. "
                "Le client Stripe et son abonnement peuvent être encore actifs côté "
                "Stripe — vérification manuelle recommandée.",
                customer_id, user_id, org_id, exc,
            )

    def _fn(conn, cur):
        cur.execute("DELETE FROM tva_customers WHERE org_id=%s", (org_id,))
        cur.execute("DELETE FROM tva_subscriptions WHERE org_id=%s", (org_id,))
        cur.execute("DELETE FROM tva_export_credits WHERE org_id=%s", (org_id,))
        cur.execute("DELETE FROM tva_siren_registrations WHERE org_id=%s", (org_id,))
        # On ne supprime tva_account_siren_links que si le scope_id correspond à un scope utilisateur
        # mais on n'a pas accès à l'email ici. On laisse auth.py s'en charger s'il le souhaite
        # ou on le fait par filtrage de préfixe.
        cur.execute("DELETE FROM tva_account_siren_links WHERE scope_id LIKE 'user:%'")
        conn.commit()

    _run(_fn)


def export_user_billing_data(user_id: str) -> dict:
    """Récupère toutes les données de facturation pour export RGPD.

    ORG_ID (2026-08-24) : ces données étant désormais partagées au niveau de
    l'organisation, l'export renvoie l'état PARTAGÉ (abonnement, SIREN...)
    de l'org de ce compte, pas des données isolées de ce seul user_id."""
    org_id = _org_id_for_user(user_id)
    if org_id is None:
        return {"customer": None, "subscriptions": [], "export_credits": [], "siren_registrations": []}

    def _fn(conn, cur):
        data = {}

        cur.execute("SELECT stripe_customer_id FROM tva_customers WHERE org_id=%s", (org_id,))
        data["customer"] = cur.fetchone()

        cur.execute("SELECT * FROM tva_subscriptions WHERE org_id=%s", (org_id,))
        data["subscriptions"] = [dict(zip([col[0] for col in cur.description], row)) for row in cur.fetchall()]

        cur.execute("SELECT * FROM tva_export_credits WHERE org_id=%s", (org_id,))
        data["export_credits"] = [dict(zip([col[0] for col in cur.description], row)) for row in cur.fetchall()]

        cur.execute("SELECT * FROM tva_siren_registrations WHERE org_id=%s", (org_id,))
        rows = cur.fetchall()
        # Déchiffrement des noms d'entreprises
        regs = []
        for r in rows:
            d = dict(zip([col[0] for col in cur.description], r))
            if d.get("company_name"):
                d["company_name"] = _dec(d["company_name"])
            regs.append(d)
        data["siren_registrations"] = regs

        return data

    return _run(_fn)


@dataclass
class SubscriptionStatus:
    active: bool
    plan: Optional[str] = None
    status: Optional[str] = None
    current_period_end: Optional[float] = None
    billing_interval: Optional[str] = None
    siren_quantity: Optional[int] = None
    # Downgrade différé planifié (Subscription Schedule Stripe) — voir note
    # dans _init_schema. None/None/None si aucun changement n'est programmé.
    scheduled_plan: Optional[str] = None
    scheduled_billing_interval: Optional[str] = None
    scheduled_change_at: Optional[float] = None


@st.cache_data(ttl=60, show_spinner=False)
def get_subscription_status(org_id: str) -> SubscriptionStatus:
    """ORG_ID (2026-08-24) : un seul abonnement par organisation, partagé
    entre tous ses membres (voir _migrate_billing_to_org_id)."""
    def _fn(conn, cur):
        cur.execute(
            """
            SELECT status, plan, current_period_end, billing_interval, siren_quantity,
                   scheduled_plan, scheduled_billing_interval, scheduled_change_at
            FROM tva_subscriptions WHERE org_id=%s
            """,
            (org_id,),
        )
        return cur.fetchone()

    row = _run(_fn)

    if not row:
        return SubscriptionStatus(active=False)

    (status, plan, period_end, billing_interval, siren_quantity,
     scheduled_plan, scheduled_billing_interval, scheduled_change_at) = row
    active = status in ("active", "trialing") and period_end > time.time()
    return SubscriptionStatus(
        active=active,
        plan=plan,
        status=status,
        current_period_end=period_end,
        billing_interval=billing_interval,
        siren_quantity=siren_quantity,
        scheduled_plan=scheduled_plan,
        scheduled_billing_interval=scheduled_billing_interval,
        scheduled_change_at=scheduled_change_at,
    )


def has_active_subscription_direct(org_id: str) -> bool:
    return get_subscription_status(org_id).active


# Statuts de compte de haut niveau (2026-09-05) — voir get_account_status().
ACCOUNT_STATUS_GRATUIT = "gratuit"
ACCOUNT_STATUS_ACHAT = "achat"


def _has_any_payg_purchase(org_id: str) -> bool:
    """True si cette organisation a déjà effectué au moins un achat PAYG
    (peu importe la période ou le SIREN concerné) — sert uniquement à
    distinguer un compte "Achat" (voir get_account_status/ACCOUNT_STATUS_ACHAT)
    d'un compte gratuit n'ayant jamais payé, dans request_siren_removal()."""
    def _fn(conn, cur):
        cur.execute("SELECT 1 FROM tva_export_credits WHERE org_id=%s LIMIT 1", (org_id,))
        return cur.fetchone() is not None

    return _run(_fn)


def get_account_status(org_id: str) -> str:
    """Statut de compte affiché à l'utilisateur, par ordre de priorité :
    - "business" / "cabinet" : abonnement actif de ce type — un abonnement
      actif prime toujours sur un historique d'achat PAYG (demande produit
      explicite du 2026-09-05).
    - "achat" : aucun abonnement actif NI passé, mais au moins un achat PAYG
      déjà effectué. C'est ce statut qui verrouille le SIREN dans
      request_siren_removal() — dès qu'un abonnement a existé (même résilié
      depuis), l'organisation n'est plus considérée "achat unique" et
      redevient "gratuit" au sens de ce statut (le détail reste visible via
      last_sub_msg dans la sidebar).
    - "gratuit" : aucun paiement, abonnement ou PAYG, jamais effectué (ou
      abonnement déjà existant mais inactif)."""
    sub = get_subscription_status(org_id)
    if sub.active:
        return sub.plan or ACCOUNT_STATUS_GRATUIT
    if sub.status is not None:
        return ACCOUNT_STATUS_GRATUIT
    if _has_any_payg_purchase(org_id):
        return ACCOUNT_STATUS_ACHAT
    return ACCOUNT_STATUS_GRATUIT


def has_export_credit(org_id: str, period_label: str, siren: str = "") -> bool:
    """BUGFIX (2026-09-04) : un crédit PAYG doit être scellé à UN SIREN (donc
    un seul client/compte Amazon), pas seulement à une période — sinon deux
    fichiers de vente différents sur la même période mais rattachés à des
    SIREN distincts se débloquaient tous les deux au premier achat. On
    n'accepte donc qu'un crédit dont `siren` correspond exactement à celui
    demandé, ou un crédit legacy (`siren=''`, acheté avant ce correctif) —
    ces derniers restent valables pour n'importe quel SIREN de l'org, par
    rétrocompatibilité pure ; tout nouvel achat est scellé au SIREN courant
    (voir grant_export_credit / create_payg_checkout_session).
    """
    if has_active_subscription_direct(org_id):
        return True

    def _fn(conn, cur):
        cur.execute(
            "SELECT 1 FROM tva_export_credits WHERE org_id=%s AND period_label=%s AND siren IN (%s, '')",
            (org_id, period_label, siren or ""),
        )
        return cur.fetchone() is not None

    return _run(_fn)


def grant_export_credit(
        org_id: str, period_label: str, payment_intent_id: str = "",
        acting_user_id: str = "", siren: str = "",
) -> None:
    """acting_user_id : compte à l'origine de l'achat (audit uniquement,
    colonne `user_id` conservée à titre historique — org_id est la clé).

    siren : SIREN pour lequel ce crédit est valable (voir has_export_credit).
    Laissé à '' seulement pour les rares cas où aucun SIREN n'a encore pu
    être déterminé au moment de l'achat — ce crédit sera alors valable pour
    tout SIREN de l'org, comme les crédits legacy."""
    def _fn(conn, cur):
        cur.execute(
            """
            INSERT INTO tva_export_credits (org_id, user_id, period_label, siren, purchased_at, stripe_payment_intent_id)
            VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT (org_id, period_label, siren)
            DO UPDATE SET purchased_at = EXCLUDED.purchased_at,
                          stripe_payment_intent_id = EXCLUDED.stripe_payment_intent_id,
                          user_id = EXCLUDED.user_id
            """,
            (org_id, acting_user_id or org_id, period_label, siren or "", time.time(), payment_intent_id),
        )
        conn.commit()

    _run(_fn)


def list_purchased_credits(org_id: str) -> list[dict]:
    """Liste tous les crédits d'export PAYG achetés par l'organisation."""
    def _fn(conn, cur):
        cur.execute(
            "SELECT period_label, purchased_at, siren FROM tva_export_credits WHERE org_id=%s ORDER BY purchased_at DESC",
            (org_id,),
        )
        return cur.fetchall()

    rows = _run(_fn)
    return [{"period": r[0], "at": r[1], "siren": r[2]} for r in rows]


def list_legacy_export_credits() -> list[dict]:
    """Migration manuelle (2026-09-04) : liste tous les crédits PAYG achetés
    AVANT le rattachement d'un crédit à un SIREN précis (voir ADD COLUMN
    siren dans _init_schema / has_export_credit) — reconnaissables à
    `siren=''`. Ces crédits restent valables pour tout SIREN de leur org
    (rétrocompatibilité, voir has_export_credit) ; cette fonction sert
    uniquement à préparer un resserrement manuel via
    assign_siren_to_legacy_credit(), pour les org dont on peut établir sans
    ambiguïté le SIREN concerné (typiquement : un seul SIREN enregistré)."""
    def _fn(conn, cur):
        cur.execute(
            "SELECT org_id, period_label, purchased_at FROM tva_export_credits "
            "WHERE siren = '' ORDER BY org_id, period_label"
        )
        return cur.fetchall()

    rows = _run(_fn)
    return [{"org_id": r[0], "period_label": r[1], "purchased_at": r[2]} for r in rows]


def assign_siren_to_legacy_credit(org_id: str, period_label: str, siren: str) -> None:
    """Migration manuelle (2026-09-04) : resserre un crédit legacy
    (siren='') sur un SIREN précis a posteriori. À utiliser uniquement quand
    le SIREN concerné peut être établi avec certitude (voir
    scripts/migrate_legacy_export_credits.py) — ne fait rien de magique,
    n'écrit que ce qu'on lui donne explicitement."""
    def _fn(conn, cur):
        cur.execute(
            "UPDATE tva_export_credits SET siren=%s "
            "WHERE org_id=%s AND period_label=%s AND siren=''",
            (siren, org_id, period_label),
        )
        conn.commit()
        return cur.rowcount

    return _run(_fn)


# =============================================================================
# QUOTAS SIREN
# =============================================================================
# Le SIREN identifie l'entreprise cliente (9 chiffres). Chaque compte peut en
# enregistrer un nombre limité selon son forfait :
#   - Sans abonnement actif (PAYG) : pas de limite technique — le paiement se
#     fait par période, indépendamment du nombre de SIREN utilisés.
#   - Pro ("business")  : 1 SIREN maximum.
#   - Cabinet ("cabinet"): jusqu'à `siren_quantity` (quantité Stripe achetée).


def _purge_expired_siren_removals(org_id: str) -> None:
    """Supprime définitivement les SIREN dont le retrait différé est arrivé à
    échéance (lazy deletion : exécuté à chaque lecture, pas de tâche de fond)."""
    def _fn(conn, cur):
        cur.execute(
            """
            DELETE FROM tva_siren_registrations
            WHERE org_id=%s AND pending_removal_at IS NOT NULL AND pending_removal_at <= %s
            """,
            (org_id, time.time()),
        )
        conn.commit()

    _run(_fn)


@st.cache_data(ttl=60, show_spinner=False)
def list_registered_sirens(org_id: str) -> list[dict]:
    """Mis en cache (TTL 60s, même pattern que get_subscription_status) :
    mesuré en prod à ~830 ms par appel (requête réseau réelle vers Supabase,
    pas un coût de connexion — voir retour perf du 2026-08-02), et appelée
    2 à 3 fois par run (get_siren_quota_status, can_register_new_siren,
    sidebar.py, billing_gate.py appellent tous cette fonction indépendamment
    pour le même org_id). Invalidé explicitement (`.clear()`) par
    register_siren/request_siren_removal/cancel_siren_removal ci-dessous,
    pour ne jamais afficher un état SIREN périmé après une mutation.

    ORG_ID (2026-08-24) : les SIREN enregistrés sont partagés par toute
    l'organisation — tous les membres d'un même cabinet voient et gèrent la
    même liste, plus une liste isolée par compte individuel.

    TRI (2026-08-25) : ordre alphabétique par raison sociale (`company_name`,
    insensible à la casse ; à défaut de nom, par SIREN), plutôt que l'ordre
    d'enregistrement (`first_used_at`) — plus pratique pour un cabinet gérant
    de nombreux clients. Le tri est fait ici, côté Python, APRÈS déchiffrement
    (`_dec`) : `company_name` est chiffré (Fernet) en base, un `ORDER BY`
    SQL sur la colonne chiffrée donnerait un ordre sans rapport avec le nom
    réel. La requête garde `ORDER BY first_used_at ASC` uniquement pour un
    résultat déterministe avant tri Python (peu importe lequel, écrasé
    juste après)."""
    _purge_expired_siren_removals(org_id)

    def _fn(conn, cur):
        cur.execute(
            """
            SELECT siren, company_name, tva_number, first_used_at, pending_removal_at,
                   ioss_number, seller_is_importer, apply_fr_under_threshold, countries_with_vat, vat_numbers_json,
                   oss_threshold_exceeded_prev_year, ioss_own_number_active
            FROM tva_siren_registrations
            WHERE org_id=%s
            ORDER BY first_used_at ASC
            """,
            (org_id,),
        )
        return cur.fetchall()

    rows = _run(_fn)
    _decrypted = [
        {
            "siren": r[0], "company_name": _dec(r[1]), "tva_number": _dec(r[2]),
            "first_used_at": r[3], "pending_removal_at": r[4],
            "ioss_number": _dec(r[5]), "seller_is_importer": r[6],
            "apply_fr_under_threshold": r[7], "countries_with_vat": r[8],
            "vat_numbers_json": _dec(r[9]),
            "oss_threshold_exceeded_prev_year": r[10] if len(r) > 10 else False,
            "ioss_own_number_active": r[11] if len(r) > 11 else False,
        }
        for r in rows
    ]
    _decrypted.sort(key=lambda d: (d["company_name"] or d["siren"] or "").casefold())
    return _decrypted


def get_siren_quota(org_id: str) -> int:
    """Retourne le quota de SIREN distincts pour cette organisation.

    - Pas d'abonnement actif (PAYG) : 1 SIREN, comme le forfait Pro.
    - Pro ("business") : 1 SIREN.
    - Cabinet ("cabinet") : quantité Stripe achetée (`siren_quantity`).
    """
    sub = get_subscription_status(org_id)
    if not sub.active:
        return _BUSINESS_SIREN_QUOTA
    if sub.plan == "business":
        return _BUSINESS_SIREN_QUOTA
    if sub.plan == "cabinet":
        return sub.siren_quantity or 1
    return _BUSINESS_SIREN_QUOTA


@dataclass
class SirenQuotaStatus:
    registered_count: int
    quota: int
    over_quota_by: int  # 0 si dans les clous

    @property
    def blocked(self) -> bool:
        return self.over_quota_by > 0


def get_siren_quota_status(org_id: str) -> SirenQuotaStatus:
    quota = get_siren_quota(org_id)
    count = len(list_registered_sirens(org_id))
    return SirenQuotaStatus(registered_count=count, quota=quota, over_quota_by=max(0, count - quota))


def can_register_new_siren(org_id: str) -> tuple[bool, str]:
    """Vérifie si l'organisation peut enregistrer un SIREN supplémentaire
    (celui-ci n'étant pas déjà dans sa liste). Ne s'applique pas à un SIREN
    déjà enregistré (mise à jour du nom/TVA toujours autorisée).

    Best-effort, PAS transactionnel : sert au retour rapide côté UI (message
    avant même de tenter l'enregistrement). Le garde-fou qui compte
    réellement contre une course concurrente (deux membres du même cabinet
    ajoutant chacun un SIREN au même instant) est le verrou avisé
    (`pg_advisory_xact_lock`) + recomptage pris DANS register_siren() -- voir
    BUGFIX point #4, README - évolution.md."""
    status = get_siren_quota_status(org_id)
    if status.registered_count >= status.quota:
        return False, (
            f"Quota de {status.quota} SIREN atteint pour votre abonnement actuel. "
            "Passez à un forfait supérieur ou augmentez votre quantité Cabinet "
            "pour en enregistrer un de plus."
        )
    return True, ""


def _require_write_access(user_id: str) -> None:
    """Lève PermissionError si ce compte est en lecture seule (`role`
    "reader" — voir auth.py/ui/admin.py). Utilisé par toutes les fonctions
    d'écriture SIREN/TVA de ce module.

    Requête directement `tva_users` via le pool de CE module (billing._run)
    plutôt que d'appeler auth.get_user_by_id — auth.py maintient son propre
    pool/schéma (partagé via database.py, mais initialisé indépendamment) ;
    passer par le pool de billing.py évite une dépendance croisée inutile et
    reste cohérent avec les tests existants qui mockent billing._get_pool()."""
    def _fn(conn, cur):
        cur.execute("SELECT role FROM tva_users WHERE id=%s", (user_id,))
        row = cur.fetchone()
        return row[0] if row else None

    role = _run(_fn)
    if role == "reader":
        raise PermissionError(
            "Votre compte est en lecture seule — contactez l'administrateur "
            "de votre organisation pour modifier ces données."
        )


def register_siren(
        org_id: str, acting_user_id: str, siren: str, company_name: str = "", tva_number: str = "",
        ioss_number: str = "", seller_is_importer: bool = False,
        apply_fr_under_threshold: bool = False, countries_with_vat: str = "",
        vat_numbers_json: str = "",
        oss_threshold_exceeded_prev_year: bool = False,
        ioss_own_number_active: bool = False,
) -> None:
    """Enregistre un SIREN pour ce compte, ou met à jour ses métadonnées s'il
    est déjà enregistré. Le contrôle de quota (`can_register_new_siren`) doit
    être fait par l'appelant AVANT d'appeler cette fonction pour un nouveau
    SIREN — cette fonction ne le revérifie pas elle-même.

    oss_threshold_exceeded_prev_year : déclaratif utilisateur — le seuil OSS
        de 10 000 € a-t-il été dépassé l'année civile précédente (tous canaux
        confondus, hors périmètre de cet outil) ? Si vrai, le régime "sous
        seuil" (apply_fr_under_threshold) ne doit PAS s'appliquer pour
        l'année en cours, quel que soit le cumul recalculé par l'outil sur
        les seules données importées ici (CGI art. 258 B / art. 59 quater
        dir. 2006/112/CE : appréciation sur l'année en cours ET l'année N-1).
        Concerne l'année de traitement en cours ; pour un import multi-années
        dont le statut N-1 diffère selon l'année, traiter chaque année dans
        un import séparé.
    ioss_own_number_active : voir docstring engine.compute_vat().

    SÉCURITÉ (voir README - évolution.md) : `tva_number`, `ioss_number` et
    `vat_numbers_json` sont désormais chiffrés (Fernet, `_enc`) avant
    insertion, au même titre que `company_name` — ces numéros identifient
    de manière unique l'activité fiscale d'un client et étaient stockés en
    clair. Backfill effectué le 2026-08-16 (`backfill_encrypt_pii.py`) sur
    toutes les lignes existantes ; le fail-open de `decrypt_data` a été
    retiré en conséquence (security.py) — une valeur non chiffrée en base
    lève désormais une erreur explicite plutôt que d'être acceptée.

    RÔLES (2026-08-23) : un compte lecteur (`role="reader"`, voir auth.py)
    ne peut pas enregistrer/modifier de SIREN — contrôle fait ici, côté
    serveur, en plus du masquage des champs côté UI (sidebar.py), pour ne
    pas dépendre uniquement du frontend.

    ORG_ID (2026-08-24) : le SIREN est enregistré au niveau de
    l'organisation (`org_id`), partagé par tous ses membres — `acting_user_id`
    identifie qui a fait CETTE écriture (contrôle de rôle + colonne d'audit
    `user_id`, plus la clé de la ligne).
    """
    _require_write_access(acting_user_id)

    # BUGFIX (point #4, README - évolution.md) : capturé AVANT la transaction
    # verrouillée ci-dessous plutôt que rappelé via get_siren_quota() depuis
    # l'intérieur de `_fn` — get_siren_quota() passe par _run() (donc un
    # nouveau `with conn:`/commit sur la même connexion mise en cache par
    # thread, voir database.py NonPoolingConnectionPool(cache_connection=True))
    # qui commiterait prématurément la transaction verrouillée avant notre
    # propre INSERT. Léger compromis accepté : le quota lu ici peut en
    # théorie devenir obsolète entre cet appel et le verrou pris juste après
    # (ex. changement de forfait Stripe concurrent, cas indépendant de la
    # race ciblée ici) — négligeable comparé au problème résolu (deux AJOUTS
    # de SIREN concurrents pour la MÊME organisation).
    _quota_for_new_siren = get_siren_quota(org_id)

    def _fn(conn, cur):
        # BUGFIX (point #4, README - évolution.md) : verrou avisé Postgres
        # transactionnel scopé à org_id, même pattern que
        # auth.lock_org_for_user (2026-08-26). Ferme la race TOCTOU
        # documentée plus haut dans cette docstring et dans
        # can_register_new_siren() : deux membres du même cabinet
        # enregistrant chacun un SIREN DIFFÉRENT à quelques centaines de ms
        # d'intervalle pouvaient tous deux passer can_register_new_siren()
        # (lu AVANT cet appel, côté UI) avant que l'un des deux ne commit,
        # faisant passer registered_count à quota+1. La deuxième transaction
        # concurrente pour la même org_id attend ici que la première commit
        # (et libère le verrou) avant de faire son propre COMPTAGE, qui la
        # voit alors déjà à quota et bloque correctement. Verrou libéré
        # automatiquement au commit/rollback, aucun UNLOCK explicite requis.
        # Sans effet sur la simple mise à jour d'un SIREN déjà enregistré
        # (le verrou est pris dans tous les cas, mais le comptage/blocage
        # ci-dessous ne s'applique qu'à un NOUVEAU SIREN, voir is_new_siren).
        cur.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (org_id,))

        cur.execute(
            "SELECT 1 FROM tva_siren_registrations WHERE org_id=%s AND siren=%s",
            (org_id, siren),
        )
        is_new_siren = cur.fetchone() is None
        if is_new_siren:
            cur.execute(
                "SELECT COUNT(*) FROM tva_siren_registrations WHERE org_id=%s",
                (org_id,),
            )
            current_count = cur.fetchone()[0]
            if current_count >= _quota_for_new_siren:
                raise PermissionError(
                    f"Quota de {_quota_for_new_siren} SIREN atteint pour votre "
                    "abonnement actuel (vérification concurrente) — un autre "
                    "enregistrement vient probablement de consommer le dernier "
                    "slot disponible au même instant. Passez à un forfait "
                    "supérieur ou augmentez votre quantité Cabinet pour en "
                    "enregistrer un de plus."
                )

        cur.execute(
            """
            INSERT INTO tva_siren_registrations (
                org_id, user_id, siren, company_name, tva_number, first_used_at,
                ioss_number, seller_is_importer, apply_fr_under_threshold, countries_with_vat, vat_numbers_json,
                oss_threshold_exceeded_prev_year, ioss_own_number_active
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (org_id, siren)
            DO UPDATE SET company_name = EXCLUDED.company_name,
                          user_id = EXCLUDED.user_id,
                          -- SÉCURITÉ (voir README - évolution.md) : verrouillage définitif de
                          -- tva_number/ioss_number appliqué désormais AUSSI côté SQL, pas
                          -- seulement dans l'UI (_edit_siren_form_fragment cache le champ une
                          -- fois rempli, mais un appel direct à register_siren -- bug de script,
                          -- appel API -- pouvait jusqu'ici écraser une valeur déjà "verrouillée").
                          -- Une valeur déjà enregistrée (non NULL/non vide) est conservée quel que
                          -- soit ce qui est passé en paramètre ; seul un champ encore vide peut
                          -- être renseigné. vat_numbers_json n'est volontairement PAS verrouillé
                          -- ainsi : son usage légitime consiste à AJOUTER un nouveau pays au fil du
                          -- temps (le verrouillage par pays déjà rempli est, lui, géré au niveau UI).
                          tva_number = CASE
                              WHEN tva_siren_registrations.tva_number IS NOT NULL
                                   AND tva_siren_registrations.tva_number <> ''
                              THEN tva_siren_registrations.tva_number
                              ELSE EXCLUDED.tva_number
                          END,
                          ioss_number = CASE
                              WHEN tva_siren_registrations.ioss_number IS NOT NULL
                                   AND tva_siren_registrations.ioss_number <> ''
                              THEN tva_siren_registrations.ioss_number
                              ELSE EXCLUDED.ioss_number
                          END,
                          seller_is_importer = EXCLUDED.seller_is_importer,
                          apply_fr_under_threshold = EXCLUDED.apply_fr_under_threshold,
                          countries_with_vat = EXCLUDED.countries_with_vat,
                          vat_numbers_json = EXCLUDED.vat_numbers_json,
                          oss_threshold_exceeded_prev_year = EXCLUDED.oss_threshold_exceeded_prev_year,
                          ioss_own_number_active = EXCLUDED.ioss_own_number_active
            """,
            (org_id, acting_user_id, siren, _enc(company_name), _enc(tva_number), time.time(),
             _enc(ioss_number), seller_is_importer, apply_fr_under_threshold, countries_with_vat, _enc(vat_numbers_json),
             oss_threshold_exceeded_prev_year, ioss_own_number_active),
        )
        conn.commit()

    _run(_fn)
    list_registered_sirens.clear()


def request_siren_removal(org_id: str, acting_user_id: str, siren: str) -> float:
    """Marque un SIREN "en attente de retrait". Le retrait est effectif à la
    date anniversaire de l'abonnement en cours (current_period_end) pour
    éviter les abus (retirer/ajouter un SIREN à volonté en cours de période).
    Sans abonnement actif MAIS ayant déjà eu un abonnement (même résilié
    depuis), le retrait est immédiat (pas de notion de période).

    STATUT "Achat" (2026-09-05) : une organisation qui n'a JAMAIS souscrit
    d'abonnement mais a déjà effectué au moins un achat PAYG a son SIREN
    VERROUILLÉ — aucun retrait possible (PermissionError) — pour empêcher
    l'abus consistant à retirer/réajouter un SIREN différent à volonté sur
    un compte à l'unité (le quota de 1 SIREN à la fois ne suffisait pas à
    lui seul à empêcher cette rotation). Le verrou saute dès que
    l'organisation souscrit un abonnement, actif ou non : voir
    get_account_status() pour la même logique de priorité.

    Retourne le timestamp d'échéance effective."""
    _require_write_access(acting_user_id)
    sub = get_subscription_status(org_id)
    if sub.active and sub.current_period_end:
        effective_at = sub.current_period_end
    elif sub.status is not None:
        # Abonnement déjà existant (actif ou passé/résilié) : comportement
        # standard, immédiat puisqu'on sait déjà qu'il n'est pas actif ici.
        effective_at = time.time()
    elif _has_any_payg_purchase(org_id):
        raise PermissionError(
            "Ce SIREN est verrouillé : un compte à l'achat unique (PAYG) ne "
            "permet pas de changer de SIREN. Souscrivez un abonnement pour "
            "pouvoir en changer (retrait possible ensuite à la date de "
            "renouvellement)."
        )
    else:
        # Jamais rien payé (ni abonnement, ni PAYG) : retrait immédiat.
        effective_at = time.time()

    def _fn(conn, cur):
        cur.execute(
            "UPDATE tva_siren_registrations SET pending_removal_at=%s, user_id=%s WHERE org_id=%s AND siren=%s",
            (effective_at, acting_user_id, org_id, siren),
        )
        conn.commit()

    _run(_fn)
    list_registered_sirens.clear()
    return effective_at


def cancel_siren_removal(org_id: str, acting_user_id: str, siren: str) -> None:
    """Annule une demande de retrait en attente."""
    _require_write_access(acting_user_id)

    def _fn(conn, cur):
        cur.execute(
            "UPDATE tva_siren_registrations SET pending_removal_at=NULL, user_id=%s WHERE org_id=%s AND siren=%s",
            (acting_user_id, org_id, siren),
        )
        conn.commit()

    _run(_fn)
    list_registered_sirens.clear()


# =============================================================================
# LIAISON COMPTE AMAZON (UNIQUE_ACCOUNT_IDENTIFIER) <-> SIREN
# =============================================================================
# Anti-abus : un UNIQUE_ACCOUNT_IDENTIFIER (colonne du fichier Amazon,
# identifiant le compte vendeur d'origine) ne doit pouvoir être rattaché
# qu'à un seul SIREN par scope — sans quoi un utilisateur pourrait importer
# le fichier d'un client sous le SIREN (donc le crédit/abonnement) payé d'un
# autre. Un SIREN peut en revanche posséder plusieurs identifiants (un même
# client peut avoir plusieurs comptes Amazon). Le scope est identique à celui
# du cache VIES (voir vies_engine.resolve_scope_id) : partagé entre les
# collaborateurs d'un même cabinet, isolé par compte pour les domaines grand
# public — un cabinet n'a donc pas à reconfirmer le rattachement à chaque
# nouvel utilisateur de la même structure.


@st.cache_data(ttl=60, show_spinner=False)
def get_siren_links_for_identifiers(scope_id: str, identifiers) -> dict[str, str]:
    """Mis en cache (TTL 60s, même pattern que list_registered_sirens) :
    mesuré en prod à ~400-1240 ms par appel (requête réseau réelle), appelée
    à chaque render de build_billing_gate (donc à chaque rerun Streamlit)
    avec typiquement le même jeu d'identifiants tant qu'aucun nouveau fichier
    n'est importé. Invalidé explicitement (`.clear()`) par
    link_account_identifier ci-dessous : sans ça, un identifiant tout juste
    lié par l'utilisateur réapparaîtrait comme "à confirmer" pendant jusqu'à
    60s au prochain rerun (qui a lieu immédiatement après le clic)."""
    ids = sorted({i for i in identifiers if i})
    if not ids:
        return {}

    def _fn(conn, cur):
        cur.execute(
            """
            SELECT account_identifier, siren FROM tva_account_siren_links
            WHERE scope_id=%s AND account_identifier = ANY(%s)
            """,
            (scope_id, ids),
        )
        return cur.fetchall()

    rows = _run(_fn)
    return {r[0]: r[1] for r in rows}


def link_account_identifier(scope_id: str, account_identifier: str, siren: str) -> None:
    """Crée le lien identifiant Amazon <-> SIREN pour ce scope.

    Ne doit être appelée qu'après confirmation explicite de l'utilisateur
    (voir ui/billing_gate.py) — jamais automatiquement à l'import d'un
    fichier, pour éviter qu'une simple erreur de sélection de SIREN au
    moment de l'upload ne fige un rattachement incorrect. `ON CONFLICT DO
    NOTHING` : un identifiant déjà lié (même à ce même SIREN) n'est jamais
    réécrit silencieusement par cet appel — un changement de rattachement
    nécessite une action explicite distincte (non exposée ici : cas rare,
    à traiter au cas par cas si besoin)."""
    def _fn(conn, cur):
        cur.execute(
            """
            INSERT INTO tva_account_siren_links (scope_id, account_identifier, siren, linked_at)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (scope_id, account_identifier) DO NOTHING
            """,
            (scope_id, account_identifier, siren, time.time()),
        )
        conn.commit()

    _run(_fn)
    get_siren_links_for_identifiers.clear()


def _existing_stripe_customer_id(org_id: str) -> Optional[str]:
    """Lit le stripe_customer_id existant, sans en créer un nouveau (contrairement
    à _get_or_create_stripe_customer) — utilisé pour de simples vérifications en
    lecture (ex. éligibilité aux codes promo) où créer un client Stripe pour un
    simple affichage de grille tarifaire serait un effet de bord indésirable."""
    def _select(conn, cur):
        cur.execute("SELECT stripe_customer_id FROM tva_customers WHERE org_id=%s", (org_id,))
        row = cur.fetchone()
        return row[0] if row else None

    return _run(_select)


def _stripe_customer_has_paid_before(customer_id: str) -> bool:
    """Vérifie côté Stripe (pas en base locale) si ce client a déjà un paiement
    réussi — utilisé pour évaluer la restriction "1ère commande uniquement" des
    Promotion Codes, indépendamment de ce que notre base locale sait déjà."""
    try:
        charges = stripe.Charge.list(customer=customer_id, limit=1)
        for ch in charges.auto_paging_iter():
            if _safe_get(ch, "paid"):
                return True
        return False
    except Exception:
        # En cas d'erreur réseau/API, on ne bloque pas l'affichage — on
        # considère prudemment que l'éligibilité "1ère commande" est inconnue
        # plutôt que de risquer un faux positif.
        return False


@st.cache_data(ttl=600, show_spinner=False)
def list_available_promotions(org_id: Optional[str] = None) -> list[dict]:
    """Liste les codes promotionnels actifs configurés côté Stripe (Dashboard),
    avec leurs conditions d'utilisation, sans jamais les recopier en dur ici.

    Si `org_id` est fourni et qu'un client Stripe existe déjà pour cette
    organisation, chaque code inclut aussi son éligibilité pour CE client
    (vérifiée en direct côté Stripe : historique de paiement pour la
    restriction "1ère commande", stock restant, date d'expiration). Sans
    `org_id` (visiteur non connecté), "eligible" vaut None (inconnu).

    Les codes restreints à un client Stripe précis (`promo.customer` défini)
    et différent du client de `org_id` sont exclus de la liste — ce sont des
    codes privés, pas des offres publiques à afficher.

    Retourne une liste de dicts :
        {
          "code": str, "percent_off": float|None, "amount_off": float|None,
          "currency": str|None, "expires_at": int|None (timestamp Unix),
          "first_time_only": bool, "minimum_amount": float|None,
          "minimum_amount_currency": str|None, "max_redemptions": int|None,
          "stock_remaining": int|None (None = illimité),
          "eligible": bool|None, "ineligible_reasons": list[str],
          "applies_to": dict|None,
        }
    """
    if not _stripe_configured():
        raise RuntimeError("Stripe non configuré (STRIPE_SECRET_KEY manquante).")

    customer_id = _existing_stripe_customer_id(org_id) if org_id else None
    has_paid_before = _stripe_customer_has_paid_before(customer_id) if customer_id else False

    results: list[dict] = []
    try:
        # On étend le coupon (quel que soit son emplacement selon la version de l'API)
        promos = stripe.PromotionCode.list(
            active=True, limit=100,
            expand=["data.coupon", "data.promotion.coupon"]
        )
    except Exception:
        # CORRECTIF 2026-09-01 (audit) : avant, erreur avalée silencieusement,
        # y compris pour des causes autres qu'un simple aléa réseau (clé API
        # invalide/expirée, compte Stripe mal configuré...). Résultat : la
        # grille de codes promo s'affichait juste vide côté UI, sans aucune
        # trace exploitable en prod pour distinguer "pas de promo active" de
        # "l'appel Stripe a échoué". On logue désormais l'échec (le
        # comportement de fallback — liste vide — reste identique).
        logger.warning(
            "list_available_promotions : échec de stripe.PromotionCode.list "
            "(org_id=%s) — liste vide retournée.", org_id, exc_info=True,
        )
        return results

    for promo in promos.auto_paging_iter():
        customer_restriction = _safe_get(promo, "customer")
        if customer_restriction and customer_restriction != customer_id:
            # Code privé réservé à un autre client précis : jamais affiché.
            continue

        # Récupération robuste du coupon
        _promotion_obj = _safe_get(promo, "promotion")
        coupon_ref = _safe_get(_promotion_obj, "coupon") if _promotion_obj else _safe_get(promo, "coupon")
        coupon_id = coupon_ref if isinstance(coupon_ref, str) else _safe_get(coupon_ref, "id")

        try:
            # On récupère l'objet complet pour être sûr d'avoir applies_to et les montants
            coupon = stripe.Coupon.retrieve(coupon_id, expand=["applies_to"]) if coupon_id else coupon_ref
        except Exception:
            # CORRECTIF 2026-09-01 (audit) : même remarque que le except
            # ci-dessus (stripe.PromotionCode.list) — le repli sur coupon_ref
            # (objet partiel, sans applies_to ni montants complets) reste le
            # bon comportement de fallback, mais l'échec doit être visible en
            # prod plutôt que totalement invisible.
            logger.warning(
                "list_available_promotions : échec de stripe.Coupon.retrieve "
                "pour coupon_id=%s — repli sur la référence partielle.", coupon_id, exc_info=True,
            )
            coupon = coupon_ref

        # Conversion en dict pour la stabilité du cache et de l'accès aux champs
        coupon_dict = coupon.to_dict() if hasattr(coupon, "to_dict") else (coupon if isinstance(coupon, dict) else {})

        # Extraction très robuste de applies_to (restrictions produits/prix)
        applies_to_raw = coupon_dict.get("applies_to")
        applies_to_clean = None
        if applies_to_raw:
            if hasattr(applies_to_raw, "to_dict"):
                applies_to_clean = applies_to_raw.to_dict()
            elif isinstance(applies_to_raw, dict):
                applies_to_clean = applies_to_raw
            else:
                applies_to_clean = {
                    "products": getattr(applies_to_raw, "products", []) or [],
                    "prices": getattr(applies_to_raw, "prices", []) or []
                }

        percent_off = coupon_dict.get("percent_off")
        amount_off_cents = coupon_dict.get("amount_off")
        currency = coupon_dict.get("currency")
        coupon_valid = coupon_dict.get("valid", True)
        if not coupon_valid:
            continue

        restrictions = _safe_get(promo, "restrictions", {}) or {}
        first_time_only = bool(_safe_get(restrictions, "first_time_transaction", False))
        min_amount_cents = _safe_get(restrictions, "minimum_amount")
        min_currency = _safe_get(restrictions, "minimum_amount_currency")

        max_redemptions = _safe_get(promo, "max_redemptions")
        times_redeemed = _safe_get(promo, "times_redeemed", 0) or 0
        stock_remaining = (max_redemptions - times_redeemed) if max_redemptions is not None else None

        expires_at = _safe_get(promo, "expires_at")

        # Faits objectifs, vérifiables indépendamment de l'identité du client :
        # stock épuisé et expiration. Toujours évalués, connecté ou non.
        reasons: list[str] = []
        stock_exhausted = stock_remaining is not None and stock_remaining <= 0
        expired = bool(expires_at and expires_at < time.time())
        if stock_exhausted:
            reasons.append("stock de codes épuisé")
        if expired:
            reasons.append("code expiré")

        if org_id:
            # Client connu : on peut trancher précisément, y compris la
            # restriction "1ère commande" (vérifiée côté Stripe plus haut).
            first_time_blocked = first_time_only and has_paid_before
            if first_time_blocked:
                reasons.append("réservé aux nouveaux clients (1ère commande)")
            eligible: Optional[bool] = not (stock_exhausted or expired or first_time_blocked)
        elif stock_exhausted or expired:
            # Visiteur non connecté, mais on sait déjà avec certitude que ce
            # code est inutilisable (fait objectif, indépendant du client).
            eligible = False
        elif first_time_only:
            # Visiteur non connecté et restriction dépendant du client :
            # éligibilité réellement inconnue tant qu'on ne sait pas s'il a
            # déjà commandé.
            eligible = None
        else:
            # Aucune restriction dépendant du client, et aucun blocage objectif.
            eligible = True

        results.append({
            "code": _safe_get(promo, "code"),
            "percent_off": percent_off,
            "amount_off": (amount_off_cents / 100) if amount_off_cents is not None else None,
            "currency": currency,
            "expires_at": expires_at,
            "first_time_only": first_time_only,
            "minimum_amount": (min_amount_cents / 100) if min_amount_cents is not None else None,
            "minimum_amount_currency": min_currency,
            "max_redemptions": max_redemptions,
            "stock_remaining": stock_remaining,
            "eligible": eligible,
            "ineligible_reasons": reasons,
            "applies_to": applies_to_clean,
        })

    return results


@st.cache_data(ttl=600, show_spinner=False)
def get_pricing_grid(org_id: Optional[str] = None) -> dict:
    """Récupère la grille tarifaire réelle depuis l'API Stripe (source de
    vérité — jamais recopiée en dur ici, pour ne jamais diverger de ce qui
    est effectivement configuré dans le Dashboard Stripe).

    Le prix barré affiché pour chaque offre correspond au MEILLEUR code
    promotionnel actif et éligible parmi ceux renvoyés par
    `list_available_promotions(user_id)` — pas un coupon fixe codé en dur.
    "Meilleur" est évalué indépendamment pour chaque prix (le montant final
    le plus bas), car un code à réduction fixe (ex. -5 EUR) et un code en
    pourcentage (ex. -20%) ne sont pas comparables in abstracto, seulement
    une fois appliqués à un montant donné. Un code nécessitant un montant
    minimum non atteint par une offre donnée est ignoré pour cette offre.
    Le code n'est JAMAIS appliqué automatiquement à la session Checkout
    (le client doit toujours le saisir lui-même) — ces champs ne servent
    qu'à l'affichage "prix barré".

    Sans `user_id` (visiteur non connecté), seuls les codes dont
    l'éligibilité ne dépend pas de l'identité du client (`eligible` True ou
    None, cf. list_available_promotions) sont considérés comme candidats —
    les codes objectivement épuisés/expirés restent exclus.

    Retourne un dict :
        {
          "payg": {"amount": float, "currency": "eur", "discounted_amount": float|None,
                   "discount_label": str|None, "discount_code": str|None} | None,
          "business": {"month": {...}, "year": {...}},
          "cabinet": {"month": {"tiers": [...]}, "year": {"tiers": [...]}},
        }
    Les montants sont en unité principale (euros), pas en centimes.
    Lève une exception si Stripe n'est pas configuré — à l'appelant de
    l'attraper et d'afficher un message adapté.
    """
    if not _stripe_configured():
        raise RuntimeError("Stripe non configuré (STRIPE_SECRET_KEY manquante).")

    def _amount(cents: Optional[int]) -> Optional[float]:
        return (cents / 100) if cents is not None else None

    # Candidats : tout code actif dont on ne sait pas AVEC CERTITUDE qu'il
    # est inutilisable (eligible is False exclu ; True et None acceptés).
    try:
        _candidates = [p for p in list_available_promotions(org_id) if p.get("eligible") is not False]
    except Exception as _promo_err:
        # Volontairement PAS de fallback silencieux ici : une grille qui
        # affiche les prix "normaux" sans jamais dire pourquoi la réduction
        # a disparu serait plus trompeuse qu'une erreur explicite.
        raise RuntimeError(f"Erreur lors du calcul des codes promo applicables : {_promo_err}") from _promo_err

    def _best_discount(cents: Optional[int], product_id: Optional[str] = None, price_id: Optional[str] = None) -> tuple[Optional[float], Optional[str], Optional[str]]:
        """Retourne (montant_réduit, libellé, code) pour le meilleur candidat
        applicable à ce montant (en centimes), ce produit et ce prix, ou (None, None, None)
        si aucun candidat n'est applicable."""
        if cents is None:
            return None, None, None
        best_cents: Optional[float] = None
        best_label: Optional[str] = None
        best_code: Optional[str] = None
        for promo in _candidates:
            # Restriction par produit/prix (Stripe Coupon "applies_to")
            applies_to = promo.get("applies_to")
            if applies_to:
                allowed_products = _safe_get(applies_to, "products", []) or []
                allowed_prices = _safe_get(applies_to, "prices", []) or []

                # Si des restrictions existent, on vérifie si l'une d'elles correspond
                has_product_restriction = bool(allowed_products)
                has_price_restriction = bool(allowed_prices)

                if has_product_restriction or has_price_restriction:
                    match_product = product_id in allowed_products if (product_id and allowed_products) else False
                    match_price = price_id in allowed_prices if (price_id and allowed_prices) else False

                    # Si aucune des restrictions n'est satisfaite, on ignore ce coupon
                    if not (match_product or match_price):
                        continue

            _min = promo.get("minimum_amount")
            if _min is not None and (cents / 100) < _min:
                continue  # montant minimum requis non atteint par cette offre
            if promo.get("percent_off") is not None:
                candidate_cents = cents * (1 - promo["percent_off"] / 100.0)
                label = f"-{promo['percent_off']:g}%"
            elif promo.get("amount_off") is not None:
                candidate_cents = max(0, cents - promo["amount_off"] * 100)
                label = f"-{promo['amount_off']:.2f}"
            else:
                continue
            if best_cents is None or candidate_cents < best_cents:
                best_cents = candidate_cents
                best_label = label
                best_code = promo.get("code")
        if best_code is None:
            return None, None, None
        return round(best_cents / 100, 2), best_label, best_code

    grid: dict = {"payg": None, "business": {}, "cabinet": {}}

    _payg_id = _env("STRIPE_PRICE_PAYG_EXPORT")
    if _payg_id:
        p = stripe.Price.retrieve(_payg_id, expand=["product"])
        _cents = _safe_get(p, "unit_amount")
        _product = _safe_get(p, "product")
        # Extraction robuste des IDs (p.product peut être l'ID ou l'objet expanded)
        _product_id = _product if isinstance(_product, str) else _safe_get(_product, "id")
        _price_id = _safe_get(p, "id")
        _disc_amount, _disc_label, _disc_code = _best_discount(_cents, _product_id, _price_id)
        grid["payg"] = {
            "amount": _amount(_cents),
            "currency": _safe_get(p, "currency", "eur"),
            "discounted_amount": _disc_amount,
            "discount_label": _disc_label,
            "discount_code": _disc_code,
            "name": _safe_get(_product, "name") if not isinstance(_product, str) else None,
        }

    _biz_keys = {"month": "STRIPE_PRICE_SUB_BUSINESS_MONTHLY", "year": "STRIPE_PRICE_SUB_BUSINESS_YEARLY"}
    for interval, env_key in _biz_keys.items():
        price_id = _env(env_key)
        if not price_id:
            continue
        p = stripe.Price.retrieve(price_id, expand=["product"])
        _cents = _safe_get(p, "unit_amount")
        _product = _safe_get(p, "product")
        _product_id = _product if isinstance(_product, str) else _safe_get(_product, "id")
        _price_id = _safe_get(p, "id")
        _disc_amount, _disc_label, _disc_code = _best_discount(_cents, _product_id, _price_id)
        grid["business"][interval] = {
            "amount": _amount(_cents),
            "currency": _safe_get(p, "currency", "eur"),
            "discounted_amount": _disc_amount,
            "discount_label": _disc_label,
            "discount_code": _disc_code,
            "name": _safe_get(_product, "name") if not isinstance(_product, str) else None,
        }

    _cab_keys = {"month": "STRIPE_PRICE_SUB_CABINET_MONTHLY", "year": "STRIPE_PRICE_SUB_CABINET_YEARLY"}
    for interval, env_key in _cab_keys.items():
        price_id = _env(env_key)
        if not price_id:
            continue
        p = stripe.Price.retrieve(price_id, expand=["tiers", "product"])
        _product = _safe_get(p, "product")
        _product_id = _product if isinstance(_product, str) else _safe_get(_product, "id")
        _price_id = _safe_get(p, "id")
        tiers_raw = _safe_get(p, "tiers", []) or []
        tiers = []
        for t in tiers_raw:
            _unit_cents = _safe_get(t, "unit_amount")
            _disc_amount, _disc_label, _disc_code = _best_discount(_unit_cents, _product_id, _price_id)
            tiers.append({
                "up_to": _safe_get(t, "up_to"),  # None = infini (dernier palier)
                "unit_amount": _amount(_unit_cents),
                "flat_amount": _amount(_safe_get(t, "flat_amount")),
                "discounted_unit_amount": _disc_amount,
                "discount_label": _disc_label,
                "discount_code": _disc_code,
            })
        grid["cabinet"][interval] = {
            "billing_scheme": _safe_get(p, "billing_scheme"),
            "currency": _safe_get(p, "currency", "eur"),
            "tiers": tiers,
            "name": _safe_get(_product, "name") if not isinstance(_product, str) else None,
        }

    return grid



def _get_or_create_stripe_customer(org_id: str, email: str, acting_user_id: str = "") -> str:
    """Récupère le stripe_customer_id existant pour cette ORGANISATION, ou en
    crée un nouveau. Un seul client Stripe par org_id, partagé par tous ses
    membres — quel que soit le membre qui achète en premier.

    L'appel réseau à `stripe.Customer.create()` est fait EXACTEMENT une fois,
    en dehors de toute logique de retry — contrairement à une version
    précédente qui l'enfermait dans le même bloc retenté par `_run()` en cas
    de connexion Postgres fermée par le pooler (cf. tva_intracom/auth.py) :
    un retry aurait alors pu créer un second client Stripe pour la même
    organisation. Les deux accès DB de part et d'autre restent, eux, sûrs à
    retenter (SELECT, puis INSERT idempotent via ON CONFLICT DO NOTHING)."""
    def _select(conn, cur):
        cur.execute("SELECT stripe_customer_id FROM tva_customers WHERE org_id=%s", (org_id,))
        row = cur.fetchone()
        return row[0] if row else None

    existing = _run(_select)
    if existing:
        return existing

    if not _stripe_configured():
        raise RuntimeError("Stripe non configuré (STRIPE_SECRET_KEY manquante).")

    customer = stripe.Customer.create(email=email, metadata={"org_id": org_id})

    def _insert(conn, cur):
        cur.execute(
            """
            INSERT INTO tva_customers (org_id, user_id, stripe_customer_id)
            VALUES (%s, %s, %s)
            ON CONFLICT (org_id) DO NOTHING
            """,
            (org_id, acting_user_id or org_id, customer.id),
        )
        conn.commit()
        # Relit la valeur réellement stockée : en cas de course avec un autre
        # appel concurrent déjà passé, on renvoie le customer_id existant en
        # base plutôt que celui qu'on vient de créer (qui serait alors orphelin
        # côté Stripe — pas grave en soi, mais autant renvoyer la valeur
        # canonique effectivement utilisée par l'application).
        cur.execute("SELECT stripe_customer_id FROM tva_customers WHERE org_id=%s", (org_id,))
        return cur.fetchone()[0]

    return _run(_insert)


def create_payg_checkout_session(
        org_id: str, acting_user_id: str, email: str, period_label: str, success_url: str, cancel_url: str,
        siren: str = "",
) -> str:
    """Crée une session Stripe Checkout pour l'achat d'un crédit d'export
    PAYG (à l'unité, hors abonnement).

    siren (2026-09-04) : SIREN sélectionné au moment de l'achat, propagé en
    metadata Stripe pour que le webhook scelle le crédit octroyé à ce SIREN
    précis (voir _fulfill_checkout_session / grant_export_credit) — un
    paiement à l'unité ne doit débloquer qu'un SIREN, pas toute
    l'organisation. Laissé à '' si aucun SIREN n'est encore sélectionné
    (compte gratuit sans SIREN saisi) : le crédit sera alors valable pour
    tout SIREN de l'org, comme les crédits legacy — voir has_export_credit.

    RÔLES (2026-08-25) : défense en profondeur — `_require_write_access`
    lève déjà `PermissionError` si `acting_user_id` correspond à un compte
    lecteur. Aujourd'hui, en pratique, les deux seuls chemins UI vers cette
    fonction sont déjà bloqués côté interface (bouton PAYG désactivé pour
    un lecteur dans `ui/billing_gate.py`, bloc "Abonnements & forfaits"
    masqué dans `ui/sidebar.py`) — cet appel protège contre un futur appelant
    qui oublierait cette vérification, cohérent avec le même pattern déjà
    en place sur `register_siren`/`request_siren_removal`.
    """
    _require_write_access(acting_user_id)
    if not _stripe_configured():
        raise RuntimeError("Stripe non configuré (STRIPE_SECRET_KEY manquante).")
    if not _env("STRIPE_PRICE_PAYG_EXPORT"):
        raise RuntimeError("STRIPE_PRICE_PAYG_EXPORT non défini.")

    customer_id = _get_or_create_stripe_customer(org_id, email, acting_user_id)
    session = stripe.checkout.Session.create(
        mode="payment",
        customer=customer_id,
        line_items=[{"price": _env("STRIPE_PRICE_PAYG_EXPORT"), "quantity": 1}],
        success_url=success_url,
        cancel_url=cancel_url,
        allow_promotion_codes=True,
        metadata={
            "org_id": org_id, "user_id": acting_user_id, "period_label": period_label,
            "siren": siren or "", "kind": "payg_export",
        },
    )
    return session.url


def create_subscription_checkout_session(
        org_id: str,
        acting_user_id: str,
        email: str,
        plan: str,
        interval: str,
        success_url: str,
        cancel_url: str,
        quantity: int = 1,
) -> str:
    """Crée une session Stripe Checkout pour un abonnement.

    plan     : "business" (Pro) ou "cabinet".
    interval : "month" ou "year".
    quantity : nombre de SIREN pour le forfait Cabinet (tarif dégressif géré
               par un Price Stripe de type "tiered" — le code se contente de
               transmettre la quantité choisie). Ignorée (forcée à 1) pour le
               forfait Pro, qui est mono-SIREN par définition.

    RÔLES (2026-08-25) : défense en profondeur — voir docstring de
    create_payg_checkout_session ci-dessus, même justification/pattern.
    """
    _require_write_access(acting_user_id)
    if not _stripe_configured():
        raise RuntimeError("Stripe non configuré (STRIPE_SECRET_KEY manquante).")
    if plan not in ("business", "cabinet"):
        raise RuntimeError(f"Plan inconnu : {plan}")
    if interval not in ("month", "year"):
        raise RuntimeError(f"Intervalle de facturation inconnu : {interval}")

    _sub_price_env_keys = {
        ("business", "month"): "STRIPE_PRICE_SUB_BUSINESS_MONTHLY",
        ("business", "year"): "STRIPE_PRICE_SUB_BUSINESS_YEARLY",
        ("cabinet", "month"): "STRIPE_PRICE_SUB_CABINET_MONTHLY",
        ("cabinet", "year"): "STRIPE_PRICE_SUB_CABINET_YEARLY",
    }
    env_key = _sub_price_env_keys[(plan, interval)]
    price_id = _env(env_key)
    if not price_id:
        raise RuntimeError(
            f"Aucun price_id Stripe configuré pour ({plan}, {interval}) — "
            f"vérifiez la variable {env_key} (secrets Streamlit ou variable d'environnement)."
        )

    effective_quantity = quantity if plan == "cabinet" else 1
    if plan == "cabinet" and effective_quantity < _CABINET_MIN_QUANTITY:
        effective_quantity = _CABINET_MIN_QUANTITY
    if effective_quantity < 1:
        effective_quantity = 1

    customer_id = _get_or_create_stripe_customer(org_id, email, acting_user_id)
    session = stripe.checkout.Session.create(
        mode="subscription",
        customer=customer_id,
        line_items=[{"price": price_id, "quantity": effective_quantity}],
        success_url=success_url,
        cancel_url=cancel_url,
        allow_promotion_codes=True,
        subscription_data={
            # Propagée sur l'objet Subscription (et pas seulement sur la
            # Session) pour que le webhook `customer.subscription.*` puisse
            # relire le plan/intervalle sans dépendre de la Session d'origine.
            "metadata": {"org_id": org_id, "user_id": acting_user_id, "plan": plan, "interval": interval},
        },
        metadata={"org_id": org_id, "user_id": acting_user_id, "plan": plan, "interval": interval},
    )
    return session.url


def create_billing_portal_session(org_id: str, return_url: str, acting_user_id: str | None = None) -> str:
    """Crée une session du portail Stripe (gestion self-service de
    l'abonnement : moyen de paiement, factures, résiliation).

    RÔLES (2026-08-25) : `acting_user_id` optionnel (défaut None,
    rétrocompatible) — si fourni, défense en profondeur identique à
    create_payg_checkout_session ci-dessus. Le seul appelant actuel
    (ui/sidebar.py, bloc "Abonnements & forfaits") le fournit toujours et
    est de toute façon déjà masqué pour un lecteur côté UI.
    """
    if acting_user_id is not None:
        _require_write_access(acting_user_id)

    def _fn(conn, cur):
        cur.execute("SELECT stripe_customer_id FROM tva_customers WHERE org_id=%s", (org_id,))
        return cur.fetchone()

    row = _run(_fn)

    if not row:
        raise RuntimeError("Aucun client Stripe pour cette organisation.")
    if not _stripe_configured():
        raise RuntimeError("Stripe non configuré (STRIPE_SECRET_KEY manquante).")

    portal = stripe.billing_portal.Session.create(customer=row[0], return_url=return_url)
    return portal.url


def _org_id_for_stripe_customer(stripe_customer_id: str) -> Optional[str]:
    def _fn(conn, cur):
        cur.execute("SELECT org_id FROM tva_customers WHERE stripe_customer_id=%s", (stripe_customer_id,))
        row = cur.fetchone()
        return row[0] if row else None

    return _run(_fn)


def _extract_subscription_item_details(data) -> tuple[int, Optional[str], Optional[float]]:
    """Extrait (quantity, interval, current_period_end) depuis l'objet
    Subscription Stripe.

    `data` est soit `event["data"]["object"]` pour un event
    customer.subscription.created/updated, soit le résultat de
    stripe.Subscription.retrieve() — un objet Subscription complet, avec
    `items.data[0]` contenant la ligne (price + quantity) souscrite.

    Sur les versions récentes de l'API Stripe, `current_period_end` n'est
    plus porté par l'objet Subscription lui-même mais par chaque
    SubscriptionItem (`items.data[0].current_period_end`) — on essaie donc
    l'item en premier, avec repli sur l'ancien emplacement pour compatibilité.
    """
    items = _safe_get(data, "items", {}) or {}
    items_data = _safe_get(items, "data", []) or []
    if not items_data:
        return 1, None, _safe_get(data, "current_period_end")
    first_item = items_data[0]
    quantity = _safe_get(first_item, "quantity", 1) or 1
    price_obj = _safe_get(first_item, "price", {}) or {}
    recurring = _safe_get(price_obj, "recurring", {}) or {}
    interval = _safe_get(recurring, "interval")
    period_end = _safe_get(first_item, "current_period_end")
    if period_end is None:
        period_end = _safe_get(data, "current_period_end")
    return int(quantity), interval, period_end


def _upsert_subscription(
        org_id: str,
        stripe_subscription_id: str,
        status: str,
        plan: str,
        current_period_end: float,
        billing_interval: Optional[str],
        siren_quantity: int,
        acting_user_id: str = "",
) -> None:
    """acting_user_id : compte associé à l'événement Stripe traité (le
    souscripteur d'origine, ou le compte retrouvé via le fallback e-mail) —
    audit uniquement (colonne `user_id`), `org_id` reste la clé partagée."""
    def _fn(conn, cur):
        cur.execute(
            """
            INSERT INTO tva_subscriptions
                (org_id, user_id, stripe_subscription_id, status, plan, current_period_end,
                 updated_at, billing_interval, siren_quantity)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (org_id) DO UPDATE SET
                user_id = EXCLUDED.user_id,
                stripe_subscription_id = EXCLUDED.stripe_subscription_id,
                status = EXCLUDED.status,
                plan = EXCLUDED.plan,
                current_period_end = EXCLUDED.current_period_end,
                updated_at = EXCLUDED.updated_at,
                billing_interval = EXCLUDED.billing_interval,
                siren_quantity = EXCLUDED.siren_quantity
            """,
            (org_id, acting_user_id or org_id, stripe_subscription_id, status, plan, current_period_end,
             time.time(), billing_interval, siren_quantity),
        )
        conn.commit()

    _run(_fn)


def _get_or_create_user_id_by_email(email: str) -> tuple[str, str]:
    """Retrouve (user_id, org_id) pour un email donné, ou crée le compte.
    Utilisé par le webhook pour les paiements venant du site externe
    (Pricing Table), quand le paiement arrive avant toute connexion à
    l'app (donc avant qu'auth.get_or_create_user n'ait jamais tourné pour
    ce compte).

    ORG_ID (2026-08-24) : BUGFIX — cette fonction insérait auparavant une
    ligne tva_users SANS org_id (org_id restait NULL jusqu'à la première
    connexion réelle), ce qui aurait empêché tout rattachement immédiat de
    l'abonnement à une organisation. org_id est désormais calculé ici via
    `auth.resolve_org_id` (même logique que la 1ère connexion), pour que
    l'abonnement payé se retrouve immédiatement associé à la bonne
    organisation, y compris pour un compte qui ne s'est encore jamais
    connecté à l'app."""
    email = email.strip().lower()

    def _select(conn, cur):
        cur.execute("SELECT id, org_id FROM tva_users WHERE email=%s", (email,))
        return cur.fetchone()

    existing = _run(_select)
    if existing:
        return existing[0], existing[1]

    from .auth import resolve_org_id
    org_id = resolve_org_id(email)
    user_id = secrets.token_hex(12)

    def _insert(conn, cur):
        cur.execute(
            """
            INSERT INTO tva_users (id, email, created_at, org_id, role)
            VALUES (%s, %s, %s, %s, 'admin')
            ON CONFLICT (email) DO NOTHING
            """,
            (user_id, email, time.time(), org_id),
        )
        conn.commit()
        # Relit la ligne réellement en base (en cas de création concurrente)
        cur.execute("SELECT id, org_id FROM tva_users WHERE email=%s", (email,))
        return cur.fetchone()

    return _run(_insert)


def _link_stripe_customer(org_id: str, stripe_customer_id: str, acting_user_id: str = "") -> None:
    """Lie une organisation à un client Stripe en base."""
    def _fn(conn, cur):
        cur.execute(
            """
            INSERT INTO tva_customers (org_id, user_id, stripe_customer_id)
            VALUES (%s, %s, %s)
            ON CONFLICT (org_id) DO UPDATE SET stripe_customer_id = EXCLUDED.stripe_customer_id
            """,
            (org_id, acting_user_id or org_id, stripe_customer_id),
        )
        conn.commit()

    _run(_fn)


def _plan_from_price_id(price_id: Optional[str]) -> Optional[str]:
    """Résout un plan ("business"/"cabinet") à partir d'un price_id Stripe,
    en le comparant aux 4 price_id configurés en variables d'environnement.
    Retourne None si le price_id ne correspond à aucun plan connu (prix
    legacy retiré de la config, ou line item hors abonnement standard)."""
    if not price_id:
        return None
    if price_id in (_env("STRIPE_PRICE_SUB_BUSINESS_MONTHLY"), _env("STRIPE_PRICE_SUB_BUSINESS_YEARLY")):
        return "business"
    if price_id in (_env("STRIPE_PRICE_SUB_CABINET_MONTHLY"), _env("STRIPE_PRICE_SUB_CABINET_YEARLY")):
        return "cabinet"
    return None


def _first_item_price_id(subscription_like) -> Optional[str]:
    """Extrait le price_id du premier SubscriptionItem d'un objet Subscription
    (ou de l'objet event["data"]["object"], même structure `items.data[]`)."""
    items = _safe_get(subscription_like, "items", {}) or {}
    items_data = _safe_get(items, "data", []) or []
    if not items_data:
        return None
    return _safe_get(_safe_get(items_data[0], "price", {}), "id")


def _lock_org_after_payment(org_id: str, acting_user_id: str | None, *, context: str) -> None:
    """Verrouille l'organisation du payeur après un paiement confirmé —
    factorisé pour être appelé identiquement après un abonnement OU un achat
    PAYG (statut "Achat", 2026-09-05) : décision produit explicite, un achat
    unique doit désormais déclencher le même verrouillage
    organisation/passage admin qu'un abonnement, pas seulement les
    abonnements. Voir auth.lock_org_for_user (idempotent, sans effet sur une
    org déjà verrouillée ou une org solo). `context` sert uniquement au
    message de log (ex. "abonnement", "achat PAYG")."""
    if acting_user_id:
        try:
            from .auth import lock_org_for_user
            lock_org_for_user(acting_user_id)
        except Exception:
            logger.warning(
                "lock_org_for_user a échoué pour user_id=%s (%s quand même activé)",
                acting_user_id, context, exc_info=True,
            )
    else:
        logger.warning(
            "Impossible de verrouiller l'organisation %s après %s : acting_user_id inconnu "
            "(ni metadata.user_id ni fallback e-mail n'ont résolu de compte).", org_id, context,
        )


def _fulfill_checkout_session(data: dict) -> None:
    """Débloque l'accès (crédit PAYG ou abonnement) pour une session Checkout
    dont le paiement est confirmé — appelée uniquement quand payment_status
    vaut "paid" (carte, ou virement/prélèvement une fois les fonds arrivés).

    ORG_ID (2026-08-24) : org_id (partagé) est la clé d'écriture ;
    acting_user_id (le souscripteur) reste utilisé pour lock_org_for_user
    et comme colonne d'audit."""
    metadata = _safe_get(data, "metadata", {}) or {}
    org_id = _safe_get(metadata, "org_id")
    acting_user_id = _safe_get(metadata, "user_id")

    # FALLBACK : si org_id absent (session créée avant ce déploiement, ou
    # paiement via Pricing Table sur le site externe), on identifie
    # l'organisation par l'email Stripe du client.
    if not org_id:
        customer_details = _safe_get(data, "customer_details", {}) or {}
        email = _safe_get(customer_details, "email")
        if email:
            acting_user_id, org_id = _get_or_create_user_id_by_email(email)
            # On lie le customer_id pour les futurs webhooks subscription.*
            customer_id = _safe_get(data, "customer")
            if customer_id:
                _link_stripe_customer(org_id, customer_id, acting_user_id)

    if not org_id:
        return

    if _safe_get(metadata, "kind") == "payg_export":
        grant_export_credit(
            org_id,
            _safe_get(metadata, "period_label", ""),
            _safe_get(data, "payment_intent", ""),
            acting_user_id=acting_user_id or "",
            siren=_safe_get(metadata, "siren", ""),
        )
        # Statut "Achat" (2026-09-05) : un paiement PAYG déclenche désormais
        # le même verrouillage organisation + passage admin qu'un abonnement
        # (voir _lock_org_after_payment) — auparavant réservé à la branche
        # subscription ci-dessous, ce qui laissait un achat unique sans
        # aucun effet sur les rôles de l'organisation.
        _lock_org_after_payment(org_id, acting_user_id, context="achat PAYG")

    elif _safe_get(data, "mode") == "subscription":
        subscription_id = _safe_get(data, "subscription")
        plan = _safe_get(metadata, "plan", "unknown")
        if not subscription_id:
            return
        subscription = stripe.Subscription.retrieve(subscription_id)

        # INFER PLAN : si le plan est inconnu (Pricing Table), on le devine via le price_id
        if plan == "unknown":
            _inferred = _plan_from_price_id(_first_item_price_id(subscription))
            if _inferred:
                plan = _inferred

        quantity, interval, period_end = _extract_subscription_item_details(subscription)
        if period_end is None:
            raise RuntimeError(
                f"current_period_end introuvable (ni sur l'item, ni sur la Subscription "
                f"{subscription_id}) — vérifier un éventuel changement de schéma côté API Stripe."
            )
        _upsert_subscription(
            org_id=org_id,
            stripe_subscription_id=subscription_id,
            status=_safe_get(subscription, "status"),
            plan=plan,
            current_period_end=float(period_end),
            billing_interval=interval,
            siren_quantity=quantity,
            acting_user_id=acting_user_id or "",
        )
        # Rôles & organisation (2026-08-23) : le 1er abonnement payant
        # verrouille l'organisation du souscripteur — voir
        # _lock_org_after_payment (idempotent, sans effet sur un
        # renouvellement, un changement de plan, ou une org déjà verrouillée
        # par un précédent achat PAYG, cf. branche payg_export ci-dessus).
        _lock_org_after_payment(org_id, acting_user_id, context="abonnement")


def _set_scheduled_change(org_id: str, plan: str, interval: Optional[str], change_at: float) -> None:
    """Enregistre un changement de plan programmé (downgrade différé) pour
    affichage utilisateur. UPDATE seul (pas d'INSERT) : n'a de sens que si
    une ligne d'abonnement existe déjà pour cette organisation — un schedule
    Stripe est toujours associé à une Subscription existante."""
    def _fn(conn, cur):
        cur.execute(
            """
            UPDATE tva_subscriptions
            SET scheduled_plan=%s, scheduled_billing_interval=%s,
                scheduled_change_at=%s, updated_at=%s
            WHERE org_id=%s
            """,
            (plan, interval, change_at, time.time(), org_id),
        )
        conn.commit()

    _run(_fn)


def _clear_scheduled_change(org_id: str) -> None:
    """Efface un changement de plan programmé — appelé quand la planification
    s'achève (le changement est devenu effectif, `plan` a déjà été mis à jour
    par le customer.subscription.updated correspondant) ou est annulée."""
    def _fn(conn, cur):
        cur.execute(
            """
            UPDATE tva_subscriptions
            SET scheduled_plan=NULL, scheduled_billing_interval=NULL,
                scheduled_change_at=NULL, updated_at=%s
            WHERE org_id=%s
            """,
            (time.time(), org_id),
        )
        conn.commit()

    _run(_fn)


def _extract_scheduled_change(schedule_data: dict) -> Optional[tuple[str, Optional[str], float]]:
    """À partir de l'objet Subscription Schedule Stripe (event["data"]["object"]
    d'un event subscription_schedule.created/updated), détermine le
    changement de plan programmé à venir : (plan, interval, change_at).

    Repère la phase "suivante" en cherchant, dans `phases`, celle dont
    `start_date` correspond exactement à `current_phase.end_date` — Stripe
    garantit des phases contiguës (fin de l'une = début de la suivante).

    Retourne None si : le schedule n'a pas encore démarré (pas de
    current_phase), aucune phase suivante n'est trouvée (dernière phase du
    schedule), ou le price_id de cette phase ne correspond à aucun plan
    connu (_plan_from_price_id) — dans ce cas on ne veut pas afficher une
    info erronée/vide à l'utilisateur plutôt que de deviner.
    """
    current_phase = _safe_get(schedule_data, "current_phase") or {}
    current_end = _safe_get(current_phase, "end_date")
    if current_end is None:
        return None

    phases = _safe_get(schedule_data, "phases", []) or []
    next_phase = None
    for _phase in phases:
        if _safe_get(_phase, "start_date") == current_end:
            next_phase = _phase
            break
    if next_phase is None:
        return None

    items = _safe_get(next_phase, "items", []) or []
    if not items:
        return None
    first_item = items[0]
    price = _safe_get(first_item, "price")
    # `price` est soit un id (string) soit un objet Price étendu, selon la
    # configuration du webhook Stripe — on gère les deux.
    price_id = _safe_get(price, "id") if isinstance(price, dict) else price
    plan = _plan_from_price_id(price_id)
    if plan is None:
        return None

    interval = None
    if isinstance(price, dict):
        interval = _safe_get(_safe_get(price, "recurring", {}) or {}, "interval")

    return plan, interval, float(current_end)


def handle_stripe_webhook_event(payload: bytes, sig_header: str) -> None:
    if not _stripe_configured():
        raise RuntimeError("Stripe non configuré (STRIPE_SECRET_KEY manquante).")
    webhook_secret = _env("STRIPE_WEBHOOK_SECRET")
    if not webhook_secret:
        raise RuntimeError("STRIPE_WEBHOOK_SECRET non définie.")

    event = stripe.Webhook.construct_event(payload, sig_header, webhook_secret)
    etype = event["type"]
    data = event["data"]["object"]

    if etype == "checkout.session.completed":
        # Les méthodes de paiement différées (virement SEPA, prélèvement)
        # déclenchent bien "checkout.session.completed", mais avec
        # payment_status="unpaid" tant que les fonds ne sont pas arrivés
        # (jusqu'à ~6 jours pour un virement). On ne débloque l'accès ici
        # que si le paiement est déjà confirmé (carte, ou différé déjà réglé
        # au moment de l'événement) ; sinon on attend
        # "checkout.session.async_payment_succeeded" plus bas.
        if _safe_get(data, "payment_status") == "paid":
            _fulfill_checkout_session(data)
        # payment_status == "unpaid" : rien à faire maintenant, on attend
        # la confirmation asynchrone (ou l'échec) ci-dessous.

    elif etype == "checkout.session.async_payment_succeeded":
        # Confirmation tardive d'un virement/prélèvement : les fonds sont
        # arrivés, on débloque maintenant l'accès (même logique que pour
        # un paiement carte confirmé immédiatement).
        _fulfill_checkout_session(data)

    elif etype == "checkout.session.async_payment_failed":
        # Le virement/prélèvement a échoué ou a expiré : rien à débloquer.
        # On ne lève pas d'exception (ce n'est pas une erreur de traitement),
        # mais un log serveur permet de repérer les paiements différés qui
        # n'aboutissent pas.
        metadata = _safe_get(data, "metadata", {}) or {}
        logger.info(
            "[stripe_webhook] Paiement différé échoué/expiré — user_id=%s session=%s",
            _safe_get(metadata, "user_id"), _safe_get(data, "id"),
        )

    elif etype in ("customer.subscription.created", "customer.subscription.updated"):
        customer_id = _safe_get(data, "customer")
        org_id = _org_id_for_stripe_customer(customer_id)
        acting_user_id = ""

        # FALLBACK : si l'id client Stripe n'est pas encore lié (ex: abonnement direct),
        # on récupère l'email du client pour trouver l'organisation.
        if not org_id:
            try:
                # On évite de bloquer tout le traitement si un retrieve échoue (réseau)
                customer = stripe.Customer.retrieve(customer_id)
                email = _safe_get(customer, "email")
                if email:
                    acting_user_id, org_id = _get_or_create_user_id_by_email(email)
                    _link_stripe_customer(org_id, customer_id, acting_user_id)
            except Exception:
                # Avant : erreur avalée silencieusement, y compris pour des
                # causes autres qu'un simple aléa réseau (client Stripe
                # supprimé, clé API invalide/expirée...). Résultat : un
                # abonnement qui ne se lie à aucun user_id, sans aucune
                # trace exploitable en prod. On logue désormais l'échec
                # (sans lever, le comportement de fallback reste identique).
                logger.warning(
                    "[stripe_webhook] Échec du fallback customer.retrieve pour "
                    "customer_id=%s (event=%s) — abonnement non lié.",
                    customer_id, etype, exc_info=True,
                )

        if not org_id:
            return
        # BUGFIX (2026-08-16) : `data["metadata"]["plan"]` est la metadata de
        # la Subscription, posée UNE FOIS au Checkout initial — un changement
        # de plan fait depuis le Portail client Stripe (upgrade/downgrade)
        # modifie le price_id du SubscriptionItem mais NE MET JAMAIS À JOUR
        # cette metadata. Résultat observé : passer de "business" (Pro) à
        # "cabinet" via le portail met bien à jour la quantité (lue depuis
        # l'item live) mais gardait l'ancien plan "business" en base. On
        # dérive donc le plan en priorité depuis le price_id réellement actif
        # sur l'abonnement (source de vérité), avec repli sur la metadata
        # UNIQUEMENT si ce price_id ne correspond à aucun plan connu (ex.
        # price legacy retiré de la config) — pour ne pas régresser le cas où
        # l'inférence échouerait pour une raison imprévue.
        _metadata_plan = _safe_get(_safe_get(data, "metadata") or {}, "plan", "unknown")
        plan = _plan_from_price_id(_first_item_price_id(data)) or _metadata_plan
        quantity, interval, period_end = _extract_subscription_item_details(data)
        if period_end is None:
            raise RuntimeError(
                f"current_period_end introuvable (ni sur l'item, ni sur la Subscription "
                f"{_safe_get(data, 'id')}) — vérifier un éventuel changement de schéma côté API Stripe."
            )
        _upsert_subscription(
            org_id=org_id,
            stripe_subscription_id=_safe_get(data, "id"),
            status=_safe_get(data, "status"),
            plan=plan,
            current_period_end=float(period_end),
            billing_interval=interval,
            siren_quantity=quantity,
            acting_user_id=acting_user_id,
        )

    elif etype in ("subscription_schedule.created", "subscription_schedule.updated"):
        # Downgrade différé (2026-08-16) : ces events signalent qu'une
        # planification de changement de plan existe ou a été mise à jour.
        # NOTE : le changement de plan EFFECTIF (le vrai price_id actif sur
        # l'abonnement) est déjà correctement pris en charge par la branche
        # customer.subscription.updated ci-dessus, qui dérive `plan` depuis
        # le price_id réellement actif — aucune modification nécessaire là.
        # Ici, on se contente d'extraire l'info du changement À VENIR pour
        # affichage utilisateur (cf. sidebar.py) ; ça ne touche jamais au
        # plan actif en base.
        customer_id = _safe_get(data, "customer")
        org_id = _org_id_for_stripe_customer(customer_id)
        if not org_id:
            return
        _pending = _extract_scheduled_change(data)
        if _pending:
            _plan, _interval, _change_at = _pending
            _set_scheduled_change(org_id, _plan, _interval, _change_at)
        else:
            # Pas de phase suivante distincte trouvée (schedule fraîchement
            # créé sans changement identifiable, ou déjà sur sa dernière
            # phase) : on n'affiche rien plutôt que d'afficher une info
            # potentiellement obsolète/erronée.
            _clear_scheduled_change(org_id)

    elif etype in ("subscription_schedule.released", "subscription_schedule.completed",
                    "subscription_schedule.canceled"):
        # La planification s'achève (le changement vient de devenir effectif
        # — customer.subscription.updated a normalement déjà mis à jour
        # `plan`/`billing_interval` avec la nouvelle valeur) ou est annulée
        # avant terme (le downgrade n'aura pas lieu). Dans les deux cas,
        # l'info "changement à venir" affichée à l'utilisateur n'a plus lieu
        # d'être : on l'efface.
        customer_id = _safe_get(data, "customer")
        org_id = _org_id_for_stripe_customer(customer_id)
        if not org_id:
            return
        _clear_scheduled_change(org_id)

    elif etype == "customer.subscription.deleted":
        customer_id = _safe_get(data, "customer")
        org_id = _org_id_for_stripe_customer(customer_id)
        if not org_id:
            return

        def _fn(conn, cur):
            cur.execute(
                "UPDATE tva_subscriptions SET status='canceled', updated_at=%s WHERE org_id=%s",
                (time.time(), org_id),
            )
            conn.commit()

        _run(_fn)