"""Tests de la logique de facturation et de quotas SIREN — tva_intracom.billing.

Contexte : billing.py gère 3 forfaits (achat unique, Pro, Cabinet), le quota
de SIREN par compte, et l'extraction des détails d'abonnement Stripe
(quantité, intervalle, date d'échéance) depuis les objets renvoyés par
l'API — dont le schéma a changé entre versions (current_period_end déplacé
de l'objet Subscription vers chaque SubscriptionItem, cf. bug corrigé en
production suite à un webhook qui échouait silencieusement en 400).

Approche : les fonctions pures (extraction, quota, statut) sont testées
directement. Les fonctions qui touchent la base Postgres ou l'API Stripe
sont testées en isolant leurs dépendances via monkeypatch (mock de
_get_pool, de _stripe_configured, de stripe.checkout.Session.create) plutôt
qu'en simulant une vraie base — l'objectif est de vérifier la LOGIQUE
(quels arguments sont calculés, quelles requêtes sont déclenchées), pas le
comportement réel de Postgres/Stripe.

Ces tests ne nécessitent ni base Supabase réelle, ni clé Stripe valide.
"""

from __future__ import annotations

import os
import sys
import time
from types import SimpleNamespace
from unittest.mock import MagicMock

# Permet d'importer le package tva_intracom depuis /home/claude/ (cf. test_engine.py)
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import pytest

from tva_intracom import billing


# ---------------------------------------------------------------------------
# Objets Stripe factices : dict-like, suffisant pour billing._safe_get()
# (qui fait obj[key] avec fallback try/except), sans dépendre du SDK stripe.
# ---------------------------------------------------------------------------

def _fake_price(interval: str | None) -> dict:
    return {"recurring": ({"interval": interval} if interval else {})}


def _fake_item(quantity: int = 1, interval: str | None = "month",
                current_period_end: float | None = None) -> dict:
    item = {"quantity": quantity, "price": _fake_price(interval)}
    if current_period_end is not None:
        item["current_period_end"] = current_period_end
    return item


def _fake_subscription(items: list[dict] | None = None,
                        top_level_period_end: float | None = None) -> dict:
    sub: dict = {"items": {"data": items if items is not None else []}}
    if top_level_period_end is not None:
        sub["current_period_end"] = top_level_period_end
    return sub


# ---------------------------------------------------------------------------
# Fixture : pool Postgres factice (MagicMock), pour les fonctions qui
# touchent la base sans qu'on ait besoin d'une vraie sémantique SQL — on
# vérifie les valeurs passées à cur.execute(), pas le comportement de
# Postgres lui-même.
# ---------------------------------------------------------------------------

@pytest.fixture
def fake_db(monkeypatch):
    """Mocke billing._get_pool() : retourne un objet dont .getconn() donne
    une connexion factice utilisable en `with conn, conn.cursor() as cur`.
    La cursor est un MagicMock configurable par le test (fetchone/fetchall)."""
    cursor = MagicMock()
    cursor.__enter__ = MagicMock(return_value=cursor)
    cursor.__exit__ = MagicMock(return_value=False)

    conn = MagicMock()
    conn.__enter__ = MagicMock(return_value=conn)
    conn.__exit__ = MagicMock(return_value=False)
    conn.cursor.return_value = cursor

    pool = MagicMock()
    pool.getconn.return_value = conn
    pool.putconn.return_value = None

    monkeypatch.setattr(billing, "_get_pool", lambda: pool)
    return SimpleNamespace(pool=pool, conn=conn, cursor=cursor)


# ---------------------------------------------------------------------------
# _extract_subscription_item_details : extraction quantité/intervalle/échéance
# ---------------------------------------------------------------------------

