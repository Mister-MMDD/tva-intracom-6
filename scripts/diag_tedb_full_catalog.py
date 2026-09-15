#!/usr/bin/env python3
"""Script de diagnostic AUTONOME pour l'API SOAP TEDB (Commission europeenne).

Complémentaire de diag_tedb.py (qui affiche le detail brut d'UN pays sans
filtre). Ici, on boucle sur TOUS les territoires TEDB supportes par le
projet (meme liste que vat_rates_db._TEDB_SUPPORTED, dupliquee ici a
dessein -- ce script est volontairement independant du package
tva_intracom, meme raison que diag_tedb.py : pouvoir tourner n'importe ou
ayant acces reseau a ec.europa.eu, sans toucher au cache Postgres ni a
VAT_DYNAMIC_TEDB_ENABLED) et on consolide TOUTES les entrees dont
type != STANDARD (REDUCED, SUPER_REDUCED, PARKING, ZERO...) dans un CSV
unique, pour construire/valider _CATEGORY_TO_TEDB a partir de donnees
reelles plutot que de la documentation TEDB seule.

Usage :
    python diag_tedb_full_catalog.py 2026-01-01
    python diag_tedb_full_catalog.py 2026-01-01 --countries FR,DE,ES
    python diag_tedb_full_catalog.py 2026-01-01 --out mon_fichier.csv
    python diag_tedb_full_catalog.py 2026-01-01 --delay 1.5   # espacement entre requetes

Sortie :
    - un CSV (par defaut tedb_reduced_catalog_<date>.csv) avec une ligne
      par entree vatRateResults non-STANDARD trouvee, colonnes :
      country, vat_type, category_id, rate_type, rate_value, comment
    - a l'ecran, un recapitulatif : liste des category_id distincts vus,
      et pour chacun la liste des pays ou il apparait
"""
from __future__ import annotations

import argparse
import csv
import sys
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from collections import defaultdict
from datetime import date

TEDB_ENDPOINT = "https://ec.europa.eu/taxation_customs/tedb/ws/VatRetrievalService"
TEDB_SOAP_ACTION = (
    "urn:ec.europa.eu:taxud:tedb:services:v1:VatRetrievalService/RetrieveVatRates"
)

# Duplique volontairement vat_rates_db._TEDB_SUPPORTED (voir docstring module).
# A tenir synchronise manuellement si la liste change cote prod.
TEDB_SUPPORTED = [
    "AT", "BE", "BG", "CY", "CZ", "DE", "DK", "EE", "EL", "ES", "FI", "FR",
    "HR", "HU", "IE", "IT", "LT", "LU", "LV", "MT", "NL", "PL", "PT", "RO",
    "SE", "SI", "SK", "XI",
]


def build_soap_request(iso_code: str, situation_on: str) -> bytes:
    xml_body = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/" '
        'xmlns:urn="urn:ec.europa.eu:taxud:tedb:services:v1:IVatRetrievalService" '
        'xmlns:urn1="urn:ec.europa.eu:taxud:tedb:services:v1:IVatRetrievalService:types">'
        '<soapenv:Header/>'
        '<soapenv:Body>'
        '<urn:retrieveVatRatesReqMsg>'
        '<urn1:memberStates>'
        f'<urn1:isoCode>{iso_code}</urn1:isoCode>'
        '</urn1:memberStates>'
        f'<urn1:situationOn>{situation_on}</urn1:situationOn>'
        '</urn:retrieveVatRatesReqMsg>'
        '</soapenv:Body>'
        '</soapenv:Envelope>'
    )
    return xml_body.encode("utf-8")


def fetch_raw(iso_code: str, situation_on: str) -> bytes:
    req = urllib.request.Request(
        TEDB_ENDPOINT,
        data=build_soap_request(iso_code, situation_on),
        method="POST",
        headers={
            "Content-Type": "text/xml; charset=utf-8",
            "SOAPAction": TEDB_SOAP_ACTION,
        },
    )
    with urllib.request.urlopen(req, timeout=20) as resp:
        return resp.read()


