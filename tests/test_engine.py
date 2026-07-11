"""Tests unitaires du moteur de TVA intracommunautaire.

Couvre l'intégralité des scénarios de compute_vat() :
  - Export hors UE
  - IOSS_DIRECT (vendeur avec son propre numéro IOSS)
  - Deemed Supplier (Amazon collecte)
  - B2B Reverse Charge (autoliquidation)
  - OSS B2C intra-UE
  - Domestic FR / étranger
  - IMPORT_STANDARD (acheteur = importateur)
  - IMPORT_SELLER_AS_IMPORTER (DDP, vendeur = importateur)
  - Taux réduits / super-réduits / parking
  - Seuil OSS 10 000 EUR (compute_all)
"""

from __future__ import annotations

import sys
import os

# Permet d'importer le package tva_intracom depuis /home/claude/
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from decimal import Decimal

import pytest

from tva_intracom.engine import compute_vat, compute_all
from tva_intracom.models import (
    BuyerType,
    Channel,
    Collector,
    Sale,
    Scenario,
)
from tva_intracom.rates import vat_rate


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_sale(**kwargs) -> Sale:
    """Construit une Sale avec des valeurs par défaut raisonnables."""
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
# 1. Exportation hors UE
# ---------------------------------------------------------------------------

class TestExport:
    def test_export_to_gb(self):
        sale = make_sale(buyer_country="GB")
        res = compute_vat(sale)
        assert res.scenario == Scenario.EXPORT
        assert res.vat_rate == Decimal("0")
        assert res.vat_amount == Decimal("0.00")
        assert res.collector == Collector.SELLER
        assert res.channel == Channel.EXONERATION

    def test_export_to_us(self):
        sale = make_sale(buyer_country="US", amount_ht=Decimal("500"))
        res = compute_vat(sale)
        assert res.scenario == Scenario.EXPORT
        assert res.vat_amount == Decimal("0.00")

    def test_export_to_ch(self):
        """Suisse hors UE."""
        sale = make_sale(buyer_country="CH")
        res = compute_vat(sale)
        assert res.scenario == Scenario.EXPORT

    def test_export_from_non_eu_stock(self):
        """Stock hors UE, acheteur hors UE : export."""
        sale = make_sale(stock_country="CN", buyer_country="US")
        res = compute_vat(sale)
        assert res.scenario == Scenario.EXPORT


# ---------------------------------------------------------------------------
# 2. IOSS_DIRECT — vendeur avec son propre numéro IOSS
# ---------------------------------------------------------------------------

class TestIossDirect:
    def test_ioss_direct_basic(self):
        """B2C, import ≤ 150 EUR, numéro IOSS renseigné."""
        sale = make_sale(
            stock_country="CN",
            buyer_country="FR",
            amount_ht=Decimal("80.00"),
            ioss_number="IM1234567890",
        )
        res = compute_vat(sale)
        assert res.scenario == Scenario.IOSS_DIRECT
        assert res.collector == Collector.SELLER
        assert res.channel == Channel.IOSS
        assert res.vat_country == "FR"
        assert res.vat_rate == Decimal("20")  # taux FR standard

    def test_ioss_direct_exactly_150(self):
        """Limite haute : 150 EUR exactement → IOSS."""
        sale = make_sale(
            stock_country="US",
            buyer_country="DE",
            amount_ht=Decimal("150.00"),
            ioss_number="IM9999999999",
        )
        res = compute_vat(sale)
        assert res.scenario == Scenario.IOSS_DIRECT

    def test_ioss_direct_above_150_falls_to_import(self):
        """151 EUR avec numéro IOSS → IMPORT_STANDARD (IOSS ne s'applique plus)."""
        sale = make_sale(
            stock_country="CN",
            buyer_country="FR",
            amount_ht=Decimal("151.00"),
            ioss_number="IM1234567890",
        )
        res = compute_vat(sale)
        # Le numéro IOSS est ignoré au-dessus du seuil
        assert res.scenario in (Scenario.IMPORT_STANDARD, Scenario.IMPORT_SELLER_AS_IMPORTER)

    def test_ioss_direct_no_number_falls_to_deemed(self):
        """Import ≤ 150 EUR sans numéro IOSS → deemed supplier."""
        sale = make_sale(
            stock_country="CN",
            buyer_country="FR",
            amount_ht=Decimal("80.00"),
            ioss_number="",
        )
        res = compute_vat(sale)
        assert res.scenario == Scenario.DEEMED_SUPPLIER

    def test_ioss_direct_note_contains_ioss_number(self):
        sale = make_sale(
            stock_country="CN", buyer_country="IT",
            amount_ht=Decimal("50"), ioss_number="IM0000000001",
        )
        res = compute_vat(sale)
        assert "IM0000000001" in res.note


