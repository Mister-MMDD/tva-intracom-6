"""Migration unique : cache VIES SQLite (ancienne base locale, commune de
facto à tous les comptes) → Postgres/Supabase (nouvelle architecture à 3
niveaux : vies_global_cache / vies_scope_cache / vies_manual_overrides /
vies_check_history).

À exécuter UNE SEULE FOIS, depuis un environnement ayant accès à la fois :
  - à l'ancien fichier vies_cache.db (copié depuis le repo / Streamlit Cloud)
  - à la variable d'environnement SUPABASE_DB_URL

Répartition des données migrées :

  vies_cache (SQLite)          → vies_global_cache (Postgres)
      Ces vérifications étaient de facto partagées entre tous les comptes
      (une seule base pour tout le monde) : elles sont donc migrées telles
      quelles dans le cache GLOBAL mutualisé — pas dans un scope particulier.
      Chaque scope les redécouvrira naturellement au premier accès (cascade
      scope → global → API), avec sa propre entrée d'historique horodatée
      à ce moment-là.

  vies_manual_overrides (SQLite) → vies_manual_overrides (Postgres, scopées)
      IMPORTANT : contrairement aux vérifications automatiques, les
      classifications MANUELLES ne doivent JAMAIS être mutualisées. Comme
      l'ancienne base ne conservait aucune trace du compte à l'origine de
      chaque override, ce script ne peut PAS deviner à qui elles
      appartiennent — c'est une décision produit, pas une déduction de code.
      D'où l'argument obligatoire --legacy-scope-id : toutes les
      classifications manuelles existantes seront rattachées à ce scope
      unique que tu choisis explicitement (ex. le scope de l'unique compte
      utilisé jusqu'ici). Si plusieurs comptes doivent en réalité se
      partager des overrides différents, NE PAS lancer ce script tel quel —
      me fournir la table de correspondance vat → compte à la place.

  (aucun équivalent SQLite pour vies_check_history : l'ancienne base ne
  conservait pas systématiquement une trace distincte de l'historique dans
  tous les déploiements. Si un fichier d'historique existe, voir la
  fonction migrate_history() ci-dessous, désactivée par défaut.)

Usage :
    python migrate_vies_to_postgres.py \\
        --sqlite-path /chemin/vers/vies_cache.db \\
        --legacy-scope-id "domain:tondomaine.fr" \\
        [--dry-run]
"""
from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from datetime import datetime, timezone

import psycopg2


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--sqlite-path", required=True, help="Chemin vers l'ancien vies_cache.db")
    p.add_argument("--legacy-scope-id", required=True,
                   help="Scope auquel rattacher les overrides manuels existants "
                        "(ex: 'domain:tondomaine.fr' ou 'user:toi@exemple.fr'). "
                        "Décision produit — voir docstring du script.")
    p.add_argument("--dry-run", action="store_true",
                   help="N'écrit rien, affiche seulement ce qui serait migré.")
    return p.parse_args()


def _get_pg_dsn() -> str:
    dsn = os.environ.get("SUPABASE_DB_URL")
    if not dsn:
        print("ERREUR : SUPABASE_DB_URL non définie dans l'environnement.", file=sys.stderr)
        sys.exit(1)
    return dsn


