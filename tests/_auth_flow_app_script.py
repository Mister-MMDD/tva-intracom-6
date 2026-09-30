"""Script Streamlit exécuté par AppTest (voir test_auth_flow_characterization.py).

Rend `run_auth_flow()` avec TOUTES les dépendances externes (Supabase, Postgres,
cookies, en-têtes HTTP, horloge, aléa) remplacées par des doublures pilotées
via st.session_state["_scn"]. Ne pas importer ailleurs.
"""

from datetime import datetime
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import streamlit as st

from tva_intracom.ui import auth_flow as af

import builtins

# Le test dépose ses paramètres dans builtins.__af_store__ (et NON dans
# st.session_state) : la déconnexion vide session_state, ce qui effacerait le
# scénario en cours de route.
store = builtins.__af_store__
scn = store["scn"]
calls = MagicMock()

if not store.get("init_done"):
    store["init_done"] = True
    for _k, _v in scn.get("preset_ss", {}).items():
        st.session_state[_k] = _v
    if scn.get("logged_in"):
        st.session_state["auth_user"] = SimpleNamespace(
            id="u1", email="user@example.com", org_id="org1", role="admin")


def mk(name, **kw):
    m = MagicMock(**kw)
    calls.attach_mock(m, name)
    return m


USER = SimpleNamespace(id="u1", email="user@example.com", org_id="org1", role="admin")


class FakeCM:
    def get(self, name=None):
        calls.cm_get(name)
        return scn.get("cm_cookie")

    def get_all(self):
        calls.cm_get_all()
        return {}

    def set(self, name, value, expires_at=None, **kw):
        days = None if expires_at is None else round((expires_at - datetime.now()).total_seconds() / 86400)
        calls.cm_set(name, value, days)

    def delete(self, name, **kw):
        calls.cm_delete(name)


cm = FakeCM()


def _res(email="user@example.com", token="ACCESS"):
    return SimpleNamespace(email=email, access_token=token)


def _beh(key, ok_value, err=RuntimeError("boom")):
    b = scn.get("sb", {}).get(key, "ok")
    if b == "err":
        raise err
    return ok_value


sb = scn.get("sb", {})
magic = scn.get("magic", "user")


def _magic(token, ip_address="unknown"):
    calls.consume_magic_link(token, ip_address)
    if magic == "perm":
        raise PermissionError("blocked")
    if magic == "err":
        raise RuntimeError("magic-boom")
    return None if magic == "none" else USER


def _goc(email):
    calls.get_or_create_user(email)
    if scn.get("goc_error"):
        raise PermissionError("not-allowed")
    return USER


def _verifier_db(nonce, provider):
    calls.consume_pkce_verifier(nonce, provider)
    v = scn.get("verifier_db", "V-DB")
    if v == "lookup_error":
        raise LookupError("diag-info")
    return v


org = scn.get("org", {})


def _sub(org_id):
    if org.get("err"):
        raise RuntimeError("sub-boom")
    return SimpleNamespace(active=org.get("sub_active", False))


secrets_map = scn.get("secrets", {})
patches = [
    patch.object(af.st, "context", SimpleNamespace(
        cookies=dict(scn.get("cookies", {})), headers=dict(scn.get("headers", {})),
        ip_address=scn.get("ip", "1.2.3.4"))),
    patch.object(af, "get_secret", side_effect=lambda k, d=None: secrets_map.get(k, d)),
    patch.object(af.time, "sleep", lambda *_: None),
    patch.object(af.secrets, "token_urlsafe", lambda n=32: "NONCE-FIXED-0123456789ABCDEF"),
    patch.object(af, "_vies_resolve_scope_id", lambda email: f"scope:{email}"),
    patch("tva_intracom.mem_utils.release_memory", mk("release_memory")),
    patch.object(af.tva_auth, "get_user_by_session_token",
                 mk("get_user_by_session_token", side_effect=lambda t: USER if scn.get("restore_user") else None)),
    patch.object(af.tva_auth, "get_or_create_user", _goc),
    patch.object(af.tva_auth, "create_session_token", mk("create_session_token", return_value="SESSION-TOKEN")),
    patch.object(af.tva_auth, "delete_session_token", mk("delete_session_token")),
    patch.object(af.tva_auth, "consume_magic_link", _magic),
    patch.object(af.tva_auth, "can_signup", mk("can_signup", return_value=tuple(scn.get("can_signup", (True, None))))),
    patch.object(af.tva_auth, "create_magic_link", mk("create_magic_link", return_value="MAGIC-TOK")),
    patch.object(af.tva_auth, "send_magic_link_email", mk(
        "send_magic_link_email", side_effect=(RuntimeError("smtp") if sb.get("send_magic") == "err" else None))),
    patch.object(af.tva_auth, "save_pkce_verifier", mk(
        "save_pkce_verifier", side_effect=(RuntimeError("pkce-save") if sb.get("save_pkce") == "err" else None))),
    patch.object(af.tva_auth, "consume_pkce_verifier", _verifier_db),
    patch.object(af.tva_auth, "consume_latest_pkce_verifiers_by_provider",
                 mk("consume_latest_pkce_verifiers", return_value=list(scn.get("candidates", [])))),
    patch.object(af.tva_auth, "is_org_locked", mk("is_org_locked", return_value=org.get("locked", False))),
    patch.object(af.tva_auth, "is_solo_org", mk("is_solo_org", return_value=org.get("solo", False))),
    patch.object(af.tva_auth, "lock_org_for_user", mk("lock_org_for_user")),
    patch.object(af.tva_billing, "get_subscription_status", _sub),
    patch.object(af.tva_sb_auth, "sign_in_with_password", mk(
        "sign_in", side_effect=lambda e, p: _beh("sign_in", _res(e)))),
    patch.object(af.tva_sb_auth, "sign_up_with_password", mk(
        "sign_up", side_effect=lambda e, p: _beh("sign_up", _res(e, token=None if sb.get("sign_up") == "notoken" else "ACC")))),
    patch.object(af.tva_sb_auth, "reset_password_for_email", mk(
        "reset_email", side_effect=lambda e, **k: _beh("reset_email", None))),
    patch.object(af.tva_sb_auth, "update_user_password", mk(
        "update_password", side_effect=lambda t, p: _beh("update_pwd", {}))),
    patch.object(af.tva_sb_auth, "new_code_verifier", lambda: "VERIFIER-FIXED"),
    patch.object(af.tva_sb_auth, "build_oauth_authorize_url", lambda prov, redir, v: (
        (_ for _ in ()).throw(RuntimeError("oauth-url")) if sb.get("oauth_url") == "err"
        else f"https://sb.example/authorize?p={prov}&r={redir}&v={v}")),
    patch.object(af.tva_sb_auth, "exchange_pkce_code", mk(
        "exchange_pkce", side_effect=lambda c, v, redirect_uri=None: (
            _beh("exchange_" + str(v), _res()) if sb.get("exchange_by_verifier") else _beh("exchange", _res())))),
    patch.object(af.tva_sb_auth, "get_user_from_access_token", mk(
        "user_from_token", side_effect=lambda t: _beh("access_token_user", _res()))),
]
for p in patches:
    p.start()
try:
    ctx = af.run_auth_flow(cm)
    store["ret"] = {
        "email": ctx.current_user.email, "app_base_url": ctx.app_base_url,
        "vies_scope_id": ctx.vies_scope_id,
        "success_url": ctx.stripe_success_url("x=1"), "cancel_url": ctx.stripe_cancel_url(),
    }
finally:
    for p in patches:
        p.stop()
    store.setdefault("calls", []).extend(repr(c) for c in calls.mock_calls)