# ---------------------------------------------------------------------------
# 3. Deemed Supplier (Amazon collecte)
# ---------------------------------------------------------------------------

class TestDeemedSupplier:
    def test_low_value_import_b2c(self):
        """Import ≤ 150 EUR, B2C, sans numéro IOSS → deemed supplier."""
        sale = make_sale(
            stock_country="CN",
            buyer_country="FR",
            amount_ht=Decimal("50.00"),
        )
        res = compute_vat(sale)
        assert res.scenario == Scenario.DEEMED_SUPPLIER
        assert res.collector == Collector.AMAZON
        assert res.channel == Channel.EXONERATION

    def test_seller_non_eu_intra_eu_stock_b2c(self):
        """Vendeur non-UE, stock UE, acheteur UE, B2C → deemed supplier."""
        sale = make_sale(
            seller_country="US",
            stock_country="DE",
            buyer_country="FR",
        )
        res = compute_vat(sale)
        assert res.scenario == Scenario.DEEMED_SUPPLIER

    def test_deemed_supplier_correct_vat_rate(self):
        """TVA appliquée = taux du pays acheteur."""
        sale = make_sale(
            stock_country="CN", buyer_country="IT",
            amount_ht=Decimal("100"),
        )
        res = compute_vat(sale)
        assert res.scenario == Scenario.DEEMED_SUPPLIER
        assert res.vat_rate == Decimal("22")  # IT standard
        assert res.vat_amount == Decimal("22.00")

    def test_deemed_supplier_b2b_not_triggered(self):
        """B2B avec TVA valide ne déclenche pas deemed supplier."""
        sale = make_sale(
            stock_country="CN",
            buyer_country="FR",
            amount_ht=Decimal("50"),
            buyer_type=BuyerType.B2B,
            buyer_vat_valid=True,
            buyer_vat_number="FR12345678901",
        )
        res = compute_vat(sale)
        assert res.scenario != Scenario.DEEMED_SUPPLIER


# ---------------------------------------------------------------------------
# 4. B2B Reverse Charge (autoliquidation)
# ---------------------------------------------------------------------------

class TestB2BReverseCharge:
    def test_basic_reverse_charge(self):
        """DE → FR, B2B, TVA valide → autoliquidation."""
        sale = make_sale(
            stock_country="DE",
            buyer_country="FR",
            buyer_type=BuyerType.B2B,
            buyer_vat_valid=True,
            buyer_vat_number="FR12345678901",
        )
        res = compute_vat(sale)
        assert res.scenario == Scenario.B2B_REVERSE_CHARGE
        assert res.vat_rate == Decimal("0")
        assert res.vat_amount == Decimal("0.00")
        assert res.collector == Collector.BUYER
        assert res.channel == Channel.EXONERATION

    def test_reverse_charge_invalid_vat_not_triggered(self):
        """TVA invalide (non validée) → pas d'autoliquidation."""
        sale = make_sale(
            stock_country="DE",
            buyer_country="FR",
            buyer_type=BuyerType.B2B,
            buyer_vat_valid=False,
            buyer_vat_number="FR00000000000",
        )
        res = compute_vat(sale)
        assert res.scenario != Scenario.B2B_REVERSE_CHARGE

    def test_reverse_charge_domestic_not_triggered(self):
        """Vente domestique B2B → pas d'autoliquidation intra-UE."""
        sale = make_sale(
            stock_country="FR",
            buyer_country="FR",
            buyer_type=BuyerType.B2B,
            buyer_vat_valid=True,
            buyer_vat_number="FR12345678901",
        )
        res = compute_vat(sale)
        assert res.scenario == Scenario.DOMESTIC

    def test_reverse_charge_es_to_it(self):
        """ES → IT, B2B validé."""
        sale = make_sale(
            stock_country="ES",
            buyer_country="IT",
            buyer_type=BuyerType.B2B,
            buyer_vat_valid=True,
            buyer_vat_number="IT12345678901",
        )
        res = compute_vat(sale)
        assert res.scenario == Scenario.B2B_REVERSE_CHARGE


