from datetime import date
from decimal import Decimal

from tva_intracom.local_vat_report import (
    format_local_standard_rates,
    local_standard_rate_timeline,
)
from tva_intracom.models import BuyerType, Channel, Collector, Sale, Scenario, VatResult


def _result(country: str, channel: Channel, transaction_date: str) -> VatResult:
    sale = Sale(
        sale_id=transaction_date,
        amount_ht=Decimal("100"),
        buyer_type=BuyerType.B2C,
        stock_country=country,
        buyer_country=country,
        transaction_date=transaction_date,
    )
    return VatResult(
        sale=sale,
        scenario=Scenario.DOMESTIC,
        vat_country=country,
        vat_rate=Decimal("20"),
        vat_amount=Decimal("20"),
        collector=Collector.SELLER,
        channel=channel,
        note="",
    )


def test_local_standard_rate_timeline_reports_each_dynamic_rate_change(monkeypatch):
    rates_by_date = {
        date(2025, 1, 1): Decimal("19"),
        date(2025, 1, 15): Decimal("19"),
        date(2025, 2, 1): Decimal("20"),
        date(2025, 3, 1): Decimal("21"),
    }
    calls = []

    def fake_vat_rate(country, category, tx_date):
        calls.append((country, category, tx_date))
        return rates_by_date[tx_date]

    monkeypatch.setattr("tva_intracom.local_vat_report.vat_rate", fake_vat_rate)
    results = [
        _result("DE", Channel.LOCAL_REGISTRATION, "2025-01-15"),
        _result("DE", Channel.LOCAL_REGISTRATION, "2025-01-01"),
        _result("DE", Channel.LOCAL_REGISTRATION, "2025-02-01"),
        _result("DE", Channel.LOCAL_REGISTRATION, "2025-03-01"),
        _result("FR", Channel.LOCAL_REGISTRATION, "2025-01-01"),
        _result("DE", Channel.OSS, "2025-01-01"),
    ]

    assert local_standard_rate_timeline(results, "de") == [
        (date(2025, 1, 1), Decimal("19")),
        (date(2025, 2, 1), Decimal("20")),
        (date(2025, 3, 1), Decimal("21")),
    ]
    assert calls == [
        ("DE", "STANDARD", date(2025, 1, 1)),
        ("DE", "STANDARD", date(2025, 1, 15)),
        ("DE", "STANDARD", date(2025, 2, 1)),
        ("DE", "STANDARD", date(2025, 3, 1)),
    ]


def test_local_standard_rate_timeline_ignores_invalid_transaction_dates(monkeypatch):
    def unexpected_rate_lookup(*args):
        raise AssertionError("No valid transaction dates should be queried")

    monkeypatch.setattr("tva_intracom.local_vat_report.vat_rate", unexpected_rate_lookup)
    results = [_result("DE", Channel.LOCAL_REGISTRATION, "")]

    assert local_standard_rate_timeline(results, "DE") == []


def test_format_local_standard_rates_shows_change_and_observed_date():
    assert format_local_standard_rates([
        (date(2025, 1, 1), Decimal("19.00")),
        (date(2025, 2, 1), Decimal("20.00")),
    ]) == "19 %; 20 % (changement observé le 2025-02-01)"
