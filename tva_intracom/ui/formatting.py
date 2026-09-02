"""Helpers de formatage et d'affichage réutilisés par plusieurs onglets.

Extraits tel quel de app.py (aucune modification de comportement) :
  - _fmt / country_label : formatage montant et libellé pays
  - _money_col / _pct_col : column_config Streamlit réutilisables
  - _smart_money_df       : column_config auto pour un DataFrame (montants + taux)
  - _gated_preview_table  : aperçu bridé tant que la période n'est pas débloquée

Les noms restent préfixés par underscore pour ne rien casser côté imports
existants (`from tva_intracom.ui.formatting import _fmt, ...`).
"""

from __future__ import annotations

import math
from decimal import Decimal
from typing import Any, Optional

import pandas as pd
import streamlit as st

from tva_intracom.i18n import _


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


# PERF/SÉCURITÉ (2026-09-03) : plafond appliqué à l'option "Tous" des
# sélecteurs `rows_per_page_label` (detail_ventes.py, audit.py). Sans ce
# plafond, un fichier de plusieurs dizaines/centaines de milliers de lignes
# avec "Tous" sélectionné force `st.dataframe` à sérialiser l'intégralité
# du tableau en JSON : le CPU serveur (vCPU partagé Streamlit Cloud) sature
# le temps de la sérialisation, ET le navigateur du client peut geler en
# tentant d'afficher un tableau de cette taille. Le compteur déjà affiché
# par ailleurs (`results_count_caption`, "visible=X sur Y") continue de
# signaler à l'utilisateur que l'affichage est borné ; au-delà, l'export
# Excel (onglet Téléchargements) reste la voie recommandée pour consulter
# l'intégralité des lignes.
MAX_ROWS_ALL_DISPLAY = 5000


def _resolve_display_limit(selected_option, filtered_count: int) -> int:
    """Résout le nombre de lignes à afficher à partir de la valeur choisie
    dans un `st.select_slider` "lignes par page" (voir MAX_ROWS_ALL_DISPLAY
    ci-dessus pour le rationnel du plafond sur l'option "Tous")."""
    if selected_option == _("rows_all"):
        return min(filtered_count, MAX_ROWS_ALL_DISPLAY)
    return int(selected_option)


def _render_filter_bar(df: pd.DataFrame, key_suffix: str) -> pd.DataFrame:
    """Affiche une barre de filtres (Recherche, Destination, Scénario, Canal) 
    et retourne le DataFrame filtré. Utilisé uniformément sur tous les tableaux.
    """
    _fa, _fb, _fc, _fd = st.columns([2, 2, 2, 2])
    
    with _fa:
        _search = st.text_input(_("filter_search"), placeholder=_("filter_search_placeholder"), key=f"search_{key_suffix}")
    
    with _fb:
        _dest_opts = sorted([str(x) for x in df["Dest"].unique() if pd.notna(x)]) if "Dest" in df.columns else []
        _dest_sel = st.multiselect(_("filter_dest"), _dest_opts, key=f"dest_{key_suffix}", 
                                   placeholder=_("filter_dest_placeholder"))
        
    with _fc:
        _canal_opts = sorted([str(x) for x in df["Canal"].unique() if pd.notna(x)]) if "Canal" in df.columns else []
        _canal_sel = st.multiselect(_("filter_canal"), _canal_opts, key=f"canal_{key_suffix}", 
                                   placeholder=_("filter_canal_placeholder"))
        
    with _fd:
        _scen_opts = sorted([str(x) for x in df["Scénario"].unique() if pd.notna(x)]) if "Scénario" in df.columns else []
        _scen_sel = st.multiselect(_("filter_scenario"), _scen_opts, key=f"scen_{key_suffix}", 
                                   placeholder=_("filter_scenario_placeholder"))
        
    df_filt = df # On évite la copie systématique ici
    
    if _search:
        _search_cols = [c for c in ("ID", "Note", "Transaction") if c in df_filt.columns]
        if _search_cols:
            # Une seule colonne concaténée + un seul scan .str.contains(), au
            # lieu d'une conversion .astype(str) et d'un scan par colonne
            # (3x le travail sur un DataFrame identique). Le gain se voit
            # surtout au-delà de 50k lignes. Le séparateur "\u0001" (non
            # imprimable) évite qu'une recherche ne matche accidentellement
            # à cheval sur deux colonnes concaténées.
            _search_index = df_filt[_search_cols[0]].fillna("").astype(str)
            for col in _search_cols[1:]:
                _search_index = _search_index.str.cat(
                    df_filt[col].fillna("").astype(str), sep="\u0001", na_rep=""
                )
            mask = _search_index.str.contains(_search, case=False, na=False)
            df_filt = df_filt[mask]
        
    if _dest_sel and "Dest" in df_filt.columns:
        df_filt = df_filt[df_filt["Dest"].isin(_dest_sel)]
    if _scen_sel and "Scénario" in df_filt.columns:
        df_filt = df_filt[df_filt["Scénario"].isin(_scen_sel)]
    if _canal_sel and "Canal" in df_filt.columns:
        df_filt = df_filt[df_filt["Canal"].isin(_canal_sel)]
        
    return df_filt


