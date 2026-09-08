"""Tests pour l'integration VIES (avec mock HTTP pour eviter les appels reels)."""

from __future__ import annotations

import json
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest

from tva_intracom import BuyerType, Sale, Scenario, compute_all_with_vies
from tva_intracom.vies_engine import (
    ViesResult, _clean_vat_number, check_vat, check_vat_raw, _is_downgrade,
)


def test_clean_vat_number_standard():
    code, number = _clean_vat_number("DE123456789")
    assert code == "DE"
    assert number == "123456789"


def test_clean_vat_number_with_spaces():
    code, number = _clean_vat_number("FR 12 345 678 901")
    assert code == "FR"
    assert number == "12345678901"


def test_clean_vat_number_lowercase():
    code, number = _clean_vat_number("de123456789")
    assert code == "DE"
    assert number == "123456789"


def test_clean_vat_number_too_short():
    with pytest.raises(ValueError, match="trop court"):
        _clean_vat_number("D1")


def _mock_urlopen(valid: bool, name: str = "Firma GmbH"):
    """Cree un mock pour urllib.request.urlopen retournant un resultat VIES."""
    response_data = json.dumps({
        "valid": valid,
        "countryCode": "DE",
        "vatNumber": "123456789",
        "name": name if valid else "---",
        "address": "Berlin" if valid else "",
    }).encode("utf-8")
    mock_resp = MagicMock()
    mock_resp.read.return_value = response_data
    mock_resp.__enter__ = MagicMock(return_value=mock_resp)
    mock_resp.__exit__ = MagicMock(return_value=False)
    return mock_resp


@patch("tva_intracom.vies_engine.urllib.request.urlopen")
def test_check_vat_valid(mock_urlopen_func):
    mock_urlopen_func.return_value = _mock_urlopen(valid=True)
    result = check_vat("DE", "123456789")
    assert result.valid is True
    assert result.name == "Firma GmbH"


@patch("tva_intracom.vies_engine.urllib.request.urlopen")
def test_check_vat_invalid(mock_urlopen_func):
    mock_urlopen_func.return_value = _mock_urlopen(valid=False)
    result = check_vat("DE", "000000000")
    assert result.valid is False


@patch("tva_intracom.vies_engine.urllib.request.urlopen")
def test_check_vat_network_error(mock_urlopen_func):
    import urllib.error
    mock_urlopen_func.side_effect = urllib.error.URLError("timeout")
    result = check_vat("DE", "123456789")
    assert result.valid is False
    assert "indisponible" in result.error.lower() or "timeout" in result.error.lower()


@patch("tva_intracom.vies_engine.urllib.request.urlopen")
def test_check_vat_raw_valid(mock_urlopen_func):
    mock_urlopen_func.return_value = _mock_urlopen(valid=True)
    result = check_vat_raw("test", "DE123456789")
    assert result.valid is True


def test_is_downgrade_detects_valid_to_empty():
    """Numero precedemment VALIDE qui revient vide sans erreur -> downgrade
    suspect (potentielle panne VIES, voir incident du 31/07/2026)."""
    previous = ViesResult(valid=True, country_code="DE", vat_number="123456789", name="Firma GmbH")
    new_empty = ViesResult(valid=False, country_code="DE", vat_number="123456789")
    assert _is_downgrade(previous, new_empty) is True


def test_is_downgrade_false_when_new_result_has_error():
    """Une vraie erreur (transitoire) n'est pas un downgrade silencieux :
    elle est deja geree par _is_unreliable / le mecanisme de retry."""
    previous = ViesResult(valid=True, country_code="DE", vat_number="123456789", name="Firma GmbH")
    new_error = ViesResult(valid=False, country_code="DE", vat_number="123456789", error="timeout")
    assert _is_downgrade(previous, new_error) is False


def test_is_downgrade_false_when_previous_was_invalid():
    """Un numero deja invalide qui reste vide n'est pas un downgrade."""
    previous = ViesResult(valid=False, country_code="DE", vat_number="123456789")
    new_empty = ViesResult(valid=False, country_code="DE", vat_number="123456789")
    assert _is_downgrade(previous, new_empty) is False


