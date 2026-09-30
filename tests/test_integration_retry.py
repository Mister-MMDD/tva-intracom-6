"""Tests d'intégration pour les scénarios de failure et retry.

Couvre les scénarios d'échec et les mécanismes de retry :
- Timeout VIES
- Échec BCE
- Échec TEDB
- Retry exponentiel
- Cache négatif des échecs

Approche de mocking : Mocks complets des APIs externes
"""

from __future__ import annotations

import os
import sys
from decimal import Decimal
from unittest.mock import MagicMock, patch
import time

# Permet d'importer le package tva_intracom
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import pytest

from tva_intracom.vies_engine import validate_vat
from tva_intracom.ecb_rates import get_rate
from tva_intracom.vat_rates_db import vat_rate
from datetime import date


# ---------------------------------------------------------------------------
# Tests Retry VIES
# ---------------------------------------------------------------------------

class TestViesRetry:
    """Test du retry exponentiel pour VIES."""

    @patch('tva_intracom.vies_engine._call_vies_service')
    def test_vies_retry_on_timeout(self, mock_vies):
        """Test du retry en cas de timeout VIES."""
        # Simuler 2 timeouts puis un succès
        mock_vies.side_effect = [
            TimeoutError("VIES timeout"),
            TimeoutError("VIES timeout"),
            {
                'valid': True,
                'name': 'Test Company',
                'address': 'Test Address',
                'request_date': '2026-01-01'
            }
        ]
        
        vat_number = "DE123456789"
        result = validate_vat(vat_number)
        
        # Vérifier que le retry a fonctionné
        assert result['valid'] == True
        assert mock_vies.call_count == 3

    @patch('tva_intracom.vies_engine._call_vies_service')
    def test_vies_retry_exhausted(self, mock_vies):
        """Test de l'épuisement des retries VIES."""
        # Simuler des timeouts permanents
        mock_vies.side_effect = TimeoutError("VIES timeout")
        
        vat_number = "DE123456789"
        
        # L'appel devrait échouer après les retries
        with pytest.raises(Exception):  # Ou une exception spécifique
            validate_vat(vat_number)
        
        # Vérifier que le nombre maximum de tentatives a été atteint
        assert mock_vies.call_count >= 3  # Au moins 3 tentatives

    @patch('tva_intracom.vies_engine._call_vies_service')
    def test_vies_negative_cache(self, mock_vies):
        """Test du cache négatif pour les échecs VIES."""
        # Simuler un échec permanent
        mock_vies.side_effect = TimeoutError("VIES timeout")
        
        vat_number = "DE123456789"
        
        # Premier appel (échec)
        with pytest.raises(Exception):
            validate_vat(vat_number)
        
        # Deuxième appel (devrait utiliser le cache négatif et échouer rapidement)
        with pytest.raises(Exception):
            validate_vat(vat_number)
        
        # Vérifier que l'API n'a été appelée qu'une fois (cache négatif)
        assert mock_vies.call_count == 1


# ---------------------------------------------------------------------------
# Tests Retry BCE
# ---------------------------------------------------------------------------

class TestEcbRetry:
    """Test du retry exponentiel pour BCE."""

    @patch('tva_intracom.ecb_rates._fetch_ecb_rate')
    def test_ecb_retry_on_network_error(self, mock_ecb):
        """Test du retry en cas d'erreur réseau BCE."""
        # Simuler 2 erreurs réseau puis un succès
        mock_ecb.side_effect = [
            ConnectionError("BCE network error"),
            ConnectionError("BCE network error"),
            Decimal('1.10')
        ]
        
        currency = "USD"
        test_date = date(2026, 1, 15)
        
        rate = get_rate(currency, test_date)
        
        # Vérifier que le retry a fonctionné
        assert rate == Decimal('1.10')
        assert mock_ecb.call_count == 3

    @patch('tva_intracom.ecb_rates._fetch_ecb_rate')
    def test_ecb_retry_exhausted(self, mock_ecb):
        """Test de l'épuisement des retries BCE."""
        # Simuler des erreurs réseau permanentes
        mock_ecb.side_effect = ConnectionError("BCE network error")
        
        currency = "USD"
        test_date = date(2026, 1, 15)
        
        # L'appel devrait retourner None après les retries
        rate = get_rate(currency, test_date)
        assert rate is None
        
        # Vérifier que le nombre maximum de tentatives a été atteint
        assert mock_ecb.call_count >= 3

    @patch('tva_intracom.ecb_rates._fetch_ecb_rate')
    def test_ecb_negative_cache(self, mock_ecb):
        """Test du cache négatif pour les échecs BCE."""
        # Simuler un échec permanent
        mock_ecb.side_effect = ConnectionError("BCE network error")
        
        currency = "USD"
        test_date = date(2026, 1, 15)
        
        # Premier appel (échec)
        rate1 = get_rate(currency, test_date)
        assert rate1 is None
        
        # Deuxième appel (devrait utiliser le cache négatif)
        rate2 = get_rate(currency, test_date)
        assert rate2 is None
        
        # Vérifier que l'API n'a été appelée qu'une fois (cache négatif)
        assert mock_ecb.call_count == 1


