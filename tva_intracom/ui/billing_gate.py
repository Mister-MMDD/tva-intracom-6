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
from tva_intracom.rates import is_eu
from tva_intracom.ui.formatting import _country_label
from tva_intracom.vies_engine import resolve_scope_id as _vies_resolve_scope_id
from tva_intracom.ui.sidebar import _cached_db_read


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
    sub_status: Optional[str] = None

    # Rattachement compte Amazon (UNIQUE_ACCOUNT_IDENTIFIER) <-> SIREN —
    # anti-abus (voir billing.get_siren_links_for_identifiers). Bloque
    # can_export tant qu'il reste des identifiants non confirmés ou en
    # conflit avec un autre SIREN — voir render_account_link_panel().
    account_link_blocked: bool = False
    unlinked_identifiers: list = field(default_factory=list)
    conflicting_links: list = field(default_factory=list)  # [(identifier, other_siren)]

    # SIREN saisi non retrouvé parmi les SIREN enregistrés pour ce compte —
    # distinct de account_link_blocked (rattachement compte Amazon<->SIREN) :
    # ici c'est le SIREN lui-même qui n'est pas reconnu. Les deux forcent
    # can_export=False mais doivent afficher un message différent de celui
    # du paywall Stripe dans gated_download() — voir BUGFIX ci-dessous.
    siren_mismatch: bool = False

    # Abonnement actif OU crédit ponctuel — indépendant des gates de
    # conformité (SIREN, rattachement compte, quota). Voir build_billing_gate.
    billing_ok: bool = True

    current_user: Any = field(repr=False, default=None)
    vies_summary: Any = field(repr=False, default=None)
    stripe_success_url: Any = field(repr=False, default=None)
    stripe_cancel_url: Any = field(repr=False, default=None)
    vies_scope_id: str = field(repr=False, default="")
    siren_entreprise: str = field(repr=False, default="")
    nom_entreprise: str = field(repr=False, default="")

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
        # Priorité 0 : Paiement / Déblocage de la période (l'abonnement/crédit
        # doit toujours être la 1ère raison de blocage affichée à l'utilisateur,
        # avant toute autre vérification — un non-abonné n'a pas à voir un
        # message de conformité ou d'anti-abus tant qu'il n'a pas payé).
        if not self.can_export:
            if self.sub_status == "incomplete":
                st.info(_("gate_payment_pending_info"))
                return

            if self.quota_status and self.quota_status.blocked:
                st.error(
                    _("gate_quota_blocked_err",
                      label=label,
                      registered=self.quota_status.registered_count,
                      quota=self.quota_status.quota,
                      over=self.quota_status.over_quota_by)
                )
                return

            # BUGFIX (priorité) : account_link_blocked / siren_mismatch
            # forcent can_export=False mais N'ONT DE SENS QUE pour un compte
            # DÉJÀ payant — ce sont des gates de conformité, pas de
            # facturation. Un utilisateur qui n'est PAS (encore) abonné doit
            # toujours voir le paywall Stripe en priorité : sinon, un
            # nouveau compte non-abonné (dont le rattachement Amazon<->SIREN
            # n'a par définition jamais pu être confirmé) voyait le message
            # "confirmez le rattachement du compte" au lieu du vrai message
            # bloquant, "abonnez-vous". `billing_ok` est calculé plus haut,
            # AVANT les gates de conformité, précisément pour permettre
            # cette distinction.
            if not self.billing_ok:
                pass  # tombe directement dans le paywall Stripe ci-dessous

            # Ces deux cas ne concernent donc que le cas où le compte EST
            # déjà payant (billing_ok True) mais reste bloqué par une
            # conformité non résolue.
            elif self.account_link_blocked:
                st.error(_("gate_account_link_blocked_err", label=label))
                return

            elif self.siren_mismatch:
                st.error(_("gate_siren_not_registered_err", siren=self.siren_entreprise))
                return

            _url = self.get_payg_checkout_url()
            if _url:
                st.markdown(
                    f"""
                    <a href="{_url}" target="_top" style="text-decoration: none;">
                        <div style="
                            display: flex;
                            align-items: center;
                            justify-content: center;
                            gap: 8px;
                            background-color: #7F77DD;
                            color: #FFFFFF;
                            border-radius: 8px;
                            padding: 10px 16px;
                            font-size: 14px;
                            font-weight: 500;
                            cursor: pointer;
                            width: 100%;
                        ">
                            🔓 {label} — {self.unlock_label_suffix}
                        </div>
                    </a>
                    """,
                    unsafe_allow_html=True,
                )
                st.caption(_("unlock_export_footer"))
            else:
                _err = st.session_state.get(f"_stripe_checkout_error::{self.period_label}", _("unknown_error"))
                st.error(_("gate_payment_unavailable_err", label=label, error=_err))
            return

        # Priorité 1 : Rattachement compte Amazon <-> SIREN non résolu (anti-abus).
        # Vérifié seulement une fois l'abonnement/crédit confirmé : peu importe
        # qu'une période soit déjà débloquée, un fichier appartenant à un autre
        # client ne doit jamais être exportable sans confirmation/résolution
        # explicite du conflit.
        if self.account_link_blocked:
            st.error(_("gate_account_link_blocked_err", label=label))
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
        vies_scope_id: str = "",
        all_account_identifiers=None,
        nom_entreprise: str = "",
        home_country: str = "FR",
) -> BillingGate:
    """Exécute tout le gating (période, crédit/abonnement, quota SIREN,
    conformité TVA/IOSS) et retourne un BillingGate prêt à l'emploi.
    """
    period_label, period_detected_range = detect_period_label(results, oss_period)
    st.session_state["_period_label"] = period_label

    # NOTE (correctif) : ce bloc forçait auparavant un st.rerun() complet dès
    # que `cache_key` changeait (donc à CHAQUE changement de pays d'origine,
    # chaque nouvel import de fichier, chaque retry VIES...), uniquement pour
    # que le bloc PAYG de la sidebar (rendu PLUS TÔT dans le script, donc
    # avant que period_label ne soit calculé ici) affiche un libellé de
    # période à jour dès le tout premier rendu. Le rerun forcé interrompait
    # le script à cet endroit précis, empêchant les onglets (Détail ventes,
    # Visualisations, etc.) de se rendre du tout dans cette passe — d'où
    # l'écran qui semblait vide après un changement de pays d'origine, jusqu'à
    # ce qu'une interaction ultérieure (ex: changer la devise) redéclenche un
    # passage complet qui, lui, aboutissait. Le compromis retenu : la légende
    # PAYG peut afficher la période de la dernière analyse pendant UN seul
    # rendu dans de rares cas (purement cosmétique, sans impact sur le calcul
    # ni l'export), en échange d'un affichage systématique et immédiat du
    # contenu principal.

    # `has_export_credit()` réinterroge en interne l'abonnement via
    # `has_active_subscription_direct()` → `get_subscription_status()` — la
    # même requête que la sidebar vient déjà de faire et de mettre en cache
    # plus tôt dans ce même rerun. On réutilise ce cache pour éviter le
    # doublon ; seule la table `tva_export_credits` (crédit ponctuel à la
    # période) nécessite une lecture propre à ce gate.
    _cached_sub_status = _cached_db_read(
        f"sub_status_{current_user.id}",
        lambda: tva_billing.get_subscription_status(current_user.id),
    )
    can_export = bool(period_label) and (
        (_cached_sub_status and _cached_sub_status.active)
        or tva_billing.has_export_credit(current_user.id, period_label)
    )
    # État "financier" pur (abonnement actif OU crédit ponctuel), capturé
    # AVANT les gates de conformité (SIREN, quota, rattachement compte)
    # ci-dessous qui peuvent eux aussi mettre can_export à False. Nécessaire
    # pour prioriser correctement le message affiché dans gated_download() :
    # un utilisateur non abonné doit voir "abonnez-vous", jamais un message
    # de conformité qui n'a de sens que s'il est déjà payant.
    billing_ok = can_export

    quota_status = siren_quota_status

    # ── Gate SIREN
    # Réutilise le même cache (clé identique) que render_sidebar() : la
    # sidebar a déjà lu/mis en cache cette liste plus tôt dans le même
    # rerun, donc pas de second aller-retour Postgres ici.
    siren_mismatch = False
    if can_export and siren_entreprise:
        try:
            _siren_ok = any(
                r["siren"] == siren_entreprise
                for r in _cached_db_read(
                    f"sirens_{current_user.id}",
                    lambda: tva_billing.list_registered_sirens(current_user.id),
                )
            )
        except Exception:
            _siren_ok = True
        if not _siren_ok:
            can_export = False
            siren_mismatch = True
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
    # BUGFIX : un stock situé hors UE (pays non listé dans rates.EU_COUNTRIES)
    # ne crée aucune obligation d'immatriculation TVA intracommunautaire — il
    # ne doit donc jamais réclamer un numéro de TVA local ni bloquer le
    # téléchargement. `all_stock_countries` n'était pas filtré à l'UE, et
    # l'exclusion du pays "domestique" était figée sur "FR" au lieu du pays
    # d'origine choisi (home_country).
    missing_vats = []
    required_local_vats = {c for c in all_stock_countries if c and is_eu(c)} | pay_eu
    if seller_is_importer:
        required_local_vats |= {r.vat_country for r in results if r.scenario.value == "IMPORT_SELLER_AS_IMPORTER"}

    for _ccode in sorted(required_local_vats):
        if _ccode and _ccode != home_country and not local_vat_numbers.get(_ccode):
            missing_vats.append(f"{_country_label(_ccode)} ({_ccode})")

    _has_ioss_vendeur = any(r.channel == Channel.IOSS for r in results)
    ioss_missing = _has_ioss_vendeur and not ioss_number.strip()

    compliance_blocked = bool(missing_vats or ioss_missing)

    # ── Gate Rattachement compte Amazon <-> SIREN (anti-abus) ─────────────
    # Scope identique à celui du cache VIES (partagé par domaine pro, isolé
    # par compte pour les domaines grand public) — un cabinet ne reconfirme
    # pas ce rattachement à chaque collaborateur.
    _scope_id = vies_scope_id or _vies_resolve_scope_id(current_user.email)
    unlinked_identifiers: list[str] = []
    conflicting_links: list[tuple[str, str]] = []
    if all_account_identifiers and siren_entreprise:
        try:
            _existing_links = tva_billing.get_siren_links_for_identifiers(
                _scope_id, all_account_identifiers
            )
        except Exception:
            _existing_links = {}
        for _identifier in sorted(all_account_identifiers):
            _linked_siren = _existing_links.get(_identifier)
            if _linked_siren is None:
                unlinked_identifiers.append(_identifier)
            elif _linked_siren != siren_entreprise:
                conflicting_links.append((_identifier, _linked_siren))

    account_link_blocked = bool(unlinked_identifiers or conflicting_links)
    if account_link_blocked:
        can_export = False

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
        billing_ok=billing_ok,
        quota_status=quota_status,
        compliance_blocked=compliance_blocked,
        missing_vats=missing_vats,
        ioss_missing=ioss_missing,
        unlock_label_suffix=unlock_label_suffix,
        sub_status=_cached_sub_status.status if _cached_sub_status else None,
        account_link_blocked=account_link_blocked,
        siren_mismatch=siren_mismatch,
        unlinked_identifiers=unlinked_identifiers,
        conflicting_links=conflicting_links,
        current_user=current_user,
        vies_summary=vies_summary,
        stripe_success_url=stripe_success_url,
        stripe_cancel_url=stripe_cancel_url,
        vies_scope_id=_scope_id,
        siren_entreprise=siren_entreprise,
        nom_entreprise=nom_entreprise,
    )


