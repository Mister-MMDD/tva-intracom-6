"""Interface en ligne de commande.

Usage:
    python -m tva_intracom.cli [chemin_ventes.csv] [--details] [--xlsx rapport.xlsx]
    python -m tva_intracom.cli rapport_amazon.tsv --amazon [--xlsx rapport.xlsx]

Sans argument de fichier, le jeu de donnees d'exemple est utilise.
La validation VIES des numeros de TVA B2B est desormais toujours active
(requiert Internet) -- il n'y a plus de flag pour la desactiver.
"""

from __future__ import annotations

import argparse
import csv
import sys
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import List

from .engine import compute_all_with_vies
from .models import BuyerType, Sale, VatResult
from .report import build_report, render_report

_DEFAULT_DATA = Path(__file__).parent / "data" / "ventes_exemple.csv"

_TRUE_VALUES = {"true", "1", "oui", "yes", "vrai", "x"}


def _parse_bool(value: str) -> bool:
    return value.strip().lower() in _TRUE_VALUES


def load_sales(path: Path) -> List[Sale]:
    """Charge les ventes depuis un fichier CSV."""
    sales: List[Sale] = []
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        for line_no, row in enumerate(reader, start=2):
            try:
                sale = Sale(
                    sale_id=row["sale_id"].strip(),
                    amount_ht=Decimal(row["amount_ht"].strip()),
                    buyer_type=BuyerType(row["buyer_type"].strip().upper()),
                    stock_country=row["stock_country"].strip(),
                    buyer_country=row["buyer_country"].strip(),
                    seller_country=(row.get("seller_country") or "FR").strip() or "FR",
                    buyer_vat_valid=_parse_bool(row.get("buyer_vat_valid") or ""),
                    buyer_vat_number=(row.get("buyer_vat_number") or "").strip(),
                    quantity=int(row.get("quantity") or "1"),
                )
            except (KeyError, ValueError, InvalidOperation) as exc:
                raise ValueError(
                    f"Erreur de lecture du CSV ligne {line_no}: {exc}"
                ) from exc
            sales.append(sale)
    return sales


