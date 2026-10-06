"""Tests pour vat_rates_db.py (taux de TVA dynamiques via TEDB SOAP).

Deux fixtures XML REELLES (capturees en direct depuis TEDB, pas generees a
la main) servent de base :
  - FR_standard_2025-07-01.xml : cas simple, une seule entree STANDARD (20%)
  - ES_standard_ambiguous_2026-01-01.xml : deux entrees STANDARD distinctes
    (7% Canaries hors TVA UE + 21% continent), avec resolution conservatrice
    par correspondance unique au taux statique.
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


def test_parse_es_standard_ambiguous_uses_unique_static_match(caplog):
    """ES : parmi les entrées STANDARD distinctes, la correspondance
    unique au taux statique (21% continental) résout l'ambiguïté."""
    root = _load_fixture("ES_standard_ambiguous_2026-01-01.xml")
    with caplog.at_level(logging.WARNING, logger="tva_intracom.vat_rates_db"):
        result = m._parse_tedb_response(root, country="ES", target_date=date(2026, 1, 1))
    assert result["STANDARD"] == Decimal("21")
    assert result["STANDARD"] != Decimal("7")
    assert not any("valeurs STANDARD distinctes" in rec.message for rec in caplog.records)


def test_parse_es_ambiguous_does_not_silently_pick_first_document_order():
    """Regression directe de l'incident : s'assurer qu'on ne retombe JAMAIS
    sur 7.0 (Canaries, premiere entree du document) meme accidentellement."""
    root = _load_fixture("ES_standard_ambiguous_2026-01-01.xml")
    result = m._parse_tedb_response(root, country="ES", target_date=date(2026, 1, 1))
    assert result.get("STANDARD") != Decimal("7.0")


# ---------------------------------------------------------------------
# Regression 2026-09-27 : le taux ES doit rester dynamique meme si le
# taux legal reel change et que le statique (rates.py) devient perime —
# c'etait exactement le risque signale par Matthieu (correspondance au
# statique = ne "detecte" jamais un vrai changement de taux). Le plancher
# legal UE (15%, art. 97 Dir. 2006/112/CE) doit trancher seul, sans
# dependre de l'exactitude du statique.
# ---------------------------------------------------------------------

def _es_fixture_with_standard_rate(new_value: str) -> ET.Element:
    """Reconstruit la fixture ES reelle en changeant uniquement la valeur
    du candidat STANDARD continental (21.0), pour simuler un changement
    legal reel sans toucher au candidat Canaries (7.0)."""
    raw = (FIXTURES_DIR / "ES_standard_ambiguous_2026-01-01.xml").read_text()
    target = (
        "<value>21.0</value></rate><situationOn>2026-01-01+01:00"
        "</situationOn></vatRateResults>"
    )
    assert raw.count(target) == 1, "fixture ES modifiee de facon inattendue"
    replacement = (
        f"<value>{new_value}</value></rate><situationOn>2026-01-01+01:00"
        "</situationOn></vatRateResults>"
    )
    return ET.fromstring(raw.replace(target, replacement))


def test_parse_es_uses_dynamic_rate_even_when_static_reference_is_stale(caplog):
    """Le vrai taux ES est desormais 22% (simule un changement legal
    posterieur a la valeur figee dans rates.py, restee a 21%). Avant le
    correctif du 2026-09-27, la correspondance au statique echouait
    (22 != 21) -> resultat ambigu -> repli statique errone (21% au lieu
    de 22%). Le plancher legal (>= 15%) doit desormais isoler seul le
    candidat continental (22%, seul >= 15%) sans avoir besoin qu'il
    egale le statique."""
    root = _es_fixture_with_standard_rate("22.0")
    with caplog.at_level(logging.WARNING, logger="tva_intracom.vat_rates_db"):
        result = m._parse_tedb_response(root, country="ES", target_date=date(2026, 1, 1))
    assert result["STANDARD"] == Decimal("22.0")
    assert not any("valeurs STANDARD distinctes" in rec.message for rec in caplog.records)


