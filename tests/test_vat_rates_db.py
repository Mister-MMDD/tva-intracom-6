"""Tests pour vat_rates_db.py (taux de TVA dynamiques via TEDB SOAP).

Portee volontairement restreinte au taux STANDARD : les taux reduits
restent geres par rates.py, meme si leur structure TEDB est presente.

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


def test_parse_es_standard_ambiguous_uses_static_reference(caplog):
    """ES : parmi les valeurs STANDARD distinctes, la référence statique
    permet de retenir le taux continental plutôt que celui des Canaries."""
    root = _load_fixture("ES_standard_ambiguous_2026-01-01.xml")
    with caplog.at_level(logging.DEBUG, logger="tva_intracom.vat_rates_db"):
        result = m._parse_tedb_response(root, country="ES", target_date=date(2026, 1, 1))
    assert result["STANDARD"] == Decimal("21.0")
    assert any("ambiguïté résolue" in rec.message for rec in caplog.records)


def test_parse_es_ambiguous_does_not_silently_pick_first_document_order():
    """Regression directe de l'incident : s'assurer qu'on ne retombe JAMAIS
    sur 7.0 (Canaries, premiere entree du document) meme accidentellement."""
    root = _load_fixture("ES_standard_ambiguous_2026-01-01.xml")
    result = m._parse_tedb_response(root, country="ES", target_date=date(2026, 1, 1))
    assert result.get("STANDARD") != Decimal("7.0")


# ---------------------------------------------------------------------
# Perimetre dynamique : seuls les taux STANDARD sont eligibles
# ---------------------------------------------------------------------

def test_is_tedb_eligible_standard_true_when_enabled(tedb_enabled_no_db):
    assert m._is_tedb_eligible("FR", "STANDARD") is True


@pytest.mark.parametrize("rate_type", [
    "FOOD", "MEDICINES", "PARKING", "PERIODICALS", "MEDICAL_EQUIPMENT",
    "CHILDREN_CAR_SEATS", "SOLAR_PANELS", "PLANT", "FOSSIL_FUEL",
    "CHEMICAL_FERTILISERS", "CHEMICAL_PESTICIDES_ENVIRONMENT",
    "CERTAIN_AGRICULTURAL_INPUT", "CHILD_WEAR", "AGRICULTURAL_PRODUCTION",
    "BOOKS", "CLOTHING", "SUPER_REDUCED",
])
def test_is_tedb_eligible_non_standard_always_false(tedb_enabled_no_db, rate_type):
    """Toute catégorie autre que STANDARD reste en repli statique."""
    assert m._is_tedb_eligible("FR", rate_type) is False


def test_is_tedb_eligible_false_when_flag_disabled():
    """Flag explicitement désactivé (VAT_DYNAMIC_TEDB_ENABLED="false") : personne n'est éligible,
    tout part directement au statique — comportement identique a avant
    l'introduction de ce module."""
    with patch.object(m, "get_secret", return_value="false"):
        assert m._is_tedb_eligible("FR", "STANDARD") is False


# ---------------------------------------------------------------------
# get_vat_rate() de bout en bout : le cas ambigu ES ne doit JAMAIS renvoyer
# un taux errone, meme integre au flux complet (cache L1/L2/TEDB/statique)
# ---------------------------------------------------------------------

def test_get_vat_rate_es_ambiguous_uses_static_reference(tedb_enabled_no_db, caplog):
    root = _load_fixture("ES_standard_ambiguous_2026-01-01.xml")
    fake_raw_xml = (FIXTURES_DIR / "ES_standard_ambiguous_2026-01-01.xml").read_bytes()

    with patch.object(m, "_request_tedb", return_value=(root, fake_raw_xml)):
        with caplog.at_level(logging.DEBUG, logger="tva_intracom.vat_rates_db"):
            rate = m.get_vat_rate("ES", "STANDARD", date(2026, 1, 1))

    assert rate == Decimal("21")
    assert any("source=TEDB_FETCH" in rec.message for rec in caplog.records)


def test_get_vat_rate_fr_standard_uses_tedb_when_plausible(tedb_enabled_no_db, caplog):
    root = _load_fixture("FR_standard_2025-07-01.xml")
    fake_raw_xml = (FIXTURES_DIR / "FR_standard_2025-07-01.xml").read_bytes()

    with patch.object(m, "_request_tedb", return_value=(root, fake_raw_xml)):
        with caplog.at_level(logging.DEBUG, logger="tva_intracom.vat_rates_db"):
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
        with caplog.at_level(logging.DEBUG, logger="tva_intracom.vat_rates_db"):
            rate = m.get_vat_rate("FR", "STANDARD", date(2025, 7, 1))

    assert rate == Decimal("20.0")
    mocked_request.assert_called_once()  # un seul appel reseau au total
    assert any("source=L1_RAM" in rec.message for rec in caplog.records)


def test_get_vat_rate_non_standard_category_never_calls_tedb(tedb_enabled_no_db):
    """Les catégories REDUCED restent statiques, même pour un pays sûr."""
    with patch.object(m, "_request_tedb") as mocked_request:
        rate = m.get_vat_rate("PT", "FOOD", date(2025, 7, 1))
    assert rate == m._static_vat_rate_at_date("PT", date(2025, 7, 1), "FOOD")
    mocked_request.assert_not_called()