def render_details(results: List[VatResult]) -> str:
    """Tableau detaille ligne par ligne."""
    header = (
        f"{'ID':<6} {'Type':<4} {'Stock':<5} {'Dest':<5} "
        f"{'HT':>10} {'Scenario':<20} {'Taux':>6} {'TVA':>10}"
    )
    lines = [header, "-" * len(header)]
    for r in results:
        lines.append(
            f"{r.sale.sale_id:<6} {r.sale.buyer_type.value:<4} "
            f"{r.sale.stock_country:<5} {r.sale.buyer_country:<5} "
            f"{r.sale.amount_ht:>10.2f} {r.scenario.value:<20} "
            f"{r.vat_rate:>5}% {r.vat_amount:>10.2f}"
        )
    return "\n".join(lines)


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Calcul de la TVA intracommunautaire des ventes marketplace."
    )
    parser.add_argument(
        "fichier",
        nargs="?",
        default=str(_DEFAULT_DATA),
        help="Fichier CSV des ventes (defaut: jeu d'exemple).",
    )
    parser.add_argument(
        "--details",
        action="store_true",
        help="Afficher le detail ligne par ligne.",
    )
    parser.add_argument(
        "--xlsx",
        metavar="FICHIER",
        help="Exporter le rapport au format Excel (.xlsx).",
    )
    parser.add_argument(
        "--platform",
        choices=["amazon", "mirakl", "shopify", "woocommerce", "aliexpress"],
        default=None,
        help="Plateforme source du fichier (active le parser specifique).",
    )
    parser.add_argument(
        "--amazon",
        action="store_true",
        help="Raccourci pour --platform amazon.",
    )
    parser.add_argument(
        "--encoding",
        default="utf-8",
        help="Encodage du fichier (defaut: utf-8). Ex: latin-1, cp1252.",
    )
    parser.add_argument(
        "--convert-fx",
        action="store_true",
        help="Convertir les devises non-EUR via les taux BCE du jour.",
    )
    args = parser.parse_args(argv)

    path = Path(args.fichier)
    if not path.exists():
        print(f"Fichier introuvable : {path}", file=sys.stderr)
        return 1

    try:
        platform = args.platform
        if args.amazon and not platform:
            platform = "amazon"

        if platform:
            from .parsers import amazon as p_amazon
            from .parsers import mirakl as p_mirakl
            from .parsers import shopify as p_shopify
            from .parsers import woocommerce as p_woocommerce
            from .parsers import aliexpress as p_aliexpress

            parser_map = {
                "amazon": p_amazon,
                "mirakl": p_mirakl,
                "shopify": p_shopify,
                "woocommerce": p_woocommerce,
                "aliexpress": p_aliexpress,
            }
            p = parser_map[platform]
            parse_result = p.parse(
                path, encoding=args.encoding, convert_currencies=args.convert_fx
            )
            sales   = parse_result.sales
            refunds = parse_result.refunds
            if parse_result.warnings:
                for w in parse_result.warnings:
                    print(f"[WARN] {w}", file=sys.stderr)
            print(f"Import {platform} : {len(sales)} ventes, "
                  f"{len(refunds)} remboursements "
                  f"({parse_result.total_rows} lignes, "
                  f"{parse_result.skipped_rows} ignorees).")
            if parse_result.stock_countries - {"FR"}:
                print(f"Pays de stockage detectes (hors FR) : "
                      f"{', '.join(sorted(parse_result.stock_countries - {'FR'}))}")
            if parse_result.fc_transfers:
                print(f"{len(parse_result.fc_transfers)} transferts FC detectes.")
            print()
        else:
            sales   = load_sales(path)
            refunds = []
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    # scope_id "cli:local" : le CLI n'a pas de compte/email authentifie comme
    # dans l'UI (resolve_scope_id) -- portee de cache VIES dediee, non partagee
    # avec les scopes utilisateur/domaine de l'application web.
    _CLI_VIES_SCOPE_ID = "cli:local"

    results, vies_summary, oss_summary = compute_all_with_vies(
        sales, scope_id=_CLI_VIES_SCOPE_ID, refunds=refunds if refunds else None
    )

    # Recalcul des avoirs avec marketplace_name pour appliquer les bons taux reduits.
    # asin_to_category indisponible en CLI : categories portees par le parser (product_category).
    # VIES obligatoire aussi sur les avoirs (plus de distinction avec le calcul
    # principal).
    refund_results = compute_all_with_vies(
        refunds,
        scope_id=_CLI_VIES_SCOPE_ID,
        marketplace_name=platform or "",
    )[0] if refunds else []

    if args.details:
        print(render_details(results))
        print()

    summary = build_report(results, refund_results=refund_results or None)
    print(render_report(summary))

    # Afficher la synthese VIES si applicable.
    if vies_summary is not None and vies_summary.total_checked > 0:
        print()
        print("=" * 64)
        print("VERIFICATION VIES - SYNTHESE FRAUDE")
        print("=" * 64)
        print(f"Numeros verifies : {vies_summary.total_checked}")
        print(f"Numeros valides  : {vies_summary.total_valid}")
        print(f"Numeros invalides: {vies_summary.total_invalid}")
        if vies_summary.reclassifications:
            print(f"\nFRAUDE EVITEE : {vies_summary.fraud_avoided_amount:,.2f} EUR de TVA")
            print(f"({len(vies_summary.reclassifications)} vente(s) reclassifiee(s) "
                  f"B2B -> B2C sur {vies_summary.fraud_avoided_ht:,.2f} EUR HT)")
            for r in vies_summary.reclassifications:
                reason = getattr(r, "reason", "") or "autoliquidation nationale"
                print(f"  - {r.sale_id} : {r.buyer_vat_number} ({reason}) "
                      f"-> +{r.vat_avoided:,.2f} EUR de TVA recuperee")
        else:
            print("Aucune fraude detectee (tous les numeros sont valides).")

    if args.xlsx:
        from .excel_report import export_xlsx

        xlsx_path = export_xlsx(results, args.xlsx, summary=summary)
        print(f"\nRapport Excel genere : {xlsx_path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())