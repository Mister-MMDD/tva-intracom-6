"""Tests pour l'integration VIES (avec mock HTTP pour eviter les appels reels)."""

from __future__ import annotations

import json
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest

from tva_intracom.vies import ViesResult, _clean_vat_number, check_vat, check_vat_raw
from tva_intracom import BuyerType, Sale, Scenario, compute_all_with_vies


def test_clean_vat_number_standard():
    code, number = _clean_vat_number("DE123456789")
    assert code == "DE"
    assert number == "123456789"


def test_clean_vat_number_with_spaces():
    code, number = _clean_vat_number("FR 12 345 678 901")
    assert code == "FR"
    assert number == "12345678901"


def test_clean_vat_number_lowercase():
    code, number = _clean_vat_number("de123456789")
    assert code == "DE"
    assert number == "123456789"


def test_clean_vat_number_too_short():
    with pytest.raises(ValueError, match="trop court"):
        _clean_vat_number("D1")


def _mock_urlopen(valid: bool, name: str = "Firma GmbH"):
    """Cree un mock pour urllib.request.urlopen retournant un resultat VIES."""
    response_data = json.dumps({
        "valid": valid,
        "countryCode": "DE",
        "vatNumber": "123456789",
        "name": name if valid else "---",
        "address": "Berlin" if valid else "",
    }).encode("utf-8")
    mock_resp = MagicMock()
    mock_resp.read.return_value = response_data
    mock_resp.__enter__ = MagicMock(return_value=mock_resp)
    mock_resp.__exit__ = MagicMock(return_value=False)
    return mock_resp


@patch("tva_intracom.vies.urllib.request.urlopen")
def test_check_vat_valid(mock_urlopen_func):
    mock_urlopen_func.return_value = _mock_urlopen(valid=True)
    result = check_vat("DE", "123456789")
    assert result.valid is True
    assert result.name == "Firma GmbH"


@patch("tva_intracom.vies.urllib.request.urlopen")
def test_check_vat_invalid(mock_urlopen_func):
    mock_urlopen_func.return_value = _mock_urlopen(valid=False)
    result = check_vat("DE", "000000000")
    assert result.valid is False


@patch("tva_intracom.vies.urllib.request.urlopen")
def test_check_vat_network_error(mock_urlopen_func):
    import urllib.error
    mock_urlopen_func.side_effect = urllib.error.URLError("timeout")
    result = check_vat("DE", "123456789")
    assert result.valid is False
    assert "indisponible" in result.error.lower() or "timeout" in result.error.lower()


@patch("tva_intracom.vies.urllib.request.urlopen")
def test_check_vat_raw_valid(mock_urlopen_func):
    mock_urlopen_func.return_value = _mock_urlopen(valid=True)
    result = check_vat_raw("DE123456789")
    assert result.valid is True


@patch("tva_intracom.vies.check_vat_raw")
def test_compute_all_with_vies_reclassifies_invalid(mock_check):
    """B2B avec numero invalide est reclassifie en B2C -> TVA facturee."""
    mock_check.return_value = ViesResult(
        valid=False, country_code="DE", vat_number="000000000",
        error="numero invalide"
    )
    sales = [
        Sale(
            sale_id="T1",
            amount_ht=Decimal("200"),
            buyer_type=BuyerType.B2B,
            stock_country="FR",
            buyer_country="DE",
            buyer_vat_number="DE000000000",
        ),
    ]
    results, vies_summary = compute_all_with_vies(sales)
    assert len(results) == 1
    r = results[0]
    # Reclassifie en B2C -> OSS (pas reverse charge).
    assert r.scenario == Scenario.OSS_B2C
    assert r.vat_amount == Decimal("38.00")  # 200 * 19%
    # Verification du summary fraude.
    assert vies_summary.total_checked == 1
    assert vies_summary.total_invalid == 1
    assert vies_summary.fraud_avoided_amount == Decimal("38.00")
    assert len(vies_summary.reclassifications) == 1
    assert vies_summary.reclassifications[0].buyer_vat_number == "DE000000000"


@patch("tva_intracom.vies.check_vat_raw")
def test_compute_all_with_vies_valid_number(mock_check):
    """B2B avec numero valide -> autoliquidation."""
    mock_check.return_value = ViesResult(
        valid=True, country_code="DE", vat_number="123456789",
        name="Firma GmbH"
    )
    sales = [
        Sale(
            sale_id="T2",
            amount_ht=Decimal("200"),
            buyer_type=BuyerType.B2B,
            stock_country="FR",
            buyer_country="DE",
            buyer_vat_number="DE123456789",
        ),
    ]
    results, vies_summary = compute_all_with_vies(sales)
    assert len(results) == 1
    r = results[0]
    assert r.scenario == Scenario.B2B_REVERSE_CHARGE
    assert r.vat_amount == Decimal("0.00")
    # Pas de fraude quand le numero est valide.
    assert vies_summary.total_checked == 1
    assert vies_summary.total_valid == 1
    assert vies_summary.total_invalid == 0
    assert vies_summary.fraud_avoided_amount == Decimal("0.00")


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
