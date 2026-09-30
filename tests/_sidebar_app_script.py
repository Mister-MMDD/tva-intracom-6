"""Script Streamlit exécuté par AppTest (voir test_sidebar_characterization.py).

Rend `render_sidebar()` avec toutes les dépendances externes (Postgres, VIES,
Stripe) remplacées par des doublures pilotées via st.session_state["_scn"].
Ne pas importer ailleurs : ce fichier n'a de sens que sous AppTest.
"""
import dataclasses
import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import streamlit as st

from tva_intracom.ui import sidebar as sb

scn = st.session_state["_scn"]
user = SimpleNamespace(
    id="u1", org_id="org1", email="a@b.c", role=scn.get("role", "admin"),
    home_country=st.session_state.get("_persist_home", scn.get("home_country", "FR")),
    display_currency=st.session_state.get("_persist_cur", scn.get("display_currency", "DEFAULT")),
)
auth_ctx = SimpleNamespace(
    current_user=user, vies_scope_id="scope1",
    stripe_success_url=lambda q="": "https://x/ok", stripe_cancel_url=lambda: "https://x/ko",
)

quota = sb.tva_billing.SirenQuotaStatus(
    registered_count=len(scn.get("sirens", [])), quota=scn.get("quota", 1),
    over_quota_by=max(0, len(scn.get("sirens", [])) - scn.get("quota", 1)),
)
calls = MagicMock()


def _sirens():
    if scn.get("sirens_error"):
        raise RuntimeError("db down")
    out = []
    for r in scn.get("sirens", []):
        r = dict(r)
        # État "en base" modifié par les actions (retrait demandé / annulé) : rend
        # visible, au rerun suivant, l'oubli d'invalider le cache de lecture.
        if r["siren"] in st.session_state.get("_persist_pending", {}):
            r["pending_removal_at"] = st.session_state["_persist_pending"][r["siren"]]
        out.append(r)
    return out


stats = dict(scn.get("vies_stats", {
    "ttl_days": 7, "total": 10, "fresh": 6, "expired": 4, "valid": 5, "invalid": 1,
    "oldest_check": "2026-01-02T00:00:00", "manual_total": 2, "manual_valid": 1, "manual_invalid": 1,
}))
stats["ttl_days"] = st.session_state.get("_persist_ttl", stats["ttl_days"])
vstats = MagicMock(return_value=stats)

if scn.get("detailed"):
    st.session_state["display_mode"] = "detaille"

patches = [
    patch.object(sb.tva_billing, "list_registered_sirens", side_effect=lambda org: _sirens()),
    patch.object(sb.tva_billing, "get_siren_quota_status", side_effect=lambda org: quota),
    patch.object(sb.tva_billing, "can_register_new_siren",
                 return_value=tuple(scn.get("can_add", (True, "")))),
    patch.object(sb.tva_billing, "request_siren_removal", side_effect=lambda *a: (calls.request_removal(*a), st.session_state.setdefault("_persist_pending", {}).__setitem__(a[2], scn.get("removal_eff", 1893499200)), scn.get("removal_eff", 1893499200))[2]),
    patch.object(sb.tva_billing, "cancel_siren_removal", side_effect=lambda *a: (calls.cancel_removal(*a), st.session_state.setdefault("_persist_pending", {}).__setitem__(a[2], None))),
    patch.object(sb.tva_billing, "is_payg_removal_over_quota", return_value=scn.get("payg_over", False)),
    patch.object(sb.tva_auth, "set_home_country", side_effect=lambda *a: (calls.set_home(*a), st.session_state.__setitem__("_persist_home", a[1]))),
    patch.object(sb.tva_auth, "set_display_currency", side_effect=lambda *a: (calls.set_cur(*a), st.session_state.__setitem__("_persist_cur", a[1]))),
    patch.object(sb, "vies_cache_stats", vstats),
    patch.object(sb, "set_cache_ttl", side_effect=lambda *a, **k: (calls.set_ttl(a, k), st.session_state.__setitem__("_persist_ttl", a[1]))),
    patch.object(sb, "purge_expired_cache", side_effect=lambda *a, **k: (calls.purge(a, k), 4)[1]),
    patch("tva_intracom.vies_engine.get_scope_vies_snapshot", return_value=scn.get("snapshot", [{"v": 1}])),
    patch("tva_intracom.vies_engine.get_scope_vies_history_flat", return_value=scn.get("history", [{"v": 1}])),
    patch("tva_intracom.vies_certificate.generate_vies_certificate_pdf", return_value=b"%PDF-snapshot"),
    patch("tva_intracom.vies_certificate.generate_vies_history_pdf", return_value=b"%PDF-history"),
]
for p in patches:
    p.start()
try:
    res = sb.render_sidebar(auth_ctx, pulse_target=scn.get("pulse"))
finally:
    for p in patches:
        p.stop()
    # st.rerun() (RerunException) court-circuite la suite : on journalise ici.
    _log = st.session_state.setdefault("_calls_log", [])
    _log.extend(repr(c) for c in calls.mock_calls)
    st.session_state.setdefault("_vstats_log", []).extend(repr(c) for c in vstats.mock_calls)

d = dataclasses.asdict(res)
st.session_state["_out"] = json.loads(json.dumps(d, default=repr))
st.session_state["_keys"] = sorted(
    k for k in st.session_state.keys()
    if not str(k).startswith(("_scn", "_out", "_calls_log", "_vstats_log", "_keys", "_persist", "$$"))
)
