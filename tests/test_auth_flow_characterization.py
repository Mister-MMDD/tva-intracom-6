"""Tests de caractérisation de ui/auth_flow.py::run_auth_flow.

But : figer le comportement OBSERVABLE actuel du flux d'authentification
(arbre de widgets Streamlit, AuthContext renvoyé, appels sortants vers
Supabase / Postgres / cookies / mémoire, paramètres d'URL restants, clés de
session) AVANT le découpage de `run_auth_flow` en sous-fonctions, puis
prouver qu'il est strictement inchangé après. Module SENSIBLE (sécurité :
sessions, PKCE, récupération de mot de passe, liens magiques, déconnexion).

Références : tests/golden/auth_flow_*.json, générées sur le code d'origine.
Régénération volontaire (changement de comportement ASSUMÉ, jamais lors d'un
simple refactor) : UPDATE_AUTH_FLOW_GOLDEN=1 pytest tests/test_auth_flow_characterization.py

Aucun réseau, aucun Postgres, aucune connexion persistante, aucun sleep réel.
"""
from __future__ import annotations

import builtins
import json
import os
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from _apptest_snapshot import contains_in_order, walk

_HERE = Path(__file__).parent
_SCRIPT = str(_HERE / "_auth_flow_app_script.py")
_GOLDEN = _HERE / "golden"

EMAIL = "user@example.com"
_COOKIE = {"cookies": {"tva_session_token": "COOKIE-TOK"}}

# nom -> (scénario, query_params, actions)
# action = ("text", key, valeur) | ("click", key) | ("click_idx", n)
S: dict[str, tuple[dict, dict, list[tuple]]] = {}


def add(name, scn=None, qp=None, actions=None):
    S[name] = (scn or {}, qp or {}, actions or [])


# ── 0. Restauration de session ───────────────────────────────────────────────
add("anon_login_screen")
add("anon_cookie_sync_waiting", {"preset_ss": {"_cookie_sync_attempts": 1}})
add("anon_cookie_sync_done", {"preset_ss": {"_cookie_sync_attempts": -1}})
add("cookie_restore_ok", dict(_COOKIE, restore_user=True))
add("cookie_restore_invalid", dict(_COOKIE, restore_user=False))
add("cookie_logged_out_marker", {"cookies": {"tva_session_token": "LOGGED_OUT"}, "restore_user": True})
add("cookie_quoted_token", {"cookies": {"tva_session_token": '"COOKIE-TOK"'}, "restore_user": True})
add("cookie_but_manual_logout", dict(_COOKIE, restore_user=True, preset_ss={"manual_logout": True}))
add("cookie_via_component_fallback", {"cm_cookie": "CM-TOK", "restore_user": True})
add("session_token_in_url_restore_ok", {"restore_user": True}, {"session_token": "URL-TOK"})
add("session_token_in_url_invalid", {"restore_user": False}, {"session_token": "URL-TOK"})

# ── Utilisateur connecté ─────────────────────────────────────────────────────
add("logged_in_plain", {"logged_in": True})
add("logged_in_oauth_leftovers_cleared", {"logged_in": True}, {"code": "abc", "sb_provider": "google"})
add("logged_in_magic_token_cleared", {"logged_in": True}, {"login_token": "MAGIC"})
add("logged_in_org_catchup_locks", {"logged_in": True, "org": {"sub_active": True, "solo": False, "locked": False}})
add("logged_in_org_catchup_solo", {"logged_in": True, "org": {"sub_active": True, "solo": True}})
add("logged_in_org_catchup_already_locked", {"logged_in": True, "org": {"sub_active": True, "locked": True}})
add("logged_in_org_catchup_error", {"logged_in": True, "org": {"err": True}})
add("logged_in_secret_base_url", {"logged_in": True, "secrets": {"APP_BASE_URL": "https://mon.site/"}})
add("logged_in_trusted_host_header", {"logged_in": True, "headers": {"Host": "foo.streamlit.app"}})
add("logged_in_untrusted_host_header", {"logged_in": True, "headers": {"Host": "evil.com"}})
add("logged_in_localhost_header", {"logged_in": True, "headers": {"Host": "localhost:8501"}})
add("logout_with_context_cookie", dict(_COOKIE, logged_in=True), actions=[("click", "btn_logout")])
add("logout_with_component_cookie", {"cm_cookie": "CM-TOK", "logged_in": True}, actions=[("click", "btn_logout")])
add("logout_without_any_cookie", {"logged_in": True}, actions=[("click", "btn_logout")])

