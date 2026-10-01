"""Tests de caractérisation de ``engine.compute_all_with_vies`` (filet avant découpage).

On fige, sur le code D'ORIGINE, la sortie complète (résultats, avoirs,
ViesValidationSummary, OssThresholdSummary) ET les appels aux dépendances
(validation VIES parallèle/séquentielle, overrides manuels, préchargement des
taux, callbacks de progression) pour un jeu de scénarios couvrant chaque
branche de la fonction.

Déterministe, sans réseau (sockets bloquées par fixture), taux statiques.
Référence : tests/golden/compute_all_with_vies.json. Régénération volontaire
(changement de comportement ASSUMÉ uniquement) :
    UPDATE_COMPUTE_ALL_VIES_GOLDEN=1 pytest tests/test_compute_all_with_vies_characterization.py
"""
from __future__ import annotations

import dataclasses
import enum
import json
import os
import socket
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

import pytest

from tva_intracom import engine, rates
from tva_intracom.models import BuyerType, Sale
from tva_intracom.vies_engine import ViesResult

_GOLDEN = Path(__file__).parent / "golden" / "compute_all_with_vies.json"


def _s(sid, amount="100.00", *, bt=BuyerType.B2C, stock="FR", buyer="DE", vat="", valid=False, date="2026-02-10",
       display="", nif="", seller="FR", cat="STANDARD", cur="EUR", asin="", order_date=""):
    return Sale(sale_id=sid, amount_ht=Decimal(amount), buyer_type=bt, stock_country=stock, buyer_country=buyer,
                seller_country=seller, buyer_vat_number=vat, buyer_vat_valid=valid, transaction_date=date,
                display_id=display, national_tax_id=nif, product_category=cat, original_currency=cur,
                asin=asin, order_date=order_date)


def _b2b(sid, vat, **kw):
    kw.setdefault("valid", True)
    return _s(sid, bt=BuyerType.B2B, vat=vat, **kw)


V_OK = ViesResult(valid=True, country_code="DE", vat_number="123456789", name="Firma GmbH")
V_KO = ViesResult(valid=False, country_code="DE", vat_number="000000000")
V_TRANSIENT = ViesResult(valid=False, country_code="DE", vat_number="111111111", error="timeout")
V_STALE = ViesResult(valid=True, country_code="DE", vat_number="222222222", checked_at="2026-01-02T00:00:00+00:00",
                     stale_fallback=True)
V_STALE_KO = ViesResult(valid=False, country_code="DE", vat_number="333333333",
                        checked_at="2026-01-03T00:00:00+00:00", stale_fallback=True)

# nom -> dict(sales, refunds, vies (dict vat->ViesResult), overrides, flags...)
SC: dict[str, dict] = {}
SC["empty"] = dict(sales=[])
SC["b2c_only_no_vies"] = dict(sales=[_s("A1"), _s("A2", buyer="ES"), _s("A3", buyer="FR")])
SC["valid_number"] = dict(sales=[_b2b("B1", "DE123456789")], vies={"DE123456789": V_OK})
SC["invalid_number"] = dict(sales=[_b2b("B2", "DE000000000", display="D-B2")], vies={"DE000000000": V_KO})
SC["transient_error"] = dict(sales=[_b2b("B3", "DE111111111")], vies={"DE111111111": V_TRANSIENT})
SC["stale_fallback_valid"] = dict(sales=[_b2b("B4", "DE222222222")], vies={"DE222222222": V_STALE})
SC["stale_fallback_invalid"] = dict(sales=[_b2b("B5", "DE333333333")], vies={"DE333333333": V_STALE_KO})
SC["vies_result_missing"] = dict(sales=[_b2b("B6", "DE444444444")], vies={})
SC["mixed_vies_batch"] = dict(sales=[
    _b2b("M1", "DE123456789", date="2026-01-05"), _b2b("M2", "DE000000000", date="2026-01-04", display="D-M2"),
    _b2b("M3", "DE111111111", date="2026-01-03"), _b2b("M4", "DE222222222", date="2026-01-02"),
    _b2b("M5", "DE123456789", date="2026-01-06", display="D-M5"), _s("M6", date="2026-01-01"),
], vies={"DE123456789": V_OK, "DE000000000": V_KO, "DE111111111": V_TRANSIENT, "DE222222222": V_STALE})
SC["b2b_flag_false_skipped"] = dict(sales=[_b2b("F1", "DE000000000", valid=False)], vies={"DE000000000": V_KO})
SC["vat_number_unnormalizable"] = dict(sales=[_b2b("F2", "!!", buyer="DE"), _b2b("F3", "-", buyer="DE"), _b2b("F3b", "  ", buyer="DE")], vies={})
SC["domestic_b2b_invalid_fr"] = dict(sales=[_b2b("D1", "FR12345678901", stock="FR", buyer="FR")],
                                     vies={"FR12345678901": V_KO})