def _get_conversion_rate() -> tuple[str, float]:
    """Devise cible + taux de conversion EUR -> devise cible pour la session en
    cours (home_country choisi). Mis en cache dans st.session_state pour éviter
    un appel BCE répété à chaque cellule affichée. Retombe sur (EUR, 1.0) si la
    devise cible est l'EUR ou si le taux BCE est indisponible (le montant EUR
    calculé par le moteur reste alors affiché tel quel, plutôt que de planter
    l'affichage)."""
    target_currency = st.session_state.get("target_currency", "EUR")
    if not target_currency or target_currency == "EUR":
        return "EUR", 1.0
    cache_key = f"_fx_rate_{target_currency}"
    if cache_key in st.session_state:
        return target_currency, st.session_state[cache_key]
    try:
        from tva_intracom.ecb_rates import get_rate
        import datetime
        rate = get_rate(target_currency, datetime.date.today())
        rate = float(rate) if rate else 1.0
    except Exception:
        rate = 1.0
    st.session_state[cache_key] = rate
    return target_currency, rate


def _fmt(value, symbol=None) -> str:
    """Formate un montant : 13 → '13 €', 13.5 → '13.50 €', 13.00 → '13 €'.

    Si `symbol` n'est PAS fourni : le montant est supposé être en EUR (devise
    de calcul interne du moteur fiscal) et est converti vers la devise cible
    du pays d'origine (home_country) avant affichage, au taux BCE du jour
    (voir _get_conversion_rate). C'est le cas d'usage par défaut (KPIs,
    tableaux de résultats).
    Si `symbol` EST fourni explicitement : aucune conversion n'est appliquée —
    utile pour afficher un montant déjà dans sa devise d'origine (ex. montant
    de transaction non-EUR affiché tel quel dans la colonne "Montant orig.").

    Note précision : quand `symbol` est fourni (pas de conversion FX), une
    valeur `Decimal` (calcul fiscal) est formatée directement depuis le
    Decimal, sans passage par `float`, pour éviter un écart d'arrondi de
    0.01€ possible entre cet affichage et un total calculé ailleurs en
    Decimal (ex. tableau de bord vs graphique Plotly, qui lui doit rester
    en float pour ses besoins internes). Quand une conversion FX est
    nécessaire (`symbol is None`), le passage par float reste inévitable
    (le taux BCE lui-même est un float)."""
    if value is None:
        return "—"

    if symbol is not None and isinstance(value, Decimal):
        if value.is_nan():
            return "—"
        if value.is_infinite():
            return f"∞ {symbol}"
        v_dec = value.quantize(Decimal("0.01"))
        if v_dec == v_dec.to_integral_value():
            return f"{int(v_dec):,} {symbol}".replace(",", " ")
        return f"{v_dec:,.2f} {symbol}".replace(",", " ")

    try:
        v = float(value)
    except (ValueError, TypeError):
        return str(value)

    if math.isnan(v):
        return "—"

    if symbol is None:
        _currency, _rate = _get_conversion_rate()
        v = v * _rate
        symbol = st.session_state.get("currency_symbol", "€")

    if math.isinf(v):
        return f"∞ {symbol}"

    if v == int(v):
        return f"{int(v):,} {symbol}".replace(",", " ")
    return f"{v:,.2f} {symbol}".replace(",", " ")


