"""Test de non-régression pour le pré-batch des taux BCE dans
aggregate_oss_results (voir oss_export.py).

Contexte : convert_ht_tva_for_oss_period (appelée par aggregate_oss_results
pour chaque VatResult OSS_B2C/IOSS_DIRECT) convertit vers EUR au taux BCE de
clôture de période via ecb_rates.get_rate. Sans pré-batch, chaque devise
distincte rencontrée dans `results` déclenche sa propre requête DB
individuelle (mesuré en prod : 5 devises distinctes ~2.9s cumulés). Ce test
vérifie qu'un seul appel `prefetch_rates` groupé est fait en amont, avec
exactement les paires (devise, date de clôture) nécessaires — pas une par
ligne de `results`.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from unittest.mock import patch

import pytest

from tva_intracom import BuyerType, Sale, compute_all_with_vies
from tva_intracom import oss_export


def _make_sale(sale_id: str, currency: str, tx_date: str, amount: str) -> Sale:
    return Sale(
        sale_id, Decimal(amount), BuyerType.B2C,
        stock_country="FR", buyer_country="DE",
        original_currency=currency, original_amount=Decimal(amount),
        transaction_date=tx_date,
    )


def test_aggregate_oss_results_prefetches_rates_in_one_batch_call():
    # 3 devises distinctes (dont EUR, à ignorer), 6 lignes au total : sans
    # pré-batch, ce serait jusqu'à 5 appels get_rate individuels (une fois
    # par devise non-EUR rencontrée pour la première fois).
    sales = [
        _make_sale("s1", "GBP", "2026-01-15", "100"),
        _make_sale("s2", "GBP", "2026-02-20", "150"),  # même trimestre -> même rate_date que s1
        _make_sale("s3", "PLN", "2026-01-10", "200"),
        _make_sale("s4", "PLN", "2026-03-05", "250"),  # même trimestre -> même rate_date que s3
        _make_sale("s5", "EUR", "2026-01-01", "300"),  # EUR : jamais dans le prefetch
        _make_sale("s6", "SEK", "2026-02-01", "400"),
    ]
    results = compute_all_with_vies(sales, scope_id="test-oss-prefetch")[0]

    prefetch_calls = []

    def _fake_prefetch(pairs, **kwargs):
        prefetch_calls.append(list(pairs))

    with patch.object(oss_export, "prefetch_rates", side_effect=_fake_prefetch) as mock_prefetch, \
         patch.object(oss_export, "convert_to_currency_for_oss",
                      return_value=(Decimal("100"), Decimal("1"), "cache")):
        oss_export.aggregate_oss_results(results, period="2026-Q1")

    assert mock_prefetch.call_count == 1, (
        f"Un seul appel prefetch_rates groupé attendu, obtenu {mock_prefetch.call_count} "
        f"(voir commentaire dans aggregate_oss_results)"
    )
    pairs = set(prefetch_calls[0])
    q1_end = date(2026, 3, 31)
    assert pairs == {("GBP", q1_end), ("PLN", q1_end), ("SEK", q1_end)}, (
        f"Paires (devise, date de clôture) inattendues : {pairs}"
    )


def test_aggregate_oss_results_skips_prefetch_when_no_period():
    """Sans `period` fourni, convert_ht_tva_for_oss_period ne convertit rien
    (comportement historique conservé) : aucun prefetch ne doit être tenté."""
    results = compute_all_with_vies([_make_sale("s1", "GBP", "2026-01-15", "100")], scope_id="test-oss-prefetch")[0]

    with patch.object(oss_export, "prefetch_rates") as mock_prefetch, \
         patch.object(oss_export, "convert_to_currency_for_oss") as mock_convert:
        oss_export.aggregate_oss_results(results, period="")

    mock_prefetch.assert_not_called()
    mock_convert.assert_not_called()
