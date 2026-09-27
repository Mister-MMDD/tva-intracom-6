import csv
from decimal import Decimal
from io import StringIO

from tva_intracom.models import BuyerType, Channel, Collector, Sale, Scenario, VatResult
from tva_intracom.rates_evidence import build_rates_evidence_csv
from tva_intracom.rates_evidence_pdf import generate_rates_evidence_pdf


def _result(
    sale_id: str,
    tx_date: str,
    vat_rate: Decimal = Decimal("19"),
    category: str = "STANDARD",
) -> VatResult:
    sale = Sale(
        sale_id=sale_id,
        amount_ht=Decimal("100.00"),
        buyer_type=BuyerType.B2C,
        stock_country="FR",
        buyer_country="DE",
        original_currency="USD",
        original_amount=Decimal("110.00"),
        exchange_rate=Decimal("1.10"),
        exchange_rate_source="ecb",
        transaction_date=tx_date,
        product_category=category,
    )
    return VatResult(
        sale=sale,
        scenario=Scenario.OSS_B2C,
        vat_country="DE",
        vat_rate=vat_rate,
        vat_amount=Decimal("100") * vat_rate / Decimal("100"),
        collector=Collector.SELLER,
        channel=Channel.OSS,
        note="",
    )


def test_rates_evidence_lists_daily_closing_and_vat_rates(monkeypatch):
    monkeypatch.setattr(
        "tva_intracom.rates_evidence.get_closing_rate",
        lambda currency, closing_date: Decimal("1.12"),
    )
    csv_bytes = build_rates_evidence_csv(
        [_result("S1", "2026-04-01"), _result("S2", "2026-04-03")],
        "2026-Q2",
    )
    rows = list(csv.reader(StringIO(csv_bytes.decode("utf-8-sig")), delimiter=";"))
    daily = [row for row in rows[1:] if row[0] == "Change quotidien"]
    closing = [row for row in rows[1:] if row[0] == "Clôture BCE"]
    vat = [row for row in rows[1:] if row[0] == "TVA appliquée"]

    assert len(daily) == 2
    assert {row[2] for row in daily} == {"2026-04-01", "2026-04-03"}
    assert len(closing) == 1
    assert closing[0][2:6] == ["", "2026-06-30", "USD", "1.12"]
    assert len(vat) == 1
    assert vat[0][6:10] == ["DE", "19", "2026-04-01", "2026-04-03"]


def test_rates_evidence_lists_country_rate_once_across_categories_and_years():
    early = _result("S1", "2021-01-08")
    later = _result("S2", "2026-05-26", category="FOOD")
    changed_rate = _result("S3", "2026-05-29", vat_rate=Decimal("7"))
    csv_bytes = build_rates_evidence_csv([early, later, changed_rate], "2026-Q2")
    rows = list(csv.reader(StringIO(csv_bytes.decode("utf-8-sig")), delimiter=";"))
    vat = [row for row in rows[1:] if row[0] == "TVA appliquée"]

    assert len(vat) == 2
    assert [row[6:10] for row in vat] == [
        ["DE", "19", "2021-01-08", "2026-05-26"],
        ["DE", "7", "2026-05-29", "2026-05-29"],
    ]


def test_rates_evidence_marks_sale_rate_fallback(monkeypatch):
    monkeypatch.setattr(
        "tva_intracom.rates_evidence.get_closing_rate",
        lambda currency, closing_date: None,
    )
    csv_bytes = build_rates_evidence_csv([_result("S1", "2026-04-01")], "2026-Q2")
    rows = list(csv.reader(StringIO(csv_bytes.decode("utf-8-sig")), delimiter=";"))
    fallback = [row for row in rows[1:] if row[0] == "Repli taux de vente"]

    assert len(fallback) == 1
    assert fallback[0][1:6] == ["OSS", "2026-04-01", "2026-06-30", "USD", "1.10"]


def test_rates_evidence_excludes_zero_vat_rates(monkeypatch):
    monkeypatch.setattr(
        "tva_intracom.rates_evidence.get_closing_rate",
        lambda currency, closing_date: Decimal("1.12"),
    )
    csv_bytes = build_rates_evidence_csv(
        [_result("S0", "2026-04-01", vat_rate=Decimal("0"))],
        "2026-Q2",
    )
    rows = list(csv.reader(StringIO(csv_bytes.decode("utf-8-sig")), delimiter=";"))

    assert not any(row[0] == "TVA appliquée" for row in rows[1:])


def test_rates_evidence_pdf_has_pdf_signature_and_unique_reference():
    csv_bytes = build_rates_evidence_csv([_result("S1", "2026-04-01")], "2026-Q2")
    options = {
        "company_name": "Example",
        "siren": "123456789",
        "scope_id": "private-scope",
        "period_label": "2026-Q2",
    }

    first = generate_rates_evidence_pdf(csv_bytes, **options)
    second = generate_rates_evidence_pdf(csv_bytes, **options)

    assert first.startswith(b"%PDF-")
    assert first.endswith(b"%%EOF\n")
    assert first != second
