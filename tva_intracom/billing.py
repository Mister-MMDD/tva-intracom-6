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
import secrets
import time
from dataclasses import dataclass
from typing import Optional

import psycopg2
import psycopg2.pool

try:
    import stripe  # type: ignore
except ImportError:
    stripe = None

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
    key = _env("STRIPE_SECRET_KEY")
    if not key or stripe is None:
        return False
    stripe.api_key = key
    return True


def _get_pool() -> psycopg2.pool.SimpleConnectionPool:
    global _pool
    if _pool is None:
        dsn = _env("SUPABASE_DB_URL")
        if not dsn:
            raise RuntimeError(
                "SUPABASE_DB_URL non définie — impossible de se connecter à la base."
            )
        _pool = psycopg2.pool.SimpleConnectionPool(1, 5, dsn, sslmode="require")
        _init_schema()
    return _pool


def _run(fn):
    """Exécute fn(conn, cur) avec une connexion prise dans le pool, avec un
    retry unique si la connexion s'avère fermée côté serveur.

    Même correctif que tva_intracom/auth.py : le pool global survit à toutes
    les reruns tant que le process tourne, et le pooler Supabase (PgBouncer,
    mode transaction) recycle agressivement les connexions inactives côté
    serveur — d'où `psycopg2.InterfaceError: connection already closed`
    après un moment d'inactivité. On jette le pool et on en recrée un neuf
    pour retenter une fois plutôt que de laisser planter la requête."""
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
            _pool = None
    raise last_exc


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

    _run(_fn)


def delete_user_billing_data(user_id: str) -> None:
    """Supprime toutes les données de facturation d'un utilisateur, y compris
    ses SIREN enregistrés, ses crédits d'export et ses liens Amazon.
    Supprime également le client Stripe (ce qui annule ses abonnements)."""

    # 1. Supprimer le client Stripe (si configuré)
    customer_id = _existing_stripe_customer_id(user_id)
    if customer_id and _stripe_configured():
        try:
            stripe.Customer.delete(customer_id)
        except Exception:
            # On ignore l'erreur si le client est déjà supprimé côté Stripe
            pass

    def _fn(conn, cur):
        # 2. Supprimer les données locales
        cur.execute("DELETE FROM tva_customers WHERE user_id=%s", (user_id,))
        cur.execute("DELETE FROM tva_subscriptions WHERE user_id=%s", (user_id,))
        cur.execute("DELETE FROM tva_export_credits WHERE user_id=%s", (user_id,))
        cur.execute("DELETE FROM tva_siren_registrations WHERE user_id=%s", (user_id,))
        # On ne supprime tva_account_siren_links que si le scope_id correspond à un scope utilisateur
        # mais on n'a pas accès à l'email ici. On laisse auth.py s'en charger s'il le souhaite
        # ou on le fait par filtrage de préfixe.
        cur.execute("DELETE FROM tva_account_siren_links WHERE scope_id LIKE %s", (f"user:%",))
        conn.commit()

    _run(_fn)


def export_user_billing_data(user_id: str) -> dict:
    """Récupère toutes les données de facturation d'un utilisateur pour export."""
    def _fn(conn, cur):
        data = {}

        cur.execute("SELECT stripe_customer_id FROM tva_customers WHERE user_id=%s", (user_id,))
        data["customer"] = cur.fetchone()

        cur.execute("SELECT * FROM tva_subscriptions WHERE user_id=%s", (user_id,))
        data["subscriptions"] = [dict(zip([col[0] for col in cur.description], row)) for row in cur.fetchall()]

        cur.execute("SELECT * FROM tva_export_credits WHERE user_id=%s", (user_id,))
        data["export_credits"] = [dict(zip([col[0] for col in cur.description], row)) for row in cur.fetchall()]

        cur.execute("SELECT * FROM tva_siren_registrations WHERE user_id=%s", (user_id,))
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