def test_parse_es_below_floor_candidate_never_wins_even_if_it_matches_static():
    """Garde-fou inverse : meme si (par coincidence) le statique valait le
    candidat Canaries, celui-ci reste hors champ TVA UE (< 15%) et ne doit
    jamais etre retenu comme taux STANDARD."""
    root = _es_fixture_with_standard_rate("21.0")
    with patch.object(m, "_STATIC_STANDARD_RATES", {**m._STATIC_STANDARD_RATES, "ES": Decimal("7.0")}):
        result = m._parse_tedb_response(root, country="ES", target_date=date(2026, 1, 1))
    assert result["STANDARD"] == Decimal("21.0")
    assert result["STANDARD"] != Decimal("7.0")


def test_get_vat_rate_es_dynamic_change_end_to_end(tedb_enabled_no_db):
    """Bout en bout : get_vat_rate() renvoie bien le nouveau taux dynamique
    (22%) et non le statique perime (21%), sans intervention manuelle sur
    rates.py."""
    root = _es_fixture_with_standard_rate("22.0")
    fake_raw_xml = ET.tostring(root)

    with patch.object(m, "_request_tedb", return_value=(root, fake_raw_xml)):
        rate = m.get_vat_rate("ES", "STANDARD", date(2026, 1, 1))

    assert rate == Decimal("22.0")


def test_parse_multiple_candidates_above_floor_falls_back_to_static_match(caplog):
    """Cas type Portugal (continent/Madere/Acores : plusieurs taux STANDARD
    distincts, tous >= 15%) : le plancher legal seul ne suffit pas a
    trancher, le filet de securite (correspondance statique) doit prendre
    le relais, sans regression du comportement conservateur."""
    root = ET.fromstring(
        "<env:Envelope xmlns:env='http://schemas.xmlsoap.org/soap/envelope/'>"
        "<env:Body><ns0:retrieveVatRatesRespMsg "
        "xmlns='urn:ec.europa.eu:taxud:tedb:services:v1:IVatRetrievalService:types' "
        "xmlns:ns0='urn:ec.europa.eu:taxud:tedb:services:v1:IVatRetrievalService'>"
        "<vatRateResults><memberState>PT</memberState><type>STANDARD</type>"
        "<rate><type>DEFAULT</type><value>23.0</value></rate>"
        "<situationOn>2026-01-01+01:00</situationOn></vatRateResults>"
        "<vatRateResults><memberState>PT</memberState><type>STANDARD</type>"
        "<rate><type>DEFAULT</type><value>22.0</value></rate>"
        "<situationOn>2026-01-01+01:00</situationOn>"
        "<comment>Madeira</comment></vatRateResults>"
        "</ns0:retrieveVatRatesRespMsg></env:Body></env:Envelope>"
    )
    with patch.object(m, "_STATIC_STANDARD_RATES", {**m._STATIC_STANDARD_RATES, "PT": Decimal("23.0")}):
        with caplog.at_level(logging.WARNING, logger="tva_intracom.vat_rates_db"):
            result = m._parse_tedb_response(root, country="PT", target_date=date(2026, 1, 1))
    assert result["STANDARD"] == Decimal("23.0")
    assert not any("valeurs STANDARD distinctes" in rec.message for rec in caplog.records)


def test_parse_multiple_candidates_above_floor_no_static_match_is_ambiguous(caplog):
    """Meme cas que ci-dessus mais sans reference statique correspondante :
    doit rester ambigu (repli statique + warning), comportement inchange."""
    root = ET.fromstring(
        "<env:Envelope xmlns:env='http://schemas.xmlsoap.org/soap/envelope/'>"
        "<env:Body><ns0:retrieveVatRatesRespMsg "
        "xmlns='urn:ec.europa.eu:taxud:tedb:services:v1:IVatRetrievalService:types' "
        "xmlns:ns0='urn:ec.europa.eu:taxud:tedb:services:v1:IVatRetrievalService'>"
        "<vatRateResults><memberState>PT</memberState><type>STANDARD</type>"
        "<rate><type>DEFAULT</type><value>23.0</value></rate>"
        "<situationOn>2026-01-01+01:00</situationOn></vatRateResults>"
        "<vatRateResults><memberState>PT</memberState><type>STANDARD</type>"
        "<rate><type>DEFAULT</type><value>22.0</value></rate>"
        "<situationOn>2026-01-01+01:00</situationOn>"
        "<comment>Madeira</comment></vatRateResults>"
        "</ns0:retrieveVatRatesRespMsg></env:Body></env:Envelope>"
    )
    with patch.object(m, "_STATIC_STANDARD_RATES", {**m._STATIC_STANDARD_RATES, "PT": Decimal("99.0")}):
        with caplog.at_level(logging.WARNING, logger="tva_intracom.vat_rates_db"):
            result = m._parse_tedb_response(root, country="PT", target_date=date(2026, 1, 1))
    assert "STANDARD" not in result
    assert any("valeurs STANDARD distinctes" in rec.message for rec in caplog.records)


