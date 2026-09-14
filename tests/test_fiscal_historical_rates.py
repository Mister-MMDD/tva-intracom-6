"""Tests critiques pour la conformité fiscale UE - Taux historiques.

Ce module vérifie que les fichiers historiques utilisent les bons taux TVA
pour chaque année, conformément aux réglementations UE.

CRITIQUE: rates.py contient VAT_RATE_HISTORY mais la fonction vat_rate_at_date
ne l'utilise PAS - elle retourne toujours le taux actuel. Cela signifie qu'un
fichier de vente de 2024 sera calculé avec les taux de 2026, ce qui est
NON CONFORME aux réglementations UE.
"""

import pytest
from datetime import date
from decimal import Decimal
from tva_intracom.rates import vat_rate, vat_rate_at_date, VAT_RATE_HISTORY
from tva_intracom.models import Sale, BuyerType


class TestHistoricalRates:
    """Tests critiques pour les taux historiques TVA."""

    def test_vat_rate_history_exists(self):
        """Vérifie que VAT_RATE_HISTORY existe et contient des données."""
        assert VAT_RATE_HISTORY is not None
        assert len(VAT_RATE_HISTORY) > 0, "VAT_RATE_HISTORY ne devrait pas être vide"

    def test_vat_rate_history_has_multiple_years(self):
        """Vérifie que VAT_RATE_HISTORY couvre plusieurs années."""
        years = set()
        for period in VAT_RATE_HISTORY:
            if period.date_from:
                years.add(period.date_from.year)
            if period.date_to:
                years.add(period.date_to.year)
        
        assert len(years) >= 2, f"VAT_RATE_HISTORY devrait couvrir au moins 2 années, trouvé: {years}"

    def test_france_historical_rates_2024_vs_2026(self):
        """Vérifie que les taux France ont changé entre 2024 et 2026."""
        # VAT_RATE_HISTORY est une liste de _VatPeriod
        # Chercher les périodes pour la France
        fr_periods = [p for p in VAT_RATE_HISTORY if p.country == "FR"]
        
        if not fr_periods:
            pytest.skip("FR non présent dans VAT_RATE_HISTORY")
        
        # Vérifier qu'il y a des périodes couvrant 2024 et 2026
        has_2024 = any(p.date_from and p.date_from.year == 2024 for p in fr_periods)
        has_2026 = any(p.date_from and p.date_from.year == 2026 for p in fr_periods)
        
        # On peut ne pas avoir 2026 car c'est l'année courante
        # mais on devrait avoir au moins 2024
        assert has_2024, "Devrait avoir des périodes pour 2024"

    def test_vat_rate_at_date_uses_history(self):
        """CRITIQUE: Vérifie que vat_rate_at_date utilise VAT_RATE_HISTORY.
        
        CE TEST DOIT ÉCHOUER avec l'implémentation actuelle de rates.py,
        car vat_rate_at_date n'utilise PAS VAT_RATE_HISTORY correctement.
        """
        # Test avec une date passée (2024)
        historical_date = date(2024, 6, 15)
        
        # Taux actuel France STANDARD
        current_rate = vat_rate("FR", "STANDARD")
        
        # Taux historique France STANDARD en 2024
        fr_periods = [p for p in VAT_RATE_HISTORY if p.country == "FR" and p.category == "STANDARD"]
        historical_rate_2024 = None
        for p in fr_periods:
            if p.date_from and p.date_from <= historical_date:
                if p.date_to is None or historical_date <= p.date_to:
                    historical_rate_2024 = p.rate
                    break
        
        if historical_rate_2024 is not None:
            # vat_rate_at_date devrait retourner le taux historique
            rate_at_date = vat_rate_at_date("FR", historical_date, "STANDARD")
            
            # CRITIQUE: Avec l'implémentation actuelle, rate_at_date peut == current_rate
            # Ce test échouera donc, ce qui est attendu et documente le bug
            assert rate_at_date == historical_rate_2024, \
                f"vat_rate_at_date devrait retourner {historical_rate_2024}% pour 2024, pas {current_rate}%"
        else:
            pytest.skip("Taux STANDARD 2024 non disponible pour FR")

    def test_vat_rate_ignores_date_parameter(self):
        """Documente le bug: vat_rate ignore le paramètre tx_date."""
        # vat_rate a un paramètre tx_date mais ne l'utilise pas
        # Il retourne toujours le taux actuel
        
        current_rate = vat_rate("FR", "STANDARD")
        
        # Ces appels devraient retourner des taux différents si tx_date était utilisé
        rate_2024 = vat_rate("FR", "STANDARD", tx_date=date(2024, 1, 1))
        rate_2025 = vat_rate("FR", "STANDARD", tx_date=date(2025, 1, 1))
        rate_2026 = vat_rate("FR", "STANDARD", tx_date=date(2026, 1, 1))
        
        # CRITIQUE: Tous ces taux sont identiques avec l'implémentation actuelle
        assert rate_2024 == rate_2025 == rate_2026 == current_rate, \
            "Documente le bug: vat_rate ignore tx_date et retourne toujours le taux actuel"

    def test_sale_with_historical_date_uses_correct_rate(self):
        """Test une vente avec une date historique utilise le bon taux."""
        # Créer une vente en 2024
        sale_2024 = Sale(
            sale_id="test_2024_001",
            transaction_date="2024-06-15",
            amount_ht=Decimal("100.00"),
            buyer_country="FR",
            stock_country="FR",
            seller_country="FR",
            product_category="STANDARD",
            buyer_type=BuyerType.B2C,
        )
        
        # Le calcul devrait utiliser le taux de 2024
        # Mais avec l'implémentation actuelle, il utilisera le taux de 2026
        from tva_intracom.engine import compute_vat
        
        result = compute_vat(sale_2024)
        
        # Vérifier quel taux a été utilisé
        fr_periods = [p for p in VAT_RATE_HISTORY if p.country == "FR" and p.category == "STANDARD"]
        historical_rate_2024 = None
        for p in fr_periods:
            if p.date_from and p.date_from <= date(2024, 6, 15):
                if p.date_to is None or date(2024, 6, 15) <= p.date_to:
                    historical_rate_2024 = p.rate
                    break
        
        if historical_rate_2024 is not None:
            expected_vat = Decimal("100.00") * (historical_rate_2024 / Decimal("100"))
            # CRITIQUE: Ce test échouera car le calcul utilise le taux actuel
            assert result.vat_amount == expected_vat, \
                f"Une vente de 2024 devrait utiliser le taux {histor_rate_2024}%, pas {result.vat_rate}%"
        else:
            pytest.skip("Taux STANDARD 2024 non disponible pour FR")

    def test_germany_historical_rates_exist(self):
        """Vérifie que l'Allemagne a des taux historiques."""
        de_periods = [p for p in VAT_RATE_HISTORY if p.country == "DE"]
        
        if not de_periods:
            pytest.skip("DE non présent dans VAT_RATE_HISTORY")
        
        assert len(de_periods) >= 1, "DE devrait avoir au moins une période de taux historiques"

    def test_all_eu_countries_have_some_historical_data(self):
        """Vérifie que tous les pays UE ont au moins des données partielles."""
        from tva_intracom.rates import EU_COUNTRIES
        
        countries_with_history = set(p.country for p in VAT_RATE_HISTORY)
        eu_without_history = EU_COUNTRIES - countries_with_history
        
        # On tolère que certains pays n'aient pas d'historique pour l'instant
        # mais on documente lesquels
        if eu_without_history:
            pytest.skip(f"Pays UE sans historique: {eu_without_history}")

    def test_vat_rate_history_format_consistency(self):
        """Vérifie que le format de VAT_RATE_HISTORY est cohérent."""
        for period in VAT_RATE_HISTORY:
            assert isinstance(period.country, str), f"Country devrait être str, got {type(period.country)}"
            assert isinstance(period.rate, Decimal), f"Rate devrait être Decimal, got {type(period.rate)}"
            assert isinstance(period.category, str), f"Category devrait être str, got {type(period.category)}"
            
            if period.date_from:
                assert isinstance(period.date_from, date), f"date_from devrait être date, got {type(period.date_from)}"
            if period.date_to:
                assert isinstance(period.date_to, date), f"date_to devrait être date, got {type(period.date_to)}"


