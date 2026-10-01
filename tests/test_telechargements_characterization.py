"""Tests de caractérisation de ui/tabs/telechargements.py::render_telechargements.

Fige le comportement OBSERVABLE actuel (arbre de widgets, appels aux
générateurs d'exports avec leurs arguments, boutons de téléchargement — libellé,
nom de fichier, mime, contenu —, clés de session finales) AVANT le découpage de
`render_telechargements`, puis prouve qu'il est strictement inchangé après.

Références : tests/golden/telechargements_*.json (générées sur le code
d'origine). Régénération volontaire UNIQUEMENT pour un changement de
comportement assumé : UPDATE_TELECHARGEMENTS_GOLDEN=1 pytest <ce fichier>.

Aucun réseau, aucun Postgres, aucun thread.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

_HERE = Path(__file__).parent
_SCRIPT = str(_HERE / "_telechargements_app_script.py")
_GOLDEN = _HERE / "golden"

_FULL = {"ioss": True, "b2b": True, "local": ["PL", "DE"], "refunds": True}
_ALL_GEN = [  # clic successif sur chaque bouton "Générer" (clés = _gen_btn_<artefact>)
    ("button", "_gen_btn_main_xlsx"), ("button", "_gen_btn_oss_xml_False"),
    ("button", "_gen_btn_oss_xlsx"), ("button", "_gen_btn_ioss_xlsx"),
    ("button", "_gen_btn_ca3_html"), ("button", "_gen_btn_ca3_edi_preparation_trimestriel"),
    ("button", "_gen_btn_b2b_xlsx"), ("button", "_gen_btn_local_csv_DE"),
    ("button", "_gen_btn_local_html_DE"), ("button", "_gen_btn_fec_bytes"),
    ("button", "_gen_btn_rates_evidence"),
]

# nom -> (scénario, interactions)
SCENARIOS: dict[str, tuple[dict, list[tuple]]] = {
    "locked_paywall_full": ({**_FULL, "can_export": False, "billing_ok": False, "detailed": True}, []),
    "locked_payment_pending": ({**_FULL, "can_export": False, "billing_ok": False, "sub_status": "incomplete"}, []),
    "locked_billing_ok_other_reason": ({**_FULL, "can_export": False, "billing_ok": True}, []),
    "locked_home_de": ({**_FULL, "can_export": False, "billing_ok": False, "home": "DE", "local": ["FR", "PL"]}, []),
    "unlocked_full_initial": (dict(_FULL), []),
    "unlocked_full_generate_all": (dict(_FULL, detailed=True), _ALL_GEN),
    "unlocked_full_fr_local_csv_generic_country": ({**_FULL, "local": ["PL", "DE", "ES", "HU"]}, [
        ("selectbox", "dl_country_select", "HU"), ("button", "_gen_btn_local_csv_HU")]),
    "unlocked_local_csv_box_country_de": (dict(_FULL), [
        ("selectbox", "dl_country_select", "DE"), ("button", "_gen_btn_local_csv_DE")]),
    "unlocked_local_html_second_country": (dict(_FULL), [
        ("selectbox", "dl_country_select", "DE"), ("button", "_gen_btn_local_html_DE")]),
    "unlocked_local_csv_home_country_branch": ({**_FULL, "local": ["FR"]}, [
        ("button", "_gen_btn_local_csv_FR")]),
    "simple_mode_minimal": ({"oss": False}, []),
    "detailed_mode_minimal": ({"oss": False, "detailed": True}, []),
    "simple_mode_oss_only": ({}, []),
    "home_de_unlocked": ({**_FULL, "home": "DE", "local": ["PL", "FR"]}, [
        ("button", "_gen_btn_home_html"), ("button", "_gen_btn_oss_xml_False")]),
    "home_de_generate_home_html": ({"home": "DE", "oss": False}, [("button", "_gen_btn_home_html")]),
    "home_fr_no_local_no_b2b_detailed": ({"detailed": True}, []),
    "ca3_edi_regime_mensuel_mismatch": ({}, [("selectbox", "ca3_edi_regime", "mensuel")]),
    "ca3_edi_regime_mensuel_ok": ({"period": "2026-03"}, [
        ("selectbox", "ca3_edi_regime", "mensuel"), ("button", "_gen_btn_ca3_edi_preparation_mensuel")]),
    "ca3_edi_regime_trimestriel_mismatch_monthly_period": ({"period": "2026-03"}, []),
    "oss_negative_matched_expander": ({"neg_matched": True, "neg_unmatched": True}, []),
    "oss_negative_matched_confirm_then_xml": ({"neg_matched": True}, [
        ("checkbox", "confirm_oss_corrections", True), ("button", "_gen_btn_oss_xml_True")]),
    "oss_negative_unmatched_only_xml_error": ({"neg_unmatched": True, "xml_error": "Solde négatif FR→DE"}, [
        ("button", "_gen_btn_oss_xml_False")]),
    "oss_xml_error_then_nothing": ({"xml_error": "boom"}, [("button", "_gen_btn_oss_xml_False")]),
    "fallback_warning_main_xlsx": ({"fallback": {"PLN": 3, "SEK": 1}}, [("button", "_gen_btn_main_xlsx")]),
    "fallback_warning_oss_xml": ({"fallback": {"PLN": 2}}, [("button", "_gen_btn_oss_xml_False")]),
    "fallback_warning_oss_xlsx": ({"fallback": {"CZK": 5}}, [("button", "_gen_btn_oss_xlsx")]),
    "fallback_warning_ioss_xlsx": ({"ioss": True, "fallback": {"HUF": 4}}, [("button", "_gen_btn_ioss_xlsx")]),
    "vies_unverified_and_period_range": ({"vies_unverified": 3, "range": ("2026-01-01", "2026-03-31")}, []),
    "no_vies_summary": ({"vies": False}, [("button", "_gen_btn_main_xlsx")]),
    "format3_grouped_warning": ({"amazon_format": 3, "format3_risk": True}, []),
    "format3_no_risk": ({"amazon_format": 3, "format3_risk": False}, []),
    "format3_detection_raises": ({"amazon_format": 3, "format3_raises": True}, []),
    "cache_key_change_purges_artifacts": ({"calc_key": "ck2", "preset_state": {
        "_dl_active_cache_key": "old", "_dl_artifact_main_xlsx": ("old", b"x"),
        "_oss_preview_old": ("a", []), "keep_me": 1}}, []),
    "cached_artifact_reused": ({}, [("button", "_gen_btn_main_xlsx"), ("button", "_gen_btn_fec_bytes")]),
    "missing_oss_tva_net_total": ({"oss_net_none": True}, []),
}

SCENARIOS.update({
    "oss_negative_mixed_suggestions": ({"neg_mixed": True}, []),
    "local_csv_generic_country_gr": ({"local": ["GR"]}, [("button", "_gen_btn_local_csv_GR")]),
    "local_csv_str_mapping_country_it": ({"local": ["IT"]}, [("button", "_gen_btn_local_csv_IT")]),
    "local_csv_empty_box_mapping": ({"local": ["PL"], "empty_box_mapping": True}, [
        ("button", "_gen_btn_local_csv_PL")]),
    "tmp_file_removal_fails": ({**_FULL}, [
        ("button", "_gen_btn_main_xlsx"), ("button", "_gen_btn_oss_xlsx"),
        ("button", "_gen_btn_ioss_xlsx"), ("button", "_gen_btn_b2b_xlsx")]),
})
_KEY = ("ck1", "ACME SAS", "111222333", "FR11111222333", (("DE", "DE999888777"),), "EUR", "2026-Q1", "scope1")
SCENARIOS.update({
    # clé de cache identique : l'artefact déjà en session est réutilisé (bouton de téléchargement direct)
    "cache_key_identical_reuses_artifact": ({"preset_state": {
        "_dl_active_cache_key": _KEY, "_dl_artifact_main_xlsx": (_KEY, b"cached-xlsx")}}, []),
    # clé différente uniquement par le périmètre VIES : purge (l'artefact caché ne doit pas être resservi)
    "cache_key_differs_only_by_vies_scope": ({"preset_state": {
        "_dl_active_cache_key": _KEY[:-1] + ("other-scope",),
        "_dl_artifact_main_xlsx": (_KEY[:-1] + ("other-scope",), b"stale-xlsx")}}, []),
})
SCENARIOS.update({
    # Correctif 2026-10-01 : l'alerte BCE doit survivre au st.rerun(scope="fragment") ET aux
    # interactions suivantes tant que l'artefact est en cache.
    "fallback_warning_persists_after_other_interaction": ({"fallback": {"PLN": 3}}, [
        ("button", "_gen_btn_main_xlsx"), ("selectbox", "ca3_edi_regime", "mensuel")]),
    "fallback_warning_two_artifacts": ({"fallback": {"PLN": 3}, **_FULL}, [
        ("button", "_gen_btn_main_xlsx"), ("button", "_gen_btn_oss_xml_False")]),
    "no_fallback_no_warning_after_generate": ({}, [
        ("button", "_gen_btn_main_xlsx"), ("selectbox", "ca3_edi_regime", "mensuel")]),
})
_OLD_KEY = _KEY[:-1] + ("old-scope",)
SCENARIOS.update({
    # régénération SANS repli : l'alerte mémorisée par une génération précédente ne doit pas survivre
    "fallback_stale_alert_removed_on_regeneration": ({"preset_state": {
        "_dl_active_cache_key": _KEY, "_dl_artifact_main_xlsx": (_OLD_KEY, b"old"),
        "_dl_artifact_fallback_main_xlsx": {"PLN": 9}}}, [("button", "_gen_btn_main_xlsx")]),
    # un dépôt « pending » périmé (génération interrompue) ne doit pas être rattaché à l'artefact suivant
    "fallback_stale_pending_not_attached": ({"preset_state": {
        "_dl_fallback_pending": {"SEK": 1}}}, [("button", "_gen_btn_main_xlsx")]),
})
SCENARIOS["tmp_file_removal_fails"][0]["remove_fails"] = True

_KNOWN_EXCEPTIONS = {"missing_oss_tva_net_total"}


def _elem(el) -> dict:
    d = {"type": el.type}
    for a in ("label", "key", "disabled", "help", "options", "index"):
        try:
            v = getattr(el, a)
        except Exception:
            continue
        if v not in (None, [], ""):
            d[a] = v if not isinstance(v, (set, tuple)) else list(v)
    for a in ("value", "body"):
        try:
            v = getattr(el, a)
        except Exception:
            continue
        if v is not None:
            d[a] = v if isinstance(v, (str, int, float, bool, list, dict)) else repr(v)
    return d


def _walk(block, depth=0):
    out = []
    for ch in block.children.values():
        if hasattr(ch, "children"):
            out.append({"type": "BLOCK:" + str(getattr(ch, "type", "?")), "depth": depth,
                        "label": getattr(ch, "label", None)})
            out.extend(_walk(ch, depth + 1))
        else:
            e = _elem(ch)
            e["depth"] = depth
            out.append(e)
    return out


def _find(at, kind, key):
    return getattr(at, kind)(key=key)


def _run(name: str) -> dict:
    scn, actions = SCENARIOS[name]
    at = AppTest.from_file(_SCRIPT, default_timeout=60)
    at.session_state["_scn"] = scn
    at.run()
    for act in actions:
        kind, key = act[0], act[1]
        w = _find(at, kind, key)
        if kind == "button":
            w.click()
        else:
            w.set_value(act[2])
        at.run()
    return {
        "exceptions": [e.value for e in at.exception],
        "calls": list(at.session_state["_calls_log"]) if "_calls_log" in at.session_state else [],
        "elements": _walk(at.main),
        "session_keys": list(at.session_state["_keys"]) if "_keys" in at.session_state else [],
    }


@pytest.mark.parametrize("name", sorted(SCENARIOS))
def test_telechargements_matches_golden(name):
    snap = json.loads(json.dumps(_run(name), default=repr, sort_keys=True))
    if name not in _KNOWN_EXCEPTIONS:
        assert snap["exceptions"] == [], snap["exceptions"]
    path = _GOLDEN / f"telechargements_{name}.json"
    if os.environ.get("UPDATE_TELECHARGEMENTS_GOLDEN") == "1":
        path.write_text(json.dumps(snap, indent=1, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")
        pytest.skip("golden régénéré")
    assert path.exists(), f"référence manquante : {path.name} (UPDATE_TELECHARGEMENTS_GOLDEN=1 pour la créer)"
    assert snap == json.loads(path.read_text(encoding="utf-8"))
