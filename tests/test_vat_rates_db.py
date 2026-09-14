"""Tests pour vat_rates_db.py (taux de TVA dynamiques via TEDB SOAP).

Portee volontairement restreinte au taux STANDARD (cf. session 2026-09-13) :
FOOD/MEDICINES/PARKING ne sont pas testes ici, ils restent geres par
rates.py comme avant l'introduction de ce module.

Deux fixtures XML REELLES (capturees en direct depuis TEDB, pas generees a
la main) servent de base :
  - FR_standard_2025-07-01.xml : cas simple, une seule entree STANDARD (20%)
  - ES_standard_ambiguous_2026-01-01.xml : cas ambigu confirme en prod, DEUX
    entrees STANDARD distinctes (7% Canaries hors TVA UE + 21% continent) —
    origine exacte du bug d'incident du 2026-09-12.
"""
from __future__ import annotations

import logging
import xml.etree.ElementTree as ET
from datetime import date
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

import pytest

from tva_intracom import vat_rates_db as m

FIXTURES_DIR = Path(__file__).parent / "fixtures" / "tedb"


def _load_fixture(name: str) -> ET.Element:
    raw = (FIXTURES_DIR / name).read_bytes()
    return ET.fromstring(raw)


@pytest.fixture(autouse=True)
def _reset_module_state():
    """Reinitialise tout l'etat process (L1 RAM, lru_cache, flags DB,
    paires en echec) avant et apres chaque test — sans toucher a une vraie
    base (persistent=False)."""
    m.clear_cache(persistent=False)
    m._schema_ready = False
    m._db_unavailable = False
    yield
    m.clear_cache(persistent=False)
    m._schema_ready = False
    m._db_unavailable = False


@pytest.fixture
def tedb_enabled_no_db():
    """Active le flag dynamique TEDB, sans base Postgres configuree (donc
    cache L2 systematiquement absent — force le chemin memoire/TEDB/statique)."""
    def _secret(key, default=None):
        if key == "VAT_DYNAMIC_TEDB_ENABLED":
            return "true"
        if key == "SUPABASE_DB_URL":
            return None
        return default

    with patch.object(m, "get_secret", side_effect=_secret):
        yield


# ---------------------------------------------------------------------
# Parsing bas niveau (_parse_tedb_response) sur donnees TEDB reelles
# ---------------------------------------------------------------------

def test_parse_fr_standard_single_value_returns_20():
    """FR : une seule entree STANDARD/DEFAULT (20.0) -> cas non ambigu."""
    root = _load_fixture("FR_standard_2025-07-01.xml")
    result = m._parse_tedb_response(root, country="FR", target_date=date(2025, 7, 1))
    assert result["STANDARD"] == Decimal("20.0")


def test_parse_es_standard_ambiguous_omits_standard_key(caplog):
    """ES : deux entrees STANDARD/DEFAULT distinctes (7.0 Canaries, 21.0
    continent) -> resultat juge ambigu, AUCUNE cle STANDARD retournee
    (repli statique assure cote appelant), et un warning explicite loggue."""
    root = _load_fixture("ES_standard_ambiguous_2026-01-01.xml")
    with caplog.at_level(logging.WARNING, logger="tva_intracom.vat_rates_db"):
        result = m._parse_tedb_response(root, country="ES", target_date=date(2026, 1, 1))
    assert "STANDARD" not in result
    assert any("valeurs STANDARD distinctes" in rec.message for rec in caplog.records)


def test_parse_es_ambiguous_does_not_silently_pick_first_document_order():
    """Regression directe de l'incident : s'assurer qu'on ne retombe JAMAIS
    sur 7.0 (Canaries, premiere entree du document) meme accidentellement."""
    root = _load_fixture("ES_standard_ambiguous_2026-01-01.xml")
    result = m._parse_tedb_response(root, country="ES", target_date=date(2026, 1, 1))
    assert result.get("STANDARD") != Decimal("7.0")


# ---------------------------------------------------------------------
# Perimetre restreint : eligibilite TEDB limitee au STANDARD
# ---------------------------------------------------------------------

def test_is_tedb_eligible_standard_true_when_enabled(tedb_enabled_no_db):
    assert m._is_tedb_eligible("FR", "STANDARD") is True


