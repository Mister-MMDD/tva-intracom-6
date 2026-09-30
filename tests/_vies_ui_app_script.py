"""Script Streamlit exécuté par AppTest (voir test_vies_ui_characterization.py).

Rend `render_vies(ctx)` avec toutes les dépendances externes (VIES, Postgres,
jobs en arrière-plan, génération PDF, facturation) remplacées par des doublures
pilotées via builtins.__vies_store__["scn"]. Ne pas importer ailleurs.
"""
import builtins
import threading
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import streamlit as st

from tva_intracom.models import ViesReclassification, ViesValidationSummary
from tva_intracom.ui import calc_cache
from tva_intracom.ui.tabs import vies_ui
from tva_intracom.ui.tabs.context import TabContext

store = builtins.__vies_store__
scn = store["scn"]
calls = MagicMock()
SCOPE = "scope1"


def _summary():
    s = scn.get("summary")
    if s is None:
        return None
    s = dict(s)
    rec = []
    for r in s.pop("reclass", []):
        r = dict(r)
        for k in ("amount_ht", "vat_avoided", "vat_delta"):
            if k in r:
                r[k] = Decimal(str(r[k]))
        rec.append(ViesReclassification(**r))
    return ViesValidationSummary(reclassifications=rec, **s)


def _sale(vat, country="DE"):
    return SimpleNamespace(sale=SimpleNamespace(buyer_vat_number=vat, buyer_country=country))


def _gated_download(label, data, file_name, mime, **kw):
    calls.gated_download(label, data.decode("utf-8"), file_name, mime)
    st.download_button(label, data=data, file_name=file_name, mime=mime, key="gd_test")


summary = _summary()
ctx = TabContext(
    results=[_sale(v, c) for v, c in scn.get("sale_vats", [])],
    refund_results=[_sale(v, c) for v, c in scn.get("refund_vats", [])] if scn.get("refund_vats") is not None else None,
    summary=None, vies_summary=summary, oss_summary=None,
    period_label="2026-Q1", period_detected_range=None,
    can_export=scn.get("can_export", True), billing_ok=True, account_link_blocked=False,
    gated_download=_gated_download, unlock_label_suffix="", lock_message="LOCKED-MSG",
    vies_scope_id=SCOPE, vies_retry_nonce=scn.get("nonce", 7), enable_vies=scn.get("enable_vies", True),
    is_admin=scn.get("is_admin", True), current_user_id="u1",
    nom_entreprise="ACME", siren_entreprise="111222333", tva_fr="FR1", countries_with_vat=["FR"],
    local_vat_numbers={}, all_fc_transfers=[], all_invoice_credit_notes=[], all_sales=[], platform_name="amazon",
)

inc = list(summary.inconclusive_vats) if summary else []
_jid = f"vies_retry_{SCOPE}_{'|'.join(sorted(inc))}" if inc else None

if not store.get("init_done"):
    store["init_done"] = True
    for _k, _v in scn.get("preset_ss", {}).items():
        st.session_state[_k] = _v
    job = scn.get("job")
    if job and _jid:
        st.session_state[f"_bgjob_{_jid}"] = SimpleNamespace(
            done=job.get("done", True), result=job.get("result"), lock=threading.Lock(),
            progress=0.0, progress_text="", rerun_triggered=True, error=None)
st.session_state["_tab_ctx"] = ctx


def _start(scope_id, vat_ids):
    calls.start_vies_retry_loop(scope_id, list(vat_ids))
    st.session_state[f"_bgjob_{_jid}"] = SimpleNamespace(
        done=False, result=None, lock=threading.Lock(), progress=0.0, progress_text="",
        rerun_triggered=False, error=None)
    return _jid


def _raise_or(name, ok):
    def f(*a, **k):
        calls.__getattr__(name)(*a, **k)
        beh = scn.get("beh", {}).get(name)
        if beh == "perm":
            raise PermissionError("perm-denied")
        if beh == "err":
            raise RuntimeError("boom-" + name)
        return ok
    return f


def _cert(kind):
    def f(rows, **kw):
        calls.__getattr__("pdf_" + kind)(len(rows), kw["company_name"], kw["siren"], kw["scope_id"], kw["period_label"])
        if scn.get("beh", {}).get("pdf") == "err":
            raise RuntimeError("pdf-boom")
        return b"%PDF-" + kind.encode()
    return f


