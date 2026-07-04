"""Facturation & quotas Stripe — tva_intracom.

Backend Postgres (Supabase).
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
PRICE_SUB_BUSINESS = os.environ.get("STRIPE_PRICE_SUB_BUSINESS", "")
PRICE_SUB_CABINET = os.environ.get("STRIPE_PRICE_SUB_CABINET", "")

_pool: Optional[psycopg2.pool.SimpleConnectionPool] = None


def _safe_get(obj, key, default=None):
    """Accès sécurisé à une clé, compatible dict classique ET objets Stripe
    (stripe.stripe_object.StripeObject des versions récentes du SDK, qui ne
    supportent pas .get() comme un dict — provoque AttributeError: get)."""
    try:
        return obj[key]
    except (KeyError, TypeError):
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


def get_subscription_status(user_id: str) -> SubscriptionStatus:
    if has_active_subscription_direct(user_id):
        pool = _get_pool()
        conn = pool.getconn()
        try:
            with conn, conn.cursor() as cur:
                cur.execute(
                    "SELECT status, plan, current_period_end FROM tva_subscriptions WHERE user_id=%s",
                    (user_id,),
                )
                row = cur.fetchone()
        finally:
            pool.putconn(conn)

        if row:
            status, plan, period_end = row
            active = status in ("active", "trialing") and period_end > time.time()
            return SubscriptionStatus(active=active, plan=plan, current_period_end=period_end)

    return SubscriptionStatus(active=False)


def has_active_subscription_direct(user_id: str) -> bool:
    pool = _get_pool()
    conn = pool.getconn()
    try:
        with conn, conn.cursor() as cur:
            cur.execute(
                "SELECT status, current_period_end FROM tva_subscriptions WHERE user_id=%s",
                (user_id,),
            )
            row = cur.fetchone()
            if row:
                status, period_end = row
                return status in ("active", "trialing") and period_end > time.time()
            return False
    finally:
        pool.putconn(conn)


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


def create_subscription_checkout_session(user_id: str, email: str, plan: str, success_url: str, cancel_url: str) -> str:
    if not _stripe_configured():
        raise RuntimeError("Stripe non configuré (STRIPE_SECRET_KEY manquante).")

    price_id = {"business": PRICE_SUB_BUSINESS, "cabinet": PRICE_SUB_CABINET}.get(plan)
    if not price_id:
        raise RuntimeError(f"Plan inconnu ou prix non configuré : {plan}")

    customer_id = _get_or_create_stripe_customer(user_id, email)
    session = stripe.checkout.Session.create(
        mode="subscription",
        customer=customer_id,
        line_items=[{"price": price_id, "quantity": 1}],
        success_url=success_url,
        cancel_url=cancel_url,
        subscription_data={"trial_period_days": 14},
        metadata={"user_id": user_id, "plan": plan},
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
        pool = _get_pool()
        conn = pool.getconn()
        try:
            with conn, conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO tva_subscriptions
                        (user_id, stripe_subscription_id, status, plan, current_period_end, updated_at)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    ON CONFLICT (user_id) DO UPDATE SET
                        stripe_subscription_id = EXCLUDED.stripe_subscription_id,
                        status = EXCLUDED.status,
                        plan = EXCLUDED.plan,
                        current_period_end = EXCLUDED.current_period_end,
                        updated_at = EXCLUDED.updated_at
                    """,
                    (user_id, _safe_get(data, "id"), _safe_get(data, "status"), plan,
                     float(_safe_get(data, "current_period_end")), time.time()),
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