@pytest.mark.parametrize("rate_type", ["FOOD", "MEDICINES", "PARKING", "BOOKS", "CLOTHING"])
def test_is_tedb_eligible_non_standard_always_false(tedb_enabled_no_db, rate_type):
    """Meme avec le flag dynamique actif, les categories autres que STANDARD
    restent hors perimetre TEDB pour l'instant (repli rates.py systematique)."""
    assert m._is_tedb_eligible("FR", rate_type) is False


def test_is_tedb_eligible_false_when_flag_disabled():
    """Comportement par defaut (flag desactive) : personne n'est eligible,
    tout part directement au statique — comportement identique a avant
    l'introduction de ce module."""
    with patch.object(m, "get_secret", return_value=None):
        assert m._is_tedb_eligible("FR", "STANDARD") is False


# ---------------------------------------------------------------------
# get_vat_rate() de bout en bout : le cas ambigu ES ne doit JAMAIS renvoyer
# un taux errone, meme integre au flux complet (cache L1/L2/TEDB/statique)
# ---------------------------------------------------------------------

def test_get_vat_rate_es_ambiguous_falls_back_to_static_21(tedb_enabled_no_db, caplog):
    root = _load_fixture("ES_standard_ambiguous_2026-01-01.xml")
    fake_raw_xml = (FIXTURES_DIR / "ES_standard_ambiguous_2026-01-01.xml").read_bytes()

    with patch.object(m, "_request_tedb", return_value=(root, fake_raw_xml)):
        with caplog.at_level(logging.INFO, logger="tva_intracom.vat_rates_db"):
            rate = m.get_vat_rate("ES", "STANDARD", date(2026, 1, 1))

    assert rate == Decimal("21")
    assert any("source=STATIC_FALLBACK" in rec.message for rec in caplog.records)


def test_get_vat_rate_fr_standard_uses_tedb_when_plausible(tedb_enabled_no_db, caplog):
    root = _load_fixture("FR_standard_2025-07-01.xml")
    fake_raw_xml = (FIXTURES_DIR / "FR_standard_2025-07-01.xml").read_bytes()

    with patch.object(m, "_request_tedb", return_value=(root, fake_raw_xml)):
        with caplog.at_level(logging.INFO, logger="tva_intracom.vat_rates_db"):
            rate = m.get_vat_rate("FR", "STANDARD", date(2025, 7, 1))

    assert rate == Decimal("20.0")
    assert any("source=TEDB_FETCH" in rec.message for rec in caplog.records)


def test_get_vat_rate_memory_cache_hit_logs_l1_source(tedb_enabled_no_db, caplog):
    """Deuxieme appel identique -> doit venir du cache L1 RAM, pas d'un
    nouvel appel reseau."""
    root = _load_fixture("FR_standard_2025-07-01.xml")
    fake_raw_xml = (FIXTURES_DIR / "FR_standard_2025-07-01.xml").read_bytes()

    with patch.object(m, "_request_tedb", return_value=(root, fake_raw_xml)) as mocked_request:
        m.get_vat_rate("FR", "STANDARD", date(2025, 7, 1))
        with caplog.at_level(logging.INFO, logger="tva_intracom.vat_rates_db"):
            rate = m.get_vat_rate("FR", "STANDARD", date(2025, 7, 1))

    assert rate == Decimal("20.0")
    mocked_request.assert_called_once()  # un seul appel reseau au total
    assert any("source=L1_RAM" in rec.message for rec in caplog.records)


def test_get_vat_rate_non_standard_category_never_calls_tedb(tedb_enabled_no_db):
    """FOOD reste hors perimetre : aucun appel reseau ne doit meme etre
    tente (coherent avec le principe scale-to-zero : pas d'appel sortant
    superflu)."""
    with patch.object(m, "_request_tedb") as mocked_request:
        m.get_vat_rate("FR", "FOOD", date(2025, 7, 1))
    mocked_request.assert_not_called()


# ---------------------------------------------------------------------
# Regression 2026-09-13 (2) : pas de pollution de logs par les categories
# REDUCED non utilisees, et cache par MOIS (pas par jour) cote TEDB
# ---------------------------------------------------------------------

def test_parse_response_skips_reduced_categories_by_default():
    """Meme si le XML contient des categories REDUCED mappees (FOOD...),
    elles ne doivent PAS apparaitre dans le resultat tant que
    _PARSE_REDUCED_CATEGORIES est False (perimetre STANDARD uniquement)."""
    root = _load_fixture("FR_standard_2025-07-01.xml")  # contient FOODSTUFFS etc.
    result = m._parse_tedb_response(root, country="FR", target_date=date(2025, 7, 1))
    assert set(result.keys()) == {"STANDARD"}


