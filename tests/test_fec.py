"""Tests pour fec_export.py — en particulier le correctif du 2026-08-27
sur l'équilibrage débit/crédit quand net_ht et net_vat ont des signes
opposés au sein d'un même bucket d'agrégation."""

from decimal import Decimal

import pytest

from tva_intracom.fec_export import build_fec_rows, _assert_balanced
from tva_intracom.models import BuyerType, Collector, Channel, Sale, Scenario, VatResult


def _make_result(sale_id: str, amount_ht: Decimal, vat_amount: Decimal,
                  vat_rate: Decimal = Decimal("20.00"), vat_country: str = "FR") -> VatResult:
    sale = Sale(
        sale_id=sale_id,
        amount_ht=amount_ht,
        buyer_type=BuyerType.B2C,
        stock_country="FR",
        buyer_country="FR",
    )
    return VatResult(
        sale=sale,
        scenario=Scenario.DOMESTIC,
        vat_country=vat_country,
        vat_rate=vat_rate,
        vat_amount=vat_amount,
        collector=Collector.SELLER,
        channel=Channel.FR_DOMESTIC,
        note="",
    )


def _debit_credit_totals(rows: list[list[str]]) -> tuple[Decimal, Decimal]:
    debit_total = sum(Decimal(r[11]) for r in rows)
    credit_total = sum(Decimal(r[12]) for r in rows)
    return debit_total, credit_total


def test_build_fec_rows_balanced_normal_case():
    """Cas normal (ventes positives) : toujours équilibré."""
    results = [_make_result("S1", Decimal("100.00"), Decimal("20.00"))]
    rows = build_fec_rows(results, period="2026-Q2", ecriture_date="20260630")
    debit_total, credit_total = _debit_credit_totals(rows)
    assert debit_total == credit_total


def test_build_fec_rows_balanced_full_refund_bucket():
    """Bucket où avoirs > ventes (net_ht et net_vat négatifs ensemble) :
    toujours équilibré, sens inversé (client crédité)."""
    results = [
        _make_result("S1", Decimal("100.00"), Decimal("20.00")),
        _make_result("R1", Decimal("-150.00"), Decimal("-30.00")),
    ]
    rows = build_fec_rows(results, period="2026-Q2", ecriture_date="20260630")
    debit_total, credit_total = _debit_credit_totals(rows)
    assert debit_total == credit_total


def test_build_fec_rows_no_vat_line_when_individual_vat_amount_negative():
    """Un VatResult avec vat_amount <= 0 (cas dégénéré, ne devrait pas
    survenir avec un vrai moteur puisque vat_amount = round(amount_ht *
    rate/100) et amount_ht > 0 pour une vente) n'obtient aucun compte de
    TVA (_vat_account_for retourne "") : il est donc filtré de la ligne
    TVA plutôt que de risquer de fausser le signe agrégé du bucket. Le
    montant HT associé n'en est pas moins comptabilisé et l'écriture reste
    équilibrée (via le compte client uniquement pour cette ligne)."""
    results = [
        _make_result("S1", Decimal("50.00"), Decimal("-0.01")),
    ]
    rows = build_fec_rows(results, period="2026-Q2", ecriture_date="20260630")
    debit_total, credit_total = _debit_credit_totals(rows)
    assert debit_total == credit_total
    assert not any(r[4] == "4457100" for r in rows)  # aucune ligne TVA générée
    sale_line = next(r for r in rows if r[4] == "7071000")
    assert Decimal(sale_line[12]) == Decimal("50.00") and Decimal(sale_line[11]) == Decimal("0.00")


def test_assert_balanced_raises_on_mismatch():
    """Garde-fou testé directement (indépendamment de tout scénario réel
    d'agrégation) : si Débit != Crédit, une erreur explicite doit être levée
    — jamais un FEC invalide silencieux. Note : avec la logique corrigée de
    build_fec_rows, ce cas est algébriquement impossible à produire depuis
    des VatResult réels (l'équilibrage tient par construction, quel que
    soit le signe respectif de net_ht et net_vat) ; ce garde-fou reste une
    protection défensive contre une régression future qui romprait cette
    propriété, d'où ce test direct de la fonction plutôt qu'un scénario
    d'entrée artificiellement cassé."""
    with pytest.raises(RuntimeError, match="FEC déséquilibré"):
        _assert_balanced(
            debit_total=Decimal("100.00"),
            credit_total=Decimal("100.01"),
            ecriture_num="1",
            scenario_value="DOMESTIC",
            vat_country="FR",
            vat_rate=Decimal("20.00"),
        )


def test_assert_balanced_passes_when_equal():
    """Ne lève rien quand Débit == Crédit."""
    _assert_balanced(
        debit_total=Decimal("100.00"),
        credit_total=Decimal("100.00"),
        ecriture_num="1",
        scenario_value="DOMESTIC",
        vat_country="FR",
        vat_rate=Decimal("20.00"),
    )