SC["domestic_reverse_charge_es"] = dict(sales=[_b2b("D2", "ESB12345678", stock="ES", buyer="ES", seller="FR")],
                                        vies={"ESB12345678": V_KO})
SC["domestic_b2b_valid_es"] = dict(sales=[_b2b("D3", "ESB12345678", stock="ES", buyer="ES", seller="FR")],
                                   vies={"ESB12345678": V_OK})
SC["national_id_cross_border"] = dict(sales=[
    _s("N1", bt=BuyerType.B2B, nif="X1234567L", buyer="ES", date="2026-01-02"),
    _s("N2", bt=BuyerType.B2B, nif="X1234567L", buyer="ES", date="2026-01-03"),
    _s("N3", bt=BuyerType.B2B, nif="Y7654321K", buyer="ES", date="2026-01-04"),
    _s("N4", bt=BuyerType.B2B, nif="Z0000000A", buyer="ES", stock="ES", date="2026-01-05")])
SC["national_id_refund"] = dict(
    sales=[_s("N5", bt=BuyerType.B2B, nif="X1234567L", buyer="ES")],
    refunds=[_s("N5", "-100.00", bt=BuyerType.B2B, nif="X1234567L", buyer="ES", date="2026-02-20")])
SC["refund_invalid_not_duplicated"] = dict(
    sales=[_b2b("R1", "DE000000000", date="2026-01-10")],
    refunds=[_b2b("R1", "DE000000000", amount="-100.00", date="2026-01-20")], vies={"DE000000000": V_KO})
SC["refund_only_vat_counted"] = dict(
    sales=[_s("R2")], refunds=[_b2b("R3", "DE123456789", amount="-50.00", date="2026-02-01")],
    vies={"DE123456789": V_OK})
SC["refund_valid_reverse_charge"] = dict(
    sales=[_b2b("R4", "DE123456789", date="2026-01-10")],
    refunds=[_b2b("R4", "DE123456789", amount="-100.00", date="2026-01-20")], vies={"DE123456789": V_OK})
SC["refund_oss_deduction"] = dict(
    sales=[_s("R5", "9000.00", date="2026-01-10"), _s("R6", "3000.00", date="2026-03-10")],
    refunds=[_s("R5", "-2500.00", date="2026-04-10")])
SC["override_applied_vies_missing"] = dict(
    sales=[_b2b("O1", "DE444444444")], vies={}, overrides={"DE444444444": True})
SC["override_invalid_applied_on_transient"] = dict(
    sales=[_b2b("O2", "DE111111111")], vies={"DE111111111": V_TRANSIENT}, overrides={"DE111111111": False})
SC["override_applied_on_stale"] = dict(
    sales=[_b2b("O3", "DE222222222")], vies={"DE222222222": V_STALE}, overrides={"DE222222222": True})
SC["override_cleaned_when_vies_conclusive"] = dict(
    sales=[_b2b("O4", "DE123456789")], vies={"DE123456789": V_OK}, overrides={"DE123456789": False})
