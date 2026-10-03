"""Script AppTest : rend le stepper SIREN ou le wizard d'onboarding, puis applique
la purge de session d'app.py (aucun fichier uploadé) comme le fait le vrai run.

Piloté via st.session_state["_target"] ("stepper" | "wizard") et
st.session_state["_legacy_purge"] (True = ancienne boucle sans protection).
Ne pas importer ailleurs : n'a de sens que sous AppTest.
"""
from types import SimpleNamespace
from unittest.mock import patch

import streamlit as st

from tva_intracom.ui import onboarding as ob
from tva_intracom.ui import onboarding_wizard as wz
from tva_intracom.ui import sidebar as sb
from tva_intracom.ui.session_guard import purge_stale_session_keys

_WHITELIST = {"_target", "_legacy_purge", "_calls", "language"}
st.session_state.setdefault("_calls", [])
user = SimpleNamespace(id="u1", org_id="org1", role="admin", onboarding_seen=False)

if st.session_state["_target"] == "stepper":
    with patch.object(
        sb.tva_billing, "register_siren",
        side_effect=lambda *a, **k: st.session_state["_calls"].append(("register", a[2])),
    ):
        sb._new_siren_stepper_fragment(current_user=user, home_country="FR", siren_options=[])
else:
    with patch.object(
        ob.tva_auth, "set_onboarding_seen",
        side_effect=lambda uid, seen: st.session_state["_calls"].append(("seen", uid, seen)),
    ):
        wz.render_onboarding_wizard(user)

if st.session_state.get("_legacy_purge"):
    for _k in list(st.session_state.keys()):
        if _k not in _WHITELIST:
            st.session_state.pop(_k, None)
else:
    purge_stale_session_keys(st.session_state, _WHITELIST)
