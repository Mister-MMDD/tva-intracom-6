"""Non-régression (2026-10-02) : la purge de session d'app.py (aucun fichier
chargé) ne doit pas effacer l'état du stepper SIREN ni de l'onboarding.

Symptômes d'origine : « Le numéro SIREN est requis » après saisie des pays/TVA ;
boutons « Passer »/« Suivant » du wizard ramenant à l'étape précédente.
"""
from streamlit.testing.v1 import AppTest

from tva_intracom.ui import sidebar as sb
from tva_intracom.ui.session_guard import purge_stale_session_keys

_SCRIPT = "tests/_purge_flow_app_script.py"


def _at(target: str) -> AppTest:
    at = AppTest.from_file(_SCRIPT, default_timeout=30)
    at.session_state["_target"] = target
    at.run()
    assert not at.exception
    return at


def test_purge_removes_derived_keys_but_keeps_whitelist_and_protected():
    state = {
        "keep_me": 1, "derived_df": 2, "siren_stepper_step": 1,
        "siren_stepper_data": {}, "vat_num_new_FR": "x", "onboarding_wizard_step": 2,
        "_onboarding_step": "active", "_onboarding_vies_ttl_pulse_done": True,
    }
    purge_stale_session_keys(state, {"keep_me"})
    assert set(state) == {
        "keep_me", "siren_stepper_step", "siren_stepper_data", "vat_num_new_FR",
        "onboarding_wizard_step", "_onboarding_step", "_onboarding_vies_ttl_pulse_done",
    }


def test_all_stepper_keys_are_protected():
    keys = ["siren_stepper_step", "siren_stepper_data", "vat_num_new_DE"]
    keys += list(sb._get_stepper_data.__globals__["st"].session_state.get("siren_stepper_data", {}))
    keys += ["nom_new", "siren_new", "vat_countries_new", "ioss_new", "ioss_own_active_new",
             "ddp_new", "oss_thr_new", "oss_thr_prevyear_new"]
    state = {k: 1 for k in keys}
    purge_stale_session_keys(state, set())
    assert set(state) == set(keys)


def test_stepper_survives_purge_between_steps():
    at = _at("stepper")
    at.text_input(key="nom_new").set_value("ACME SARL")
    at.text_input(key="siren_new").set_value("123456789")
    at.button(key="stepper_next").click().run()
    assert at.session_state["siren_stepper_step"] == 1
    at.text_input(key="vat_num_new_FR").set_value("FR12123456789")
    at.button(key="stepper_next").click().run()
    assert at.session_state["siren_stepper_step"] == 2
    assert at.session_state["siren_stepper_data"]["siren_new"] == "123456789"
    at.button(key="stepper_finish").click().run()
    assert ("register", "123456789") in at.session_state["_calls"]
    assert not [w for w in at.warning if "SIREN" in w.value]


def test_wizard_next_and_prev_survive_purge():
    at = _at("wizard")
    at.button(key="wizard_next").click().run()
    assert at.session_state["onboarding_wizard_step"] == 1
    at.run()  # un run complet de plus : l'étape ne doit pas être réinitialisée
    assert at.session_state["onboarding_wizard_step"] == 1


def test_wizard_skip_marks_onboarding_seen_for_current_user():
    at = _at("wizard")
    at.button(key="wizard_skip").click().run()
    assert ("seen", "u1", True) in at.session_state["_calls"]


def test_wizard_finish_marks_onboarding_seen_for_current_user():
    at = _at("wizard")
    at.button(key="wizard_next").click().run()
    at.button(key="wizard_next").click().run()
    assert at.session_state["onboarding_wizard_step"] == 2
    at.button(key="wizard_finish").click().run()
    assert ("seen", "u1", True) in at.session_state["_calls"]
