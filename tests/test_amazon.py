"""Tests pour l'adaptateur Amazon VAT Transactions Report."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from tva_intracom.amazon_adapter import load_amazon_report
from tva_intracom.models import BuyerType

_SAMPLE = Path(__file__).parent.parent / "tva_intracom" / "data" / "amazon_sample.tsv"


def test_load_counts():
    r = load_amazon_report(_SAMPLE)
    assert len(r.sales) == 6
    assert len(r.refunds) == 1
    assert len(r.fc_transfers) == 3


def test_stock_countries_detected():
    r = load_amazon_report(_SAMPLE)
    assert "DE" in r.stock_countries
    assert "PL" in r.stock_countries
    assert "FR" in r.stock_countries


def test_sale_mapping_b2c():
    r = load_amazon_report(_SAMPLE)
    sale = next(s for s in r.sales if s.sale_id == "ORD-2024-001")
    assert sale.buyer_type == BuyerType.B2C
    assert sale.stock_country == "FR"
    assert sale.buyer_country == "FR"
    assert sale.amount_ht == Decimal("50.00")


def test_sale_mapping_b2b():
    r = load_amazon_report(_SAMPLE)
    sale = next(s for s in r.sales if s.sale_id == "ORD-2024-003")
    assert sale.buyer_type == BuyerType.B2B
    assert sale.buyer_vat_number == "DE123456789"
    assert sale.buyer_vat_valid is True
    assert sale.amount_ht == Decimal("200.00")


def test_refund_negative_amount():
    r = load_amazon_report(_SAMPLE)
    refund = r.refunds[0]
    assert refund.amount_ht < 0


def test_fc_transfer_tracked():
    r = load_amazon_report(_SAMPLE)
    assert len(r.fc_transfers) == 3


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
