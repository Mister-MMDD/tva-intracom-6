"""Diagnostic perf : où passe le temps de l'étape « Téléchargement des taux (BCE) » ?

Mesure, avec le VRAI service BCE (aucune écriture en base, aucun secret requis) :
  1. la requête batch multi-devises (taille du corps, avec et sans gzip, durée) ;
  2. le même téléchargement devise par devise (pour repérer une devise lente) ;
  3. optionnellement la phase Postgres si une base est configurée (--db) :
     lecture L2 de N paires, avec le nombre d'allers-retours.

Usage (depuis la racine du dépôt) :
    python scripts/diag_bce_perf.py
    python scripts/diag_bce_perf.py --ccy GBP PLN SEK CZK --from 2023-01-01 --to 2026-09-30
    python scripts/diag_bce_perf.py --db

À renvoyer tel quel. Un seul appel HTTP par mesure, puis fin du processus
(aucun thread, aucune connexion persistante : compatible scale-to-zero).
"""
from __future__ import annotations

import argparse
import gzip
import os
import sys
import time
import urllib.request
from datetime import date, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from tva_intracom import ecb_rates as e  # noqa: E402


def _url(ccys: list[str], start: date, end: date) -> str:
    key = "+".join(sorted(ccys))
    return (f"{e.ECB_BASE_URL}/D.{key}.EUR.SP00.A?startPeriod={start.isoformat()}"
            f"&endPeriod={end.isoformat()}&detail=dataonly&format=jsondata")


def _timed_get(url: str, gzip_on: bool) -> tuple[float, int, int, str]:
    headers = {"Accept": "application/json"}
    if gzip_on:
        headers["Accept-Encoding"] = "gzip"
    req = urllib.request.Request(url, headers=headers)
    t0 = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=60, context=e._SSL_CONTEXT) as resp:
            raw = resp.read()
            enc = resp.headers.get("Content-Encoding") or "-"
            wire = len(raw)
            if enc.lower() == "gzip":
                raw = gzip.decompress(raw)
            return time.perf_counter() - t0, wire, len(raw), enc
    except Exception as exc:  # noqa: BLE001
        return time.perf_counter() - t0, 0, 0, f"ERREUR {type(exc).__name__}: {exc}"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ccy", nargs="+", default=["GBP", "PLN", "SEK", "CZK", "USD"])
    ap.add_argument("--from", dest="start", default=(date.today() - timedelta(days=3 * 365)).isoformat())
    ap.add_argument("--to", dest="end", default=date.today().isoformat())
    ap.add_argument("--db", action="store_true", help="mesurer aussi la lecture L2 Postgres")
    a = ap.parse_args()
    start, end = date.fromisoformat(a.start) - timedelta(days=7), date.fromisoformat(a.end)
    ccys = [c.upper() for c in a.ccy]

    print(f"Fenêtre {start} -> {end} | devises {ccys}\n")
    print("== 1. Batch multi-devises (comme prefetch_rates) ==")
    for gz in (False, True):
        dt, wire, size, enc = _timed_get(_url(ccys, start, end), gz)
        print(f"  gzip demandé={gz!s:5}  durée={dt:6.2f}s  octets_réseau={wire:>9}  "
              f"octets_JSON={size:>9}  Content-Encoding={enc}")

    print("\n== 2. Devise par devise (gzip) ==")
    for c in ccys:
        dt, wire, size, enc = _timed_get(_url([c], start, end), True)
        print(f"  {c}: {dt:6.2f}s  réseau={wire:>8}  {enc if enc.startswith('ERREUR') else ''}")

    if a.db:
        print("\n== 3. Lecture L2 Postgres ==")
        pairs = [(ccys[0], start + timedelta(days=i)) for i in range(0, 3000)]
        t0 = time.perf_counter()
        hits = e._db_get_rates_batch(pairs)
        print(f"  3000 paires : {time.perf_counter() - t0:6.2f}s, {len(hits)} trouvées "
              f"(page_size actuel : 2000 -> 2 allers-retours)")
    print("\nFin. Renvoyez cette sortie ; et, depuis l'appli, les lignes de log "
          "« Prefetch BCE durées : lecture_L2=… http_BCE=… écriture_L2=… ».")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
