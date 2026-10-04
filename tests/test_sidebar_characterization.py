"""Tests de caractérisation de ui/sidebar.py::render_sidebar.

But : figer le comportement OBSERVABLE actuel (arbre de widgets Streamlit —
type, libellé, clé, valeur, état désactivé, options —, valeurs de
SidebarResult, appels Postgres/VIES/Stripe déclenchés) AVANT le découpage de
`render_sidebar` en sous-fonctions, puis prouver qu'il est strictement
inchangé après.

Les références (tests/golden/sidebar_*.json) ont été générées sur le code
d'origine. Pour les régénérer volontairement (changement de comportement
ASSUMÉ, jamais lors d'un simple refactor) :
    UPDATE_SIDEBAR_GOLDEN=1 pytest tests/test_sidebar_characterization.py

Aucun réseau, aucun Postgres, aucune connexion persistante.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from _apptest_snapshot import contains_in_order

_HERE = Path(__file__).parent
_SCRIPT = str(_HERE / "_sidebar_app_script.py")
_GOLDEN = _HERE / "golden"

_SIREN_A = {
    "siren": "111222333", "company_name": "ACME SAS", "tva_number": "FR11111222333",
    "ioss_number": "IM1234567890", "seller_is_importer": True,
    "apply_fr_under_threshold": True, "oss_threshold_exceeded_prev_year": False,
    "ioss_own_number_active": True, "countries_with_vat": "FR,DE",
    "vat_numbers_json": json.dumps({"FR": "FR11111222333", "DE": "DE999888777"}),
    "pending_removal_at": None,
}
_SIREN_B = {
    "siren": "444555666", "company_name": "", "tva_number": "",
    "ioss_number": "", "seller_is_importer": False,
    "apply_fr_under_threshold": False, "oss_threshold_exceeded_prev_year": True,
    "ioss_own_number_active": False, "countries_with_vat": "FR",
    "vat_numbers_json": "not-json", "pending_removal_at": 1893499200,
}

# nom -> (scénario, interactions).  Interaction = ("button"|"toggle"|"selectbox", key[, valeur])
SCENARIOS: dict[str, tuple[dict, list[tuple]]] = {
    "admin_no_siren": ({"sirens": []}, []),
    "admin_no_siren_detailed_pulse": ({"sirens": [], "detailed": True, "pulse": "entreprise"}, []),
    "admin_one_siren": ({"sirens": [_SIREN_A]}, []),
    "admin_one_siren_detailed_pulse_vies": (
        {"sirens": [_SIREN_A], "detailed": True, "pulse": "vies_ttl"}, []),
    "reader_one_siren": ({"sirens": [_SIREN_A], "role": "reader", "detailed": True}, []),
    "admin_two_sirens_pending_removal": ({"sirens": [_SIREN_A, _SIREN_B], "quota": 2}, [
        ("selectbox", "siren_select_box", "444555666")]),
    "admin_over_quota_new_selected_blocked": (
        {"sirens": [_SIREN_A, _SIREN_B], "quota": 1, "can_add": (False, "Quota atteint"),
         "_new": True}, []),
    "admin_new_selected_allowed": ({"sirens": [_SIREN_A], "quota": 5, "_new": True}, []),
    "sirens_db_error": ({"sirens_error": True}, []),
    "pending_siren_switch": ({"sirens": [_SIREN_A, _SIREN_B], "quota": 2, "_switch": "444555666"}, []),
    "prevyear_conflict_desync": ({"sirens": [dict(_SIREN_A, oss_threshold_exceeded_prev_year=True)]}, []),
    # Bug corrigé le 2026-09-29 : deux cases cochées -> StreamlitWidgetAlreadyInstantiatedError.
    "oss_conflict_user_checks_prevyear": ({"sirens": [_SIREN_A]}, [
        ("toggle", "oss_thr_prevyear_view_111222333", True)]),
    "oss_conflict_user_checks_threshold": (
        {"sirens": [dict(_SIREN_A, apply_fr_under_threshold=False, oss_threshold_exceeded_prev_year=True)]}, [
            ("toggle", "oss_thr_view_111222333", True)]),
    "oss_conflict_new_siren_form": ({"sirens": [], "quota": 5}, [
        ("text_input", "siren_new", "123456789"),
        ("button", "stepper_next"),
        ("text_input", "vat_num_new_FR", "FR123456789"),
        ("button", "stepper_next"),
        ("toggle", "oss_thr_new", True),
        ("toggle", "oss_thr_prevyear_new", True),
    ]),
    "oss_no_conflict_uncheck_both": ({"sirens": [_SIREN_A]}, [
        ("toggle", "oss_thr_view_111222333", False)]),
    "home_country_change": ({"sirens": [_SIREN_A]}, [("selectbox", "home_country_select", "DE")]),
    "display_currency_change": ({"sirens": [_SIREN_A]}, [("selectbox", "display_currency_select", "PLN")]),
    "remove_siren_scheduled": ({"sirens": [_SIREN_A]}, [("button", "btn_remove_entreprise_111222333")]),
    "remove_siren_immediate": (
        {"sirens": [_SIREN_A], "removal_eff": 1}, [("button", "btn_remove_entreprise_111222333")]),
    "remove_siren_payg_over_quota": (
        {"sirens": [_SIREN_A], "removal_eff": 1, "payg_over": True},
        [("button", "btn_remove_entreprise_111222333")]),
    "cancel_removal": ({"sirens": [_SIREN_B], "quota": 2}, [
        ("button", "btn_cancel_removal_444555666")]),
    "vies_ttl_slider_change": ({"sirens": [_SIREN_A], "detailed": True}, [("slider", None, 12)]),
    "vies_purge": ({"sirens": [_SIREN_A], "detailed": True}, [("button", "purge_vies_cache")]),
    "vies_certificate_snapshot": ({"sirens": [_SIREN_A], "detailed": True}, [
        ("button", "btn_gen_vies_certificate_sidebar")]),
    "vies_certificate_history": ({"sirens": [_SIREN_A], "detailed": True}, [
        ("checkbox", "vies_cert_history_mode_sidebar", True),
        ("button", "btn_gen_vies_certificate_sidebar")]),
    "vies_certificate_empty": ({"sirens": [_SIREN_A], "detailed": True, "snapshot": []}, [
        ("button", "btn_gen_vies_certificate_sidebar")]),
    "file_encoding_choice": ({"sirens": [_SIREN_A], "detailed": True}, [
        ("selectbox", "file_encoding_select", "latin-1")]),
}

# Scénarios dont on ACCEPTE une exception Streamlit dans la référence. Vide
# depuis le 2026-09-29 : le bug latent « seuil OSS + seuil N-1 » (écriture
# session_state après instanciation du widget) est corrigé — voir
# _resolve_oss_threshold_conflict et les scénarios oss_conflict_* ci-dessous.
KNOWN_EXCEPTIONS: set[str] = set()

_ATTRS = ("label", "key", "value", "disabled", "options", "help", "index", "proto_type")


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
        if v not in (None,):
            d[a] = v if isinstance(v, (str, int, float, bool, list, dict)) else repr(v)
    return d


def _walk(block, depth=0):
    out = []
    for ch in block.children.values():
        if hasattr(ch, "children") and getattr(ch, "type", "") in (
                "sidebar", "main", "expander", "container", "column", "popover", "form", "fragment", "tab"):
            out.append({"type": "BLOCK:" + ch.type, "depth": depth,
                        "label": getattr(ch, "label", None)})
            out.extend(_walk(ch, depth + 1))
        elif hasattr(ch, "children"):
            out.append({"type": "BLOCK:" + str(getattr(ch, "type", "?")), "depth": depth})
            out.extend(_walk(ch, depth + 1))
        else:
            e = _elem(ch)
            e["depth"] = depth
            out.append(e)
    return out


def _find(at, kind, key):
    if kind == "slider":
        return at.slider[0]
    if kind == "selectbox":
        return at.selectbox(key=key)
    if kind == "button":
        return at.button(key=key)
    if kind == "checkbox":
        return at.checkbox(key=key)
    if kind == "toggle":
        return at.toggle(key=key)
    if kind == "text_input":
        return at.text_input(key=key)
    raise ValueError(kind)


# Scénarios terminés par st.rerun() (preserve_upload_rerun) : restes périmés dans l'arbre (Streamlit 1.58).
_STALE_AFTER_RERUN = {"cancel_removal"}


def _run(name: str) -> dict:
    scn, actions = SCENARIOS[name]
    at = AppTest.from_file(_SCRIPT, default_timeout=60)
    at.session_state["_scn"] = scn
    if scn.get("_new") or scn.get("_switch"):
        # libellé "nouveau SIREN" tel que rendu par i18n dans la langue par défaut
        from tva_intracom.i18n import _ as tr
        if scn.get("_new"):
            at.session_state["siren_select_box"] = tr("new_siren_option")
        if scn.get("_switch"):
            at.session_state["_pending_siren_switch"] = scn["_switch"]
    at.run()
    for act in actions:
        kind, key = act[0], act[1]
        w = _find(at, kind, key)
        if kind == "button":
            w.click()
        elif kind == "slider":
            w.set_value(act[2])
        elif kind in ("selectbox", "checkbox", "toggle", "text_input"):
            w.set_value(act[2])
        at.run()
    return {
        "exceptions": [e.value for e in at.exception],
        "result": at.session_state["_out"] if "_out" in at.session_state else None,
        "calls": list(at.session_state["_calls_log"]) if "_calls_log" in at.session_state else [],
        "vies_stats_calls": list(at.session_state["_vstats_log"]) if "_vstats_log" in at.session_state else [],
        "elements": _walk(at.sidebar),
        "session_keys": list(at.session_state["_keys"]) if "_keys" in at.session_state else [],
    }


@pytest.mark.parametrize("name", sorted(SCENARIOS))
def test_sidebar_matches_golden(name):
    snap = json.loads(json.dumps(_run(name), default=repr, sort_keys=True))
    if name not in KNOWN_EXCEPTIONS:
        assert snap["exceptions"] == [], snap["exceptions"]
    path = _GOLDEN / f"sidebar_{name}.json"
    if os.environ.get("UPDATE_SIDEBAR_GOLDEN") == "1":
        path.write_text(json.dumps(snap, indent=1, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")
        pytest.skip("golden régénéré")
    assert path.exists(), f"référence manquante : {path.name} (UPDATE_SIDEBAR_GOLDEN=1 pour la créer)"
    expected = json.loads(path.read_text(encoding="utf-8"))
    if name in _STALE_AFTER_RERUN:
        # Voir _apptest_snapshot.contains_in_order : restes de la passe interrompue par st.rerun().
        assert contains_in_order(expected["elements"], snap["elements"]), "éléments de référence manquants"
        assert {k: v for k, v in snap.items() if k != "elements"} == {k: v for k, v in expected.items() if k != "elements"}
    else:
        assert snap == expected
