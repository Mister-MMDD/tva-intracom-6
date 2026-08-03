"""Tests de robustesse pour `NonPoolingConnectionPool(cache_connection=True)`
sous usage multi-thread.

Contexte : ce mode (utilisé par auth.py, billing.py, ecb_rates.py, et depuis
le 2026-08-02 vies_engine.py — voir database.py pour la justification du
changement) met en cache une connexion par thread via `threading.local()`.
Deux risques distincts à couvrir :

1. Isolation : deux threads concurrents (ex. deux jobs "gros fichier" pour
   deux utilisateurs différents lancés en même temps par
   ui/background_calc.py) ne doivent JAMAIS se partager une connexion.
2. Fuite : un thread `bgjob-*` (voir background_calc.py) est créé À CHAQUE
   job, jamais réutilisé — il faut s'assurer que la connexion qu'il a mise en
   cache est bien fermée quand il se termine, et pas seulement "perdue" côté
   process jusqu'à un hypothétique passage du GC.

Ces tests utilisent un faux `psycopg2.connect` (pas de dépendance réseau/DB
réelle) pour rester rapides et déterministes en CI.
"""
from __future__ import annotations

import threading
import time
from unittest.mock import patch

import pytest

from tva_intracom.database import NonPoolingConnectionPool
import tva_intracom.database as database_module


class _FakeConn:
    """Connexion factice : trace qui l'a ouverte, l'a fermée, et si elle a
    été explicitement fermée (`close()`) ou seulement laissée à la merci du
    GC (`__del__`, ce que fait réellement psycopg2 en interne)."""

    def __init__(self, thread_name: str, closed_log: list, del_log: list):
        self.thread_name = thread_name
        self.closed = 0
        self._closed_log = closed_log
        self._del_log = del_log

    def close(self):
        self.closed = 1
        self._closed_log.append(("explicit_close", threading.current_thread().name))

    def __del__(self):
        # Reproduit le comportement documenté de psycopg2 : `__del__` ferme
        # la connexion si elle ne l'était pas déjà.
        if self.closed == 0:
            self.closed = 1
            self._del_log.append(("gc___del__", threading.current_thread().name))


@pytest.fixture
def fake_connect(monkeypatch):
    """Remplace psycopg2.connect par une factory de _FakeConn, et fournit les
    deux listes d'événements (fermeture explicite / fermeture via GC)."""
    closed_log: list = []
    del_log: list = []
    opened: list = []
    lock = threading.Lock()

    def _connect(*_args, **_kwargs):
        conn = _FakeConn(threading.current_thread().name, closed_log, del_log)
        with lock:
            opened.append(conn)
        return conn

    monkeypatch.setattr(database_module.psycopg2, "connect", _connect)
    return {"closed_log": closed_log, "del_log": del_log, "opened": opened}


