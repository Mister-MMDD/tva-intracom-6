"""Instrumentation de performance — logging pur, sans effet de bord.

Objectif : mesurer où passe le temps (parsing, VIES, moteur TVA, appels DB,
export Excel/PDF...) pour identifier les délais perçus comme longs par
l'utilisateur, sur la base de retours empiriques plutôt que de suppositions.

IMPORTANT (scale-to-zero Railway) : ce module ne fait QUE des `logging.*`
synchrones dans le thread appelant. Aucun thread, timer, `run_every`, ou
appel réseau n'est ajouté ici — donc aucun impact sur la détection
d'inactivité par Railway. Se contenter de logs texte, jamais de polling.

Usage :
    from tva_intracom.perf_log import timeit, timed

    @timeit()                      # utilise le nom qualifié de la fonction
    def ma_fonction(...): ...

    @timeit("parsing_amazon")      # label explicite
    def load_amazon_report(...): ...

    with timed("boucle_parsing_fichiers"):
        ...

Format de sortie (logger "perf", niveau INFO) :
    2026-08-03 10:00:00 [INFO] perf: ma_fonction — 123.4 ms
    2026-08-03 10:00:00 [INFO] perf: ma_fonction — 4200.1 ms  [LENT >1s]

Activation/désactivation : `PERF_LOG_ENABLED` (variable d'env, "0" pour
désactiver totalement sans retirer les décorateurs) et `PERF_LOG_SLOW_MS`
(seuil, en ms, au-delà duquel la ligne est marquée "[LENT]" — 1000 par
défaut) permettent de filtrer le bruit une fois les gros points identifiés,
sans repasser sur tout le code.
"""
from __future__ import annotations

import functools
import logging
import os
import threading
import time
from contextlib import contextmanager
from typing import Callable, Optional, TypeVar

logger = logging.getLogger("perf")

_ENABLED = os.environ.get("PERF_LOG_ENABLED", "1") != "0"
try:
    _SLOW_MS = float(os.environ.get("PERF_LOG_SLOW_MS", "1000"))
except ValueError:
    _SLOW_MS = 1000.0

F = TypeVar("F", bound=Callable)


def _fmt(label: str, elapsed_ms: float, extra: str = "") -> str:
    tag = "  [LENT]" if elapsed_ms >= _SLOW_MS else ""
    thread_name = threading.current_thread().name
    suffix = f" ({extra})" if extra else ""
    return f"{label}{suffix} — {elapsed_ms:.1f} ms [{thread_name}]{tag}"


@contextmanager
def timed(label: str, extra: str = ""):
    """Context manager : mesure et logue le temps passé dans le bloc `with`.

    N'avale jamais les exceptions : le temps écoulé est loggé même en cas
    d'erreur (utile pour repérer les timeouts DB/HTTP plutôt qu'un simple
    silence).
    """
    if not _ENABLED:
        yield
        return
    _t0 = time.perf_counter()
    try:
        yield
    except Exception:
        _elapsed = (time.perf_counter() - _t0) * 1000
        logger.info(_fmt(label, _elapsed, extra) + "  [EXCEPTION]")
        raise
    else:
        _elapsed = (time.perf_counter() - _t0) * 1000
        logger.info(_fmt(label, _elapsed, extra))


def timeit(label: Optional[str] = None, min_ms: float = 0.0) -> Callable[[F], F]:
    """Décorateur : mesure et logue le temps d'exécution d'une fonction.

    Usage : @timeit() ou @timeit("mon_label"). Sans argument, utilise
    `module.qualname` de la fonction décorée.

    `min_ms` : n'écrit une ligne de log que si l'appel a pris AU MOINS
    `min_ms` millisecondes. Utile pour des fonctions appelées très souvent et
    presque toujours triviales une fois le cache mémoire chaud (ex.
    ecb_rates.get_rate, appelée des dizaines de fois par run, à 0.0 ms dès
    que le taux est en cache L1) : sans ce filtre, ces lignes noient le
    signal utile dans le bruit une fois le point déjà investigué/confirmé
    (voir historique des investigations perf du 2026-08). Si l'appel
    redevient lent un jour (nouvelle cause), il continue d'apparaître
    normalement — seul le cas "rapide et répétitif" est masqué.
    """
    def _decorator(fn: F) -> F:
        _label = label or f"{fn.__module__}.{fn.__qualname__}"

        @functools.wraps(fn)
        def _wrapper(*args, **kwargs):
            if not _ENABLED:
                return fn(*args, **kwargs)
            _t0 = time.perf_counter()
            try:
                return fn(*args, **kwargs)
            finally:
                _elapsed = (time.perf_counter() - _t0) * 1000
                if _elapsed >= min_ms:
                    logger.info(_fmt(_label, _elapsed))
        return _wrapper  # type: ignore[return-value]
    return _decorator
