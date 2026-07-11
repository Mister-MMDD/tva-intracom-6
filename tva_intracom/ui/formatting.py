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

import pandas as pd
import streamlit as st
from tva_intracom.i18n import _

from tva_intracom.rates import COUNTRY_NAMES


def _fec_period_end_date(period: str) -> str:
    """Calcule la date de fin de période au format AAAAMMJJ (FEC EcritureDate)
    à partir du libellé de période détecté (ex: '2026-Q2', '2026-T2',
    '2026-06', '2026'). Retombe sur la date du jour si le format n'est pas
    reconnu — cohérent avec le fait que la date d'écriture n'est qu'un
    repère de comptabilisation, pas une donnée fiscale opposable en soi
    (contrairement à la période elle-même, mentionnée dans le libellé de
    compte ou de journal)."""
    import datetime
    _today = datetime.date.today()
    if not period:
        return _today.strftime("%Y%m%d")
    _p = period.upper().strip()
    if len(_p) == 4 and _p.isdigit():
        return f"{_p}1231"
    if len(_p) == 7 and _p[4] == "-" and _p[5:].isdigit():
        _y = int(_p[:4])
        _m = int(_p[5:])
        if _m in (1, 3, 5, 7, 8, 10, 12):
            return f"{_y}{_m:02d}31"
        elif _m in (4, 6, 9, 11):
            return f"{_y}{_m:02d}30"
        elif _m == 2:
            _leap = (_y % 4 == 0 and (_y % 100 != 0 or _y % 400 == 0))
            return f"{_y}0229" if _leap else f"{_y}0228"
    if len(_p) >= 7 and "-Q" in _p:
        try:
            _parts = _p.split("-Q")
            _y = int(_parts[0])
            _q = int(_parts[1].split("_")[0])
            _mapping = {1: "0331", 2: "0630", 3: "0930", 4: "1231"}
            return f"{_y}{_mapping.get(_q, '1231')}"
        except Exception:
            pass
    if len(_p) >= 7 and "-T" in _p:
        try:
            _parts = _p.split("-T")
            _y = int(_parts[0])
            _q = int(_parts[1].split("_")[0])
            _mapping = {1: "0331", 2: "0630", 3: "0930", 4: "1231"}
            return f"{_y}{_mapping.get(_q, '1231')}"
        except Exception:
            pass
    if len(_p) >= 7 and "-S" in _p:
        try:
            _parts = _p.split("-S")
            _y = int(_parts[0])
            _s = int(_parts[1].split("_")[0])
            return f"{_y}0630" if _s == 1 else f"{_y}1231"
        except Exception:
            pass
    return _today.strftime("%Y%m%d")


def _render_filter_bar(df: pd.DataFrame, key_suffix: str) -> pd.DataFrame:
    """Affiche une barre de filtres (Recherche, Destination, Scénario, Canal) 
    et retourne le DataFrame filtré. Utilisé uniformément sur tous les tableaux.
    """
    _fa, _fb, _fc, _fd = st.columns([2, 2, 2, 2])
    
    with _fa:
        _search = st.text_input(_("filter_search"), placeholder=_("filter_search_placeholder"), key=f"search_{key_suffix}")
    
    with _fb:
        _dest_opts = sorted(df["Dest"].unique()) if "Dest" in df.columns else []
        _dest_sel = st.multiselect(_("filter_dest"), _dest_opts, key=f"dest_{key_suffix}", 
                                   placeholder=_("filter_dest_placeholder"))
        
    with _fc:
        _scen_opts = sorted(df["Scénario"].unique()) if "Scénario" in df.columns else []
        _scen_sel = st.multiselect(_("filter_scenario"), _scen_opts, key=f"scen_{key_suffix}", 
                                   placeholder=_("filter_scenario_placeholder"))
        
    with _fd:
        _canal_opts = sorted(df["Canal"].unique()) if "Canal" in df.columns else []
        _canal_sel = st.multiselect(_("filter_canal"), _canal_opts, key=f"canal_{key_suffix}", 
                                   placeholder=_("filter_canal_placeholder"))
        
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
    if value is None:
        return "—"
    try:
        v = float(value)
    except (ValueError, TypeError):
        return str(value)

    if math.isnan(v):
        return "—"
    if math.isinf(v):
        return "∞ €"

    if v == int(v):
        return f"{int(v):,} €".replace(",", " ")
    return f"{v:,.2f} €".replace(",", " ")


def _country_label(code: str) -> str:
    """Retourne le nom complet du pays à partir de son code ISO (ex: FR -> France)."""
    if not code:
        return ""
    _c = str(code).upper().strip()
    return COUNTRY_NAMES.get(_c, _c)


# Helpers column_config réutilisables
# ── Colonne monétaire : tri numérique conservé, affichage smart (0 déc. ou 2 déc.)
def _money_col(label: str, help_txt: str = "") -> st.column_config.NumberColumn:
    """NumberColumn monétaire : entier si .00, sinon 2 décimales."""
    return st.column_config.NumberColumn(
        label,
        format="%.2f €",   # Streamlit applique toujours 2 déc. dans l'affichage natif
        help=help_txt,
    )


def _pct_col(label: str, help_txt: str = "") -> st.column_config.NumberColumn:
    """NumberColumn pourcentage : 1 décimale, suffixe %."""
    return st.column_config.NumberColumn(
        label,
        format="%.1f%%",
        help=help_txt,
    )