def get_subscription_status(user_id: str) -> SubscriptionStatus:
    def _fn(conn, cur):
        cur.execute(
            """
            SELECT status, plan, current_period_end, billing_interval, siren_quantity
            FROM tva_subscriptions WHERE user_id=%s
            """,
            (user_id,),
        )
        return cur.fetchone()

    row = _run(_fn)

    if not row:
        return SubscriptionStatus(active=False)

    status, plan, period_end, billing_interval, siren_quantity = row
    active = status in ("active", "trialing") and period_end > time.time()
    return SubscriptionStatus(
        active=active,
        plan=plan,
        status=status,
        current_period_end=period_end,
        billing_interval=billing_interval,
        siren_quantity=siren_quantity,
    )


def has_active_subscription_direct(user_id: str) -> bool:
    return get_subscription_status(user_id).active


def has_export_credit(user_id: str, period_label: str) -> bool:
    if has_active_subscription_direct(user_id):
        return True

    def _fn(conn, cur):
        cur.execute(
            "SELECT 1 FROM tva_export_credits WHERE user_id=%s AND period_label=%s",
            (user_id, period_label),
        )
        return cur.fetchone() is not None

    return _run(_fn)


def grant_export_credit(user_id: str, period_label: str, payment_intent_id: str = "") -> None:
    def _fn(conn, cur):
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

    _run(_fn)


def list_purchased_credits(user_id: str) -> list[dict]:
    """Liste tous les crédits d'export PAYG achetés par l'utilisateur."""
    def _fn(conn, cur):
        cur.execute(
            "SELECT period_label, purchased_at FROM tva_export_credits WHERE user_id=%s ORDER BY purchased_at DESC",
            (user_id,),
        )
        return cur.fetchall()

    rows = _run(_fn)
    return [{"period": r[0], "at": r[1]} for r in rows]


# =============================================================================
# QUOTAS SIREN
# =============================================================================
# Le SIREN identifie l'entreprise cliente (9 chiffres). Chaque compte peut en
# enregistrer un nombre limité selon son forfait :
#   - Sans abonnement actif (PAYG) : pas de limite technique — le paiement se
#     fait par période, indépendamment du nombre de SIREN utilisés.
#   - Pro ("business")  : 1 SIREN maximum.
#   - Cabinet ("cabinet"): jusqu'à `siren_quantity` (quantité Stripe achetée).


def _purge_expired_siren_removals(user_id: str) -> None:
    """Supprime définitivement les SIREN dont le retrait différé est arrivé à
    échéance (lazy deletion : exécuté à chaque lecture, pas de tâche de fond)."""
    def _fn(conn, cur):
        cur.execute(
            """
            DELETE FROM tva_siren_registrations
            WHERE user_id=%s AND pending_removal_at IS NOT NULL AND pending_removal_at <= %s
            """,
            (user_id, time.time()),
        )
        conn.commit()

    _run(_fn)


def list_registered_sirens(user_id: str) -> list[dict]:
    _purge_expired_siren_removals(user_id)

    def _fn(conn, cur):
        cur.execute(
            """
            SELECT siren, company_name, tva_number, first_used_at, pending_removal_at,
                   ioss_number, seller_is_importer, apply_fr_under_threshold, countries_with_vat, vat_numbers_json
            FROM tva_siren_registrations
            WHERE user_id=%s
            ORDER BY first_used_at ASC
            """,
            (user_id,),
        )
        return cur.fetchall()

    rows = _run(_fn)
    return [
        {
            "siren": r[0], "company_name": _dec(r[1]), "tva_number": r[2],
            "first_used_at": r[3], "pending_removal_at": r[4],
            "ioss_number": r[5], "seller_is_importer": r[6],
            "apply_fr_under_threshold": r[7], "countries_with_vat": r[8],
            "vat_numbers_json": r[9],
        }
        for r in rows
    ]


def get_siren_quota(user_id: str) -> int:
    """Retourne le quota de SIREN distincts pour ce compte.

    - Pas d'abonnement actif (PAYG) : 1 SIREN, comme le forfait Pro.
    - Pro ("business") : 1 SIREN.
    - Cabinet ("cabinet") : quantité Stripe achetée (`siren_quantity`).
    """
    sub = get_subscription_status(user_id)
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


def get_siren_quota_status(user_id: str) -> SirenQuotaStatus:
    quota = get_siren_quota(user_id)
    count = len(list_registered_sirens(user_id))
    return SirenQuotaStatus(registered_count=count, quota=quota, over_quota_by=max(0, count - quota))