def local_tag(tag: str) -> str:
    return tag.split("}")[-1] if "}" in tag else tag


def extract_non_standard_entries(raw: bytes) -> list[dict[str, str]]:
    """Retourne toutes les entrees vatRateResults dont type != STANDARD,
    sans filtre supplementaire (contrairement a vat_rates_db._parse_tedb_response
    qui ne garde que rate.type in (DEFAULT, EXEMPTED) -- ici on veut TOUT
    voir, y compris ce qui serait normalement rejete, pour audit complet)."""
    root = ET.fromstring(raw)
    rows: list[dict[str, str]] = []

    for elem in root.iter():
        if local_tag(elem.tag) != "vatRateResults":
            continue

        vtype = rtype = rvalue = cat_id = comment = ""
        for child in elem:
            tag = local_tag(child.tag)
            if tag == "type":
                vtype = (child.text or "").strip().upper()
            elif tag == "rate":
                for rc in child:
                    rtag = local_tag(rc.tag)
                    if rtag == "type":
                        rtype = (rc.text or "").strip().upper()
                    elif rtag == "value":
                        rvalue = (rc.text or "").strip()
            elif tag == "category":
                for cc in child:
                    if local_tag(cc.tag) == "identifier":
                        cat_id = (cc.text or "").strip().upper()
            elif tag == "comment":
                comment = (child.text or "").strip()

        if vtype and vtype != "STANDARD":
            rows.append({
                "vat_type": vtype,
                "category_id": cat_id,
                "rate_type": rtype,
                "rate_value": rvalue,
                "comment": comment,
            })
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("situation_date", help="Date au format YYYY-MM-DD")
    parser.add_argument("--countries", help="Liste de codes ISO separes par virgule (defaut : tous les territoires TEDB)", default=None)
    parser.add_argument("--out", help="Chemin du CSV de sortie", default=None)
    parser.add_argument("--delay", type=float, default=1.0, help="Delai en secondes entre chaque requete pays (defaut 1.0, courtoisie envers le service TEDB)")
    args = parser.parse_args()

    date.fromisoformat(args.situation_date)  # valide le format, echoue vite sinon

    countries = (
        [c.strip().upper() for c in args.countries.split(",") if c.strip()]
        if args.countries else TEDB_SUPPORTED
    )
    out_path = args.out or f"tedb_reduced_catalog_{args.situation_date}.csv"

    all_rows: list[dict[str, str]] = []
    failures: list[str] = []

    for i, country in enumerate(countries):
        print(f"[{i+1}/{len(countries)}] {country} ...", file=sys.stderr)
        try:
            raw = fetch_raw(country, args.situation_date)
            entries = extract_non_standard_entries(raw)
            for e in entries:
                e["country"] = country
            all_rows.extend(entries)
            print(f"    -> {len(entries)} entree(s) non-STANDARD", file=sys.stderr)
        except (urllib.error.URLError, urllib.error.HTTPError, ET.ParseError) as exc:
            print(f"    !! echec : {exc}", file=sys.stderr)
            failures.append(country)
        if i < len(countries) - 1:
            time.sleep(args.delay)

    with open(out_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["country", "vat_type", "category_id", "rate_type", "rate_value", "comment"])
        writer.writeheader()
        writer.writerows(all_rows)

    print(f"\n{'='*70}")
    print(f"CSV ecrit : {out_path} ({len(all_rows)} lignes, {len(countries) - len(failures)}/{len(countries)} pays OK)")
    if failures:
        print(f"Echecs : {', '.join(failures)}")

    # Recapitulatif : quels category_id existent, dans quels pays
    by_category: dict[str, set[str]] = defaultdict(set)
    for row in all_rows:
        if row["category_id"]:
            by_category[row["category_id"]].add(row["country"])

    print(f"\n{len(by_category)} category_id distincts trouves :")
    for cat_id in sorted(by_category):
        countries_list = ", ".join(sorted(by_category[cat_id]))
        print(f"  {cat_id:35s} -> {len(by_category[cat_id]):2d} pays : {countries_list}")


if __name__ == "__main__":
    main()
