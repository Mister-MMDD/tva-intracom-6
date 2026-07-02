"""Application Streamlit — Moteur TVA Intracommunautaire."""
from __future__ import annotations
import tempfile, re
import logging
from decimal import Decimal
from pathlib import Path
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st
import pandas as pd
import sys
from tva_intracom.historical_rates_widget import render_historical_rates_alert

sys.path.insert(0, str(Path(__file__).parent))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)

from tva_intracom.ecb_rates import cache_info as ecb_cache_info
from tva_intracom.vies import get_cache_stats as vies_cache_stats, purge_expired_cache, set_cache_ttl
from tva_intracom.engine import ViesValidationSummary, compute_all, compute_all_with_vies
from tva_intracom.excel_report import export_xlsx
from tva_intracom.models import Scenario
from tva_intracom.rates import EU_COUNTRIES
from tva_intracom.report import build_report, render_report
from tva_intracom.oss_export import build_oss_excel, build_oss_csv
from tva_intracom.ca3_report import generate_ca3_html_report_v2  # (et autres imports nécessaires)
from tva_intracom.oss_xml import generate_oss_xml
from tva_intracom.oss_export import aggregate_oss_results, find_oss_negative_buckets

_ZERO = Decimal("0.00")
from tva_intracom.rates import (
        COUNTRY_NAMES,
        COUNTRY_ISO3,
        COUNTRY_FISCAL_META,
        STANDARD_VAT_RATES,
        EU_COUNTRIES,
    )

def _country_label(code):
    return COUNTRY_NAMES.get(code, code)

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
# =============================================================================
# SIDEBAR
# =============================================================================
_PLATFORM_OPTIONS = [
    "Amazon VAT Transactions Report (TSV)",
    "Mirakl (Fnac, Darty, Leroy Merlin...)",
    "Shopify (orders_export.csv)",
    "WooCommerce / PrestaShop (CSV)",
    "AliExpress / eBay / Temu (CSV)",
]

# =============================================================================
# PAGE CONFIG + PURGE CACHE MAL-PREFIXÉ (une fois par session)
# =============================================================================
st.set_page_config(page_title="TVA Intracommunautaire", page_icon="\U0001f1ea\U0001f1fa", layout="wide")

if "_malformed_vies_purged" not in st.session_state:
    try:
        import sqlite3 as _sq
        from tva_intracom.vies import CACHE_DB_FILE as _VIES_DB
        _EU_CC_P = {"AT","BE","BG","HR","CY","CZ","DK","EE","FI","FR","DE","GR","HU",
                    "IE","IT","LV","LT","LU","MT","NL","PL","PT","RO","SK","SI","ES","SE"}
        _cn = _sq.connect(_VIES_DB)
        _cu = _cn.cursor()
        _cu.execute("SELECT vat_id FROM vies_cache")
        _to_delete = [r[0] for r in _cu.fetchall()
            if len(r[0]) >= 4 and r[0][:2].upper() in _EU_CC_P
            and r[0][2:4].upper() in _EU_CC_P and r[0][:2].upper() != r[0][2:4].upper()]
        if _to_delete:
            _cu.executemany("DELETE FROM vies_cache WHERE vat_id=?", [(_v,) for _v in _to_delete])
            _cn.commit()
        _cn.close()
    except Exception:
        pass
    st.session_state["_malformed_vies_purged"] = True

st.title("\U0001f1ea\U0001f1fa Moteur de TVA Intracommunautaire")

# =============================================================================
# SIDEBAR — en accordéon par thème
# =============================================================================
with st.sidebar:
    st.header("\u2699\ufe0f Options")

    # ── Plateforme source (toujours visible) ──────────────────────────────────
    file_format = st.radio("Plateforme source", _PLATFORM_OPTIONS, index=0)

    # ── Validation & Devises ──────────────────────────────────────────────────
    with st.expander("\U0001f50d Validation & Devises", expanded=True):
        enable_vies = st.checkbox("Valider les numéros TVA B2B via VIES", value=True,
            help="Interroge les serveurs de l'UE pour vérifier chaque numéro de TVA B2B.")
        on_invalid_behavior = "reclassify"
        convert_fx = st.checkbox("Convertir devises via taux BCE", value=True,
            help="Convertit automatiquement les montants non-EUR au taux BCE.")
        if convert_fx:
            info = ecb_cache_info()
            if info["entries"] > 0:
                st.caption(f"\U0001f4be Cache BCE : {info['entries']} taux"
                    + (f" ({info['disk_size_kb']} Ko)" if info["disk_exists"] else "")
                    + (f" — {', '.join(info['currencies'])}" if info["currencies"] else ""))

    # ── Cache VIES ────────────────────────────────────────────────────────────
    with st.expander("\U0001f5c4\ufe0f Cache VIES (SQLite)", expanded=False):
        try:
            _cs = vies_cache_stats()
            _ttl_days = st.slider("TTL du cache (jours)", min_value=7, max_value=365,
                value=_cs["ttl_days"], step=7,
                help="Durée avant revalidation automatique d'un numéro de TVA auprès de VIES.")
            if _ttl_days != _cs["ttl_days"]:
                set_cache_ttl(_ttl_days)
                st.rerun()
            _c1, _c2, _c3 = st.columns(3)
            _c1.metric("Total", _cs["total"])
            _c2.metric("✅ Frais", _cs["fresh"])
            _c3.metric("⏳ Expirés", _cs["expired"])
            if _cs["total"] > 0:
                st.caption(
                    f"Valides : {_cs['valid']} · Invalides : {_cs['invalid']} · "
                    f"Vérifié au plus tôt : {(_cs['oldest_check'] or '—')[:10]}")
            if _cs.get("manual_total", 0) > 0:
                st.markdown("**🖊️ Classifications manuelles**")
                _m1, _m2 = st.columns(2)
                _m1.metric("✅ Valides (B2B)", _cs["manual_valid"])
                _m2.metric("❌ Invalides (B2C)", _cs["manual_invalid"])
            if _cs["expired"] > 0:
                if st.button(f"🗑️ Purger {_cs['expired']} entrée(s) expirée(s)", key="purge_vies_cache"):
                    n = purge_expired_cache()
                    st.success(f"{n} entrée(s) supprimée(s).")
                    st.rerun()
        except Exception as _e:
            st.caption(f"Cache VIES indisponible : {_e}")

    # ── Paramètres d'import ───────────────────────────────────────────────────
    with st.expander("\U0001f4e6 Paramètres d'import", expanded=False):
        ioss_number = st.text_input("Numéro IOSS propre (optionnel)", value="",
            placeholder="ex: IM1234567890",
            help="Si renseigné, les imports B2C ≤ 150 € hors marketplace seront traités en IOSS_DIRECT.")
        seller_is_importer = st.toggle("Vendeur = importateur officiel (DDP)", value=False,
            help="Activez si vous prenez en charge le dédouanement (Incoterms DDP).")
        apply_fr_under_threshold = st.toggle(
            "Appliquer TVA FR sous le seuil OSS (10 000 €)", value=False,
            help=(
                "Si activé, les premières ventes OSS cross-border B2C jusqu'à 10 000 € HT "
                "sont déclarées en TVA française (CA3) au lieu du guichet OSS. "
                "Option réservée aux vendeurs dont le CA OSS annuel est proche ou sous le seuil. "
                "Désactivé par défaut : toutes les ventes OSS sont déclarées via le guichet OSS."
            ),
        )
        countries_with_vat = st.multiselect("Pays où vous avez un numéro TVA local :",
            options=sorted(list(EU_COUNTRIES)),
            help="Incluez tous les pays où vous êtes immatriculé, y compris la France si applicable.")
        encoding = st.selectbox("Encodage du fichier", ["utf-8","latin-1","cp1252"], index=0)

    # ── Catalogue Produits ────────────────────────────────────────────────────
    with st.expander("\U0001f4cb Catalogue Produits (taux réduits)", expanded=False):
        catalog_file = st.file_uploader("Importer le catalogue Amazon",
            type=["csv","tsv","txt","xlsx"],
            help="Colonnes ASIN + PRODUCT-TAX-CODE pour taux réduits.")
        asin_to_category = {}
        if catalog_file is not None:
            try:
                if catalog_file.name.endswith(".xlsx"):
                    df_cat = pd.read_excel(catalog_file)
                elif catalog_file.name.endswith(".csv"):
                    df_cat = pd.read_csv(catalog_file)
                else:
                    df_cat = pd.read_csv(catalog_file, sep="\t")
                df_cat.columns = [c.strip().upper() for c in df_cat.columns]
                asin_col = next((c for c in df_cat.columns if "ASIN" in c), None)
                cat_col  = next((c for c in df_cat.columns if "PRODUCT-TAX-CODE" in c or "TAX-CODE" in c), None)
                if not cat_col:
                    cat_col = next((c for c in df_cat.columns if any(k in c for k in ["TAX","GROUP","CODE","TYPE"])), None)
                if asin_col and cat_col:
                    asin_to_category = {str(a).strip().upper(): str(c).strip().upper()
                        for a, c in zip(df_cat[asin_col], df_cat[cat_col]) if pd.notna(a) and pd.notna(c)}
                    st.success(f"\U0001f4c8 {len(asin_to_category)} ASIN mappés.")
            except Exception as e:
                st.error(f"Erreur catalogue : {e}")

    # ── Période (exports) ─────────────────────────────────────────────────────
    with st.expander("\U0001f4c5 Période & entreprise (exports officiels)", expanded=False):
        from datetime import date as _date
        _now = _date.today()

        st.markdown("**Période fiscale**")
        st.caption(
            "La période est auto-détectée depuis les dates de vos transactions. "
            "Vous pouvez la remplacer manuellement si besoin (ex : agrégation partielle)."
        )

        # ── Affichage de la période auto-détectée (depuis session_state) ──
        _sidebar_results = st.session_state.get("_results", [])
        if _sidebar_results:
            _sd = sorted(
                r.sale.transaction_date for r in _sidebar_results
                if r.sale.transaction_date and len(r.sale.transaction_date) >= 7
            )
            if _sd:
                from datetime import datetime as _dt2
                _sd_min = _dt2.fromisoformat(_sd[0][:10])
                _sd_max = _dt2.fromisoformat(_sd[-1][:10])
                _sy, _sm = _sd_min.year, _sd_min.month
                _ey, _em = _sd_max.year, _sd_max.month
                if _sy != _ey:
                    _detected = f"{_sy}-{_ey}"
                elif _sm == 1 and _em == 12:
                    _detected = str(_sy)
                elif _sm == 1 and _em == 6:
                    _detected = f"{_sy}-S1"
                elif _sm == 7 and _em == 12:
                    _detected = f"{_sy}-S2"
                else:
                    _qmin = (_sm - 1) // 3 + 1
                    _qmax = (_em - 1) // 3 + 1
                    _detected = f"{_sy}-Q{_qmin}" if _qmin == _qmax else f"{_sy}-Q{_qmin}_Q{_qmax}"
                st.markdown(
                    f"✅ Période détectée : **{_detected}**  \n"
                    f"<small style='color:grey'>{_sd[0][:10]} → {_sd[-1][:10]}</small>",
                    unsafe_allow_html=True,
                )
        elif not _sidebar_results:
            st.caption("_(aucune donnée chargée)_")

        _override = st.toggle("Saisir la période manuellement", value=False)

        if _override:
            _years = [str(y) for y in range(_now.year, _now.year - 4, -1)]
            _pcol1, _pcol2 = st.columns(2)
            with _pcol1:
                _period_year = st.selectbox("Année", _years, index=0)
            with _pcol2:
                _period_freq = st.selectbox(
                    "Fréquence",
                    ["Trimestriel", "Semestriel", "Annuel"],
                    index=0,
                    help="Trimestriel : standard OSS. Semestriel/Annuel : pour agrégations multi-fichiers.",
                )
            if _period_freq == "Trimestriel":
                _q = st.selectbox("Trimestre", ["T1 (Jan–Mar)", "T2 (Avr–Jun)", "T3 (Jul–Sep)", "T4 (Oct–Déc)"])
                oss_period = f"{_period_year}-Q{_q[1]}"
            elif _period_freq == "Semestriel":
                _s = st.selectbox("Semestre", ["S1 (Jan–Jun)", "S2 (Jul–Déc)"])
                oss_period = f"{_period_year}-S{_s[1]}"
            else:
                oss_period = _period_year
            st.caption(f"Période saisie : **{oss_period}**")
        else:
            # Auto-détection : sera écrasé par la valeur calculée depuis results
            # (voir plus bas dans tab_dl). Valeur temporaire ici.
            oss_period = "__auto__"

        nom_entreprise   = st.text_input("Nom de l'entreprise", "Mon Entreprise E-commerce")
        siren_entreprise = st.text_input("Numéro SIREN", "123456789")
        tva_fr = st.text_input("Numéro de TVA FR (pour XML OSS)", "FR12345678901")