class TestExtractSubscriptionItemDetails:
    """Couvre le bug de production : current_period_end déplacé de l'objet
    Subscription vers le SubscriptionItem selon la version d'API Stripe."""

    def test_period_end_read_from_item_when_present(self):
        sub = _fake_subscription(items=[_fake_item(quantity=3, interval="month",
                                                     current_period_end=1234.0)])
        quantity, interval, period_end = billing._extract_subscription_item_details(sub)
        assert quantity == 3
        assert interval == "month"
        assert period_end == 1234.0

    def test_period_end_falls_back_to_subscription_level(self):
        # Item sans current_period_end (ancien schéma d'API) : repli sur
        # l'objet Subscription lui-même.
        sub = _fake_subscription(
            items=[_fake_item(quantity=5, interval="year")],
            top_level_period_end=5678.0,
        )
        quantity, interval, period_end = billing._extract_subscription_item_details(sub)
        assert quantity == 5
        assert interval == "year"
        assert period_end == 5678.0

    def test_item_level_takes_priority_over_subscription_level(self):
        sub = _fake_subscription(
            items=[_fake_item(quantity=1, current_period_end=111.0)],
            top_level_period_end=999.0,
        )
        _, _, period_end = billing._extract_subscription_item_details(sub)
        assert period_end == 111.0

    def test_no_items_returns_defaults_with_subscription_level_period_end(self):
        sub = _fake_subscription(items=[], top_level_period_end=42.0)
        quantity, interval, period_end = billing._extract_subscription_item_details(sub)
        assert quantity == 1
        assert interval is None
        assert period_end == 42.0

    def test_no_items_and_no_period_end_anywhere_returns_none(self):
        sub = _fake_subscription(items=[])
        _, _, period_end = billing._extract_subscription_item_details(sub)
        assert period_end is None

    def test_missing_quantity_defaults_to_one(self):
        item = {"price": _fake_price("month")}  # pas de clé "quantity"
        sub = _fake_subscription(items=[item], top_level_period_end=1.0)
        quantity, _, _ = billing._extract_subscription_item_details(sub)
        assert quantity == 1


# ---------------------------------------------------------------------------
# SirenQuotaStatus : blocage total en sur-quota
# ---------------------------------------------------------------------------

class TestSirenQuotaStatus:

    def test_within_quota_not_blocked(self):
        status = billing.SirenQuotaStatus(registered_count=2, quota=3, over_quota_by=0)
        assert status.blocked is False

    def test_exactly_at_quota_not_blocked(self):
        status = billing.SirenQuotaStatus(registered_count=3, quota=3, over_quota_by=0)
        assert status.blocked is False

    def test_over_quota_blocked(self):
        status = billing.SirenQuotaStatus(registered_count=5, quota=3, over_quota_by=2)
        assert status.blocked is True


# ---------------------------------------------------------------------------
# get_siren_quota : 1 pour Pro/PAYG, quantité Stripe pour Cabinet
# ---------------------------------------------------------------------------

class TestGetSirenQuota:

    def test_no_active_subscription_quota_is_one(self, monkeypatch):
        monkeypatch.setattr(billing, "get_subscription_status",
                             lambda user_id: billing.SubscriptionStatus(active=False))
        assert billing.get_siren_quota("user-1") == 1

    def test_business_plan_quota_is_one(self, monkeypatch):
        monkeypatch.setattr(billing, "get_subscription_status",
                             lambda user_id: billing.SubscriptionStatus(active=True, plan="business"))
        assert billing.get_siren_quota("user-1") == 1

    def test_cabinet_plan_quota_is_purchased_quantity(self, monkeypatch):
        monkeypatch.setattr(billing, "get_subscription_status",
                             lambda user_id: billing.SubscriptionStatus(
                                 active=True, plan="cabinet", siren_quantity=7))
        assert billing.get_siren_quota("user-1") == 7

    def test_cabinet_plan_with_missing_quantity_falls_back_to_one(self, monkeypatch):
        monkeypatch.setattr(billing, "get_subscription_status",
                             lambda user_id: billing.SubscriptionStatus(
                                 active=True, plan="cabinet", siren_quantity=None))
        assert billing.get_siren_quota("user-1") == 1

    def test_unknown_plan_defaults_to_business_quota(self, monkeypatch):
        monkeypatch.setattr(billing, "get_subscription_status",
                             lambda user_id: billing.SubscriptionStatus(active=True, plan="mystere"))
        assert billing.get_siren_quota("user-1") == billing._BUSINESS_SIREN_QUOTA


# ---------------------------------------------------------------------------
# can_register_new_siren : autorisation d'ajout selon le quota
# ---------------------------------------------------------------------------

