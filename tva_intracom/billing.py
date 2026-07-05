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

import os
import time
from dataclasses import dataclass
from typing import Optional

import psycopg2
import psycopg2.pool

try:
    import stripe  # type: ignore
except ImportError:
    stripe = None

PRICE_PAYG_EXPORT = os.environ.get("STRIPE_PRICE_PAYG_EXPORT", "")

# Abonnements : 1 price_id par (plan, intervalle).
PRICE_SUB_BUSINESS_MONTHLY = os.environ.get("STRIPE_PRICE_SUB_BUSINESS_MONTHLY", "")
PRICE_SUB_BUSINESS_YEARLY = os.environ.get("STRIPE_PRICE_SUB_BUSINESS_YEARLY", "")
PRICE_SUB_CABINET_MONTHLY = os.environ.get("STRIPE_PRICE_SUB_CABINET_MONTHLY", "")
PRICE_SUB_CABINET_YEARLY = os.environ.get("STRIPE_PRICE_SUB_CABINET_YEARLY", "")

_SUB_PRICE_IDS = {
    ("business", "month"): PRICE_SUB_BUSINESS_MONTHLY,
    ("business", "year"): PRICE_SUB_BUSINESS_YEARLY,
    ("cabinet", "month"): PRICE_SUB_CABINET_MONTHLY,
    ("cabinet", "year"): PRICE_SUB_CABINET_YEARLY,
}

# Quota de SIREN distincts pour le plan "business" (Pro). Le quota du plan
# "cabinet" est dynamique : il vaut la quantité Stripe achetée
# (tva_subscriptions.siren_quantity).
_BUSINESS_SIREN_QUOTA = 1

_pool: Optional[psycopg2.pool.SimpleConnectionPool] = None


def _safe_get(obj, key, default=None):
    """Accès sécurisé à une clé, compatible dict classique ET objets Stripe
    (stripe.stripe_object.StripeObject des versions récentes du SDK, qui ne
    supportent pas .get() comme un dict — provoque AttributeError: get)."""
    try:
        return obj[key]
    except (KeyError, TypeError, IndexError):
        return default


def _stripe_configured() -> bool:
    key = os.environ.get("STRIPE_SECRET_KEY")
    if not key or stripe is None:
        return False
    stripe.api_key = key
    return True


def _get_pool() -> psycopg2.pool.SimpleConnectionPool:
    global _pool
    if _pool is None:
        dsn = os.environ.get("SUPABASE_DB_URL")
        if not dsn:
            raise RuntimeError(
                "SUPABASE_DB_URL non définie — impossible de se connecter à la base."
            )
        _pool = psycopg2.pool.SimpleConnectionPool(1, 5, dsn)
        _init_schema()
    return _pool


def _init_schema() -> None:
    pool = _get_pool()
    conn = pool.getconn()
    try:
        with conn, conn.cursor() as cur:
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
            conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        pool.putconn(conn)


@dataclass
class SubscriptionStatus:
    active: bool
    plan: Optional[str] = None
    current_period_end: Optional[float] = None
    billing_interval: Optional[str] = None
    siren_quantity: Optional[int] = None


