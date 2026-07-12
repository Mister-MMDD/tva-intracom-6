"""Onglet "Déclarations" (extrait tel quel de app.py, with tab_decl:).

Affiche le récapitulatif "Ce que vous devez reverser" (France CA3, OSS par
pays, IOSS, DDP, Fisc local), la barre de seuil OSS, et le Contrôle de
Cohérence Comptable.

Calcule et stocke `ctx.oss_tva_net_total` — relu ensuite par l'onglet
Téléchargements (export CSV local FR) : dépendance intentionnelle entre
onglets, voir tva_intracom/ui/tabs/context.py.
"""

from __future__ import annotations

from decimal import Decimal

import pandas as pd
import streamlit as st
from tva_intracom.i18n import _

from tva_intracom.models import Channel
from tva_intracom.oss_export import aggregate_oss_results
from tva_intracom.ui.formatting import _country_label, _gated_preview_table, _money_col, _smart_money_df, _fmt
from tva_intracom.ui.tabs.context import TabContext

_ZERO = Decimal("0.00")


def render_declarations(ctx: TabContext) -> None:
    """Rendu complet de l'onglet Déclarations."""
    results = ctx.results
    refund_results = ctx.refund_results
    summary = ctx.summary
    oss_summary = ctx.oss_summary
    period_label = ctx.period_label
    _can_export = ctx.can_export
    home_country = ctx.home_country

    st.subheader(_("what_you_must_remit"))

    # OSS : source unique de vérité = aggregate_oss_results() (même
    # fonction que celle utilisée par les exports Excel/CSV/XML OSS),
    # avec reconversion BCE de clôture de période (art. 5 bis Règl.
    # UE 2020/194). `summary.oss_by_country` (report.py) n'applique
    # PAS cette reconversion et affichait donc un total légèrement
    # différent de celui des exports pour les ventes en devise
    # étrangère — on ne l'utilise plus ici pour éviter la divergence.
    _oss_period_agg = aggregate_oss_results(results + (refund_results or []), period=period_label)
    _oss_country_totals: dict = {}
    for _dep, _dests in _oss_period_agg.items():
        for _arr, _rates in _dests.items():
            _acc = _oss_country_totals.setdefault(_arr, {
                "tva_vente": _ZERO, "tva_remb": _ZERO, "tva_net": _ZERO,
                "ht_vente": _ZERO, "ht_remb": _ZERO, "ht_net": _ZERO
            })
            for _rate, _amt in _rates.items():
                _acc["tva_vente"] += _amt["tva_vente"]
                _acc["tva_remb"]  += _amt["tva_remb"]
                _acc["tva_net"]   += _amt["tva"]
                _acc["ht_vente"]  += _amt["ht_vente"]
                _acc["ht_remb"]   += _amt["ht_remb"]
                _acc["ht_net"]    += _amt["ht"]

    _oss_tva_vente_total = sum((v["tva_vente"] for v in _oss_country_totals.values()), _ZERO)
    _oss_tva_remb_total  = sum((v["tva_remb"]  for v in _oss_country_totals.values()), _ZERO)
    _oss_tva_net_total   = sum((v["tva_net"]   for v in _oss_country_totals.values()), _ZERO)
    _oss_ht_vente_total  = sum((v["ht_vente"]  for v in _oss_country_totals.values()), _ZERO)
    _oss_ht_remb_total   = sum((v["ht_remb"]   for v in _oss_country_totals.values()), _ZERO)
    _oss_ht_net_total    = sum((v["ht_net"]    for v in _oss_country_totals.values()), _ZERO)

    # Home Country declaration (ex-France CA3)
    home_ht_brut = sum(r.sale.amount_ht for r in results if r.channel == Channel.FR_DOMESTIC)
    home_ht_remb = sum(r.sale.amount_ht for r in (refund_results or []) if r.channel == Channel.FR_DOMESTIC)

    if home_country == "FR":
        home_label = _("canal_vat_fr")
    else:
        # On utilise une version courte pour le tableau récap : "Déclaration [Pays]"
        home_label = f"🏠 Déclaration {home_country}"

    recap_data = [
        {
            _("col_canal"): home_label,
            _("col_ca_ht_brut"): float(home_ht_brut),
            _("col_ca_ht_remb"): float(home_ht_remb) if home_ht_remb else None,
            _("col_ca_ht_net"): float(home_ht_brut + home_ht_remb),
            _("col_tva_brute"): float(summary.fr_domestic_vat),
            _("col_tva_remb"): float(summary.refund_fr_domestic_vat) if summary.refund_count else None,
            _("col_tva_nette"): float(summary.net_fr_domestic_vat)
        },
        {
            _("col_canal"): _("canal_oss_total"),
            _("col_ca_ht_brut"): float(_oss_ht_vente_total),
            _("col_ca_ht_remb"): float(_oss_ht_remb_total) if _oss_ht_remb_total else None,
            _("col_ca_ht_net"): float(_oss_ht_net_total),
            _("col_tva_brute"): float(_oss_tva_vente_total),
            _("col_tva_remb"): float(_oss_tva_remb_total) if summary.refund_count else None,
            _("col_tva_nette"): float(_oss_tva_net_total)
        },
    ]
    for country in sorted(_oss_country_totals):
        _c = _oss_country_totals[country]
        recap_data.append({
            _("col_canal"): f"  → {_country_label(country)} ({country})",
            _("col_ca_ht_brut"): float(_c["ht_vente"]),
            _("col_ca_ht_remb"): float(_c["ht_remb"]) if _c["ht_remb"] else None,
            _("col_ca_ht_net"): float(_c["ht_net"]),
            _("col_tva_brute"): float(_c["tva_vente"]),
            _("col_tva_remb"): float(_c["tva_remb"]) if summary.refund_count else None,
            _("col_tva_nette"): float(_c["tva_net"])
        })

    _ioss_results = [r for r in results if r.scenario.value == "IOSS_DIRECT"]
    _ioss_refund_results = [r for r in (refund_results or []) if r.scenario.value == "IOSS_DIRECT"]
    if _ioss_results or _ioss_refund_results:
        _ioss_tva_brute = sum(r.vat_amount for r in _ioss_results)
        _ioss_tva_remb = sum(r.vat_amount for r in _ioss_refund_results)
        _ioss_ht_brut = sum(r.sale.amount_ht for r in _ioss_results)
        _ioss_ht_remb = sum(r.sale.amount_ht for r in _ioss_refund_results)
        recap_data.append({
            _("col_canal"): _("canal_ioss_vendeur"),
            _("col_ca_ht_brut"): float(_ioss_ht_brut),
            _("col_ca_ht_remb"): float(_ioss_ht_remb) if _ioss_ht_remb else None,
            _("col_ca_ht_net"): float(_ioss_ht_brut + _ioss_ht_remb),
            _("col_tva_brute"): float(_ioss_tva_brute),
            _("col_tva_remb"): float(_ioss_tva_remb) if _ioss_tva_remb else None,
            _("col_tva_nette"): float(_ioss_tva_brute + _ioss_tva_remb)
        })

    _ddp_results = [r for r in results if r.scenario.value == "IMPORT_SELLER_AS_IMPORTER"]
    _ddp_refund_results = [r for r in (refund_results or []) if r.scenario.value == "IMPORT_SELLER_AS_IMPORTER"]
    if _ddp_results or _ddp_refund_results:
        _ddp_agg = {}
        for r in _ddp_results:
            _acc = _ddp_agg.setdefault(r.vat_country, {"ht_brut": _ZERO, "ht_remb": _ZERO, "tva_brute": _ZERO, "tva_remb": _ZERO})
            _acc["ht_brut"] += r.sale.amount_ht
            _acc["tva_brute"] += r.vat_amount
        for r in _ddp_refund_results:
            _acc = _ddp_agg.setdefault(r.vat_country, {"ht_brut": _ZERO, "ht_remb": _ZERO, "tva_brute": _ZERO, "tva_remb": _ZERO})
            _acc["ht_remb"] += r.sale.amount_ht
            _acc["tva_remb"] += r.vat_amount
        for _ccode, _vals in sorted(_ddp_agg.items()):
            if _ccode == home_country:
                _label = _("canal_ddp_fr") if home_country == "FR" else f"TVA DDP {home_country}"
            else:
                _label = _("canal_ddp_local", country=_country_label(_ccode))

            recap_data.append({
                _("col_canal"): f"📦 {_label}",
                _("col_ca_ht_brut"): float(_vals["ht_brut"]),
                _("col_ca_ht_remb"): float(_vals["ht_remb"]) if _vals["ht_remb"] else None,
                _("col_ca_ht_net"): float(_vals["ht_brut"] + _vals["ht_remb"]),
                _("col_tva_brute"): float(_vals["tva_brute"]),
                _("col_tva_remb"): float(_vals["tva_remb"]) if _vals["tva_remb"] else None,
                _("col_tva_nette"): float(_vals["tva_brute"] + _vals["tva_remb"])
            })

    # 5. Déclarations Locales (hors pays d'origine)
    if summary.local_by_country:
        local_ht_brut_by_country = {}
        for r in results:
            if r.channel == Channel.LOCAL_REGISTRATION:
                local_ht_brut_by_country[r.vat_country] = local_ht_brut_by_country.get(r.vat_country, _ZERO) + r.sale.amount_ht
        local_ht_remb_by_country = {}
        for r in (refund_results or []):
            if r.channel == Channel.LOCAL_REGISTRATION:
                local_ht_remb_by_country[r.vat_country] = local_ht_remb_by_country.get(r.vat_country, _ZERO) + r.sale.amount_ht

        _local_ht_brut_total = sum(local_ht_brut_by_country.values(), _ZERO)
        _local_ht_remb_total = sum(local_ht_remb_by_country.values(), _ZERO)
        _local_tva_brute_total = sum(summary.local_by_country.values(), _ZERO)
        _local_tva_remb_total = sum(getattr(summary, "refund_local_by_country", {}).values(), _ZERO)

        if home_country == "FR":
            local_label = _("canal_local_hors_fr")
        else:
            # On renomme dynamiquement le libellé pour refléter le pays d'origine choisi
            local_label = _("canal_local_hors_fr").replace("FR", home_country)

        recap_data.append({
            _("col_canal"): local_label,
            _("col_ca_ht_brut"): float(_local_ht_brut_total),
            _("col_ca_ht_remb"): float(_local_ht_remb_total) if _local_ht_remb_total else None,
            _("col_ca_ht_net"): float(_local_ht_brut_total + _local_ht_remb_total),
            _("col_tva_brute"): float(_local_tva_brute_total),
            _("col_tva_remb"): float(_local_tva_remb_total) if summary.refund_count else None,
            _("col_tva_nette"): float(_local_tva_brute_total + _local_tva_remb_total)
        })
        for country in sorted(summary.local_by_country):
            _ht_brut = local_ht_brut_by_country.get(country, _ZERO)
            _ht_remb = local_ht_remb_by_country.get(country, _ZERO)
            _tva_brute = summary.local_by_country[country]
            _tva_remb = float(getattr(summary, "refund_local_by_country", {}).get(country, 0))
            recap_data.append({
                _("col_canal"): f"  → {_country_label(country)} ({country})",
                _("col_ca_ht_brut"): float(_ht_brut),
                _("col_ca_ht_remb"): float(_ht_remb) if _ht_remb else None,
                _("col_ca_ht_net"): float(_ht_brut + _ht_remb),
                _("col_tva_brute"): float(_tva_brute),
                _("col_tva_remb"): float(_tva_remb) if summary.refund_count else None,
                _("col_tva_nette"): float(_tva_brute + Decimal(str(_tva_remb)))
            })
    _recap_cols = [
        _("col_ca_ht_brut"), _("col_ca_ht_remb"), _("col_ca_ht_net"),
        _("col_tva_brute"), _("col_tva_remb"), _("col_tva_nette")
    ]
    _recap_df = pd.DataFrame(recap_data)
    _recap_cfg = _smart_money_df(
        _recap_df,
        money_cols=_recap_cols,
    )
    # Amélioration 3 : colonne Type pour distinguer totaux et sous-lignes
    # pays — un "→" (OSS/local par pays) ou "📦" (DDP par pays) marque
    # une ligne de détail par pays ; le reste (France CA3, OSS total,
    # IOSS, Fisc local total) est une ligne agrégée.
    _recap_df.insert(0, _("type_column_label"), _recap_df[_("col_canal")].apply(
        lambda c: _("type_pays") if str(c).startswith("  →") or str(c).startswith("📦") else _("type_total")
    ))
    _recap_cfg[_("type_column_label")] = st.column_config.TextColumn(_("type_column_label"), width="small")
    _recap_cfg[_("canal_column_label")] = st.column_config.TextColumn(_("canal_column_label"), width="large")

    if _can_export:
        st.dataframe(_recap_df, use_container_width=True, hide_index=True,
                     column_config=_recap_cfg)
    else:
        # Aperçu gratuit restreint :
        # - Lignes Total : CA visible, TVA verrouillée.
        # - Lignes Pays : tout verrouillé.
        _recap_preview = _recap_df.copy()
        tva_cols = [_("col_tva_brute"), _("col_tva_remb"), _("col_tva_nette")]
        ca_cols = [_("col_ca_ht_brut"), _("col_ca_ht_remb"), _("col_ca_ht_net")]

        # On s'assure que les colonnes sont de type object pour accepter les strings de verrouillage
        for col in tva_cols + ca_cols:
            if col in _recap_preview.columns:
                _recap_preview[col] = _recap_preview[col].astype(object)

        # Masquage conditionnel
        for idx, row in _recap_preview.iterrows():
            if row[_("type_column_label")] == _("type_total"):
                # Ligne Total : on masque seulement la TVA
                for col in tva_cols:
                    if col in _recap_preview.columns:
                        _recap_preview.at[idx, col] = _("locked_premium")
            else:
                # Ligne Pays : on masque tout (CA et TVA)
                for col in tva_cols + ca_cols:
                    if col in _recap_preview.columns:
                        _recap_preview.at[idx, col] = _("locked_premium")

        # Affichage (on retire la colonne Type pour l'aperçu comme avant)
        st.table(_recap_preview.drop(columns=[_("type_column_label")]))
        st.caption(_("locked_preview_caption"))

    if summary.refund_count:
        st.info(_("refund_summary_info", count=summary.refund_count, ht=_fmt(summary.refund_total_ht)))

    # ── Contrôle de Cohérence Comptable ─────────────────────────────
    _declared_net_ht = summary.total_ht + summary.refund_total_ht
    _bucket_net_ht = summary.net_ht_total
    _coherence_delta = _declared_net_ht - _bucket_net_ht
    with st.expander(_("coherence_header"), expanded=abs(_coherence_delta) > Decimal("0.01")):
        _bucket_rows = [
            {_("col_fiscal_canal"): b, _("col_net_ht_eur"): float(v)}
            for b, v in summary.net_ht_by_bucket.items() if v != 0
        ]
        if _bucket_rows:
            _gated_preview_table(pd.DataFrame(_bucket_rows), _can_export,
                column_config={_("col_net_ht_eur"): _money_col(_("col_net_ht_eur"))})
        c1, c2, c3 = st.columns(3)
        c1.metric(_("kpi_declared_net_ht"), _fmt(_declared_net_ht))
        c2.metric(_("kpi_sum_canals_net_ht"), _fmt(_bucket_net_ht))
        c3.metric(_("kpi_gap"), _fmt(_coherence_delta))
        if abs(_coherence_delta) > Decimal("0.01"):
            st.error(_("coherence_error"))
        else:
            st.success(_("coherence_success"))
        st.caption(_("coherence_caption"))

    # Exposé pour l'onglet Téléchargements (voir docstring de ce module).
    ctx.oss_tva_net_total = _oss_tva_net_total