class TestCanRegisterNewSiren:

    def test_under_quota_allowed(self, monkeypatch):
        monkeypatch.setattr(billing, "get_siren_quota_status",
                             lambda user_id: billing.SirenQuotaStatus(
                                 registered_count=1, quota=3, over_quota_by=0))
        allowed, msg = billing.can_register_new_siren("user-1")
        assert allowed is True
        assert msg == ""

    def test_at_quota_blocked(self, monkeypatch):
        monkeypatch.setattr(billing, "get_siren_quota_status",
                             lambda user_id: billing.SirenQuotaStatus(
                                 registered_count=3, quota=3, over_quota_by=0))
        allowed, msg = billing.can_register_new_siren("user-1")
        assert allowed is False
        assert "Quota" in msg
        assert "3" in msg

    def test_over_quota_blocked(self, monkeypatch):
        monkeypatch.setattr(billing, "get_siren_quota_status",
                             lambda user_id: billing.SirenQuotaStatus(
                                 registered_count=5, quota=3, over_quota_by=2))
        allowed, _ = billing.can_register_new_siren("user-1")
        assert allowed is False


# ---------------------------------------------------------------------------
# create_subscription_checkout_session : validations et clamp de quantité
# ---------------------------------------------------------------------------

class TestCreateSubscriptionCheckoutSessionQuantity:
    """Vérifie la logique de quantité (SANS toucher Stripe ni la base) :
    - Pro forcé à 1, quel que soit l'input.
    - Cabinet : minimum 3 (_CABINET_MIN_QUANTITY), sinon la valeur demandée.
    """

    @pytest.fixture(autouse=True)
    def _mock_dependencies(self, monkeypatch):
        monkeypatch.setattr(billing, "_stripe_configured", lambda: True)
        monkeypatch.setattr(billing, "_get_or_create_stripe_customer",
                             lambda org_id, email, acting_user_id="": "cus_fake")
        monkeypatch.setattr(billing, "_env", lambda key, default="": f"price_fake_{key}")

        self.captured_kwargs = {}

        def _fake_session_create(**kwargs):
            self.captured_kwargs.update(kwargs)
            return SimpleNamespace(url="https://checkout.stripe.com/fake")

        fake_stripe = SimpleNamespace(
            checkout=SimpleNamespace(Session=SimpleNamespace(create=_fake_session_create))
        )
        monkeypatch.setattr(billing, "stripe", fake_stripe)

    def _quantity_sent_to_stripe(self) -> int:
        return self.captured_kwargs["line_items"][0]["quantity"]

    def test_business_plan_quantity_forced_to_one(self):
        billing.create_subscription_checkout_session(
            org_id="org1", acting_user_id="u1", email="a@b.fr", plan="business", interval="month",
            success_url="https://x/success", cancel_url="https://x/cancel",
            quantity=42,  # doit être ignoré
        )
        assert self._quantity_sent_to_stripe() == 1

    def test_cabinet_plan_quantity_below_minimum_is_clamped(self):
        billing.create_subscription_checkout_session(
            org_id="org1", acting_user_id="u1", email="a@b.fr", plan="cabinet", interval="month",
            success_url="https://x/success", cancel_url="https://x/cancel",
            quantity=1,
        )
        assert self._quantity_sent_to_stripe() == billing._CABINET_MIN_QUANTITY

    def test_cabinet_plan_quantity_above_minimum_is_kept(self):
        billing.create_subscription_checkout_session(
            org_id="org1", acting_user_id="u1", email="a@b.fr", plan="cabinet", interval="year",
            success_url="https://x/success", cancel_url="https://x/cancel",
            quantity=10,
        )
        assert self._quantity_sent_to_stripe() == 10

    def test_cabinet_plan_quantity_exactly_minimum_is_kept(self):
        billing.create_subscription_checkout_session(
            org_id="org1", acting_user_id="u1", email="a@b.fr", plan="cabinet", interval="month",
            success_url="https://x/success", cancel_url="https://x/cancel",
            quantity=billing._CABINET_MIN_QUANTITY,
        )
        assert self._quantity_sent_to_stripe() == billing._CABINET_MIN_QUANTITY

    def test_unknown_plan_raises(self):
        with pytest.raises(RuntimeError, match="Plan inconnu"):
            billing.create_subscription_checkout_session(
                org_id="org1", acting_user_id="u1", email="a@b.fr", plan="entreprise", interval="month",
                success_url="https://x/success", cancel_url="https://x/cancel",
            )

    def test_unknown_interval_raises(self):
        with pytest.raises(RuntimeError, match="[Ii]ntervalle"):
            billing.create_subscription_checkout_session(
                org_id="org1", acting_user_id="u1", email="a@b.fr", plan="business", interval="week",
                success_url="https://x/success", cancel_url="https://x/cancel",
            )

    def test_metadata_propagated_to_subscription_for_webhook(self):
        """Le plan/intervalle doivent être portés par subscription_data.metadata
        (pas seulement la Session) pour que le webhook puisse les relire sur
        l'objet Subscription lui-même — cf. bug corrigé en production où le
        plan restait 'unknown' sur les events customer.subscription.*."""
        billing.create_subscription_checkout_session(
            org_id="org42", acting_user_id="u42", email="a@b.fr", plan="cabinet", interval="year",
            success_url="https://x/success", cancel_url="https://x/cancel",
            quantity=5,
        )
        sub_metadata = self.captured_kwargs["subscription_data"]["metadata"]
        assert sub_metadata == {"org_id": "org42", "user_id": "u42", "plan": "cabinet", "interval": "year"}

    def test_no_trial_period(self):
        """L'essai gratuit de 14 jours a été retiré (cf. historique : faussait
        les tests de bout en bout, aucune transaction visible dans Stripe)."""
        billing.create_subscription_checkout_session(
            org_id="org1", acting_user_id="u1", email="a@b.fr", plan="business", interval="month",
            success_url="https://x/success", cancel_url="https://x/cancel",
        )
        assert "trial_period_days" not in self.captured_kwargs["subscription_data"]

    def test_missing_price_id_raises(self, monkeypatch):
        monkeypatch.setattr(billing, "_env", lambda key, default="": "")
        with pytest.raises(RuntimeError, match="price_id"):
            billing.create_subscription_checkout_session(
                org_id="org1", acting_user_id="u1", email="a@b.fr", plan="business", interval="month",
                success_url="https://x/success", cancel_url="https://x/cancel",
            )


