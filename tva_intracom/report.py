"""Agregation des resultats de TVA et rendu du recapitulatif."""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Dict, List, Optional, Set

from .models import Channel, Collector, Scenario, VatResult
from .i18n import _
from .rates import is_eu

_ZERO = Decimal("0.00")


@dataclass
class ReportSummary:
    """Synthese chiffree de l'ensemble des ventes."""

    total_ht: Decimal = _ZERO

    # TVA que VOUS devez reverser.
    fr_domestic_vat: Decimal = _ZERO                      # CA3 France
    oss_by_country: Dict[str, Decimal] = field(default_factory=dict)  # via OSS (FR)
    local_by_country: Dict[str, Decimal] = field(default_factory=dict)  # immat. locale
    ioss_vat: Decimal = _ZERO                             # Guichet IOSS (propre numéro)

    # TVA geree par d'autres / sans reversement de votre part.
    amazon_vat: Decimal = _ZERO                           # deemed supplier
    import_vat: Decimal = _ZERO                           # due en douane par l'importateur
    reverse_charge_ht: Decimal = _ZERO                    # B2B exonere (HT)
    export_ht: Decimal = _ZERO                            # export hors UE (HT)

    # Remboursements (montants négatifs, ventilés par canal).
    refund_total_ht: Decimal = _ZERO                      # CA HT remboursé (négatif)
    refund_fr_domestic_vat: Decimal = _ZERO               # TVA FR à déduire (négatif)
    refund_oss_by_country: Dict[str, Decimal] = field(default_factory=dict)  # TVA OSS à déduire
    refund_local_by_country: Dict[str, Decimal] = field(default_factory=dict)
    refund_amazon_vat: Decimal = _ZERO                    # TVA Amazon remboursée
    refund_count: int = 0

    # Cas 4 : pays ou le stock reside et qui imposent une immatriculation locale.
    stock_countries_requiring_registration: Set[str] = field(default_factory=set)

    # Ventilation HT EXHAUSTIVE par "seau" de traitement fiscal (ventes, hors
    # remboursements). Chaque VatResult tombe dans exactement un seau — la
    # classification est construite à partir de (channel, collector, scenario)
    # de façon à ce que la somme des seaux égale TOUJOURS total_ht par
    # construction. Sert de test d'intégrité (Contrôle de Cohérence Comptable
    # dans app.py) : si un scénario futur n'était pas couvert par les branches
    # ci-dessous, il tomberait dans "Autre / non classé" et rendrait l'écart
    # visible plutôt que silencieux.
    ht_by_bucket: Dict[str, Decimal] = field(default_factory=dict)
    refund_ht_by_bucket: Dict[str, Decimal] = field(default_factory=dict)

    @property
    def oss_total(self) -> Decimal:
        return sum(self.oss_by_country.values(), _ZERO)

    @property
    def local_total(self) -> Decimal:
        return sum(self.local_by_country.values(), _ZERO)

    @property
    def net_local_by_country(self) -> Dict[str, Decimal]:
        """TVA locale nette par pays (ventes - remboursements)."""
        all_countries = set(self.local_by_country) | set(self.refund_local_by_country)
        return {
            c: self.local_by_country.get(c, _ZERO) + self.refund_local_by_country.get(c, _ZERO)
            for c in sorted(all_countries)
        }

    @property
    def net_local_total(self) -> Decimal:
        return sum(self.net_local_by_country.values(), _ZERO)

    @property
    def refund_oss_total(self) -> Decimal:
        return sum(self.refund_oss_by_country.values(), _ZERO)

    @property
    def net_fr_domestic_vat(self) -> Decimal:
        """TVA domestique FR nette des remboursements."""
        return self.fr_domestic_vat + self.refund_fr_domestic_vat

    @property
    def net_oss_by_country(self) -> Dict[str, Decimal]:
        """TVA OSS nette par pays (ventes - remboursements)."""
        all_countries = set(self.oss_by_country) | set(self.refund_oss_by_country)
        return {
            c: self.oss_by_country.get(c, _ZERO) + self.refund_oss_by_country.get(c, _ZERO)
            for c in sorted(all_countries)
        }

    @property
    def net_oss_total(self) -> Decimal:
        return sum(self.net_oss_by_country.values(), _ZERO)

    @property
    def total_you_owe(self) -> Decimal:
        """TVA totale nette a reverser (ventes - remboursements)."""
        return self.net_fr_domestic_vat + self.net_oss_total + self.net_local_total + self.ioss_vat

    @property
    def net_ht_by_bucket(self) -> Dict[str, Decimal]:
        """CA HT net (ventes - remboursements) par seau de traitement fiscal."""
        all_buckets = set(self.ht_by_bucket) | set(self.refund_ht_by_bucket)
        return {
            b: self.ht_by_bucket.get(b, _ZERO) + self.refund_ht_by_bucket.get(b, _ZERO)
            for b in sorted(all_buckets)
        }

    @property
    def net_ht_total(self) -> Decimal:
        """CA HT net total, recalculé à partir des seaux (doit égaler
        total_ht + refund_total_ht — sert de test d'intégrité)."""
        return sum(self.net_ht_by_bucket.values(), _ZERO)


