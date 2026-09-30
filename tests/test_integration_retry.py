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
import urllib.error

# Permet d'importer le package tva_intracom
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import pytest

from tva_intracom import ecb_rates, vat_rates_db
from tva_intracom.vies_engine import ViesResult, check_vat_with_retry
from tva_intracom.ecb_rates import get_rate
from tva_intracom.vat_rates_db import vat_rate
from datetime import date


@pytest.fixture(autouse=True)
def reset_rate_caches():
    ecb_rates._rate_cache.clear()
    ecb_rates._failed_pairs.clear()
    vat_rates_db.clear_cache(persistent=False)
    yield
    ecb_rates._rate_cache.clear()
    ecb_rates._failed_pairs.clear()
    vat_rates_db.clear_cache(persistent=False)


def _http_response(body: bytes):
    response = MagicMock()
    response.__enter__.return_value.read.return_value = body
    return response


# ---------------------------------------------------------------------------
# Tests Retry VIES
# ---------------------------------------------------------------------------

class TestViesRetry:
    """Test du retry exponentiel pour VIES."""

    @patch('tva_intracom.vies_engine.check_vat')
    def test_vies_retry_on_timeout(self, mock_vies):
        """Test du retry en cas de timeout VIES."""
        # Simuler 2 réponses transitoires puis un succès.
        mock_vies.side_effect = [
            ViesResult(False, "DE", "123456789", error="Timeout VIES"),
            ViesResult(False, "DE", "123456789", error="Timeout VIES"),
            ViesResult(True, "DE", "123456789", name="Test Company"),
        ]

        result = check_vat_with_retry("DE", "123456789", max_attempts=3, base_delay=0)

        assert result.valid
        assert mock_vies.call_count == 3

    @patch('tva_intracom.vies_engine.check_vat')
    def test_vies_retry_exhausted(self, mock_vies):
        """Test de l'épuisement des retries VIES."""
        mock_vies.return_value = ViesResult(False, "DE", "123456789", error="Timeout VIES")

        result = check_vat_with_retry("DE", "123456789", max_attempts=3, base_delay=0)

        assert not result.valid
        assert mock_vies.call_count == 3


# ---------------------------------------------------------------------------
# Tests Retry BCE
# ---------------------------------------------------------------------------

class TestEcbRetry:
    """Test du retry exponentiel pour BCE."""

    @patch('tva_intracom.ecb_rates.urllib.request.urlopen')
    def test_ecb_retry_on_network_error(self, mock_urlopen):
        """Test du retry en cas d'erreur réseau BCE."""
        mock_urlopen.side_effect = [
            urllib.error.URLError("BCE network error"),
            urllib.error.URLError("BCE network error"),
            _http_response(b'{"success": true}'),
        ]
        with patch.object(ecb_rates, "_is_ssl_broken", return_value=False), \
             patch("tva_intracom.ecb_rates.time.sleep"):
            response = ecb_rates._request_ecb("https://example.test", "test")

        assert response == {"success": True}
        assert mock_urlopen.call_count == 3

    @patch('tva_intracom.ecb_rates.urllib.request.urlopen')
    def test_ecb_retry_exhausted(self, mock_urlopen):
        """Test de l'épuisement des retries BCE."""
        mock_urlopen.side_effect = urllib.error.URLError("BCE network error")
        with patch.object(ecb_rates, "_is_ssl_broken", return_value=False), \
             patch("tva_intracom.ecb_rates.time.sleep"):
            response = ecb_rates._request_ecb("https://example.test", "test")

        assert response is None
        assert mock_urlopen.call_count == ecb_rates._FETCH_MAX_ATTEMPTS

    @patch('tva_intracom.ecb_rates._fetch_ecb_rate', return_value=None)
    @patch('tva_intracom.ecb_rates._db_get_rate', return_value=None)
    def test_ecb_negative_cache(self, _mock_db, mock_ecb):
        """Test du cache négatif pour les échecs BCE."""
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

    @patch('tva_intracom.vat_rates_db.urllib.request.urlopen')
    def test_tedb_retry_on_network_error(self, mock_urlopen):
        """Test du retry en cas d'erreur réseau TEDB."""
        mock_urlopen.side_effect = [
            urllib.error.URLError("TEDB network error"),
            urllib.error.URLError("TEDB network error"),
            _http_response(b"<response/>"),
        ]
        with patch.object(vat_rates_db, "_is_ssl_broken", return_value=False), \
             patch("tva_intracom.vat_rates_db.time.sleep"):
            response = vat_rates_db._request_tedb("FR", date(2026, 1, 15))

        assert response is not None
        assert mock_urlopen.call_count == 3

    @patch('tva_intracom.vat_rates_db.urllib.request.urlopen')
    def test_tedb_retry_exhausted(self, mock_urlopen):
        """Test de l'épuisement des retries TEDB."""
        mock_urlopen.side_effect = urllib.error.URLError("TEDB network error")
        with patch.object(vat_rates_db, "_is_ssl_broken", return_value=False), \
             patch("tva_intracom.vat_rates_db.time.sleep"):
            response = vat_rates_db._request_tedb("FR", date(2026, 1, 15))

        assert response is None
        assert mock_urlopen.call_count == vat_rates_db._FETCH_MAX_ATTEMPTS

    @patch('tva_intracom.vat_rates_db._fetch_tedb_rates', return_value=None)
    @patch('tva_intracom.vat_rates_db._db_get_rate', return_value=None)
    def test_tedb_fallback_on_failure(self, _mock_db, _mock_tedb):
        """Test du repli statique après échec TEDB."""

        rate = vat_rate("FR", "STANDARD", date(2026, 1, 15))
        assert rate is not None  # Le repli statique retourne un taux


