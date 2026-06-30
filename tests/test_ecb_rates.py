"""Tests pour le module ecb_rates (taux de change BCE)."""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest

from tva_intracom.ecb_rates import (
    clear_cache,
    convert_to_eur,
    get_rate,
)


@pytest.fixture(autouse=True)
def _clear_rate_cache():
    """Vide le cache avant chaque test."""
    clear_cache()
    yield
    clear_cache()


def _mock_ecb_response(rate_value: float):
    """Cree un mock pour la reponse ECB JSON."""
    response_data = json.dumps({
        "dataSets": [{
            "series": {
                "0:0:0:0:0": {
                    "observations": {
                        "0": [rate_value],
                    }
                }
            }
        }]
    }).encode("utf-8")
    mock_resp = MagicMock()
    mock_resp.read.return_value = response_data
    mock_resp.__enter__ = MagicMock(return_value=mock_resp)
    mock_resp.__exit__ = MagicMock(return_value=False)
    return mock_resp


def test_eur_to_eur():
    """EUR -> EUR : taux = 1, pas d'appel API."""
    rate = get_rate("EUR", date(2024, 3, 15))
    assert rate == Decimal("1")


@patch("tva_intracom.ecb_rates.urllib.request.urlopen")
def test_get_rate_usd(mock_urlopen):
    mock_urlopen.return_value = _mock_ecb_response(1.0892)
    rate = get_rate("USD", date(2024, 3, 15))
    assert rate == Decimal("1.0892")


@patch("tva_intracom.ecb_rates.urllib.request.urlopen")
def test_get_rate_gbp(mock_urlopen):
    mock_urlopen.return_value = _mock_ecb_response(0.8543)
    rate = get_rate("GBP", date(2024, 3, 15))
    assert rate == Decimal("0.8543")


@patch("tva_intracom.ecb_rates.urllib.request.urlopen")
def test_convert_to_eur(mock_urlopen):
    mock_urlopen.return_value = _mock_ecb_response(1.0892)
    eur_amount, rate, source = convert_to_eur(
        Decimal("108.92"), "USD", date(2024, 3, 15)
    )
    assert source == "ecb"
    assert rate == Decimal("1.0892")
    # 108.92 / 1.0892 = ~100.03 EUR
    assert eur_amount == Decimal("100.00")


def test_convert_to_eur_already_eur():
    """Si la devise est EUR, retourne le montant tel quel."""
    eur_amount, rate, source = convert_to_eur(
        Decimal("150.00"), "EUR", date(2024, 3, 15)
    )
    assert eur_amount == Decimal("150.00")
    assert rate == Decimal("1")
    assert source == "eur"


@patch("tva_intracom.ecb_rates.urllib.request.urlopen")
def test_get_rate_network_error_returns_none(mock_urlopen):
    import urllib.error
    mock_urlopen.side_effect = urllib.error.URLError("timeout")
    rate = get_rate("GBP", date(2024, 3, 15))
    assert rate is None


@patch("tva_intracom.ecb_rates.urllib.request.urlopen")
def test_convert_with_fallback(mock_urlopen):
    import urllib.error
    mock_urlopen.side_effect = urllib.error.URLError("timeout")
    eur_amount, rate, source = convert_to_eur(
        Decimal("100"), "GBP", date(2024, 3, 15),
        fallback_rate=Decimal("0.85")
    )
    assert source == "fallback"
    assert rate == Decimal("0.85")
    # 100 / 0.85 = 117.65
    assert eur_amount == Decimal("117.65")


@patch("tva_intracom.ecb_rates.urllib.request.urlopen")
def test_convert_no_rate_raises(mock_urlopen):
    import urllib.error
    mock_urlopen.side_effect = urllib.error.URLError("timeout")
    with pytest.raises(ValueError, match="Impossible d'obtenir le taux"):
        convert_to_eur(Decimal("100"), "GBP", date(2024, 3, 15))


@patch("tva_intracom.ecb_rates.urllib.request.urlopen")
def test_cache_avoids_duplicate_calls(mock_urlopen):
    mock_urlopen.return_value = _mock_ecb_response(1.0892)
    get_rate("USD", date(2024, 3, 15))
    get_rate("USD", date(2024, 3, 15))
    # Seul le premier appel devrait appeler l'API.
    assert mock_urlopen.call_count == 1


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