def get_subscription_status(user_id: str) -> SubscriptionStatus:
    pool = _get_pool()
    conn = pool.getconn()
    try:
        with conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT status, plan, current_period_end, billing_interval, siren_quantity
                FROM tva_subscriptions WHERE user_id=%s
                """,
                (user_id,),
            )
            row = cur.fetchone()
    finally:
        pool.putconn(conn)

    if not row:
        return SubscriptionStatus(active=False)

    status, plan, period_end, billing_interval, siren_quantity = row
    active = status in ("active", "trialing") and period_end > time.time()
    return SubscriptionStatus(
        active=active,
        plan=plan,
        current_period_end=period_end,
        billing_interval=billing_interval,
        siren_quantity=siren_quantity,
    )


def has_active_subscription_direct(user_id: str) -> bool:
    return get_subscription_status(user_id).active


def has_export_credit(user_id: str, period_label: str) -> bool:
    if has_active_subscription_direct(user_id):
        return True
    pool = _get_pool()
    conn = pool.getconn()
    try:
        with conn, conn.cursor() as cur:
            cur.execute(
                "SELECT 1 FROM tva_export_credits WHERE user_id=%s AND period_label=%s",
                (user_id, period_label),
            )
            return cur.fetchone() is not None
    finally:
        pool.putconn(conn)


def grant_export_credit(user_id: str, period_label: str, payment_intent_id: str = "") -> None:
    pool = _get_pool()
    conn = pool.getconn()
    try:
        with conn, conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO tva_export_credits (user_id, period_label, purchased_at, stripe_payment_intent_id)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (user_id, period_label)
                DO UPDATE SET purchased_at = EXCLUDED.purchased_at,
                              stripe_payment_intent_id = EXCLUDED.stripe_payment_intent_id
                """,
                (user_id, period_label, time.time(), payment_intent_id),
            )
            conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        pool.putconn(conn)


# =============================================================================
# QUOTAS SIREN
# =============================================================================
# Le SIREN identifie l'entreprise cliente (9 chiffres). Chaque compte peut en
# enregistrer un nombre limité selon son forfait :
#   - Sans abonnement actif (PAYG) : pas de limite technique — le paiement se
#     fait par période, indépendamment du nombre de SIREN utilisés.
#   - Pro ("business")  : 1 SIREN maximum.
#   - Cabinet ("cabinet"): jusqu'à `siren_quantity` (quantité Stripe achetée).


def list_registered_sirens(user_id: str) -> list[dict]:
    pool = _get_pool()
    conn = pool.getconn()
    try:
        with conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT siren, company_name, tva_number, first_used_at
                FROM tva_siren_registrations
                WHERE user_id=%s
                ORDER BY first_used_at ASC
                """,
                (user_id,),
            )
            rows = cur.fetchall()
    finally:
        pool.putconn(conn)
    return [
        {"siren": r[0], "company_name": r[1], "tva_number": r[2], "first_used_at": r[3]}
        for r in rows
    ]


def get_siren_quota(user_id: str) -> Optional[int]:
    """Retourne le quota de SIREN distincts pour ce compte, ou None si aucune
    limite technique ne s'applique (pas d'abonnement actif → PAYG)."""
    sub = get_subscription_status(user_id)
    if not sub.active:
        return None
    if sub.plan == "business":
        return _BUSINESS_SIREN_QUOTA
    if sub.plan == "cabinet":
        return sub.siren_quantity or 1
    return None


def can_register_new_siren(user_id: str) -> tuple[bool, str]:
    """Vérifie si le compte peut enregistrer un SIREN supplémentaire (celui-ci
    n'étant pas déjà dans sa liste). Ne s'applique pas à un SIREN déjà
    enregistré (mise à jour du nom/TVA toujours autorisée)."""
    quota = get_siren_quota(user_id)
    if quota is None:
        return True, ""
    current_count = len(list_registered_sirens(user_id))
    if current_count >= quota:
        return False, (
            f"Quota de {quota} SIREN atteint pour votre abonnement actuel. "
            "Passez à un forfait supérieur ou augmentez votre quantité Cabinet "
            "pour en enregistrer un de plus."
        )
    return True, ""


def register_siren(user_id: str, siren: str, company_name: str = "", tva_number: str = "") -> None:
    """Enregistre un SIREN pour ce compte, ou met à jour ses métadonnées s'il
    est déjà enregistré. Le contrôle de quota (`can_register_new_siren`) doit
    être fait par l'appelant AVANT d'appeler cette fonction pour un nouveau
    SIREN — cette fonction ne le revérifie pas elle-même."""
    pool = _get_pool()
    conn = pool.getconn()
    try:
        with conn, conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO tva_siren_registrations (user_id, siren, company_name, tva_number, first_used_at)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (user_id, siren)
                DO UPDATE SET company_name = EXCLUDED.company_name,
                              tva_number = EXCLUDED.tva_number
                """,
                (user_id, siren, company_name, tva_number, time.time()),
            )
            conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        pool.putconn(conn)


