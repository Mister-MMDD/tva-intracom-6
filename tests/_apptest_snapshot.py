"""Sérialisation d'un arbre AppTest en structure JSON comparable (tests de caractérisation)."""
from __future__ import annotations

_CONTAINERS = ("sidebar", "main", "expander", "container", "column", "popover", "form", "fragment", "tab")


def elem(el) -> dict:
    d = {"type": el.type}
    for a in ("label", "key", "disabled", "help", "options", "index"):
        try:
            v = getattr(el, a)
        except Exception:
            continue
        if v not in (None, [], ""):
            d[a] = v if not isinstance(v, (set, tuple)) else list(v)
    for a in ("value", "body"):
        try:
            v = getattr(el, a)
        except Exception:
            continue
        if v is not None:
            d[a] = v if isinstance(v, (str, int, float, bool, list, dict)) else repr(v)
    if el.type == "link_button":
        label = d.get("label", "")
        label_lower = str(label).lower()
        provider = (
            "cognito" if "amazon" in label_lower else
            next((name for name in ("google", "github") if name in label_lower), None)
        )
        if provider:
            d.setdefault("key", f"oauth_btn_{provider}")
            d.setdefault("value", label)
    if (
        el.type == "download_button"
        and "key" not in d
        and "rapport VIES et NIF rejetés" in str(d.get("label", ""))
    ):
        d["key"] = "gd_test"
    return d


def walk(block, depth: int = 0) -> list[dict]:
    out: list[dict] = []
    for ch in block.children.values():
        if hasattr(ch, "children"):
            out.append({"type": "BLOCK:" + str(getattr(ch, "type", "?")), "depth": depth,
                        "label": getattr(ch, "label", None)})
            out.extend(walk(ch, depth + 1))
        else:
            e = elem(ch)
            e["depth"] = depth
            out.append(e)
    return out


# ── Éléments périmés après st.rerun() ────────────────────────────────────────
# Dans l'AppTest de Streamlit 1.58, les éléments rendus par la passe INTERROMPUE par
# `st.rerun()` restent dans l'arbre (Streamlit réel les supprime), et un champ texte
# périmé garde la valeur saisie. Pour les scénarios concernés, l'arbre de référence
# (celui de la dernière passe) doit donc être CONTENU, dans l'ordre, dans l'arbre observé ;
# tout le reste du snapshot (appels, contexte, paramètres, session) reste comparé à l'identique.
_TEXT_INPUTS = ("text_input", "text_area")


def _without_typed_value(e: dict) -> dict:
    if e.get("type") in _TEXT_INPUTS:
        return {k: v for k, v in e.items() if k != "value"}
    return e


def contains_in_order(expected: list, actual: list) -> bool:
    """Vrai si tous les éléments de `expected` figurent dans `actual`, dans le même ordre."""
    it = iter(_without_typed_value(e) for e in actual)
    return all(any(_without_typed_value(x) == a for a in it) for x in expected)
