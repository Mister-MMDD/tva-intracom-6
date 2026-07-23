"""Exécution de calculs longs (validation VIES + moteur TVA) dans un thread
séparé, pour les gros fichiers.

Contexte : `compute_all_with_vies()` (engine.py) est déjà parallélisé en
interne pour les appels réseau VIES (ThreadPoolExecutor, voir
vies_engine.validate_vat_numbers_parallel) — le calcul lui-même n'est donc
pas le problème. Le problème est que tout cela s'exécute dans le thread
d'exécution du script Streamlit : le modèle Streamlit réexécute l'intégralité
du script à chaque interaction, et pendant qu'un `st.progress()` bloquant
tourne, l'utilisateur ne peut RIEN faire d'autre sur la page (cliquer un
onglet, ouvrir la sidebar...) — ce n'est pas un bug du code, c'est le
fonctionnement normal d'un script Streamlit synchrone.

Décision : PAS de task queue externe (Celery/RQ + broker Redis). Cela
suppose un worker persistant séparé du process Streamlit, absent du
déploiement actuel (Streamlit Cloud, un seul process) — ajouter cette brique
d'infra serait disproportionné pour un besoin de simple non-blocage de l'UI.

Solution retenue : un thread Python natif exécute le calcul ; un
`st.fragment(run_every=...)` relit périodiquement sa progression depuis
`st.session_state` (protégée par un verrou) et l'affiche, indépendamment du
reste du script. Le thread principal Streamlit reste ainsi libre de réagir
aux autres widgets. Pattern recommandé par la documentation Streamlit pour
ce cas précis (voir "Run long-running tasks" / fragments + threads).

Important : le thread lancé ici ne doit JAMAIS appeler st.* directement
(seulement écrire dans le `_JobState` sous verrou) — les appels Streamlit
depuis un thread autre que le thread de script ne sont pas garantis fiables.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

import streamlit as st


@dataclass
class _JobState:
    done: bool = False
    error: Optional[BaseException] = None
    result: Any = None
    progress: float = 0.0
    progress_text: str = ""
    started_at: float = field(default_factory=time.time)
    lock: threading.Lock = field(default_factory=threading.Lock)


def _session_key(job_id: str) -> str:
    return f"_bgjob_{job_id}"


def start_background_job(
    job_id: str,
    target_fn: Callable[[Callable[[float, str], None]], Any],
) -> None:
    """Démarre `target_fn` dans un thread séparé pour ce `job_id`, sauf s'il
    est déjà en cours (ou terminé) dans la session courante — un rerun
    Streamlit pendant l'exécution ne relance donc jamais un second thread
    pour le même job.

    `target_fn` reçoit un callback `report(progress: float, text: str)` à
    appeler pour publier son avancement, lu ensuite par
    `render_job_progress()`.
    """
    _skey = _session_key(job_id)
    if _skey in st.session_state:
        return

    state = _JobState()
    st.session_state[_skey] = state

    def _report(progress: float, text: str = "") -> None:
        with state.lock:
            state.progress = max(0.0, min(1.0, progress))
            state.progress_text = text

    def _runner() -> None:
        try:
            result = target_fn(_report)
            with state.lock:
                state.result = result
                state.progress = 1.0
        except BaseException as exc:  # noqa: BLE001 - remonté au thread principal, jamais avalé
            with state.lock:
                state.error = exc
        finally:
            with state.lock:
                state.done = True

    threading.Thread(target=_runner, daemon=True, name=f"bgjob-{job_id}").start()


def get_job_state(job_id: str) -> Optional[_JobState]:
    return st.session_state.get(_session_key(job_id))


def clear_job(job_id: str) -> None:
    st.session_state.pop(_session_key(job_id), None)


def is_job_done(job_id: str) -> bool:
    state = get_job_state(job_id)
    if state is None:
        return False
    with state.lock:
        return state.done


@st.fragment(run_every=0.4)
def render_job_progress(job_id: str, label: str) -> None:
    """Barre de progression qui se rafraîchit toute seule (0,4s) tant que le
    job tourne, sans bloquer ni rafraîchir le reste de la page. Une fois le
    job terminé, déclenche un rerun complet (hors fragment) pour que le
    script principal aille lire `get_job_state(job_id).result`.
    """
    state = get_job_state(job_id)
    if state is None:
        return
    with state.lock:
        _done, _progress, _text = state.done, state.progress, state.progress_text
    if _done:
        st.rerun()
        return
    _elapsed = time.time() - state.started_at
    _suffix = f" ({_elapsed:.0f}s)" if _elapsed >= 3 else ""
    st.progress(_progress, text=f"{label}{(' — ' + _text) if _text else ''}{_suffix}")
