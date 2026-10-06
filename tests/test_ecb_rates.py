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


# ---------------------------------------------------------------------------
# Audit 2026-10-04 : HTTP 404, persistance L2 du jour courant, taux fixes
# ---------------------------------------------------------------------------
import io as _io
import urllib.error
from datetime import date as _date, timedelta as _timedelta
from decimal import Decimal as _Decimal


def _reset_ecb_state():
    from tva_intracom import ecb_rates as e
    e._rate_cache.clear()
    e._failed_pairs.clear()
    e._forward_rate_cache.clear()
    return e


def test_http_404_no_retry_no_sleep():
    """404 SDMX = « aucune observation » : 1 seule requête, aucun backoff."""
    e = _reset_ecb_state()
    err = urllib.error.HTTPError("http://x", 404, "Not Found", {}, _io.BytesIO(b"No results found"))
    with patch("tva_intracom.ecb_rates.urllib.request.urlopen", side_effect=err) as m, \
         patch("tva_intracom.ecb_rates.time.sleep") as slept:
        assert e._request_ecb("http://x", "test") is None
    assert m.call_count == 1
    slept.assert_not_called()


def test_http_500_still_retried():
    e = _reset_ecb_state()
    err = urllib.error.HTTPError("http://x", 500, "Err", {}, _io.BytesIO(b""))
    with patch("tva_intracom.ecb_rates.urllib.request.urlopen", side_effect=err) as m, \
         patch("tva_intracom.ecb_rates.time.sleep"):
        assert e._request_ecb("http://x", "test") is None
    assert m.call_count == e._FETCH_MAX_ATTEMPTS


def test_get_rate_today_not_persisted_in_l2_but_past_is():
    e = _reset_ecb_state()
    today = _date.today()
    past = today - _timedelta(days=3)
    saved = []
    with patch.object(e, "_db_get_rate", return_value=None), \
         patch.object(e, "_fetch_ecb_rate", return_value=_Decimal("1.10")), \
         patch.object(e, "_db_upsert_rate", side_effect=lambda c, d, r: saved.append((c, d))):
        e.get_rate("USD", today)
        e.get_rate("USD", past)
    assert saved == [("USD", past)]


def test_prefetch_rates_today_not_persisted_in_l2_but_past_is():
    e = _reset_ecb_state()
    today = _date.today()
    past = today - _timedelta(days=3)
    batch = {"USD": {past: _Decimal("1.10"), today - _timedelta(days=1): _Decimal("1.11")}}
    persisted = []
    with patch.object(e, "_db_get_rates_batch", return_value={}), \
         patch.object(e, "_fetch_ecb_batch", return_value=batch), \
         patch.object(e, "_db_upsert_batch", side_effect=lambda entries: persisted.extend(entries)):
        e.prefetch_rates([("USD", today), ("USD", past)])
    assert [(c, d) for c, d, _r in persisted] == [("USD", past)]
    # le taux (veille) reste utilisable en mémoire pour le calcul du jour
    assert e.get_rate("USD", today) == _Decimal("1.11")


def test_bgn_fixed_rate_after_adoption():
    e = _reset_ecb_state()
    with patch("tva_intracom.ecb_rates.urllib.request.urlopen") as m:
        eur, rate, src = e.convert_to_eur(_Decimal("195.583"), "BGN", _date(2026, 3, 1))
        assert (eur, rate, src) == (_Decimal("100.00"), _Decimal("1.95583"), "fixed_eur_bgn")
        e.prefetch_rates([("BGN", _date(2026, 3, 1))])
        assert e.get_rate("BGN", _date(2026, 1, 1)) == _Decimal("1.95583")
        # conversion croisée BGN -> PLN : taux source fixe, aucun appel réseau
        with patch.object(e, "get_rate", return_value=_Decimal("4.3")):
            amt, _r, info = e.convert_to_currency(_Decimal("195.583"), "BGN", "PLN", _date(2026, 3, 1))
        assert amt == _Decimal("430.00") and info == "fixed_eur_bgn_to_pln"
        m.assert_not_called()


