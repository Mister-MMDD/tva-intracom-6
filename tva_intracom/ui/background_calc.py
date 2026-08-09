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

from .. import auth as _tva_auth
from .. import billing as _tva_billing
from .. import ecb_rates as _tva_ecb_rates
from .. import vies_engine as _tva_vies_engine

# Les 4 modules utilisant `NonPoolingConnectionPool(cache_connection=True)` :
# chacun met en cache SA connexion dans le `threading.local()` du thread
# appelant (voir database.py). app.py appelle leurs `close_idle_connections()`
# respectifs en tout début de CHAQUE run, mais uniquement depuis le thread
# principal du script Streamlit — jamais depuis un thread `bgjob-*` lancé
# ci-dessous. Sans l'appel explicite dans `_runner()` (voir plus bas), la
# connexion ouverte par ce thread (ex. par compute_all_with_vies) ne serait
# fermée que lorsque le thread se termine ET que Python collecte l'objet
# (comportement de `__del__` de psycopg2 — vérifié empiriquement comme fiable
# en pratique, mais implicite et non garanti par contrat). L'appeler ici la
# rend déterministe et immédiate, sans dépendre du timing du GC.
_CLOSE_FNS = (
    _tva_auth.close_idle_connections,
    _tva_billing.close_idle_connections,
    _tva_ecb_rates.close_idle_connections,
    _tva_vies_engine.close_idle_connections,
)

# Compteur global (process entier, toutes sessions confondues) de jobs en
# cours — volontairement un compteur module-level protégé par verrou, PAS
# dans st.session_state (qui est par session, donc invisible d'une session à
# l'autre). Sert de garde-fou pour ne jamais fermer les pools de connexions
# DB (voir app.py, fin de script) pendant qu'un thread de calcul est encore
# en train de s'en servir (ex. vies_engine via compute_all_with_vies).
_active_jobs_lock = threading.Lock()
_active_jobs_count = 0

# Plafond de gros calculs (VIES + moteur TVA sur > 20k lignes, voir
# app.py/_BIG_FILE_ROW_THRESHOLD) exécutés SIMULTANÉMENT, tous utilisateurs
# confondus sur ce process. Chaque job garde en RAM la totalité des Sale +
# VatResult du fichier (potentiellement plusieurs dizaines de Mo pour un
# fichier de 100 Mo) pendant toute sa durée de vie -- un TTL/cache_data ne
# peut rien faire ici : ce sont des utilisateurs ACTIFS en cours de calcul,
# pas des entrées obsolètes à évincer. Le seul levier réel pour borner le pic
# mémoire face à plusieurs gros uploads concurrents est de plafonner ce
# nombre de jobs simultanés. Valeur conservatrice, ajustable selon la RAM
# réellement disponible sur l'instance Railway.
MAX_CONCURRENT_BIG_JOBS = 3


def any_job_running() -> bool:
    with _active_jobs_lock:
        return _active_jobs_count > 0


def can_start_big_job() -> bool:
    """True si un nouveau job "gros fichier" peut démarrer sans dépasser
    MAX_CONCURRENT_BIG_JOBS. Purement informatif/best-effort (pas de réservation
    de slot) : deux appels concurrents peuvent en théorie tous les deux voir
    de la marge et démarrer, auquel cas le compteur dépasse temporairement le
    plafond de 1. Accepté comme compromis simple plutôt que d'introduire un
    verrou tenu entre la vérification et le démarrage effectif du thread."""
    with _active_jobs_lock:
        return _active_jobs_count < MAX_CONCURRENT_BIG_JOBS


@dataclass
class _JobState:
    done: bool = False
    error: Optional[BaseException] = None
    result: Any = None
    progress: float = 0.0
    progress_text: str = ""
    started_at: float = field(default_factory=time.time)
    lock: threading.Lock = field(default_factory=threading.Lock)
    rerun_triggered: bool = False


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
        global _active_jobs_count
        with _active_jobs_lock:
            _active_jobs_count += 1
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
            with _active_jobs_lock:
                _active_jobs_count -= 1
            # Ferme explicitement les connexions DB mises en cache par CE
            # thread (voir commentaire de _CLOSE_FNS plus haut) — ce thread
            # va mourir juste après, autant fermer proprement tout de suite
            # plutôt que de compter sur le GC.
            for _close_fn in _CLOSE_FNS:
                try:
                    _close_fn()
                except Exception:
                    pass

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
        _already_triggered = state.rerun_triggered
        if _done and not _already_triggered:
            state.rerun_triggered = True
    if _done:
        # Le rerun complet n'est déclenché qu'une seule fois par job : le
        # timer run_every de ce fragment est indépendant du thread principal
        # et peut re-tiquer plusieurs fois avant que le rerun précédent
        # n'ait eu le temps d'atteindre clear_job() côté script principal.
        # Sans ce garde-fou, chaque tick relance un st.rerun() complet,
        # recrée un nouveau placeholder (st.empty()) et abandonne l'ancien
        # fragment, qui continue de tourner tout seul indéfiniment
        # ("fragment does not exist anymore" en boucle).
        if not _already_triggered:
            st.rerun()
        return
    _elapsed = time.time() - state.started_at
    _suffix = f" ({_elapsed:.0f}s)" if _elapsed >= 3 else ""
    st.progress(_progress, text=f"{label}{(' — ' + _text) if _text else ''}{_suffix}")
