"""Tests pour les parsers multi-plateforme."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from tva_intracom.models import BuyerType
from tva_intracom.parsers import mirakl, shopify, woocommerce, aliexpress, amazon

_SAMPLES = Path(__file__).parent.parent / "tva_intracom" / "data" / "samples"


class TestMiraklParser:
    def test_parse_csv_sample(self):
        result = mirakl.parse(_SAMPLES / "mirakl_sample.csv", encoding="utf-8")
        assert result.platform == "mirakl"
        assert len(result.sales) == 8
        assert result.total_rows == 8
        assert result.skipped_rows == 0

    def test_b2b_detection(self):
        result = mirakl.parse(_SAMPLES / "mirakl_sample.csv")
        # MK-003 a un vat_number -> B2B
        mk003 = [s for s in result.sales if s.sale_id == "MK-003"][0]
        assert mk003.buyer_type == BuyerType.B2B
        assert mk003.buyer_vat_number == "ES12345678A"

    def test_stock_country_from_shipping(self):
        result = mirakl.parse(_SAMPLES / "mirakl_sample.csv")
        # MK-007 a shipping_country=DE
        mk007 = [s for s in result.sales if s.sale_id == "MK-007"][0]
        assert mk007.stock_country == "DE"

    def test_amounts(self):
        result = mirakl.parse(_SAMPLES / "mirakl_sample.csv")
        mk001 = [s for s in result.sales if s.sale_id == "MK-001"][0]
        assert mk001.amount_ht == Decimal("150.00")
        assert mk001.buyer_country == "FR"


class TestShopifyParser:
    def test_parse_csv_sample(self):
        result = shopify.parse(_SAMPLES / "shopify_sample.csv")
        assert result.platform == "shopify"
        # 10 lignes - 1 refund = 9 ventes + 1 refund
        assert len(result.sales) == 9
        assert len(result.refunds) == 1

    def test_refund_detection(self):
        result = shopify.parse(_SAMPLES / "shopify_sample.csv")
        # #1008 est refunded
        refund = result.refunds[0]
        assert refund.sale_id == "#1008"
        assert refund.amount_ht < 0

    def test_country_mapping(self):
        result = shopify.parse(_SAMPLES / "shopify_sample.csv")
        sh1002 = [s for s in result.sales if s.sale_id == "#1002"][0]
        assert sh1002.buyer_country == "DE"
        assert sh1002.amount_ht == Decimal("89.99")

    def test_stock_country_is_seller(self):
        """Shopify = vente directe, stock = pays du vendeur."""
        result = shopify.parse(_SAMPLES / "shopify_sample.csv", seller_country="FR")
        for sale in result.sales:
            assert sale.stock_country == "FR"

    def test_gbp_currency_detected(self):
        result = shopify.parse(_SAMPLES / "shopify_sample.csv")
        sh1007 = [s for s in result.sales if s.sale_id == "#1007"][0]
        assert sh1007.original_currency == "GBP"


class TestWooCommerceParser:
    def test_parse_csv_sample(self):
        result = woocommerce.parse(_SAMPLES / "woocommerce_sample.csv")
        assert result.platform == "woocommerce"
        # 10 lignes - 1 cancelled = 9 ventes
        assert len(result.sales) == 9
        assert result.skipped_rows == 1  # WC-1007 cancelled

    def test_cancelled_filtered(self):
        result = woocommerce.parse(_SAMPLES / "woocommerce_sample.csv")
        ids = [s.sale_id for s in result.sales]
        assert "WC-1007" not in ids

    def test_b2b_with_vat_number(self):
        result = woocommerce.parse(_SAMPLES / "woocommerce_sample.csv")
        wc1004 = [s for s in result.sales if s.sale_id == "WC-1004"][0]
        assert wc1004.buyer_type == BuyerType.B2B
        assert wc1004.buyer_vat_number == "IT12345678901"

    def test_amounts_and_countries(self):
        result = woocommerce.parse(_SAMPLES / "woocommerce_sample.csv")
        wc1002 = [s for s in result.sales if s.sale_id == "WC-1002"][0]
        assert wc1002.amount_ht == Decimal("150.00")
        assert wc1002.buyer_country == "DE"


class TestAliExpressParser:
    def test_parse_csv_sample(self):
        result = aliexpress.parse(_SAMPLES / "aliexpress_sample.csv")
        assert result.platform == "aliexpress"
        # 10 lignes - 1 cancelled = 9 ventes
        assert len(result.sales) == 9
        assert result.skipped_rows == 1  # AE-10008 cancelled

    def test_cancelled_filtered(self):
        result = aliexpress.parse(_SAMPLES / "aliexpress_sample.csv")
        ids = [s.sale_id for s in result.sales]
        assert "AE-10008" not in ids

    def test_seller_country_cn_default(self):
        result = aliexpress.parse(_SAMPLES / "aliexpress_sample.csv")
        ae1001 = [s for s in result.sales if s.sale_id == "AE-10001"][0]
        assert ae1001.seller_country == "CN"
        assert ae1001.stock_country == "CN"

    def test_seller_country_override(self):
        """AE-10010 a ship_from=FR."""
        result = aliexpress.parse(_SAMPLES / "aliexpress_sample.csv")
        ae10010 = [s for s in result.sales if s.sale_id == "AE-10010"][0]
        assert ae10010.stock_country == "FR"

    def test_all_b2c(self):
        """Marketplaces = toujours B2C."""
        result = aliexpress.parse(_SAMPLES / "aliexpress_sample.csv")
        for sale in result.sales:
            assert sale.buyer_type == BuyerType.B2C


class TestAmazonParserWrapper:
    def test_parse_via_parsers(self):
        sample = Path(__file__).parent.parent / "tva_intracom" / "data" / "amazon_sample.tsv"
        result = amazon.parse(sample)
        assert result.platform == "amazon"
        assert len(result.sales) > 0


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
