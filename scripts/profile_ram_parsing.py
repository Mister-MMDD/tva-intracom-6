"""Profilage RAM du parsing Amazon — reproduit les mesures de la session
d'optimisation RAM (voir patch_ram_optim.zip et patch_oss_export_writeonly.zip).

Usage :
    pip install pympler --break-system-packages
    python3 scripts/profile_ram_parsing.py fichier1.txt [fichier2.txt ...]

Affiche :
  - le nombre de ventes/remboursements parsés par fichier
  - la taille mémoire réelle (pympler.asizeof) de l'ensemble des Sale/Refund
    combinés, c'est-à-dire le volume qui était dupliqué en session_state
    avant le fix `app.py` (_parse_cache_data) : avant le fix, ce volume
    était compté 2x (une fois dans all_sales, une fois dans chaque
    parse_result.sales/.refunds encore référencé en cache).
"""

from __future__ import annotations

import sys
from pathlib import Path

from tva_intracom.parsers.amazon.loader import load_amazon_report

try:
    from pympler import asizeof
except ImportError:
    print("pympler manquant : pip install pympler --break-system-packages")
    sys.exit(1)


def main(paths: list[str]) -> None:
    all_sales, all_refunds = [], []
    for p in paths:
        res = load_amazon_report(p)
        print(f"{Path(p).name}: {len(res.sales)} ventes, {len(res.refunds)} remboursements")
        all_sales.extend(res.sales)
        all_refunds.extend(res.refunds)

    size_sales = asizeof.asizeof(all_sales)
    size_refunds = asizeof.asizeof(all_refunds)
    total_kb = (size_sales + size_refunds) / 1024
    print()
    print(f"Total : {len(all_sales)} ventes + {len(all_refunds)} remboursements")
    print(f"Taille mémoire réelle (pympler) : {total_kb:.1f} Ko")
    print(
        "-> volume dupliqué en session_state AVANT le fix app.py "
        "(_parse_cache_data gardait parse_result.sales/.refunds en plus "
        "de all_sales/all_refunds, alors que ces attributs ne sont plus "
        "jamais relus après l'agrégation) : ~"
        f"{total_kb:.0f} Ko économisés en continu pendant toute la session."
    )


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    main(sys.argv[1:])