# ---------------------------------------------------------------------------
# request_siren_removal : échéance différée (abonnement actif) vs immédiate
# ---------------------------------------------------------------------------

class TestRequestSirenRemoval:
    """Retrait différé (lazy deletion) : effectif à la date anniversaire de
    l'abonnement si actif, immédiat sinon — cf. décision produit explicite
    pour éviter les abus d'ajout/retrait en cours de période."""

    def test_immediate_removal_without_active_subscription(self, fake_db, monkeypatch):
        monkeypatch.setattr(billing, "get_subscription_status",
                             lambda org_id: billing.SubscriptionStatus(active=False))
        before = time.time()
        effective_at = billing.request_siren_removal("org-1", "user-1", "123456789")
        after = time.time()
        assert before <= effective_at <= after

    def test_deferred_removal_with_active_subscription(self, fake_db, monkeypatch):
        period_end = time.time() + 30 * 24 * 3600  # dans 30 jours
        monkeypatch.setattr(billing, "get_subscription_status",
                             lambda org_id: billing.SubscriptionStatus(
                                 active=True, plan="cabinet", current_period_end=period_end))
        effective_at = billing.request_siren_removal("org-1", "user-1", "123456789")
        assert effective_at == period_end

    def test_removal_writes_pending_removal_at_via_sql(self, fake_db, monkeypatch):
        monkeypatch.setattr(billing, "get_subscription_status",
                             lambda org_id: billing.SubscriptionStatus(active=False))
        # Le mock de cursor.fetchone() (utilisé par _require_write_access
        # pour vérifier le rôle) renvoie None par défaut sur ce MagicMock
        # non configuré explicitement -> traité comme "pas lecteur" (rôle
        # inconnu / compte absent), ce qui est le comportement voulu ici :
        # ne jamais bloquer sur une erreur de lecture du rôle.
        fake_db.cursor.fetchone.return_value = None
        billing.request_siren_removal("org-42", "user-42", "999888777")
        # 2 requêtes désormais : 1) lecture du rôle (_require_write_access),
        # 2) UPDATE pending_removal_at — le dernier appel est bien l'écriture.
        assert fake_db.cursor.execute.call_count == 2
        _last_sql = fake_db.cursor.execute.call_args_list[-1][0][0]
        assert "pending_removal_at" in _last_sql
        sql, params = fake_db.cursor.execute.call_args[0]
        assert "pending_removal_at" in sql
        # (effective_at, acting_user_id, org_id, siren) — org_id est
        # désormais la clé de l'UPDATE, acting_user_id est l'audit.
        assert params[1:] == ("user-42", "org-42", "999888777")