@patch("tva_intracom.vies_engine.check_vat_raw")
def test_compute_all_with_vies_stale_fallback_not_treated_as_valid(mock_check):
    """BUGFIX (2026-09-08) : un ViesResult stale_fallback=True (repli suite a
    un downgrade detecte cote vies_engine, TTL expire + reponse vide) NE DOIT
    PLUS declencher l'autoliquidation B2B, meme si son champ `valid` (dernier
    statut automatique connu) vaut True. Il doit etre traite comme un
    inconclusif : pas de reclassification en B2C, mais TVA au depart (pas
    d'OSS), et remonter dans stale_fallback_count / inconclusive_vats pour
    apparaitre dans la liste de classification manuelle."""
    mock_check.return_value = ViesResult(
        valid=True, country_code="DE", vat_number="123456789",
        name="Firma GmbH", checked_at="2026-08-20T10:00:00+00:00",
        stale_fallback=True,
    )
    sales = [
        Sale(
            sale_id="T3",
            amount_ht=Decimal("200"),
            buyer_type=BuyerType.B2B,
            stock_country="FR",
            buyer_country="DE",
            buyer_vat_number="DE123456789",
            buyer_vat_valid=True,
        ),
    ]
    results, _refund_results, vies_summary, _ = compute_all_with_vies(sales, scope_id="test")
    assert len(results) == 1
    r = results[0]
    # Pas d'autoliquidation tant que non reconfirme / non classifie manuellement.
    assert r.scenario != Scenario.B2B_REVERSE_CHARGE
    assert vies_summary.stale_fallback_count == 1
    assert "DE123456789" in vies_summary.inconclusive_vats
    _detail = next(d for d in vies_summary.inconclusive_vat_details if d["vat"] == "DE123456789")
    assert _detail["reason"] == "stale_fallback"
    assert _detail["last_auto_status"] is True
    assert _detail["last_checked_at"] == "2026-08-20T10:00:00+00:00"


@patch("tva_intracom.vies_engine.check_vat_raw")
def test_compute_all_with_vies_reclassifies_invalid(mock_check):
    """B2B avec numero invalide est reclassifie en B2C -> TVA facturee."""
    mock_check.return_value = ViesResult(
        valid=False, country_code="DE", vat_number="000000000",
        error="numero invalide"
    )
    sales = [
        Sale(
            sale_id="T1",
            amount_ht=Decimal("200"),
            buyer_type=BuyerType.B2B,
            stock_country="FR",
            buyer_country="DE",
            buyer_vat_number="DE000000000",
            # BUGFIX (2026-08-25) : le nouveau filtre d'entrée de la boucle
            # VIES (engine.py, ~L1259) ignore désormais dès le départ tout
            # Sale dont buyer_vat_valid n'est pas déjà True (pré-filtre
            # "ressemble à un vrai n° TVA intracom", positionné par
            # classify.py côté import réel) — sans ce flag, ce Sale de test
            # ne serait jamais soumis à la vérification VIES mockée
            # ci-dessus, et vies_summary resterait vide.
            buyer_vat_valid=True,
        ),
    ]
    results, _refund_results, vies_summary, _ = compute_all_with_vies(sales, scope_id="test")
    assert len(results) == 1
    r = results[0]
    # Reclassifie en B2C -> OSS (pas reverse charge).
    assert r.scenario == Scenario.OSS_B2C
    assert r.vat_amount == Decimal("38.00")  # 200 * 19%
    # Verification du summary fraude.
    assert vies_summary.total_checked == 1
    assert vies_summary.total_invalid == 1
    assert vies_summary.fraud_avoided_amount == Decimal("38.00")
    assert len(vies_summary.reclassifications) == 1
    assert vies_summary.reclassifications[0].buyer_vat_number == "DE000000000"


@patch("tva_intracom.vies_engine.check_vat_raw")
def test_compute_all_with_vies_valid_number(mock_check):
    """B2B avec numero valide -> autoliquidation."""
    mock_check.return_value = ViesResult(
        valid=True, country_code="DE", vat_number="123456789",
        name="Firma GmbH"
    )
    sales = [
        Sale(
            sale_id="T2",
            amount_ht=Decimal("200"),
            buyer_type=BuyerType.B2B,
            stock_country="FR",
            buyer_country="DE",
            buyer_vat_number="DE123456789",
            # BUGFIX (2026-08-25) : voir commentaire identique dans
            # test_compute_all_with_vies_reclassifies_invalid ci-dessus.
            buyer_vat_valid=True,
        ),
    ]
    results, _refund_results, vies_summary, _ = compute_all_with_vies(sales, scope_id="test")
    assert len(results) == 1
    r = results[0]
    assert r.scenario == Scenario.B2B_REVERSE_CHARGE
    assert r.vat_amount == Decimal("0.00")
    # Pas de fraude quand le numero est valide.
    assert vies_summary.total_checked == 1
    assert vies_summary.total_valid == 1
    assert vies_summary.total_invalid == 0
    assert vies_summary.fraud_avoided_amount == Decimal("0.00")