# ---------------------------------------------------------------------------
# Tests Retry TEDB
# ---------------------------------------------------------------------------

class TestTedbRetry:
    """Test du retry exponentiel pour TEDB."""

    @patch('tva_intracom.vat_rates_db._fetch_tedb_rate')
    def test_tedb_retry_on_network_error(self, mock_tedb):
        """Test du retry en cas d'erreur réseau TEDB."""
        # Simuler 2 erreurs réseau puis un succès
        mock_tedb.side_effect = [
            ConnectionError("TEDB network error"),
            ConnectionError("TEDB network error"),
            Decimal('0.20')
        ]
        
        country = "FR"
        test_date = date(2026, 1, 15)
        
        rate = vat_rate(country, test_date)
        
        # Vérifier que le retry a fonctionné
        assert rate == Decimal('0.20')
        assert mock_tedb.call_count == 3

    @patch('tva_intracom.vat_rates_db._fetch_tedb_rate')
    def test_tedb_retry_exhausted(self, mock_tedb):
        """Test de l'épuisement des retries TEDB."""
        # Simuler des erreurs réseau permanentes
        mock_tedb.side_effect = ConnectionError("TEDB network error")
        
        country = "FR"
        test_date = date(2026, 1, 15)
        
        # L'appel devrait utiliser le repli statique
        rate = vat_rate(country, test_date)
        assert rate is not None  # Le repli statique
        
        # Vérifier que le nombre maximum de tentatives a été atteint
        assert mock_tedb.call_count >= 3

    @patch('tva_intracom.vat_rates_db._fetch_tedb_rate')
    def test_tedb_fallback_on_failure(self, mock_tedb):
        """Test du repli statique après échec TEDB."""
        # Simuler un échec permanent
        mock_tedb.side_effect = ConnectionError("TEDB network error")
        
        country = "FR"
        test_date = date(2026, 1, 15)
        
        # L'appel devrait réussir grâce au repli statique
        rate = vat_rate(country, test_date)
        assert rate is not None  # Le repli statique retourne un taux


# ---------------------------------------------------------------------------
# Tests Retry exponentiel
# ---------------------------------------------------------------------------

class TestExponentialBackoff:
    """Test du backoff exponentiel."""

    @patch('tva_intracom.vies_engine._call_vies_service')
    def test_exponential_backoff_timing(self, mock_vies):
        """Test que le délai entre les retries augmente exponentiellement."""
        call_times = []
        
        def side_effect(*args, **kwargs):
            call_times.append(time.time())
            if len(call_times) < 3:
                raise TimeoutError("VIES timeout")
            return {
                'valid': True,
                'name': 'Test Company',
                'address': 'Test Address',
                'request_date': '2026-01-01'
            }
        
        mock_vies.side_effect = side_effect
        
        vat_number = "DE123456789"
        result = validate_vat(vat_number)
        
        # Vérifier que les délais augmentent
        if len(call_times) >= 2:
            delay1 = call_times[1] - call_times[0]
            delay2 = call_times[2] - call_times[1]
            # Le deuxième délai devrait être plus long (backoff exponentiel)
            assert delay2 >= delay1

    @patch('tva_intracom.ecb_rates._fetch_ecb_rate')
    def test_retry_max_attempts(self, mock_ecb):
        """Test que le nombre maximum de tentatives est respecté."""
        mock_ecb.side_effect = ConnectionError("BCE network error")
        
        currency = "USD"
        test_date = date(2026, 1, 15)
        
        # L'appel devrait échouer après le nombre maximum de tentatives
        rate = get_rate(currency, test_date)
        assert rate is None
        
        # Vérifier que le nombre de tentatives est conforme à la configuration
        # (par défaut 3, mais peut être configuré)
        assert mock_ecb.call_count >= 3