# ── Lien magique ─────────────────────────────────────────────────────────────
add("magic_link_landing", qp={"login_token": "MAGIC"})
add("magic_link_confirm_ok", {"magic": "user"}, {"login_token": "MAGIC"}, [("click", "confirm_magic_link")])
add("magic_link_confirm_none", {"magic": "none"}, {"login_token": "MAGIC"}, [("click", "confirm_magic_link")])
add("magic_link_confirm_permission_error", {"magic": "perm"}, {"login_token": "MAGIC"}, [("click", "confirm_magic_link")])
add("magic_link_confirm_error", {"magic": "err"}, {"login_token": "MAGIC"}, [("click", "confirm_magic_link")])
add("magic_link_confirm_account_blocked", {"magic": "user", "goc_error": True}, {"login_token": "MAGIC"},
    [("click", "confirm_magic_link")])

# ── Connexion refusée (compte bloqué) : le motif doit rester VISIBLE (bugfix 2026-09-29) ──
add("dev_bypass_login_blocked", {"secrets": {"LOCAL_DEV_BYPASS_AUTH": True}, "goc_error": True},
    actions=[("text", "dev_login_email_input", "dev@example.com"), ("click", "btn_dev_login")])
add("signup_ok_immediate_blocked", {"goc_error": True},
    actions=[("text", "login_email_input", EMAIL), ("text", "login_password_input", "secret"),
             ("click", "btn_password_signup")])
add("oauth_access_token_blocked", {"goc_error": True}, {"access_token": "AT"})
add("pkce_B_login_blocked", {"goc_error": True},
    {"code": "CODE", "sb_provider": "google", "sb_nonce": "NONCE123456789"})
# Le motif est affiché UNE seule fois : une interaction ultérieure (saisie) le fait disparaître.
add("blocked_message_shown_once", {"goc_error": True}, actions=[
    ("text", "login_email_input", EMAIL), ("text", "login_password_input", "secret"),
    ("click", "btn_password_signin"), ("text", "login_email_input", "autre@example.com")])

# ── Mode dev local ───────────────────────────────────────────────────────────
_DEV = {"secrets": {"LOCAL_DEV_BYPASS_AUTH": True}}
add("dev_bypass_screen", _DEV)
add("dev_bypass_login_ok", _DEV, actions=[("text", "dev_login_email_input", "dev@example.com"), ("click", "btn_dev_login")])
add("dev_bypass_invalid_email", _DEV, actions=[("text", "dev_login_email_input", "nope"), ("click", "btn_dev_login")])

# ── Connexion / inscription par mot de passe ─────────────────────────────────
_PW = [("text", "login_email_input", EMAIL), ("text", "login_password_input", "secret")]
add("signin_ok", actions=_PW + [("click", "btn_password_signin")])
add("signin_error", {"sb": {"sign_in": "err"}}, actions=_PW + [("click", "btn_password_signin")])
add("signin_missing_fields", actions=[("text", "login_email_input", EMAIL), ("click", "btn_password_signin")])
add("signin_account_blocked", {"goc_error": True}, actions=_PW + [("click", "btn_password_signin")])
add("signup_ok_immediate", actions=_PW + [("click", "btn_password_signup")])
add("signup_confirm_email", {"sb": {"sign_up": "notoken"}}, actions=_PW + [("click", "btn_password_signup")])
add("signup_refused", {"can_signup": (False, "Inscriptions fermées")}, actions=_PW + [("click", "btn_password_signup")])
add("signup_error", {"sb": {"sign_up": "err"}}, actions=_PW + [("click", "btn_password_signup")])
add("signup_invalid_email", actions=[("text", "login_email_input", "bad"), ("text", "login_password_input", "x"),
                                     ("click", "btn_password_signup")])