# ---------------------------------------------------------------------------
# has_export_credit : abonnement actif court-circuite le crédit à l'unité
# ---------------------------------------------------------------------------

class TestHasExportCredit:

    def test_active_subscription_grants_access_without_credit_lookup(self, monkeypatch):
        monkeypatch.setattr(billing, "has_active_subscription_direct", lambda org_id: True)
        # Si le crédit à l'unité était consulté, ceci ferait planter le test
        # (pas de fake_db fourni) — on vérifie donc aussi le court-circuit.
        assert billing.has_export_credit("org-1", "2026-Q1") is True

    def test_no_subscription_checks_export_credit_table(self, fake_db, monkeypatch):
        monkeypatch.setattr(billing, "has_active_subscription_direct", lambda org_id: False)
        fake_db.cursor.fetchone.return_value = (1,)
        assert billing.has_export_credit("org-1", "2026-Q1") is True

    def test_no_subscription_and_no_credit_denies_access(self, fake_db, monkeypatch):
        monkeypatch.setattr(billing, "has_active_subscription_direct", lambda org_id: False)
        fake_db.cursor.fetchone.return_value = None
        assert billing.has_export_credit("org-1", "2026-Q1") is False


# ---------------------------------------------------------------------------
# _extract_scheduled_change : downgrade différé via Subscription Schedule
# Stripe (ajout 2026-08-16) — logique pure, sans DB ni API Stripe.
# ---------------------------------------------------------------------------

def _fake_schedule(current_end: float | None, phases: list[dict]) -> dict:
    schedule: dict = {"phases": phases}
    if current_end is not None:
        schedule["current_phase"] = {"start_date": 0, "end_date": current_end}
    return schedule


def _fake_schedule_phase(start_date: float, price_id: str | None,
                          interval: str | None = "month", expanded_price: bool = False) -> dict:
    if price_id is None:
        price = None
    elif expanded_price:
        price = {"id": price_id, "recurring": ({"interval": interval} if interval else {})}
    else:
        price = price_id
    return {"start_date": start_date, "items": [{"price": price, "quantity": 1}]}


class TestExtractScheduledChange:

    def setup_method(self):
        # _plan_from_price_id compare aux price_id configurés en variables
        # d'environnement — on les fixe explicitement pour ces tests plutôt
        # que de dépendre de l'environnement sandbox (souvent vide).
        os.environ["STRIPE_PRICE_SUB_BUSINESS_MONTHLY"] = "price_business_month"
        os.environ["STRIPE_PRICE_SUB_BUSINESS_YEARLY"] = "price_business_year"
        os.environ["STRIPE_PRICE_SUB_CABINET_MONTHLY"] = "price_cabinet_month"
        os.environ["STRIPE_PRICE_SUB_CABINET_YEARLY"] = "price_cabinet_year"

    def test_no_current_phase_returns_none(self):
        # Schedule pas encore démarré (statut "not_started") : rien à afficher.
        schedule = _fake_schedule(current_end=None, phases=[])
        assert billing._extract_scheduled_change(schedule) is None

    def test_next_phase_found_and_plan_resolved(self):
        schedule = _fake_schedule(
            current_end=1000.0,
            phases=[
                _fake_schedule_phase(0, "price_business_month"),
                _fake_schedule_phase(1000.0, "price_cabinet_month", expanded_price=True),
            ],
        )
        result = billing._extract_scheduled_change(schedule)
        assert result == ("cabinet", "month", 1000.0)

    def test_next_phase_price_as_plain_string_id(self):
        # Cas non-expanded (le plus courant côté webhook réel) : price est
        # directement l'id string, pas un objet — interval reste None
        # (non extractible sans expand), mais le plan doit être résolu.
        schedule = _fake_schedule(
            current_end=500.0,
            phases=[
                _fake_schedule_phase(0, "price_cabinet_month"),
                _fake_schedule_phase(500.0, "price_business_year", expanded_price=False),
            ],
        )
        result = billing._extract_scheduled_change(schedule)
        assert result == ("business", None, 500.0)

    def test_no_matching_next_phase_returns_none(self):
        # Aucune phase ne démarre exactement à current_phase.end_date (ex:
        # dernière phase du schedule) : rien à afficher.
        schedule = _fake_schedule(
            current_end=1000.0,
            phases=[_fake_schedule_phase(0, "price_business_month")],
        )
        assert billing._extract_scheduled_change(schedule) is None

    def test_unknown_price_id_returns_none(self):
        # price_id de la phase suivante ne correspond à aucun plan connu
        # (price legacy, etc.) : on préfère ne rien afficher plutôt qu'une
        # info fausse.
        schedule = _fake_schedule(
            current_end=1000.0,
            phases=[
                _fake_schedule_phase(0, "price_business_month"),
                _fake_schedule_phase(1000.0, "price_legacy_unknown"),
            ],
        )
        assert billing._extract_scheduled_change(schedule) is None