SC["override_cleanup_delete_raises"] = dict(
    sales=[_b2b("O5", "DE123456789")], vies={"DE123456789": V_OK}, overrides={"DE123456789": False},
    delete_raises=True)
SC["override_for_unseen_vat_ignored"] = dict(
    sales=[_b2b("O6", "DE123456789")], vies={"DE123456789": V_OK}, overrides={"DE999999999": True})
SC["overrides_loading_raises"] = dict(
    sales=[_b2b("O7", "DE111111111")], vies={"DE111111111": V_TRANSIENT}, overrides_raises=True)
SC["parallel_raises_sequential_ok"] = dict(
    sales=[_b2b("P1", "DE123456789")], vies={"DE123456789": V_OK}, parallel_raises=True)
SC["parallel_and_sequential_raise"] = dict(
    sales=[_b2b("P2", "DE123456789")], vies={"DE123456789": V_OK}, parallel_raises=True, seq_raises=True)
SC["progress_callbacks_passed"] = dict(
    sales=[_b2b("C1", "DE123456789"), _s("C2")], vies={"DE123456789": V_OK}, callbacks=True)
SC["oss_eligibility_edges"] = dict(sales=[
    _s("G1", "100.00", stock="US", seller="US", buyer="DE", date="2026-01-02"),
    _s("G2", "100.00", stock="DE", seller="FR", buyer="ES", date="2026-01-03"),
    _s("G3", "100.00", stock="DE", seller="FR", buyer="FR", date="2026-01-04"),
    _s("G4", "100.00", stock="FR", seller="FR", buyer="DE", date="2026-01-05"),
    _s("G5", "100.00", stock="FR", seller="FR", buyer="ES", date="2026-01-06", bt=BuyerType.B2B, vat="ES000", valid=False),
    _s("G6", "100.00", stock="FR", seller="FR", buyer="DE", date="2026-01-07", bt=BuyerType.B2B, vat="DE111", valid=True),
    _s("G7", "100.00", stock="FR", seller="FR", buyer="MC", date="2026-01-08")], apply_fr_under_threshold=True)
SC["oss_exact_threshold_boundary"] = dict(sales=[
    _s("H1", "6000.00", buyer="DE", date="2026-01-02"), _s("H2", "4000.00", buyer="ES", date="2026-01-03"),
    _s("H3", "0.01", buyer="IT", date="2026-01-04"), _s("H4", "50.00", buyer="PL", date="2026-01-05")],
    apply_fr_under_threshold=True)
SC["oss_exact_threshold_boundary_no_fr_flag"] = dict(sales=[
    _s("H5", "10000.00", buyer="DE", date="2026-01-02"), _s("H6", "0.01", buyer="ES", date="2026-01-03")])
_CROSS_SALES = [
    _s("J0", "500.00", buyer="DE", date="2026-01-01"),
    _s("J1", "9000.00", buyer="DE", date="2026-01-02"), _s("J2", "3000.00", buyer="ES", date="2026-01-03"),
    _s("J3", "100.00", buyer="IT", date="2026-01-05"), _s("J4", "40.00", buyer="PL", date="2026-01-06")]
_CROSS_REFUNDS = [_s("J0", "-500.00", buyer="DE", date="2026-01-01"),
                  _s("J1", "-9000.00", buyer="DE", date="2026-01-04")]
SC["oss_cross_then_refund_below_fr_flag"] = dict(sales=_CROSS_SALES, refunds=_CROSS_REFUNDS,
                                                 apply_fr_under_threshold=True)
SC["oss_cross_then_refund_below_en"] = dict(sales=_CROSS_SALES, refunds=_CROSS_REFUNDS,
                                            apply_fr_under_threshold=True, lang="en")