# ---------------------------------------------------------------------------
# 5. OSS B2C intra-UE
# ---------------------------------------------------------------------------

class TestOssB2C:
    def test_oss_fr_to_de(self):
        """FR → DE, B2C, intra-UE → OSS."""
        sale = make_sale(stock_country="FR", buyer_country="DE")
        res = compute_vat(sale)
        assert res.scenario == Scenario.OSS_B2C
        assert res.vat_country == "DE"
        assert res.collector == Collector.SELLER
        assert res.channel == Channel.OSS
        assert res.vat_rate == Decimal("19")

    def test_oss_vat_amount_calculation(self):
        sale = make_sale(
            stock_country="FR", buyer_country="ES",
            amount_ht=Decimal("200.00"),
        )
        res = compute_vat(sale)
        assert res.scenario == Scenario.OSS_B2C
        assert res.vat_rate == Decimal("21")
        assert res.vat_amount == Decimal("42.00")

    def test_oss_b2b_no_vat_number_falls_to_oss(self):
        """B2B sans numéro TVA → pas d'autoliquidation, tombe sur OSS."""
        sale = make_sale(
            stock_country="FR",
            buyer_country="NL",
            buyer_type=BuyerType.B2B,
            buyer_vat_valid=False,
            buyer_vat_number="",
        )
        res = compute_vat(sale)
        # Sans n° valide, B2B cross-border ne peut pas faire reverse charge
        # → le moteur tombe sur OSS_B2C ou DOMESTIC selon la logique
        assert res.scenario in (Scenario.OSS_B2C, Scenario.DOMESTIC)

    def test_oss_multiple_destinations(self):
        """Vérification pour plusieurs pays destination."""
        expected = {
            "AT": Decimal("20"), "BE": Decimal("21"),
            "FI": Decimal("25.5"), "HU": Decimal("27"),
        }
        for country, expected_rate in expected.items():
            sale = make_sale(stock_country="FR", buyer_country=country)
            res = compute_vat(sale)
            assert res.scenario == Scenario.OSS_B2C, f"Échec pour {country}"
            assert res.vat_rate == expected_rate, f"Taux incorrect pour {country}"


# ---------------------------------------------------------------------------
# 6. Vente domestique
# ---------------------------------------------------------------------------