# ── Mot de passe oublié / lien magique (envoi) ───────────────────────────────
add("forgot_password_ok", actions=[("text", "reset_password_email_input", EMAIL), ("click", "btn_send_reset_password")])
add("forgot_password_error", {"sb": {"reset_email": "err"}},
    actions=[("text", "reset_password_email_input", EMAIL), ("click", "btn_send_reset_password")])
add("forgot_password_pkce_save_error", {"sb": {"save_pkce": "err"}},
    actions=[("text", "reset_password_email_input", EMAIL), ("click", "btn_send_reset_password")])
add("forgot_password_invalid_email", actions=[("text", "reset_password_email_input", "bad"),
                                              ("click", "btn_send_reset_password")])
add("send_magic_ok", actions=[("text", "login_email_input", EMAIL), ("click", "btn_send_magic_link")])
add("send_magic_refused", {"can_signup": (False, "Refusé")},
    actions=[("text", "login_email_input", EMAIL), ("click", "btn_send_magic_link")])
add("send_magic_smtp_error", {"sb": {"send_magic": "err"}},
    actions=[("text", "login_email_input", EMAIL), ("click", "btn_send_magic_link")])
add("send_magic_invalid_email", actions=[("text", "login_email_input", "bad"), ("click", "btn_send_magic_link")])

# ── Boutons OAuth ────────────────────────────────────────────────────────────
add("oauth_url_error", {"sb": {"oauth_url": "err"}})
add("oauth_pkce_save_error", {"sb": {"save_pkce": "err"}})
add("oauth_cached_pkce", {"preset_ss": {"_sb_pkce_google": ("N-CACHED", "V-CACHED")}})

# ── Retours OAuth / récupération : cas A, A0, B0, B, C ───────────────────────
add("oauth_access_token_ok", qp={"access_token": "AT"})
add("oauth_access_token_error", {"sb": {"access_token_user": "err"}}, {"access_token": "AT"})
_NEWPW = [("text", "reset_new_password_input", "pw1"), ("text", "reset_new_password_confirm_input", "pw1")]
add("recovery_A0_form", qp={"access_token": "AT", "type": "recovery"})
add("recovery_A0_mismatch", qp={"access_token": "AT", "type": "recovery"},
    actions=[("text", "reset_new_password_input", "a"), ("text", "reset_new_password_confirm_input", "b"),
             ("click", "btn_update_password")])
add("recovery_A0_ok", qp={"access_token": "AT", "type": "recovery"}, actions=_NEWPW + [("click", "btn_update_password")])
add("recovery_A0_error", {"sb": {"update_pwd": "err"}}, {"access_token": "AT", "type": "recovery"},
    _NEWPW + [("click", "btn_update_password")])
add("recovery_B0_form", {"candidates": ["V1"]}, {"code": "CODE"})
add("recovery_B0_second_candidate", {"candidates": ["V1", "V2"], "sb": {"exchange_by_verifier": True, "exchange_V1": "err"}},
    {"code": "CODE"})
add("recovery_B0_all_fail", {"candidates": ["V1", "V2"], "sb": {"exchange_by_verifier": True, "exchange_V1": "err",
                                                                 "exchange_V2": "err"}}, {"code": "CODE"})
add("recovery_B0_no_candidates", {"candidates": []}, {"code": "CODE"})
add("recovery_B0_cached_token", {"candidates": ["V1"], "preset_ss": {"_sb_pkce_recovery_bare": ("CODE", "AT-CACHED")}},
    {"code": "CODE"})
add("recovery_B0_update_ok", {"candidates": ["V1"]}, {"code": "CODE"}, _NEWPW + [("click", "btn_update_password")])
add("recovery_B0_update_error", {"candidates": ["V1"], "sb": {"update_pwd": "err"}}, {"code": "CODE"},
    _NEWPW + [("click", "btn_update_password")])
add("recovery_B0_mismatch", {"candidates": ["V1"]}, {"code": "CODE"},
    [("text", "reset_new_password_input", "a"), ("text", "reset_new_password_confirm_input", "b"),
     ("click", "btn_update_password")])
add("pkce_B_recovery_mismatch", qp={"code": "CODE", "sb_provider": "recovery", "sb_nonce": "NONCE123456789"},
    actions=[("text", "reset_new_password_input", "a"), ("text", "reset_new_password_confirm_input", "b"),
             ("click", "btn_update_password")])