# ---------------------------------------------------------------------------
# Tests Retry exponentiel
# ---------------------------------------------------------------------------

class TestExponentialBackoff:
    """Test du backoff exponentiel."""

    @patch('tva_intracom.vies_engine.check_vat')
    def test_exponential_backoff_timing(self, mock_vies):
        """Test que le délai entre les retries augmente exponentiellement."""
        mock_vies.side_effect = [
            ViesResult(False, "DE", "123456789", error="Timeout VIES"),
            ViesResult(False, "DE", "123456789", error="Timeout VIES"),
            ViesResult(True, "DE", "123456789"),
        ]
        delays = []

        with patch("tva_intracom.vies_engine.time.sleep", side_effect=delays.append):
            result = check_vat_with_retry(
                "DE", "123456789", max_attempts=3, base_delay=0.1
            )

        assert result.valid
        assert delays == [0.1, 0.2]

    @patch('tva_intracom.ecb_rates._fetch_ecb_rate', return_value=None)
    @patch('tva_intracom.ecb_rates._db_get_rate', return_value=None)
    def test_rate_lookup_returns_none_after_fetch_failure(self, _mock_db, mock_ecb):
        rate = get_rate("USD", date(2026, 1, 15))
        assert rate is None
        assert mock_ecb.call_count == 1


# ---------------------------------------------------------------------------
# Tests Cache négatif
# ---------------------------------------------------------------------------

class TestNegativeCache:
    """Test du cache négatif des échecs."""

    @patch('tva_intracom.ecb_rates._fetch_ecb_rate')
    @patch('tva_intracom.ecb_rates._db_get_rate', return_value=None)
    def test_negative_cache_per_key(self, _mock_db, mock_ecb):
        """Test que le cache négatif est par clé (currency, date)."""
        mock_ecb.return_value = None
        
        # Appel pour USD/2026-01-15
        rate1 = get_rate("USD", date(2026, 1, 15))
        assert rate1 is None
        
        # Appel pour GBP/2026-01-15 (devrait appeler l'API)
        mock_ecb.return_value = Decimal('0.85')
        rate2 = get_rate("GBP", date(2026, 1, 15))
        assert rate2 == Decimal('0.85')
        
        # Vérifier que l'API a été appelée 2 fois (une par clé)
        assert mock_ecb.call_count == 2


# ---------------------------------------------------------------------------
# Tests Résilience
# ---------------------------------------------------------------------------

class TestResilience:
    """Test de la résilience du système."""

    @patch('tva_intracom.ecb_rates._fetch_ecb_rate', return_value=None)
    @patch('tva_intracom.vat_rates_db._fetch_tedb_rates', return_value=None)
    @patch('tva_intracom.vat_rates_db._db_get_rate', return_value=None)
    @patch('tva_intracom.vies_engine.check_vat')
    def test_partial_failure_resilience(self, mock_vies, _mock_vat_db, mock_tedb, mock_ecb):
        """Test que le système reste fonctionnel en cas de défaillance partielle."""
        mock_vies.return_value = ViesResult(True, "DE", "123456789", name="Test Company")
        
        # BCE échoue (mais a un cache)
        mock_ecb.return_value = None
        
        # TEDB échoue (mais a un repli statique)
        mock_tedb.return_value = None
        
        # Le système devrait rester fonctionnel grâce aux caches et replis
        vat_number = "DE123456789"
        result = check_vat_with_retry("DE", vat_number[2:], max_attempts=1)
        assert result.valid
        
        # BCE devrait utiliser le cache ou retourner None
        rate = get_rate("USD", date(2026, 1, 15))
        # Peut être None si le cache est vide
        
        # TEDB devrait utiliser le repli statique
        rate = vat_rate("FR", "STANDARD", date(2026, 1, 15))
        assert rate is not None  # Repli statique

    @patch('tva_intracom.vies_engine.check_vat')
    def test_vies_service_unavailable_handling(self, mock_vies):
        """Test de la gestion de l'indisponibilité du service VIES."""
        mock_vies.return_value = ViesResult(
            False, "DE", "123456789", error="VIES service unavailable"
        )

        result = check_vat_with_retry("DE", "123456789", max_attempts=1)

        assert not result.valid
        assert result.error == "VIES service unavailable"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
