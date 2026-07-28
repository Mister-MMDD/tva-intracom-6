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


def find_referrer_chain(type_name: str, max_instances: int = 3, max_depth: int = 4) -> list[list[str]]:
    """Pour quelques instances survivantes du type `type_name` (ex: "Sale"),
    remonte la chaîne de référents via `gc.get_referrers` et renvoie, pour
    chacune, la liste des TYPES rencontrés en remontant (jamais le contenu
    réel des objets -- on ne veut voir "qui retient quoi", pas les données
    métier, par précaution RGPD puisque `Sale` peut porter des données
    d'entreprise clientes).

    Lecture du résultat : le dernier élément de chaque chaîne est
    généralement le conteneur "racine" qui explique pourquoi l'objet
    n'a jamais été libéré (un module, un dict au niveau module, une
    closure de thread, un cache Streamlit interne, etc.).
    """
    gc.collect()
    instances = [o for o in gc.get_objects() if type(o).__name__ == type_name][:max_instances]

    chains: list[list[str]] = []
    for inst in instances:
        chain: list[str] = [type_name]
        current = inst
        seen_ids = {id(inst)}
        for _ in range(max_depth):
            referrers = [
                r for r in gc.get_referrers(current)
                # on ignore les frames/listes locales de cette fonction elle-même
                if r is not instances and id(r) not in seen_ids
            ]
            if not referrers:
                chain.append("(plus de référent trouvé -- racine atteinte)")
                break
            ref = referrers[0]
            seen_ids.add(id(ref))
            ref_type = type(ref).__name__
            # Pour un dict/list/frame, on ajoute un indice utile (nom de
            # variable si dispo pour une frame) sans jamais dumper le
            # contenu d'un Sale/VatResult.
            if ref_type == "frame":
                chain.append(f"frame (fonction: {ref.f_code.co_name})")
                break  # une frame de fonction est une racine suffisante
            if ref_type == "module":
                chain.append(f"module ({getattr(ref, '__name__', '?')})")
                break
            chain.append(ref_type)
            current = ref
        chains.append(chain)
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