# Helpers column_config réutilisables
# ── Colonne monétaire : tri numérique conservé, affichage smart (0 déc. ou 2 déc.)
def _money_col(label: str, help_txt: str = "", symbol=None, width="small"):
    """NumberColumn monétaire : entier si .00, sinon 2 décimales.

    Pas d'annotation de retour : `st.column_config.NumberColumn` est une
    fonction factory Streamlit (pas une classe), donc invalide comme
    annotation de type — l'IDE la signalait comme "Invalid type annotation".
    """
    if symbol is None:
        symbol = st.session_state.get("currency_symbol", "€")
        
    return st.column_config.NumberColumn(
        label,
        format=f"%.2f {symbol}",   # Streamlit applique toujours 2 déc. dans l'affichage natif
        help=help_txt,
        width=width,
    )


def _pct_col(label: str, help_txt: str = "", width="small"):
    """NumberColumn pourcentage : 1 décimale, suffixe % (cf. _money_col
    pour la raison de l'absence d'annotation de retour)."""
    return st.column_config.NumberColumn(
        label,
        format="%.1f%%",
        help=help_txt,
        width=width,
    )


def _smart_money_df(
    df: pd.DataFrame,
    money_cols: Optional[list[str]] = None,
    pct_cols: Optional[list[str]] = None,
    note_cols: Optional[list[str]] = None,
    existing_config: Optional[dict] = None
) -> dict[str, Any]:
    """Génère un column_config Streamlit optimisé.
    
    Amélioration Performance : utilise la vectorisation Pandas pour les conversions.
    Amélioration UX : utilise NumberColumn pour conserver le tri numérique correct.

    ATTENTION effet de bord assumé : cette fonction MUTE le DataFrame reçu
    (conversion de devise en place sur les colonnes monétaires) — c'est un
    contrat volontaire dont dépendent tous les appelants actuels (le même
    objet `df` est réaffiché juste après via `_gated_preview_table`/
    `st.dataframe`, sans quoi les montants convertis n'apparaîtraient
    jamais). Le rendre "pur" casserait silencieusement l'affichage devise
    dans toute l'app.

    Garde-fou anti double-conversion : un DataFrame déjà passé une fois par
    cette fonction est marqué via `df.attrs`, et un second appel sur le même
    objet (ex. réutilisation accidentelle, référence partagée par un cache)
    n'applique plus le taux une seconde fois.
    """
    _already_converted = bool(df.attrs.get("_tva_currency_converted"))
    column_config = existing_config.copy() if existing_config else {}
    m_cols = money_cols or []
    p_cols = pct_cols or []
    n_cols = note_cols or []
    
    # Récupération du taux une seule fois pour tout le tableau
    _target_curr, _rate = _get_conversion_rate()
    _symbol = st.session_state.get("currency_symbol", "€")

    for col in df.columns:
        if col in column_config:
            continue

        col_lower = col.lower()

        # 0. Colonnes IDENTIFIANTS (numéro de TVA, ID, etc.) — À VÉRIFIER EN
        # PREMIER, avant la détection "montant" ci-dessous. Sans cette
        # priorité, un libellé comme "N° TVA rejeté" matche la sous-chaîne
        # "tva" du test monétaire (pensé pour "Montant TVA"/"TVA collectée")
        # par pure coïncidence lexicale — "TVA" désigne à la fois la taxe et
        # apparaît dans "numéro de TVA". Un numéro de TVA n'est PAS un
        # montant, même si son libellé contient le mot "tva". Cf. incident :
        # numéros de TVA italiens (stockés sans préfixe "IT" à cet endroit,
        # donc purement numériques) reformatés en "1 234 567 890 €" pour les
        # utilisateurs gratuits (_gated_preview_table applique _fmt(), qui
        # réussit un float() sur une chaîne purement numérique).
        if any(k in col_lower for k in ["n°", "numéro", "numero", "num."]) or "id" in col_lower:
            column_config[col] = st.column_config.TextColumn(col, width="medium")
            continue

        # 1. Colonnes de notes (Texte long)
        if col in n_cols or "note" in col_lower or "commentaire" in col_lower:
            column_config[col] = st.column_config.TextColumn(col)
            
        # 2. Colonnes Monétaires (Tri numérique préservé)
        elif col in m_cols or any(k in col_lower for k in ["montant", "tva", "ttc", "ht", "total", "remboursé"]):
            # Conversion vectorisée si nécessaire (plus rapide que .apply)
            if _rate != 1.0 and not _already_converted and df[col].dtype in ['float64', 'int64']:
                df[col] = df[col] * _rate
            
            column_config[col] = st.column_config.NumberColumn(
                col, 
                format=f"%.2f {_symbol}", 
                width="small"
            )
            
        # 3. Pourcentages
        elif col in p_cols or any(k in col_lower for k in ["taux", "pct", "rate"]):
            column_config[col] = _pct_col(col)
            
        # 4. Colonnes ID/Codes (Largeurs fixes)
        elif any(k == col_lower for k in ["stock", "dest", "pays", "devise", "départ"]):
            column_config[col] = st.column_config.TextColumn(col, width=40)
        elif any(k == col_lower for k in ["date", "canal", "type", "collecteur", "collector", "scénario", "scenario"]):
            column_config[col] = st.column_config.TextColumn(col, width="small")
        elif "id" in col_lower:
            column_config[col] = st.column_config.TextColumn(col, width="medium")

    if _rate != 1.0 and not _already_converted:
        df.attrs["_tva_currency_converted"] = True

    return column_config