SC["oss_cross_then_refund_below_default"] = dict(sales=_CROSS_SALES, refunds=_CROSS_REFUNDS)
SC["oss_prev_year_flag_with_refund_fr_flag"] = dict(
    sales=[_s("K1", "100.00", buyer="DE", date="2026-01-02"), _s("K2", "30.00", buyer="ES", date="2026-02-02")],
    refunds=[_s("K1", "-100.00", buyer="DE", date="2026-03-02")],
    oss_threshold_exceeded_prev_year=True, apply_fr_under_threshold=True)
SC["empty_transaction_date"] = dict(sales=[_s("Q1", "100.00", date=""), _s("Q2", "50.00", buyer="ES", date="2026-01-05"),
                                           _s("Q3", "70.00", buyer="IT", date="")])
SC["refund_order_date_malformed"] = dict(
    sales=[_s("W1", "100.00", buyer="EE", date="2025-06-01")],
    refunds=[_s("W1", "-100.00", buyer="EE", date="2026-03-01", order_date="garbage")])
SC["refund_order_date_cross_year"] = dict(
    sales=[_s("W2", "100.00", buyer="EE", date="2025-06-01")],
    refunds=[_s("W2", "-100.00", buyer="EE", date="2026-03-01", order_date="2025-06-01")])
SC["oss_threshold_exceeded_same_year"] = dict(sales=[
    _s("T1", "6000.00", date="2026-01-10"), _s("T2", "6000.00", date="2026-02-10", buyer="ES"),
    _s("T3", "500.00", date="2026-03-10", buyer="IT")])
SC["oss_threshold_prev_year_flag"] = dict(
    sales=[_s("T4", "100.00", date="2026-01-10"), _s("T5", "50.00", date="2026-01-11", buyer="PL")],
    oss_threshold_exceeded_prev_year=True)
SC["apply_fr_under_threshold"] = dict(
    sales=[_s("T6", "100.00", date="2026-01-10"), _s("T7", "9900.00", date="2026-02-10", buyer="ES")],
    apply_fr_under_threshold=True)
SC["multi_year_reset"] = dict(sales=[
    _s("Y1", "9000.00", date="2025-11-10"), _s("Y2", "2000.00", date="2025-12-10"),
    _s("Y3", "100.00", date="2026-01-10"), _s("Y4", "", date="")] if False else [
    _s("Y1", "9000.00", date="2025-11-10"), _s("Y2", "2000.00", date="2025-12-10"),
    _s("Y3", "100.00", date="2026-01-10"), _s("Y5", "100.00", date="not-a-date")])
SC["ioss_own_number_active"] = dict(
    sales=[_s("I1", "100.00", stock="US", date="2026-03-10")], ioss_own_number_active=True)
SC["non_eu_and_marketplace_en"] = dict(
    sales=[_s("E1", "300.00", stock="US", buyer="DE"), _s("E2", "40.00", buyer="US"),
           _s("E3", "50.00", buyer="MC"), _s("E4", "75.00", seller="US", stock="DE")],
    lang="en", marketplace_name="eBay")
SC["category_and_asin_propagation"] = dict(
    sales=[_b2b("K1", "DE123456789", cat="BOOKS", asin="B00ASIN"), _b2b("K2", "DE000000000", cat="BOOKS", asin="B00XYZ")],
    vies={"DE123456789": V_OK, "DE000000000": V_KO})
SC["unsorted_input_and_duplicates_ids"] = dict(sales=[
    _b2b("U1", "DE123456789", date="2026-03-05"), _b2b("U1", "DE123456789", amount="55.00", date="2026-01-05"),
    _b2b("U1", "DE000000000", amount="77.00", date="2026-02-05")],
    vies={"DE123456789": V_OK, "DE000000000": V_KO})
SC["check_vies_func_ignored"] = dict(sales=[_b2b("Z1", "DE123456789")], vies={"DE123456789": V_OK},
                                     check_vies_func=lambda *a, **k: 1 / 0)