def can_register_new_siren(user_id: str) -> tuple[bool, str]:
    """Vérifie si le compte peut enregistrer un SIREN supplémentaire (celui-ci
    n'étant pas déjà dans sa liste). Ne s'applique pas à un SIREN déjà
    enregistré (mise à jour du nom/TVA toujours autorisée)."""
    status = get_siren_quota_status(user_id)
    if status.registered_count >= status.quota:
        return False, (
            f"Quota de {status.quota} SIREN atteint pour votre abonnement actuel. "
            "Passez à un forfait supérieur ou augmentez votre quantité Cabinet "
            "pour en enregistrer un de plus."
        )
    return True, ""


def register_siren(
        user_id: str, siren: str, company_name: str = "", tva_number: str = "",
        ioss_number: str = "", seller_is_importer: bool = False,
        apply_fr_under_threshold: bool = False, countries_with_vat: str = "",
        vat_numbers_json: str = ""
) -> None:
    """Enregistre un SIREN pour ce compte, ou met à jour ses métadonnées s'il
    est déjà enregistré. Le contrôle de quota (`can_register_new_siren`) doit
    être fait par l'appelant AVANT d'appeler cette fonction pour un nouveau
    SIREN — cette fonction ne le revérifie pas elle-même."""
    def _fn(conn, cur):
        cur.execute(
            """
            INSERT INTO tva_siren_registrations (
                user_id, siren, company_name, tva_number, first_used_at,
                ioss_number, seller_is_importer, apply_fr_under_threshold, countries_with_vat, vat_numbers_json
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (user_id, siren)
            DO UPDATE SET company_name = EXCLUDED.company_name,
                          tva_number = EXCLUDED.tva_number,
                          ioss_number = EXCLUDED.ioss_number,
                          seller_is_importer = EXCLUDED.seller_is_importer,
                          apply_fr_under_threshold = EXCLUDED.apply_fr_under_threshold,
                          countries_with_vat = EXCLUDED.countries_with_vat,
                          vat_numbers_json = EXCLUDED.vat_numbers_json
            """,
            (user_id, siren, _enc(company_name), tva_number, time.time(),
             ioss_number, seller_is_importer, apply_fr_under_threshold, countries_with_vat, vat_numbers_json),
        )
        conn.commit()

    _run(_fn)


def request_siren_removal(user_id: str, siren: str) -> float:
    """Marque un SIREN "en attente de retrait". Le retrait est effectif à la
    date anniversaire de l'abonnement en cours (current_period_end) pour
    éviter les abus (retirer/ajouter un SIREN à volonté en cours de période).
    Sans abonnement actif, le retrait est immédiat (pas de notion de période).
    Retourne le timestamp d'échéance effective."""
    sub = get_subscription_status(user_id)
    effective_at = sub.current_period_end if (sub.active and sub.current_period_end) else time.time()

    def _fn(conn, cur):
        cur.execute(
            "UPDATE tva_siren_registrations SET pending_removal_at=%s WHERE user_id=%s AND siren=%s",
            (effective_at, user_id, siren),
        )
        conn.commit()

    _run(_fn)
    return effective_at


def cancel_siren_removal(user_id: str, siren: str) -> None:
    """Annule une demande de retrait en attente."""
    def _fn(conn, cur):
        cur.execute(
            "UPDATE tva_siren_registrations SET pending_removal_at=NULL WHERE user_id=%s AND siren=%s",
            (user_id, siren),
        )
        conn.commit()

    _run(_fn)


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


def get_siren_links_for_identifiers(scope_id: str, identifiers) -> dict[str, str]:
    """Retourne {account_identifier: siren} pour les identifiants déjà liés
    dans ce scope, parmi ceux fournis. Les identifiants inconnus (jamais liés)
    sont simplement absents du dict retourné — à l'appelant de les traiter
    comme "à confirmer" (voir ui/billing_gate.py)."""
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


