"""Diagnostic (LECTURE SEULE) avant migration abonnement/SIREN de user_id vers org_id.

Objectif : détecter, pour chaque organisation (org_id) comptant plusieurs
comptes (tva_users), les conflits qui empêcheraient une fusion automatique
sans risque :
  - plusieurs user_id de la même org avec un abonnement Stripe actif/trialing
    en parallèle (lequel garder comme source de vérité ?)
  - plusieurs stripe_customer_id distincts dans la même org
  - des SIREN enregistrés sous des user_id différents de la même org
    (dont d'éventuels doublons du même SIREN sous deux comptes)
  - des crédits export (PAYG) répartis sur plusieurs user_id de la même org

Ce script NE MODIFIE RIEN. Il se contente d'imprimer un rapport texte.
À lancer manuellement (pas dans l'app, pas dans Railway) :

    SUPABASE_DB_URL="postgresql://..." python scripts/diag_org_migration.py

Aucun impact sur Railway scale-to-zero : script ponctuel, connexion fermée
en sortie, ne tourne jamais en tâche de fond.
"""
from __future__ import annotations

import os
import sys

import psycopg2


def _dsn() -> str:
    dsn = os.environ.get("SUPABASE_DB_URL", "")
    if not dsn:
        print("ERREUR : variable d'environnement SUPABASE_DB_URL non définie.", file=sys.stderr)
        sys.exit(1)
    return dsn


def main() -> None:
    conn = psycopg2.connect(_dsn())
    conn.set_session(readonly=True, autocommit=True)
    cur = conn.cursor()

    # 1) Organisations avec plusieurs comptes
    cur.execute(
        """
        SELECT org_id, COUNT(*) AS nb_users
        FROM tva_users
        WHERE org_id IS NOT NULL AND org_id <> ''
        GROUP BY org_id
        HAVING COUNT(*) > 1
        ORDER BY nb_users DESC
        """
    )
    multi_orgs = cur.fetchall()

    print("=" * 78)
    print(f"Organisations multi-comptes : {len(multi_orgs)}")
    print("=" * 78)

    if not multi_orgs:
        print("Aucune organisation avec plusieurs comptes détectée. "
              "La bascule vers org_id sera transparente (org_id == user_id "
              "en pratique pour toutes les orgs 'solo').")
        cur.close()
        conn.close()
        return

    any_conflict = False

    for org_id, nb_users in multi_orgs:
        cur.execute(
            "SELECT id, email, role, created_at FROM tva_users WHERE org_id=%s ORDER BY created_at",
            (org_id,),
        )
        members = cur.fetchall()
        member_ids = [m[0] for m in members]

        print(f"\n--- org_id={org_id!r} ({nb_users} comptes) ---")
        for uid, email, role, created_at in members:
            print(f"    user_id={uid}  email={email}  role={role}")

        # --- Abonnements ---
        cur.execute(
            """
            SELECT user_id, stripe_subscription_id, status, plan, current_period_end
            FROM tva_subscriptions
            WHERE user_id = ANY(%s)
            """,
            (member_ids,),
        )
        subs = cur.fetchall()
        active_subs = [s for s in subs if s[2] in ("active", "trialing", "past_due")]
        if len(active_subs) > 1:
            any_conflict = True
            print(f"    [CONFLIT] {len(active_subs)} abonnements actifs/trialing distincts "
                  f"dans la même org :")
            for uid, sub_id, status, plan, period_end in active_subs:
                print(f"        user_id={uid} sub={sub_id} status={status} plan={plan}")
        elif len(subs) > 1:
            print(f"    (info) {len(subs)} lignes d'abonnement au total (dont au plus 1 active) "
                  f"— pas de conflit direct mais historique à vérifier :")
            for uid, sub_id, status, plan, period_end in subs:
                print(f"        user_id={uid} sub={sub_id} status={status} plan={plan}")

        # --- Clients Stripe ---
        cur.execute(
            "SELECT user_id, stripe_customer_id FROM tva_customers WHERE user_id = ANY(%s)",
            (member_ids,),
        )
        customers = cur.fetchall()
        if len(customers) > 1:
            any_conflict = True
            print(f"    [CONFLIT] {len(customers)} clients Stripe distincts dans la même org :")
            for uid, cust_id in customers:
                print(f"        user_id={uid} stripe_customer_id={cust_id}")

        # --- SIREN ---
        cur.execute(
            "SELECT user_id, siren, company_name IS NOT NULL AS has_name "
            "FROM tva_siren_registrations WHERE user_id = ANY(%s)",
            (member_ids,),
        )
        sirens = cur.fetchall()
        if sirens:
            by_siren: dict[str, list[str]] = {}
            for uid, siren, _has_name in sirens:
                by_siren.setdefault(siren, []).append(uid)
            dup_sirens = {s: uids for s, uids in by_siren.items() if len(uids) > 1}
            print(f"    (info) {len(sirens)} SIREN enregistrés au total dans l'org, "
                  f"répartis sur {len({u for u, *_ in sirens})} compte(s).")
            if dup_sirens:
                any_conflict = True
                print("    [CONFLIT] SIREN enregistrés sous PLUSIEURS user_id différents "
                      "dans la même org :")
                for siren, uids in dup_sirens.items():
                    print(f"        siren={siren} -> user_ids={uids}")

        # --- Crédits export PAYG ---
        cur.execute(
            "SELECT user_id, period_label FROM tva_export_credits WHERE user_id = ANY(%s)",
            (member_ids,),
        )
        credits = cur.fetchall()
        distinct_credit_users = {u for u, _ in credits}
        if len(distinct_credit_users) > 1:
            print(f"    (info) Crédits export PAYG répartis sur {len(distinct_credit_users)} "
                  f"comptes différents de l'org (pas bloquant, juste à fusionner) :")
            for uid, period_label in credits:
                print(f"        user_id={uid} period={period_label}")

    print("\n" + "=" * 78)
    if any_conflict:
        print("RÉSULTAT : au moins un conflit détecté (voir [CONFLIT] ci-dessus).")
        print("Une fusion automatique par script n'est PAS sûre pour ces organisations : "
              "elles nécessitent un arbitrage manuel (quel abonnement garder, quel SIREN "
              "garder) avant toute migration de schéma.")
    else:
        print("RÉSULTAT : aucun conflit bloquant détecté. La migration automatique "
              "(un seul abonnement/SIREN par org dans tous les cas multi-comptes) "
              "peut être envisagée en toute confiance pour la donnée actuelle.")
    print("=" * 78)

    cur.close()
    conn.close()


if __name__ == "__main__":
    main()
