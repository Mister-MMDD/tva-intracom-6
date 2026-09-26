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

logger = logging.getLogger(__name__)

_libc = None
_jemalloc = None
_load_attempted = False


# ---------------------------------------------------------------------------
# Registre auto-déclaratif des caches @st.cache_data "lourds"
# ---------------------------------------------------------------------------
#
# Avant : release_memory() maintenait à la main une liste d'imports
# (_parse_catalog_bytes, _aggregate_viz_raw, ...) qu'il fallait penser à
# mettre à jour à chaque nouvel onglet/cache lourd ajouté ailleurs dans le
# code -- un oubli = fuite mémoire silencieuse en prod (le cache reste vivant
# process-wide jusqu'à expiration du ttl=1800s, ou indéfiniment si aucun ttl
# n'est fixé).
#
# Maintenant : `heavy_cache_data` remplace `st.cache_data` à l'endroit où la
# fonction est définie. Le décorateur s'auto-enregistre dans
# `_HEAVY_CACHE_REGISTRY` au moment de l'import du module qui le définit --
# release_memory() n'a donc plus besoin de connaître la liste des fonctions
# concernées ni de les importer une par une : il lui suffit d'itérer le
# registre. Un nouveau cache lourd est nettoyé automatiquement dès lors
# qu'il utilise `@heavy_cache_data(...)` au lieu de `@st.cache_data(...)`.
_HEAVY_CACHE_REGISTRY: list = []


def heavy_cache_data(*args, **kwargs):
    """Remplace `st.cache_data` pour les caches lourds en RAM (agrégats de
    graphiques, DataFrames de détail, parsing de catalogues...).

    Usage identique à `st.cache_data` :

        @heavy_cache_data(show_spinner=False, ttl=1800, max_entries=20)
        def _aggregate_viz_raw(...): ...

    La fonction décorée est automatiquement ajoutée à `_HEAVY_CACHE_REGISTRY`
    et sera donc vidée par `release_memory()`, sans modification requise
    dans ce fichier.

    Import de `streamlit` fait ici (et non en tête de module) : `mem_utils`
    ne doit pas devenir indisponible si jamais il finissait importé depuis un
    contexte sans Streamlit (voir la règle équivalente déjà appliquée à
    d'autres modules du projet pour Vercel/serverless).
    """
    import streamlit as st

    def _decorator(func):
        cached_func = st.cache_data(*args, **kwargs)(func)
        _HEAVY_CACHE_REGISTRY.append(cached_func)
        return cached_func

    return _decorator


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
    # 1. On NE vide PLUS les caches @heavy_cache_data ici (ancien step 1 :
    # boucle `_fn.clear()` sur `_HEAVY_CACHE_REGISTRY`).
    #
    # Pourquoi ce retrait : `_fn.clear()` sur un `st.cache_data` vide TOUTES
    # les entrées de la fonction, tous utilisateurs/sessions confondus — même
    # si les clés incluent déjà `calc_key` (voir audit.py/declarations.py/
    # visualisations.py, qui composent leur clé de session avec `ctx.calc_key`).
    # `release_memory()` est appelée sur des évènements PAR UTILISATEUR
    # (retrait d'un fichier par l'utilisateur A dans app.py, logout de
    # l'utilisateur B dans auth_flow.py) mais le process Streamlit est
    # partagé entre sessions/threads : un `.clear()` ici vidait donc aussi
    # les entrées valides d'autres utilisateurs actifs au même moment,
    # provoquant un recalcul (graphiques, DataFrames détail) au clic suivant
    # alors qu'ils n'avaient rien demandé. C'est exactement le problème déjà
    # identifié et corrigé pour les caches billing/Stripe le 02/08/2026 (voir
    # plus bas) — on applique maintenant le même principe aux caches lourds.
    #
    # À la place : on laisse le TTL et le `max_entries` déjà fixés sur chaque
    # `@heavy_cache_data(..., ttl=..., max_entries=...)` faire le ménage
    # naturellement. Ces caches sont déjà bornés en RAM par construction
    # (Streamlit évince les entrées les plus anciennes au-delà de
    # `max_entries`, et purge celles expirées par `ttl`) ; il n'est donc pas
    # nécessaire de forcer un vidage global pour éviter une fuite mémoire
    # long terme — seulement pour libérer immédiatement la RAM d'UN
    # utilisateur qui vient de se déconnecter/retirer un fichier, ce qu'un
    # clear ciblé par clé permettrait mais qu'un clear global ne doit plus
    # faire.
    logger.info(
        f"Mémoire : {len(_HEAVY_CACHE_REGISTRY)} cache(s) @heavy_cache_data "
        "laissé(s) intact(s) (nettoyage global supprimé — effet de bord "
        "multi-utilisateur, voir commentaire ci-dessus ; TTL/max_entries "
        "font le ménage par entrée)."
    )

    # 2. (SUPPRIMÉ — BUGFIX point #7, README - évolution.md) : ce bloc
    # appelait `load_translations.cache_clear()`, qui vidait un
    # `@lru_cache(maxsize=None)` process-wide (tva_intracom/i18n/i18n.py) —
    # partagé par TOUTES les sessions/utilisateurs, exactement le même
    # anti-pattern que celui déjà identifié et corrigé juste au-dessus pour
    # `heavy_cache_data` (step 1) et pour les caches billing/Stripe le
    # 02/08/2026 : `release_memory()` est appelée sur un événement PAR
    # UTILISATEUR (retrait de fichier, déconnexion), mais un `.cache_clear()`
    # ici évinçait les 7 langues pour tout le monde, forçant chaque autre
    # session active à reparser son fichier TOML depuis le disque au
    # prochain rendu — une charge CPU/IO inutile et synchronisée pour les
    # AUTRES utilisateurs, causée par le nettoyage d'UN SEUL compte. Les 7
    # fichiers TOML (~1207 clés chacun) sont d'une taille négligeable face
    # aux "gros objets" (DataFrames, résultats de calcul) que cette fonction
    # cible réellement : aucune raison de les vider ici, tout comme il n'y
    # en avait pas pour heavy_cache_data.

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
                # -1 signifie "tous les arenas" (0xFFFFFFFF = 4294967295)
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