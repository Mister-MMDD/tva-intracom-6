"""Tests de caractérisation de ui/tabs/vies_ui.py::render_vies.

But : figer le comportement OBSERVABLE actuel de l'onglet VIES (arbre de
widgets, appels sortants vers vies_engine / jobs / PDF / cache de calcul,
contenu du CSV exporté, clés de session) AVANT le découpage de `render_vies`,
puis prouver qu'il est strictement inchangé après.

Références : tests/golden/vies_ui_*.json, générées sur le code d'origine.
Régénération volontaire (changement de comportement ASSUMÉ, jamais lors d'un
simple refactor) : UPDATE_VIES_UI_GOLDEN=1 pytest tests/test_vies_ui_characterization.py

Aucun réseau, aucun Postgres, aucun thread réel, aucune connexion persistante.
"""
from __future__ import annotations

import builtins
import json
import os
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from _apptest_snapshot import walk

_HERE = Path(__file__).parent
_SCRIPT = str(_HERE / "_vies_ui_app_script.py")
_GOLDEN = _HERE / "golden"

S: dict[str, tuple[dict, list[tuple]]] = {}


def add(name, scn=None, actions=None):
    S[name] = (scn or {}, actions or [])


def _rec(sale_id, vat, country, amount, avoided, *, delta=None, rc=False, disp=None, stock="FR",
         national=False, dep=False, scenario="", reason="VIES invalide"):
    return dict(sale_id=sale_id, buyer_vat_number=vat, buyer_country=country, amount_ht=amount,
                vat_avoided=avoided, reason=reason, vat_delta=delta if delta is not None else avoided,
                is_domestic_reverse_charge=rc, display_id=disp or sale_id, stock_country=stock,
                is_national_tax_id=national, taxed_at_departure=dep, scenario=scenario)


_REJ = _rec("S1", "DE111111111", "DE", "100.00", "19.00", dep=True, scenario="A")
_REJ2 = _rec("S2", "IT22222222222", "IT", "50.00", "11.00", scenario="B")
_RC = _rec("S3", "FR33333333333", "FR", "80.00", "0.00", delta="16.00", rc=True, scenario="C")
_ZERO = _rec("S4", "ES44444444", "ES", "10.00", "0.00", delta="0.00", scenario="D")
_NAT = _rec("S5", "12345678Z", "ES", "20.00", "0.00", national=True, scenario="E")
_XSS = _rec("S<img>", "DE<b>9", "DE", "5.00", "1.00", disp="<script>x</script>")

_BASE = {"total_checked": 5, "valid_count": 3, "invalid_count": 2}
_INC = {"total_checked": 5, "valid_count": 2, "invalid_count": 1, "inconclusive_count": 2,
        "inconclusive_vats": ["DE100", "FR200"],
        "inconclusive_vat_details": [
            {"vat": "DE100", "country": "DE", "display_ids": ["A1", "A2", "A3", "A4", "A5"],
             "last_auto_status": True, "last_checked_at": "2026-08-30T10:00:00"},
            {"vat": "FR200", "last_auto_status": False, "last_checked_at": ""}],
        "vat_to_display_ids": {"DE100": ["A1"]}}

# ── États de haut niveau ─────────────────────────────────────────────────────
add("vies_disabled", {"enable_vies": False, "summary": _BASE})
add("summary_none", {"summary": None})
add("zero_checked", {"summary": {"total_checked": 0}})
add("all_valid", {"summary": {"total_checked": 3, "valid_count": 3}})
add("all_valid_with_manual_and_stale", {"summary": {"total_checked": 3, "valid_count": 3, "manual_valid_count": 1,
                                                    "stale_fallback_count": 1, "national_id_count": 1}})
add("inconclusive_only_no_reject", {"summary": dict(_INC, invalid_count=0, valid_count=3)})
add("national_id_only", {"summary": {"total_checked": 1, "national_id_count": 1, "reclass": [_NAT]}})
add("all_inconclusive", {"summary": {"total_checked": 2, "inconclusive_count": 2,
                                     "inconclusive_vats": ["DE100", "FR200"]}})
