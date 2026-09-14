"""Script d'extraction des taux de TVA historiques (2000-Présent) et mise à jour de rates.py.

Source : Sourced from official EC records (via Vatnode/TEDB).
"""

import json
import os
import re
import urllib.request
from datetime import date
from decimal import Decimal
from typing import List, NamedTuple, Optional

# Configuration
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
RATES_PY_PATH = os.path.join(PROJECT_ROOT, "tva_intracom", "rates.py")
VATNODE_URL = "https://cdn.jsdelivr.net/gh/vatnode/eu-vat-rates-data@main/data/eu-vat-rates-history.json"

EU_COUNTRIES_ISO = {
    "AT", "BE", "BG", "HR", "CY", "CZ", "DK", "EE", "FI", "FR", "DE", "GR",
    "HU", "IE", "IT", "LV", "LT", "LU", "MT", "NL", "PL", "PT", "RO", "SK",
    "SI", "ES", "SE", "MC", "XI", "GB"
}

class VatPeriod(NamedTuple):
    country: str
    date_from: str
    date_to: Optional[str]
    rate: str
    category: str = "STANDARD"

def fetch_history() -> List[VatPeriod]:
    print(f"Fetching history from {VATNODE_URL}...")
    with urllib.request.urlopen(VATNODE_URL) as response:
        data = json.loads(response.read().decode())

    history = []
    start_date_limit = "2000-01-01"

    history_data = data.get("history", {})

    for iso_code, entry in history_data.items():
        country_name = entry.get("country")
        iso = iso_code.upper()
        # On ne garde que les pays de l'UE
        if iso not in EU_COUNTRIES_ISO:
            continue

        periods = entry.get("periods", [])
        for r in periods:
            # On prend toutes les périodes depuis 2000
            effective_from = r.get("from")
            effective_to = r.get("to")
            rate_value = r.get("standard")
            if rate_value is None:
                continue
            
            rate_value = str(rate_value)

            # Troncature à 2000-01-01 si la période a commencé avant
            if effective_from < start_date_limit:
                if effective_to and effective_to < start_date_limit:
                    continue # Période entièrement avant 2000
                effective_from = start_date_limit
            
            history.append(VatPeriod(iso, effective_from, effective_to, rate_value))

    # Cas spéciaux
    # 1. Monaco (MC) suit la France (FR)
    fr_periods = [p for p in history if p.country == "FR"]
    for p in fr_periods:
        history.append(VatPeriod("MC", p.date_from, p.date_to, p.rate))
    
    # 2. Irlande du Nord (XI) suit le Royaume-Uni (GB)
    gb_periods = [p for p in history if p.country == "GB"]
    for p in gb_periods:
        history.append(VatPeriod("XI", p.date_from, p.date_to, p.rate))

    # On retire GB du résultat final car hors UE fiscale maintenant (sauf XI)
    history = [p for p in history if p.country != "GB"]

    print(f"Fetched {len(history)} total standard rate periods.")
    return sorted(history, key=lambda x: (x.country, x.date_from))

def update_rates_py(new_standard_history: List[VatPeriod]):
    with open(RATES_PY_PATH, "r", encoding="utf-8") as f:
        content = f.read()

    # On préserve les taux REDUCED existants dans VAT_RATE_HISTORY
    # On va extraire le bloc VAT_RATE_HISTORY
    match = re.search(r"VAT_RATE_HISTORY: List\[_VatPeriod\] = \[(.*?)\]", content, re.DOTALL)
    if not match:
        print("Could not find VAT_RATE_HISTORY block in rates.py")
        return

    old_block = match.group(1)
    # Extraction des entrées non-STANDARD (FOOD, BOOKS, MEDICINES, etc.)
    # Exemple: _VatPeriod("EE", date(2024, 1, 1), date(2025, 6, 30), Decimal("22"), "FOOD"),
    reduced_entries = []
    for line in old_block.split("\n"):
        if '"STANDARD"' not in line and 'STANDARD' not in line and '_VatPeriod' in line:
            reduced_entries.append(line.strip())

    # Génération du nouveau bloc
    new_lines = []
    current_country = None
    for p in new_standard_history:
        if p.country != current_country:
            new_lines.append(f"    # --- {p.country} ---")
            current_country = p.country
        
        # Remove leading zeros from date components to avoid Python syntax errors
        y, m, d = p.date_from.split('-')
        d_from = f"date({int(y)}, {int(m)}, {int(d)})"
        
        if p.date_to:
            y2, m2, d2 = p.date_to.split('-')
            d_to = f"date({int(y2)}, {int(m2)}, {int(d2)})"
        else:
            d_to = "None"
            
        new_lines.append(f'    _VatPeriod("{p.country}", {d_from:17}, {d_to:17}, Decimal("{p.rate}"), "STANDARD"),')

    # Ajout des taux réduits préservés (on les trie par pays à la fin ou on les insère au bon endroit)
    # Pour faire simple, on les ajoute à la fin du bloc
    if reduced_entries:
        new_lines.append("")
        new_lines.append("    # --- Taux réduits historiques (préservés) ---")
        new_lines.extend(reduced_entries)

    new_block = "\n".join(new_lines)
    new_content = content.replace(old_block, "\n" + new_block + "\n")

    # Mise à jour du docstring/commentaire de début
    new_content = re.sub(
        r"VAT_RATE_HISTORY \(taux historiques depuis le 01/01/2024 pour toutes catégories\)",
        "VAT_RATE_HISTORY (taux historiques depuis le 01/01/2000)",
        new_content
    )
    new_content = re.sub(
        r"Périmètre historique : à partir du 01/01/2024",
        "Périmètre historique : à partir du 01/01/2000",
        new_content
    )

    with open(RATES_PY_PATH, "w", encoding="utf-8") as f:
        f.write(new_content)
    
    print(f"Successfully updated {RATES_PY_PATH}")

if __name__ == "__main__":
    history = fetch_history()
    update_rates_py(history)