# ---------------------------------------------------------------------
# Perimetre restreint : eligibilite TEDB limitee au STANDARD
# ---------------------------------------------------------------------

def test_is_tedb_eligible_standard_true_when_enabled(tedb_enabled_no_db):
    assert m._is_tedb_eligible("FR", "STANDARD") is True


@pytest.mark.parametrize("rate_type", ["PARKING", "BOOKS", "CLOTHING"])
def test_is_tedb_eligible_non_standard_always_false(tedb_enabled_no_db, rate_type):
    """Categories jamais mappees a une categorie TEDB (BOOKS/CLOTHING) ou
    sans safe-list definie (PARKING) : jamais eligibles, quel que soit le
    pays -> repli rates.py systematique."""
    assert m._is_tedb_eligible("FR", rate_type) is False


def test_is_tedb_eligible_food_true_for_safe_country(tedb_enabled_no_db):
    """Fin de la restriction STANDARD-only (2026-09-16) : FR est dans la
    safe-list FOOD (un seul taux dans tout le dump TEDB) -> eligible."""
    assert m._is_tedb_eligible("FR", "FOOD") is True


def test_is_tedb_eligible_food_false_for_ambiguous_country(tedb_enabled_no_db):
    """PT est un pays FOOD ambigu (plusieurs taux distincts selon le
    produit dans le dump TEDB) -> jamais interroge en dynamique pour cette
    categorie, meme si PT est bien un pays TEDB_SUPPORTED pour STANDARD."""
    assert m._is_tedb_eligible("PT", "FOOD") is False


def test_is_tedb_eligible_medicines_false_for_fr(tedb_enabled_no_db):
    """FR est un pays MEDICINES ambigu (10%/5,5%/2,1% selon le statut de
    remboursement, non deductible du seul PRODUCT_TAX_CODE Amazon) -> hors
    safe-list, meme si FR est eligible pour FOOD."""
    assert m._is_tedb_eligible("FR", "MEDICINES") is False


def test_is_tedb_eligible_false_when_flag_disabled():
    """Un flag explicitement désactivé force le repli statique."""
    with patch.object(m, "get_secret", return_value="false"):
        assert m._is_tedb_eligible("FR", "STANDARD") is False


# ---------------------------------------------------------------------
# get_vat_rate() de bout en bout : le cas ambigu ES ne doit JAMAIS renvoyer
# un taux errone, meme integre au flux complet (cache L1/L2/TEDB/statique)
# ---------------------------------------------------------------------

def test_get_vat_rate_es_ambiguous_uses_static_matching_candidate(tedb_enabled_no_db, caplog):
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
    """PT est hors safe-list FOOD (ambigu) : aucun appel reseau ne doit
    meme etre tente (coherent avec le principe scale-to-zero : pas d'appel
    sortant superflu pour un (pays, categorie) qu'on sait ne jamais
    exploiter en dynamique)."""
    with patch.object(m, "_request_tedb") as mocked_request:
        m.get_vat_rate("PT", "FOOD", date(2025, 7, 1))
    mocked_request.assert_not_called()


