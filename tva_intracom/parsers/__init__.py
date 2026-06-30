"""Parsers pour les fichiers d'export des differentes plateformes e-commerce.

Chaque parser traduit le format natif de la plateforme en objets Sale
exploitables par le moteur de calcul TVA (engine.py).

Architecture :
- amazon.py    : Amazon VAT Transactions Report (TSV)
- mirakl.py    : Mirakl (Fnac, Darty, Leroy Merlin...) - Excel/CSV
- shopify.py   : Shopify orders_export.csv
- woocommerce.py : WooCommerce / PrestaShop (CSV generique)
- aliexpress.py  : AliExpress / eBay / Temu (CSV/TSV marketplace)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Set

from ..models import Sale


@dataclass
class ParseResult:
    """Resultat commun a tous les parsers.

    Chaque parser retourne un ParseResult avec les ventes exploitables
    et les metadonnees specifiques a la plateforme.
    """

    sales: List[Sale]
    refunds: List[Sale] = field(default_factory=list)
    stock_countries: Set[str] = field(default_factory=set)
    skipped_rows: int = 0
    total_rows: int = 0
    warnings: List[str] = field(default_factory=list)
    platform: str = ""
    # Pour les marketplaces : transferts FC (Amazon) ou equivalents.
    fc_transfers: List[dict] = field(default_factory=list)


# Enumeration des plateformes supportees.
PLATFORMS = {
    "amazon": "Amazon VAT Transactions Report (TSV)",
    "mirakl": "Mirakl (Fnac, Darty, Leroy Merlin...)",
    "shopify": "Shopify (orders_export.csv)",
    "woocommerce": "WooCommerce / PrestaShop (CSV)",
    "aliexpress": "AliExpress / eBay / Temu (CSV)",
    "csv_simple": "CSV simplifie (notre format)",
}