class TestDomestic:
    def test_domestic_fr(self):
        sale = make_sale(stock_country="FR", buyer_country="FR")
        res = compute_vat(sale)
        assert res.scenario == Scenario.DOMESTIC
        assert res.vat_country == "FR"
        assert res.channel == Channel.FR_DOMESTIC
        assert res.collector == Collector.SELLER
        assert res.vat_rate == Decimal("20")

    def test_domestic_de(self):
        sale = make_sale(stock_country="DE", buyer_country="DE")
        res = compute_vat(sale)
        assert res.scenario == Scenario.DOMESTIC
        assert res.channel == Channel.LOCAL_REGISTRATION
        assert res.vat_rate == Decimal("19")

    def test_domestic_es_food_super_reduced(self):
        """Espagne, alimentation de base → taux super-réduit 4%."""
        sale = make_sale(
            stock_country="ES", buyer_country="ES",
            product_category="SUPER_REDUCED",
        )
        res = compute_vat(sale)
        assert res.scenario == Scenario.DOMESTIC
        assert res.vat_rate == Decimal("4")

    def test_domestic_it_super_reduced(self):
        """Italie, biens de 1ère nécessité → 4%."""
        sale = make_sale(
            stock_country="IT", buyer_country="IT",
            product_category="SUPER_REDUCED",
        )
        res = compute_vat(sale)
        assert res.vat_rate == Decimal("4")

    def test_domestic_fr_books_reduced(self):
        """France, livres → 5.5%."""
        sale = make_sale(
            stock_country="FR", buyer_country="FR",
            product_category="BOOKS",
        )
        res = compute_vat(sale)
        assert res.vat_rate == Decimal("5.5")

    def test_domestic_fr_super_reduced(self):
        """France, taux super-réduit (médicaments remboursables) → 2.1%."""
        sale = make_sale(
            stock_country="FR", buyer_country="FR",
            product_category="SUPER_REDUCED",
        )
        res = compute_vat(sale)
        assert res.vat_rate == Decimal("2.1")

    def test_domestic_be_parking(self):
        """Belgique, taux parking → 12%."""
        sale = make_sale(
            stock_country="BE", buyer_country="BE",
            product_category="PARKING",
        )
        res = compute_vat(sale)
        assert res.vat_rate == Decimal("12")

    def test_domestic_lu_parking(self):
        """Luxembourg, taux parking → 14%."""
        sale = make_sale(
            stock_country="LU", buyer_country="LU",
            product_category="PARKING",
        )
        res = compute_vat(sale)
        assert res.vat_rate == Decimal("14")

    def test_domestic_ie_parking(self):
        """Irlande, taux parking → 13.5%."""
        sale = make_sale(
            stock_country="IE", buyer_country="IE",
            product_category="PARKING",
        )
        res = compute_vat(sale)
        assert res.vat_rate == Decimal("13.5")


# ---------------------------------------------------------------------------
# 7. Import standard (acheteur = importateur)
# ---------------------------------------------------------------------------

class TestImportStandard:
    def test_import_above_150_buyer_is_importer(self):
        """Import > 150 EUR, vendeur PAS importateur → TVA douane (acheteur)."""
        sale = make_sale(
            stock_country="CN",
            buyer_country="FR",
            amount_ht=Decimal("200.00"),
            seller_is_importer=False,
        )
        res = compute_vat(sale)
        assert res.scenario == Scenario.IMPORT_STANDARD
        assert res.collector == Collector.BUYER
        assert res.channel == Channel.EXONERATION
        assert res.vat_country == "FR"

    def test_import_standard_vat_rate(self):
        sale = make_sale(
            stock_country="US",
            buyer_country="DE",
            amount_ht=Decimal("500.00"),
        )
        res = compute_vat(sale)
        assert res.scenario == Scenario.IMPORT_STANDARD
        assert res.vat_rate == Decimal("19")
        assert res.vat_amount == Decimal("95.00")

    def test_import_standard_b2b_above_threshold(self):
        """B2B, import > 150 EUR, sans TVA valide → IMPORT_STANDARD."""
        sale = make_sale(
            stock_country="CN",
            buyer_country="FR",
            amount_ht=Decimal("300.00"),
            buyer_type=BuyerType.B2B,
            buyer_vat_valid=False,
        )
        res = compute_vat(sale)
        assert res.scenario == Scenario.IMPORT_STANDARD


# ---------------------------------------------------------------------------
# 8. Import avec vendeur = importateur (DDP)
# ---------------------------------------------------------------------------