def test_get_vat_rate_food_safe_country_uses_tedb(tedb_enabled_no_db, caplog):
    """A l'inverse, FR est dans la safe-list FOOD (2026-09-16) : le taux
    dynamique doit etre utilise, coherent avec la valeur reelle du dump
    (5.5%, un seul taux FOODSTUFFS sur toute la fixture FR)."""
    root = _load_fixture("FR_standard_2025-07-01.xml")
    fake_raw_xml = (FIXTURES_DIR / "FR_standard_2025-07-01.xml").read_bytes()

    with patch.object(m, "_request_tedb", return_value=(root, fake_raw_xml)):
        with caplog.at_level(logging.DEBUG, logger="tva_intracom.vat_rates_db"):
            rate = m.get_vat_rate("FR", "FOOD", date(2025, 7, 1))

    assert rate == Decimal("5.5")
    assert any("source=TEDB_FETCH" in rec.message for rec in caplog.records)


# ---------------------------------------------------------------------
# Regression 2026-09-13 (2) : pas de pollution de logs par les categories
# REDUCED non utilisees, et cache journalier coté TEDB
# ---------------------------------------------------------------------

def test_parse_response_extracts_only_eligible_reduced_categories():
    """Depuis le 2026-09-16 (fin de la restriction STANDARD-only) : les
    categories REDUCED mappees ET eligibles pour ce pays (safe-list) sont
    extraites (FOOD, MEDICAL_EQUIPMENT, PERIODICALS, SOLAR_PANELS,
    AGRICULTURAL_PRODUCTION pour FR), mais PAS celles mappees hors
    safe-list pour ce pays (MEDICINES/PHARMACEUTICAL_PRODUCTS, FR est
    ambigu) ni celles non mappees du tout (MEDICAL_CARE, LOAN_LIBRARIES,
    SUPPLY_WATER, etc. — la majorite du XML reel)."""
    root = _load_fixture("FR_standard_2025-07-01.xml")
    result = m._parse_tedb_response(root, country="FR", target_date=date(2025, 7, 1))
    assert set(result.keys()) == {
        "STANDARD", "FOOD", "MEDICAL_EQUIPMENT", "PERIODICALS",
        "SOLAR_PANELS", "AGRICULTURAL_PRODUCTION",
    }
    assert "MEDICINES" not in result  # FR ambigu pour cette categorie
    assert result["FOOD"] == Decimal("5.5")
    assert result["SOLAR_PANELS"] == Decimal("5.5")
    assert result["PERIODICALS"] == Decimal("2.1")
    assert result["AGRICULTURAL_PRODUCTION"] == Decimal("10.0")


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


def test_get_vat_rate_different_days_fetch_independently(tedb_enabled_no_db):
    """Les changements de taux en cours de mois restent distinguables :
    chaque date distincte doit être interrogée et mise en cache séparément."""
    root = _load_fixture("FR_standard_2025-07-01.xml")
    fake_raw_xml = (FIXTURES_DIR / "FR_standard_2025-07-01.xml").read_bytes()

    with patch.object(m, "_request_tedb", return_value=(root, fake_raw_xml)) as mocked_request:
        rate_day1 = m.get_vat_rate("FR", "STANDARD", date(2025, 7, 1))
        rate_day15 = m.get_vat_rate("FR", "STANDARD", date(2025, 7, 15))
        rate_day31 = m.get_vat_rate("FR", "STANDARD", date(2025, 7, 31))

    assert rate_day1 == rate_day15 == rate_day31 == Decimal("20.0")
    m.get_vat_rate("FR", "STANDARD", date(2025, 7, 15))
    assert mocked_request.call_count == 3
    assert [call.args[1] for call in mocked_request.call_args_list] == [
        date(2025, 7, 1), date(2025, 7, 15), date(2025, 7, 31),
    ]


def test_get_vat_rate_different_dates_trigger_separate_tedb_calls(tedb_enabled_no_db):
    """Deux dates différentes doivent être interrogées séparément."""
    root = _load_fixture("FR_standard_2025-07-01.xml")
    fake_raw_xml = (FIXTURES_DIR / "FR_standard_2025-07-01.xml").read_bytes()

    with patch.object(m, "_request_tedb", return_value=(root, fake_raw_xml)) as mocked_request:
        m.get_vat_rate("FR", "STANDARD", date(2025, 7, 15))
        m.get_vat_rate("FR", "STANDARD", date(2025, 8, 3))

    assert mocked_request.call_count == 2