def _bucket_label(r: "VatResult") -> str:
    """Classe un VatResult dans un seau HT exhaustif et mutuellement exclusif.

    L'ordre des tests reflète l'ordre de priorité utilisé par compute_vat()
    dans engine.py — à maintenir synchronisé si de nouveaux scenarios/canaux
    y sont ajoutés.
    """
    if r.channel == Channel.IOSS:
        return _("bucket_ioss_vendeur")
    if r.collector == Collector.AMAZON:
        return _("bucket_deemed_supplier")
    if r.scenario == Scenario.B2B_REVERSE_CHARGE:
        return _("bucket_b2b_exempt")
    # Autoliquidation nationale B2B domestique hors FR (engine.py ~L239-257) :
    # vente B2B entre assujettis dans un même pays UE (hors FR) relevant
    # d'un régime national de reverse charge. Distinct de B2B_REVERSE_CHARGE
    # (qui ne couvre que l'intracommunautaire cross-border) : ici
    # scenario=DOMESTIC mais collector=BUYER / channel=EXONERATION, donc ce cas
    # échapperait aux tests channel FR_DOMESTIC/LOCAL_REGISTRATION plus bas
    # sans ce test explicite.
    if r.scenario == Scenario.DOMESTIC and r.collector == Collector.BUYER:
        return _("bucket_reverse_charge_nat")
    if r.channel == Channel.OSS:
        return _("bucket_oss")
    if r.channel == Channel.FR_DOMESTIC:
        return _("bucket_fr_domestic")
    if r.channel == Channel.LOCAL_REGISTRATION:
        return _("bucket_local_registration")
    if r.scenario == Scenario.EXPORT:
        return _("bucket_export")
    if r.scenario == Scenario.IMPORT_STANDARD:
        return _("bucket_import")
    return _("bucket_other")


def _aggregate_result(summary: ReportSummary, r: "VatResult", is_refund: bool = False) -> None:
    """Ventile un VatResult dans le bon canal du summary."""
    ht = r.sale.amount_ht  # déjà négatif pour les remboursements

    bucket = _bucket_label(r)
    target = summary.refund_ht_by_bucket if is_refund else summary.ht_by_bucket
    target[bucket] = target.get(bucket, _ZERO) + ht

    if is_refund:
        summary.refund_total_ht += ht
        summary.refund_count += 1
        if r.channel == Channel.FR_DOMESTIC:
            summary.refund_fr_domestic_vat += r.vat_amount
        elif r.channel == Channel.OSS:
            summary.refund_oss_by_country[r.vat_country] = (
                    summary.refund_oss_by_country.get(r.vat_country, _ZERO) + r.vat_amount
            )
        elif r.channel == Channel.LOCAL_REGISTRATION:
            summary.refund_local_by_country[r.vat_country] = (
                    summary.refund_local_by_country.get(r.vat_country, _ZERO) + r.vat_amount
            )
        if r.collector == Collector.AMAZON:
            summary.refund_amazon_vat += r.vat_amount
    else:
        summary.total_ht += ht
        stock = r.sale.stock_country
        # Un stockage hors du pays d'origine (établissement) crée une obligation d'immatriculation.
        if is_eu(stock) and stock != r.sale.seller_country:
            summary.stock_countries_requiring_registration.add(stock)

        if r.channel == Channel.FR_DOMESTIC:
            summary.fr_domestic_vat += r.vat_amount
        elif r.channel == Channel.OSS:
            summary.oss_by_country[r.vat_country] = (
                    summary.oss_by_country.get(r.vat_country, _ZERO) + r.vat_amount
            )
        elif r.channel == Channel.LOCAL_REGISTRATION:
            summary.local_by_country[r.vat_country] = (
                    summary.local_by_country.get(r.vat_country, _ZERO) + r.vat_amount
            )
        elif r.channel == Channel.IOSS:
            summary.ioss_vat += r.vat_amount
        if r.collector == Collector.AMAZON:
            summary.amazon_vat += r.vat_amount
        if r.scenario == Scenario.B2B_REVERSE_CHARGE:
            summary.reverse_charge_ht += ht
        if r.scenario == Scenario.EXPORT:
            summary.export_ht += ht
        if r.scenario == Scenario.IMPORT_STANDARD:
            summary.import_vat += r.vat_amount


def build_report(
        results: List[VatResult],
        refund_results: Optional[List[VatResult]] = None,
) -> ReportSummary:
    """Agrege une liste de resultats en une synthese.

    Args:
        results: VatResult des ventes normales.
        refund_results: VatResult des remboursements (montants négatifs).
            Si fourni, ils sont ventilés dans les champs refund_* et déduits
            des totaux nets.
    """
    summary = ReportSummary()
    for r in results:
        _aggregate_result(summary, r, is_refund=False)
    if refund_results:
        for r in refund_results:
            _aggregate_result(summary, r, is_refund=True)
    return summary


