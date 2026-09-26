"""Tests pour l'optimisation du 2026-09-13 (5) : préchargement en lot des
taux de TVA dynamiques avant la boucle OSS (voir README - evolution.md).

Couvre deux choses distinctes :
  - engine._collect_vat_rate_prefetch_pairs : construction du SUPERSET de
    couples (pays, date) a partir d'un lot de ventes/avoirs.
  - vat_rates_db.prefetch_standard_rates : dedup + fetch parallele + cache,
    reutilise deja teste indirectement par test_vat_rates_db.py pour le
    chemin get_vat_rate ; ici on teste specifiquement le comportement en
    LOT (plusieurs pays/mois d'un coup, un seul appel reseau par couple
    distinct, jamais de doublon).
"""
from __future__ import annotations

import logging
from datetime import date
from decimal import Decimal
from unittest.mock import patch

import pytest

from tva_intracom import vat_rates_db as m
from tva_intracom.engine import _collect_vat_rate_prefetch_pairs
from tva_intracom.models import BuyerType, Sale


def make_sale(**kwargs) -> Sale:
    defaults = dict(
        sale_id="TEST-001",
        amount_ht=Decimal("100.00"),
        buyer_type=BuyerType.B2C,
        stock_country="FR",
        buyer_country="DE",
        seller_country="FR",
        buyer_vat_valid=False,
        buyer_vat_number="",
        transaction_date="2026-01-01",
        product_category="STANDARD",
    )
    defaults.update(kwargs)
    return Sale(**defaults)


# ---------------------------------------------------------------------
# _collect_vat_rate_prefetch_pairs (engine.py)
# ---------------------------------------------------------------------

class TestCollectPrefetchPairs:
    def test_includes_fr_stock_and_buyer_country(self):
        sale = make_sale(stock_country="IT", buyer_country="ES", transaction_date="2026-03-15")
        pairs = _collect_vat_rate_prefetch_pairs([sale])
        assert ("FR", date(2026, 3, 15)) in pairs
        assert ("IT", date(2026, 3, 15)) in pairs
        assert ("ES", date(2026, 3, 15)) in pairs

    def test_refund_uses_order_date_not_transaction_date(self):
        """Un avoir (amount_ht < 0) doit utiliser order_date (date de la
        vente d'origine) pour le taux, exactement comme _vat_rate_tx_date
        dans _run_oss_loop — sinon le prefetch chargerait le mauvais mois."""
        refund = make_sale(
            amount_ht=Decimal("-50.00"),
            transaction_date="2026-06-10",
            order_date="2026-01-20",
        )
        pairs = _collect_vat_rate_prefetch_pairs([refund])
        assert ("FR", date(2026, 1, 20)) in pairs
        assert not any(d == date(2026, 6, 10) for _, d in pairs)

    def test_refund_without_order_date_falls_back_to_transaction_date(self):
        refund = make_sale(amount_ht=Decimal("-50.00"), transaction_date="2026-06-10", order_date="")
        pairs = _collect_vat_rate_prefetch_pairs([refund])
        assert ("FR", date(2026, 6, 10)) in pairs

    def test_empty_transaction_date_skipped_without_error(self):
        sale = make_sale(transaction_date="")
        pairs = _collect_vat_rate_prefetch_pairs([sale])
        assert pairs == []

    def test_malformed_date_skipped_without_error(self):
        sale = make_sale(transaction_date="pas-une-date")
        pairs = _collect_vat_rate_prefetch_pairs([sale])
        assert pairs == []

    def test_multiple_sales_produce_superset_with_duplicates_allowed(self):
        """Pas de deduplication a ce niveau (faite par prefetch_standard_rates
        lui-meme) : la fonction peut renvoyer des doublons, c'est attendu."""
        sales = [
            make_sale(stock_country="FR", buyer_country="DE", transaction_date="2026-02-01"),
            make_sale(stock_country="FR", buyer_country="DE", transaction_date="2026-02-15"),
        ]
        pairs = _collect_vat_rate_prefetch_pairs(sales)
        assert pairs.count(("FR", date(2026, 2, 1))) >= 1
        assert pairs.count(("FR", date(2026, 2, 15))) >= 1


# ---------------------------------------------------------------------
# prefetch_standard_rates (vat_rates_db.py) : comportement en lot
# ---------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _reset_module_state():
    m.clear_cache(persistent=False)
    m._schema_ready = False
    m._db_unavailable = False
    yield
    m.clear_cache(persistent=False)
    m._schema_ready = False
    m._db_unavailable = False


@pytest.fixture
def tedb_enabled_no_db():
    def _secret(key, default=None):
        if key == "VAT_DYNAMIC_TEDB_ENABLED":
            return "true"
        if key == "SUPABASE_DB_URL":
            return None
        return default

    with patch.object(m, "get_secret", side_effect=_secret):
        yield