def _gated_preview_table(
    df: pd.DataFrame,
    can_export: bool,
    pct: float = 0.15,
    min_rows: int = 1,
    key: Optional[str] = None,
    column_config: Optional[dict] = None,
    total_count: Optional[int] = None,
    extra_safe_cols: Optional[list[str]] = None,
    lock_all: bool = False,
    exclude_safe_cols: Optional[list[str]] = None
) -> None:
    """Affiche un tableau de résultats avec protection des données sensibles."""
    if can_export:
        config = _smart_money_df(df, existing_config=column_config)
        st.dataframe(df, width="stretch", column_config=config, hide_index=True)
        return

    # PERFORMANCE : Si bridé, on ne traite qu'un échantillon pour économiser la RAM
    n_total = total_count if total_count is not None else len(df)
    
    # Règle de masquage : au moins 'min_rows' (1 par défaut) en clair, 
    # dans la limite de 'pct' (15%) du total, et sans dépasser 10 lignes.
    n_clear = max(min_rows, math.ceil(n_total * pct))
    n_clear = min(n_clear, 10)
    
    n_visible = max(n_clear + 5, min(20, len(df))) 
    
    # On crée un aperçu léger
    df_preview = df.head(n_visible).copy()
    
    # PERFORMANCE : On formate les nombres en strings AVANT de masquer, 
    # pour garder un bel affichage (espaces, €) dans les lignes visibles.
    # On force également le type object pour permettre l'insertion ultérieure 
    # du cadenas (chaîne) dans des colonnes initialement numériques sans
    # provoquer de TypeError sur les versions récentes de pandas/numpy.
    # Détection des colonnes "identifiant" (n° TVA, n° commande…) à exclure du
    # formatage monétaire — sans quoi un n° de TVA sans séparateur (11 chiffres
    # IT, 10 chiffres PL…) peut être interprété comme un montant par _fmt() et
    # affiché multiplié par le taux de change. Ajout de "nº" (variante typo
    # avec indicateur ordinal ° vs º) en plus de "n°" déjà couvert.
    #
    # Piste explorée et ABANDONNÉE : ajouter "vat"/"iva"/"mwst" comme
    # marqueurs identifiant pour couvrir les n° TVA traduits (audit externe,
    # point 7). Rejetée après vérification des i18n/*.toml : ces mots
    # apparaissent aussi dans de VRAIS libellés de colonnes MONTANT
    # (col_vat_eur = "VAT (EUR)" / "MwSt (EUR)" / "IVA (EUR)",
    # col_tva_amz_eur = "Amazon VAT (EUR)"...) — les marquer comme
    # identifiant aurait cassé le formatage monétaire dans 6 langues sur 7.
    # Aucun bug actif constaté sur les libellés de colonnes VAT-ID
    # réellement utilisés dans l'app (voir README évolution) ; risque/gain
    # défavorable pour un fix plus large ici.
    _ID_MARKERS = ("n°", "nº", "numéro", "numero", "num.")
    for col in df_preview.columns:
        col_lower = col.lower()
        is_identifier_col = (
            any(k in col_lower for k in _ID_MARKERS)
            or "id" in col_lower
        )
        if not is_identifier_col and any(k in col_lower for k in ["montant", "tva", "ttc", "ht", "total"]):
            df_preview[col] = df_preview[col].apply(lambda x: _fmt(x) if pd.notna(x) else "—").astype(object)
        else:
            df_preview[col] = df_preview[col].astype(str).astype(object)

    lock_msg = "🔒 " + _("gated_locked")
    safe_cols = [] if lock_all else ["Date", "Pays", "Dest", "ID", "Transaction", "Type", "Stock"]
    if extra_safe_cols:
        safe_cols.extend(extra_safe_cols)
    
    if exclude_safe_cols:
        safe_cols = [c for c in safe_cols if c not in exclude_safe_cols]
    
    # Masquage sur l'échantillon
    for i, col in enumerate(df_preview.columns):
        if col not in safe_cols:
            df_preview.iloc[n_clear:, i] = lock_msg

    # Pour un aperçu masqué, on utilise TextColumn partout car les types sont mixtes.
    # On doit forcer TextColumn même si column_config demande du numérique, 
    # pour éviter l'erreur "The value cannot be interpreted as a number" sur le cadenas.
    config = {}
    for col in df_preview.columns:
        label = col
        if column_config and col in column_config:
            c_orig = column_config[col]
            # On essaie d'extraire le label (soit via l'attribut .label de l'objet config,
            # soit via une clé 'label' si c'est un dict).
            label = getattr(c_orig, "label", None) or (c_orig.get("label") if isinstance(c_orig, dict) else None) or col
        config[col] = st.column_config.TextColumn(label)

    st.dataframe(df_preview, width="stretch", column_config=config, hide_index=True, key=key)
    
    if n_total > min_rows:
        # Le nombre de lignes masquées est le total moins les lignes affichées en clair (min_rows)
        st.warning(_("gated_preview_warning", count=n_total - min_rows))


