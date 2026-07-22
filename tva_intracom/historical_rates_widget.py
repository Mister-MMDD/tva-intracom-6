"""Composant Streamlit : encart taux historiques.

Affiche un tableau des changements de taux TVA UE uniquement si le fichier
chargé contient des ventes dans les pays et périodes concernés.

Usage dans app.py :
    from historical_rates_widget import render_historical_rates_alert
    render_historical_rates_alert(results)

où `results` est la liste de VatResult retournée par compute_all_with_vies().
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import List

import streamlit as st

from .rates import (
    VAT_RATE_HISTORY,
    COUNTRY_NAMES,
    rate_periods_for_country,
    vat_rate_at_date,
    STANDARD_VAT_RATES,
)
from .models import VatResult


def _parse_date(s: str) -> date | None:
    """Parse YYYY-MM-DD ou YYYY-MM, retourne None si invalide."""
    if not s:
        return None
    try:
        return date.fromisoformat(s[:10])
    except ValueError:
        return None


def _countries_with_sales(results: List[VatResult]) -> dict[str, list[date]]:
    """Retourne {pays_destination: [dates de transaction]} pour toutes les ventes."""
    out: dict[str, list[date]] = {}
    for r in results:
        d = _parse_date(r.sale.transaction_date)
        if d:
            out.setdefault(r.sale.buyer_country, []).append(d)
    return out


def render_historical_rates_alert(results: List[VatResult]) -> None:
    """Affiche l'encart taux historiques si et seulement si des ventes sont
    concernées par un changement de taux dans la période couverte par le fichier.

    Ne fait rien si aucun pays avec historique n'est présent dans les données.
    """
    if not results:
        return

    countries_dates = _countries_with_sales(results)

    # Pays dans l'historique ET présents dans les données
    countries_with_history = [
        c for c in countries_dates
        if rate_periods_for_country(c)
    ]

    if not countries_with_history:
        return  # Rien à afficher — aucun pays concerné dans ce fichier

    # Construire le tableau : une ligne par changement de taux détecté
    rows = []
    for country in sorted(countries_with_history):
        sale_dates = countries_dates[country]
        min_date = min(sale_dates)
        max_date = max(sale_dates)
        periods = rate_periods_for_country(country)
        country_name = COUNTRY_NAMES.get(country, country)

        # Vérifier si plusieurs taux distincts ont été appliqués dans la période
        rates_used: set[Decimal] = set()
        for tx_date in sale_dates:
            rates_used.add(vat_rate_at_date(country, tx_date))

        for period in periods:
            # N'afficher la période que si elle chevauche la plage du fichier
            period_end = period.date_to or date(2099, 12, 31)
            if period_end < min_date or period.date_from > max_date:
                continue

            date_to_str = (
                period.date_to.strftime("%d/%m/%Y")
                if period.date_to
                else "aujourd'hui"
            )
            rows.append({
                "Pays": f"{country_name} ({country})",
                "Du": period.date_from.strftime("%d/%m/%Y"),
                "Au": date_to_str,
                "Taux appliqué": f"{period.rate}%",
                "Ventes concernées": sum(
                    1 for d in sale_dates
                    if period.date_from <= d <= (period.date_to or date(2099, 12, 31))
                ),
            })

    if not rows:
        return

    # Déterminer si des ventes ont été calculées avec des taux différents
    # (situation réelle de changement en cours de période)
    countries_with_multiple_rates = [
        c for c in countries_with_history
        if len({vat_rate_at_date(c, d) for d in countries_dates[c]}) > 1
    ]

    with st.expander(
        f"📅 Taux TVA historiques détectés — {len(countries_with_history)} pays concerné(s)",
        expanded=bool(countries_with_multiple_rates),  # ouvert si taux multiples effectifs
    ):
        if countries_with_multiple_rates:
            names = ", ".join(
                f"**{COUNTRY_NAMES.get(c, c)}**" for c in countries_with_multiple_rates
            )
            st.warning(
                f"⚡ Changement de taux en cours de période détecté pour : {names}. "
                "Les taux ont été appliqués vente par vente selon la date de transaction.",
                icon="⚠️",
            )
        else:
            st.info(
                "Les pays ci-dessous ont connu un changement de taux TVA récent. "
                "Toutes vos ventes se situent dans une seule période — "
                "le taux correct a été appliqué uniformément.",
                icon="ℹ️",
            )

        import pandas as pd
        df = pd.DataFrame(rows)
        st.dataframe(
            df,
            use_container_width=True,
            hide_index=True,
            column_config={
                "Pays": st.column_config.TextColumn(width="medium"),
                "Du": st.column_config.TextColumn(width="small"),
                "Au": st.column_config.TextColumn(width="small"),
                "Taux appliqué": st.column_config.TextColumn(width="small"),
                "Ventes concernées": st.column_config.NumberColumn(
                    width="small", format="%d vente(s)"
                ),
            },
        )

        st.caption(
            "Source : Commission européenne, tableau des taux TVA 2024/2026. "
            "Périmètre historique : à partir du 01/01/2024."
        )