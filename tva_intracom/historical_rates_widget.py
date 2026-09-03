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
from typing import List

import streamlit as st

from .models import VatResult
from .i18n import country_label
from .rates import (
    rate_periods_for_country,
    vat_rate_at_date,
)


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


def _sale_dates_by_country_category(
    results: List[VatResult],
) -> dict[tuple[str, str], list[date]]:
    """Retourne {(pays_destination, catégorie): [dates de transaction]}.

    Sert à ne retenir, pour l'affichage des changements de taux, que les
    combinaisons pays/catégorie réellement présentes dans le fichier importé.
    Sans catalogue Amazon importé, `product_category` vaut "STANDARD" pour
    toutes les ventes — les périodes historiques sur des catégories réduites
    (FOOD, SUPER_REDUCED...) ne doivent donc pas être affichées dans ce cas.
    """
    out: dict[tuple[str, str], list[date]] = {}
    for r in results:
        d = _parse_date(r.sale.transaction_date)
        if d:
            cat = (r.sale.product_category or "STANDARD").upper()
            out.setdefault((r.sale.buyer_country, cat), []).append(d)
    return out


def render_historical_rates_alert(results: List[VatResult], calc_key=None) -> None:
    """Affiche l'encart taux historiques si et seulement si des ventes sont
    concernées par un changement de taux dans la période couverte par le fichier.

    Ne fait rien si aucun pays avec historique n'est présent dans les données.

    `calc_key` (optionnel, rétrocompatible) : PERF (2026-09-03, voir README -
    évolution.md) — `_countries_with_sales`/`_sale_dates_by_country_category`
    scannaient tout `results` (avec un parsing de date par ligne, deux fois)
    à CHAQUE rerun où le mode détaillé est actif, jamais mis en cache par
    calc_key contrairement au reste du pipeline. Si `calc_key` est fourni,
    les deux dicts (immuables, fonction pure de `results`) sont mémoïsés en
    session_state et ne sont recalculés que quand `calc_key` change — même
    pattern que les autres mémoïsations de app.py/billing_gate.py. Si omis,
    comportement inchangé (toujours recalculé), pour ne pas casser d'appelant
    existant qui ne dispose pas encore d'un calc_key à ce stade.
    """
    if not results:
        return

    if calc_key is not None:
        _HIST_CACHE_KEY_SS = "_historical_rates_cache_key"
        _HIST_CACHE_VAL_SS = "_historical_rates_cache_val"
        if st.session_state.get(_HIST_CACHE_KEY_SS) != calc_key:
            countries_dates = _countries_with_sales(results)
            dates_by_country_category = _sale_dates_by_country_category(results)
            st.session_state[_HIST_CACHE_KEY_SS] = calc_key
            st.session_state[_HIST_CACHE_VAL_SS] = (countries_dates, dates_by_country_category)
        else:
            countries_dates, dates_by_country_category = st.session_state[_HIST_CACHE_VAL_SS]
    else:
        countries_dates = _countries_with_sales(results)
        dates_by_country_category = _sale_dates_by_country_category(results)

    # Candidats bruts : pays présents dans les données ET ayant au moins une
    # entrée d'historique, TOUTES catégories confondues (STANDARD, FOOD,
    # BOOKS...). Ce n'est qu'une présélection : un pays peut apparaître ici
    # sans qu'aucune période ne chevauche réellement les dates de vente du
    # fichier (ex. historique limité à une catégorie de produit non vendue,
    # ou à une plage de dates antérieure aux ventes). Le compte affiché dans
    # le titre ne doit PAS se baser sur cette présélection brute — voir plus
    # bas, il est recalculé à partir des lignes effectivement construites.
    candidate_countries = [
        c for c in countries_dates
        if rate_periods_for_country(c)
    ]

    if not candidate_countries:
        return  # Rien à afficher — aucun pays concerné dans ce fichier

    # Construire le tableau : une ligne par changement de taux détecté.
    # On ne retient une période que si des ventes existent réellement dans
    # SA catégorie (dates_by_country_category) — pas seulement dans le pays.
    # Sans catalogue Amazon importé, toutes les ventes sont "STANDARD" : les
    # périodes réduites (FOOD, SUPER_REDUCED...) ne doivent alors jamais
    # apparaître, même si le pays a des ventes sur la plage concernée.
    # On déduplique aussi par clé (Pays, Du, Au, Taux) : deux catégories
    # distinctes peuvent partager exactement la même plage/taux (ex. ES
    # FOOD et SUPER_REDUCED), ce qui produirait sinon des lignes identiques.
    rows_by_key: dict[tuple[str, str, str, str], dict] = {}
    countries_in_rows: set[str] = set()
    for country in sorted(candidate_countries):
        periods = rate_periods_for_country(country)
        country_name = country_label(country)

        for period in periods:
            sale_dates = dates_by_country_category.get((country, period.category))
            if not sale_dates:
                continue  # Aucune vente de cette catégorie précise dans le fichier

            min_date = min(sale_dates)
            max_date = max(sale_dates)

            # N'afficher la période que si elle chevauche la plage du fichier
            period_end = period.date_to or date(2099, 12, 31)
            if period_end < min_date or period.date_from > max_date:
                continue

            date_to_str = (
                period.date_to.strftime("%d/%m/%Y")
                if period.date_to
                else "aujourd'hui"
            )
            matching_sales = sum(
                1 for d in sale_dates
                if period.date_from <= d <= (period.date_to or date(2099, 12, 31))
            )
            if matching_sales == 0:
                continue

            key = (
                f"{country_name} ({country})",
                period.date_from.strftime("%d/%m/%Y"),
                date_to_str,
                f"{period.rate}%",
            )
            if key in rows_by_key:
                # Même pays/plage/taux qu'une autre catégorie déjà ajoutée :
                # on fusionne le compte de ventes plutôt que dupliquer la ligne.
                rows_by_key[key]["Ventes concernées"] += matching_sales
            else:
                rows_by_key[key] = {
                    "Pays": key[0],
                    "Du": key[1],
                    "Au": key[2],
                    "Taux appliqué": key[3],
                    "Ventes concernées": matching_sales,
                }
            countries_in_rows.add(country)

    rows = list(rows_by_key.values())
    if not rows:
        return

    # Le compte affiché dans le titre correspond aux pays qui ont
    # effectivement au moins une ligne dans le tableau — pas à la
    # présélection brute (candidate_countries), qui peut inclure des pays
    # dont l'historique existe mais ne s'applique pas à la période/catégorie
    # réellement vendue.
    countries_with_history = sorted(countries_in_rows)

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
                f"**{country_label(c)}**" for c in countries_with_multiple_rates
            )
            st.caption(
                f"⚡ Changement de taux en cours de période détecté pour : {names}. "
                "Les taux ont été appliqués vente par vente selon la date de transaction."
            )
        else:
            st.caption(
                "Les pays ci-dessous ont connu un changement de taux TVA récent. "
                "Toutes vos ventes se situent dans une seule période — "
                "le taux correct a été appliqué uniformément."
            )

        import pandas as pd
        df = pd.DataFrame(rows)
        st.dataframe(
            df,
            width="stretch",
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