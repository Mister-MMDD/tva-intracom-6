"""Tests pour le respect de l'art. 5 bis Règl. UE 2020/194 (taux de clôture BCE).

Règlement UE 2020/194, art. 5 bis:
"Pour la conversion en euros, les États membres utilisent le taux de change
en vigueur à la date de clôture de la période de référence."

Le taux de clôture est le PREMIER taux publié par la BCE à partir de la date
de clôture (inclus), et non le dernier taux publié avant cette date.
"""

import pytest
from datetime import date
from decimal import Decimal
from unittest.mock import patch, MagicMock
from tva_intracom import ecb_rates


@pytest.fixture(autouse=True)
def reset_ecb_caches():
    ecb_rates._rate_cache.clear()
    ecb_rates._forward_rate_cache.clear()
    ecb_rates._failed_pairs.clear()
    yield
    ecb_rates._rate_cache.clear()
    ecb_rates._forward_rate_cache.clear()
    ecb_rates._failed_pairs.clear()


class TestBCEClosingRate:
    """Tests pour le taux de clôture BCE."""

    def test_closing_rate_looks_forward(self, monkeypatch):
        """Vérifie que le taux de clôture cherche en avant, pas en arrière."""
        closing_date = date(2026, 6, 15)
        forward_lookup = MagicMock(return_value=Decimal("1.08"))
        monkeypatch.setattr(ecb_rates, "_fetch_ecb_rate_forward", forward_lookup)

        assert ecb_rates.get_closing_rate("usd", closing_date) == Decimal("1.08")
        forward_lookup.assert_called_once_with("USD", closing_date)

    def test_forward_vs_historical_lookup(self):
        """Vérifie la distinction entre lookup historique et forward."""
        from tva_intracom.ecb_rates import _fetch_ecb_rate, _fetch_ecb_rate_forward
        
        # _fetch_ecb_rate: dernier taux AVANT la date (historique)
        # _fetch_ecb_rate_forward: PREMIER taux À PARTIR de la date (clôture)
        
        # Les deux fonctions devraient exister
        assert callable(_fetch_ecb_rate)
        assert callable(_fetch_ecb_rate_forward)

    def test_weekend_closing_date_is_passed_to_forward_lookup(self, monkeypatch):
        """Vérifie le traitement des weekends (BCE ne publie pas le weekend)."""
        forward_lookup = MagicMock(return_value=Decimal("1.09"))
        monkeypatch.setattr(ecb_rates, "_fetch_ecb_rate_forward", forward_lookup)

        for closing_date in (date(2026, 6, 13), date(2026, 6, 14)):
            assert ecb_rates.get_closing_rate("USD", closing_date) == Decimal("1.09")

        assert [call.args for call in forward_lookup.call_args_list] == [
            ("USD", date(2026, 6, 13)),
            ("USD", date(2026, 6, 14)),
        ]

    def test_closing_rate_for_oss_period(self, monkeypatch):
        """Vérifie le taux de clôture pour une période OSS."""
        forward_lookup = MagicMock(return_value=Decimal("1.10"))
        monkeypatch.setattr(ecb_rates, "_fetch_ecb_rate_forward", forward_lookup)

        assert ecb_rates.get_closing_rate("USD", date(2026, 3, 31)) == Decimal("1.10")
        forward_lookup.assert_called_once_with("USD", date(2026, 3, 31))

    def test_closing_rate_for_ioss_period(self, monkeypatch):
        """Vérifie le taux de clôture pour une période IOSS."""
        forward_lookup = MagicMock(return_value=Decimal("1.11"))
        monkeypatch.setattr(ecb_rates, "_fetch_ecb_rate_forward", forward_lookup)

        assert ecb_rates.get_closing_rate("GBP", date(2026, 2, 28)) == Decimal("1.11")
        forward_lookup.assert_called_once_with("GBP", date(2026, 2, 28))

    def test_cache_respects_lookup_type(self, monkeypatch):
        """Vérifie que le cache distingue lookup historique vs forward."""
        monkeypatch.setattr(ecb_rates, "_db_get_rate", lambda *_: None)
        monkeypatch.setattr(ecb_rates, "_db_upsert_rate", lambda *_: None)
        monkeypatch.setattr(
            ecb_rates, "_fetch_ecb_rate", lambda *_: Decimal("1.07")
        )
        monkeypatch.setattr(
            ecb_rates, "_fetch_ecb_rate_forward", lambda *_: Decimal("1.08")
        )
        lookup_date = date(2026, 6, 15)

        assert ecb_rates.get_rate("USD", lookup_date) == Decimal("1.07")
        assert ecb_rates.get_closing_rate("USD", lookup_date) == Decimal("1.08")
        assert ecb_rates.get_rate("USD", lookup_date) == Decimal("1.07")
        assert ecb_rates.get_closing_rate("USD", lookup_date) == Decimal("1.08")

    def test_no_forward_rate_is_not_cached_as_a_rate(self, monkeypatch):
        """Vérifie le fallback quand aucun taux n'est trouvé après la date."""
        forward_lookup = MagicMock(return_value=None)
        monkeypatch.setattr(ecb_rates, "_fetch_ecb_rate_forward", forward_lookup)
        closing_date = date(2099, 1, 1)

        assert ecb_rates.get_closing_rate("USD", closing_date) is None
        assert ecb_rates.get_closing_rate("USD", closing_date) is None
        forward_lookup.assert_called_once_with("USD", closing_date)


