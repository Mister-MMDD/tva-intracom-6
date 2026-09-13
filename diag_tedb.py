#!/usr/bin/env python3
"""Script de diagnostic AUTONOME pour l'API SOAP TEDB (Commission europeenne).

But : observer la reponse XML BRUTE de TEDB pour un pays/date donnes, sans
passer par vat_rates_db.py, sans toucher au cache Postgres, sans activer
VAT_DYNAMIC_TEDB_ENABLED. Zero dependance au package tva_intracom : ce
script est volontairement independant pour pouvoir tourner n'importe ou
(poste local, Cloud Shell, Codespace...) ayant acces reseau a ec.europa.eu.

Usage :
    python diag_tedb.py ES 2026-01-01
    python diag_tedb.py ES 2026-01-01 --country2 FR   # comparaison

Sorties :
    - un fichier <pays>_<date>.xml contenant la reponse brute complete
    - a l'ecran, le detail de CHAQUE entree vatRateResults trouvee (aucun
      filtre applique - contrairement a _parse_tedb_response en prod, ici
      on veut TOUT voir, y compris ce qui serait normalement rejete)
"""
from __future__ import annotations

import sys
import argparse
import urllib.request
import urllib.error
import xml.etree.ElementTree as ET
from datetime import date

TEDB_ENDPOINT = "https://ec.europa.eu/taxation_customs/tedb/ws/VatRetrievalService"
TEDB_SOAP_ACTION = (
    "urn:ec.europa.eu:taxud:tedb:services:v1:VatRetrievalService/RetrieveVatRates"
)


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
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return resp.read()
    except urllib.error.HTTPError as exc:
        body = exc.read()
        print(f"!! HTTP {exc.code} — corps de la reponse d'erreur ci-dessous :", file=sys.stderr)
        print(body.decode("utf-8", errors="replace"), file=sys.stderr)
        raise
    except urllib.error.URLError as exc:
        print(f"!! Erreur reseau : {exc}", file=sys.stderr)
        raise


def local_tag(tag: str) -> str:
    return tag.split("}")[-1] if "}" in tag else tag


def dump_all_entries(raw: bytes) -> None:
    """Affiche TOUTES les entrees vatRateResults sans aucun filtre, avec
    tous les champs bruts, pour observation manuelle."""
    root = ET.fromstring(raw)
    count = 0
    for elem in root.iter():
        if local_tag(elem.tag) != "vatRateResults":
            continue
        count += 1
        fields: dict[str, str] = {}
        for child in elem:
            tag = local_tag(child.tag)
            if tag == "rate":
                for rc in child:
                    fields[f"rate.{local_tag(rc.tag)}"] = (rc.text or "").strip()
            elif tag == "category":
                for cc in child:
                    fields[f"category.{local_tag(cc.tag)}"] = (cc.text or "").strip()
            else:
                fields[tag] = (child.text or "").strip()
        print(f"--- vatRateResults #{count} ---")
        for k, v in fields.items():
            print(f"  {k} = {v!r}")
        print()
    if count == 0:
        print("!! Aucune entree <vatRateResults> trouvee dans la reponse. "
              "Verifier si c'est une erreur SOAP Fault (voir XML brut sauvegarde).")
    else:
        print(f"=> {count} entree(s) vatRateResults au total.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("country", help="Code ISO du pays a interroger (ex: ES)")
    parser.add_argument("situation_date", help="Date au format YYYY-MM-DD")
    parser.add_argument("--country2", help="Second pays pour comparaison (ex: FR)", default=None)
    args = parser.parse_args()

    # Validation simple de la date (echoue vite et clairement si mal formee)
    date.fromisoformat(args.situation_date)

    for country in filter(None, [args.country, args.country2]):
        print(f"\n{'='*70}\nRequete TEDB : {country} au {args.situation_date}\n{'='*70}")
        try:
            raw = fetch_raw(country, args.situation_date)
        except Exception:
            print(f"!! Echec pour {country}, passage au suivant si applicable.")
            continue

        out_path = f"{country}_{args.situation_date}.xml"
        with open(out_path, "wb") as f:
            f.write(raw)
        print(f"XML brut sauvegarde dans : {out_path}")
        print()
        dump_all_entries(raw)


if __name__ == "__main__":
    main()
