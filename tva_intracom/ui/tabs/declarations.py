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

from tva_intracom.models import Channel
from tva_intracom.oss_export import aggregate_oss_results
from tva_intracom.ui.formatting import _country_label, _gated_preview_table, _money_col, _smart_money_df
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

    st.subheader("Ce que vous devez reverser")

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

    # France CA3
    fr_ht_brut = sum(r.sale.amount_ht for r in results if r.channel == Channel.FR_DOMESTIC)
    fr_ht_remb = sum(r.sale.amount_ht for r in (refund_results or []) if r.channel == Channel.FR_DOMESTIC)

    recap_data = [
        {
            "Canal": "TVA domestique France (CA3)",
            "CA HT Brut (EUR)": float(fr_ht_brut),
            "CA HT Remb. (EUR)": float(fr_ht_remb) if fr_ht_remb else None,
            "CA HT Net (EUR)": float(fr_ht_brut + fr_ht_remb),
            "TVA Brute (EUR)": float(summary.fr_domestic_vat),
            "TVA Remb. (EUR)": float(summary.refund_fr_domestic_vat) if summary.refund_count else None,
            "TVA Nette (EUR)": float(summary.net_fr_domestic_vat)
        },
        {
            "Canal": "Guichet OSS (total)",
            "CA HT Brut (EUR)": float(_oss_ht_vente_total),
            "CA HT Remb. (EUR)": float(_oss_ht_remb_total) if _oss_ht_remb_total else None,
            "CA HT Net (EUR)": float(_oss_ht_net_total),
            "TVA Brute (EUR)": float(_oss_tva_vente_total),
            "TVA Remb. (EUR)": float(_oss_tva_remb_total) if summary.refund_count else None,
            "TVA Nette (EUR)": float(_oss_tva_net_total)
        },
    ]
    for country in sorted(_oss_country_totals):
        _c = _oss_country_totals[country]
        recap_data.append({
            "Canal": f"  → {_country_label(country)} ({country})",
            "CA HT Brut (EUR)": float(_c["ht_vente"]),
            "CA HT Remb. (EUR)": float(_c["ht_remb"]) if _c["ht_remb"] else None,
            "CA HT Net (EUR)": float(_c["ht_net"]),
            "TVA Brute (EUR)": float(_c["tva_vente"]),
            "TVA Remb. (EUR)": float(_c["tva_remb"]) if summary.refund_count else None,
            "TVA Nette (EUR)": float(_c["tva_net"])
        })

    _ioss_results = [r for r in results if r.scenario.value == "IOSS_DIRECT"]
    _ioss_refund_results = [r for r in (refund_results or []) if r.scenario.value == "IOSS_DIRECT"]
    if _ioss_results or _ioss_refund_results:
        _ioss_tva_brute = sum(r.vat_amount for r in _ioss_results)
        _ioss_tva_remb = sum(r.vat_amount for r in _ioss_refund_results)
        _ioss_ht_brut = sum(r.sale.amount_ht for r in _ioss_results)
        _ioss_ht_remb = sum(r.sale.amount_ht for r in _ioss_refund_results)
        recap_data.append({
            "Canal": "🌐 Guichet IOSS (propre numéro vendeur)",
            "CA HT Brut (EUR)": float(_ioss_ht_brut),
            "CA HT Remb. (EUR)": float(_ioss_ht_remb) if _ioss_ht_remb else None,
            "CA HT Net (EUR)": float(_ioss_ht_brut + _ioss_ht_remb),
            "TVA Brute (EUR)": float(_ioss_tva_brute),
            "TVA Remb. (EUR)": float(_ioss_tva_remb) if _ioss_tva_remb else None,
            "TVA Nette (EUR)": float(_ioss_tva_brute + _ioss_tva_remb)
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
            _label = "TVA DDP France (CA3)" if _ccode == "FR" else f"TVA DDP {_country_label(_ccode)} (immat. locale)"
            recap_data.append({
                "Canal": f"📦 {_label}",
                "CA HT Brut (EUR)": float(_vals["ht_brut"]),
                "CA HT Remb. (EUR)": float(_vals["ht_remb"]) if _vals["ht_remb"] else None,
                "CA HT Net (EUR)": float(_vals["ht_brut"] + _vals["ht_remb"]),
                "TVA Brute (EUR)": float(_vals["tva_brute"]),
                "TVA Remb. (EUR)": float(_vals["tva_remb"]) if _vals["tva_remb"] else None,
                "TVA Nette (EUR)": float(_vals["tva_brute"] + _vals["tva_remb"])
            })

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

        recap_data.append({
            "Canal": "Fisc local (hors FR) — Total",
            "CA HT Brut (EUR)": float(_local_ht_brut_total),
            "CA HT Remb. (EUR)": float(_local_ht_remb_total) if _local_ht_remb_total else None,
            "CA HT Net (EUR)": float(_local_ht_brut_total + _local_ht_remb_total),
            "TVA Brute (EUR)": float(_local_tva_brute_total),
            "TVA Remb. (EUR)": float(_local_tva_remb_total) if summary.refund_count else None,
            "TVA Nette (EUR)": float(_local_tva_brute_total + _local_tva_remb_total)
        })
        for country in sorted(summary.local_by_country):
            _ht_brut = local_ht_brut_by_country.get(country, _ZERO)
            _ht_remb = local_ht_remb_by_country.get(country, _ZERO)
            _tva_brute = summary.local_by_country[country]
            _tva_remb = float(getattr(summary, "refund_local_by_country", {}).get(country, 0))
            recap_data.append({
                "Canal": f"  → {_country_label(country)} ({country})",
                "CA HT Brut (EUR)": float(_ht_brut),
                "CA HT Remb. (EUR)": float(_ht_remb) if _ht_remb else None,
                "CA HT Net (EUR)": float(_ht_brut + _ht_remb),
                "TVA Brute (EUR)": float(_tva_brute),
                "TVA Remb. (EUR)": float(_tva_remb) if summary.refund_count else None,
                "TVA Nette (EUR)": float(_tva_brute + Decimal(str(_tva_remb)))
            })
    _recap_cols = [
        "CA HT Brut (EUR)", "CA HT Remb. (EUR)", "CA HT Net (EUR)",
        "TVA Brute (EUR)", "TVA Remb. (EUR)", "TVA Nette (EUR)"
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
    _recap_df.insert(0, "Type", _recap_df["Canal"].apply(
        lambda c: "↳ Pays" if str(c).startswith("  →") or str(c).startswith("📦") else "Total"
    ))
    _recap_cfg["Type"] = st.column_config.TextColumn("Type", width="small")
    _recap_cfg["Canal"] = st.column_config.TextColumn("Canal", width="large")

    if _can_export:
        st.dataframe(_recap_df, use_container_width=True, hide_index=True,
                     column_config=_recap_cfg)
    else:
        # Aperçu gratuit restreint :
        # - Lignes Total : CA visible, TVA verrouillée.
        # - Lignes Pays : tout verrouillé.
        _recap_preview = _recap_df.copy()
        tva_cols = ["TVA Brute (EUR)", "TVA Remb. (EUR)", "TVA Nette (EUR)"]
        ca_cols = ["CA HT Brut (EUR)", "CA HT Remb. (EUR)", "CA HT Net (EUR)"]

        # On s'assure que les colonnes sont de type object pour accepter les strings de verrouillage
        for col in tva_cols + ca_cols:
            if col in _recap_preview.columns:
                _recap_preview[col] = _recap_preview[col].astype(object)

        # Masquage conditionnel
        for idx, row in _recap_preview.iterrows():
            if row["Type"] == "Total":
                # Ligne Total : on masque seulement la TVA
                for col in tva_cols:
                    if col in _recap_preview.columns:
                        _recap_preview.at[idx, col] = "[🔒 Verrouillé Option Premium]"
            else:
                # Ligne Pays : on masque tout (CA et TVA)
                for col in tva_cols + ca_cols:
                    if col in _recap_preview.columns:
                        _recap_preview.at[idx, col] = "[🔒 Verrouillé Option Premium]"

        # Affichage (on retire la colonne Type pour l'aperçu comme avant)
        st.table(_recap_preview.drop(columns=["Type"]))
        st.caption(
            "🔒 Aperçu limité : débloquez cette période (achat ou abonnement) "
            "pour voir le détail par pays et les montants de TVA dus."
        )

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
            _gated_preview_table(pd.DataFrame(_bucket_rows), _can_export,
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

    # Exposé pour l'onglet Téléchargements (voir docstring de ce module).
    ctx.oss_tva_net_total = _oss_tva_net_total
