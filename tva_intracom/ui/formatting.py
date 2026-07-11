"""Helpers de formatage et d'affichage réutilisés par plusieurs onglets.

Extraits tel quel de app.py (aucune modification de comportement) :
  - _fmt / _country_label : formatage montant et libellé pays
  - _money_col / _pct_col : column_config Streamlit réutilisables
  - _smart_money_df       : column_config auto pour un DataFrame (montants + taux)
  - _gated_preview_table  : aperçu bridé tant que la période n'est pas débloquée

Les noms restent préfixés par underscore pour ne rien casser côté imports
existants (`from tva_intracom.ui.formatting import _fmt, ...`).
"""

from __future__ import annotations

import math
from typing import Any

import streamlit as st

from tva_intracom.rates import COUNTRY_NAMES


def _fec_period_end_date(period: str) -> str:
    """Calcule la date de fin de période au format AAAAMMJJ (FEC EcritureDate)
    à partir du libellé de période détecté (ex: '2026-Q2', '2026-T2',
    '2026-06', '2026'). Retombe sur la date du jour si le format n'est pas
    reconnu — cohérent avec le fait que la date d'écriture n'est qu'un
    repère de comptabilisation, pas une donnée fiscale opposable en soi
    (contrairement à la période elle-même, mentionnée dans le libellé de
    chaque écriture par fec_export.build_fec_rows)."""
    import calendar
    import re
    from datetime import date as _fec_date

    p = (period or "").strip()
    m = re.match(r"^(\d{4})-[QT]([1-4])$", p, re.IGNORECASE)
    if m:
        year, q = int(m.group(1)), int(m.group(2))
        month = q * 3
        last_day = calendar.monthrange(year, month)[1]
        return f"{year}{month:02d}{last_day:02d}"
    m2 = re.match(r"^(\d{4})-(\d{2})$", p)
    if m2:
        year, month = int(m2.group(1)), int(m2.group(2))
        last_day = calendar.monthrange(year, month)[1]
        return f"{year}{month:02d}{last_day:02d}"
    if re.match(r"^\d{4}$", p):
        return f"{p}1231"
    return _fec_date.today().strftime("%Y%m%d")


def _country_label(code):
    return COUNTRY_NAMES.get(code, code)


def _render_filter_bar(df: "pd.DataFrame", key_suffix: str) -> "pd.DataFrame":
    """Affiche une barre de filtres (Recherche, Destination, Scénario, Canal) 
    et retourne le DataFrame filtré. Utilisé uniformément sur tous les tableaux.
    """
    import streamlit as st
    _fa, _fb, _fc, _fd = st.columns([2, 2, 2, 2])
    
    with _fa:
        _search = st.text_input("🔍 Rechercher", placeholder="ID, ASIN, Note...", key=f"search_{key_suffix}")
    
    with _fb:
        _dest_opts = sorted(df["Dest"].unique()) if "Dest" in df.columns else []
        _dest_sel = st.multiselect("Pays destination", _dest_opts, key=f"dest_{key_suffix}", 
                                   placeholder="Tous les pays")
        
    with _fc:
        _scen_opts = sorted(df["Scénario"].unique()) if "Scénario" in df.columns else []
        _scen_sel = st.multiselect("Scénario", _scen_opts, key=f"scen_{key_suffix}", 
                                   placeholder="Tous les scénarios")
        
    with _fd:
        _canal_opts = sorted(df["Canal"].unique()) if "Canal" in df.columns else []
        _canal_sel = st.multiselect("Canal", _canal_opts, key=f"canal_{key_suffix}", 
                                   placeholder="Tous les canaux")
        
    df_filt = df.copy()
    if _search:
        mask = df_filt["ID"].astype(str).str.contains(_search, case=False, na=False)
        if "Note" in df_filt.columns:
            mask |= df_filt["Note"].astype(str).str.contains(_search, case=False, na=False)
        df_filt = df_filt[mask]
        
    if _dest_sel and "Dest" in df_filt.columns:
        df_filt = df_filt[df_filt["Dest"].isin(_dest_sel)]
    if _scen_sel and "Scénario" in df_filt.columns:
        df_filt = df_filt[df_filt["Scénario"].isin(_scen_sel)]
    if _canal_sel and "Canal" in df_filt.columns:
        df_filt = df_filt[df_filt["Canal"].isin(_canal_sel)]
        
    return df_filt


