"""Tests de couverture exhaustive pour report._bucket_label().

Contexte : ReportSummary.ht_by_bucket (report.py) ventile chaque VatResult
dans un "seau" fiscal à des fins de contrôle de cohérence comptable (UI
Streamlit, cf. app.py). Le seau "Autre / non classé" est un filet de
sécurité — il ne doit JAMAIS être atteint en pratique, sinon un scénario
échappe à la ventilation et casse l'utilité du contrôle de cohérence
(un vrai écart deviendrait indiscernable d'un scénario simplement mal
étiqueté).

Ce fichier construit, pour CHAQUE branche connue de compute_vat()
(engine.py), une Sale représentative et vérifie que le VatResult résultant
tombe dans un seau nommé — jamais dans "Autre / non classé". Si un nouveau
scénario est ajouté à models.py/engine.py sans être répercuté dans
report._bucket_label(), ce test doit échouer et alerter explicitement,
plutôt que de laisser le trou passer inaperçu (c'est exactement le type de
lacune trouvée manuellement lors de la revue de code : l'autoliquidation
nationale B2B hors FR, scenario=DOMESTIC/collector=BUYER, n'était couverte
par aucune branche de _bucket_label avant correction).

Les Sale de test reprennent la logique de test_engine.py (mêmes valeurs de
référence par scénario) pour rester cohérentes avec la suite existante.
"""

from __future__ import annotations

import os
import sys

# Permet d'importer le package tva_intracom depuis /home/claude/ (cf. test_engine.py)
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from decimal import Decimal

import pytest

from tva_intracom.engine import compute_vat
from tva_intracom.models import BuyerType, Channel, Collector, Sale, Scenario, VatResult
from tva_intracom.rates import DOMESTIC_REVERSE_CHARGE_COUNTRIES
from tva_intracom.report import _bucket_label, _ZERO, ReportSummary, build_report

_UNCLASSIFIED = "Autre / non classé"


def make_sale(**kwargs) -> Sale:
    """Construit une Sale avec des valeurs par défaut raisonnables.

    Identique au helper de test_engine.py — à garder synchronisé si les
    défauts y changent.
    """
    defaults = dict(
        sale_id="TEST-001",
        amount_ht=Decimal("100.00"),
        buyer_type=BuyerType.B2C,
        stock_country="FR",
        buyer_country="DE",
        seller_country="FR",
        buyer_vat_valid=False,
        buyer_vat_number="",
        transaction_date="2024-01-15",
        product_category="STANDARD",
    )
    defaults.update(kwargs)
    return Sale(**defaults)


# ---------------------------------------------------------------------------
# Un cas représentatif par branche connue de compute_vat().
# Chaque entrée : (libellé du cas, Sale, label de seau attendu)
# ---------------------------------------------------------------------------

def _build_cases() -> list[tuple[str, Sale, str]]:
    # Un pays quelconque de la liste art. 194 pour le cas d'autoliquidation
    # nationale. Pris dynamiquement plutôt que codé en dur, pour rester
    # valide même si la liste de rates.py évolue.
    _drc_country = sorted(DOMESTIC_REVERSE_CHARGE_COUNTRIES)[0]

    return [
        (
            "export_hors_ue",
            make_sale(buyer_country="US"),
            "Export hors UE",
        ),
        (
            "ioss_direct",
            make_sale(
                stock_country="CN", buyer_country="FR",
                amount_ht=Decimal("80.00"), ioss_number="IM1234567890",
            ),
            "Guichet IOSS (vendeur)",
        ),
        (
            "deemed_supplier_import_faible_valeur",
            make_sale(stock_country="CN", buyer_country="FR", amount_ht=Decimal("50.00")),
            "Deemed supplier (Amazon)",
        ),
        (
            "deemed_supplier_vendeur_non_eu",
            make_sale(seller_country="US", stock_country="DE", buyer_country="FR"),
            "Deemed supplier (Amazon)",
        ),
        (
            "b2b_reverse_charge_intracom",
            make_sale(
                stock_country="DE", buyer_country="FR",
                buyer_type=BuyerType.B2B, buyer_vat_valid=True,
                buyer_vat_number="FR12345678901",
            ),
            "B2B exonéré (autoliquidation intracom)",
        ),
        (
            "autoliquidation_nationale_b2b_hors_fr",
            make_sale(
                stock_country=_drc_country, buyer_country=_drc_country,
                buyer_type=BuyerType.B2B, buyer_vat_valid=False,
                buyer_vat_number=f"{_drc_country}123456789",
            ),
            "Autoliquidation nationale B2B (hors FR)",
        ),
        (
            "oss_b2c_intracom",
            make_sale(stock_country="FR", buyer_country="DE"),
            "Guichet OSS",
        ),
        (
            "domestic_fr",
            make_sale(stock_country="FR", buyer_country="FR"),
            "TVA domestique France (CA3)",
        ),
        (
            "domestic_etranger_immat_locale",
            # DE n'est pas dans DOMESTIC_REVERSE_CHARGE_COUNTRIES pour une
            # vente B2C simple : reste une vente domestique classique avec
            # immatriculation locale (pas d'autoliquidation, buyer_type=B2C
            # sans numéro fiscal fourni).
            make_sale(stock_country="DE", buyer_country="DE"),
            "Immatriculation TVA locale",
        ),
        (
            "import_standard_acheteur_importateur",
            make_sale(
                stock_country="CN", buyer_country="FR",
                amount_ht=Decimal("200.00"), seller_is_importer=False,
            ),
            "Import (TVA douane, hors IOSS)",
        ),
        (
            "import_seller_as_importer_ddp_fr",
            make_sale(
                stock_country="CN", buyer_country="FR",
                amount_ht=Decimal("300.00"), seller_is_importer=True,
            ),
            "TVA domestique France (CA3)",
        ),
        (
            "import_seller_as_importer_ddp_etranger",
            make_sale(
                stock_country="US", buyer_country="DE",
                amount_ht=Decimal("500.00"), seller_is_importer=True,
            ),
            "Immatriculation TVA locale",
        ),
    ]