def test_fetch_does_not_warn_about_unused_reduced_categories(tedb_enabled_no_db, caplog):
    """Un ecart de plausibilite sur une categorie REDUCED non consommee
    (FOOD, etc.) ne doit jamais generer de warning ni de dump XML, car ces
    categories ne sont meme plus extraites (cf. _PARSE_REDUCED_CATEGORIES)."""
    root = _load_fixture("FR_standard_2025-07-01.xml")
    fake_raw_xml = (FIXTURES_DIR / "FR_standard_2025-07-01.xml").read_bytes()

    with patch.object(m, "_request_tedb", return_value=(root, fake_raw_xml)):
        with caplog.at_level(logging.WARNING, logger="tva_intracom.vat_rates_db"):
            m.get_vat_rate("FR", "STANDARD", date(2025, 7, 1))

    assert not any("rejeté" in rec.message for rec in caplog.records)


def test_get_vat_rate_same_month_different_days_share_one_tedb_call(tedb_enabled_no_db):
    """Deux dates distinctes du MEME mois ne doivent declencher qu'un seul
    appel SOAP TEDB (granularite mensuelle, demande Matthieu 2026-09-13) :
    un taux standard ne change qu'au 1er du mois."""
    root = _load_fixture("FR_standard_2025-07-01.xml")
    fake_raw_xml = (FIXTURES_DIR / "FR_standard_2025-07-01.xml").read_bytes()

    with patch.object(m, "_request_tedb", return_value=(root, fake_raw_xml)) as mocked_request:
        rate_day1 = m.get_vat_rate("FR", "STANDARD", date(2025, 7, 1))
        rate_day15 = m.get_vat_rate("FR", "STANDARD", date(2025, 7, 15))
        rate_day31 = m.get_vat_rate("FR", "STANDARD", date(2025, 7, 31))

    assert rate_day1 == rate_day15 == rate_day31 == Decimal("20.0")
    mocked_request.assert_called_once()
    # L'appel effectif doit avoir ete fait avec le 1er du mois normalise,
    # jamais avec une date arbitraire "vue en premier".
    called_with_date = mocked_request.call_args[0][1]
    assert called_with_date == date(2025, 7, 1)


def test_get_vat_rate_different_months_trigger_separate_tedb_calls(tedb_enabled_no_db):
    """A l'inverse, deux mois differents doivent bien re-interroger TEDB
    (pas de sur-cache au-dela de la granularite mensuelle prevue)."""
    root = _load_fixture("FR_standard_2025-07-01.xml")
    fake_raw_xml = (FIXTURES_DIR / "FR_standard_2025-07-01.xml").read_bytes()

    with patch.object(m, "_request_tedb", return_value=(root, fake_raw_xml)) as mocked_request:
        m.get_vat_rate("FR", "STANDARD", date(2025, 7, 15))
        m.get_vat_rate("FR", "STANDARD", date(2025, 8, 3))

    assert mocked_request.call_count == 2


def test_vat_rate_public_api_unaffected_when_tedb_disabled():
    """Sans le flag active (comportement par defaut / production actuelle),
    vat_rate() se comporte a l'identique d'avant l'introduction du module."""
    with patch.object(m, "get_secret", return_value=None):
        assert m.vat_rate("FR", "STANDARD", date(2025, 7, 1)) == Decimal("20")
        assert m.vat_rate("ES", "STANDARD", date(2026, 1, 1)) == Decimal("21")


# ---------------------------------------------------------------------
# Regression audit 2026-09-13 (6) : une panne TEDB transitoire ne doit
# JAMAIS figer le taux sur le statique pour le reste du process — le
# mecanisme de reessai apres _FAILED_PAIR_TTL_SECONDS doit rester actif.
# ---------------------------------------------------------------------