add("pkce_B_recovery_update_error", {"sb": {"update_pwd": "err"}},
    {"code": "CODE", "sb_provider": "recovery", "sb_nonce": "NONCE123456789"},
    _NEWPW + [("click", "btn_update_password")])
add("pkce_B_login_ok", qp={"code": "CODE", "sb_provider": "google", "sb_nonce": "NONCE123456789"})
add("pkce_B_login_session_cached", {"preset_ss": {"_sb_pkce_google": ("NONCE123456789", "V-CACHED")}},
    {"code": "CODE", "sb_provider": "google", "sb_nonce": "NONCE123456789"})
add("pkce_B_exchange_error", {"sb": {"exchange": "err"}}, {"code": "CODE", "sb_provider": "github", "sb_nonce": "NONCE123456789"})
add("pkce_B_verifier_lost", {"verifier_db": None}, {"code": "CODE", "sb_provider": "google", "sb_nonce": "NONCE123456789"})
add("pkce_B_verifier_lookup_error", {"verifier_db": "lookup_error"},
    {"code": "CODE", "sb_provider": "google", "sb_nonce": "NONCE123456789"})
add("pkce_B_verifier_lost_retry", {"verifier_db": None}, {"code": "CODE", "sb_provider": "google", "sb_nonce": "NONCE123456789"},
    [("click_idx", 0)])
add("pkce_B_no_nonce", qp={"code": "CODE", "sb_provider": "google"})
add("pkce_B_recovery_form", qp={"code": "CODE", "sb_provider": "recovery", "sb_nonce": "NONCE123456789"})
add("pkce_B_recovery_update_ok", qp={"code": "CODE", "sb_provider": "recovery", "sb_nonce": "NONCE123456789"},
    actions=_NEWPW + [("click", "btn_update_password")])
# L'erreur OAuth est lue AVANT le traitement du jeton : elle doit survivre à la purge
# de l'URL faite par le cas A en échec (cas C exécuté ensuite avec la valeur d'origine).
add("oauth_access_token_error_then_error_param", {"sb": {"access_token_user": "err"}},
    {"access_token": "AT", "error_code": "access_denied", "error_description": "Denied"})
add("oauth_error_email_verification", qp={"error_code": "provider_email_needs_verification"})
add("oauth_error_generic", qp={"error_code": "access_denied", "error_description": "User said no"})
add("oauth_error_generic_no_desc", qp={"error_code": "server_error"})
add("oauth_error_cancel", qp={"error_code": "access_denied"}, actions=[("click", "clear_oauth_error")])

_ATTRS_SESSION_IGNORE = ()


def _find(at, kind, key):
    if kind == "text":
        return at.text_input(key=key)
    return at.button(key=key)


# Scénarios qui se terminent par st.rerun() : l'arbre observé contient des restes périmés (Streamlit 1.58).
_STALE_AFTER_RERUN = {
    "blocked_message_shown_once", "dev_bypass_login_ok", "magic_link_confirm_ok",
    "session_token_in_url_invalid", "signin_account_blocked", "signin_ok",
    "signup_ok_immediate", "signup_ok_immediate_blocked",
}


def _run(name: str) -> dict:
    scn, qp, actions = S[name]
    builtins.__af_store__ = {"scn": scn}
    at = AppTest.from_file(_SCRIPT, default_timeout=60)
    for k, v in qp.items():
        at.query_params[k] = v
    at.run()
    for act in actions:
        if act[0] == "text":
            at.text_input(key=act[1]).input(act[2])
        elif act[0] == "click":
            at.button(key=act[1]).click()
        elif act[0] == "click_idx":
            at.button[act[1]].click()
        at.run()
    st = builtins.__af_store__
    return {
        "exceptions": [e.value for e in at.exception],
        "returned_context": st.get("ret"),
        "calls": st.get("calls", []),
        # Lus côté AppTest : après st.stop(), tout accès st.* depuis le script relève StopException.
        "query_params_final": {k: at.query_params[k] for k in at.query_params},
        "session_keys": sorted(str(k) for k in at.session_state.filtered_state),
        "main": walk(at.main),
        "sidebar": walk(at.sidebar),
    }


