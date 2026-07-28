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
import logging
import streamlit as st

logger = logging.getLogger(__name__)

_libc = None
_jemalloc = None
_load_attempted = False


def _get_libs():
    """Charge les bibliothèques système (paresseux)."""
    global _libc, _jemalloc, _load_attempted
    if not _load_attempted:
        _load_attempted = True
        # 1. Tente de charger jemalloc (le chemin Railway/Nixpacks)
        for path in ["libjemalloc.so.2", "/usr/lib/x86_64-linux-gnu/libjemalloc.so.2"]:
            try:
                _jemalloc = ctypes.CDLL(path)
                logger.info(f"Mémoire : jemalloc chargé depuis {path}")
                break
            except OSError:
                continue
        
        # 2. Tente de charger libc standard
        try:
            _libc = ctypes.CDLL("libc.so.6")
        except OSError:
            _libc = None
            
    return _libc, _jemalloc


def release_memory() -> None:
    """Libère les objets Python inutilisés ET rend la mémoire à l'OS.

    À appeler après toute suppression de gros objets en session_state
    (fichiers uploadés, résultats de calcul, DataFrames...) : à la
    déconnexion (logout) et lors du retrait de fichiers uploadés.
    """
    # 1. Vide les caches globaux Streamlit (TRÈS IMPORTANT)
    # Les agrégats de graphiques (@st.cache_data) peuvent peser plusieurs dizaines de Mo.
    try:
        st.cache_data.clear()
        logger.info("Mémoire : st.cache_data vidé.")
    except Exception:
        pass

    # 2. Vide les caches de traduction
    try:
        from .i18n.i18n import load_translations
        load_translations.cache_clear()
    except Exception:
        pass

    # 3. Force le ramasse-miettes (plusieurs passes pour les cycles)
    gc.collect()
    gc.collect()

    # 4. Rend la mémoire à l'OS
    libc, jemalloc = _get_libs()
    
    # Cas A : jemalloc (prioritaire si détecté)
    if jemalloc is not None:
        try:
            # arenas.dirty_decay_ms = 0 -> purge immédiate des pages sales
            # arenas.muzzy_decay_ms = 0 -> purge immédiate des pages "muzzy" (tièdes)
            val = ctypes.c_ssize_t(0)
            jemalloc.mallctl(b"arenas.dirty_decay_ms", None, None, ctypes.byref(val), ctypes.sizeof(val))
            jemalloc.mallctl(b"arenas.muzzy_decay_ms", None, None, ctypes.byref(val), ctypes.sizeof(val))
            
            # Flush complet de l'allocateur
            # arenas.purge forcera la libération de toutes les pages inutilisées
            try:
                # -1 signifie "tous les arenas"
                all_arenas = ctypes.c_uint(0xFFFFFFFF)
                jemalloc.mallctl(b"arena.4294967295.purge", None, None, None, 0)
            except Exception:
                pass

            # Force également un "thread.tcache.flush" pour libérer les caches locaux aux threads
            try:
                jemalloc.mallctl(b"thread.tcache.flush", None, None, None, 0)
            except Exception:
                pass
            logger.info("Mémoire : Purge jemalloc effectuée.")
        except Exception as e:
            logger.warning(f"Mémoire : Échec purge mallctl jemalloc : {e}")

    # Cas B : glibc standard (ou fallback)
    if libc is not None:
        try:
            libc.malloc_trim(0)
            logger.info("Mémoire : malloc_trim(0) effectué.")
        except Exception:
            pass
