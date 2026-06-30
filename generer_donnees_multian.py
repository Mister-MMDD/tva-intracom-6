"""Générateur de données de ventes multi-années pour tester le seuil OSS.

Produit un fichier CSV au format Amazon VAT Transactions Report (Format 4)
identique à source_vente.csv, couvrant plusieurs années civiles.

Scénarios générés par année pour tester les cas limites OSS :
  - Ventes B2C intra-UE cross-border (OSS) — réparties pour piloter le cumul
  - Ventes B2C domestiques France
  - Ventes B2B cross-border (reverse charge)
  - Ventes B2B avec NIF national ES/IT (autoliquidation art.194)
  - Avoirs (RETURN)
  - Passage de seuil OSS en cours d'année (vente de franchissement)
  - Reset du cumul au 1er janvier

Usage:
    python generer_donnees_multian.py [--annees 2022 2023 2024] [--output fichier.csv]
    python generer_donnees_multian.py  # produit data/ventes_multian_test.csv
"""

from __future__ import annotations

import argparse
import csv
import random
import sys
import uuid
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from typing import List, Optional

# ---------------------------------------------------------------------------
# Constantes
# ---------------------------------------------------------------------------
SEUIL_OSS = Decimal("10000.00")

# Pays UE disponibles pour les ventes cross-border B2C
_EU_DEST = ["DE", "IT", "ES", "NL", "BE", "PL", "SE", "AT", "PT", "CZ",
            "HU", "RO", "GR", "DK", "FI", "SK", "HR", "LT", "LV", "BG"]

# Numéros TVA fictifs B2B valides par pays (format correct mais fictifs)
_B2B_VAT_BY_COUNTRY = {
    "DE": "DE123456789",
    "IT": "IT12345678901",
    "ES": "ESB12345678",
    "NL": "NL123456789B01",
    "PL": "PL1234567890",
    "BE": "BE0123456789",
    "AT": "ATU12345678",
}

# NIF nationaux ES/IT (sans préfixe, pour tester la détection _is_national_tax_id)
_NATIONAL_TAX_IDS = {
    "ES": ["B65885360", "F99091738", "51235746A"],
    "IT": ["03645930961", "12345678901"],
}

# Taux TVA standard simplifiés (copie légère pour le générateur — pas d'import du moteur)
_VAT_RATES = {
    "FR": Decimal("20"), "DE": Decimal("19"), "IT": Decimal("22"),
    "ES": Decimal("21"), "NL": Decimal("21"), "BE": Decimal("21"),
    "PL": Decimal("23"), "SE": Decimal("25"), "AT": Decimal("20"),
    "PT": Decimal("23"), "CZ": Decimal("21"), "HU": Decimal("27"),
    "RO": Decimal("21"), "GR": Decimal("24"), "DK": Decimal("25"),
    "FI": Decimal("25.5"), "SK": Decimal("23"), "HR": Decimal("25"),
    "LT": Decimal("21"), "LV": Decimal("21"), "BG": Decimal("20"),
}

