"""Disjoncteur réseau BCE + prefetch_closing_rates sans re-tentative (2026-10-04)."""
import urllib.error
from datetime import date

import pytest

from tva_intracom import ecb_rates


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    monkeypatch.setattr(ecb_rates, "_net_consecutive_failures", 0)
    monkeypatch.setattr(ecb_rates, "_net_open_until", 0.0)
    monkeypatch.setattr(ecb_rates, "_FETCH_BACKOFF_BASE_SECONDS", 0.0)
    ecb_rates._failed_pairs.clear()
    ecb_rates._forward_rate_cache.clear()
    yield
    ecb_rates._failed_pairs.clear()
    ecb_rates._forward_rate_cache.clear()


def _timeout(*a, **k):
    raise urllib.error.URLError(TimeoutError("The read operation timed out"))


def test_breaker_opens_after_threshold_and_skips_network(monkeypatch):
    calls = {"n": 0}

    def fake_urlopen(*a, **k):
        calls["n"] += 1
        return _timeout()

    monkeypatch.setattr(ecb_rates.urllib.request, "urlopen", fake_urlopen)
    for _ in range(ecb_rates._NET_BREAKER_THRESHOLD):
        assert ecb_rates._request_ecb("http://x", "t") is None
    attempts = calls["n"]
    assert attempts == ecb_rates._NET_BREAKER_THRESHOLD * ecb_rates._FETCH_MAX_ATTEMPTS
    assert ecb_rates._net_breaker_is_open()
    # disjoncteur ouvert : plus aucun appel réseau
    assert ecb_rates._request_ecb("http://x", "t") is None
    assert calls["n"] == attempts


def test_prefetch_closing_rates_skips_already_failed_pairs(monkeypatch):
    calls = []
    monkeypatch.setattr(
        ecb_rates, "_fetch_ecb_batch",
        lambda ccys, s, e: calls.append(ccys) or {},
    )
    pairs = [("CZK", date(2024, 3, 31)), ("RON", date(2024, 3, 31))]
    ecb_rates.prefetch_closing_rates(pairs)
    assert len(calls) == 2
    ecb_rates.prefetch_closing_rates(pairs)  # 2e appel : paires marquées en échec
    assert len(calls) == 2