@st.fragment
def _render_unlinked_identifiers_fragment(gate: "BillingGate") -> None:
    """Isolé en fragment : BUGFIX — cocher la case de confirmation ne fait
    que révéler le bouton "Confirmer" juste en dessous, ça ne doit surtout
    pas redessiner toute la page (les 6 onglets, tableaux et graphiques déjà
    affichés) à chaque coche. Seul le clic sur "Confirmer" a besoin d'un
    rerun complet (st.rerun() par défaut, hors fragment) puisqu'il faut que
    build_billing_gate() soit ré-évalué en amont pour faire disparaître ce
    panneau et débloquer les téléchargements."""
    for _identifier in gate.unlinked_identifiers:
        st.markdown(
            _("account_link_new_title", identifier=_identifier)
        )
        st.caption(_("account_link_new_caption"))
        _confirm_key = f"confirm_link_{gate.vies_scope_id}_{_identifier}"
        _confirmed = st.checkbox(
            _("account_link_confirm_checkbox",
              identifier=_identifier, company=gate.nom_entreprise or gate.siren_entreprise,
              siren=gate.siren_entreprise),
            key=_confirm_key,
        )
        if _confirmed:
            if st.button(_("account_link_confirm_btn"), key=f"btn_link_{gate.vies_scope_id}_{_identifier}"):
                try:
                    tva_billing.link_account_identifier(gate.vies_scope_id, _identifier, gate.siren_entreprise)
                    st.success(_("account_link_success", identifier=_identifier, siren=gate.siren_entreprise))
                    st.rerun()  # rerun complet volontaire : fait disparaître ce panneau
                except Exception as _link_err:
                    st.error(_("account_link_error", error=_link_err))


