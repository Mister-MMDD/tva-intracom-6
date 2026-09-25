"""Tests pour le traitement de Monaco dans le calcul TVA.

Monaco est assimilé à la France pour la TVA selon la convention
fiscale franco-monégasque. Ce module vérifie que tous les scénarios
Monaco sont correctement traités.
"""

import pytest
from decimal import Decimal
from tva_intracom.models import Sale, BuyerType
from tva_intracom.engine import compute_vat


class TestMonacoScenarios:
    """Tests pour les scénarios Monaco."""

    def test_monaco_stock_monaco_buyer_domestic(self):
        """Vente avec stock Monaco et acheteur Monaco = vente domestique FR."""
        sale = Sale(
            sale_id="test_mc_001",
            transaction_date="2026-06-15",
            amount_ht=Decimal("100.00"),
            buyer_country="MC",
            stock_country="MC",
            seller_country="FR",
            product_category="STANDARD",
            buyer_type=BuyerType.B2C,
        )
        
        result = compute_vat(sale)
        
        # Monaco assimilé à la France pour la TVA
        # Devrait être DOMESTIC avec taux FR (20%)
        assert result.scenario.value == "DOMESTIC"
        assert result.vat_rate == Decimal("20")
        assert result.vat_amount == Decimal("20.00")

    def test_monaco_stock_france_buyer_monaco(self):
        """Vente avec stock France et acheteur Monaco = vente domestique FR."""
        sale = Sale(
            sale_id="test_mc_002",
            transaction_date="2026-06-15",
            amount_ht=Decimal("100.00"),
            buyer_country="MC",
            stock_country="FR",
            seller_country="FR",
            product_category="STANDARD",
            buyer_type=BuyerType.B2C,
        )
        
        result = compute_vat(sale)
        
        # Monaco assimilé à la France
        # Stock FR + buyer MC = DOMESTIC
        assert result.scenario.value == "DOMESTIC"
        assert result.vat_rate == Decimal("20")

    def test_monaco_stock_monaco_buyer_germany_b2c(self):
        """Vente avec stock Monaco et acheteur Allemagne B2C = OSS."""
        sale = Sale(
            sale_id="test_mc_003",
            transaction_date="2026-06-15",
            amount_ht=Decimal("100.00"),
            buyer_country="DE",
            stock_country="MC",
            seller_country="FR",
            product_category="STANDARD",
            buyer_type=BuyerType.B2C,
        )
        
        result = compute_vat(sale)
        
        # Stock MC (assimilé FR) + buyer DE B2C = OSS_B2C
        assert result.scenario.value == "OSS_B2C"
        # Taux DE (19%)
        assert result.vat_rate == Decimal("19")

    def test_monaco_stock_monaco_buyer_germany_b2b_valid_vat(self):
        """Vente avec stock Monaco et acheteur Allemagne B2B avec TVA valide.

        Note (audit sécurité 2026-09-13) : ce cas était traité à tort en
        OSS_B2C — le branchement spécifique "stock == MC" ne testait jamais
        `buyer_type == B2B`, contrairement au cas général (engine.py, ~L454).
        Corrigé : une livraison B2B intracommunautaire au départ de Monaco
        (assimilé France) vers un acheteur UE assujetti avec TVA valide doit
        être exonérée avec autoliquidation (Art. 262 ter CGI), exactement
        comme un départ de stock France.
        """
        sale = Sale(
            sale_id="test_mc_004",
            transaction_date="2026-06-15",
            amount_ht=Decimal("100.00"),
            buyer_country="DE",
            stock_country="MC",
            seller_country="FR",
            product_category="STANDARD",
            buyer_type=BuyerType.B2B,
            buyer_vat_valid=True,
            buyer_vat_number="DE123456789",
        )
        
        result = compute_vat(sale)
        
        assert result.scenario.value == "B2B_REVERSE_CHARGE"
        assert result.vat_rate == Decimal("0")
        assert result.vat_amount == Decimal("0.00")

    def test_monaco_in_eu_countries_set(self):
        """Vérifie que Monaco est dans l'ensemble EU_COUNTRIES."""
        from tva_intracom.rates import EU_COUNTRIES
        
        assert "MC" in EU_COUNTRIES, "Monaco devrait être dans EU_COUNTRIES"

    def test_monaco_currency_eur(self):
        """Vérifie que Monaco utilise l'EUR."""
        from tva_intracom.rates import COUNTRY_CURRENCIES
        
        assert COUNTRY_CURRENCIES.get("MC") == "EUR", "Monaco devrait utiliser l'EUR"

    def test_monaco_fiscal_meta_same_as_france(self):
        """Vérifie que les métadonnées fiscales de Monaco sont identiques à la France."""
        from tva_intracom.rates import COUNTRY_FISCAL_META
        
        fr_meta = COUNTRY_FISCAL_META.get("FR")
        mc_meta = COUNTRY_FISCAL_META.get("MC")
        
        assert fr_meta is not None, "FR devrait avoir des métadonnées fiscales"
        assert mc_meta is not None, "MC devrait avoir des métadonnées fiscales"
        
        # Monaco devrait avoir les mêmes métadonnées que la France
        assert fr_meta == mc_meta, "Monaco devrait avoir les mêmes métadonnées que la France"

    def test_monaco_reduced_rates_same_as_france(self):
        """Vérifie que les taux réduits de Monaco sont identiques à la France."""
        from tva_intracom.rates import REDUCED_VAT_RATES
        
        fr_reduced = REDUCED_VAT_RATES.get("FR", {})
        mc_reduced = REDUCED_VAT_RATES.get("MC", {})
        
        # Monaco devrait avoir les mêmes taux réduits que la France
        assert fr_reduced == mc_reduced, "Monaco devrait avoir les mêmes taux réduits que la France"

    def test_monaco_standard_rate_same_as_france(self):
        """Vérifie que le taux standard de Monaco est identique à la France."""
        from tva_intracom.rates import STANDARD_VAT_RATES
        
        fr_standard = STANDARD_VAT_RATES.get("FR")
        mc_standard = STANDARD_VAT_RATES.get("MC")
        
        assert fr_standard == mc_standard, "Monaco devrait avoir le même taux standard que la France"
        assert mc_standard == Decimal("20"), "Taux standard MC devrait être 20%"

    def test_monaco_not_in_non_fiscal_eu(self):
        """Vérifie que Monaco n'est PAS dans les territoires exclus fiscaux."""
        from tva_intracom.rates import NON_FISCAL_EU_POSTCODES
        
        # Monaco ne devrait pas être dans les territoires exclus
        assert "MC" not in NON_FISCAL_EU_POSTCODES, "Monaco ne devrait pas être exclu fiscalement"

    def test_monaco_oss_export_uses_france_rate(self):
        """Vérifie que l'export OSS Monaco utilise le taux standard FR."""
        # Scénario: vente depuis stock MC vers DE
        sale = Sale(
            sale_id="test_mc_oss_001",
            transaction_date="2026-06-15",
            amount_ht=Decimal("100.00"),
            buyer_country="DE",
            stock_country="MC",
            seller_country="FR",
            product_category="STANDARD",
            buyer_type=BuyerType.B2C,
        )
        
        result = compute_vat(sale)
        
        # Pour l'export OSS, le taux de départ (MC) est utilisé
        # Mais MC est assimilé à FR, donc c'est le taux DE qui s'applique
        assert result.vat_rate == Decimal("19")  # Taux DE