def _ensure_schema(pg_conn) -> None:
    """Recrée le schéma cible si besoin (idempotent — mêmes DDL que vies.py)."""
    with pg_conn, pg_conn.cursor() as cur:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS vies_global_cache (
                vat_id TEXT PRIMARY KEY, valid BOOLEAN NOT NULL,
                country_code TEXT NOT NULL, vat_number TEXT NOT NULL,
                name TEXT DEFAULT '', address TEXT DEFAULT '', error TEXT DEFAULT '',
                checked_at TIMESTAMPTZ NOT NULL
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS vies_scope_cache (
                scope_id TEXT NOT NULL, vat_id TEXT NOT NULL, valid BOOLEAN NOT NULL,
                country_code TEXT NOT NULL, vat_number TEXT NOT NULL,
                name TEXT DEFAULT '', address TEXT DEFAULT '', error TEXT DEFAULT '',
                checked_at TIMESTAMPTZ NOT NULL, PRIMARY KEY (scope_id, vat_id)
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS vies_check_history (
                id BIGSERIAL PRIMARY KEY, scope_id TEXT NOT NULL, vat_id TEXT NOT NULL,
                valid BOOLEAN NOT NULL, country_code TEXT NOT NULL, vat_number TEXT NOT NULL,
                name TEXT DEFAULT '', address TEXT DEFAULT '', error TEXT DEFAULT '',
                checked_at TIMESTAMPTZ NOT NULL
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS vies_manual_overrides (
                scope_id TEXT NOT NULL, full_vat TEXT NOT NULL, is_valid BOOLEAN NOT NULL,
                set_at TIMESTAMPTZ NOT NULL, PRIMARY KEY (scope_id, full_vat)
            )
        """)


def _parse_sqlite_dt(s: str | None) -> datetime:
    if not s:
        return datetime.now(timezone.utc)
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return datetime.now(timezone.utc)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def migrate_automatic_cache(sqlite_conn, pg_conn, dry_run: bool) -> int:
    """vies_cache (SQLite, table partagée de facto) → vies_global_cache (Postgres)."""
    sqlite_conn.row_factory = sqlite3.Row
    rows = sqlite_conn.execute(
        "SELECT vat_id, valid, country_code, vat_number, name, address, error, checked_at "
        "FROM vies_cache"
    ).fetchall()
    print(f"[cache automatique] {len(rows)} entrée(s) trouvée(s) dans vies_cache (SQLite).")
    if dry_run:
        return len(rows)
    with pg_conn, pg_conn.cursor() as cur:
        for r in rows:
            cur.execute("""
                INSERT INTO vies_global_cache
                    (vat_id, valid, country_code, vat_number, name, address, error, checked_at)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (vat_id) DO NOTHING
            """, (
                r["vat_id"], bool(r["valid"]), r["country_code"], r["vat_number"],
                r["name"] or "", r["address"] or "", r["error"] or "",
                _parse_sqlite_dt(r["checked_at"]),
            ))
    return len(rows)


def migrate_manual_overrides(sqlite_conn, pg_conn, legacy_scope_id: str, dry_run: bool) -> int:
    """vies_manual_overrides (SQLite, sans scope) → vies_manual_overrides (Postgres, scopées)."""
    sqlite_conn.row_factory = sqlite3.Row
    try:
        rows = sqlite_conn.execute(
            "SELECT full_vat, is_valid, set_at FROM vies_manual_overrides"
        ).fetchall()
    except sqlite3.OperationalError:
        print("[overrides manuels] Table vies_manual_overrides absente de la base SQLite — rien à migrer.")
        return 0
    print(f"[overrides manuels] {len(rows)} entrée(s) trouvée(s) — "
          f"rattachées au scope '{legacy_scope_id}'.")
    if dry_run:
        return len(rows)
    with pg_conn, pg_conn.cursor() as cur:
        for r in rows:
            cur.execute("""
                INSERT INTO vies_manual_overrides (scope_id, full_vat, is_valid, set_at)
                VALUES (%s,%s,%s,%s)
                ON CONFLICT (scope_id, full_vat) DO NOTHING
            """, (legacy_scope_id, r["full_vat"], bool(r["is_valid"]), _parse_sqlite_dt(r["set_at"])))
    return len(rows)


def main() -> None:
    args = _parse_args()
    if not os.path.exists(args.sqlite_path):
        print(f"ERREUR : fichier introuvable : {args.sqlite_path}", file=sys.stderr)
        sys.exit(1)

    sqlite_conn = sqlite3.connect(args.sqlite_path)
    pg_conn = psycopg2.connect(_get_pg_dsn())

    try:
        if not args.dry_run:
            _ensure_schema(pg_conn)

        n_cache = migrate_automatic_cache(sqlite_conn, pg_conn, args.dry_run)
        n_overrides = migrate_manual_overrides(sqlite_conn, pg_conn, args.legacy_scope_id, args.dry_run)

        if args.dry_run:
            print(f"\n[DRY RUN] Rien n'a été écrit. {n_cache} entrée(s) cache + "
                  f"{n_overrides} override(s) seraient migrées.")
        else:
            print(f"\nMigration terminée : {n_cache} entrée(s) cache automatique → vies_global_cache, "
                  f"{n_overrides} override(s) manuel(s) → vies_manual_overrides (scope='{args.legacy_scope_id}').")
    finally:
        sqlite_conn.close()
        pg_conn.close()


if __name__ == "__main__":
    main()