def _fmt(value) -> str:
    """Formate un montant : 13 → '13 €', 13.5 → '13.50 €', 13.00 → '13 €'."""
    v = float(value)
    if v == int(v):
        return f"{int(v):,} €".replace(",", " ")
    return f"{v:,.2f} €".replace(",", " ")


# Helpers column_config réutilisables
# ── Colonne monétaire : tri numérique conservé, affichage smart (0 déc. ou 2 déc.)
def _money_col(label: str, help_txt: str = "") -> "st.column_config.NumberColumn":
    """NumberColumn monétaire : entier si .00, sinon 2 décimales."""
    import streamlit as _st
    return _st.column_config.NumberColumn(
        label,
        format="%.2f €",   # Streamlit applique toujours 2 déc. dans l'affichage natif
        help=help_txt,
    )


def _pct_col(label: str, help_txt: str = "") -> "st.column_config.NumberColumn":
    """NumberColumn pourcentage : 1 décimale, suffixe %."""
    import streamlit as _st
    return _st.column_config.NumberColumn(label, format="%.2f %%", help=help_txt)


def _smart_money_df(df: "pd.DataFrame", money_cols: list[str], pct_cols: list[str] | None = None,
                    note_cols: list[str] | None = None) -> dict:
    """Génère un column_config Streamlit pour les colonnes monétaires et de taux.

    Règle d'affichage monétaire : entier si pas de décimale significative, sinon 2 déc.
    Streamlit NumberColumn avec format="%.2f €" affiche toujours 2 déc.
    On contourne en pré-formatant les valeurs en string et en utilisant TextColumn
    pour les colonnes qui nécessitent l'affichage smart (0 ou 2 déc.).

    Stratégie retenue : pré-formater les colonnes monétaires en string dans le DataFrame
    (les valeurs sont déjà des floats, on les formate avant st.dataframe).
    Cette fonction retourne le column_config à passer à st.dataframe.
    """
    cfg = {}
    for col in (money_cols or []):
        if col in df.columns:
            # Pré-formatage dans le df : on remplace les floats par des strings formatées
            df[col] = df[col].apply(
                lambda v: (
                    "" if v is None or (isinstance(v, float) and __import__('math').isnan(v))
                    else (f"{int(v):,}".replace(",", "\u202f") if float(v) == int(float(v))
                          else f"{float(v):,.2f}".replace(",", "\u202f"))
                ) if v is not None else ""
            )
            cfg[col] = st.column_config.TextColumn(col, help="Montant en EUR")
    for col in (pct_cols or []):
        if col in df.columns:
            cfg[col] = st.column_config.NumberColumn(col, format="%.2f %%")
    for col in (note_cols or []):
        if col in df.columns:
            cfg[col] = st.column_config.TextColumn(col, width="large",
                help="Explication du calcul (survol pour voir le texte complet)")
    return cfg