class TestMonacoEdgeCases:
    """Tests edge cases pour Monaco."""

    def test_monaco_buyer_with_vat_number_b2c(self):
        """Vente B2C avec numéro TVA Monaco = B2C (numéro ignoré)."""
        sale = Sale(
            sale_id="test_mc_edge_001",
            transaction_date="2026-06-15",
            amount_ht=Decimal("100.00"),
            buyer_country="MC",
            stock_country="FR",
            seller_country="FR",
            product_category="STANDARD",
            buyer_type=BuyerType.B2C,
            buyer_vat_number="MC12345678901",  # Monaco a des numéros TVA
        )
        
        result = compute_vat(sale)
        
        # B2C = DOMESTIC, numéro TVA ignoré
        assert result.scenario.value == "DOMESTIC"

    def test_monaco_export_to_non_eu(self):
        """Export de Monaco vers pays hors UE = Export."""
        sale = Sale(
            sale_id="test_mc_export_001",
            transaction_date="2026-06-15",
            amount_ht=Decimal("100.00"),
            buyer_country="US",
            stock_country="MC",
            seller_country="FR",
            product_category="STANDARD",
            buyer_type=BuyerType.B2C,
        )
        
        result = compute_vat(sale)
        
        # Export vers hors UE = EXPORT (exonéré)
        assert result.scenario.value == "EXPORT"
        assert result.vat_amount == Decimal("0")

    def test_monaco_reduced_category(self):
        """Vente Monaco avec catégorie réduite."""
        sale = Sale(
            sale_id="test_mc_reduced_001",
            transaction_date="2026-06-15",
            amount_ht=Decimal("100.00"),
            buyer_country="MC",
            stock_country="MC",
            seller_country="FR",
            product_category="FOOD",
            buyer_type=BuyerType.B2C,
        )
        
        result = compute_vat(sale)
        
        # Monaco utilise les mêmes taux réduits que la France
        # FOOD = 5.5% en France
        assert result.scenario.value == "DOMESTIC"
        assert result.vat_rate == Decimal("5.5")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
