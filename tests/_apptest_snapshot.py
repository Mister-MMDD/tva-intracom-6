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