# ---------------------------------------------------------------------------
# handle_stripe_webhook_event : events subscription_schedule.* (2026-08-16)
# ---------------------------------------------------------------------------

class TestSubscriptionScheduleWebhookEvents:

    def _make_event(self, etype: str, data: dict) -> dict:
        return {"type": etype, "data": {"object": data}}

    def test_schedule_created_sets_scheduled_change(self, fake_db, monkeypatch):
        monkeypatch.setattr(billing, "_stripe_configured", lambda: True)
        monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", "whsec_test")
        schedule = _fake_schedule(
            current_end=2000.0,
            phases=[
                _fake_schedule_phase(0, "price_business_month"),
                _fake_schedule_phase(2000.0, "price_cabinet_year", expanded_price=True, interval="year"),
            ],
        )
        schedule["customer"] = "cus_123"
        event = self._make_event("subscription_schedule.created", schedule)
        monkeypatch.setattr(billing.stripe.Webhook, "construct_event", lambda *a, **k: event)
        monkeypatch.setattr(billing, "_org_id_for_stripe_customer", lambda cid: "org-42")

        os.environ["STRIPE_PRICE_SUB_BUSINESS_MONTHLY"] = "price_business_month"
        os.environ["STRIPE_PRICE_SUB_CABINET_YEARLY"] = "price_cabinet_year"

        billing.handle_stripe_webhook_event(b"{}", "sig")

        fake_db.cursor.execute.assert_called_once()
        sql, params = fake_db.cursor.execute.call_args[0]
        assert "scheduled_plan" in sql
        assert params[0] == "cabinet"
        assert params[1] == "year"
        assert params[2] == 2000.0
        assert params[-1] == "org-42"

    def test_schedule_released_clears_scheduled_change(self, fake_db, monkeypatch):
        monkeypatch.setattr(billing, "_stripe_configured", lambda: True)
        monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", "whsec_test")
        event = self._make_event("subscription_schedule.released", {"customer": "cus_123"})
        monkeypatch.setattr(billing.stripe.Webhook, "construct_event", lambda *a, **k: event)
        monkeypatch.setattr(billing, "_org_id_for_stripe_customer", lambda cid: "org-42")

        billing.handle_stripe_webhook_event(b"{}", "sig")

        fake_db.cursor.execute.assert_called_once()
        sql, params = fake_db.cursor.execute.call_args[0]
        assert "scheduled_plan=NULL" in sql
        assert params[-1] == "org-42"

    def test_schedule_event_without_org_id_is_noop(self, fake_db, monkeypatch):
        monkeypatch.setattr(billing, "_stripe_configured", lambda: True)
        monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", "whsec_test")
        event = self._make_event("subscription_schedule.canceled", {"customer": "cus_unknown"})
        monkeypatch.setattr(billing.stripe.Webhook, "construct_event", lambda *a, **k: event)
        monkeypatch.setattr(billing, "_org_id_for_stripe_customer", lambda cid: None)

        billing.handle_stripe_webhook_event(b"{}", "sig")

        fake_db.cursor.execute.assert_not_called()


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