class TestImportSellerAsImporter:
    def test_ddp_to_france(self):
        """DDP vers France → requalifié en domestique FR, CA3."""
        sale = make_sale(
            stock_country="CN",
            buyer_country="FR",
            amount_ht=Decimal("300.00"),
            seller_is_importer=True,
        )
        res = compute_vat(sale)
        assert res.scenario == Scenario.IMPORT_SELLER_AS_IMPORTER
        assert res.collector == Collector.SELLER
        assert res.channel == Channel.FR_DOMESTIC
        assert res.vat_country == "FR"
        assert res.vat_rate == Decimal("20")

    def test_ddp_to_germany(self):
        """DDP vers Allemagne → immatriculation locale DE."""
        sale = make_sale(
            stock_country="US",
            buyer_country="DE",
            amount_ht=Decimal("500.00"),
            seller_is_importer=True,
        )
        res = compute_vat(sale)
        assert res.scenario == Scenario.IMPORT_SELLER_AS_IMPORTER
        assert res.channel == Channel.LOCAL_REGISTRATION
        assert res.vat_rate == Decimal("19")
        assert res.vat_amount == Decimal("95.00")

    def test_ddp_below_150_preempted_by_ioss(self):
        """DDP mais ≤ 150 EUR avec numéro IOSS → IOSS gagne."""
        sale = make_sale(
            stock_country="CN",
            buyer_country="FR",
            amount_ht=Decimal("80.00"),
            seller_is_importer=True,
            ioss_number="IM1234567890",
        )
        res = compute_vat(sale)
        # IOSS_DIRECT est évalué avant le bloc import
        assert res.scenario == Scenario.IOSS_DIRECT

    def test_ddp_note_mentions_ddp(self):
        sale = make_sale(
            stock_country="CN", buyer_country="IT",
            amount_ht=Decimal("200"), seller_is_importer=True,
        )
        res = compute_vat(sale)
        assert "DDP" in res.note or "importateur" in res.note.lower()


# ---------------------------------------------------------------------------
# 9. Taux de TVA — rates.py
# ---------------------------------------------------------------------------

class TestVatRates:
    def test_standard_rates_key_countries(self):
        assert vat_rate("FR") == Decimal("20")
        assert vat_rate("DE") == Decimal("19")
        assert vat_rate("HU") == Decimal("27")
        assert vat_rate("LU") == Decimal("16")
        assert vat_rate("MT") == Decimal("18")

    def test_reduced_books(self):
        assert vat_rate("FR", "BOOKS") == Decimal("5.5")
        assert vat_rate("DE", "BOOKS") == Decimal("7")
        assert vat_rate("IT", "BOOKS") == Decimal("4")
        assert vat_rate("CZ", "BOOKS") == Decimal("0")

    def test_super_reduced(self):
        assert vat_rate("FR", "SUPER_REDUCED") == Decimal("2.1")
        assert vat_rate("ES", "SUPER_REDUCED") == Decimal("4")
        assert vat_rate("IT", "SUPER_REDUCED") == Decimal("4")
        assert vat_rate("IE", "SUPER_REDUCED") == Decimal("4.8")

    def test_parking(self):
        assert vat_rate("AT", "PARKING") == Decimal("13")
        assert vat_rate("BE", "PARKING") == Decimal("12")
        assert vat_rate("IE", "PARKING") == Decimal("13.5")
        assert vat_rate("LU", "PARKING") == Decimal("14")
        assert vat_rate("PT", "PARKING") == Decimal("13")

    def test_standard_fallback_unknown_category(self):
        """Catégorie inconnue → taux standard."""
        assert vat_rate("FR", "ELECTRONICS") == Decimal("20")
        assert vat_rate("DE", "UNKNOWN_CAT") == Decimal("19")

    def test_unknown_country_raises(self):
        with pytest.raises(KeyError):
            vat_rate("XX")

    def test_non_eu_raises(self):
        with pytest.raises(KeyError):
            vat_rate("US")


# ---------------------------------------------------------------------------
# 10. Seuil OSS 10 000 EUR — compute_all
# ---------------------------------------------------------------------------

