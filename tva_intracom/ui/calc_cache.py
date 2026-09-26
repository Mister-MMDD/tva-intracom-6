"""État de cache du pipeline parsing + calcul TVA, regroupé dans une
dataclass plutôt que dispersé en clés `st.session_state` individuelles
(`_parse_cache_key`, `_cache_key`/`_calc_key`, `_period_sync_key`,
`_results`, etc.).

Comportement strictement identique à l'ancien code (extrait de app.py) :
seule la façade change. Deux points volontairement PAS regroupés ici après
cartographie complète des usages (voir README - évolution.md) :

- `_parse_cache_key` / `_parse_cache_data` (résultat du parsing des fichiers
  importés) et `_calc_key` / résultats de calcul (`_results`, `_summary`...)
  restent deux groupes distincts dans la dataclass ET dans session_state :
  ce sont deux caches indépendants avec des clés de cache différentes (le
  parsing ne dépend pas de `enable_vies`/`target_currency`/`oss_period`
  contrairement au calcul), les fusionner forcerait un recalcul complet à
  chaque fois qu'un des deux invalide, ce qui n'est pas le comportement
  actuel.
- `invalidate_calc()` invalide UNIQUEMENT `calc_key` (voir vies_ui.py) —
  PAS `results`/`summary`/`vies_summary`/`oss_summary`, qui restent affichés
  jusqu'au recalcul suivant (évite un flash d'interface vide pendant que
  l'utilisateur classe manuellement un numéro TVA). Ne pas "nettoyer" ce
  comportement en ajoutant un clear complet ici : c'est un choix UX
  volontaire, pas un oubli.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

import streamlit as st

_SS_PARSE_KEY = "_parse_cache_key"
_SS_PARSE_DATA = "_parse_cache_data"
_SS_CALC_KEY = "_calc_key"
_SS_PERIOD_SYNC_KEY = "_period_sync_key"
_SS_RESULTS = "_results"
_SS_REFUND_RESULTS = "_refund_results"
_SS_SUMMARY = "_summary"
_SS_VIES_SUMMARY = "_vies_summary"
_SS_OSS_SUMMARY = "_oss_summary"
_SS_VIES_RETRY_NONCE = "_vies_retry_nonce"


@dataclass
class CalcCacheState:
    """Snapshot de session_state pour le pipeline parsing + calcul TVA.

    Ne PAS instancier directement pour lire l'état courant : utiliser
    `CalcCacheState.load()`, qui reflète exactement `st.session_state` au
    moment de l'appel (des widgets ailleurs dans le script peuvent l'avoir
    modifié entre deux lectures).
    """
    parse_key: Optional[tuple] = None
    parse_data: Optional[tuple] = None
    calc_key: Optional[tuple] = None
    period_sync_key: Optional[tuple] = None
    results: list = field(default_factory=list)
    refund_results: list = field(default_factory=list)
    summary: Any = None
    vies_summary: Any = None
    oss_summary: Any = None
    vies_retry_nonce: int = 0

    @classmethod
    def load(cls) -> "CalcCacheState":
        ss = st.session_state
        return cls(
            parse_key=ss.get(_SS_PARSE_KEY),
            parse_data=ss.get(_SS_PARSE_DATA),
            calc_key=ss.get(_SS_CALC_KEY),
            period_sync_key=ss.get(_SS_PERIOD_SYNC_KEY),
            results=ss.get(_SS_RESULTS, []),
            refund_results=ss.get(_SS_REFUND_RESULTS, []),
            summary=ss.get(_SS_SUMMARY),
            vies_summary=ss.get(_SS_VIES_SUMMARY),
            oss_summary=ss.get(_SS_OSS_SUMMARY),
            vies_retry_nonce=ss.get(_SS_VIES_RETRY_NONCE, 0),
        )

    # -- Parsing -------------------------------------------------------
    @staticmethod
    def save_parse(parse_key: tuple, parse_data: tuple) -> None:
        st.session_state[_SS_PARSE_KEY] = parse_key
        st.session_state[_SS_PARSE_DATA] = parse_data

    # -- Calcul ----------------------------------------------------------
    @staticmethod
    def save_calc(calc_key: tuple, results: list, refund_results: list,
                   summary: Any, vies_summary: Any, oss_summary: Any) -> None:
        st.session_state[_SS_CALC_KEY] = calc_key
        st.session_state[_SS_RESULTS] = results
        st.session_state[_SS_REFUND_RESULTS] = refund_results
        st.session_state[_SS_SUMMARY] = summary
        st.session_state[_SS_VIES_SUMMARY] = vies_summary
        st.session_state[_SS_OSS_SUMMARY] = oss_summary

    @staticmethod
    def save_period_sync_key(calc_key: tuple) -> None:
        st.session_state[_SS_PERIOD_SYNC_KEY] = calc_key

    @staticmethod
    def save_vies_retry_nonce(nonce: int) -> None:
        st.session_state[_SS_VIES_RETRY_NONCE] = nonce

    @staticmethod
    def invalidate_calc() -> None:
        """Force un recalcul au prochain run, SANS effacer les résultats
        actuellement affichés (`results`/`summary`/`vies_summary`/
        `oss_summary` restent en session_state — voir docstring du module).
        Utilisé par vies_ui.py après une (re)classification manuelle VIES.
        """
        st.session_state.pop(_SS_CALC_KEY, None)

    @staticmethod
    def get_results() -> list:
        """Lecture seule de `_results`, pour les modules qui n'ont besoin
        que de ça (ex. sidebar.py, détection de période — rendue AVANT le
        calcul dans le flux du script, donc lit le `_results` du run
        précédent)."""
        return st.session_state.get(_SS_RESULTS, [])