def _get_or_create_stripe_customer(user_id: str, email: str) -> str:
    pool = _get_pool()
    conn = pool.getconn()
    try:
        with conn, conn.cursor() as cur:
            cur.execute("SELECT stripe_customer_id FROM tva_customers WHERE user_id=%s", (user_id,))
            row = cur.fetchone()
            if row:
                return row[0]

            if not _stripe_configured():
                raise RuntimeError("Stripe non configuré (STRIPE_SECRET_KEY manquante).")

            customer = stripe.Customer.create(email=email, metadata={"user_id": user_id})
            cur.execute(
                "INSERT INTO tva_customers (user_id, stripe_customer_id) VALUES (%s, %s)",
                (user_id, customer.id),
            )
            conn.commit()
            return customer.id
    except Exception:
        conn.rollback()
        raise
    finally:
        pool.putconn(conn)


def create_payg_checkout_session(user_id: str, email: str, period_label: str, success_url: str, cancel_url: str) -> str:
    if not _stripe_configured():
        raise RuntimeError("Stripe non configuré (STRIPE_SECRET_KEY manquante).")
    if not PRICE_PAYG_EXPORT:
        raise RuntimeError("STRIPE_PRICE_PAYG_EXPORT non défini.")

    customer_id = _get_or_create_stripe_customer(user_id, email)
    session = stripe.checkout.Session.create(
        mode="payment",
        customer=customer_id,
        line_items=[{"price": PRICE_PAYG_EXPORT, "quantity": 1}],
        success_url=success_url,
        cancel_url=cancel_url,
        metadata={"user_id": user_id, "period_label": period_label, "kind": "payg_export"},
    )
    return session.url


