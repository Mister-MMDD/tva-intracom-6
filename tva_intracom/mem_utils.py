"""
Utilitaires de libération mémoire.

Contexte : sur Streamlit Cloud / Railway, un seul process Python sert
potentiellement plusieurs sessions utilisateur (threads). `gc.collect()`
libère bien les objets Python (DataFrames, bytes de fichiers uploadés,
caches de calcul...) mais glibc ne rend quasiment jamais les pages mémoire
correspondantes à l'OS : l'allocateur les garde dans ses arènes pour les
réutiliser. Résultat : la RSS observée par les métriques Railway ne
redescend pas, même si plus aucun objet Python volumineux n'existe.

`release_memory()` fait donc deux choses :
1. `gc.collect()` — libère les objets Python (comme avant).
2. `ctypes` -> `malloc_trim(0)` — force glibc à rendre à l'OS les pages
   libres en haut de tas. 
   Note : En production, nous utilisons jemalloc (via LD_PRELOAD) qui 
   gère cela de manière beaucoup plus efficace que glibc.
"""
from __future__ import annotations

import ctypes
import gc

_libc = None
_libc_load_attempted = False


def _get_libc():
    """Charge libc une seule fois (paresseux, silencieux si indisponible)."""
    global _libc, _libc_load_attempted
    if not _libc_load_attempted:
        _libc_load_attempted = True
        try:
            _libc = ctypes.CDLL("libc.so.6")
        except OSError:
            _libc = None
    return _libc


def release_memory() -> None:
    """Libère les objets Python inutilisés ET rend la mémoire à l'OS.

    À appeler après toute suppression de gros objets en session_state
    (fichiers uploadés, résultats de calcul, DataFrames...) : à la
    déconnexion (logout) et lors du retrait de fichiers uploadés.
    """
    gc.collect()
    libc = _get_libc()
    if libc is not None:
        try:
            libc.malloc_trim(0)
        except Exception:
            pass
