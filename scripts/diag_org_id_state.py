"""Diagnostic (LECTURE SEULE) de l'état de la migration org_id sur les 4 tables
de facturation (tva_customers, tva_subscriptions, tva_export_credits,
tva_siren_registrations).

Objectif : avant de corriger `_migrate_billing_to_org_id` dans billing.py,
vérifier précisément l'état actuel de la base "main" :
  - la colonne org_id existe-t-elle déjà sur chaque table ?
  - si oui, combien de lignes ont org_id NULL (backfill manquant) ?
  - combien de lignes au total, et le user_id est-il bien renseigné pour
    permettre le backfill proposé (org_id = user_id) ?
  - les contraintes de PK "_org_pkey" existent-elles déjà (migration PK
    partiellement appliquée) ?

Ce script NE MODIFIE RIEN (session en readonly, autocommit). À lancer
manuellement, jamais dans l'app ni comme tâche de fond :

    SUPABASE_DB_URL="postgresql://...main..." python scripts_diag_org_id_state.py

Aucun impact sur le scale-to-zero : connexion ouverte puis fermée
explicitement, script ponctuel.
"""
from __future__ import annotations

import os
import sys

import psycopg2

TABLES = (
    "tva_customers",
    "tva_subscriptions",
    "tva_export_credits",
    "tva_siren_registrations",
)

PK_MIGRATIONS = {
    "tva_customers": ("tva_customers_pkey", "tva_customers_org_pkey"),
    "tva_subscriptions": ("tva_subscriptions_pkey", "tva_subscriptions_org_pkey"),
    "tva_export_credits": ("tva_export_credits_pkey", "tva_export_credits_org_pkey"),
    "tva_siren_registrations": (
        "tva_siren_registrations_pkey",
        "tva_siren_registrations_org_pkey",
    ),
}


def _dsn() -> str:
    dsn = os.environ.get("SUPABASE_DB_URL", "")
    if not dsn:
        print("ERREUR : variable d'environnement SUPABASE_DB_URL non définie.", file=sys.stderr)
        sys.exit(1)
    return dsn


def _column_exists(cur, table: str, column: str) -> bool:
    cur.execute(
        """
        SELECT 1 FROM information_schema.columns
        WHERE table_name=%s AND column_name=%s
        """,
        (table, column),
    )
    return cur.fetchone() is not None


def _constraint_exists(cur, table: str, constraint: str) -> bool:
    cur.execute(
        """
        SELECT 1 FROM information_schema.table_constraints
        WHERE table_name=%s AND constraint_name=%s
        """,
        (table, constraint),
    )
    return cur.fetchone() is not None


def main() -> None:
    conn = psycopg2.connect(_dsn())
    conn.set_session(readonly=True, autocommit=True)
    cur = conn.cursor()

    print("=" * 78)
    print("DIAGNOSTIC ETAT MIGRATION org_id (lecture seule)")
    print("=" * 78)

    for table in TABLES:
        print(f"\n--- {table} ---")

        cur.execute(
            "SELECT 1 FROM information_schema.tables WHERE table_name=%s",
            (table,),
        )
        if not cur.fetchone():
            print("    Table introuvable (pas encore créée sur cette base ?).")
            continue

        has_org_id = _column_exists(cur, table, "org_id")
        has_user_id = _column_exists(cur, table, "user_id")
        print(f"    colonne org_id présente : {has_org_id}")
        print(f"    colonne user_id présente : {has_user_id}")

        cur.execute(f"SELECT COUNT(*) FROM {table}")
        total = cur.fetchone()[0]
        print(f"    lignes au total : {total}")

        if has_org_id:
            cur.execute(f"SELECT COUNT(*) FROM {table} WHERE org_id IS NULL")
            null_org = cur.fetchone()[0]
            print(f"    lignes avec org_id IS NULL : {null_org}")

        if has_user_id:
            cur.execute(f"SELECT COUNT(*) FROM {table} WHERE user_id IS NULL")
            null_user = cur.fetchone()[0]
            print(f"    lignes avec user_id IS NULL : {null_user} "
                  f"(problématique pour un backfill org_id = user_id si > 0)")

        old_pk, new_pk = PK_MIGRATIONS[table]
        has_old_pk = _constraint_exists(cur, table, old_pk)
        has_new_pk = _constraint_exists(cur, table, new_pk)
        print(f"    contrainte PK ancienne ({old_pk}) présente : {has_old_pk}")
        print(f"    contrainte PK org ({new_pk}) présente : {has_new_pk}")

    print("\n" + "=" * 78)
    print("Fin du diagnostic. Aucune donnée modifiée.")
    print("=" * 78)

    cur.close()
    conn.close()


if __name__ == "__main__":
    main()