class TestBCEHistoricalRate:
    """Tests pour les taux historiques BCE."""

    def test_historical_rate_looks_backward(self, monkeypatch):
        """Vérifie que le taux historique cherche en arrière."""
        historical_date = date(2024, 6, 15)
        historical_lookup = MagicMock(return_value=Decimal("1.06"))
        monkeypatch.setattr(ecb_rates, "_db_get_rate", lambda *_: None)
        monkeypatch.setattr(ecb_rates, "_db_upsert_rate", lambda *_: None)
        monkeypatch.setattr(ecb_rates, "_fetch_ecb_rate", historical_lookup)

        assert ecb_rates.get_rate("USD", historical_date) == Decimal("1.06")
        historical_lookup.assert_called_once_with("USD", historical_date)

    def test_historical_rate_for_old_transaction(self, monkeypatch):
        """Vérifie le taux historique pour une transaction ancienne."""
        historical_lookup = MagicMock(return_value=Decimal("1.05"))
        monkeypatch.setattr(ecb_rates, "_db_get_rate", lambda *_: None)
        monkeypatch.setattr(ecb_rates, "_db_upsert_rate", lambda *_: None)
        monkeypatch.setattr(ecb_rates, "_fetch_ecb_rate", historical_lookup)

        transaction_date = date(2024, 6, 15)
        assert ecb_rates.get_rate("GBP", transaction_date) == Decimal("1.05")
        historical_lookup.assert_called_once_with("GBP", transaction_date)

    def test_cache_key_different_for_same_date(self, monkeypatch):
        """Vérifie que la clé de cache est différente pour historique vs forward."""
        monkeypatch.setattr(ecb_rates, "_db_get_rate", lambda *_: None)
        monkeypatch.setattr(ecb_rates, "_db_upsert_rate", lambda *_: None)
        historical_lookup = MagicMock(return_value=Decimal("1.07"))
        forward_lookup = MagicMock(return_value=Decimal("1.08"))
        monkeypatch.setattr(ecb_rates, "_fetch_ecb_rate", historical_lookup)
        monkeypatch.setattr(ecb_rates, "_fetch_ecb_rate_forward", forward_lookup)
        lookup_date = date(2026, 6, 15)

        ecb_rates.get_rate("USD", lookup_date)
        ecb_rates.get_closing_rate("USD", lookup_date)
        ecb_rates.get_rate("USD", lookup_date)
        ecb_rates.get_closing_rate("USD", lookup_date)

        historical_lookup.assert_called_once()
        forward_lookup.assert_called_once()


class TestBCEEdgeCases:
    """Tests edge cases pour BCE."""

    def test_non_eur_currency(self, monkeypatch):
        """Vérifie le traitement des devises non EUR."""
        forward_lookup = MagicMock(return_value=Decimal("1.25"))
        monkeypatch.setattr(ecb_rates, "_fetch_ecb_rate_forward", forward_lookup)

        assert ecb_rates.get_closing_rate("GBP", date(2026, 6, 30)) == Decimal("1.25")
        forward_lookup.assert_called_once_with("GBP", date(2026, 6, 30))

    def test_very_old_date(self, monkeypatch):
        """Vérifie le traitement des dates très anciennes."""
        historical_lookup = MagicMock(return_value=None)
        monkeypatch.setattr(ecb_rates, "_db_get_rate", lambda *_: None)
        monkeypatch.setattr(ecb_rates, "_fetch_ecb_rate", historical_lookup)

        old_date = date(1990, 1, 1)
        assert ecb_rates.get_rate("USD", old_date) is None
        historical_lookup.assert_called_once_with("USD", old_date)

    def test_future_date(self, monkeypatch):
        """Vérifie le traitement des dates futures."""
        forward_lookup = MagicMock(return_value=None)
        monkeypatch.setattr(ecb_rates, "_fetch_ecb_rate_forward", forward_lookup)

        future_date = date(2099, 12, 31)
        assert ecb_rates.get_closing_rate("USD", future_date) is None
        forward_lookup.assert_called_once_with("USD", future_date)

    def test_bce_api_failure_handling(self, monkeypatch):
        """Vérifie la gestion des échecs de l'API BCE."""
        failed_lookup = MagicMock(return_value=None)
        monkeypatch.setattr(ecb_rates, "_fetch_ecb_rate_forward", failed_lookup)
        closing_date = date(2026, 6, 30)

        assert ecb_rates.get_closing_rate("USD", closing_date) is None
        assert ecb_rates.get_closing_rate("USD", closing_date) is None
        failed_lookup.assert_called_once_with("USD", closing_date)


class TestBCECompliance:
    """Tests de conformité UE pour BCE."""

    def test_regulation_reference(self):
        """Vérifie que le code référence le règlement UE 2020/194."""
        import tva_intracom.ecb_rates as ecb_module
        import inspect
        
        source = inspect.getsource(ecb_module)
        
        # Le code devrait mentionner le règlement UE 2020/194
        # Ce test documente la conformité
        assert "2020/194" in source or "5 bis" in source, \
            "Le code devrait référencer le règlement UE 2020/194"

    def test_oss_uses_closing_rate(self):
        """Vérifie que l'export OSS utilise le taux de clôture."""
        import inspect
        from tva_intracom import oss_export

        source = inspect.getsource(oss_export)
        assert "get_closing_rate" in source



if __name__ == "__main__":
    pytest.main([__file__, "-v"])