def test_cache_connection_isolated_across_concurrent_threads(fake_connect):
    """5 threads concurrents (barrière pour forcer un vrai chevauchement) ne
    doivent jamais voir la connexion d'un autre thread — chacun doit obtenir
    et garder SA propre connexion tout du long."""
    pool = NonPoolingConnectionPool("fake-dsn", cache_connection=True)
    n_threads = 5
    barrier = threading.Barrier(n_threads)
    errors: list[str] = []

    def _job(n: int) -> None:
        try:
            barrier.wait(timeout=5)
            my_name = threading.current_thread().name
            for _ in range(20):
                conn = pool.getconn()
                if conn.thread_name != my_name:
                    errors.append(f"thread {my_name} a reçu la connexion de {conn.thread_name}")
                pool.putconn(conn)
                time.sleep(0.001)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"exception dans le thread {n}: {exc!r}")

    threads = [threading.Thread(target=_job, args=(i,), name=f"bgjob-{i}") for i in range(n_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)

    assert not errors, f"Fuite d'isolation entre threads détectée : {errors}"
    # Exactement 1 connexion ouverte par thread (jamais plus, jamais partagée).
    assert len(fake_connect["opened"]) == n_threads


def test_background_job_thread_connection_is_closed_without_close_idle(monkeypatch):
    """Sans l'appel explicite ajouté dans background_calc.py : la connexion
    mise en cache par un thread de job doit malgré tout finir fermée dès que
    le thread se termine et que la référence Python retombe à zéro — ceci
    documente/valide le filet de sécurité implicite (comportement `__del__`
    de psycopg2), qui existait déjà AVANT le nettoyage explicite ajouté.

    Important : contrairement à la fixture `fake_connect` utilisée par les
    autres tests de ce module, on ne garde ICI aucune référence forte vers la
    connexion créée (pas de liste `opened` qui l'accumulerait) — sinon le GC
    ne pourrait jamais la collecter, et ce test ne testerait plus rien."""
    closed_log: list = []
    del_log: list = []

    def _connect(*_args, **_kwargs):
        return _FakeConn(threading.current_thread().name, closed_log, del_log)

    monkeypatch.setattr(database_module.psycopg2, "connect", _connect)
    pool = NonPoolingConnectionPool("fake-dsn", cache_connection=True)

    def _job() -> None:
        conn = pool.getconn()
        pool.putconn(conn)  # no-op volontaire : reste en cache pour ce thread

    t = threading.Thread(target=_job, name="bgjob-no-explicit-close", daemon=True)
    t.start()
    t.join(timeout=5)

    import gc
    time.sleep(0.05)
    gc.collect()

    assert closed_log == [], (
        "Ce test documente le filet de sécurité implicite : aucune fermeture "
        "explicite ne doit avoir eu lieu ici (pas de close_idle_connections appelé)."
    )
    assert len(del_log) == 1
    assert del_log[0][0] == "gc___del__"


def test_background_calc_runner_closes_connections_explicitly():
    """Le `_runner()` de background_calc.py doit maintenant appeler les 4
    `close_idle_connections()` (auth, billing, ecb_rates, vies_engine) dans
    son bloc finally, pour fermer la connexion du thread de job de façon
    déterministe plutôt que de compter sur le GC (voir _CLOSE_FNS)."""
    from tva_intracom.ui import background_calc

    calls: list[str] = []

    def _make_tracker(name):
        def _tracker():
            calls.append(name)
        return _tracker

    with patch.object(background_calc, "_CLOSE_FNS", (
        _make_tracker("auth"),
        _make_tracker("billing"),
        _make_tracker("ecb_rates"),
        _make_tracker("vies_engine"),
    )):
        state_holder = {}

        def _target(report):
            report(0.5, "en cours")
            return "resultat-ok"

        # start_background_job() ne dépend que de `st.session_state` (dict-like).
        # On remplace tout le module `st` vu par background_calc par un objet
        # minimal, pour ne pas dépendre d'un vrai ScriptRunContext Streamlit
        # (absent hors `streamlit run` / AppTest) — @st.fragment n'est jamais
        # invoqué par ce test (seul render_job_progress l'utilise).
        class _FakeSt:
            session_state: dict = {}

        with patch.object(background_calc, "st", _FakeSt):
            background_calc.start_background_job("job-test-1", _target)
            # Attend la fin du thread daemon lancé par start_background_job.
            for _ in range(200):
                state = background_calc.get_job_state("job-test-1")
                if state is not None:
                    with state.lock:
                        if state.done:
                            break
                time.sleep(0.01)
            else:
                pytest.fail("Le job de test ne s'est jamais terminé (timeout).")

            state = background_calc.get_job_state("job-test-1")
            with state.lock:
                assert state.error is None
                assert state.result == "resultat-ok"

    assert set(calls) == {"auth", "billing", "ecb_rates", "vies_engine"}, (
        f"Les 4 close_idle_connections() doivent être appelés depuis le "
        f"thread du job ; obtenu : {calls}"
    )