# ---------------------------------------------------------------------
# Regression 2026-09-13 (2) : pas de pollution de logs par les categories
# REDUCED non utilisées et cache journalier côté TEDB
# ---------------------------------------------------------------------

def test_parse_response_ignores_reduced_categories():
    """Le parseur ne conserve que STANDARD ; les taux REDUCED sont ignorés."""
    root = _load_fixture("FR_standard_2025-07-01.xml")
    result = m._parse_tedb_response(root, country="FR", target_date=date(2025, 7, 1))
    assert result == {"STANDARD": Decimal("20.0")}


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


def test_get_vat_rate_midmonth_change_keeps_daily_rates_separate(tedb_enabled_no_db):
    """Un changement TEDB au milieu du mois ne doit pas être masqué par le cache."""
    transition_date = date(2026, 3, 15)

    def _request_with_midmonth_change(iso_code, target_date):
        rate = "20.0" if target_date < transition_date else "19.0"
        xml = (
            "<Envelope><Body><retrieveVatRatesRespMsg><vatRateResults>"
            f"<memberState>{iso_code}</memberState><type>STANDARD</type>"
            f"<rate><type>DEFAULT</type><value>{rate}</value></rate>"
            f"<situationOn>{target_date.isoformat()}</situationOn>"
            "</vatRateResults></retrieveVatRatesRespMsg></Body></Envelope>"
        )
        return ET.fromstring(xml), xml.encode("utf-8")

    with patch.object(m, "_request_tedb", side_effect=_request_with_midmonth_change) as mocked:
        assert m.get_vat_rate("FR", "STANDARD", date(2026, 3, 1)) == Decimal("20.0")
        assert m.get_vat_rate("FR", "STANDARD", date(2026, 3, 14)) == Decimal("20.0")
        assert m.get_vat_rate("FR", "STANDARD", transition_date) == Decimal("19.0")
        assert m.get_vat_rate("FR", "STANDARD", date(2026, 3, 31)) == Decimal("19.0")
        # Cache hits on both sides of the effective date must preserve their rates.
        assert m.get_vat_rate("FR", "STANDARD", date(2026, 3, 1)) == Decimal("20.0")
        assert m.get_vat_rate("FR", "STANDARD", transition_date) == Decimal("19.0")

    assert mocked.call_count == 4
    assert [call.args[1] for call in mocked.call_args_list] == [
        date(2026, 3, 1), date(2026, 3, 14), transition_date, date(2026, 3, 31),
    ]


def test_get_vat_rate_different_months_trigger_separate_tedb_calls(tedb_enabled_no_db):
    """Des dates différentes déclenchent chacune une interrogation TEDB."""
    root = _load_fixture("FR_standard_2025-07-01.xml")
    fake_raw_xml = (FIXTURES_DIR / "FR_standard_2025-07-01.xml").read_bytes()

    with patch.object(m, "_request_tedb", return_value=(root, fake_raw_xml)) as mocked_request:
        m.get_vat_rate("FR", "STANDARD", date(2025, 7, 15))
        m.get_vat_rate("FR", "STANDARD", date(2025, 8, 3))

    assert mocked_request.call_count == 2


def test_vat_rate_public_api_unaffected_when_tedb_disabled():
    """Quand le flag est explicitement désactivé, l'API conserve son repli statique."""
    with patch.object(m, "get_secret", return_value="false"):
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

        # Deuxieme appel, meme date : doit retenter TEDB (pas bloque par
        # un cache L1 pollue par le repli precedent) et reussir cette fois.
        rate2 = m.get_vat_rate("FR", "STANDARD", date(2025, 7, 1))
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
        # Deuxième appel à la même date : doit venir du cache L1.
        with caplog.at_level(logging.DEBUG, logger="tva_intracom.vat_rates_db"):
            rate = m.get_vat_rate("ES", "STANDARD", date(2026, 1, 1))

    assert rate == Decimal("21")
    mocked.assert_called_once()
    assert any("source=L1_RAM" in rec.message for rec in caplog.records)


def test_permanently_failed_window_prevents_repeated_network_calls(tedb_enabled_no_db):
    """Tant que la fenetre _FAILED_PAIR_TTL_SECONDS n'est pas ecoulee, on
    ne doit PAS retenter le reseau a chaque ligne (protection deja
    existante, ne doit pas regresser avec le correctif ci-dessus)."""
    with patch.object(m, "_request_tedb", return_value=None) as mocked:
        m.get_vat_rate("FR", "STANDARD", date(2025, 7, 1))
        m.get_vat_rate("FR", "STANDARD", date(2025, 7, 1))
        m.get_vat_rate("FR", "STANDARD", date(2025, 7, 1))

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
        m.get_vat_rate("FR", "STANDARD", date(2025, 7, 1))
        m.get_vat_rate("FR", "STANDARD", date(2025, 7, 1))

    mocked_db.assert_not_called()