# =============================================================================
# UPLOAD
# =============================================================================
uploaded_files = st.file_uploader(
    "Déposez vos fichiers ici (un ou plusieurs mois)",
    type=["csv","tsv","txt","xlsx","xls"],
    accept_multiple_files=True,
    help="CSV, TSV, ou Excel. Plusieurs fichiers pour agréger plusieurs mois.",
)

if uploaded_files:
    from tva_intracom.parsers import ParseResult
    from tva_intracom.parsers import amazon as parser_amazon
    from tva_intracom.parsers import mirakl as parser_mirakl
    from tva_intracom.parsers import shopify as parser_shopify
    from tva_intracom.parsers import woocommerce as parser_woocommerce
    from tva_intracom.parsers import aliexpress as parser_aliexpress

    # Déduplication silencieuse
    _seen_file_keys: set = set()
    _deduped: list = []
    _dup_names: list = []
    for _f in uploaded_files:
        _fkey = (_f.name, _f.size)
        if _fkey in _seen_file_keys:
            _dup_names.append(_f.name)
        else:
            _seen_file_keys.add(_fkey)
            _deduped.append(_f)
    if _dup_names:
        st.warning(f"⚠️ **{len(_dup_names)} fichier(s) en double ignoré(s)** : "
            + ", ".join(f"`{n}`" for n in _dup_names))
    uploaded_files = _deduped

    all_sales, all_refunds, all_fc_transfers = [], [], []
    all_stock_countries, all_warnings, all_platforms = set(), [], []
    total_rows_sum = skipped_rows_sum = 0
    file_summaries, tmp_paths, _parse_results = [], [], []

    for uploaded_file in uploaded_files:
        _ext = Path(uploaded_file.name).suffix or ".csv"
        with tempfile.NamedTemporaryFile(delete=False, suffix=_ext, mode="wb") as tmp:
            tmp.write(uploaded_file.getvalue())
            tmp_path = Path(tmp.name)
        tmp_paths.append(tmp_path)
        try:
            parse_result = None
            if "Amazon" in file_format:
                # Barre de progression : utile sur les gros rapports Amazon
                # (des centaines de milliers de lignes) où le parsing peut
                # sembler figer l'interface. Widgets détruits après usage.
                _progress_bar = st.progress(0.0, text=f"Analyse de {uploaded_file.name}…")

                def _on_parse_progress(processed: int, total: int, _fname=uploaded_file.name) -> None:
                    pct = processed / total if total else 1.0
                    _progress_bar.progress(
                        min(pct, 1.0),
                        text=f"Analyse de {_fname} : {processed:,} / {total:,} lignes".replace(",", " "),
                    )

                parse_result = parser_amazon.load_amazon_report(
                    tmp_path, encoding=encoding, convert_currencies=convert_fx,
                    asin_to_category=asin_to_category,
                    progress_callback=_on_parse_progress,
                )
                _progress_bar.empty()
            elif "Mirakl" in file_format:
                parse_result = parser_mirakl.parse(tmp_path, encoding=encoding, convert_currencies=convert_fx)
            elif "Shopify" in file_format:
                parse_result = parser_shopify.parse(tmp_path, encoding=encoding, convert_currencies=convert_fx)
            elif "WooCommerce" in file_format:
                parse_result = parser_woocommerce.parse(tmp_path, encoding=encoding, convert_currencies=convert_fx)
            elif "AliExpress" in file_format:
                parse_result = parser_aliexpress.parse(tmp_path, encoding=encoding, convert_currencies=convert_fx)
            if parse_result is not None:
                platform = parse_result.platform or file_format.split("(")[0].strip()
                all_sales.extend(parse_result.sales); all_refunds.extend(parse_result.refunds)
                all_fc_transfers.extend(parse_result.fc_transfers)
                all_stock_countries |= parse_result.stock_countries
                all_warnings.extend(parse_result.warnings); all_platforms.append(platform)
                total_rows_sum += parse_result.total_rows; skipped_rows_sum += parse_result.skipped_rows
                _parse_results.append(parse_result)
                file_summaries.append({"Fichier": uploaded_file.name, "Source": platform,
                    "Ventes": len(parse_result.sales), "Remboursements": len(parse_result.refunds),
                    "Lignes lues": parse_result.total_rows, "Ignorees": parse_result.skipped_rows})
        except Exception as e:
            st.error(f"Erreur sur **{uploaded_file.name}** : {e}")
            for p in tmp_paths: p.unlink(missing_ok=True)
            st.stop()

    platform_name = all_platforms[0] if all_platforms else file_format.split("(")[0].strip()
    unique_platforms = list(dict.fromkeys(all_platforms))
    _total_returns = sum(getattr(pr, "return_rows", 0) for pr in _parse_results)
    _total_skipped = sum(getattr(pr, "skipped_rows", 0) for pr in _parse_results)

    # Résumé import
    if len(uploaded_files) == 1:
        fs = file_summaries[0]
        _return_part = f", {_total_returns} retours physiques sans montant" if _total_returns else ""
        _skip_part   = f", {_total_skipped} ignorées" if _total_skipped else ""
        st.info(f"**Import {platform_name}** : {fs['Ventes']} ventes, {fs['Remboursements']} remb., "
                f"{len(all_fc_transfers)} transferts FBA{_return_part}{_skip_part}.")
    else:
        st.success(f"**{len(uploaded_files)} fichiers agrégés** — {len(all_sales)} ventes + "
                   f"{len(all_refunds)} remboursements ({total_rows_sum} lignes).")
        with st.expander(f"Détail par fichier ({len(uploaded_files)} fichiers)"):
            st.table(file_summaries)
        if len(unique_platforms) > 1:
            st.warning(f"⚠️ Sources différentes : {', '.join(unique_platforms)}. Vérifiez que ce mix est intentionnel.")
    if all_warnings:
        with st.expander(f"⚠️ Avertissements d'import ({len(all_warnings)})"):
            for w in all_warnings: st.text(w)

    all_period_mismatches = []
    for pr in _parse_results:
        all_period_mismatches.extend(getattr(pr, "period_mismatches", []))
    if all_period_mismatches:
        with st.expander(
            f"📅 Écarts de période commande / expédition ({len(all_period_mismatches)})",
            expanded=False,
        ):
            st.caption(
                "Commandes dont la date de commande et la date d'expédition (fait "
                "générateur retenu pour le calcul fiscal — art. 65 Dir. 2006/112/CE) "
                "ne tombent pas dans le même mois civil. La TVA a été calculée sur "
                "la date d'expédition. Vérifiez que cela ne fait pas basculer ces "
                "ventes dans une période de déclaration OSS/CA3 différente de celle "
                "attendue."
            )
            st.dataframe(
                pd.DataFrame([
                    {"ID vente": m["sale_id"], "Date commande": m["order_date"],
                     "Date expédition (retenue)": m["shipment_date"],
                     "HT (EUR)": float(m["amount_ht"])}
                    for m in all_period_mismatches
                ]),
                use_container_width=True, hide_index=True,
            )

    # Résumé des taux de change BCE effectivement utilisés
    if convert_fx:
        _fx_used: dict = {}
        for _s in all_sales:
            if _s.original_currency and _s.original_currency != "EUR" and _s.exchange_rate:
                _k = (_s.original_currency, getattr(_s, "exchange_rate_source", "?"))
                if _k not in _fx_used:
                    _fx_used[_k] = {"rate": float(_s.exchange_rate), "date": _s.transaction_date[:7] if _s.transaction_date else ""}
        if _fx_used:
            with st.expander(f"💱 Taux de change BCE utilisés ({len(_fx_used)} devise(s))"):
                for (_ccy, _src), _info in sorted(_fx_used.items()):
                    _src_lbl = {"ecb": "BCE officiel", "fallback": "Taux Amazon (fallback BCE)", "eur": "EUR natif"}.get(_src, _src)
                    st.caption(f"**{_ccy}** : 1 EUR = {_info['rate']:.4f} {_ccy} — source : {_src_lbl} — période : {_info['date'] or '?'}")

    sales, refunds = all_sales, all_refunds

    try:
        if not sales:
            st.error("Aucune vente exploitable.")
            st.stop()

        import dataclasses as _dc
        if ioss_number or seller_is_importer:
            sales = [_dc.replace(s,
                ioss_number=ioss_number.strip() if ioss_number else s.ioss_number,
                seller_is_importer=seller_is_importer if seller_is_importer else s.seller_is_importer)
                for s in sales]

        if not convert_fx:
            foreign = {s.original_currency for s in sales if s.original_currency and s.original_currency != "EUR"}
            if foreign:
                st.warning(f"⚠️ Devises non-EUR : {', '.join(sorted(foreign))} — Activez conversion BCE.")

        # === CALCUL (mis en cache dans session_state) ===
        _vies_retry_nonce = st.session_state.get("_vies_retry_nonce", 0)
        _cache_key = (
            tuple(f.name + str(f.size) for f in uploaded_files),
            enable_vies, convert_fx, file_format,
            tuple(sorted(asin_to_category.items())),
            ioss_number, seller_is_importer,
            tuple(sorted(countries_with_vat)),
            apply_fr_under_threshold,
            _vies_retry_nonce,
        )
        if st.session_state.get("_calc_key") != _cache_key:
            vies_summary = None
            with st.spinner("Calcul TVA en cours..."):
                if enable_vies:
                    results, vies_summary, oss_summary = compute_all_with_vies(
                        sales, asin_to_category=asin_to_category,
                        on_invalid=on_invalid_behavior, marketplace_name=platform_name,
                        apply_fr_under_threshold=apply_fr_under_threshold,
                        refunds=refunds if refunds else None)
                else:
                    results, oss_summary = compute_all(
                        sales, marketplace_name=platform_name, asin_to_category=asin_to_category,
                        apply_fr_under_threshold=apply_fr_under_threshold,
                        refunds=refunds if refunds else None)
                refund_results = compute_all(refunds, marketplace_name=platform_name)[0] if refunds else []
                summary = build_report(results, refund_results=refund_results or None)
            st.session_state["_calc_key"]       = _cache_key
            st.session_state["_results"]        = results
            st.session_state["_refund_results"] = refund_results
            st.session_state["_summary"]        = summary
            st.session_state["_vies_summary"]   = vies_summary
            st.session_state["_oss_summary"]    = oss_summary
        else:
            results        = st.session_state["_results"]
            refund_results = st.session_state["_refund_results"]
            summary        = st.session_state["_summary"]
            vies_summary   = st.session_state["_vies_summary"]
            oss_summary    = st.session_state["_oss_summary"]

        # Segmentation écarts pour KPI
        _vies_ids_kpi     = getattr(vies_summary, 'vies_affected_sale_ids', set()) if vies_summary else set()
        _vies_rc_ids_kpi:  set[str] = set()
        _dom_rc_ids_kpi:   set[str] = set()
        if vies_summary and hasattr(vies_summary, "reclassifications"):
            for _rc_kpi in vies_summary.reclassifications:
                if getattr(_rc_kpi, "is_domestic_reverse_charge", False):
                    _dom_rc_ids_kpi.add(_rc_kpi.sale_id)
                else:
                    _vies_rc_ids_kpi.add(_rc_kpi.sale_id)
        from tva_intracom.rates import DOMESTIC_REVERSE_CHARGE_COUNTRIES as _DRC_KPI
        from tva_intracom.models import BuyerType as _BT_KPI
        ecarts_autres = []
        for _r in results:
            _tva_amz = float(getattr(_r.sale, 'amazon_vat_amount', Decimal('0')))
            _tva_mot = float(_r.vat_amount)
            _ecart_kpi = _tva_amz - _tva_mot
            if abs(_ecart_kpi) <= 0.05: continue
            if _r.sale.stock_country == 'GB' or _r.sale.buyer_country == 'GB': continue
            _sid_kpi = str(_r.sale.sale_id)
            if _sid_kpi in _vies_rc_ids_kpi or id(_r.sale) in _vies_ids_kpi: continue
            if _sid_kpi in _dom_rc_ids_kpi or (_r.sale.buyer_type == _BT_KPI.B2B and _r.sale.buyer_country in _DRC_KPI and _tva_mot == 0 and _tva_amz > 0): continue
            if _tva_amz == 0 and _tva_mot > 0: continue
            ecarts_autres.append((_r, _ecart_kpi))
        total_ecarts_autres = sum(d for _, d in ecarts_autres)

        # =====================================================================
        # ALERTES — toujours en haut, conditionnelles
        # =====================================================================
        render_historical_rates_alert(results)   
        if oss_summary.is_threshold_exceeded:
            _oss_gross_vat = float(summary.net_oss_total)
            _oss_by_year = getattr(oss_summary, "oss_ht_by_year", {})
            _years_exceeded = {y: v for y, v in _oss_by_year.items() if v > Decimal("10000.00")}
            if len(_oss_by_year) > 1:
                # Fichier multi-année : afficher le détail par année pour éviter
                # de confondre le cumul d'une seule année avec le total global.
                _year_detail = " · ".join(
                    f"**{y}** : {float(v):,.2f} € {'🔴' if v > Decimal('10000.00') else '🟡'}"
                    for y, v in sorted(_oss_by_year.items())
                )
                st.error(
                    f"🚨 **Seuil OSS dépassé sur {len(_years_exceeded)} année(s) !** "
                    f"Détail par année (CA OSS HT net) : {_year_detail}. "
                    f"(seuil réglementaire : 10 000 € / an — art. 59 ter directive 2006/112/CE). "
                    f"TVA OSS nette due (total fichier) : **{_oss_gross_vat:,.2f} €**."
                )
            else:
                _oss_net_ht = float(oss_summary.total_oss_ht)
                st.error(
                    f"🚨 **Seuil OSS dépassé !** "
                    f"CA OSS net (ventes cross-border B2C - avoirs OSS) : **{_oss_net_ht:,.2f} € HT** "
                    f"(seuil réglementaire : 10 000 € — art. 59 ter directive 2006/112/CE). "
                    f"TVA OSS nette due : **{_oss_gross_vat:,.2f} €**."
                )
        
        unregistered = (all_stock_countries - {"FR"}) - set(countries_with_vat)
        if unregistered:
            st.warning(f"⚠️ **Pays de stock non enregistrés : {', '.join(sorted(unregistered))}** — Immatriculation TVA requise.")
        if seller_is_importer:
            _ddp_unrg = {r.vat_country for r in results
                if r.scenario.value == "IMPORT_SELLER_AS_IMPORTER"
                and r.vat_country != "FR" and r.vat_country not in countries_with_vat}
            if _ddp_unrg:
                st.error(f"🚨 **DDP actif — Immatriculation TVA requise dans : "
                    f"{', '.join(_country_label(c)+' ('+c+')' for c in sorted(_ddp_unrg))}**")

        # =====================================================================
        # KPIs — toujours visibles
        # =====================================================================
        st.header("📊 Récapitulatif")
        c1, c2, c3, c4 = st.columns(4)
        ca_brut = float(summary.total_ht)
        ca_remb = float(getattr(summary, "refund_total_ht", 0))
        ca_net  = ca_brut + ca_remb
        c1.metric("CA HT total", _fmt(ca_net),
            help=f"CA net de remboursements. Brut : {_fmt(ca_brut)} · Remb : {_fmt(ca_remb)}")
        c2.metric("TVA à reverser (vous)", _fmt(float(summary.total_you_owe)),
            help="TVA que vous devez déclarer et reverser : TVA France (CA3) + OSS + IOSS. "
                 "Exclut la TVA collectée par Amazon (deemed supplier) et la TVA autoliquidée par l'acheteur (B2B).")
        c3.metric(f"TVA gérée par {platform_name}", _fmt(float(summary.amazon_vat)),
            help=f"TVA collectée et reversée directement par {platform_name} en tant que deemed supplier "
                 "(marketplace facilitator). Vous n'avez rien à faire pour ces ventes.")
        with c4:
            if abs(total_ecarts_autres) > 0.05:
                st.metric("🚨 Écarts de taux Amazon", f"{total_ecarts_autres:+.2f} €",
                    delta="Erreur paramétrage", delta_color="inverse")
            else:
                st.metric("✅ Concordance Amazon", "0 €")

        # =====================================================================
        # ONGLETS PRINCIPAUX
        # =====================================================================
        tab_decl, tab_detail, tab_vies, tab_audit, tab_dl, tab_viz = st.tabs([
            "💶 Déclarations",
            "📋 Détail ventes",
            "🛡️ VIES",
            "🔬 Audit Amazon",
            "📥 Téléchargements",
            "📊 Visualisations",
        ])

        # ── 1. DÉCLARATIONS ───────────────────────────────────────────────────
        with tab_decl:
            st.subheader("Ce que vous devez reverser")
            recap_data = [
                {"Canal":"TVA domestique France (CA3)","Ventes (EUR)":float(summary.fr_domestic_vat),
                 "Remb. (EUR)":float(summary.refund_fr_domestic_vat) if summary.refund_count else None,
                 "Net (EUR)":float(summary.net_fr_domestic_vat)},
                {"Canal":"Guichet OSS (total)","Ventes (EUR)":float(summary.oss_total),
                 "Remb. (EUR)":float(summary.refund_oss_total) if summary.refund_count else None,
                 "Net (EUR)":float(summary.net_oss_total)},
            ]
            for country in sorted(summary.net_oss_by_country):
                recap_data.append({"Canal":f"  → {_country_label(country)} ({country})",
                    "Ventes (EUR)":float(summary.oss_by_country.get(country,0)),
                    "Remb. (EUR)":float(summary.refund_oss_by_country.get(country,_ZERO)) if summary.refund_count else None,
                    "Net (EUR)":float(summary.net_oss_by_country[country])})
            _ioss_results = [r for r in results if r.scenario.value == "IOSS_DIRECT"]
            if _ioss_results:
                _ioss_total = sum(float(r.vat_amount) for r in _ioss_results)
                _ioss_ht    = sum(float(r.sale.amount_ht) for r in _ioss_results)
                recap_data.append({"Canal": "🌐 Guichet IOSS (propre numéro vendeur)",
                    "Ventes (EUR)": _ioss_total, "Remb. (EUR)": None, "Net (EUR)": _ioss_total})
                st.info(f"ℹ️ **{len(_ioss_results)} vente(s) IOSS_DIRECT** — HT : {_ioss_ht:,.2f} € · TVA : {_ioss_total:,.2f} €")
            _ddp_results = [r for r in results if r.scenario.value == "IMPORT_SELLER_AS_IMPORTER"]
            if _ddp_results:
                _ddp_by_country: dict = {}
                for r in _ddp_results:
                    _ddp_by_country[r.vat_country] = _ddp_by_country.get(r.vat_country, 0) + float(r.vat_amount)
                for _ccode, _camt in sorted(_ddp_by_country.items()):
                    _label = "TVA DDP France (CA3)" if _ccode == "FR" else f"TVA DDP {_country_label(_ccode)} (immat. locale)"
                    recap_data.append({"Canal": f"📦 {_label}", "Ventes (EUR)": _camt, "Remb. (EUR)": None, "Net (EUR)": _camt})
                st.warning(f"⚠️ **{len(_ddp_results)} vente(s) DDP (vendeur importateur)** : immatriculation locale requise.")
            if summary.local_by_country:
                local_total  = float(sum(summary.local_by_country.values()))
                local_refund = float(sum(getattr(summary,"refund_local_by_country",{}).values() or [0]))
                recap_data.append({"Canal":"Fisc local (hors FR) — Total","Ventes (EUR)":local_total,
                    "Remb. (EUR)":local_refund if summary.refund_count else None,"Net (EUR)":local_total+local_refund})
                for country in sorted(summary.local_by_country):
                    _lref = float(getattr(summary,"refund_local_by_country",{}).get(country,0))
                    recap_data.append({"Canal":f"  → {_country_label(country)} ({country})",
                        "Ventes (EUR)":float(summary.local_by_country[country]),
                        "Remb. (EUR)":_lref if summary.refund_count else None,
                        "Net (EUR)":float(summary.local_by_country[country])+_lref})
            _recap_df = pd.DataFrame(recap_data)
            _recap_cfg = _smart_money_df(
                _recap_df,
                money_cols=["Ventes (EUR)", "Remb. (EUR)", "Net (EUR)"],
            )
            # Amélioration 3 : colonne Type pour distinguer totaux et sous-lignes pays
            _recap_df.insert(0, "Type", _recap_df["Canal"].apply(
                lambda c: "↳ Pays" if str(c).startswith("  →") else "Total"
            ))
            _recap_cfg["Type"] = st.column_config.TextColumn("Type", width="small")
            _recap_cfg["Canal"] = st.column_config.TextColumn("Canal", width="large")
            st.dataframe(_recap_df, use_container_width=True, hide_index=True,
                         column_config=_recap_cfg)

            # Barre de progression seuil OSS
            _oss_ht = float(oss_summary.total_oss_ht)
            _oss_pct = min(_oss_ht / 10_000.0, 1.0)
            _oss_color = "🟢" if _oss_ht < 8_000 else ("🟡" if _oss_ht < 10_000 else "🔴")
            _oss_by_year = getattr(oss_summary, "oss_ht_by_year", {})
            if len(_oss_by_year) > 1:
                # Multi-année : préciser que la barre concerne la dernière année du fichier
                _last_year = max(_oss_by_year.keys())
                _oss_year_label = f" (année **{_last_year}**)"
            else:
                _oss_year_label = ""
            st.markdown(
                f"{_oss_color} **Seuil OSS**{_oss_year_label} : {_oss_ht:,.2f} € / 10 000 € HT "
                f"({'dépassé' if _oss_ht >= 10_000 else f'{_oss_pct*100:.1f} %'})"
            )
            st.progress(_oss_pct)

            if summary.refund_count:
                st.info(f"🔄 **{summary.refund_count} remboursement(s)** — HT : {float(summary.refund_total_ht):,.2f} €")

            # ── Contrôle de Cohérence Comptable ─────────────────────────────
            # Rapproche le CA HT net déclaré (total_ht - remboursements) avec
            # la somme du CA HT ventilé par canal fiscal (ht_by_bucket dans
            # report.py). Les deux sont calculés indépendamment (l'un lors de
            # l'agrégation globale, l'autre lors de la classification par
            # canal) donc un écart révèle un scénario non couvert par la
            # ventilation plutôt qu'une simple tautologie.
            #
            # ⚠️ Ceci ne rapproche PAS avec le relevé Amazon (commissions,
            # frais, remises promo non détaillées ici) : c'est un test
            # d'intégrité interne du moteur, pas un lettrage bancaire complet.
            _declared_net_ht = summary.total_ht + summary.refund_total_ht
            _bucket_net_ht = summary.net_ht_total
            _coherence_delta = _declared_net_ht - _bucket_net_ht
            with st.expander("🧮 Contrôle de cohérence comptable", expanded=abs(_coherence_delta) > Decimal("0.01")):
                _bucket_rows = [
                    {"Canal fiscal": b, "CA HT net (EUR)": float(v)}
                    for b, v in summary.net_ht_by_bucket.items() if v != 0
                ]
                if _bucket_rows:
                    st.dataframe(pd.DataFrame(_bucket_rows), use_container_width=True, hide_index=True,
                        column_config={"CA HT net (EUR)": _money_col("CA HT net (EUR)")})
                c1, c2, c3 = st.columns(3)
                c1.metric("CA HT net déclaré", f"{float(_declared_net_ht):,.2f} €")
                c2.metric("CA HT net (somme des canaux)", f"{float(_bucket_net_ht):,.2f} €")
                c3.metric("Écart", f"{float(_coherence_delta):,.2f} €")
                if abs(_coherence_delta) > Decimal("0.01"):
                    st.error(
                        "⛔ Écart détecté entre le CA HT déclaré et la somme des canaux fiscaux — "
                        "un scénario de vente échappe probablement à la ventilation par canal "
                        "(voir « Autre / non classé » ci-dessus si présent). À investiguer avant "
                        "de considérer les déclarations comme fiables."
                    )
                else:
                    st.success("✅ Cohérence interne vérifiée : le CA HT déclaré correspond à la somme des canaux fiscaux.")
                st.caption(
                    "Ce contrôle vérifie la cohérence interne du calcul (aucune vente perdue "
                    "entre les canaux). Il ne remplace pas un rapprochement avec votre relevé "
                    "de règlements Amazon, qui inclut des éléments hors périmètre de cet outil "
                    "(commissions, frais logistiques, remises)."
                )

            # Immatriculations requises
            pay_eu = {r.vat_country for r in results
                if r.channel.value == "LOCAL" and r.vat_country and r.vat_country != "FR"}
            if pay_eu:
                unregistered_local = pay_eu - set(countries_with_vat)
                if unregistered_local:
                    st.warning(f"⚠️ Immatriculation TVA requise dans : "
                        + ", ".join(f"**{_country_label(p)} ({p})**" for p in sorted(unregistered_local)))

        # ── 2. DÉTAIL VENTES ──────────────────────────────────────────────────
        with tab_detail:
            sub_a, sub_b, sub_c, sub_d = st.tabs([
                "💸 Ce que vous devez", "🤝 Géré par des tiers", "📄 Ligne par ligne",
                f"🔄 Remboursements ({len(refund_results or [])})",
            ])

            with sub_a:
                st.caption("Ventes dont vous êtes responsable de la TVA.")
                your_results = [r for r in results if r.collector.value == "SELLER"]
                sort_yours = st.radio("Trier par", ["Pays","Taux","HT"], horizontal=True, key="sort_yours")
                if sort_yours == "Pays": your_results.sort(key=lambda r: r.vat_country)
                elif sort_yours == "Taux": your_results.sort(key=lambda r: -r.vat_rate)
                else: your_results.sort(key=lambda r: -r.sale.amount_ht)
                _your_rows = [{
                    "ID":r.sale.sale_id, "Stock":r.sale.stock_country, "Dest":r.sale.buyer_country,
                    "HT (EUR)":float(r.sale.amount_ht), "Taux %":float(r.vat_rate),
                    "TVA (EUR)":float(r.vat_amount), "Canal":r.channel.value,
                    "Devise":r.sale.original_currency if r.sale.original_currency != "EUR" else "",
                    "Montant orig.":float(r.sale.original_amount) if r.sale.original_currency != "EUR" else None,
                    "Note":r.note}
                    for r in your_results]
                _your_df_full = pd.DataFrame(_your_rows)
                _ps_your = st.select_slider("Lignes par page", options=[100, 250, 500, 1000, "Toutes"],
                    value=250, key="page_size_your")
                _lim_your = len(_your_df_full) if _ps_your == "Toutes" else int(_ps_your)
                st.caption(f"{len(_your_df_full)} ligne(s) — affichage : {min(_lim_your, len(_your_df_full))}")
                _your_df = _your_df_full.head(_lim_your).copy()
                _your_cfg = _smart_money_df(_your_df,
                    money_cols=["HT (EUR)", "TVA (EUR)", "Montant orig."],
                    pct_cols=["Taux %"],
                    note_cols=["Note"])
                st.dataframe(_your_df, use_container_width=True, hide_index=True,
                             column_config=_your_cfg)

            with sub_b:
                st.caption("Ventes dont Amazon ou la douane collecte la TVA.")
                third_results = [r for r in results if r.collector.value != "SELLER"]
                _third_rows = [{
                    "ID":r.sale.sale_id, "Stock":r.sale.stock_country, "Dest":r.sale.buyer_country,
                    "HT (EUR)":float(r.sale.amount_ht), "Scénario":r.scenario.value,
                    "Collecteur":r.collector.value}
                    for r in third_results]
                _third_df = pd.DataFrame(_third_rows)
                _third_cfg = _smart_money_df(_third_df, money_cols=["HT (EUR)"])
                st.dataframe(_third_df, use_container_width=True, hide_index=True,
                             column_config=_third_cfg)

            with sub_c:
                st.caption("Toutes les ventes, ligne par ligne.")
                sort_all = st.radio("Trier par", ["Pays","Taux","HT"], horizontal=True, key="sort_all")
                all_sorted = sorted(results,
                    key=lambda r: r.vat_country if sort_all=="Pays" else (-r.vat_rate if sort_all=="Taux" else -r.sale.amount_ht))
                _all_rows = [{
                    "ID":r.sale.sale_id, "Stock":r.sale.stock_country, "Dest":r.sale.buyer_country,
                    "HT (EUR)":float(r.sale.amount_ht), "Scénario":r.scenario.value,
                    "Taux %":float(r.vat_rate), "TVA (EUR)":float(r.vat_amount),
                    "Canal":r.channel.value,
                    "Devise":r.sale.original_currency if r.sale.original_currency != "EUR" else "",
                    "Montant orig.":float(r.sale.original_amount) if r.sale.original_currency != "EUR" else None,
                    "Note":r.note}
                    for r in all_sorted]
                _all_df_full = pd.DataFrame(_all_rows)

                # Filtres
                _fa, _fb, _fc, _fd = st.columns([2, 2, 2, 2])
                with _fa:
                    _search_id = st.text_input("🔍 Rechercher ID", placeholder="ex: 123-456", key="search_id_all")
                with _fb:
                    _dest_opts = sorted(_all_df_full["Dest"].unique())
                    _dest_sel  = st.multiselect("Pays destination", _dest_opts, key="filter_dest_all",
                        placeholder="Tous les pays")
                with _fc:
                    _scen_opts = sorted(_all_df_full["Scénario"].unique())
                    _scen_sel  = st.multiselect("Scénario", _scen_opts, key="filter_scen_all",
                        placeholder="Tous les scénarios")
                with _fd:
                    _canal_opts = sorted(_all_df_full["Canal"].unique())
                    _canal_sel  = st.multiselect("Canal", _canal_opts, key="filter_canal_all",
                        placeholder="Tous les canaux")

                _all_df_filt = _all_df_full.copy()
                if _search_id:
                    _all_df_filt = _all_df_filt[_all_df_filt["ID"].astype(str).str.contains(_search_id, case=False, na=False)]
                if _dest_sel:
                    _all_df_filt = _all_df_filt[_all_df_filt["Dest"].isin(_dest_sel)]
                if _scen_sel:
                    _all_df_filt = _all_df_filt[_all_df_filt["Scénario"].isin(_scen_sel)]
                if _canal_sel:
                    _all_df_filt = _all_df_filt[_all_df_filt["Canal"].isin(_canal_sel)]

                # Pagination
                _page_size_all = st.select_slider("Lignes par page", options=[100, 250, 500, 1000, "Toutes"],
                    value=250, key="page_size_all")
                _n_all = len(_all_df_filt)
                _limit_all = _n_all if _page_size_all == "Toutes" else int(_page_size_all)
                st.caption(f"{_n_all} ligne(s) {'(filtrées)' if _n_all < len(_all_df_full) else ''} — affichage : {min(_limit_all, _n_all)}")

                _all_df_page = _all_df_filt.head(_limit_all).copy()
                _all_cfg = _smart_money_df(_all_df_page,
                    money_cols=["HT (EUR)", "TVA (EUR)", "Montant orig."],
                    pct_cols=["Taux %"],
                    note_cols=["Note"])
                st.dataframe(_all_df_page, use_container_width=True, hide_index=True,
                             column_config=_all_cfg)

            with sub_d:
                if not refund_results:
                    st.info("ℹ️ Aucun remboursement dans ce fichier.")
                else:
                    _ref_ht  = sum(float(r.sale.amount_ht) for r in refund_results)
                    _ref_tva = sum(float(r.vat_amount)     for r in refund_results)
                    ra, rb, rc = st.columns(3)
                    ra.metric("Remboursements", len(refund_results))
                    rb.metric("HT total remboursé", _fmt(_ref_ht))
                    rc.metric("TVA restituée", _fmt(_ref_tva))
                    sort_ref = st.radio("Trier par", ["Pays","Taux","HT"], horizontal=True, key="sort_ref")
                    ref_sorted = sorted(refund_results,
                        key=lambda r: r.vat_country if sort_ref=="Pays" else (-r.vat_rate if sort_ref=="Taux" else r.sale.amount_ht))
                    _ref_rows = [{
                        "ID":r.sale.sale_id, "Stock":r.sale.stock_country, "Dest":r.sale.buyer_country,
                        "HT (EUR)":float(r.sale.amount_ht), "Scénario":r.scenario.value,
                        "Taux %":float(r.vat_rate), "TVA (EUR)":float(r.vat_amount),
                        "Canal":r.channel.value}
                        for r in ref_sorted]
                    _ref_df = pd.DataFrame(_ref_rows)
                    _ref_cfg = _smart_money_df(_ref_df,
                        money_cols=["HT (EUR)", "TVA (EUR)"],
                        pct_cols=["Taux %"])
                    st.dataframe(_ref_df, use_container_width=True, hide_index=True,
                                 column_config=_ref_cfg)

        # ── 3. VIES ───────────────────────────────────────────────────────────
        with tab_vies:
            if not enable_vies:
                st.info("ℹ️ Activez la validation VIES dans la barre latérale pour accéder à cet onglet.")
            elif vies_summary is None or vies_summary.total_checked == 0:
                st.info("ℹ️ Aucun numéro B2B détecté dans ce fichier.")
            else:
                # KPIs VIES
                v1, v2, v3, v4, v5 = st.columns(5)
                v1.metric("Numéros vérifiés", vies_summary.total_checked)
                v2.metric("✅ Valides", vies_summary.total_valid)
                v3.metric("❌ Invalides", vies_summary.total_invalid,
                    delta=f"-{vies_summary.total_invalid}" if vies_summary.total_invalid else None, delta_color="inverse")
                v4.metric("⚠️ Non vérifiés (erreur serveur)", vies_summary.total_inconclusive,
                    delta=f"{vies_summary.total_inconclusive}" if vies_summary.total_inconclusive else None, delta_color="off")
                v5.metric("TVA récupérée via VIES", f"{float(vies_summary.fraud_avoided_amount):,.2f} €")

                # Inconclus
                if vies_summary.total_inconclusive > 0:
                    st.warning(f"⚠️ **{vies_summary.total_inconclusive} numéro(s)** non vérifiés (serveur VIES indisponible).")
                    if vies_summary.total_inconclusive == vies_summary.total_checked:
                        st.error("🚫 **100% des numéros sont non vérifiés.** Problème de connectivité probable vers ec.europa.eu.")
                        if st.button("🧪 Tester la connexion à VIES", key="test_vies_conn"):
                            from tva_intracom.vies import check_vat
                            with st.spinner("Test en cours..."):
                                test_res = check_vat("FR", "40303265045")
                            if test_res.valid:
                                st.success(f"✅ Connexion VIES OK : {test_res.name}")
                            else:
                                st.error(f"❌ Échec : valid={test_res.valid}, error={test_res.error!r}")

                    with st.expander("🔎 Classifier manuellement les numéros non vérifiés", expanded=True):
                        _details = getattr(vies_summary, "inconclusive_vat_details", None)
                        if _details:
                            _inc_entries = [{"vat": d["vat"], "country": d.get("country", d["vat"][:2]),
                                "sale_ids": d.get("sale_ids", [])} for d in _details]
                        else:
                            _inc_entries = [{"vat": v, "country": v[:2], "sale_ids": []}
                                for v in vies_summary.inconclusive_vats]
                        _overrides: dict = st.session_state.get("_vies_manual_overrides", {})
                        st.caption("Indiquez Valide (B2B exonéré) ou Invalide (B2C, TVA due) pour chaque numéro non vérifié.")
                        _changed = False
                        for _entry in _inc_entries:
                            _vat = _entry["vat"]; _country = _entry["country"]; _sale_ids = _entry["sale_ids"]
                            _label = f"**{_vat}** ({_country})"
                            if _sale_ids:
                                _label += f" — vente(s) : {', '.join(_sale_ids[:3])}"
                                if len(_sale_ids) > 3: _label += f" +{len(_sale_ids)-3}"
                            _current = _overrides.get(_vat, "⏳ Non classifié")
                            _col_label, _col_sel, _col_badge = st.columns([3, 2, 1])
                            _col_label.markdown(_label)
                            _choice = _col_sel.selectbox("Statut",
                                options=["⏳ Non classifié", "✅ Valide (B2B)", "❌ Invalide (B2C)"],
                                index=["⏳ Non classifié", "✅ Valide (B2B)", "❌ Invalide (B2C)"].index(_current),
                                key=f"vies_override_{_vat}", label_visibility="collapsed")
                            _col_badge.markdown("🆕" if _choice != _current else "")
                            if _choice != _current:
                                _overrides[_vat] = _choice; _changed = True
                        if _changed:
                            st.session_state["_vies_manual_overrides"] = _overrides
                        _pending = {v: c for v, c in _overrides.items() if c != "⏳ Non classifié"}
                        st.caption(f"**{len(_pending)} / {len(_inc_entries)}** numéros classifiés manuellement.")
                        _col_apply, _col_reset = st.columns([2, 1])
                        with _col_apply:
                            if _pending and st.button("💾 Appliquer les classifications et recalculer", type="primary"):
                                from tva_intracom.vies import set_manual_override as _smo_apply
                                for _vat_key, _choice_val in _pending.items():
                                    _smo_apply(_vat_key, valid=(_choice_val == "✅ Valide (B2B)"))
                                st.session_state.pop("_vies_manual_overrides", None)
                                st.session_state.pop("_calc_key", None)
                                st.success("Classification appliquée — recalcul en cours…")
                                st.rerun()
                        with _col_reset:
                            if st.button("↩️ Réinitialiser"):
                                st.session_state.pop("_vies_manual_overrides", None)
                                st.rerun()

                if st.button("🔄 Revérifier les numéros VIES en erreur", key="retry_vies_btn"):
                    st.session_state["_vies_retry_nonce"] = _vies_retry_nonce + 1
                    st.rerun()

                # Overrides manuels en base (toujours accessible, replié par défaut)
                try:
                    from tva_intracom.vies import set_manual_override as _smo_edit, CACHE_DB_FILE as _VIES_DB_B
                    import sqlite3 as _sqlite3b
                    _conn_b = _sqlite3b.connect(_VIES_DB_B)
                    _cur_b = _conn_b.cursor()
                    _cur_b.execute("SELECT full_vat, is_valid, set_at FROM vies_manual_overrides ORDER BY set_at DESC")
                    _existing_overrides_b = _cur_b.fetchall()
                    _conn_b.close()
                except Exception:
                    _existing_overrides_b = []

                if _existing_overrides_b:
                    with st.expander(f"🖊️ Classifications manuelles enregistrées ({len(_existing_overrides_b)})", expanded=False):
                        st.caption("Ces classifications persistent entre les sessions. "
                            "Modifiez ou supprimez chaque entrée pour corriger une erreur.")
                        for _ov_vat2, _ov_valid2, _ov_date2 in _existing_overrides_b:
                            _ov_date_str2 = (_ov_date2 or "")[:10]
                            _oc1b, _oc2b, _oc3b, _oc4b = st.columns([3, 2, 1, 1])
                            _oc1b.markdown(f"**{_ov_vat2}**  \n<small style='color:grey'>{_ov_date_str2}</small>",
                                unsafe_allow_html=True)
                            _ov_new2 = _oc2b.selectbox("Statut",
                                options=["✅ Valide (B2B)", "❌ Invalide (B2C)"],
                                index=0 if _ov_valid2 else 1,
                                key=f"edit_override_b_{_ov_vat2}", label_visibility="collapsed")
                            if _oc3b.button("💾", key=f"save_override_b_{_ov_vat2}", help="Enregistrer"):
                                _smo_edit(_ov_vat2, valid=(_ov_new2 == "✅ Valide (B2B)"))
                                st.session_state.pop("_calc_key", None)
                                st.success(f"{_ov_vat2} → {_ov_new2}")
                                st.rerun()
                            if _oc4b.button("🗑️", key=f"del_override_b_{_ov_vat2}", help="Supprimer (retour VIES)"):
                                try:
                                    _conn_del = _sqlite3b.connect(_VIES_DB_B)
                                    _conn_del.execute("DELETE FROM vies_manual_overrides WHERE full_vat = ?", (_ov_vat2,))
                                    _conn_del.commit(); _conn_del.close()
                                    st.session_state.pop("_calc_key", None)
                                    st.success(f"Override supprimé pour {_ov_vat2}.")
                                    st.rerun()
                                except Exception as _del_err2:
                                    st.error(f"Erreur : {_del_err2}")

                # Reclassifications VIES
                if vies_summary.reclassifications:
                    avec_delta = [r for r in vies_summary.reclassifications if r.vat_delta > 0]
                    dom_rc     = [r for r in vies_summary.reclassifications if getattr(r, "is_domestic_reverse_charge", False)]
                    dom_taxe   = [r for r in vies_summary.reclassifications if r.vat_delta <= 0 and not getattr(r, "is_domestic_reverse_charge", False)]
                    st.success(f"🛡️ **{len(vies_summary.reclassifications)} vente(s) reclassifiée(s)** B2B→B2C. "
                               f"TVA supplémentaire : **{float(vies_summary.fraud_avoided_amount):,.2f} €**")
                    if dom_rc:
                        st.info(f"ℹ️ **{len(dom_rc)} vente(s) en autoliquidation nationale** (art.194) — acheteur reverse la TVA directement.")
                    if dom_taxe:
                        st.info(f"ℹ️ **{len(dom_taxe)} vente(s) à impact nul** — TVA déjà due, pas de double imposition.")

                    def _vies_statut(r):
                        if getattr(r, "is_domestic_reverse_charge", False): return "♻️ Autoliquidation nationale"
                        elif r.vat_delta <= 0: return "✅ Déjà taxé (domestic)"
                        return "💰 TVA récupérée"

                    def _vies_explication(r):
                        if getattr(r, "is_domestic_reverse_charge", False):
                            return f"Domestic {r.buyer_country} — Art.194"
                        elif r.vat_delta <= 0: return "Domestic — TVA due dans les 2 cas"
                        return "Cross-border — exonération évitée"

                    fraud_data = [{"Vente": r.sale_id, "N° TVA rejeté": r.buyer_vat_number,
                        "Pays": _country_label(r.buyer_country), "HT (EUR)": float(r.amount_ht),
                        "TVA récupérée (EUR)": float(r.vat_avoided),
                        "Statut": _vies_statut(r), "Explication": _vies_explication(r)}
                        for r in vies_summary.reclassifications]

                    filtre = st.radio("Afficher", ["Toutes","TVA récupérée","Autoliquidation","Impact nul"], horizontal=True)
                    if filtre == "TVA récupérée":   display = [d for d in fraud_data if "💰" in d["Statut"]]
                    elif filtre == "Autoliquidation": display = [d for d in fraud_data if "♻️" in d["Statut"]]
                    elif filtre == "Impact nul":      display = [d for d in fraud_data if "✅" in d["Statut"]]
                    else: display = fraud_data
                    _fraud_df = pd.DataFrame(display)
                    _fraud_cfg = _smart_money_df(_fraud_df,
                        money_cols=["HT (EUR)", "TVA récupérée (EUR)"])
                    st.dataframe(_fraud_df, use_container_width=True, hide_index=True,
                                 column_config=_fraud_cfg)

                    if avec_delta:
                        by_c = {}
                        for r in avec_delta:
                            by_c[_country_label(r.buyer_country)] = by_c.get(_country_label(r.buyer_country),0) + float(r.vat_avoided)
                        fig_f = go.Figure(go.Bar(x=list(by_c.keys()), y=list(by_c.values()),
                            marker_color="#d62728", text=[f"{v:,.2f}€" for v in by_c.values()], textposition="auto"))
                        fig_f.update_layout(title="TVA récupérée par pays", yaxis_title="Montant (EUR)", height=280, margin=dict(t=40,b=30))
                        st.plotly_chart(fig_f, use_container_width=True)

                    import io as _io, csv as _csv
                    buf = _io.StringIO(); w = _csv.writer(buf, delimiter=";")
                    w.writerow(["Vente","N TVA rejete","Pays","HT (EUR)","TVA recuperee (EUR)","Statut","Explication"])
                    for r in vies_summary.reclassifications:
                        if getattr(r, "is_domestic_reverse_charge", False):
                            statut_csv = "Autoliquidation nationale"; expl_csv = f"Domestic {r.buyer_country} — Art.194"
                        elif r.vat_delta <= 0:
                            statut_csv = "Deja taxe (domestic)"; expl_csv = "Domestic"
                        else:
                            statut_csv = "TVA recuperee"; expl_csv = "Cross-border"
                        w.writerow([r.sale_id, r.buyer_vat_number, _country_label(r.buyer_country),
                            str(r.amount_ht).replace(".",","), str(r.vat_avoided).replace(".",","),
                            statut_csv, expl_csv])
                    st.download_button("⬇️ Exporter rapport VIES (.csv)",
                        data=("\ufeff"+buf.getvalue()).encode("utf-8"),
                        file_name="rapport_vies.csv", mime="text/csv")
                elif vies_summary.total_inconclusive:
                    st.info("ℹ️ Aucun numéro invalide confirmé pour le moment (certains restent à vérifier).")
                else:
                    st.success("✅ Tous les numéros de TVA B2B sont valides.")

        # ── 4. AUDIT AMAZON ───────────────────────────────────────────────────
        with tab_audit:
            audit_sub1, audit_sub2, audit_sub3 = st.tabs([
                "⚖️ Écarts TVA Amazon",
                "📦 Mouvements stock FBA",
                "🌍 Exports déclarations locales",
            ])

            with audit_sub1:
                has_amazon_vat = any(getattr(r.sale,"amazon_vat_amount",Decimal("0"))>0 for r in results)
                if not has_amazon_vat:
                    st.info("ℹ️ Aucune TVA Amazon disponible. Cet onglet nécessite le format Amazon 3 (2024+).")
                else:
                    _vies_affected_ids = getattr(vies_summary, "vies_affected_sale_ids", set()) if vies_summary else set()
                    _vies_rc_ids_app: set[str] = set()
                    _dom_rc_ids_app:  set[str] = set()
                    if vies_summary and hasattr(vies_summary, "reclassifications"):
                        for _rc in vies_summary.reclassifications:
                            if getattr(_rc, "is_domestic_reverse_charge", False): _dom_rc_ids_app.add(_rc.sale_id)
                            else: _vies_rc_ids_app.add(_rc.sale_id)
                    from tva_intracom.rates import DOMESTIC_REVERSE_CHARGE_COUNTRIES as _DRC_APP
                    from tva_intracom.models import BuyerType as _BT_APP
                    ecarts_vies_tab, ecarts_b2b_dom_tab, ecarts_gb_tab, ecarts_autres_tab, ecarts_amz_manquante_tab = [], [], [], [], []
                    nb_arrondis = 0
                    for r in results:
                        tva_amazon = float(getattr(r.sale,"amazon_vat_amount",Decimal("0")))
                        tva_moteur = float(r.vat_amount)
                        if tva_amazon==0 and tva_moteur==0: continue
                        ecart = tva_amazon - tva_moteur
                        row_d = {"ID vente":r.sale.sale_id,
                            "Stock→Dest":f"{r.sale.stock_country}→{r.sale.buyer_country}",
                            "Scénario":r.scenario.value,"HT (EUR)":float(r.sale.amount_ht),
                            "TVA Amazon (EUR)":round(tva_amazon,2),"TVA moteur (EUR)":round(tva_moteur,2),
                            "Écart (EUR)":round(ecart,2),
                            "Taux Amazon (%)":round(tva_amazon/float(r.sale.amount_ht)*100,2) if r.sale.amount_ht else 0,
                            "Taux moteur (%)":float(r.vat_rate)}
                        if abs(ecart)<=0.05:
                            if abs(ecart)>0: nb_arrondis+=1
                            continue
                        _dep = r.sale.stock_country; _arr = r.sale.buyer_country; _sid = str(r.sale.sale_id)
                        _is_b2b = (r.sale.buyer_type == _BT_APP.B2B)
                        if _dep == "GB" or _arr == "GB": ecarts_gb_tab.append(row_d)
                        elif _sid in _vies_rc_ids_app or id(r.sale) in _vies_affected_ids: ecarts_vies_tab.append(row_d)
                        elif _sid in _dom_rc_ids_app or (_is_b2b and _arr in _DRC_APP and tva_moteur == 0 and tva_amazon > 0): ecarts_b2b_dom_tab.append(row_d)
                        elif tva_amazon == 0 and tva_moteur > 0: ecarts_amz_manquante_tab.append(row_d)
                        else: ecarts_autres_tab.append(row_d)

                    # Amélioration 4 : helper formatage uniforme pour tous les sous-onglets audit
                    def _audit_df(rows):
                        """Affiche un tableau d'écarts avec formatage smart monétaire et taux."""
                        if not rows:
                            return
                        _df = pd.DataFrame(rows)
                        _cfg = _smart_money_df(_df,
                            money_cols=["HT (EUR)", "TVA Amazon (EUR)", "TVA moteur (EUR)", "Écart (EUR)"],
                            pct_cols=["Taux Amazon (%)", "Taux moteur (%)"])
                        st.dataframe(_df, use_container_width=True, hide_index=True, column_config=_cfg)

                    sub1, sub2, sub3, sub4, sub5 = st.tabs([
                        f"⚙️ Écarts de taux ({len(ecarts_autres_tab)})",
                        f"🚨 Risque VIES ({len(ecarts_vies_tab)})",
                        f"🇬🇧 Royaume-Uni ({len(ecarts_gb_tab)})",
                        f"♻️ Autoliquidation art.194 ({len(ecarts_b2b_dom_tab)})",
                        f"⚠️ TVA Amazon manquante ({len(ecarts_amz_manquante_tab)})",
                    ])
                    with sub1:
                        if ecarts_autres_tab:
                            total = sum(r["Écart (EUR)"] for r in ecarts_autres_tab)
                            st.error(f"🚨 **{len(ecarts_autres_tab)} écart(s)** — Total : {total:+.2f} EUR. Ouvrez un ticket Amazon Seller Central.")
                            _audit_df(ecarts_autres_tab)
                        else:
                            st.success("✅ Aucun écart de paramétrage de taux.")
                    with sub2:
                        if not enable_vies: st.info("ℹ️ Activez VIES pour auditer les numéros B2B.")
                        elif ecarts_vies_tab:
                            total = sum(r["Écart (EUR)"] for r in ecarts_vies_tab)
                            st.error(f"Risque fiscal (requalification VIES) : {abs(total):,.2f} EUR")
                            _audit_df(ecarts_vies_tab)
                        else:
                            st.success("✅ Aucun risque VIES détecté.")
                    with sub3:
                        st.info("💡 TVA britannique collectée par Amazon depuis le Brexit. Normal, hors déclarations UE.")
                        if ecarts_gb_tab:
                            st.metric("Écart technique UK", f"{sum(r['Écart (EUR)'] for r in ecarts_gb_tab):,.2f} EUR")
                            _audit_df(ecarts_gb_tab)
                        else:
                            st.success("✅ Aucune transaction UK.")
                    with sub4:
                        st.info("💡 Ventes B2B où l'acheteur autoliquide la TVA (art.194 dir.2006/112/CE — ES, IT, PL, CZ…). Amazon collecte, le moteur calcule 0€. Normal.")
                        if ecarts_b2b_dom_tab:
                            total = sum(r["Écart (EUR)"] for r in ecarts_b2b_dom_tab)
                            st.metric("TVA collectée par Amazon (autoliquidation)", f"{abs(total):,.2f} EUR")
                            _audit_df(ecarts_b2b_dom_tab)
                        else:
                            st.success("✅ Aucune vente en autoliquidation avec écart.")
                    with sub5:
                        st.info("⚠️ Le moteur calcule une TVA due mais Amazon n'a rien collecté (0€). Vérifier le paramétrage Amazon.")
                        if ecarts_amz_manquante_tab:
                            total = sum(r["Écart (EUR)"] for r in ecarts_amz_manquante_tab)
                            st.metric("TVA potentiellement manquante", f"{abs(total):,.2f} EUR")
                            _audit_df(ecarts_amz_manquante_tab)
                            import io as _io2, csv as _csv2
                            _buf2 = _io2.StringIO(); _w2 = _csv2.writer(_buf2, delimiter=";")
                            _w2.writerow(["ID vente","Stock->Dest","Scenario","HT (EUR)","TVA Amazon (EUR)","TVA moteur (EUR)","Ecart (EUR)"])
                            for _rw in ecarts_amz_manquante_tab:
                                _w2.writerow([_rw["ID vente"],_rw["Stock→Dest"],_rw["Scénario"],
                                    str(_rw["HT (EUR)"]).replace(".",","),str(_rw["TVA Amazon (EUR)"]).replace(".",","),
                                    str(_rw["TVA moteur (EUR)"]).replace(".",","),str(_rw["Écart (EUR)"]).replace(".",",")])
                            st.download_button("⬇️ Exporter TVA Amazon manquante (.csv)",
                                data=("\ufeff"+_buf2.getvalue()).encode("utf-8"),
                                file_name="TVA_amazon_manquante.csv", mime="text/csv")
                        else:
                            st.success("✅ Amazon collecte correctement la TVA sur toutes les ventes.")
                    if nb_arrondis > 0:
                        st.caption(f"ℹ️ {nb_arrondis} micro-écart(s) d'arrondi (<= 0.05 EUR) masqués.")

            with audit_sub2:
                st.subheader("Mouvements de stock inter-entrepôts (FC_Transfer / Inbound)")
                local_sales_outside_fr = [s for s in all_sales if s.stock_country==s.buyer_country and s.stock_country!="FR"]
                if local_sales_outside_fr:
                    by_c = {}
                    for s in local_sales_outside_fr:
                        by_c.setdefault(s.stock_country,{"nb":0,"ht":0.0})
                        by_c[s.stock_country]["nb"]+=1; by_c[s.stock_country]["ht"]+=float(s.amount_ht)
                    at_risk = [c for c in by_c if c not in countries_with_vat]
                    ok = [c for c in by_c if c in countries_with_vat]
                    if at_risk: st.error(f"🚨 Ventes locales sans immatriculation : **{', '.join(at_risk)}**")
                    if ok: st.success(f"✅ Pays couverts : **{', '.join(ok)}**")
                    _df_loc = pd.DataFrame([{"Pays":c,"Ventes":d["nb"],"Volume HT (EUR)":round(d["ht"],2),
                        "Statut":"✅ OK" if c in countries_with_vat else "🚨 Immatriculation requise"}
                        for c,d in by_c.items()])
                    _loc_cfg = _smart_money_df(_df_loc, money_cols=["Volume HT (EUR)"])
                    st.dataframe(_df_loc, use_container_width=True, hide_index=True, column_config=_loc_cfg)
                if all_fc_transfers:
                    st.caption(f"{len(all_fc_transfers)} transfert(s) FC détecté(s).")
                    with st.expander("Voir les transferts FBA"):
                        st.dataframe(pd.DataFrame(all_fc_transfers[:200]), use_container_width=True, hide_index=True)
                        if len(all_fc_transfers) > 200:
                            st.caption(f"Affichage limité à 200 sur {len(all_fc_transfers)}.")
                else:
                    st.info("Aucun transfert FC détecté.")

            with audit_sub3:
                st.subheader("Exports pour déclarations fiscales locales (27 pays UE)")
                _local_results = [r for r in results if r.channel.value == "LOCAL" and r.vat_country]
                if not _local_results:
                    st.info("ℹ️ Aucune vente en immatriculation locale détectée.")
                else:
                    _local_countries = sorted({r.vat_country for r in _local_results})
                    c1_loc, c2_loc, c3_loc = st.columns(3)
                    c1_loc.metric("Pays concernés", len(_local_countries))
                    c2_loc.metric("Ventes locales", len(_local_results))
                    _local_tva = sum(float(r.vat_amount) for r in _local_results)
                    c3_loc.metric("TVA locale totale", f"{_local_tva:,.2f} €")
                    pay_eu_local = set(_local_countries) - set(countries_with_vat)
                    if pay_eu_local:
                        st.warning(f"⚠️ Immatriculation TVA requise : {', '.join(f'{_country_label(p)} ({p})' for p in sorted(pay_eu_local))}")
                    export_country = st.selectbox("Pays à exporter", _local_countries,
                        format_func=lambda c: f"{_country_label(c)} ({c})")
                    if export_country:
                        def _build_local_csv(country):
                            import io as _il, csv as _cl
                            from collections import defaultdict as _dd
                            buf = _il.StringIO(); w = _cl.writer(buf, delimiter=";")
                            period_lbl = oss_period or "Periode non renseignee"
                            meta = COUNTRY_FISCAL_META.get(country, (f"Declaration TVA {_country_label(country)}", "Base HT", "TVA", "—", "—"))
                            decl_name, lbl_base, lbl_tax, rate_std, rate_red = meta
                            country_results = ([r for r in results if r.channel.value in ("FR_DOMESTIC","OSS")]
                                if country == "FR" else [r for r in results if r.vat_country == country or r.sale.buyer_country == country])
                            by_rate = _dd(lambda: {"base": Decimal("0"), "tva": Decimal("0"), "nb": 0})
                            for r in country_results:
                                by_rate[str(r.vat_rate)]["base"] += r.sale.amount_ht
                                by_rate[str(r.vat_rate)]["tva"]  += r.vat_amount
                                by_rate[str(r.vat_rate)]["nb"]   += 1
                            w.writerow([f"{decl_name} — {period_lbl}"])
                            w.writerow([f"Pays : {_country_label(country)} ({country}) | Standard : {rate_std} | Reduit : {rate_red}"])
                            w.writerow([])
                            fmt_map = {
                                "DE": (["Kennzahl","Bezeichnung","Base (EUR)","TVA (EUR)","Nb"], {"19":("81","19%"),"7":("86","7%")}),
                                "ES": (["Casilla","Concepto","Base (EUR)","TVA (EUR)","Nb"], {"21":("01","21%"),"10":("03","10%"),"4":("05","4%")}),
                                "IT": (["Aliquota","Descrizione","Base (EUR)","TVA (EUR)","N."], {"22":"22%","10":"10%","4":"4%"}),
                                "PL": (["Pole","Opis","Base","TVA","Liczba"], {"23":("K_19","23%"),"8":("K_17","8%"),"5":("K_15","5%")}),
                                "NL": (["Rubriek","Omschrijving","Base (EUR)","TVA (EUR)","Antal"], {"21":("1a","21%"),"9":("1b","9%")}),
                                "BE": (["Grille","Description","Base (EUR)","TVA (EUR)","Nb"], {"21":("03","21%"),"12":("02","12%"),"6":("01","6%")}),
                                "PT": (["Campo","Descricao","Base (EUR)","TVA (EUR)","N."], {"23":("1","23%"),"13":("2","13%"),"6":("3","6%")}),
                                "SE": (["Ruta","Beskrivning","Base","TVA","Antal"], {"25":("05","25%"),"12":("06","12%"),"6":("07","6%")}),
                                "AT": (["Kennzahl","Bezeichnung","Base (EUR)","TVA (EUR)","Anz."], {"20":("022","20%"),"10":("029","10%"),"13":("006","13%")}),
                                "CZ": (["Radek","Popis","Base","TVA","Pocet"], {"21":("1","21%"),"12":("2","12%")}),
                                "RO": (["Rand","Descriere","Base","TVA","Nr."], {"19":("9","19%"),"9":("10","9%"),"5":("11","5%")}),
                                "HU": (["Sor","Megnevezes","Base","TVA","Db"], {"27":("B2","27%"),"18":("C2","18%"),"5":("D2","5%")}),
                                "DK": (["Felt","Beskrivelse","Base","TVA"], None),
                                "IE": (["Box","Description","Base (EUR)","TVA (EUR)","Count"], {"23":("T1","23%"),"9":("T1","9%"),"0":("E1","0%")}),
                                "FI": (["Koodi","Kuvaus","Base (EUR)","TVA (EUR)","Lkm"], None),
                                "GR": (["Kod.","Perigraphi","Base (EUR)","TVA (EUR)","Ar."], None),
                            }
                            if country == "FR":
                                w.writerow(["Base HT","Taux (%)","TVA","ID vente","Canal"])
                                for r in country_results:
                                    w.writerow([str(r.sale.amount_ht).replace(".",","),str(r.vat_rate).replace(".",","),
                                        str(r.vat_amount).replace(".",","),r.sale.sale_id,r.channel.value])
                                w.writerow([]); w.writerow(["TOTAL TVA FR",str(summary.net_fr_domestic_vat).replace(".",",")])
                                w.writerow(["TOTAL OSS",str(summary.net_oss_total).replace(".",",")])
                            elif country in fmt_map:
                                headers, mapping = fmt_map[country]
                                w.writerow(headers)
                                for rk, d in sorted(by_rate.items(), key=lambda x: -float(x[0])):
                                    if mapping:
                                        val = mapping.get(rk, ("", rk+"%"))
                                        code, desc = val if isinstance(val, tuple) else (rk, val)
                                    else:
                                        code, desc = "", rk+"%"
                                    w.writerow([code,desc,str(d["base"]).replace(".",","),str(d["tva"]).replace(".",","),d["nb"]])
                                w.writerow(["","TOTAL","",str(sum(d["tva"] for d in by_rate.values())).replace(".",",")])
                            else:
                                w.writerow([lbl_base+" (EUR)","Taux (%)","TVA (EUR)","Nb","ID vente","Date"])
                                for r in country_results:
                                    w.writerow([str(r.sale.amount_ht).replace(".",","),str(r.vat_rate).replace(".",","),
                                        str(r.vat_amount).replace(".",","),1,r.sale.sale_id,r.sale.transaction_date])
                                w.writerow([]); w.writerow(["TOTAL TVA","",str(sum(d["tva"] for d in by_rate.values())).replace(".",",")])
                            w.writerow([]); w.writerow(["--- Détail ---"])
                            w.writerow(["ID vente","Date","Base HT (EUR)","Taux (%)","TVA (EUR)","Canal","Pays dest."])
                            for r in country_results:
                                w.writerow([r.sale.sale_id,r.sale.transaction_date,str(r.sale.amount_ht).replace(".",","),
                                    str(r.vat_rate).replace(".",","),str(r.vat_amount).replace(".",","),
                                    r.channel.value,r.sale.buyer_country])
                            return ("\ufeff"+buf.getvalue()).encode("utf-8")

                        meta_sel = COUNTRY_FISCAL_META.get(export_country, ("","","","—","—"))
                        country_vat = (float(summary.fr_domestic_vat) if export_country == "FR"
                            else float(summary.oss_by_country.get(export_country,0)) + float(summary.local_by_country.get(export_country,0)))
                        m1, m2, m3 = st.columns(3)
                        m1.metric(f"TVA due — {_country_label(export_country)}", f"{country_vat:,.2f} EUR")
                        m2.metric("Taux standard", meta_sel[3])
                        m3.metric("Taux réduit", meta_sel[4])
                        st.download_button(f"⬇️ Déclaration {_country_label(export_country)} (.csv)",
                            data=_build_local_csv(export_country),
                            file_name=f"declaration_tva_{export_country.lower()}_{oss_period or 'periode'}.csv",
                            mime="text/csv")


        # ── 5. TÉLÉCHARGEMENTS ────────────────────────────────────────────────
        with tab_dl:
            results_net = results + (refund_results or [])

            # ── Auto-détection de la période depuis les dates de transactions ──
            if oss_period == "__auto__":
                _dates = sorted(
                    r.sale.transaction_date for r in results
                    if r.sale.transaction_date and len(r.sale.transaction_date) >= 7
                )
                if _dates:
                    from datetime import datetime as _dt
                    _d_min = _dt.fromisoformat(_dates[0][:10])
                    _d_max = _dt.fromisoformat(_dates[-1][:10])
                    _y_min, _m_min = _d_min.year, _d_min.month
                    _y_max, _m_max = _d_max.year, _d_max.month
                    if _y_min != _y_max:
                        # Multi-année → afficher la plage
                        period_label = f"{_y_min}-{_y_max}"
                    elif _m_min == 1 and _m_max == 12:
                        period_label = str(_y_min)                           # Annuel
                    elif _m_min == 1 and _m_max == 6:
                        period_label = f"{_y_min}-S1"                       # S1
                    elif _m_min == 7 and _m_max == 12:
                        period_label = f"{_y_min}-S2"                       # S2
                    else:
                        # Trimestre ou plage partielle → trouver le trimestre
                        _q_min = (_m_min - 1) // 3 + 1
                        _q_max = (_m_max - 1) // 3 + 1
                        if _q_min == _q_max:
                            period_label = f"{_y_min}-Q{_q_min}"            # 1 trimestre
                        else:
                            period_label = f"{_y_min}-Q{_q_min}_Q{_q_max}" # plage trimestrielle
                    st.info(f"📅 Période auto-détectée : **{period_label}** "
                            f"(transactions du {_dates[0][:10]} au {_dates[-1][:10]}). "
                            "Modifiez via le panneau latéral si nécessaire.")
                else:
                    period_label = ""
            else:
                period_label = oss_period

            st.subheader("📥 Téléchargements")
            with st.container():
                with tempfile.NamedTemporaryFile(delete=False, suffix=".xlsx") as xlsx_tmp:
                    _vies_ids = getattr(vies_summary, "vies_affected_sale_ids", set()) if vies_summary else set()
                    xlsx_path = export_xlsx(results, xlsx_tmp.name, summary=summary,
                        refund_results=refund_results, all_fc_transfers=all_fc_transfers,
                        vies_affected_sale_ids=_vies_ids, vies_summary=vies_summary,
                        countries_with_vat=countries_with_vat,
                        period=period_label, seller_country="FR")
                with open(xlsx_path,"rb") as f: xlsx_bytes = f.read()

                # ── ZONE TÉLÉCHARGEMENTS ──────────────────────────────────────
                st.divider()

                # 1. Rapport principal — pleine largeur, bouton primaire
                st.markdown("#### 📦 Rapport principal")
                r1, r2 = st.columns([2, 1])
                with r1:
                    st.download_button(
                        "📊 Rapport complet (.xlsx)",
                        data=xlsx_bytes,
                        file_name="rapport_tva_intracommunautaire.xlsx",
                        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                        type="primary", use_container_width=True,
                    )
                with r2:
                    st.download_button(
                        "📝 Rapport texte (.txt)",
                        data=render_report(summary).encode("utf-8"),
                        file_name="rapport_tva_intracommunautaire.txt",
                        mime="text/plain", use_container_width=True,
                    )

                st.divider()

                # 2. Exports OSS / B2B — 3 colonnes avec KPI inline
                st.markdown("#### 🇪🇺 Exports OSS / B2B")
                oss_results_dl = [r for r in results_net if r.scenario == Scenario.OSS_B2C]
                b2b_results_dl = [r for r in results_net if r.scenario == Scenario.B2B_REVERSE_CHARGE]
                if oss_results_dl or b2b_results_dl:
                    o1, o2, o3 = st.columns(3)
                    with o1:
                        if oss_results_dl:
                            with tempfile.NamedTemporaryFile(delete=False, suffix=".xlsx") as oss_tmp:
                                oss_xlsx_path = build_oss_excel(results_net, oss_tmp.name, period=period_label)
                            with open(oss_xlsx_path,"rb") as f: oss_xlsx_bytes = f.read()
                            st.download_button(
                                "📊 État OSS (.xlsx)", data=oss_xlsx_bytes,
                                file_name="etat_recapitulatif_oss.xlsx",
                                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                                use_container_width=True,
                            )
                            st.caption(f"{len(oss_results_dl)} ventes · TVA {float(summary.oss_total):,.2f} €")
                    with o2:
                        if oss_results_dl:
                            oss_csv_bytes, _ = build_oss_csv(results_net, period=period_label)
                            st.download_button(
                                "📄 OSS URSSAF (.csv)", data=oss_csv_bytes,
                                file_name="oss_urssaf.csv", mime="text/csv",
                                use_container_width=True,
                            )
                    with o3:
                        if b2b_results_dl:
                            _, b2b_csv_bytes = build_oss_csv(results_net, period=period_label)
                            st.download_button(
                                "🤝 B2B Recap (.csv)", data=b2b_csv_bytes,
                                file_name="b2b_recap.csv", mime="text/csv",
                                use_container_width=True,
                            )
                            st.caption(f"{len(b2b_results_dl)} livraisons · HT {float(summary.reverse_charge_ht):,.2f} €")
                else:
                    st.info("ℹ️ Aucune vente OSS ou B2B détectée.")

                st.divider()

                # 3. Formulaires fiscaux français — 2 colonnes symétriques
                st.markdown("#### 🇫🇷 Formulaires fiscaux")
                col_xml, col_html = st.columns(2)
                with col_xml:
                    st.markdown("**🇪🇺 Guichet Unique OSS (XML)**")
                    st.caption("Fichier prêt au téléversement sur impots.gouv.fr")
                    if not period_label or not period_label.strip():
                        st.warning("⚠️ Renseignez la période dans le panneau latéral (ex : 2026-T1)")
                    else:
                        # ── Détection en amont des soldes OSS négatifs ──────────
                        # generate_oss_xml() bloque déjà avec un ValueError détaillé,
                        # mais on détecte ici AVANT le clic pour afficher un bloc rouge
                        # explicatif visible immédiatement, sans nécessiter un essai
                        # de génération raté au préalable.
                        _oss_agg_preview = aggregate_oss_results(results_net, period=period_label)
                        _negative_buckets = find_oss_negative_buckets(_oss_agg_preview)
                        if _negative_buckets:
                            _neg_lines = "\n".join(
                                f"- {_country_label(b.departure)} → {_country_label(b.arrival)} "
                                f"({b.vat_rate}%) : HT {float(b.base_ht):,.2f} € · "
                                f"TVA {float(b.vat_amount):,.2f} €"
                                for b in _negative_buckets
                            )
                            st.error(
                                "⛔ **Solde OSS négatif détecté pour cette période.** "
                                "L'URSSAF / le portail OSS n'accepte pas de montant négatif "
                                "dans le corps de la déclaration.\n\n"
                                f"{_neg_lines}\n\n"
                                "**Cela signifie généralement** que des remboursements de la "
                                "période dépassent les ventes du même couple pays/taux — "
                                "souvent parce qu'ils se rapportent à une vente d'une **période "
                                "déjà déclarée**. Dans ce cas, ne les incluez pas dans cette "
                                "déclaration : reportez-les manuellement comme correction de la "
                                "période d'origine sur le portail OSS "
                                "(rubrique *Corrections de déclarations antérieures*)."
                            )
                        try:
                            oss_xml_bytes = generate_oss_xml(
                                results=results_net, seller_vat=tva_fr, period=period_label
                            )
                            st.download_button(
                                "📥 XML OSS officiel", data=oss_xml_bytes,
                                file_name=f"oss_declaration_{period_label}.xml",
                                mime="application/xml", use_container_width=True,
                                key="btn_oss_xml_final",
                            )
                        except ValueError as _xml_err:
                            st.error(f"⛔ {_xml_err}")
                with col_html:
                    st.markdown("**🇫🇷 Déclaration CA3 (HTML)**")
                    st.caption(
                        "Rapport préparatoire ventilé par taux (20 %, 10 %, 5,5 %, 2,1 %), "
                        "ligne 08 AIC (transferts FBA) et solde net."
                    )
                    with st.expander("Paramètres déductions (optionnel)", expanded=False):
                        st.caption(
                            "Non calculables depuis les fichiers Amazon (données d'achats "
                            "indisponibles) — à saisir manuellement si vous voulez un solde net."
                        )
                        _c1, _c2, _c3 = st.columns(3)
                        _tva_ded_immo = _c1.number_input(
                            "TVA déd. immobilisations (€)", min_value=0.0, value=0.0, step=10.0,
                            key="ca3_ded_immo",
                        )
                        _tva_ded_autres = _c2.number_input(
                            "TVA déd. autres biens/services (€)", min_value=0.0, value=0.0, step=10.0,
                            key="ca3_ded_autres",
                        )
                        _credit_prec = _c3.number_input(
                            "Crédit période précédente (€)", min_value=0.0, value=0.0, step=10.0,
                            key="ca3_credit_prec",
                        )
                    ca3_html = generate_ca3_html_report_v2(
                        results=results_net,
                        company_name=nom_entreprise, siren=siren_entreprise,
                        period_label=period_label,
                        all_fc_transfers=all_fc_transfers,
                        tva_deductible_immos=Decimal(str(_tva_ded_immo)),
                        tva_deductible_autres=Decimal(str(_tva_ded_autres)),
                        credit_periode_precedente=Decimal(str(_credit_prec)),
                        seller_country="FR",
                    )
                    st.download_button(
                        "📥 Rapport CA3 (HTML)", data=ca3_html.encode("utf-8"),
                        file_name=f"rapport_ca3_{period_label}.html",
                        mime="text/html", use_container_width=True,
                        key="btn_ca3_html_final",
                    )

        # ── 6. VISUALISATIONS (repliées) ──────────────────────────────────────
        vat_by_country: dict[str, float] = {}
        with tab_viz:
            vat_by_country = {}
            if summary.fr_domestic_vat > 0: vat_by_country["FR"] = float(summary.fr_domestic_vat)
            for c, a in summary.oss_by_country.items(): vat_by_country[c] = vat_by_country.get(c,0)+float(a)
            for c, a in summary.local_by_country.items(): vat_by_country[c] = vat_by_country.get(c,0)+float(a)

            ch1, ch2 = st.columns(2)
            with ch1:
                st.subheader("TVA due par pays")
                if vat_by_country:
                    bar_data = sorted(vat_by_country.items(), key=lambda x: -x[1])
                    fig_bar = go.Figure(go.Bar(
                        x=[_country_label(c) for c,_ in bar_data], y=[a for _,a in bar_data],
                        marker_color=["#2ca02c" if c=="FR" else "#1f77b4" for c,_ in bar_data],
                        text=[f"{a:,.2f}€" for _,a in bar_data], textposition="auto"))
                    fig_bar.update_layout(yaxis_title="Montant TVA (EUR)", height=380, margin=dict(t=20,b=40))
                    st.plotly_chart(fig_bar, use_container_width=True)
            with ch2:
                st.subheader(f"Répartition : Vous vs {platform_name}")
                pie_l, pie_v, pie_c = [], [], []
                if float(summary.total_you_owe)>0: pie_l.append("Vous"); pie_v.append(float(summary.total_you_owe)); pie_c.append("#2ca02c")
                if float(summary.amazon_vat)>0: pie_l.append(platform_name); pie_v.append(float(summary.amazon_vat)); pie_c.append("#ff7f0e")
                if float(summary.import_vat)>0: pie_l.append("Douane"); pie_v.append(float(summary.import_vat)); pie_c.append("#9467bd")
                if pie_v:
                    fig_pie = go.Figure(go.Pie(labels=pie_l, values=pie_v,
                        marker=dict(colors=pie_c), hole=0.4, textinfo="label+percent"))
                    fig_pie.update_layout(height=380, margin=dict(t=20,b=20))
                    st.plotly_chart(fig_pie, use_container_width=True)

            if vat_by_country:
                st.subheader("🗺️ Carte de la TVA en Europe")
                map_data = [{"iso_alpha": COUNTRY_ISO3[c], "pays": _country_label(c), "tva": amt}
                    for c, amt in vat_by_country.items() if c in COUNTRY_ISO3]
                if map_data:
                    fig_map = px.choropleth(map_data, locations="iso_alpha", color="tva",
                        hover_name="pays", color_continuous_scale="YlOrRd", scope="europe",
                        labels={"tva": "TVA (EUR)"})
                    fig_map.update_layout(height=450, margin=dict(t=10,b=10,l=0,r=0))
                    st.plotly_chart(fig_map, use_container_width=True)

            # ── B : Évolution temporelle ──────────────────────────────────────
            st.subheader("📅 Évolution mensuelle")
            _monthly: dict = {}
            for r in results:
                _d = r.sale.transaction_date
                if _d and len(_d) >= 7:
                    _ym = _d[:7]
                    if _ym not in _monthly:
                        _monthly[_ym] = {"CA HT": 0.0, "TVA due": 0.0, "Remb. HT": 0.0, "TVA remb.": 0.0}
                    if r.sale.amount_ht > 0:
                        _monthly[_ym]["CA HT"]   += float(r.sale.amount_ht)
                        _monthly[_ym]["TVA due"]  += float(r.vat_amount)
            for r in (refund_results or []):
                _d = r.sale.transaction_date
                if _d and len(_d) >= 7:
                    _ym = _d[:7]
                    if _ym not in _monthly:
                        _monthly[_ym] = {"CA HT": 0.0, "TVA due": 0.0, "Remb. HT": 0.0, "TVA remb.": 0.0}
                    _monthly[_ym]["Remb. HT"]  += float(r.sale.amount_ht)   # négatif
                    _monthly[_ym]["TVA remb."] += float(r.vat_amount)        # négatif

            if len(_monthly) >= 2:
                _months_sorted = sorted(_monthly.keys())
                _MOIS_FR = {"01":"Jan","02":"Fév","03":"Mar","04":"Avr","05":"Mai","06":"Juin",
                            "07":"Juil","08":"Août","09":"Sep","10":"Oct","11":"Nov","12":"Déc"}
                def _mois_label(ym: str) -> str:
                    y, m = ym.split("-")
                    return f"{_MOIS_FR.get(m, m)} {y}"
                _df_monthly = pd.DataFrame([
                    {"Mois": _mois_label(m),
                     "CA HT ventes": _monthly[m]["CA HT"],
                     "Remb. HT": _monthly[m]["Remb. HT"],
                     "TVA nette": _monthly[m]["TVA due"] + _monthly[m]["TVA remb."]}
                    for m in _months_sorted
                ])
                _tviz1, _tviz2 = st.columns(2)
                with _tviz1:
                    fig_time = go.Figure()
                    fig_time.add_trace(go.Bar(
                        name="CA HT ventes", x=_df_monthly["Mois"],
                        y=_df_monthly["CA HT ventes"], marker_color="#1f77b4",
                        hovertemplate="%{x}<br>CA HT : %{y:,.2f} €<extra></extra>",
                    ))
                    fig_time.add_trace(go.Bar(
                        name="Remb. HT", x=_df_monthly["Mois"],
                        y=_df_monthly["Remb. HT"], marker_color="#d62728",
                        hovertemplate="%{x}<br>Remb. : %{y:,.2f} €<extra></extra>",
                    ))
                    fig_time.add_trace(go.Scatter(
                        name="TVA nette", x=_df_monthly["Mois"],
                        y=_df_monthly["TVA nette"], mode="lines+markers",
                        line=dict(color="#ff7f0e", width=2), yaxis="y2",
                        hovertemplate="%{x}<br>TVA nette : %{y:,.2f} €<extra></extra>",
                    ))
                    fig_time.update_layout(
                        barmode="relative", height=360,
                        xaxis=dict(type="category"),
                        yaxis=dict(title="CA HT (EUR)", tickformat=",.0f"),
                        yaxis2=dict(title="TVA (EUR)", overlaying="y", side="right",
                                    showgrid=False, tickformat=",.0f"),
                        legend=dict(orientation="h", y=1.08),
                        margin=dict(t=40, b=40),
                        hovermode="x unified",
                    )
                    st.plotly_chart(fig_time, use_container_width=True)
                with _tviz2:
                    # ── F : Répartition par scénario ─────────────────────────
                    st.markdown("**Répartition par scénario**")
                    _scen_counts: dict = {}
                    _scen_ht: dict = {}
                    for r in results:
                        _sc = r.scenario.value
                        _scen_counts[_sc] = _scen_counts.get(_sc, 0) + 1
                        _scen_ht[_sc] = _scen_ht.get(_sc, 0.0) + float(r.sale.amount_ht)
                    _scen_data = sorted(_scen_counts.items(), key=lambda x: -x[1])
                    fig_scen = go.Figure()
                    fig_scen.add_trace(go.Bar(
                        name="Nb transactions",
                        x=[s for s, _ in _scen_data],
                        y=[n for _, n in _scen_data],
                        marker_color="#1f77b4",
                        text=[str(n) for _, n in _scen_data],
                        textposition="auto",
                    ))
                    fig_scen.update_layout(height=360, margin=dict(t=20, b=60),
                        xaxis_tickangle=-30, yaxis_title="Nb transactions")
                    st.plotly_chart(fig_scen, use_container_width=True)
                    st.caption(" · ".join(
                        f"**{s}** : {n} tx · {_scen_ht.get(s, 0):,.0f} € HT"
                        for s, n in _scen_data
                    ))
            elif _monthly:
                st.caption("_(données sur 1 seul mois — graphique temporel non pertinent)_")

    except Exception as exc:
        st.error(f"Erreur lors du traitement : {exc}")
        raise
    finally:
        for _p in tmp_paths: _p.unlink(missing_ok=True)

else:
    st.markdown("---")
    col_a, col_b = st.columns([2,1])
    with col_a:
        st.markdown("### Comment utiliser\n\n1. Sélectionnez la **plateforme** dans la barre latérale.\n"
            "2. Déposez votre **fichier** dans la zone ci-dessus.\n"
            "3. Consultez le **récapitulatif** avec graphiques.\n"
            "4. Téléchargez le **rapport Excel**.")
    with col_b:
        st.markdown("### Plateformes supportées\n\n"
            "| Source | Type |\n|---|---|\n"
            "| Amazon | Marketplace |\n| Mirakl | Marketplace |\n"
            "| Shopify | CMS |\n| WooCommerce | CMS |\n| AliExpress | Marketplace |")