def render_oss_threshold_bar(oss_summary: Any) -> None:
    """Affiche la barre de progression du seuil OSS 10 000 EUR (Art. 59 quater
    Dir. 2006/112/CE).

    Le seuil légal est fixe en EUR ; pour les devises hors zone euro qui
    publient une contre-valeur nationale FIXE de ce seuil
    (rates.OSS_THRESHOLD_FIXED_EQUIVALENTS — ex. 42 000 PLN, 256 530 CZK...),
    on affiche cette contre-valeur fixe plutôt qu'une conversion au taux BCE
    du jour, qui ferait fluctuer quotidiennement un seuil légal censé rester
    stable. Le cumul de ventes est mis à l'échelle du même rapport implicite
    (contre-valeur fixe / 10 000) pour rester visuellement cohérent avec ce
    seuil. Pour une devise sans contre-valeur fixe publiée (ex. GBP, hors
    périmètre OSS), repli sur le taux BCE du jour pour les deux termes."""
    from tva_intracom.rates import oss_threshold_in_currency
    from decimal import Decimal as _Decimal

    _currency, _rate = _get_conversion_rate()
    symbol = st.session_state.get("currency_symbol", "€")

    def _color(pct: float) -> str:
        """Vert -> orange -> rouge selon la proximité du seuil (pct entre 0 et 1)."""
        if pct < 0.7: return "#2ca02c"
        if pct < 0.9: return "#d97706"
        return "#d62728"

    limit_eur = 10000.0
    limit_local = float(oss_threshold_in_currency(_currency, _Decimal(str(_rate)) if _rate else None))
    # Rapport implicite (contre-valeur fixe / seuil EUR) utilisé pour mettre le
    # cumul de ventes à l'échelle de façon cohérente avec le seuil affiché.
    _ratio = (limit_local / limit_eur) if limit_local else _rate
    total_oss = float(oss_summary.total_oss_ht) * _ratio
    limit_display = limit_local
    limit_text = f"{limit_local:,.2f} {symbol}".replace(",", " ")

    pct = min(total_oss / limit_display if limit_display > 0 else 0, 1.0)
    
    _oss_by_year = getattr(oss_summary, "oss_ht_by_year", {})
    if len(_oss_by_year) > 1:
        # Multi-année : préciser que la barre concerne la dernière année du fichier
        _last_year = max(_oss_by_year.keys())
        _label = _("oss_threshold_multi_year", year=_last_year)
    else:
        _label = _("oss_threshold_label")

    st.write(f"**{_label}**")
    st.progress(pct, text=f"{total_oss:,.2f} {symbol} / {limit_text}".replace(",", " "))
    
    if total_oss < limit_display:
        remaining_local = limit_display - total_oss
        remaining_text = f"{remaining_local:,.2f} {symbol}".replace(",", " ")
        st.caption(_("oss_threshold_help", remaining=remaining_text, limit=limit_text))
    else:
        st.success(_("oss_threshold_exceeded", limit=limit_text))