# Colonnes du format Amazon VAT Transactions Report (Format 4)
# Alignées sur source_vente.csv
_COLUMNS = [
    "UNIQUE_ACCOUNT_IDENTIFIER",
    "ACTIVITY_PERIOD",
    "SALES_CHANNEL",
    "MARKETPLACE",
    "PROGRAM_TYPE",
    "TRANSACTION_TYPE",
    "TRANSACTION_EVENT_ID",
    "ACTIVITY_TRANSACTION_ID",
    "TAX_CALCULATION_DATE",
    "TRANSACTION_DEPART_DATE",
    "TRANSACTION_ARRIVAL_DATE",
    "TRANSACTION_COMPLETE_DATE",
    "SELLER_SKU",
    "ASIN",
    "ITEM_DESCRIPTION",
    "ITEM_MANUFACTURE_COUNTRY",
    "QTY",
    "ITEM_WEIGHT",
    "TOTAL_ACTIVITY_WEIGHT",
    "COST_PRICE_OF_ITEMS",
    "PRICE_OF_ITEMS_AMT_VAT_EXCL",
    "PROMO_PRICE_OF_ITEMS_AMT_VAT_EXCL",
    "TOTAL_PRICE_OF_ITEMS_AMT_VAT_EXCL",
    "SHIP_CHARGE_AMT_VAT_EXCL",
    "PROMO_SHIP_CHARGE_AMT_VAT_EXCL",
    "TOTAL_SHIP_CHARGE_AMT_VAT_EXCL",
    "GIFT_WRAP_AMT_VAT_EXCL",
    "PROMO_GIFT_WRAP_AMT_VAT_EXCL",
    "TOTAL_GIFT_WRAP_AMT_VAT_EXCL",
    "TOTAL_ACTIVITY_VALUE_AMT_VAT_EXCL",
    "PRICE_OF_ITEMS_VAT_RATE_PERCENT",
    "PRICE_OF_ITEMS_VAT_AMT",
    "PROMO_PRICE_OF_ITEMS_VAT_AMT",
    "TOTAL_PRICE_OF_ITEMS_VAT_AMT",
    "SHIP_CHARGE_VAT_RATE_PERCENT",
    "SHIP_CHARGE_VAT_AMT",
    "PROMO_SHIP_CHARGE_VAT_AMT",
    "TOTAL_SHIP_CHARGE_VAT_AMT",
    "GIFT_WRAP_VAT_RATE_PERCENT",
    "GIFT_WRAP_VAT_AMT",
    "PROMO_GIFT_WRAP_VAT_AMT",
    "TOTAL_GIFT_WRAP_VAT_AMT",
    "TOTAL_ACTIVITY_VALUE_VAT_AMT",
    "PRICE_OF_ITEMS_AMT_VAT_INCL",
    "PROMO_PRICE_OF_ITEMS_AMT_VAT_INCL",
    "TOTAL_PRICE_OF_ITEMS_AMT_VAT_INCL",
    "SHIP_CHARGE_AMT_VAT_INCL",
    "PROMO_SHIP_CHARGE_AMT_VAT_INCL",
    "TOTAL_SHIP_CHARGE_AMT_VAT_INCL",
    "GIFT_WRAP_AMT_VAT_INCL",
    "PROMO_GIFT_WRAP_AMT_VAT_INCL",
    "TOTAL_GIFT_WRAP_AMT_VAT_INCL",
    "TOTAL_ACTIVITY_VALUE_AMT_VAT_INCL",
    "TRANSACTION_CURRENCY_CODE",
    "COMMODITY_CODE",
    "STATISTICAL_CODE_DEPART",
    "STATISTICAL_CODE_ARRIVAL",
    "COMMODITY_CODE_SUPPLEMENTARY_UNIT",
    "ITEM_QTY_SUPPLEMENTARY_UNIT",
    "TOTAL_ACTIVITY_SUPPLEMENTARY_UNIT",
    "PRODUCT_TAX_CODE",
    "DEPATURE_CITY",
    "DEPARTURE_COUNTRY",
    "DEPARTURE_POST_CODE",
    "ARRIVAL_CITY",
    "ARRIVAL_COUNTRY",
    "ARRIVAL_POST_CODE",
    "SALE_DEPART_COUNTRY",
    "SALE_ARRIVAL_COUNTRY",
    "TRANSPORTATION_MODE",
    "DELIVERY_CONDITIONS",
    "SELLER_DEPART_VAT_NUMBER_COUNTRY",
    "SELLER_DEPART_COUNTRY_VAT_NUMBER",
    "SELLER_ARRIVAL_VAT_NUMBER_COUNTRY",
    "SELLER_ARRIVAL_COUNTRY_VAT_NUMBER",
    "TRANSACTION_SELLER_VAT_NUMBER_COUNTRY",
    "TRANSACTION_SELLER_VAT_NUMBER",
    "BUYER_VAT_NUMBER_COUNTRY",
    "BUYER_VAT_NUMBER",
    "VAT_CALCULATION_IMPUTATION_COUNTRY",
    "TAXABLE_JURISDICTION",
    "TAXABLE_JURISDICTION_LEVEL",
    "VAT_INV_NUMBER",
    "VAT_INV_CONVERTED_AMT",
    "VAT_INV_CURRENCY_CODE",
    "VAT_INV_EXCHANGE_RATE",
    "VAT_INV_EXCHANGE_RATE_DATE",
    "EXPORT_OUTSIDE_EU",
    "INVOICE_URL",
    "BUYER_NAME",
    "ARRIVAL_ADDRESS",
    "SUPPLIER_NAME",
    "SUPPLIER_VAT_NUMBER",
    "TAX_REPORTING_SCHEME",
    "TAX_COLLECTION_RESPONSIBILITY",
]