def test_vat_rate_public_api_unaffected_when_tedb_disabled():
    """Sans le flag active (comportement par defaut / production actuelle),
    vat_rate() se comporte a l'identique d'avant l'introduction du module."""
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

        # Deuxième appel à la même date : doit retenter TEDB (pas bloqué par
        # un cache L1 pollue par le repli precedent) et reussir cette fois.
        rate2 = m.get_vat_rate("FR", "STANDARD", date(2025, 7, 1))
        assert rate2 == Decimal("20.0")
        assert call_count["n"] == 2  # bien deux tentatives reseau distinctes


def test_resolved_es_candidate_is_cached_in_l1(tedb_enabled_no_db, caplog):
    """Après résolution de l'ambiguïté ES, la réponse est cachée à la date
    demandée et les appels identiques ne repartent pas sur le réseau."""
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
    ne doit PAS retenter le reseau pour la même date (protection déjà
    existante, ne doit pas régresser)."""
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


# ---------------------------------------------------------------------------
# Audit 2026-10-04 : un jalon L2 ne doit pas être hérité au-delà d'un
# changement de taux connu (FI 24 % -> 25,5 % au 2024-09-01)
# ---------------------------------------------------------------------------
def _seed_history(v, country, rate_type, entries):
    v._country_history_cache.clear()
    v._country_history_dates_cache.clear()
    v._country_history_loaded.clear()
    v._vat_memory_cache.clear()
    v._country_history_cache[(country, rate_type)] = list(entries)
    v._country_history_dates_cache[(country, rate_type)] = [d for d, _ in entries]
    v._country_history_loaded.add((country, rate_type))


def test_db_get_rate_inherits_milestone_when_no_known_change():
    from datetime import date
    from decimal import Decimal
    from tva_intracom import vat_rates_db as v
    _seed_history(v, "FI", "STANDARD", [(date(2024, 9, 5), Decimal("25.5"))])
    # aucun changement FI après 2024-09-01 : héritage valide
    assert v._db_get_rate("FI", "STANDARD", date(2025, 3, 1)) == Decimal("25.5")
    assert v._db_get_rate("FI", "STANDARD", date(2024, 9, 5)) == Decimal("25.5")


def test_db_get_rate_does_not_inherit_across_known_change():
    from datetime import date
    from decimal import Decimal
    from tva_intracom import vat_rates_db as v
    _seed_history(v, "FI", "STANDARD", [(date(2024, 8, 10), Decimal("24"))])
    # jalon avant le changement du 2024-09-01 : dates avant -> héritage, après -> None (TEDB tranche)
    assert v._db_get_rate("FI", "STANDARD", date(2024, 8, 31)) == Decimal("24")
    assert v._db_get_rate("FI", "STANDARD", date(2024, 9, 1)) is None
    assert v._db_get_rate("FI", "STANDARD", date(2024, 10, 5)) is None


def test_prefetch_queries_tedb_after_known_change_despite_older_milestone():
    from datetime import date
    from decimal import Decimal
    from unittest.mock import patch
    from tva_intracom import vat_rates_db as v
    _seed_history(v, "FI", "STANDARD", [(date(2024, 8, 10), Decimal("24"))])
    v._failed_pairs.clear()
    fetched = []

    def fake_fetch(country, d):
        fetched.append((country, d))
        return ({"STANDARD": Decimal("25.5")}, b"<xml/>")

    with patch.object(v, "_dynamic_tedb_enabled", return_value=True), \
         patch.object(v, "_fetch_tedb_rates", side_effect=fake_fetch), \
         patch.object(v, "_db_upsert_batch"):
        v.prefetch_standard_rates([("FI", date(2024, 10, 5))])
        assert fetched == [("FI", date(2024, 10, 5))]
        assert v.get_vat_rate("FI", "STANDARD", date(2024, 10, 5)) == Decimal("25.5")
