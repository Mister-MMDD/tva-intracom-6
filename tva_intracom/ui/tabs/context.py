"""Contexte partagé entre les modules d'onglets (tva_intracom/ui/tabs/*.py).

Un seul objet `TabContext` est construit dans app.py juste avant l'affichage
des onglets, et transmis tel quel à chaque fonction `render_xxx(ctx)`.

Champ `oss_tva_net_total` : dépendance intentionnelle entre onglets, fidèle
au comportement d'origine (script Streamlit unique où tous les `with
tab_x:` s'exécutaient séquentiellement dans le même scope) — l'onglet
Déclarations calcule ce total et le stocke sur `ctx`, l'onglet
Téléchargements le relit pour l'export CSV local FR. Les onglets sont
rendus dans le même ordre qu'auparavant (Déclarations avant
Téléchargements), donc ce couplage reste valide.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional


@dataclass
class TabContext:
    # Résultats calculés (moteur TVA)
    results: list
    refund_results: list
    summary: Any
    vies_summary: Any
    oss_summary: Any
    period_label: str
    period_detected_range: Optional[tuple]

    # Gating billing (voir tva_intracom/ui/billing_gate.py)
    can_export: bool
    gated_download: Any    # callable : BillingGate.gated_download
    unlock_label_suffix: str

    # Auth / VIES
    vies_scope_id: str
    vies_retry_nonce: int
    enable_vies: bool

    # Entreprise / paramètres (sidebar)
    nom_entreprise: str
    siren_entreprise: str
    tva_fr: str
    countries_with_vat: list
    local_vat_numbers: dict

    # Données brutes d'import
    all_fc_transfers: list
    all_invoice_credit_notes: list
    all_sales: list
    platform_name: str

    # Cross-onglet : rempli par render_declarations(), lu par
    # render_telechargements() — voir docstring du module.
    oss_tva_net_total: Any = None