def test_hrk_fixed_from_2023_dynamic_before():
    e = _reset_ecb_state()
    # À partir du 01/01/2023 : taux fixe, jamais de réseau
    with patch("tva_intracom.ecb_rates.urllib.request.urlopen") as m:
        eur, rate, src = e.convert_to_eur(_Decimal("753.45"), "HRK", _date(2023, 1, 1))
        assert (eur, rate, src) == (_Decimal("100.00"), _Decimal("7.53450"), "fixed_eur_hrk")
        assert e.get_closing_rate("HRK", _date(2023, 3, 31)) == _Decimal("7.53450")
        m.assert_not_called()
    # Avant : taux BCE dynamique (ici 7.5300 simulé), source "ecb"
    with patch.object(e, "get_rate", return_value=_Decimal("7.5300")) as g:
        eur, rate, src = e.convert_to_eur(_Decimal("753.00"), "HRK", _date(2022, 12, 30))
    g.assert_called_once()
    assert (eur, rate, src) == (_Decimal("100.00"), _Decimal("7.5300"), "ecb")


def test_prefetch_requests_ecb_for_hrk_bgn_before_adoption_only():
    e = _reset_ecb_state()
    asked = []

    def fake_batch(currencies, start, end):
        asked.extend(currencies)
        return {}

    with patch.object(e, "_db_get_rates_batch", return_value={}), \
         patch.object(e, "_fetch_ecb_batch", side_effect=fake_batch), \
         patch.object(e, "_db_upsert_batch"):
        e.prefetch_rates([
            ("HRK", _date(2022, 6, 1)), ("HRK", _date(2023, 6, 1)),
            ("BGN", _date(2025, 6, 1)), ("BGN", _date(2026, 6, 1)),
        ])
    assert sorted(set(asked)) == ["BGN", "HRK"]
    # une seule date avant adoption par devise : le batch ne couvre pas 2023+/2026+
    assert len(asked) == 2


def test_fixed_eur_rate_helper_boundaries():
    e = _reset_ecb_state()
    assert e.fixed_eur_rate("HRK", _date(2022, 12, 31)) is None
    assert e.fixed_eur_rate("HRK", _date(2023, 1, 1)) == _Decimal("7.53450")
    assert e.fixed_eur_rate("BGN", _date(2025, 12, 31)) is None
    assert e.fixed_eur_rate("BGN", _date(2026, 1, 1)) == _Decimal("1.95583")
    assert e.fixed_eur_rate("USD", _date(2026, 1, 1)) is None


# ---------------------------------------------------------------------------
# Perf BCE (2026-10-05) : gzip + pagination Postgres
# ---------------------------------------------------------------------------
import gzip as _gzip
import json as _json_e


class _FakeHttpResp:
    def __init__(self, body: bytes, headers: dict):
        self._body = body
        self.headers = headers

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_request_ecb_sends_accept_encoding_and_decodes_gzip():
    e = _reset_ecb_state()
    payload = {"hello": "world"}
    body = _gzip.compress(_json_e.dumps(payload).encode())
    seen = {}

    def fake_urlopen(req, timeout=None, context=None):
        seen["ae"] = req.get_header("Accept-encoding")
        return _FakeHttpResp(body, {"Content-Encoding": "gzip"})

    with patch("tva_intracom.ecb_rates.urllib.request.urlopen", side_effect=fake_urlopen):
        assert e._request_ecb("http://x", "t") == payload
    assert seen["ae"] == "gzip"


def test_request_ecb_plain_body_still_works_without_content_encoding():
    e = _reset_ecb_state()
    payload = {"a": 1}
    with patch("tva_intracom.ecb_rates.urllib.request.urlopen",
               return_value=_FakeHttpResp(_json_e.dumps(payload).encode(), {})):
        assert e._request_ecb("http://x", "t") == payload


def test_db_batch_uses_large_pages_to_limit_round_trips():
    """3000 paires : 2 allers-retours max (page_size=2000) au lieu de 30."""
    e = _reset_ecb_state()
    seen = {}

    class _Cur:
        def __enter__(self): return self
        def __exit__(self, *a): return False

    class _Conn(_Cur):
        def cursor(self): return _Cur()

    class _Pool:
        def getconn(self): return _Conn()
        def putconn(self, c): pass

    def fake_execute_values(cur, sql, argslist, template=None, page_size=100, fetch=False):
        seen.setdefault("sizes", []).append(page_size)
        return [] if fetch else None

    pairs = [("USD", _date(2020, 1, 1) + _timedelta(days=i)) for i in range(3000)]
    with patch.object(e, "_get_pool", return_value=_Pool()), \
         patch.object(e.psycopg2.extras, "execute_values", side_effect=fake_execute_values):
        e._db_get_rates_batch(pairs)
        e._db_upsert_batch([(c, d, _Decimal("1.1")) for c, d in pairs])
    assert seen["sizes"] == [2000, 2000]