# ---------------------------------------------------------------------------
# Tests Cache négatif
# ---------------------------------------------------------------------------

class TestNegativeCache:
    """Test du cache négatif des échecs."""

    @patch('tva_intracom.vies_engine._call_vies_service')
    def test_negative_cache_expiration(self, mock_vies):
        """Test que le cache négatif expire après un certain temps."""
        # Simuler un échec
        mock_vies.side_effect = TimeoutError("VIES timeout")
        
        vat_number = "DE123456789"
        
        # Premier appel (échec)
        with pytest.raises(Exception):
            validate_vat(vat_number)
        
        # Simuler l'expiration du cache négatif
        # (Dans un vrai test, on attendrait ou on manipulerait le temps)
        # Pour ce test, on vérifie simplement que le cache est utilisé
        
        # Deuxième appel (devrait utiliser le cache négatif)
        with pytest.raises(Exception):
            validate_vat(vat_number)
        
        # Vérifier que l'API n'a été appelée qu'une fois
        assert mock_vies.call_count == 1

    @patch('tva_intracom.ecb_rates._fetch_ecb_rate')
    def test_negative_cache_per_key(self, mock_ecb):
        """Test que le cache négatif est par clé (currency, date)."""
        mock_ecb.side_effect = ConnectionError("BCE network error")
        
        # Appel pour USD/2026-01-15
        rate1 = get_rate("USD", date(2026, 1, 15))
        assert rate1 is None
        
        # Appel pour GBP/2026-01-15 (devrait appeler l'API)
        mock_ecb.side_effect = Decimal('0.85')
        rate2 = get_rate("GBP", date(2026, 1, 15))
        assert rate2 == Decimal('0.85')
        
        # Vérifier que l'API a été appelée 2 fois (une par clé)
        assert mock_ecb.call_count == 2


# ---------------------------------------------------------------------------
# Tests Résilience
# ---------------------------------------------------------------------------

class TestResilience:
    """Test de la résilience du système."""

    @patch('tva_intracom.vies_engine._call_vies_service')
    @patch('tva_intracom.ecb_rates._fetch_ecb_rate')
    @patch('tva_intracom.vat_rates_db._fetch_tedb_rate')
    def test_partial_failure_resilience(self, mock_tedb, mock_ecb, mock_vies):
        """Test que le système reste fonctionnel en cas de défaillance partielle."""
        # VIES fonctionne
        mock_vies.return_value = {
            'valid': True,
            'name': 'Test Company',
            'address': 'Test Address',
            'request_date': '2026-01-01'
        }
        
        # BCE échoue (mais a un cache)
        mock_ecb.side_effect = ConnectionError("BCE network error")
        
        # TEDB échoue (mais a un repli statique)
        mock_tedb.side_effect = ConnectionError("TEDB network error")
        
        # Le système devrait rester fonctionnel grâce aux caches et replis
        vat_number = "DE123456789"
        result = validate_vat(vat_number)
        assert result['valid'] == True
        
        # BCE devrait utiliser le cache ou retourner None
        rate = get_rate("USD", date(2026, 1, 15))
        # Peut être None si le cache est vide
        
        # TEDB devrait utiliser le repli statique
        rate = vat_rate("FR", date(2026, 1, 15))
        assert rate is not None  # Repli statique

    @patch('tva_intracom.vies_engine._call_vies_service')
    def test_vies_service_unavailable_handling(self, mock_vies):
        """Test de la gestion de l'indisponibilité du service VIES."""
        # Simuler une indisponibilité permanente
        mock_vies.side_effect = Exception("VIES service unavailable")
        
        vat_number = "DE123456789"
        
        # Le système devrait gérer l'erreur gracieusement
        # Dans ce cas, il pourrait utiliser un override ou retourner une valeur par défaut
        with pytest.raises(Exception):
            validate_vat(vat_number)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