def test_transient_network_failure_does_not_poison_l1_cache_forever(tedb_enabled_no_db):
    """Avant le correctif du 2026-09-13 (6) : un premier echec reseau
    ecrivait le repli statique en cache L1 (sans TTL) -> plus jamais
    ressayé, meme apres le retour de TEDB. Ce test aurait echoue sur
    l'ancien code."""
    root = _load_fixture("FR_standard_2025-07-01.xml")
    fake_raw_xml = (FIXTURES_DIR / "FR_standard_2025-07-01.xml").read_bytes()

    call_count = {"n": 0}

    def _flaky_then_ok(iso_code, target_date):
        call_count["n"] += 1
        if call_count["n"] == 1:
            return None  # simulate panne reseau (toutes tentatives epuisees)
        return root, fake_raw_xml

    with patch.object(m, "_request_tedb", side_effect=_flaky_then_ok):
        # Premier appel : echec reseau -> repli statique, mais ne doit PAS
        # figer le cache L1 (le futur appel doit pouvoir retenter).
        rate1 = m.get_vat_rate("FR", "STANDARD", date(2025, 7, 1))
        assert rate1 == Decimal("20")  # statique

        # On force la fin de la fenetre d'echec temporaire pour simuler
        # l'ecoulement de _FAILED_PAIR_TTL_SECONDS sans attendre 5 minutes.
        m._failed_pairs.clear()

        # Deuxieme appel, meme mois : doit retenter TEDB (pas bloque par
        # un cache L1 pollue par le repli precedent) et reussir cette fois.
        rate2 = m.get_vat_rate("FR", "STANDARD", date(2025, 7, 15))
        assert rate2 == Decimal("20.0")
        assert call_count["n"] == 2  # bien deux tentatives reseau distinctes


def test_result_obtained_but_category_rejected_is_cached_in_l1(tedb_enabled_no_db, caplog):
    """A l'inverse : quand TEDB REPOND mais que la categorie est absente/
    rejetee (ex: cas ambigu ES), c'est un fait fiscal stable -> mise en
    cache L1 attendue (pas de nouvel appel reseau pour la meme ligne)."""
    root = _load_fixture("ES_standard_ambiguous_2026-01-01.xml")
    fake_raw_xml = (FIXTURES_DIR / "ES_standard_ambiguous_2026-01-01.xml").read_bytes()

    with patch.object(m, "_request_tedb", return_value=(root, fake_raw_xml)) as mocked:
        m.get_vat_rate("ES", "STANDARD", date(2026, 1, 1))
        # Deuxieme appel meme mois : doit venir du cache L1 (pas un nouvel
        # appel reseau), car le rejet est un fait stable pour ce mois.
        with caplog.at_level(logging.INFO, logger="tva_intracom.vat_rates_db"):
            rate = m.get_vat_rate("ES", "STANDARD", date(2026, 1, 15))

    assert rate == Decimal("21")
    mocked.assert_called_once()
    assert any("source=L1_RAM" in rec.message for rec in caplog.records)


def test_permanently_failed_window_prevents_repeated_network_calls(tedb_enabled_no_db):
    """Tant que la fenetre _FAILED_PAIR_TTL_SECONDS n'est pas ecoulee, on
    ne doit PAS retenter le reseau a chaque ligne (protection deja
    existante, ne doit pas regresser avec le correctif ci-dessus)."""
    with patch.object(m, "_request_tedb", return_value=None) as mocked:
        m.get_vat_rate("FR", "STANDARD", date(2025, 7, 1))
        m.get_vat_rate("FR", "STANDARD", date(2025, 7, 2))
        m.get_vat_rate("FR", "STANDARD", date(2025, 7, 3))

    mocked.assert_called_once()  # 1 seul essai reseau pour les 3 lignes


def test_permanently_failed_window_skips_l2_lookup_too(tedb_enabled_no_db):
    """Audit 2026-09-13 (6), deuxieme volet : pendant la fenetre d'echec
    temporaire, on ne doit meme plus interroger Postgres (L2) a chaque
    ligne — seule la premiere ligne (avant que l'echec soit constate) a le
    droit de le faire. Sans ce correctif, un gros fichier pendant une
    panne TEDB ferait un aller-retour Postgres par ligne pour rien."""
    with patch.object(m, "_request_tedb", return_value=None):
        m.get_vat_rate("FR", "STANDARD", date(2025, 7, 1))  # constate l'echec

    with patch.object(m, "_db_get_rate") as mocked_db:
        m.get_vat_rate("FR", "STANDARD", date(2025, 7, 2))
        m.get_vat_rate("FR", "STANDARD", date(2025, 7, 3))

    mocked_db.assert_not_called()