@patch("tva_intracom.vies_engine.check_vat_raw")
def test_compute_all_with_vies_refund_reclassified_like_sale(mock_check):
    """Un avoir dont le n° TVA est invalide selon VIES doit etre reclassifie
    B2C/OSS comme la vente qu'il annule (et non rester en Reverse Charge),
    sans dupliquer d'entree dans vies_summary.reclassifications (deja
    renseignee via la vente d'origine) — voir engine.py::_effective_sale_with_vies.
    """
    mock_check.return_value = ViesResult(
        valid=False, country_code="DE", vat_number="000000000",
        error="numero invalide"
    )
    sale = Sale(
        sale_id="T3",
        amount_ht=Decimal("200"),
        buyer_type=BuyerType.B2B,
        stock_country="FR",
        buyer_country="DE",
        buyer_vat_number="DE000000000",
        buyer_vat_valid=True,
    )
    refund = Sale(
        sale_id="T3",
        amount_ht=Decimal("-200"),
        buyer_type=BuyerType.B2B,
        stock_country="FR",
        buyer_country="DE",
        buyer_vat_number="DE000000000",
        buyer_vat_valid=True,
    )
    results, refund_results, vies_summary, _ = compute_all_with_vies(
        [sale], scope_id="test", refunds=[refund],
    )

    assert len(results) == 1
    assert len(refund_results) == 1

    # La vente comme l'avoir doivent etre reclassifies B2C/OSS (pas de
    # Reverse Charge cote avoir avec un n° invalide) pour rester coherents
    # entre declaration OSS et CA3.
    assert results[0].scenario == Scenario.OSS_B2C
    assert refund_results[0].scenario == Scenario.OSS_B2C
    assert results[0].vat_amount == Decimal("38.00")  # 200 * 19%
    assert refund_results[0].vat_amount == Decimal("-38.00")

    # Une seule entree dans le tableau VIES affiche (celle de la vente),
    # l'avoir ne doit pas la dupliquer.
    assert len(vies_summary.reclassifications) == 1
    assert vies_summary.reclassifications[0].sale_id == "T3"
    # Regression point 1 : vat_avoided doit refleter le vrai montant de TVA
    # (et non rester a 0.00 a cause d'un mismatch str/Decimal dans le lookup).
    assert vies_summary.reclassifications[0].vat_avoided == Decimal("38.00")


@patch("tva_intracom.vies_engine.check_vat_raw")
def test_reclassification_post_processing_fields(mock_check):
    """Vérifie explicitement les 5 champs mis à jour en post-traitement
    dans compute_all_with_vies (engine.py, ~L1498) : vat_avoided, vat_delta,
    is_domestic_reverse_charge, taxed_at_departure, scenario. Depuis le
    2026-09-06, cette mise à jour se fait par mutation en place de l'objet
    ViesReclassification existant (au lieu d'une reconstruction Pydantic) —
    ce test garantit que les 5 champs sont toujours correctement renseignés
    après la bascule.
    """
    mock_check.return_value = ViesResult(
        valid=False, country_code="DE", vat_number="000000000",
        error="numero invalide"
    )
    sale = Sale(
        sale_id="T4",
        amount_ht=Decimal("200"),
        buyer_type=BuyerType.B2B,
        stock_country="FR",
        buyer_country="DE",
        buyer_vat_number="DE000000000",
        buyer_vat_valid=True,
    )
    results, _refund_results, vies_summary, _ = compute_all_with_vies([sale], scope_id="test")
    assert len(vies_summary.reclassifications) == 1
    reclass = vies_summary.reclassifications[0]
    r = results[0]

    assert reclass.vat_avoided == Decimal("38.00")
    assert reclass.vat_delta == Decimal("38.00")
    assert reclass.is_domestic_reverse_charge is False
    assert reclass.taxed_at_departure is False  # cross-border, taxe a destination (OSS)
    assert reclass.scenario == r.scenario.value

    # Les champs NON concernés par le post-traitement doivent rester ceux
    # de la construction initiale (pas écrasés par la mutation).
    assert reclass.sale_id == "T4"
    assert reclass.buyer_vat_number == "DE000000000"
    assert reclass.buyer_country == "DE"
    assert reclass.amount_ht == Decimal("200")
    assert reclass.reason  # non vide
    assert reclass.stock_country == "FR"
    assert reclass.is_national_tax_id is False


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