def _fmt(amount: Decimal, symbol="EUR") -> str:
    return f"{amount:,.2f} {symbol}".replace(",", " ")


def render_report(summary: ReportSummary, seller_country: str = "FR") -> str:
    """Rendu texte lisible du recapitulatif.

    Les montants sont calcules en EUR par le moteur fiscal. Ils sont convertis
    ici pour affichage dans la devise locale du pays d'origine du compte
    (COUNTRY_CURRENCIES), au taux BCE du jour de generation du rapport — a
    titre indicatif uniquement : le montant legalement du reste celui calcule
    en EUR (CA3/OSS restent legalement en EUR par reglementation). En cas
    d'indisponibilite du taux BCE, repli silencieux sur le montant EUR.
    """
    from .rates import COUNTRY_CURRENCIES
    from . import ecb_rates
    from datetime import date as _date

    currency = COUNTRY_CURRENCIES.get((seller_country or "FR").upper(), "EUR")
    conv_date = _date.today()

    def _conv(amount: Decimal) -> Decimal:
        if not currency or currency == "EUR":
            return amount
        try:
            converted, _rate, _info = ecb_rates.convert_to_currency(
                amount, "EUR", currency, conv_date,
            )
            return converted
        except Exception:
            return amount

    def _f(amount: Decimal) -> str:
        return _fmt(_conv(amount), currency)

    lines: List[str] = []
    lines.append("=" * 64)
    lines.append("RECAPITULATIF TVA INTRACOMMUNAUTAIRE")
    lines.append("=" * 64)
    if currency != "EUR":
        lines.append(
            f"(Montants convertis en {currency} au taux BCE du {conv_date.isoformat()} "
            f"— indicatif, le montant legalement du reste celui calcule en EUR)"
        )
    lines.append(f"Chiffre d'affaires HT total (ventes) : {_f(summary.total_ht)}")
    if summary.refund_count:
        lines.append(f"Remboursements HT ({summary.refund_count} lignes) : {_f(summary.refund_total_ht)}")
        lines.append(f"CA HT net : {_f(summary.total_ht + summary.refund_total_ht)}")
    lines.append("")

    lines.append("--- Ce que VOUS devez reverser (net remboursements) ---")

    home_label = "Fisc francais - TVA domestique (CA3)" if seller_country == "FR" else f"Fisc {seller_country} - TVA domestique"
    lines.append(f"{home_label} : {_f(summary.net_fr_domestic_vat)}")

    if summary.refund_fr_domestic_vat:
        lines.append(f"    dont remboursements : {_f(summary.refund_fr_domestic_vat)}")

    lines.append(
        f"Fisc {seller_country if seller_country != 'FR' else 'francais'} - via guichet OSS (TVA pays destination) : "
        f"{_f(summary.net_oss_total)}"
    )
    for country in sorted(summary.net_oss_by_country):
        net = summary.net_oss_by_country[country]
        refund = summary.refund_oss_by_country.get(country, _ZERO)
        suffix = f" (dont remboursements : {_f(refund)})" if refund else ""
        lines.append(f"    dont {country} : {_f(net)}{suffix}")

    if summary.ioss_vat:
        lines.append(f"Fisc francais - via guichet IOSS (Import vendeur) : {_f(summary.ioss_vat)}")

    if summary.net_local_by_country:
        lines.append("Fisc locaux - immatriculation TVA requise :")
        for country in sorted(summary.net_local_by_country):
            lines.append(
                f"    {country} : {_f(summary.net_local_by_country[country])}"
            )
    else:
        lines.append("Fisc locaux - immatriculation TVA requise : aucune")

    lines.append(f"=> Total TVA nette a reverser par vous : {_f(summary.total_you_owe)}")
    lines.append("")

    lines.append("--- Gere par des tiers / sans reversement de votre part ---")
    lines.append(f"TVA collectee et reversee par Amazon (deemed supplier) : "
                 f"{_f(summary.amazon_vat + summary.refund_amazon_vat)}")
    lines.append(f"TVA d'importation (due en douane par l'importateur) : "
                 f"{_f(summary.import_vat)}")
    lines.append(f"Ventes B2B exonerees (autoliquidation, HT) : "
                 f"{_f(summary.reverse_charge_ht)}")
    lines.append(f"Exportations hors UE (exonerees, HT) : "
                 f"{_f(summary.export_ht)}")
    lines.append("")

    lines.append("--- Obligations d'immatriculation (stock FBA - Cas 4) ---")
    countries = sorted(summary.stock_countries_requiring_registration)
    if countries:
        lines.append(
            "Numero de TVA local requis dans : " + ", ".join(countries)
        )
        lines.append(
            "(le stockage de vos biens dans ces pays cree une obligation "
            "d'immatriculation, independamment de l'OSS)."
        )
    else:
        lines.append("Aucune (stock uniquement en France).")
    lines.append("=" * 64)

    return "\n".join(lines)