add("all_inconclusive_test_ok", {"summary": {"total_checked": 2, "inconclusive_count": 2,
                                             "inconclusive_vats": ["DE100", "FR200"]}}, [("click", "test_vies_conn")])
add("all_inconclusive_test_down", {"summary": {"total_checked": 2, "inconclusive_count": 2,
                                               "inconclusive_vats": ["DE100", "FR200"]}, "vies_test_valid": False},
    [("click", "test_vies_conn")])

# ── Rejets / reclassifications ───────────────────────────────────────────────
_REC_SUM = dict(_BASE, reclass=[_REJ, _REJ2, _RC, _ZERO, _NAT])
add("reclass_mixed", {"summary": _REC_SUM})
add("reclass_mixed_detailed", {"summary": _REC_SUM, "preset_ss": {"display_mode": "detaille"}})
add("reclass_filter_recovered", {"summary": _REC_SUM}, [("radio_label", "vies_filter_label", "vies_filter_recovered")])
add("reclass_filter_reverse_charge", {"summary": _REC_SUM}, [("radio_label", "vies_filter_label", "vies_filter_reverse_charge")])
add("reclass_filter_zero_impact", {"summary": _REC_SUM}, [("radio_label", "vies_filter_label", "vies_filter_zero_impact")])
add("reclass_locked_export", {"summary": _REC_SUM, "can_export": False})
_MANY_NAT = [_rec(f"N{i}", f"NIF{i}", "ES", f"{10+i}.00", "0.00", national=True, scenario="E") for i in range(8)]
_MANY_REJ = [_rec(f"R{i}", f"DE{i}00000000", "DE", f"{10+i}.00", f"{i+1}.00", scenario="A") for i in range(8)]
add("reclass_many_national_ids_locked", {"summary": dict(_BASE, reclass=_MANY_NAT), "can_export": False})
add("reclass_many_national_ids_unlocked", {"summary": dict(_BASE, reclass=_MANY_NAT)})
add("reclass_many_rejections_locked", {"summary": dict(_BASE, reclass=_MANY_REJ), "can_export": False})
add("reclass_chart_multi_country", {"summary": dict(_BASE, reclass=[_REJ, _REJ2, _rec("S9", "DE9", "DE", "5.00", "2.50")])})
add("reclass_with_xss_ids", {"summary": dict(_BASE, reclass=[_XSS])})
add("reclass_only_domestic_rc", {"summary": dict(_BASE, reclass=[_RC])})
add("reclass_reader", {"summary": _REC_SUM, "is_admin": False})

# ── Numéros non vérifiés : relance automatique, job, dialogue ────────────────
add("inconclusive_autostart", {"summary": _INC})
add("inconclusive_already_launched", {"summary": _INC, "preset_ss": {"_vies_auto_retry_launched_scope1": True}})
add("inconclusive_job_running", {"summary": _INC, "job": {"done": False}})
add("inconclusive_job_done_resolved", {"summary": _INC, "job": {"done": True, "result": {"resolved": 2, "remaining": 0, "iterations": 1}}})
add("inconclusive_job_done_resolved_update", {"summary": _INC, "job": {"done": True, "result": {"resolved": 2, "remaining": 0, "iterations": 1}}},
    [("click", "retry_vies_update_btn_manual")])
# Le dialogue de succès ne doit être déclenché QU'UNE FOIS : un rerun sans rapport (case cochée) ne le rouvre pas.
add("inconclusive_job_done_resolved_then_unrelated_rerun",
    {"summary": _INC, "job": {"done": True, "result": {"resolved": 2, "remaining": 0, "iterations": 1}}},
    [("checkbox", "vies_cert_history_mode", True)])
add("inconclusive_job_done_none", {"summary": _INC, "job": {"done": True, "result": {"resolved": 0, "remaining": 2, "iterations": 5}}})
add("inconclusive_job_done_none_reverify", {"summary": _INC, "job": {"done": True, "result": {"resolved": 0, "remaining": 2, "iterations": 5}}},
    [("click", "retry_vies_btn")])
add("inconclusive_launched_flag_then_manual_retry", {"summary": _INC, "job": {"done": True, "result": {"resolved": 0, "remaining": 2, "iterations": 5}},
                                                     "preset_ss": {"_vies_auto_retry_launched_scope1": True}}, [("click", "retry_vies_btn")])