@pytest.mark.parametrize("name", sorted(S))
def test_auth_flow_matches_golden(name):
    snap = json.loads(json.dumps(_run(name), default=repr, sort_keys=True))
    assert snap["exceptions"] == [], snap["exceptions"]
    path = _GOLDEN / f"auth_flow_{name}.json"
    if os.environ.get("UPDATE_AUTH_FLOW_GOLDEN") == "1":
        path.write_text(json.dumps(snap, indent=1, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")
        pytest.skip("golden régénéré")
    assert path.exists(), f"référence manquante : {path.name} (UPDATE_AUTH_FLOW_GOLDEN=1 pour la créer)"
    expected = json.loads(path.read_text(encoding="utf-8"))
    if name in _STALE_AFTER_RERUN:
        # Voir _apptest_snapshot.contains_in_order : restes de la passe interrompue par st.rerun().
        for tree in ("main", "sidebar"):
            assert contains_in_order(expected[tree], snap[tree]), f"arbre « {tree} » : éléments de référence manquants"
        assert {k: v for k, v in snap.items() if k not in ("main", "sidebar")} == \
               {k: v for k, v in expected.items() if k not in ("main", "sidebar")}
    else:
        assert snap == expected


def test_oauth_buttons_css_is_valid():
    """Garde-fou du bugfix 2026-09-29 : le CSS des boutons OAuth ne doit plus contenir
    d'accolades doublées (règles invalides => ni logo ni couleur de marque)."""
    from tva_intracom.ui.auth_flow import _OAUTH_BUTTONS_CSS as css

    assert "{{" not in css and "}}" not in css
    assert css.count("{") == css.count("}") == 6          # 3 fournisseurs x (bouton + libellé)
    for prov in ("google", "github", "cognito"):
        assert f".st-key-oauth_btn_{prov} a[data-testid^=\"stBaseLinkButton\"] {{" in css
    # Chaque règle « bouton » porte bien logo + couleur ; chaque règle « libellé » sa couleur de texte.
    assert css.count("background-image: url(") == 3
    assert css.count("background-color:") == 3


@pytest.mark.parametrize("value", ["false", "0", "1", "yes", "", 1, None])
def test_local_dev_bypass_requires_explicit_true(monkeypatch, value):
    from tva_intracom.ui import auth_flow

    monkeypatch.setattr(
        auth_flow,
        "get_secret",
        lambda key, default=None: value if key == "LOCAL_DEV_BYPASS_AUTH" else default,
    )

    assert auth_flow._is_local_dev_bypass() is False


@pytest.mark.parametrize("value", [True, "true", " TRUE "])
def test_local_dev_bypass_accepts_explicit_true_outside_production(monkeypatch, value):
    from tva_intracom.ui import auth_flow

    monkeypatch.setattr(
        auth_flow,
        "get_secret",
        lambda key, default=None: value if key == "LOCAL_DEV_BYPASS_AUTH" else default,
    )

    assert auth_flow._is_local_dev_bypass() is True


@pytest.mark.parametrize("environment_key", [
    "APP_ENV", "ENVIRONMENT", "NODE_ENV", "VERCEL_ENV", "RAILWAY_ENVIRONMENT_NAME",
])
def test_local_dev_bypass_is_disabled_in_production(monkeypatch, environment_key):
    from tva_intracom.ui import auth_flow

    secrets = {"LOCAL_DEV_BYPASS_AUTH": "true", environment_key: "production"}
    monkeypatch.setattr(auth_flow, "get_secret", lambda key, default=None: secrets.get(key, default))

    assert auth_flow._is_local_dev_bypass() is False


@pytest.mark.parametrize("cloud_marker", [
    "VERCEL_ENV", "RAILWAY_ENVIRONMENT_NAME", "K_SERVICE", "DYNO",
])
def test_local_dev_bypass_is_disabled_on_cloud_preview(monkeypatch, cloud_marker):
    from tva_intracom.ui import auth_flow

    secrets = {"LOCAL_DEV_BYPASS_AUTH": "true", cloud_marker: "preview"}
    monkeypatch.setattr(auth_flow, "get_secret", lambda key, default=None: secrets.get(key, default))

    assert auth_flow._is_local_dev_bypass() is False