def render_account_link_panel(gate: BillingGate) -> None:
    """Affiche, le cas échéant, le panneau de confirmation/résolution du
    rattachement compte Amazon (UNIQUE_ACCOUNT_IDENTIFIER) <-> SIREN.

    À appeler une fois, juste après build_billing_gate() et avant l'affichage
    des onglets — comme le plan d'action Immatriculations. Ne fait rien si
    `gate.account_link_blocked` est False (cas normal : aucun nouvel
    identifiant, ou tous déjà liés au bon SIREN).

    Deux cas traités séparément :
      - identifiant jamais vu dans ce scope : demande de confirmation
        explicite avant de créer le lien (checkbox + bouton) — on ne lie
        jamais automatiquement, pour ne pas figer une erreur de sélection
        de SIREN faite avant l'upload du fichier.
      - identifiant déjà lié à un AUTRE SIREN que celui sélectionné :
        avertissement + bouton pour basculer la sélection de SIREN vers le
        bon (jamais de bascule silencieuse).
    """
    if not gate.account_link_blocked:
        return

    # Libellés lisibles pour les SIREN déjà connus du compte (utilisé pour
    # les messages de conflit : "SIREN X" -> "Client Untel — 123456789").
    try:
        _sirens = tva_billing.list_registered_sirens(gate.current_user.id)
        _siren_labels = {r["siren"]: f"{r['company_name'] or r['siren']} — {r['siren']}" for r in _sirens}
    except Exception:
        _siren_labels = {}

    with st.container():
        st.warning(_("account_link_panel_intro"))

        _render_unlinked_identifiers_fragment(gate)

        for _identifier, _other_siren in gate.conflicting_links:
            _other_label = _siren_labels.get(_other_siren, _other_siren)
            _current_label = gate.nom_entreprise or gate.siren_entreprise
            st.error(_("account_link_conflict_title", identifier=_identifier))
            st.caption(_("account_link_conflict_text", other_label=_other_label, current_label=_current_label))
            if st.button(_("account_link_switch_btn", other_label=_other_label),
                         key=f"btn_switch_{gate.vies_scope_id}_{_identifier}"):
                st.session_state["siren_select_box"] = _other_siren
                st.rerun()