add("inconclusive_job_done_no_result", {"summary": _INC, "job": {"done": True, "result": None}})

# ── Classification manuelle (fragment) ───────────────────────────────────────
add("manual_class_select_valid", {"summary": _INC, "preset_ss": {"_vies_auto_retry_launched_scope1": True}},
    [("select", "vies_override_DE100", ("i18n", "manual_valid"))])
add("manual_class_apply", {"summary": _INC, "preset_ss": {"_vies_auto_retry_launched_scope1": True}},
    [("select", "vies_override_DE100", ("i18n", "manual_valid")), ("select", "vies_override_FR200", ("i18n", "manual_invalid")),
     ("click_label", "vies_manual_class_apply_btn")])
add("manual_class_apply_permission_error", {"summary": _INC, "preset_ss": {"_vies_auto_retry_launched_scope1": True},
                                            "beh": {"set_override": "perm"}},
    [("select", "vies_override_DE100", ("i18n", "manual_valid")), ("click_label", "vies_manual_class_apply_btn")])
add("manual_class_reset", {"summary": _INC, "preset_ss": {"_vies_auto_retry_launched_scope1": True}},
    [("select", "vies_override_DE100", ("i18n", "manual_valid")), ("click_label", "vies_manual_class_reset_btn")])
add("manual_class_without_details", {"summary": {"total_checked": 2, "inconclusive_count": 2, "invalid_count": 0,
                                                 "inconclusive_vats": ["DE100", "FR200"]},
                                     "preset_ss": {"_vies_auto_retry_launched_scope1": True}})

# ── Overrides manuels enregistrés ────────────────────────────────────────────
_OV = [("DE100", True, "2026-09-01 10:00"), ("FR200", False, "2020-01-01 10:00"), ("IT300", True, "")]
_OVS = dict(_INC, vat_to_display_ids={"DE100": ["A1", "A2", "A3", "A4"], "FR200": ["<i>x</i>"]})
add("overrides_admin", {"summary": _OVS, "overrides": _OV, "preset_ss": {"_vies_auto_retry_launched_scope1": True}})
add("overrides_reader", {"summary": _OVS, "overrides": _OV, "is_admin": False, "preset_ss": {"_vies_auto_retry_launched_scope1": True}})
add("overrides_save", {"summary": _OVS, "overrides": _OV, "preset_ss": {"_vies_auto_retry_launched_scope1": True}},
    [("select", "edit_override_b_DE100", ("i18n", "manual_invalid")), ("click", "save_override_b_DE100")])
add("overrides_save_permission_error", {"summary": _OVS, "overrides": _OV, "beh": {"set_override": "perm"},
                                        "preset_ss": {"_vies_auto_retry_launched_scope1": True}},
    [("click", "save_override_b_DE100")])
add("overrides_delete", {"summary": _OVS, "overrides": _OV, "preset_ss": {"_vies_auto_retry_launched_scope1": True}},
    [("click", "del_override_b_FR200")])
add("overrides_delete_error", {"summary": _OVS, "overrides": _OV, "beh": {"del_override": "perm"},
                               "preset_ss": {"_vies_auto_retry_launched_scope1": True}}, [("click", "del_override_b_FR200")])
add("overrides_load_failure", {"summary": _OVS, "beh": {"get_overrides": "err"}, "preset_ss": {"_vies_auto_retry_launched_scope1": True}})
# XSS : identifiants issus du fichier importé (donnée non fiable) injectés dans un markdown HTML.
add("overrides_xss_vat_and_sales", {"summary": dict(_INC, inconclusive_vats=["DE100"], inconclusive_vat_details=[],
                                                    vat_to_display_ids={"DE<b>1": ["<script>x</script>", "B\"'&"]}),
                                    "overrides": [("DE<b>1", True, "<u>2026-09-01 10:00")],
                                    "preset_ss": {"_vies_auto_retry_launched_scope1": True}})
add("overrides_ttl_custom", {"summary": _OVS, "overrides": _OV, "ttl": 7, "preset_ss": {"_vies_auto_retry_launched_scope1": True}})

