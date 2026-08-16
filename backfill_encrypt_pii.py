#!/usr/bin/env python3
"""Backfill de chiffrement — tva-intracom-6.

Chiffre (Fernet, security.encrypt_data) toute valeur encore en clair dans :
  - tva_siren_registrations.tva_number
  - tva_siren_registrations.ioss_number
  - tva_siren_registrations.vat_numbers_json
  - tva_amazon_credentials.refresh_token

Objectif : terminer la migration entamée par le patch "audit sécurité —
7 points (2026-08-16)" (voir README - évolution.md), pour pouvoir ensuite
retirer le fail-open de `decrypt_data` (security.py) qui tolère aujourd'hui
les valeurs non chiffrées.

USAGE
-----
Ce script se connecte DIRECTEMENT à la base via SUPABASE_DB_URL (variable
d'environnement) — il ne dépend PAS de Streamlit et peut tourner en
diagnostic/maintenance ponctuelle, en local ou dans un shell Railway.

  # 1. Dry-run (ne modifie RIEN, affiche juste ce qui serait fait) :
  export SUPABASE_DB_URL="postgres://..."
  export ENCRYPTION_KEY="<la clé de prod>"
  python backfill_encrypt_pii.py

  # 2. Application réelle, après avoir vérifié le dry-run :
  python backfill_encrypt_pii.py --apply

Le script est IDEMPOTENT : relancer après application ne trouve plus rien
à faire (toute valeur commençant déjà par "gAAAA" est ignorée). Peut donc
être relancé sans risque pour vérifier qu'il ne reste plus rien en clair.

Il ne touche à AUCUNE ligne dont la valeur est déjà vide/NULL ou déjà
chiffrée. Aucune connexion persistante, aucun thread, aucun polling —
compatible scale-to-zero Railway (le script termine et le process sort).
"""

from __future__ import annotations

import argparse
import os
import sys

import psycopg2

# Le script est pensé pour être copié/exécuté depuis la racine du repo
# (import direct du module de chiffrement du projet, pour rester en tout
# point identique au chiffrement utilisé par l'application).
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    from tva_intracom.security import encrypt_data
except ImportError:
    print(
        "ERREUR : impossible d'importer tva_intracom.security.encrypt_data.\n"
        "Lancez ce script depuis la racine du repo (à côté du dossier "
        "tva_intracom/), avec le même venv/dépendances que l'application.",
        file=sys.stderr,
    )
    sys.exit(1)


def _looks_encrypted(value: str | None) -> bool:
    """Même heuristique que decrypt_data (security.py) : un jeton Fernet
    commence toujours par 'gAAAA'."""
    return bool(value) and value.startswith("gAAAA")


def _connect():
    dsn = os.environ.get("SUPABASE_DB_URL")
    if not dsn:
        print("ERREUR : variable d'environnement SUPABASE_DB_URL absente.", file=sys.stderr)
        sys.exit(1)
    return psycopg2.connect(dsn)


def backfill_siren_registrations(conn, apply: bool) -> dict:
    """Chiffre tva_number / ioss_number / vat_numbers_json en clair."""
    stats = {"rows_scanned": 0, "rows_updated": 0, "fields_encrypted": 0}
    with conn.cursor() as cur:
        cur.execute(
            "SELECT user_id, siren, tva_number, ioss_number, vat_numbers_json "
            "FROM tva_siren_registrations"
        )
        rows = cur.fetchall()

    for user_id, siren, tva_number, ioss_number, vat_numbers_json in rows:
        stats["rows_scanned"] += 1
        updates = {}

        if tva_number and not _looks_encrypted(tva_number):
            updates["tva_number"] = encrypt_data(tva_number)
        if ioss_number and not _looks_encrypted(ioss_number):
            updates["ioss_number"] = encrypt_data(ioss_number)
        if vat_numbers_json and not _looks_encrypted(vat_numbers_json):
            updates["vat_numbers_json"] = encrypt_data(vat_numbers_json)

        if not updates:
            continue

        stats["rows_updated"] += 1
        stats["fields_encrypted"] += len(updates)

        print(
            f"  [tva_siren_registrations] user_id={user_id} siren={siren} "
            f"-> chiffrement de : {', '.join(updates.keys())}"
            + ("" if apply else "  (dry-run, aucune écriture)")
        )

        if apply:
            set_clause = ", ".join(f"{col} = %s" for col in updates)
            with conn.cursor() as cur:
                cur.execute(
                    f"UPDATE tva_siren_registrations SET {set_clause} "
                    f"WHERE user_id = %s AND siren = %s",
                    (*updates.values(), user_id, siren),
                )

    if apply:
        conn.commit()
    return stats


def backfill_amazon_credentials(conn, apply: bool) -> dict:
    """Chiffre refresh_token en clair."""
    stats = {"rows_scanned": 0, "rows_updated": 0}
    with conn.cursor() as cur:
        cur.execute("SELECT user_id, refresh_token FROM tva_amazon_credentials")
        rows = cur.fetchall()

    for user_id, refresh_token in rows:
        stats["rows_scanned"] += 1
        if not refresh_token or _looks_encrypted(refresh_token):
            continue

        stats["rows_updated"] += 1
        print(
            f"  [tva_amazon_credentials] user_id={user_id} "
            f"-> chiffrement de : refresh_token"
            + ("" if apply else "  (dry-run, aucune écriture)")
        )

        if apply:
            encrypted = encrypt_data(refresh_token)
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE tva_amazon_credentials SET refresh_token = %s WHERE user_id = %s",
                    (encrypted, user_id),
                )

    if apply:
        conn.commit()
    return stats


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply", action="store_true",
        help="Applique réellement les UPDATE. Sans ce flag : dry-run (lecture seule).",
    )
    args = parser.parse_args()

    if not os.environ.get("ENCRYPTION_KEY"):
        print("ERREUR : variable d'environnement ENCRYPTION_KEY absente.", file=sys.stderr)
        sys.exit(1)

    mode = "APPLICATION RÉELLE" if args.apply else "DRY-RUN (lecture seule, rien n'est écrit)"
    print(f"=== Backfill chiffrement PII — mode : {mode} ===\n")

    conn = _connect()
    try:
        print("-- tva_siren_registrations --")
        s1 = backfill_siren_registrations(conn, args.apply)
        print(
            f"  {s1['rows_scanned']} lignes scannées, "
            f"{s1['rows_updated']} lignes à mettre à jour, "
            f"{s1['fields_encrypted']} champs concernés.\n"
        )

        print("-- tva_amazon_credentials --")
        s2 = backfill_amazon_credentials(conn, args.apply)
        print(
            f"  {s2['rows_scanned']} lignes scannées, "
            f"{s2['rows_updated']} lignes à mettre à jour.\n"
        )
    finally:
        conn.close()

    if not args.apply and (s1["rows_updated"] or s2["rows_updated"]):
        print(
            "Dry-run terminé. Relancez avec --apply pour appliquer réellement "
            "ces changements."
        )
    elif not s1["rows_updated"] and not s2["rows_updated"]:
        print(
            "Rien à faire : toutes les valeurs concernées sont déjà chiffrées. "
            "Le fail-open de decrypt_data (security.py) peut être retiré en toute "
            "sécurité pour ces colonnes."
        )
    else:
        print("Application terminée.")


if __name__ == "__main__":
    main()