def _ser(o):
    if isinstance(o, Decimal):
        return str(o)
    if isinstance(o, enum.Enum):
        return str(o)
    if isinstance(o, (set, frozenset)):
        return sorted((_ser(x) for x in o), key=lambda x: json.dumps(x, sort_keys=True))
    if isinstance(o, (list, tuple)):
        return [_ser(x) for x in o]
    if isinstance(o, dict):
        return {str(k): _ser(v) for k, v in o.items()}
    if dataclasses.is_dataclass(o) and not isinstance(o, type):
        return {f.name: _ser(getattr(o, f.name)) for f in dataclasses.fields(o)}
    return o


def _ser_result(r):
    s = r.sale
    return {"sale_id": s.sale_id, "display_id": s.display_id, "amount_ht": str(s.amount_ht),
            "buyer_type": str(s.buyer_type), "buyer_vat_valid": s.buyer_vat_valid,
            "buyer_vat_number": s.buyer_vat_number, "product_category": s.product_category, "asin": s.asin,
            "scenario": str(r.scenario), "vat_country": r.vat_country, "vat_rate": str(r.vat_rate),
            "vat_amount": str(r.vat_amount), "collector": str(r.collector), "channel": str(r.channel),
            "note": r.note}


@pytest.fixture(autouse=True)
def _offline(monkeypatch):
    monkeypatch.setattr(engine, "vat_rate", rates.vat_rate)

    def _no_net(*a, **k):
        raise AssertionError("accès réseau interdit dans ce test")
    monkeypatch.setattr(socket.socket, "connect", _no_net)
    yield


def _run(name: str) -> dict:
    sc = SC[name]
    calls: list = []
    vies = sc.get("vies", {})
    overrides = sc.get("overrides", {})

    def _parallel(scope, vats, progress_callback=None):
        calls.append(["parallel", scope, list(vats), progress_callback is not None])
        if progress_callback:
            progress_callback(len(vats), len(vats))
        if sc.get("parallel_raises"):
            raise RuntimeError("parallel down")
        return {v: vies[v] for v in vats if v in vies}

    def _sequential(scope, vats, progress_callback=None):
        calls.append(["sequential", scope, list(vats), progress_callback is not None])
        if sc.get("seq_raises"):
            raise RuntimeError("seq down")
        return {v: vies[v] for v in vats if v in vies}

    def _get_overrides(scope, include_expired=False):
        calls.append(["get_manual_overrides", scope, include_expired])
        if sc.get("overrides_raises"):
            raise RuntimeError("db down")
        return dict(overrides)

    def _delete_override(scope, vat):
        calls.append(["delete_manual_override", scope, vat])
        if sc.get("delete_raises"):
            raise RuntimeError("cannot delete")

    def _prefetch(pairs, progress_callback=None):
        calls.append(["prefetch_standard_rates", sorted(str(p) for p in pairs), progress_callback is not None])

    cb_log: dict[str, list] = {"vies": [], "oss": [], "rate": []}
    kw = {}
    if sc.get("callbacks"):
        kw = dict(vies_progress_callback=lambda d, t: cb_log["vies"].append((d, t)),
                  oss_progress_callback=lambda d, t: cb_log["oss"].append((d, t)),
                  vat_rate_progress_callback=lambda d, t: cb_log["rate"].append((d, t)))
    for k in ("lang", "marketplace_name", "apply_fr_under_threshold", "ioss_own_number_active",
              "oss_threshold_exceeded_prev_year", "check_vies_func"):
        if k in sc:
            kw[k] = sc[k]
    with patch("tva_intracom.vies_engine.validate_vat_numbers_parallel", side_effect=_parallel), \
            patch("tva_intracom.vies_engine.validate_vat_numbers", side_effect=_sequential), \
            patch("tva_intracom.vies_engine.get_manual_overrides", side_effect=_get_overrides), \
            patch("tva_intracom.vies_engine.delete_manual_override", side_effect=_delete_override), \
            patch("tva_intracom.vat_rates_db.prefetch_standard_rates", side_effect=_prefetch):
        results, refund_results, vies_summary, oss_summary = engine.compute_all_with_vies(
            sc["sales"], scope_id="scope-test", refunds=sc.get("refunds"), **kw)
    return {
        "results": [_ser_result(r) for r in results],
        "refund_results": [_ser_result(r) for r in refund_results],
        "vies_summary": _ser(vies_summary),
        "oss_summary": _ser(oss_summary),
        "calls": calls,
        "callbacks": cb_log,
    }