_CASES = _build_cases()


class TestBucketLabelExhaustiveCoverage:
    """Un test par branche connue de compute_vat() : jamais 'Autre / non classé'."""

    @pytest.mark.parametrize("case_name,sale,expected_label", _CASES, ids=[c[0] for c in _CASES])
    def test_bucket_label_matches_expected(self, case_name, sale, expected_label):
        result = compute_vat(sale)
        label = _bucket_label(result)
        assert label != _UNCLASSIFIED, (
            f"Scénario '{case_name}' (scenario={result.scenario}, "
            f"channel={result.channel}, collector={result.collector}) tombe dans "
            f"'{_UNCLASSIFIED}' — _bucket_label() dans report.py doit être mis à "
            f"jour pour couvrir ce cas."
        )
        assert label == expected_label, (
            f"Scénario '{case_name}' classé sous '{label}', attendu '{expected_label}'."
        )

    def test_no_known_scenario_falls_through_to_unclassified(self):
        """Rappel explicite : aucun des cas ci-dessus ne doit atteindre le seau
        générique. Si ce test échoue, un ou plusieurs cas de _CASES sont mal
        classés et le contrôle de cohérence de app.py perd sa valeur d'alerte."""
        unclassified = [
            name for name, sale, _ in _CASES
            if _bucket_label(compute_vat(sale)) == _UNCLASSIFIED
        ]
        assert unclassified == [], f"Cas tombés dans '{_UNCLASSIFIED}' : {unclassified}"


class TestHtByBucketIntegrity:
    """Vérifie que la somme des seaux HT égale toujours le CA HT total,
    y compris sur un mélange de ventes ET de remboursements couvrant tous
    les scénarios connus."""

    def test_sum_of_buckets_equals_total_ht(self):
        sales_results = [compute_vat(sale) for _, sale, _ in _CASES]
        summary = build_report(sales_results)
        bucket_sum = sum(summary.ht_by_bucket.values(), _ZERO)
        assert bucket_sum == summary.total_ht

    def test_sum_of_buckets_equals_total_ht_with_refunds(self):
        sales_results = [compute_vat(sale) for _, sale, _ in _CASES]
        # Remboursement partiel sur un sous-ensemble des cas, montants négatifs.
        refund_sales = [
            make_sale(
                sale_id=f"REFUND-{i}",
                amount_ht=-(sale.amount_ht / 2),
                buyer_type=sale.buyer_type,
                stock_country=sale.stock_country,
                buyer_country=sale.buyer_country,
                seller_country=sale.seller_country,
                buyer_vat_valid=sale.buyer_vat_valid,
                buyer_vat_number=sale.buyer_vat_number,
                seller_is_importer=sale.seller_is_importer,
                ioss_number=sale.ioss_number,
            )
            for i, (_, sale, _) in enumerate(_CASES)
        ]
        refund_results = [compute_vat(s) for s in refund_sales]
        summary = build_report(sales_results, refund_results=refund_results)

        bucket_sum = sum(summary.ht_by_bucket.values(), _ZERO)
        refund_bucket_sum = sum(summary.refund_ht_by_bucket.values(), _ZERO)
        assert bucket_sum == summary.total_ht
        assert refund_bucket_sum == summary.refund_total_ht
        assert summary.net_ht_total == summary.total_ht + summary.refund_total_ht


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