def _gated_preview_table(df: "pd.DataFrame", can_export: bool, column_config: dict | None = None,
                          pct: float = 0.15, min_rows: int = 1,
                          unlock_hint: str = "🔒 Aperçu limité avant paiement/abonnement.") -> None:
    """Affiche un tableau de résultats, avec deux comportements :

    - Compte débloqué pour la période (`can_export=True`) : st.dataframe complet.
    - Sinon : aperçu bridé mais montrant le VOLUME total. 
      Seules les 10 premières lignes (ou 15% du total si < 10) sont affichées 
      normalement. Le reste est affiché mais avec les colonnes sensibles masquées.
      Les colonnes Date, Pays et ID restent visibles partout.
    """
    n = len(df)
    if can_export or n == 0:
        st.dataframe(df, use_container_width=True, hide_index=True, column_config=column_config or {})
        return

    # 1. Calcul de la limite "en clair" (15% plafonné à 10)
    n_full_visible = min(10, math.ceil(n * pct))

    # On travaille sur une copie intégrale pour montrer tout le volume
    df_preview = df.copy()

    # 2. Masquage des données sensibles pour les lignes > n_full_visible
    if n > n_full_visible:
        for col in df_preview.columns:
            col_l = col.lower()

            # Liste des colonnes à garder en clair
            is_date = any(x in col_l for x in ["date", "période", "period", "mois", "month", "année", "year"])
            is_country = any(x in col_l for x in ["pays", "country", "dep", "dest", "arr", "origin", "départ", "arrivée", "stock"])
            is_id = any(x in col_l for x in ["id", "order", "commande", "transaction", "asin"])

            # Sécurité supplémentaire : si "montant" ou "tva" est dans le nom, on verrouille quand même
            is_money = any(x in col_l for x in ["montant", "tva", "ht", "eur", "total", "taux", "rate"])

            if (is_date or is_country or is_id) and not is_money:
                continue # On garde ces informations pour le rapprochement

            # Pour tout le reste (Scénarios, Montants...), on masque
            mask_val = "[🔒 Verrouillé]"
            if any(x in col_l for x in ["régime", "scenario", "scénario", "fiscal"]):
                mask_val = "[🔒 Option Premium]"

            # Conversion en object pour accepter le texte
            if df_preview[col].dtype.kind in 'ifc' or df_preview[col].dtype == 'float64':
                df_preview[col] = df_preview[col].astype(object)

            # Application du masque à partir de la ligne n_full_visible
            df_preview.iloc[n_full_visible:, df_preview.columns.get_loc(col)] = mask_val

    # 3. Ajustement du column_config pour éviter les erreurs de type (ex: NumberColumn vs String)
    preview_config = (column_config or {}).copy()
    if n > n_full_visible:
        for col in df_preview.columns:
            # Si la colonne contient maintenant du texte de verrouillage, on force le type TextColumn
            # pour éviter le warning "The value cannot be interpreted as a number"
            if df_preview[col].dtype == object and df_preview[col].astype(str).str.contains("🔒").any():
                preview_config[col] = st.column_config.TextColumn(col)

    # 4. Affichage via st.dataframe
    st.dataframe(df_preview, use_container_width=True, hide_index=True, column_config=preview_config)

    st.caption(f"{unlock_hint} {n_full_visible} ligne(s) détaillées sur {n} au total. "
               "Débloquez la période pour accéder aux calculs de TVA sur l'intégralité du fichier.")


def render_oss_threshold_bar(oss_summary: Any) -> None:
    """Affiche la barre de progression du seuil OSS 10 000 EUR."""
    if not oss_summary:
        return

    def _oss_gradient_color(pct: float) -> str:
        """Vert -> orange -> rouge selon la proximité du seuil (pct entre 0 et 1)."""
        if pct < 0.7:
            return "#2ca02c"
        elif pct < 0.9:
            return "#d97706"
        else:
            return "#d62728"

    _oss_ht = float(oss_summary.total_oss_ht)
    _oss_pct = min(_oss_ht / 10_000.0, 1.0)
    _oss_by_year = getattr(oss_summary, "oss_ht_by_year", {})

    if len(_oss_by_year) > 1:
        # Multi-année : préciser que la barre concerne la dernière année du fichier
        _last_year = max(_oss_by_year.keys())
        _oss_year_label = f" (année **{_last_year}**)"
    else:
        _oss_year_label = ""

    _bar_color = _oss_gradient_color(_oss_pct)

    st.markdown("""
    <style>
    .oss-bar-track {
        width: 100%;
        height: 14px;
        border-radius: 7px;
        background-color: color-mix(in srgb, var(--primary-color) 10%, transparent);
        overflow: hidden;
        margin: 6px 0 4px;
    }
    .oss-bar-fill {
        height: 100%;
        border-radius: 7px;
        transition: width 0.3s ease;
    }
    </style>
    """, unsafe_allow_html=True)

    st.markdown(
        f"**Seuil OSS**{_oss_year_label} : {_oss_ht:,.2f} € / 10 000 € HT "
        f"({'dépassé' if _oss_ht >= 10_000 else f'{_oss_pct*100:.1f} %'})"
    )
    st.markdown(f"""
    <div class="oss-bar-track">
        <div class="oss-bar-fill" style="width:{_oss_pct*100}%; background-color:{_bar_color};"></div>
    </div>
    """, unsafe_allow_html=True)