def _existing_stripe_customer_id(user_id: str) -> Optional[str]:
    """Lit le stripe_customer_id existant, sans en créer un nouveau (contrairement
    à _get_or_create_stripe_customer) — utilisé pour de simples vérifications en
    lecture (ex. éligibilité aux codes promo) où créer un client Stripe pour un
    simple affichage de grille tarifaire serait un effet de bord indésirable."""
    def _select(conn, cur):
        cur.execute("SELECT stripe_customer_id FROM tva_customers WHERE user_id=%s", (user_id,))
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


def list_available_promotions(user_id: Optional[str] = None) -> list[dict]:
    """Liste les codes promotionnels actifs configurés côté Stripe (Dashboard),
    avec leurs conditions d'utilisation, sans jamais les recopier en dur ici.

    Si `user_id` est fourni et qu'un client Stripe existe déjà pour ce
    compte, chaque code inclut aussi son éligibilité pour CE client
    (vérifiée en direct côté Stripe : historique de paiement pour la
    restriction "1ère commande", stock restant, date d'expiration). Sans
    `user_id` (visiteur non connecté), "eligible" vaut None (inconnu).

    Les codes restreints à un client Stripe précis (`promo.customer` défini)
    et différent de `user_id` sont exclus de la liste — ce sont des codes
    privés, pas des offres publiques à afficher.

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

    customer_id = _existing_stripe_customer_id(user_id) if user_id else None
    has_paid_before = _stripe_customer_has_paid_before(customer_id) if customer_id else False

    results: list[dict] = []
    try:
        # On étend le coupon (quel que soit son emplacement selon la version de l'API)
        promos = stripe.PromotionCode.list(
            active=True, limit=100,
            expand=["data.coupon", "data.promotion.coupon"]
        )
    except Exception:
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

        if user_id:
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


def get_pricing_grid(user_id: Optional[str] = None) -> dict:
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
        _candidates = [p for p in list_available_promotions(user_id) if p.get("eligible") is not False]
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



def _get_or_create_stripe_customer(user_id: str, email: str) -> str:
    """Récupère le stripe_customer_id existant, ou en crée un nouveau.

    L'appel réseau à `stripe.Customer.create()` est fait EXACTEMENT une fois,
    en dehors de toute logique de retry — contrairement à une version
    précédente qui l'enfermait dans le même bloc retenté par `_run()` en cas
    de connexion Postgres fermée par le pooler (cf. tva_intracom/auth.py) :
    un retry aurait alors pu créer un second client Stripe pour le même
    utilisateur. Les deux accès DB de part et d'autre restent, eux, sûrs à
    retenter (SELECT, puis INSERT idempotent via ON CONFLICT DO NOTHING)."""
    def _select(conn, cur):
        cur.execute("SELECT stripe_customer_id FROM tva_customers WHERE user_id=%s", (user_id,))
        row = cur.fetchone()
        return row[0] if row else None

    existing = _run(_select)
    if existing:
        return existing

    if not _stripe_configured():
        raise RuntimeError("Stripe non configuré (STRIPE_SECRET_KEY manquante).")

    customer = stripe.Customer.create(email=email, metadata={"user_id": user_id})

    def _insert(conn, cur):
        cur.execute(
            """
            INSERT INTO tva_customers (user_id, stripe_customer_id)
            VALUES (%s, %s)
            ON CONFLICT (user_id) DO NOTHING
            """,
            (user_id, customer.id),
        )
        conn.commit()
        # Relit la valeur réellement stockée : en cas de course avec un autre
        # appel concurrent déjà passé, on renvoie le customer_id existant en
        # base plutôt que celui qu'on vient de créer (qui serait alors orphelin
        # côté Stripe — pas grave en soi, mais autant renvoyer la valeur
        # canonique effectivement utilisée par l'application).
        cur.execute("SELECT stripe_customer_id FROM tva_customers WHERE user_id=%s", (user_id,))
        return cur.fetchone()[0]

    return _run(_insert)


def create_payg_checkout_session(user_id: str, email: str, period_label: str, success_url: str, cancel_url: str) -> str:
    if not _stripe_configured():
        raise RuntimeError("Stripe non configuré (STRIPE_SECRET_KEY manquante).")
    if not _env("STRIPE_PRICE_PAYG_EXPORT"):
        raise RuntimeError("STRIPE_PRICE_PAYG_EXPORT non défini.")

    customer_id = _get_or_create_stripe_customer(user_id, email)
    session = stripe.checkout.Session.create(
        mode="payment",
        customer=customer_id,
        line_items=[{"price": _env("STRIPE_PRICE_PAYG_EXPORT"), "quantity": 1}],
        success_url=success_url,
        cancel_url=cancel_url,
        allow_promotion_codes=True,
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

    customer_id = _get_or_create_stripe_customer(user_id, email)
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
            "metadata": {"user_id": user_id, "plan": plan, "interval": interval},
        },
        metadata={"user_id": user_id, "plan": plan, "interval": interval},
    )
    return session.url


def create_billing_portal_session(user_id: str, return_url: str) -> str:
    def _fn(conn, cur):
        cur.execute("SELECT stripe_customer_id FROM tva_customers WHERE user_id=%s", (user_id,))
        return cur.fetchone()

    row = _run(_fn)

    if not row:
        raise RuntimeError("Aucun client Stripe pour cet utilisateur.")
    if not _stripe_configured():
        raise RuntimeError("Stripe non configuré (STRIPE_SECRET_KEY manquante).")

    portal = stripe.billing_portal.Session.create(customer=row[0], return_url=return_url)
    return portal.url


def _user_id_for_stripe_customer(stripe_customer_id: str) -> Optional[str]:
    def _fn(conn, cur):
        cur.execute("SELECT user_id FROM tva_customers WHERE stripe_customer_id=%s", (stripe_customer_id,))
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
        user_id: str,
        stripe_subscription_id: str,
        status: str,
        plan: str,
        current_period_end: float,
        billing_interval: Optional[str],
        siren_quantity: int,
) -> None:
    def _fn(conn, cur):
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
            (user_id, stripe_subscription_id, status, plan, current_period_end,
             time.time(), billing_interval, siren_quantity),
        )
        conn.commit()

    _run(_fn)


def _get_or_create_user_id_by_email(email: str) -> str:
    """Retrouve l'ID utilisateur pour un email donné, ou en crée un nouveau.
    Utilisé par le webhook pour les paiements venant du site externe (Pricing Table)."""
    email = email.strip().lower()

    def _select(conn, cur):
        cur.execute("SELECT id FROM tva_users WHERE email=%s", (email,))
        row = cur.fetchone()
        return row[0] if row else None

    existing_id = _run(_select)
    if existing_id:
        return existing_id

    user_id = secrets.token_hex(12)

    def _insert(conn, cur):
        cur.execute(
            "INSERT INTO tva_users (id, email, created_at) VALUES (%s, %s, %s) ON CONFLICT (email) DO NOTHING",
            (user_id, email, time.time()),
        )
        conn.commit()
        # Relit l'ID réellement en base (en cas de création concurrente)
        cur.execute("SELECT id FROM tva_users WHERE email=%s", (email,))
        return cur.fetchone()[0]

    return _run(_insert)


def _link_stripe_customer(user_id: str, stripe_customer_id: str) -> None:
    """Lie un ID utilisateur à un ID client Stripe en base."""
    def _fn(conn, cur):
        cur.execute(
            """
            INSERT INTO tva_customers (user_id, stripe_customer_id)
            VALUES (%s, %s)
            ON CONFLICT (user_id) DO UPDATE SET stripe_customer_id = EXCLUDED.stripe_customer_id
            """,
            (user_id, stripe_customer_id),
        )
        conn.commit()

    _run(_fn)


def _fulfill_checkout_session(data: dict) -> None:
    """Débloque l'accès (crédit PAYG ou abonnement) pour une session Checkout
    dont le paiement est confirmé — appelée uniquement quand payment_status
    vaut "paid" (carte, ou virement/prélèvement une fois les fonds arrivés)."""
    metadata = _safe_get(data, "metadata", {}) or {}
    user_id = _safe_get(metadata, "user_id")

    # FALLBACK : si user_id absent (paiement via Pricing Table sur le site externe),
    # on identifie l'utilisateur par son email Stripe.
    if not user_id:
        customer_details = _safe_get(data, "customer_details", {}) or {}
        email = _safe_get(customer_details, "email")
        if email:
            user_id = _get_or_create_user_id_by_email(email)
            # On lie le customer_id pour les futurs webhooks subscription.*
            customer_id = _safe_get(data, "customer")
            if customer_id:
                _link_stripe_customer(user_id, customer_id)

    if not user_id:
        return

    if _safe_get(metadata, "kind") == "payg_export":
        grant_export_credit(
            user_id,
            _safe_get(metadata, "period_label", ""),
            _safe_get(data, "payment_intent", ""),
        )

    elif _safe_get(data, "mode") == "subscription":
        subscription_id = _safe_get(data, "subscription")
        plan = _safe_get(metadata, "plan", "unknown")
        if not subscription_id:
            return
        subscription = stripe.Subscription.retrieve(subscription_id)

        # INFER PLAN : si le plan est inconnu (Pricing Table), on le devine via le price_id
        if plan == "unknown":
            items = _safe_get(subscription, "items", {}) or {}
            items_data = _safe_get(items, "data", []) or []
            if items_data:
                price_id = _safe_get(_safe_get(items_data[0], "price", {}), "id")
                if price_id:
                    # Comparaison avec les prix configurés en variables d'env
                    if price_id in (_env("STRIPE_PRICE_SUB_BUSINESS_MONTHLY"), _env("STRIPE_PRICE_SUB_BUSINESS_YEARLY")):
                        plan = "business"
                    elif price_id in (_env("STRIPE_PRICE_SUB_CABINET_MONTHLY"), _env("STRIPE_PRICE_SUB_CABINET_YEARLY")):
                        plan = "cabinet"

        quantity, interval, period_end = _extract_subscription_item_details(subscription)
        if period_end is None:
            raise RuntimeError(
                f"current_period_end introuvable (ni sur l'item, ni sur la Subscription "
                f"{subscription_id}) — vérifier un éventuel changement de schéma côté API Stripe."
            )
        _upsert_subscription(
            user_id=user_id,
            stripe_subscription_id=subscription_id,
            status=_safe_get(subscription, "status"),
            plan=plan,
            current_period_end=float(period_end),
            billing_interval=interval,
            siren_quantity=quantity,
        )


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
        print(
            f"[stripe_webhook] Paiement différé échoué/expiré — "
            f"user_id={_safe_get(metadata, 'user_id')} "
            f"session={_safe_get(data, 'id')}"
        )

    elif etype in ("customer.subscription.created", "customer.subscription.updated"):
        customer_id = _safe_get(data, "customer")
        user_id = _user_id_for_stripe_customer(customer_id)

        # FALLBACK : si l'id client Stripe n'est pas encore lié (ex: abonnement direct),
        # on récupère l'email du client pour trouver l'utilisateur.
        if not user_id:
            try:
                # On évite de bloquer tout le traitement si un retrieve échoue (réseau)
                customer = stripe.Customer.retrieve(customer_id)
                email = _safe_get(customer, "email")
                if email:
                    user_id = _get_or_create_user_id_by_email(email)
                    _link_stripe_customer(user_id, customer_id)
            except Exception:
                pass

        if not user_id:
            return
        plan = _safe_get(_safe_get(data, "metadata") or {}, "plan", "unknown")
        quantity, interval, period_end = _extract_subscription_item_details(data)
        if period_end is None:
            raise RuntimeError(
                f"current_period_end introuvable (ni sur l'item, ni sur la Subscription "
                f"{_safe_get(data, 'id')}) — vérifier un éventuel changement de schéma côté API Stripe."
            )
        _upsert_subscription(
            user_id=user_id,
            stripe_subscription_id=_safe_get(data, "id"),
            status=_safe_get(data, "status"),
            plan=plan,
            current_period_end=float(period_end),
            billing_interval=interval,
            siren_quantity=quantity,
        )

    elif etype == "customer.subscription.deleted":
        customer_id = _safe_get(data, "customer")
        user_id = _user_id_for_stripe_customer(customer_id)
        if not user_id:
            return

        def _fn(conn, cur):
            cur.execute(
                "UPDATE tva_subscriptions SET status='canceled', updated_at=%s WHERE user_id=%s",
                (time.time(), user_id),
            )
            conn.commit()

        _run(_fn)