def _snapshot_all() -> dict:
    return json.loads(json.dumps({n: _run(n) for n in sorted(SC)}, sort_keys=True, default=repr))


def test_compute_all_with_vies_matches_golden():
    snap = _snapshot_all()
    if os.environ.get("UPDATE_COMPUTE_ALL_VIES_GOLDEN") == "1":
        _GOLDEN.write_text(json.dumps(snap, indent=1, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")
        pytest.skip("golden régénéré")
    assert _GOLDEN.exists(), "référence manquante (UPDATE_COMPUTE_ALL_VIES_GOLDEN=1 pour la créer)"
    exp = json.loads(_GOLDEN.read_text(encoding="utf-8"))
    assert set(snap) == set(exp)
    bad = [n for n in exp if snap[n] != exp[n]]
    assert not bad, f"scénarios divergents : {bad}"


def _many_sales(n):
    return [_s(f"L{i}", "10.00", buyer=("DE", "ES", "IT")[i % 3], date=f"2026-0{1 + i % 9}-1{i % 9}") for i in range(n)]


@pytest.mark.parametrize("n", [1, 499, 500, 501, 1000, 1001])
def test_run_oss_loop_progress_ticks(n):
    """Ticks de progression : tous les 500 éléments + le dernier ; un callback défaillant ne casse jamais le calcul."""
    items = sorted(_many_sales(n), key=engine._chronological_sort_key)
    ticks: list = []
    res_cb, ref_cb, sum_cb = engine._run_oss_loop(items, set(), "Amazon", False,
                                                  progress_callback=lambda d, t: ticks.append((d, t)))
    expected = [(i, n) for i in range(1, n + 1) if i % 500 == 0 or i == n]
    assert ticks == expected

    def _boom(d, t):
        raise RuntimeError("widget fermé")
    res_boom, ref_boom, sum_boom = engine._run_oss_loop(items, set(), "Amazon", False, progress_callback=_boom)
    assert [_ser_result(r) for r in res_boom] == [_ser_result(r) for r in res_cb]
    assert _ser(sum_boom) == _ser(sum_cb) and ref_boom == ref_cb == []


def test_run_oss_loop_year_revisited_in_unsorted_input():
    """Liste NON triée : une année qui réapparaît reprend son cumul (valeurs figées sur le code d'origine)."""
    items = [_s("A", "9000.00", buyer="DE", date="2025-03-01"), _s("B", "2000.00", buyer="ES", date="2026-01-01"),
             _s("C", "3000.00", buyer="IT", date="2025-04-01"), _s("D", "10.00", buyer="PL", date="2026-02-01"),
             _s("E", "5.00", buyer="DE", date="2025-05-01")]
    for flag, notes in (
        (False, ["Vente OSS vers DE", "Vente OSS vers ES", "Vente OSS vers IT", "Vente OSS vers PL", "Vente OSS vers DE"]),
        (True, ["Sous le seuil OSS (9,000.00/10,000.00€).", "Sous le seuil OSS (2,000.00/10,000.00€).",
                "FRANCHISSEMENT DU SEUIL OSS ! Vente vers", "Vente OSS vers PL", "Vente OSS vers DE"]),
    ):
        res, refunds, summ = engine._run_oss_loop(items, set(), "Amazon", flag)
        assert _ser(summ) == {"is_threshold_exceeded": True, "oss_ht_by_year": {"2025": "12005.00", "2026": "10010.01"},
                              "total_oss_ht": "12005.00"}
        assert [r.note[:len(n)] for r, n in zip(res, notes)] == notes and not refunds