# ---------------------------------------------------------------------------
# Dataclass scénario
# ---------------------------------------------------------------------------

@dataclass
class ScenarioSpec:
    """Décrit un scénario de vente à générer."""
    label: str
    tx_type: str           # SHIPMENT ou RETURN
    departure: str         # pays de départ (stock)
    arrival: str           # pays de destination (acheteur)
    amount_ht: Decimal
    buyer_vat: str = ""    # "" = B2C, valeur = B2B
    qty: int = 1
    note: str = ""         # pour le CSV commentaire humain (ITEM_DESCRIPTION)


# ---------------------------------------------------------------------------
# Générateur de lignes
# ---------------------------------------------------------------------------

def _rnd_date(year: int, month_start: int = 1, month_end: int = 12) -> date:
    """Date aléatoire dans l'intervalle [month_start, month_end] pour l'année donnée."""
    start = date(year, month_start, 1)
    if month_end == 12:
        end = date(year, 12, 31)
    else:
        import calendar
        last_day = calendar.monthrange(year, month_end)[1]
        end = date(year, month_end, last_day)
    delta = (end - start).days
    return start + timedelta(days=random.randint(0, delta))


def _fmt_date(d: date) -> str:
    return d.strftime("%Y-%m-%d")


def _vat_amt(ht: Decimal, country: str) -> Decimal:
    rate = _VAT_RATES.get(country, Decimal("20"))
    return (ht * rate / Decimal("100")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _make_row(
    year: int,
    spec: ScenarioSpec,
    seq: int,
    account_id: str = "FR_SELLER_001",
) -> dict:
    """Construit un dict complet prêt à écrire en CSV."""

    tx_date = _rnd_date(year)
    tx_date_str = _fmt_date(tx_date)
    activity_period = tx_date.strftime("%Y-%m")

    amount_ht = spec.amount_ht
    if spec.tx_type == "RETURN":
        amount_ht = -abs(amount_ht)

    vat_rate = _VAT_RATES.get(spec.arrival, Decimal("20"))
    vat_amt = _vat_amt(abs(amount_ht), spec.arrival)
    if amount_ht < 0:
        vat_amt = -vat_amt
    amount_ttc = amount_ht + vat_amt

    tx_id = f"EVT-{year}-{seq:06d}"
    activity_tx_id = f"ACT-{year}-{seq:06d}"
    sku = f"SKU-{seq % 50:04d}"
    asin = f"B{str(seq % 1000).zfill(9)}"

    # Buyer VAT
    buyer_vat_country = spec.buyer_vat[:2] if spec.buyer_vat and len(spec.buyer_vat) >= 2 else ""

    row = {col: "" for col in _COLUMNS}
    row.update({
        "UNIQUE_ACCOUNT_IDENTIFIER":        account_id,
        "ACTIVITY_PERIOD":                  activity_period,
        "SALES_CHANNEL":                    "amazon.fr",
        "MARKETPLACE":                      "amazon.fr",
        "PROGRAM_TYPE":                     "FBA",
        "TRANSACTION_TYPE":                 spec.tx_type,
        "TRANSACTION_EVENT_ID":             tx_id,
        "ACTIVITY_TRANSACTION_ID":          activity_tx_id,
        "TAX_CALCULATION_DATE":             tx_date_str,
        "TRANSACTION_DEPART_DATE":          tx_date_str,
        "TRANSACTION_ARRIVAL_DATE":         tx_date_str,
        "TRANSACTION_COMPLETE_DATE":        tx_date_str,
        "SELLER_SKU":                       sku,
        "ASIN":                             asin,
        "ITEM_DESCRIPTION":                 f"[{spec.label}] {spec.note or 'Article test'}",
        "QTY":                              str(spec.qty),
        "ITEM_WEIGHT":                      "0.5",
        "TOTAL_ACTIVITY_WEIGHT":            str(0.5 * spec.qty),
        # Montants HT
        "PRICE_OF_ITEMS_AMT_VAT_EXCL":     str(amount_ht),
        "TOTAL_PRICE_OF_ITEMS_AMT_VAT_EXCL": str(amount_ht),
        "TOTAL_ACTIVITY_VALUE_AMT_VAT_EXCL": str(amount_ht),
        # TVA
        "PRICE_OF_ITEMS_VAT_RATE_PERCENT":  str(vat_rate),
        "PRICE_OF_ITEMS_VAT_AMT":           str(vat_amt),
        "TOTAL_PRICE_OF_ITEMS_VAT_AMT":     str(vat_amt),
        "TOTAL_ACTIVITY_VALUE_VAT_AMT":     str(vat_amt),
        # Montants TTC
        "PRICE_OF_ITEMS_AMT_VAT_INCL":     str(amount_ttc),
        "TOTAL_PRICE_OF_ITEMS_AMT_VAT_INCL": str(amount_ttc),
        "TOTAL_ACTIVITY_VALUE_AMT_VAT_INCL": str(amount_ttc),
        # Zéros
        "PROMO_PRICE_OF_ITEMS_AMT_VAT_EXCL": "0",
        "SHIP_CHARGE_AMT_VAT_EXCL":         "0",
        "PROMO_SHIP_CHARGE_AMT_VAT_EXCL":   "0",
        "TOTAL_SHIP_CHARGE_AMT_VAT_EXCL":   "0",
        "GIFT_WRAP_AMT_VAT_EXCL":           "0",
        "PROMO_GIFT_WRAP_AMT_VAT_EXCL":     "0",
        "TOTAL_GIFT_WRAP_AMT_VAT_EXCL":     "0",
        "PROMO_PRICE_OF_ITEMS_VAT_AMT":     "0",
        "SHIP_CHARGE_VAT_RATE_PERCENT":     "0",
        "SHIP_CHARGE_VAT_AMT":             "0",
        "PROMO_SHIP_CHARGE_VAT_AMT":        "0",
        "TOTAL_SHIP_CHARGE_VAT_AMT":        "0",
        "GIFT_WRAP_VAT_RATE_PERCENT":       "0",
        "GIFT_WRAP_VAT_AMT":               "0",
        "PROMO_GIFT_WRAP_VAT_AMT":          "0",
        "TOTAL_GIFT_WRAP_VAT_AMT":          "0",
        "PROMO_PRICE_OF_ITEMS_AMT_VAT_INCL": "0",
        "PROMO_SHIP_CHARGE_AMT_VAT_INCL":   "0",
        "TOTAL_SHIP_CHARGE_AMT_VAT_INCL":   "0",
        "GIFT_WRAP_AMT_VAT_INCL":           "0",
        "PROMO_GIFT_WRAP_AMT_VAT_INCL":     "0",
        "TOTAL_GIFT_WRAP_AMT_VAT_INCL":     "0",
        # Devise
        "TRANSACTION_CURRENCY_CODE":        "EUR",
        "VAT_INV_CURRENCY_CODE":            "EUR",
        "VAT_INV_EXCHANGE_RATE":            "1",
        "VAT_INV_EXCHANGE_RATE_DATE":       tx_date_str,
        "VAT_INV_CONVERTED_AMT":            str(amount_ttc),
        # Géographie
        "DEPATURE_CITY":                    "Paris",
        "DEPARTURE_COUNTRY":                spec.departure,
        "DEPARTURE_POST_CODE":              "75001",
        "ARRIVAL_CITY":                     "Berlin" if spec.arrival == "DE" else "Destination",
        "ARRIVAL_COUNTRY":                  spec.arrival,
        "ARRIVAL_POST_CODE":                "10115" if spec.arrival == "DE" else "00100",
        "SALE_DEPART_COUNTRY":              spec.departure,
        "SALE_ARRIVAL_COUNTRY":             spec.arrival,
        "TRANSPORTATION_MODE":              "ROAD",
        "DELIVERY_CONDITIONS":              "DAP",
        # TVA vendeur
        "SELLER_DEPART_VAT_NUMBER_COUNTRY": "FR",
        "SELLER_DEPART_COUNTRY_VAT_NUMBER": "FR12345678901",
        "TRANSACTION_SELLER_VAT_NUMBER_COUNTRY": "FR",
        "TRANSACTION_SELLER_VAT_NUMBER":    "FR12345678901",
        # TVA acheteur (B2B si renseigné)
        "BUYER_VAT_NUMBER_COUNTRY":         buyer_vat_country,
        "BUYER_VAT_NUMBER":                 spec.buyer_vat,
        # Divers
        "PRODUCT_TAX_CODE":                 "A_GEN_STANDARD",
        "VAT_CALCULATION_IMPUTATION_COUNTRY": spec.arrival,
        "TAXABLE_JURISDICTION":             spec.arrival,
        "TAXABLE_JURISDICTION_LEVEL":       "COUNTRY",
        "VAT_INV_NUMBER":                   f"INV-{year}-{seq:06d}",
        "EXPORT_OUTSIDE_EU":                "FALSE",
        "TAX_REPORTING_SCHEME":             "OSS" if spec.departure != spec.arrival else "DOMESTIC",
        "TAX_COLLECTION_RESPONSIBILITY":    "SELLER",
    })
    return row


# ---------------------------------------------------------------------------
# Construction des scénarios par année
# ---------------------------------------------------------------------------

def _build_scenarios_for_year(
    year: int,
    oss_target: str,   # "below" | "cross" | "above"
    rng: random.Random,
) -> List[ScenarioSpec]:
    """
    Construit la liste des scénarios pour une année selon l'objectif OSS.

    oss_target :
        "below"  → cumul OSS restera < 10 000 € (test TVA FR sous seuil)
        "cross"  → une vente franchit le seuil (test alerte franchissement)
        "above"  → cumul OSS > 10 000 € dès le début (test OSS normal)
    """
    specs: List[ScenarioSpec] = []

    # --- 1. Ventes domestiques France (ne comptent pas dans le cumul OSS) ---
    for i in range(5):
        amt = Decimal(str(rng.randint(50, 500)))
        specs.append(ScenarioSpec(
            label="B2C_DOM_FR",
            tx_type="SHIPMENT",
            departure="FR", arrival="FR",
            amount_ht=amt,
            note=f"Vente domestique France #{i+1}",
        ))

    # --- 2. Ventes B2B cross-border (reverse charge — ne comptent pas OSS) ---
    for country, vat in list(_B2B_VAT_BY_COUNTRY.items())[:3]:
        amt = Decimal(str(rng.randint(200, 2000)))
        specs.append(ScenarioSpec(
            label="B2B_RC",
            tx_type="SHIPMENT",
            departure="FR", arrival=country,
            amount_ht=amt,
            buyer_vat=vat,
            note=f"B2B reverse charge vers {country}",
        ))

    # --- 3. B2B avec NIF national ES/IT (art.194) ---
    for country, nifs in list(_NATIONAL_TAX_IDS.items())[:1]:
        amt = Decimal(str(rng.randint(100, 800)))
        specs.append(ScenarioSpec(
            label="B2B_NIF_NATIONAL",
            tx_type="SHIPMENT",
            departure="FR", arrival=country,
            amount_ht=amt,
            buyer_vat=rng.choice(nifs),
            note=f"NIF national {country} (art.194)",
        ))

    # --- 4. Ventes B2C cross-border intra-UE (OSS) ---
    # Pilotage du cumul selon oss_target
    if oss_target == "below":
        # Rester sous 10 000 € : ~8 000 € de ventes OSS
        oss_amounts = [Decimal(str(rng.randint(300, 1200))) for _ in range(8)]
        # Plafonner pour rester sous le seuil
        total = sum(oss_amounts)
        if total >= SEUIL_OSS:
            factor = (SEUIL_OSS - Decimal("500")) / total
            oss_amounts = [(a * factor).quantize(Decimal("1")) for a in oss_amounts]

    elif oss_target == "cross":
        # Ventes juste sous le seuil + UNE vente de franchissement
        oss_amounts = [Decimal(str(rng.randint(800, 1200))) for _ in range(8)]
        total = sum(oss_amounts)
        # Ajuster pour que le total avant dernière vente soit juste sous 10 000 €
        if total > Decimal("9000"):
            oss_amounts = [a * Decimal("9000") / total for a in oss_amounts]
            oss_amounts = [a.quantize(Decimal("1")) for a in oss_amounts]
        # Ajouter la vente de franchissement
        oss_amounts.append(Decimal("2000"))   # celle-ci franchit le seuil

    else:  # "above"
        # Bien au-dessus du seuil : ~25 000 € de ventes OSS
        oss_amounts = [Decimal(str(rng.randint(500, 3000))) for _ in range(12)]

    countries_pool = _EU_DEST.copy()
    rng.shuffle(countries_pool)
    for i, amt in enumerate(oss_amounts):
        dest = countries_pool[i % len(countries_pool)]
        specs.append(ScenarioSpec(
            label="B2C_OSS",
            tx_type="SHIPMENT",
            departure="FR", arrival=dest,
            amount_ht=max(Decimal("10"), abs(amt)),
            note=f"OSS B2C vers {dest} (cible={oss_target})",
        ))

    # --- 5. Avoir OSS (réduit le cumul) ---
    if oss_amounts:
        avoir_amt = (oss_amounts[0] * Decimal("0.5")).quantize(Decimal("1"))
        avoir_dest = countries_pool[0]
        specs.append(ScenarioSpec(
            label="AVOIR_OSS",
            tx_type="RETURN",
            departure="FR", arrival=avoir_dest,
            amount_ht=avoir_amt,
            note=f"Avoir OSS vers {avoir_dest}",
        ))

    # --- 6. Export hors UE ---
    specs.append(ScenarioSpec(
        label="EXPORT_HUE",
        tx_type="SHIPMENT",
        departure="FR", arrival="GB",
        amount_ht=Decimal(str(rng.randint(100, 500))),
        note="Export hors UE (GB post-Brexit)",
    ))

    return specs


# ---------------------------------------------------------------------------
# Point d'entrée principal
# ---------------------------------------------------------------------------

def generate(
    years: List[int],
    output_path: Path,
    seed: int = 42,
) -> None:
    """Génère le fichier CSV multi-années."""
    rng = random.Random(seed)

    # Stratégie OSS par année pour couvrir tous les cas
    oss_strategies = ["below", "cross", "above"]

    all_rows: List[dict] = []
    seq = 1

    print(f"Génération des ventes pour {len(years)} année(s) : {years}")
    print(f"Seuil OSS : {SEUIL_OSS} €\n")

    for i, year in enumerate(years):
        strategy = oss_strategies[i % len(oss_strategies)]
        specs = _build_scenarios_for_year(year, strategy, rng)

        # Trier les specs dans un ordre aléatoire pour simuler l'ordre réel
        rng.shuffle(specs)

        year_oss_total = Decimal("0")
        year_rows = []

        for spec in specs:
            row = _make_row(year, spec, seq, account_id="FR_SELLER_TEST_001")
            year_rows.append(row)
            seq += 1

            if spec.tx_type == "SHIPMENT" and spec.departure != spec.arrival \
               and not spec.buyer_vat and spec.arrival in _VAT_RATES:
                # Compte dans le cumul OSS si B2C cross-border intra-UE
                year_oss_total += spec.amount_ht

        # Trier par date de transaction pour la chronologie
        year_rows.sort(key=lambda r: r["TRANSACTION_COMPLETE_DATE"])
        all_rows.extend(year_rows)

        oss_status = (
            "✓ SOUS le seuil" if year_oss_total < SEUIL_OSS
            else f"⚡ FRANCHISSEMENT" if strategy == "cross"
            else "↑ AU-DESSUS du seuil"
        )
        print(f"  {year} ({strategy:6s}) : {len(specs):3d} transactions, "
              f"cumul OSS estimé ≈ {year_oss_total:>10,.2f} €  {oss_status}")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=_COLUMNS)
        writer.writeheader()
        writer.writerows(all_rows)

    print(f"\n✅ Fichier généré : {output_path}")
    print(f"   {len(all_rows)} lignes, {len(years)} années ({years[0]}–{years[-1]})")
    print()
    print("Rappel des scénarios couverts par année :")
    print("  Année 1 (below)  → cumul OSS < 10 000 €  → test option TVA FR sous seuil")
    print("  Année 2 (cross)  → une vente franchit le seuil → test alerte franchissement")
    print("  Année 3 (above)  → cumul OSS >> 10 000 € → test déclaration OSS normale")
    print("  (cycle si > 3 ans)")


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Générateur de données de ventes multi-années pour tester le seuil OSS."
    )
    parser.add_argument(
        "--annees",
        nargs="+",
        type=int,
        default=[2022, 2023, 2024],
        metavar="ANNEE",
        help="Années à générer (défaut : 2022 2023 2024).",
    )
    parser.add_argument(
        "--output",
        default="data/ventes_multian_test.csv",
        help="Chemin du fichier CSV de sortie (défaut : data/ventes_multian_test.csv).",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=42,
        help="Graine aléatoire pour la reproductibilité (défaut : 42).",
    )
    args = parser.parse_args(argv)

    years = sorted(set(args.annees))
    if not years:
        print("Erreur : aucune année spécifiée.", file=sys.stderr)
        return 1

    generate(
        years=years,
        output_path=Path(args.output),
        seed=args.seed,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