def create_subscription_checkout_session(
    user_id: str,
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
    """
    if not _stripe_configured():
        raise RuntimeError("Stripe non configuré (STRIPE_SECRET_KEY manquante).")
    if plan not in ("business", "cabinet"):
        raise RuntimeError(f"Plan inconnu : {plan}")
    if interval not in ("month", "year"):
        raise RuntimeError(f"Intervalle de facturation inconnu : {interval}")

    price_id = _SUB_PRICE_IDS.get((plan, interval))
    if not price_id:
        raise RuntimeError(
            f"Aucun price_id Stripe configuré pour ({plan}, {interval}) — "
            "vérifiez les variables d'environnement STRIPE_PRICE_SUB_*."
        )

    effective_quantity = quantity if plan == "cabinet" else 1
    if effective_quantity < 1:
        effective_quantity = 1

    customer_id = _get_or_create_stripe_customer(user_id, email)
    session = stripe.checkout.Session.create(
        mode="subscription",
        customer=customer_id,
        line_items=[{"price": price_id, "quantity": effective_quantity}],
        success_url=success_url,
        cancel_url=cancel_url,
        subscription_data={
            "trial_period_days": 14,
            # Propagée sur l'objet Subscription (et pas seulement sur la
            # Session) pour que le webhook `customer.subscription.*` puisse
            # relire le plan/intervalle sans dépendre de la Session d'origine.
            "metadata": {"user_id": user_id, "plan": plan, "interval": interval},
        },
        metadata={"user_id": user_id, "plan": plan, "interval": interval},
    )
    return session.url


def create_billing_portal_session(user_id: str, return_url: str) -> str:
    pool = _get_pool()
    conn = pool.getconn()
    try:
        with conn, conn.cursor() as cur:
            cur.execute("SELECT stripe_customer_id FROM tva_customers WHERE user_id=%s", (user_id,))
            row = cur.fetchone()
    finally:
        pool.putconn(conn)

    if not row:
        raise RuntimeError("Aucun client Stripe pour cet utilisateur.")
    if not _stripe_configured():
        raise RuntimeError("Stripe non configuré (STRIPE_SECRET_KEY manquante).")

    portal = stripe.billing_portal.Session.create(customer=row[0], return_url=return_url)
    return portal.url


def _user_id_for_stripe_customer(stripe_customer_id: str) -> Optional[str]:
    pool = _get_pool()
    conn = pool.getconn()
    try:
        with conn, conn.cursor() as cur:
            cur.execute("SELECT user_id FROM tva_customers WHERE stripe_customer_id=%s", (stripe_customer_id,))
            row = cur.fetchone()
            return row[0] if row else None
    finally:
        pool.putconn(conn)


def _extract_subscription_item_details(data) -> tuple[int, Optional[str]]:
    """Extrait (quantity, interval) depuis l'objet Subscription Stripe.

    `data` est `event["data"]["object"]` pour un event
    customer.subscription.created/updated — un objet Subscription complet,
    avec `items.data[0]` contenant la ligne (price + quantity) souscrite.
    """
    items = _safe_get(data, "items", {}) or {}
    items_data = _safe_get(items, "data", []) or []
    if not items_data:
        return 1, None
    first_item = items_data[0]
    quantity = _safe_get(first_item, "quantity", 1) or 1
    price_obj = _safe_get(first_item, "price", {}) or {}
    recurring = _safe_get(price_obj, "recurring", {}) or {}
    interval = _safe_get(recurring, "interval")
    return int(quantity), interval


def handle_stripe_webhook_event(payload: bytes, sig_header: str) -> None:
    if not _stripe_configured():
        raise RuntimeError("Stripe non configuré (STRIPE_SECRET_KEY manquante).")
    webhook_secret = os.environ.get("STRIPE_WEBHOOK_SECRET", "")
    if not webhook_secret:
        raise RuntimeError("STRIPE_WEBHOOK_SECRET non définie.")

    event = stripe.Webhook.construct_event(payload, sig_header, webhook_secret)
    etype = event["type"]
    data = event["data"]["object"]

    if etype == "checkout.session.completed":
        metadata = _safe_get(data, "metadata", {}) or {}
        user_id = _safe_get(metadata, "user_id")
        if not user_id:
            return
        if _safe_get(metadata, "kind") == "payg_export":
            grant_export_credit(
                user_id,
                _safe_get(metadata, "period_label", ""),
                _safe_get(data, "payment_intent", ""),
            )

    elif etype in ("customer.subscription.created", "customer.subscription.updated"):
        customer_id = _safe_get(data, "customer")
        user_id = _user_id_for_stripe_customer(customer_id)
        if not user_id:
            return
        plan = _safe_get(_safe_get(data, "metadata") or {}, "plan", "unknown")
        quantity, interval = _extract_subscription_item_details(data)
        pool = _get_pool()
        conn = pool.getconn()
        try:
            with conn, conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO tva_subscriptions
                        (user_id, stripe_subscription_id, status, plan, current_period_end,
                         updated_at, billing_interval, siren_quantity)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (user_id) DO UPDATE SET
                        stripe_subscription_id = EXCLUDED.stripe_subscription_id,
                        status = EXCLUDED.status,
                        plan = EXCLUDED.plan,
                        current_period_end = EXCLUDED.current_period_end,
                        updated_at = EXCLUDED.updated_at,
                        billing_interval = EXCLUDED.billing_interval,
                        siren_quantity = EXCLUDED.siren_quantity
                    """,
                    (user_id, _safe_get(data, "id"), _safe_get(data, "status"), plan,
                     float(_safe_get(data, "current_period_end")), time.time(),
                     interval, quantity),
                )
                conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            pool.putconn(conn)

    elif etype == "customer.subscription.deleted":
        customer_id = _safe_get(data, "customer")
        user_id = _user_id_for_stripe_customer(customer_id)
        if not user_id:
            return
        pool = _get_pool()
        conn = pool.getconn()
        try:
            with conn, conn.cursor() as cur:
                cur.execute(
                    "UPDATE tva_subscriptions SET status='canceled', updated_at=%s WHERE user_id=%s",
                    (time.time(), user_id),
                )
                conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            pool.putconn(conn)