"""
Outil de diagnostic mémoire TEMPORAIRE.

Objectif : arrêter de deviner et observer précisément, dans le process
Streamlit réel (pas en local), quels types d'objets Python s'accumulent
entre deux mesures (ex: avant upload / après upload / après logout /
après reconnexion+upload).

Contrairement à `st.session_state`, l'état de ce module (`_last_snapshot`)
est stocké au niveau du MODULE Python, donc partagé par tout le process
et par toutes les sessions Streamlit -- exactement ce qu'on veut pour
observer une fuite qui survit au-delà d'une session utilisateur.

À RETIRER une fois la fuite identifiée et corrigée (import dans
sidebar.py + section debug à supprimer, cf. commentaires "TEMPORAIRE").
"""
from __future__ import annotations

import gc
import sys
from collections import Counter
from typing import Optional


def get_rss_mb() -> Optional[float]:
    """RSS réelle du process en Mo, lue depuis /proc/self/status (Linux
    uniquement -- fonctionne sur Railway/Streamlit Cloud, renvoie None
    ailleurs, ex. macOS en local)."""
    try:
        with open("/proc/self/status") as f:
            for line in f:
                if line.startswith("VmRSS:"):
                    kb = int(line.split()[1])
                    return kb / 1024
    except Exception:
        return None
    return None


def _object_type_counts() -> Counter:
    gc.collect()
    counts: Counter = Counter()
    for obj in gc.get_objects():
        counts[type(obj).__name__] += 1
    return counts


def find_referrer_chain(type_name: str, max_instances: int = 3, max_depth: int = 15) -> list[list[str]]:
    """Pour quelques instances survivantes du type `type_name` (ex: "Sale"),
    remonte la chaîne de référents via `gc.get_referrers` et renvoie, pour
    chacune, la liste des TYPES rencontrés en remontant (jamais le contenu
    réel des objets -- on ne veut voir "qui retient quoi", pas les données
    métier, par précaution RGPD puisque `Sale` peut porter des données
    d'entreprise clientes).

    Contrairement à une v1 naïve qui suivait juste "le premier référent
    trouvé", celle-ci :
    - explore TOUS les référents à chaque niveau (pas un seul choisi au
      hasard), pour ne pas suivre une mauvaise branche et perdre la vraie
      racine (fréquent avec des structures imbriquées liste-de-listes) ;
    - détecte les cycles (id() déjà vu) pour éviter une boucle infinie
      list <- list <- list <- ... qui ne terminait jamais avec l'ancienne
      version (max_depth atteint sans conclusion) ;
    - s'arrête dès qu'un référent "racine" reconnaissable est atteint :
      frame (nom de fonction), module, ou objet dont le refcount/contexte
      suggère un cache/état long-vivant (dict avec clé "__name__", classe
      Streamlit interne type SessionState/ScriptRunContext/ThreadPoolExecutor).

    Lecture du résultat : chaque ligne du "chemin" est
      "TypeDuRéférent (id=...) [+N autres référents à ce niveau]"
    Le nombre de référents supplémentaires à un niveau donné est un indice :
    s'il y en a beaucoup, l'objet est référencé depuis plusieurs endroits
    (ex: plusieurs caches) et pas juste un chemin linéaire.
    """
    gc.collect()
    instances = [o for o in gc.get_objects() if type(o).__name__ == type_name][:max_instances]
    _this_frame_code = sys._getframe().f_code

    ROOT_HINTS = {"frame", "module", "ThreadPoolExecutor", "_WorkItem", "SimpleQueue",
                  "ScriptRunContext", "SessionState", "Thread"}

    chains: list[list[str]] = []
    for inst in instances:
        path: list[str] = [f"{type_name} (id={id(inst)})"]
        current = inst
        visited_ids = {id(inst), id(instances)}
        for _depth in range(max_depth):
            referrers = [
                r for r in gc.get_referrers(current)
                if id(r) not in visited_ids
                and not (type(r).__name__ == "frame" and r.f_code is _this_frame_code)
            ]
            if not referrers:
                path.append("(racine atteinte : plus aucun référent)")
                break

            ref_type_counts: dict[str, int] = {}
            for r in referrers:
                ref_type_counts[type(r).__name__] = ref_type_counts.get(type(r).__name__, 0) + 1

            # on choisit comme "prochain saut" le référent dont le type
            # correspond le plus probablement à une vraie racine, sinon le
            # premier de la liste
            chosen = referrers[0]
            for r in referrers:
                if type(r).__name__ in ROOT_HINTS:
                    chosen = r
                    break

            chosen_type = type(chosen).__name__
            extra = len(referrers) - 1
            _len_info = ""
            try:
                _len_info = f", len={len(chosen)}"
            except TypeError:
                pass
            label = f"{chosen_type} (id={id(chosen)}{_len_info})" + (f" [+{extra} autre(s) référent(s) à ce niveau: {dict(ref_type_counts)}]" if extra else "")

            if chosen_type == "frame":
                label = f"frame (fonction: {chosen.f_code.co_name}, fichier: {chosen.f_code.co_filename.split('/')[-1]})"
                path.append(label)
                break
            if chosen_type == "module":
                label = f"module ({getattr(chosen, '__name__', '?')})"
                path.append(label)
                break

            if id(chosen) in visited_ids:
                path.append(f"(cycle détecté sur {chosen_type}, id={id(chosen)} déjà visité -- arrêt)")
                break

            path.append(label)
            visited_ids.add(id(chosen))
            current = chosen
        else:
            path.append(f"(profondeur max {max_depth} atteinte sans racine claire)")
        chains.append(path)
    return chains






_last_snapshot: Optional[Counter] = None
_last_rss: Optional[float] = None


def snapshot_and_diff(top_n: int = 25) -> dict:
    """Prend un instantané maintenant, le compare au précédent appel
    (tous utilisateurs/sessions confondus, puisque stocké au niveau
    module), et renvoie :
      - rss_mb : RSS actuelle
      - rss_delta_mb : delta vs mesure précédente
      - top_growth : les N types d'objets Python dont le nombre a le
        plus augmenté depuis la mesure précédente (triés décroissant)
      - top_counts : les N types les plus nombreux en absolu (utile
        pour repérer un type énorme dès la première mesure)
    """
    global _last_snapshot, _last_rss

    rss = get_rss_mb()
    counts = _object_type_counts()

    rss_delta = None if _last_rss is None else (
        None if rss is None else rss - _last_rss
    )

    if _last_snapshot is None:
        growth = Counter()
    else:
        growth = Counter()
        all_types = set(counts) | set(_last_snapshot)
        for t in all_types:
            delta = counts.get(t, 0) - _last_snapshot.get(t, 0)
            if delta != 0:
                growth[t] = delta

    result = {
        "rss_mb": rss,
        "rss_delta_mb": rss_delta,
        "top_growth": growth.most_common(top_n),
        "top_counts": counts.most_common(top_n),
    }

    _last_snapshot = counts
    _last_rss = rss
    return result