def _smart_money_df(
    df: pd.DataFrame,
    money_cols: list[str] = None,
    pct_cols: list[str] = None,
    note_cols: list[str] = None,
    existing_config: dict = None
) -> dict[str, Any]:
    """Génère un column_config Streamlit pour les colonnes monétaires et de taux.
    
    Règle d'affichage monétaire : entier si pas de décimale significative, sinon 2 déc.
    Streamlit NumberColumn avec format="%.2f €" affiche toujours 2 déc.
    On contourne en pré-formatant les valeurs en string et en utilisant TextColumn
    pour les colonnes qui nécessitent l'affichage smart (0 ou 2 déc.).
    
    Stratégie retenue : pré-formater les colonnes monétaires en string dans le DataFrame
    (les valeurs sont déjà des floats, on les formate avant st.dataframe).
    Cette fonction retourne le column_config à passer à st.dataframe.
    """
    column_config = existing_config.copy() if existing_config else {}
    m_cols = money_cols or []
    p_cols = pct_cols or []
    n_cols = note_cols or []
    
    for col in df.columns:
        if col in column_config:
            continue

        col_lower = col.lower()
        # Pré-formatage dans le df : on remplace les floats par des strings formatées
        if col in m_cols or any(k in col_lower for k in ["montant", "tva", "ttc", "ht", "total", "remboursé"]):
            column_config[col] = st.column_config.TextColumn(col)
            # On applique le formatage smart sur la colonne
            df[col] = df[col].apply(_fmt)
        elif col in p_cols or any(k in col_lower for k in ["taux", "pct", "rate"]):
            column_config[col] = _pct_col(col)
        elif col in n_cols:
            column_config[col] = st.column_config.TextColumn(col)
            
    return column_config


def _gated_preview_table(
    df: pd.DataFrame,
    can_export: bool,
    pct: float = 0.15,
    min_rows: int = 1,
    key: str = None,
    column_config: dict = None
) -> None:
    """Affiche un tableau de résultats, avec deux comportements :
    
    - Compte débloqué pour la période (`can_export=True`) : st.dataframe complet.
    - Sinon : aperçu bridé mais montrant le VOLUME total.
    Seules les 10 premières lignes (ou 15% du total si < 10) sont affichées
    normalement. Le reste est affiché mais avec les colonnes sensibles masquées.
    Les colonnes Date, Pays et ID restent visibles partout.
    """
    if can_export:
        config = _smart_money_df(df, existing_config=column_config)
        st.dataframe(df, use_container_width=True, column_config=config, hide_index=True)
        return

    # 1. Calcul de la limite "en clair" (15% plafonné à 10)
    n_total = len(df)
    n_full_visible = max(min_rows, min(10, int(n_total * pct)))

    # On travaille sur une copie intégrale pour montrer tout le volume
    df_preview = df.copy()

    # 2. Masquage des données sensibles pour les lignes > n_full_visible
    # On identifie les colonnes à masquer
    lock_msg = "🔒 " + _("gated_locked")
    
    # Liste des colonnes à garder en clair
    safe_cols = ["Date", "Pays", "Dest", "ID", "Transaction", "Type"]
    
    for col in df_preview.columns:
        # Sécurité supplémentaire : si "montant" ou "tva" est dans le nom, on verrouille quand même
        col_lower = col.lower()
        if col in safe_cols and not any(k in col_lower for k in ["montant", "tva", "ttc", "ht"]):
            continue # On garde ces informations pour le rapprochement
            
        # Pour tout le reste (Scénarios, Montants...), on masque
        # On vérifie si la colonne est numérique ou non pour le message
        # de verrouillage (Streamlit dataframe n'aime pas trop les types mixtes)
        
        # Conversion en object pour accepter le texte
        df_preview[col] = df_preview[col].astype(object)
        
        # Application du masque à partir de la ligne n_full_visible
        df_preview.iloc[n_full_visible:, df_preview.columns.get_loc(col)] = lock_msg

    # 3. Ajustement du column_config pour éviter les erreurs de type (ex: NumberColumn vs String)
    config = _smart_money_df(df_preview, existing_config=column_config)
    
    for col in df_preview.columns:
        # Si la colonne contient maintenant du texte de verrouillage, on force le type TextColumn
        # sauf si l'on est déjà sur une TextColumn (on vérifie via le nom de classe pour la robustesse
        # face à certaines versions de Streamlit où TextColumn est une factory et non un type).
        current_config = config.get(col)
        is_text_col = current_config is not None and type(current_config).__name__ == "TextColumn"
        
        if not is_text_col:
             if df_preview[col].dtype == object and any(lock_msg in str(x) for x in df_preview[col] if x is not None):
                config[col] = st.column_config.TextColumn(col)

    # 4. Affichage via st.dataframe
    st.dataframe(df_preview, use_container_width=True, column_config=config, hide_index=True, key=key)
    st.warning(_("gated_preview_warning", count=n_total - n_full_visible))


def render_oss_threshold_bar(oss_summary: Any) -> None:
    """Affiche la barre de progression du seuil OSS 10 000 EUR."""

    def _color(pct: float) -> str:
        """Vert -> orange -> rouge selon la proximité du seuil (pct entre 0 et 1)."""
        if pct < 0.7: return "#2ca02c"
        if pct < 0.9: return "#d97706"
        return "#d62728"

    total_oss = float(oss_summary.total_oss_ht)
    pct = min(total_oss / 10000.0, 1.0)
    
    _oss_by_year = getattr(oss_summary, "oss_ht_by_year", {})
    if len(_oss_by_year) > 1:
        # Multi-année : préciser que la barre concerne la dernière année du fichier
        _last_year = max(_oss_by_year.keys())
        _label = _("oss_threshold_multi_year", year=_last_year)
    else:
        _label = _("oss_threshold_label")

    st.write(f"**{_label}**")
    st.progress(pct, text=f"{_fmt(total_oss)} / 10 000 €")
    
    if total_oss < 10000:
        st.caption(_("oss_threshold_help", remaining=_fmt(10000 - total_oss)))
    else:
        st.success(_("oss_threshold_exceeded"))
