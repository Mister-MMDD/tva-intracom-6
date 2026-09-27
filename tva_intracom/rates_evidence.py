"""Exports the exchange and VAT rates applied to a calculation."""

from __future__ import annotations

import csv
import io
from collections import defaultdict
from datetime import date
from decimal import Decimal
from typing import Iterable

from .ecb_rates import get_closing_rate, get_ioss_rate_date, get_oss_rate_date
from .i18n import _
from .models import Scenario, VatResult


def _transaction_date(value: str) -> date | None:
    try:
        return date.fromisoformat((value or "")[:10])
    except ValueError:
        return None


def _decimal(value: Decimal | None) -> str:
    return str(value) if value is not None else ""


def build_rates_evidence_csv(results: Iterable[VatResult], period: str) -> bytes:
    """Build a CSV of daily FX rates, OSS/IOSS closing rates and applied VAT rates."""
    rows: list[list[str]] = []
    daily_rates: set[tuple[str, str, str, str]] = set()
    vat_periods: dict[tuple[str, str], list[date]] = defaultdict(list)
    closing_rates: dict[tuple[str, str, date], list[VatResult]] = defaultdict(list)

    for result in results:
        sale = result.sale
        currency = (sale.original_currency or "EUR").upper()
        tx_date = _transaction_date(sale.transaction_date)
        tx_date_text = tx_date.isoformat() if tx_date else sale.transaction_date[:10]

        if currency != "EUR":
            daily_rates.add((
                tx_date_text,
                currency,
                _decimal(sale.exchange_rate),
                sale.exchange_rate_source,
            ))

        if result.vat_rate != 0:
            # A rate appears once per country, regardless of product category,
            # sales channel, or the number and spacing of transactions.
            vat_key = (result.vat_country, str(result.vat_rate))
            if tx_date:
                vat_periods[vat_key].append(tx_date)
            else:
                vat_periods[vat_key]

        if currency == "EUR" or result.scenario not in (Scenario.OSS_B2C, Scenario.IOSS_DIRECT):
            continue
        effective_date = tx_date or date.today()
        regime = "IOSS" if result.scenario == Scenario.IOSS_DIRECT else "OSS"
        rate_date_fn = get_ioss_rate_date if regime == "IOSS" else get_oss_rate_date
        closing_date = rate_date_fn(period, effective_date)
        closing_rates[(regime, currency, closing_date)].append(result)

    for tx_date_text, currency, rate, source in sorted(daily_rates):
        rows.append([
            _("rates_evidence_daily"), "", tx_date_text, "", currency, rate,
            "", "", "", "", "", source,
        ])

    for (regime, currency, closing_date), rate_results in sorted(closing_rates.items()):
        if currency == "HRK":
            closing_rate = Decimal("7.53450")
            source = "fixed_eu"
        else:
            closing_rate = get_closing_rate(currency, closing_date)
            source = "ecb_closing" if closing_rate is not None else ""

        if closing_rate is not None:
            rows.append([
                _("rates_evidence_closing"), regime, "", closing_date.isoformat(),
                currency, str(closing_rate), "", "", "", "", "", source,
            ])
            continue

        # The OSS/IOSS calculation falls back to the sale-day exchange rate
        # when the closing rate has not yet been published or is unavailable.
        seen_fallbacks: set[tuple[str, str]] = set()
        for result in rate_results:
            sale = result.sale
            tx_date_text = (sale.transaction_date or "")[:10]
            fallback_key = (tx_date_text, str(sale.exchange_rate))
            if fallback_key in seen_fallbacks:
                continue
            seen_fallbacks.add(fallback_key)
            rows.append([
                _("rates_evidence_fallback"), regime, tx_date_text, closing_date.isoformat(),
                currency, _decimal(sale.exchange_rate), "", "", "", "", "",
                sale.exchange_rate_source,
            ])

    for (country, rate), dates in sorted(vat_periods.items()):
        rows.append([
            _("rates_evidence_vat"), "", "", "", "", "",
            country, rate, min(dates).isoformat() if dates else "",
            max(dates).isoformat() if dates else "", "",
        ])

    output = io.StringIO(newline="")
    writer = csv.writer(output, delimiter=";")
    writer.writerow([
        _("rates_evidence_col_type"),
        _("rates_evidence_col_regime"),
        _("rates_evidence_col_date"),
        _("rates_evidence_col_closing_date"),
        _("rates_evidence_col_currency"),
        _("rates_evidence_col_rate"),
        _("rates_evidence_col_country"),
        _("rates_evidence_col_vat_rate"),
        _("rates_evidence_col_first_date"),
        _("rates_evidence_col_last_date"),
        _("rates_evidence_col_source"),
    ])
    writer.writerows(rows)
    return ("\ufeff" + output.getvalue()).encode("utf-8")