# ── Purge automatique du cache ───────────────────────────────────────────────
add("purge_first_render", {"summary": _BASE})
add("purge_already_done", {"summary": _BASE, "preset_ss": {"vies_auto_purged_scope1": True}})
add("purge_error", {"summary": _BASE, "beh": {"purge": "err"}})

# ── Certificat VIES ──────────────────────────────────────────────────────────
_SV = {"sale_vats": [("DE111", "DE"), ("de 222", "DE"), ("", "FR"), ("FR333", "FR"), ("DE111", "DE")],
       "refund_vats": [("IT444", "IT")], "summary": _BASE}
add("cert_default_admin", _SV)
add("cert_default_reader", dict(_SV, is_admin=False))
add("cert_file_snapshot", _SV, [("click", "btn_gen_vies_certificate")])
add("cert_file_history", _SV, [("checkbox", "vies_cert_history_mode", True), ("click", "btn_gen_vies_certificate")])
add("cert_account_snapshot", _SV, [("radio", "vies_cert_scope", "account"), ("click", "btn_gen_vies_certificate")])
add("cert_account_history", _SV, [("radio", "vies_cert_scope", "account"), ("checkbox", "vies_cert_history_mode", True),
                                  ("click", "btn_gen_vies_certificate")])
add("cert_empty_snapshot", dict(_SV, snapshot=[]), [("click", "btn_gen_vies_certificate")])
add("cert_empty_history", dict(_SV, history=[]), [("checkbox", "vies_cert_history_mode", True), ("click", "btn_gen_vies_certificate")])
add("cert_pdf_error", dict(_SV, beh={"pdf": "err"}), [("click", "btn_gen_vies_certificate")])
add("cert_file_without_refunds", dict(_SV, refund_vats=None), [("click", "btn_gen_vies_certificate")])
add("cert_locked", dict(_SV, can_export=False), [("click", "btn_gen_vies_certificate")])


def _res(v):
    if isinstance(v, (list, tuple)) and len(v) == 2 and v[0] == "i18n":
        from tva_intracom.i18n import _ as tr
        return tr(v[1])
    return v


def _run(name: str) -> dict:
    scn, actions = S[name]
    builtins.__vies_store__ = {"scn": scn}
    at = AppTest.from_file(_SCRIPT, default_timeout=60)
    at.run()
    from tva_intracom.i18n import _ as tr
    for act in actions:
        kind = act[0]
        if kind == "click":
            at.button(key=act[1]).click()
        elif kind == "click_label":
            next(b for b in at.button if b.label == tr(act[1])).click()
        elif kind == "select":
            at.selectbox(key=act[1]).set_value(_res(act[2]))
        elif kind == "radio":
            at.radio(key=act[1]).set_value(act[2])
        elif kind == "radio_label":
            r = next(x for x in at.radio if x.label == tr(act[1]))
            r.set_value(tr(act[2]))
        elif kind == "checkbox":
            at.checkbox(key=act[1]).set_value(act[2])
        at.run()
    st_ = builtins.__vies_store__
    state = at.session_state.filtered_state
    prim = {k: v for k, v in state.items()
            if isinstance(v, (bool, int, str, float)) and not str(k).startswith("$$")}
    return {
        "exceptions": [e.value for e in at.exception],
        "calls": st_.get("calls", []),
        "session_keys": sorted(str(k) for k in state),
        "session_values": prim,
        "main": walk(at.main),
    }


@pytest.mark.parametrize("name", sorted(S))
def test_vies_ui_matches_golden(name):
    snap = json.loads(json.dumps(_run(name), default=repr, sort_keys=True))
    assert snap["exceptions"] == [], snap["exceptions"]
    path = _GOLDEN / f"vies_ui_{name}.json"
    if os.environ.get("UPDATE_VIES_UI_GOLDEN") == "1":
        path.write_text(json.dumps(snap, indent=1, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")
        pytest.skip("golden régénéré")
    assert path.exists(), f"référence manquante : {path.name} (UPDATE_VIES_UI_GOLDEN=1 pour la créer)"
    assert snap == json.loads(path.read_text(encoding="utf-8"))
