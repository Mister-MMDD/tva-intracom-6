"""Moteur de calcul de la TVA intracommunautaire pour les ventes sur places de
marche (type Amazon).

Voir le README pour la description des 4 scenarios modelises.
"""

from __future__ import annotations

from .engine import (
    compute_all_with_vies,
    compute_vat,
)
from .models import (
    BuyerType,
    Channel,
    Collector,
    Sale,
    Scenario,
    VatResult,
    ViesReclassification,
    ViesValidationSummary,
)
from .rates import EU_COUNTRIES, STANDARD_VAT_RATES, is_eu, vat_rate
from .report import ReportSummary, build_report, render_report

__all__ = [
    "compute_all_with_vies",
    "compute_vat",
    "BuyerType",
    "Channel",
    "Collector",
    "Sale",
    "Scenario",
    "VatResult",
    "ViesReclassification",
    "ViesValidationSummary",
    "EU_COUNTRIES",
    "STANDARD_VAT_RATES",
    "is_eu",
    "vat_rate",
    "ReportSummary",
    "build_report",
    "render_report",
]