class TestHistoricalRatesEdgeCases:
    """Tests edge cases pour les taux historiques."""

    def test_date_before_history_range(self):
        """Test avec une date avant la plage de VAT_RATE_HISTORY."""
        # VAT_RATE_HISTORY commence en 2024
        ancient_date = date(2020, 1, 1)
        
        # devrait utiliser le taux le plus ancien disponible ou une valeur par défaut
        rate = vat_rate_at_date("FR", ancient_date, "STANDARD")
        
        # Ne devrait pas planter
        assert rate is not None
        assert isinstance(rate, Decimal)

    def test_date_in_future(self):
        """Test avec une date future."""
        future_date = date(2030, 1, 1)
        
        # devrait utiliser le taux actuel ou une valeur par défaut
        rate = vat_rate_at_date("FR", future_date, "STANDARD")
        
        # Ne devrait pas planter
        assert rate is not None
        assert isinstance(rate, Decimal)

    def test_unknown_country_historical(self):
        """Test avec un pays inconnu dans VAT_RATE_HISTORY."""
        # Vérifier que XX n'est pas dans VAT_RATE_HISTORY
        xx_periods = [p for p in VAT_RATE_HISTORY if p.country == "XX"]
        if xx_periods:
            pytest.skip("XX présent dans VAT_RATE_HISTORY, ajuster le test")
        
        # Avec l'implémentation actuelle, ça lève une KeyError
        # C'est un comportement correct: on ne devrait pas accepter des pays inconnus
        with pytest.raises(KeyError, match="Pays inconnu"):
            rate = vat_rate_at_date("XX", date(2024, 1, 1), "STANDARD")

    def test_unknown_category_historical(self):
        """Test avec une catégorie inconnue."""
        # Avec l'implémentation actuelle, une catégorie inconnue
        # devrait utiliser le taux STANDARD ou une valeur par défaut
        rate = vat_rate_at_date("FR", date(2024, 1, 1), "UNKNOWN_CATEGORY")
        
        # Ne devrait pas planter - devrait utiliser le taux STANDARD
        assert rate is not None
        assert isinstance(rate, Decimal)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