class TestOssThreshold:
    def _make_oss_sale(self, sale_id: str, amount: Decimal) -> Sale:
        return make_sale(
            sale_id=sale_id,
            amount_ht=amount,
            stock_country="FR",
            buyer_country="DE",
            transaction_date="2024-03-01",
        )

    def test_under_threshold_no_oss_option(self):
        """Sous le seuil, option FR non activée → OSS normal."""
        sales = [self._make_oss_sale("S1", Decimal("5000"))]
        results, oss_summary = compute_all(sales, apply_fr_under_threshold=False)
        assert oss_summary.total_oss_ht == Decimal("5000")
        assert not oss_summary.is_threshold_exceeded
        assert results[0].scenario == Scenario.OSS_B2C

    def test_under_threshold_with_fr_option(self):
        """Sous le seuil, option FR activée → taxe FR locale."""
        sales = [self._make_oss_sale("S1", Decimal("3000"))]
        results, oss_summary = compute_all(sales, apply_fr_under_threshold=True)
        assert results[0].channel == Channel.FR_DOMESTIC
        assert results[0].vat_country == "FR"

    def test_threshold_exceeded(self):
        """Total > 10 000 EUR → seuil dépassé."""
        sales = [
            self._make_oss_sale("S1", Decimal("6000")),
            self._make_oss_sale("S2", Decimal("5000")),
        ]
        _, oss_summary = compute_all(sales)
        assert oss_summary.total_oss_ht == Decimal("11000")
        assert oss_summary.is_threshold_exceeded

    def test_threshold_crossing_flagged(self):
        """La vente qui fait franchir le seuil est annotée."""
        sales = [
            self._make_oss_sale("S1", Decimal("9000")),
            self._make_oss_sale("S2", Decimal("2000")),  # fait franchir le seuil
        ]
        results, oss_summary = compute_all(sales, apply_fr_under_threshold=True)
        assert oss_summary.is_threshold_exceeded
        # S2 passe en OSS car elle fait franchir le seuil
        s2_res = next(r for r in results if r.sale.sale_id == "S2")
        assert "FRANCHISSEMENT" in s2_res.note.upper() or s2_res.channel == Channel.OSS

    def test_non_oss_sales_dont_count(self):
        """Les ventes domestiques et exports ne comptent pas dans le cumul OSS."""
        sales = [
            make_sale(sale_id="DOM", stock_country="FR", buyer_country="FR",
                      amount_ht=Decimal("8000"), transaction_date="2024-01-01"),
            self._make_oss_sale("OSS", Decimal("3000")),
        ]
        _, oss_summary = compute_all(sales)
        assert oss_summary.total_oss_ht == Decimal("3000")
        assert not oss_summary.is_threshold_exceeded


# ---------------------------------------------------------------------------
# 11. Rounding & edge cases
# ---------------------------------------------------------------------------

class TestEdgeCases:
    def test_zero_amount(self):
        """Montant nul → TVA nulle."""
        sale = make_sale(amount_ht=Decimal("0"))
        res = compute_vat(sale)
        assert res.vat_amount == Decimal("0.00")

    def test_rounding_half_up(self):
        """33.33 * 20% = 6.666 → arrondi à 6.67."""
        sale = make_sale(
            stock_country="FR", buyer_country="FR",
            amount_ht=Decimal("33.33"),
        )
        res = compute_vat(sale)
        assert res.vat_amount == Decimal("6.67")

    def test_negative_amount_refund(self):
        """Remboursement (montant négatif) → TVA négative."""
        sale = make_sale(amount_ht=Decimal("-50.00"), stock_country="FR", buyer_country="FR")
        res = compute_vat(sale)
        assert res.vat_amount == Decimal("-10.00")

    def test_b2b_without_vat_number_domestic(self):
        """B2B sans numéro de TVA, vente domestique → DOMESTIC."""
        sale = make_sale(
            stock_country="FR", buyer_country="FR",
            buyer_type=BuyerType.B2B,
            buyer_vat_valid=False,
            buyer_vat_number="",
        )
        res = compute_vat(sale)
        assert res.scenario == Scenario.DOMESTIC

    def test_ioss_direct_b2b_not_triggered(self):
        """IOSS_DIRECT ne s'applique qu'au B2C."""
        sale = make_sale(
            stock_country="CN", buyer_country="FR",
            amount_ht=Decimal("80"),
            buyer_type=BuyerType.B2B,
            ioss_number="IM1234567890",
        )
        res = compute_vat(sale)
        assert res.scenario != Scenario.IOSS_DIRECT