_real_rerun = st.rerun


def _rerun(scope="app"):
    # AppTest rejoue le script ENTIER : `st.rerun(scope="fragment")` y est refusé
    # (réservé aux reruns de fragment réels). L'état de session étant déjà à jour,
    # un rerun complet donne le même écran final ; l'appel d'origine est journalisé.
    calls.rerun(scope)
    _real_rerun()


snap_rows = scn.get("snapshot", [{"vat_id": "DE111"}, {"vat_id": "DE222"}, {"vat_id": "FR333"}])
hist_rows = scn.get("history", [{"vat_id": "DE111"}])
overrides = [tuple(o) for o in scn.get("overrides", [])]

def _plotly(fig, **kw):
    # Le contenu du graphique (pays, montants, libellés) n'apparaît pas dans l'arbre AppTest.
    tr = fig.data[0]
    calls.plotly_chart(list(tr.x), [round(float(v), 4) for v in tr.y], list(tr.text), fig.layout.title.text)


patches = [
    patch("streamlit.rerun", _rerun),
    patch("streamlit.plotly_chart", _plotly),
    # @st.dialog n'est pas restitué par AppTest : on journalise l'ouverture du dialogue de succès.
    patch.object(vies_ui, "_render_vies_retry_done_dialog", side_effect=lambda *a: calls.retry_done_dialog(*a)),
    patch("tva_intracom.vies_engine.purge_expired_cache", side_effect=_raise_or("purge", 0)),
    patch("tva_intracom.vies_engine.set_manual_override", side_effect=_raise_or("set_override", None)),
    patch("tva_intracom.vies_engine.delete_manual_override", side_effect=_raise_or("del_override", None)),
    patch("tva_intracom.vies_engine.get_manual_overrides_full",
          side_effect=lambda scope: (_ for _ in ()).throw(RuntimeError("ov-boom")) if scn.get("beh", {}).get("get_overrides") == "err" else overrides),
    patch("tva_intracom.vies_engine._get_ttl_days", return_value=scn.get("ttl", 30)),
    patch("tva_intracom.vies_engine._is_expired", side_effect=lambda d, *a, **k: bool(d) and str(d).startswith("2020")),
    patch("tva_intracom.vies_engine.check_vat", side_effect=lambda c, n: (calls.check_vat(c, n), SimpleNamespace(
        valid=scn.get("vies_test_valid", True), name="ACME-TEST", error="down"))[1]),
    patch("tva_intracom.vies_engine.get_scope_vies_snapshot", side_effect=lambda scope: (calls.snapshot(scope), snap_rows)[1]),
    patch("tva_intracom.vies_engine.get_scope_vies_history_flat",
          side_effect=lambda scope, full_vats=None: (calls.history(scope, full_vats), hist_rows)[1]),
    patch("tva_intracom.vies_certificate.generate_vies_certificate_pdf", side_effect=_cert("snapshot")),
    patch("tva_intracom.vies_certificate.generate_vies_history_pdf", side_effect=_cert("history")),
    patch("tva_intracom.ui.background_calc.vies_retry_job_id",
          side_effect=lambda scope, vats: f"vies_retry_{scope}_{'|'.join(sorted(vats))}"),
    patch("tva_intracom.ui.background_calc.start_vies_retry_loop", side_effect=_start),
    patch("tva_intracom.ui.background_calc.render_job_progress",
          side_effect=lambda jid, label: st.caption(f"PROGRESS[{jid}] {label}")),
    patch.object(calc_cache.CalcCacheState, "save_vies_retry_nonce",
                 staticmethod(lambda n: (calls.save_nonce(n), st.session_state.__setitem__("_vies_retry_nonce", n))[0])),
    patch.object(calc_cache.CalcCacheState, "invalidate_calc",
                 staticmethod(lambda: (calls.invalidate_calc(), st.session_state.pop("_calc_key", None))[0])),
]
for p in patches:
    p.start()
try:
    vies_ui.render_vies(ctx)
finally:
    for p in patches:
        p.stop()
    store.setdefault("calls", []).extend(repr(c) for c in calls.mock_calls)