def _fake_request_tedb(iso_code, situation_date):
    """Simule une reponse TEDB minimale et plausible (20% STANDARD) pour
    n'importe quel pays — suffisant pour tester la mecanique de lot
    (dedup/parallelisme/cache), pas la fiscalite (deja couverte par
    test_vat_rates_db.py avec les fixtures XML reelles)."""
    import xml.etree.ElementTree as ET
    xml_str = (
        '<env:Envelope xmlns:env="http://schemas.xmlsoap.org/soap/envelope/">'
        '<env:Body><ns0:retrieveVatRatesRespMsg '
        'xmlns="urn:ec.europa.eu:taxud:tedb:services:v1:IVatRetrievalService:types" '
        'xmlns:ns0="urn:ec.europa.eu:taxud:tedb:services:v1:IVatRetrievalService">'
        f'<vatRateResults><memberState>{iso_code}</memberState><type>STANDARD</type>'
        f'<rate><type>DEFAULT</type><value>20.0</value></rate>'
        f'<situationOn>{situation_date.isoformat()}</situationOn></vatRateResults>'
        '</ns0:retrieveVatRatesRespMsg></env:Body></env:Envelope>'
    )
    root = ET.fromstring(xml_str)
    return root, xml_str.encode("utf-8")


def test_prefetch_dedupes_and_makes_one_call_per_distinct_pair(tedb_enabled_no_db):
    pairs = [
        ("FR", date(2026, 3, 1)), ("FR", date(2026, 3, 15)), ("FR", date(2026, 3, 31)),
        ("DE", date(2026, 3, 5)),
    ]
    with patch.object(m, "_request_tedb", side_effect=_fake_request_tedb) as mocked:
        m.prefetch_standard_rates(pairs)

    assert mocked.call_count == 2  # (FR, mars) + (DE, mars), dedup des 3 dates FR
    assert m.get_vat_rate("FR", "STANDARD", date(2026, 3, 20)) == Decimal("20.0")
    assert m.get_vat_rate("DE", "STANDARD", date(2026, 3, 1)) == Decimal("20.0")


def test_prefetch_progress_callback_reaches_total(tedb_enabled_no_db):
    pairs = [("FR", date(2026, 1, 1)), ("DE", date(2026, 1, 1)), ("ES", date(2026, 1, 1))]
    calls = []

    with patch.object(m, "_request_tedb", side_effect=_fake_request_tedb):
        m.prefetch_standard_rates(pairs, progress_callback=lambda d, t: calls.append((d, t)))

    assert calls
    assert calls[-1] == (3, 3)


def test_prefetch_skips_non_eligible_pairs_without_network_call(tedb_enabled_no_db):
    """Un pays hors couverture TEDB (ou categorie non STANDARD, ici sans
    objet car prefetch_standard_rates ne gere que STANDARD) ne doit
    declencher aucun appel reseau."""
    with patch.object(m, "_request_tedb") as mocked:
        m.prefetch_standard_rates([("US", date(2026, 1, 1))])
    mocked.assert_not_called()


def test_prefetch_noop_when_tedb_disabled():
    """Flag explicitement désactivé (VAT_DYNAMIC_TEDB_ENABLED="false") : prefetch_standard_rates
    ne doit jamais tenter le moindre appel reseau."""
    with patch.object(m, "get_secret", return_value="false"):
        with patch.object(m, "_request_tedb") as mocked:
            m.prefetch_standard_rates([("FR", date(2026, 1, 1)), ("DE", date(2026, 1, 1))])
        mocked.assert_not_called()


def test_prefetch_then_get_vat_rate_never_hits_network_again(tedb_enabled_no_db):
    """Le but meme de l'optimisation : apres un prefetch, get_vat_rate() ne
    doit plus jamais appeler le reseau pour les couples deja charges,
    quelle que soit la journee exacte demandee dans le mois."""
    with patch.object(m, "_request_tedb", side_effect=_fake_request_tedb):
        m.prefetch_standard_rates([("FR", date(2026, 5, 1))])

    with patch.object(m, "_request_tedb") as mocked_after:
        for day in (1, 10, 20, 28):
            assert m.get_vat_rate("FR", "STANDARD", date(2026, 5, day)) == Decimal("20.0")
    mocked_after.assert_not_called()


def test_prefetch_handles_partial_network_failure_gracefully(tedb_enabled_no_db, caplog):
    """Si UN SEUL couple echoue (ex: timeout), les autres doivent quand
    meme etre traites — pas d'exception remontee a l'appelant (meme
    posture defensive que _run_oss_loop / validate_vat_numbers_parallel)."""
    def _flaky(iso_code, situation_date):
        if iso_code == "IT":
            raise TimeoutError("simulated network failure")
        return _fake_request_tedb(iso_code, situation_date)

    with patch.object(m, "_request_tedb", side_effect=_flaky):
        with caplog.at_level(logging.WARNING, logger="tva_intracom.vat_rates_db"):
            m.prefetch_standard_rates([("FR", date(2026, 1, 1)), ("IT", date(2026, 1, 1))])

    assert m.get_vat_rate("FR", "STANDARD", date(2026, 1, 15)) == Decimal("20.0")
    # IT : repli statique (le fetch a echoue), pas de crash
    from tva_intracom.rates import STANDARD_VAT_RATES
    assert m.get_vat_rate("IT", "STANDARD", date(2026, 1, 15)) == STANDARD_VAT_RATES["IT"]


def test_prefetch_empty_pairs_is_noop(tedb_enabled_no_db):
    with patch.object(m, "_request_tedb") as mocked:
        m.prefetch_standard_rates([])
    mocked.assert_not_called()
