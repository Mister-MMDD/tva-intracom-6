"""Helpers for rendering import warnings as structured table rows."""

from __future__ import annotations

import re

_SOURCE_WARNING_RE = re.compile(
    r"^Ligne du fichier (?P<line>\d+)"
    r"(?: — (?P<reference_type>commande/remboursement|commande|transaction|facture)"
    r" (?P<reference>.+?))?: (?P<message>.*)$"
)


def warning_to_table_row(warning: str) -> dict[str, str]:
    """Split a file-prefixed warning into its table columns."""
    filename, separator, detail = warning.partition(" :: ")
    if not separator:
        filename, detail = "", warning

    match = _SOURCE_WARNING_RE.match(detail)
    if not match:
        return {
            "file": filename,
            "line": "",
            "reference": "",
            "warning": detail,
        }

    reference = (match.group("reference") or "").strip()
    reference_type = match.group("reference_type")
    if reference_type in {"transaction", "facture"} and reference:
        reference = f"{reference_type} {reference}"

    return {
        "file": filename,
        "line": match.group("line"),
        "reference": reference,
        "warning": match.group("message"),
    }
