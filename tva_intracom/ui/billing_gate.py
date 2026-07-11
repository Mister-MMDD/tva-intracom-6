"""Gating billing (extrait tel quel de app.py).

Regroupe :
  - detect_period_label() : détection du period_label à partir des ventes
    (ou de l'override manuel `oss_period`), sans effet de bord ;
  - build_billing_gate() : calcule can_export (crédit PAYG ou abonnement),
    le statut de quota SIREN, le blocage de conformité (TVA locales / IOSS
    manquants), et fabrique l'objet BillingGate utilisé par tous les onglets
    pour les téléchargements (`gate.gated_download(...)`).

Usage dans app.py :

    from tva_intracom.ui.billing_gate import build_billing_gate

    gate = build_billing_gate(
        results=results, oss_period=oss_period, cache_key=_cache_key,
        current_user=_current_user, siren_entreprise=siren_entreprise,
        siren_quota_status=_siren_quota_status,
        all_stock_countries=all_stock_countries, pay_eu=pay_eu,
        seller_is_importer=seller_is_importer,
        local_vat_numbers=local_vat_numbers, ioss_number=ioss_number,
        vies_summary=vies_summary,
        stripe_success_url=_stripe_success_url,
        stripe_cancel_url=_stripe_cancel_url,
    )
    period_label = gate.period_label
    _can_export = gate.can_export
    _gated_download = gate.gated_download
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime as _dt
from typing import Any, Optional

import streamlit as st
from tva_intracom.i18n import _

from tva_intracom import billing as tva_billing
from tva_intracom.models import Channel
from tva_intracom.ui.formatting import _country_label


def detect_period_label(results, oss_period: str) -> tuple[str, Optional[tuple[str, str]]]:
    """Calcule le period_label sans effet de bord (pas de st.info ici)
    — la même logique que l'auto-détection historique, extraite pour
    être appelable avant l'affichage des onglets. Retourne
    (period_label, (date_min, date_max) | None)."""
    if oss_period != "__auto__":
        return oss_period, None
    _dates = sorted(
        r.sale.transaction_date for r in results
        if r.sale.transaction_date and len(r.sale.transaction_date) >= 7
    )
    if not _dates:
        return "", None
    _d_min = _dt.fromisoformat(_dates[0][:10])
    _d_max = _dt.fromisoformat(_dates[-1][:10])
    _y_min, _m_min = _d_min.year, _d_min.month
    _y_max, _m_max = _d_max.year, _d_max.month
    if _y_min != _y_max:
        _lbl = f"{_y_min}-{_y_max}"
    elif _m_min == _m_max:
        _lbl = f"{_y_min}-{_m_min:02d}"
    elif _m_min == 1 and _m_max == 12:
        _lbl = str(_y_min)
    elif _m_min == 1 and _m_max == 6:
        _lbl = f"{_y_min}-S1"
    elif _m_min == 7 and _m_max == 12:
        _lbl = f"{_y_min}-S2"
    else:
        _q_min = (_m_min - 1) // 3 + 1
        _q_max = (_m_max - 1) // 3 + 1
        _lbl = f"{_y_min}-Q{_q_min}" if _q_min == _q_max else f"{_y_min}-Q{_q_min}_Q{_q_max}"
    return _lbl, (_dates[0][:10], _dates[-1][:10])


@dataclass
class BillingGate:
    """Résultat du gating billing/quota/conformité, avec la méthode
    `gated_download` utilisée par tous les onglets pour tout export."""

    period_label: str
    period_detected_range: Optional[tuple[str, str]]
    can_export: bool
    quota_status: Any
    compliance_blocked: bool
    missing_vats: list
    ioss_missing: bool
    unlock_label_suffix: str

    current_user: Any = field(repr=False)
    vies_summary: Any = field(repr=False)
    stripe_success_url: Any = field(repr=False)
    stripe_cancel_url: Any = field(repr=False)

    def get_payg_checkout_url(self) -> Optional[str]:
        """Crée la session Stripe Checkout une seule fois par période/session
        (mise en cache dans session_state) et retourne son URL."""
        _cache_key = f"_stripe_checkout_url::{self.period_label}"
        if _cache_key not in st.session_state:
            try:
                st.session_state[_cache_key] = tva_billing.create_payg_checkout_session(
                    user_id=self.current_user.id,
                    email=self.current_user.email,
                    period_label=self.period_label,
                    success_url=self.stripe_success_url("export_ok=1"),
                    cancel_url=self.stripe_cancel_url(),
                )
            except Exception as _billing_err:
                st.session_state.pop(_cache_key, None)
                st.session_state[f"_stripe_checkout_error::{self.period_label}"] = str(_billing_err)
        return st.session_state.get(_cache_key)

    def gated_download(self, label, data, file_name, mime, **kwargs) -> None:
        """Remplace st.download_button : affiche le vrai bouton si crédit
        disponible pour la période, sinon un lien direct vers Stripe Checkout."""
        # Priorité 1 : Paiement / Déblocage de la période
        if not self.can_export:
            if self.quota_status and self.quota_status.blocked:
                st.error(
                    _("gate_quota_blocked_err",
                      label=label,
                      registered=self.quota_status.registered_count,
                      quota=self.quota_status.quota,
                      over=self.quota_status.over_quota_by)
                )
                return

            _url = self.get_payg_checkout_url()
            if _url:
                st.link_button(f"🔒 {label} — {self.unlock_label_suffix}", _url,
                               use_container_width=kwargs.get("use_container_width", False))
            else:
                _err = st.session_state.get(f"_stripe_checkout_error::{self.period_label}", _("unknown_error"))
                st.error(_("gate_payment_unavailable_err", label=label, error=_err))
            return

        # Priorité 2 : Conformité (seulement si la période est débloquée)
        if self.compliance_blocked:
            _msg = _("gate_compliance_blocked_header", label=label)
            if self.missing_vats:
                _msg += _("gate_compliance_missing_vats", vats=', '.join(self.missing_vats))
            if self.ioss_missing:
                _msg += _("gate_compliance_missing_ioss")
            _msg += _("gate_compliance_blocked_footer")
            st.error(_msg)
            return

        # Priorité 3 : Affichage du bouton de téléchargement (avec warning VIES éventuel)
        if self.vies_summary and self.vies_summary.total_inconclusive > 0:
            st.warning(
                _("gate_vies_warning", label=label, count=self.vies_summary.total_inconclusive)
            )
        st.download_button(label, data=data, file_name=file_name, mime=mime, **kwargs)


def build_billing_gate(
        *,
        results,
        oss_period: str,
        cache_key,
        current_user,
        siren_entreprise: str,
        siren_quota_status,
        all_stock_countries,
        pay_eu,
        seller_is_importer: bool,
        local_vat_numbers: dict,
        ioss_number: str,
        vies_summary,
        stripe_success_url,
        stripe_cancel_url,
) -> BillingGate:
    """Exécute tout le gating (période, crédit/abonnement, quota SIREN,
    conformité TVA/IOSS) et retourne un BillingGate prêt à l'emploi.
    """
    period_label, period_detected_range = detect_period_label(results, oss_period)
    st.session_state["_period_label"] = period_label

    if st.session_state.get("_period_sidebar_synced_key") != cache_key:
        st.session_state["_period_sidebar_synced_key"] = cache_key
        st.rerun()

    can_export = bool(period_label) and tva_billing.has_export_credit(
        current_user.id, period_label
    )

    quota_status = siren_quota_status

    # ── Gate SIREN
    if can_export and siren_entreprise:
        try:
            _siren_ok = any(
                r["siren"] == siren_entreprise
                for r in tva_billing.list_registered_sirens(current_user.id)
            )
        except Exception:
            _siren_ok = True
        if not _siren_ok:
            can_export = False
            st.error(_("gate_siren_not_registered_err", siren=siren_entreprise))

    # ── Gate sur-quota
    if can_export and quota_status and quota_status.blocked:
        can_export = False
        st.error(
            _("gate_quota_global_blocked_err",
              registered=quota_status.registered_count,
              quota=quota_status.quota,
              over=quota_status.over_quota_by)
        )

    # ── Gate Conformité (TVA & IOSS) ──────────────────────────────────────
    missing_vats = []
    required_local_vats = all_stock_countries | pay_eu
    if seller_is_importer:
        required_local_vats |= {r.vat_country for r in results if r.scenario.value == "IMPORT_SELLER_AS_IMPORTER"}

    for _ccode in sorted(required_local_vats):
        if _ccode and _ccode != "FR" and not local_vat_numbers.get(_ccode):
            missing_vats.append(f"{_country_label(_ccode)} ({_ccode})")

    _has_ioss_vendeur = any(r.channel == Channel.IOSS for r in results)
    ioss_missing = _has_ioss_vendeur and not ioss_number.strip()

    compliance_blocked = bool(missing_vats or ioss_missing)

    try:
        payg_price = tva_billing.get_pricing_grid(current_user.id).get("payg")
    except Exception:
        payg_price = None

    if payg_price and payg_price.get("amount") is not None:
        if payg_price.get("discounted_amount") is not None:
            unlock_label_suffix = _(
                "unlock_label_discounted",
                discounted_amount=f"{payg_price['discounted_amount']:.0f}",
                currency=payg_price['currency'].upper(),
                amount=f"{payg_price['amount']:.0f}",
                discount_code=payg_price['discount_code']
            )
        else:
            unlock_label_suffix = _(
                "unlock_label_standard",
                amount=f"{payg_price['amount']:.0f}",
                currency=payg_price['currency'].upper()
            )
    else:
        unlock_label_suffix = _("unlock_label_fallback")

    return BillingGate(
        period_label=period_label,
        period_detected_range=period_detected_range,
        can_export=can_export,
        quota_status=quota_status,
        compliance_blocked=compliance_blocked,
        missing_vats=missing_vats,
        ioss_missing=ioss_missing,
        unlock_label_suffix=unlock_label_suffix,
        current_user=current_user,
        vies_summary=vies_summary,
        stripe_success_url=stripe_success_url,
        stripe_cancel_url=stripe_cancel_url,
    )