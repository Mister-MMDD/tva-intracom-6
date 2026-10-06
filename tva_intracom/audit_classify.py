"""Règles de classement partagées des écarts Amazon vs moteur (UI Audit + export Excel).

Source unique pour éviter que l'onglet Audit et le rapport Excel divergent.
"""
from __future__ import annotations

from tva_intracom.rates import is_eu


def is_non_eu_flow(stock_country: str, buyer_country: str) -> bool:
    """Flux hors Europe : départ ou arrivée hors UE (Royaume-Uni inclus).

    Le moteur exonère (export) ou ne déclare rien, alors qu'Amazon collecte
    une TVA locale : ce n'est ni un écart de taux ni un risque VIES.
    """
    return (
        stock_country == "GB" or buyer_country == "GB"
        or not is_eu(stock_country) or not is_eu(buyer_country)
    )


def is_vies_risk_gap(in_vies_affected: bool, tva_amazon: float) -> bool:
    """Risque VIES réel = Amazon a exonéré (TVA Amazon == 0) un acheteur dont le n° est invalide.

    Si Amazon a TAXÉ (VIES invalide bien détecté), le résidu moteur/Amazon n'est
    qu'un écart de taux (ex. 20 % FR vs 21/22 % destination) : pas un risque VIES.
    """
    return in_vies_affected and tva_amazon == 0


def is_domestic_reverse_charge_gap(
    in_domestic_rc: bool,
    stock_country: str,
    buyer_country: str,
    tva_moteur: float,
    tva_amazon: float,
) -> bool:
    """Écart relevant de l'autoliquidation nationale (art. 194 Dir. 2006/112/CE).

    Vrai si la vente est reclassée autoliquidation domestique par le moteur
    (`in_domestic_rc`), OU si le moteur ne facture aucune TVA alors qu'Amazon en
    a collecté pour un flux DOMESTIQUE (départ == arrivée).

    Règle validée (2026-10-05), alignée sur l'Excel :
    - un flux transfrontalier (ex. stock DE -> client ES) n'est JAMAIS de
      l'art. 194, même si le pays d'arrivée figure dans
      DOMESTIC_REVERSE_CHARGE_COUNTRIES (c'est un écart de taux / risque VIES) ;
    - un acheteur professionnel enregistré par NIF local (donc non typé B2B par
      le parseur) sur un flux domestique relève bien de l'autoliquidation
      nationale : le type d'acheteur n'est pas un critère.
    """
    return in_domestic_rc or (
        tva_moteur == 0 and tva_amazon > 0 and stock_country == buyer_country
    )
