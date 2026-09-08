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


def test_build_fec_rows_vat_line_present_when_individual_vat_amount_negative():
    """BUGFIX (2026-09-08) : un avoir isolé (vat_amount < 0, ex: remboursement
    d'une vente d'une période antérieure) doit obtenir le MÊME compte de TVA
    qu'une vente équivalente (_vat_account_for ne doit exclure que le montant
    exactement nul, pas tout montant <= 0). La ligne de TVA (4457100) doit
    donc apparaître, au débit puisque net_vat est négatif, pour permettre la
    récupération de TVA sur ce retour."""
    results = [
        _make_result("R1", Decimal("-50.00"), Decimal("-10.00")),
    ]
    rows = build_fec_rows(results, period="2026-Q2", ecriture_date="20260630")
    debit_total, credit_total = _debit_credit_totals(rows)
    assert debit_total == credit_total
    vat_line = next(r for r in rows if r[4] == "4457100")  # doit exister
    assert Decimal(vat_line[11]) == Decimal("10.00") and Decimal(vat_line[12]) == Decimal("0.00")
    sale_line = next(r for r in rows if r[4] == "7071000")
    assert Decimal(sale_line[11]) == Decimal("50.00") and Decimal(sale_line[12]) == Decimal("0.00")


def test_build_fec_rows_refund_nets_with_sale_in_same_bucket():
    """Une vente et un avoir du même groupe (période/scénario/pays/taux)
    doivent désormais tomber dans le MÊME bucket _AggKey (même
    channel_account) et se compenser dans une seule ligne de TVA nette,
    plutôt que l'avoir étant silencieusement exclu de toute ligne TVA."""
    results = [
        _make_result("S1", Decimal("100.00"), Decimal("20.00")),
        _make_result("R1", Decimal("-50.00"), Decimal("-10.00")),
    ]
    rows = build_fec_rows(results, period="2026-Q2", ecriture_date="20260630")
    debit_total, credit_total = _debit_credit_totals(rows)
    assert debit_total == credit_total
    # Une seule écriture (un seul bucket) puisque vente + avoir partagent
    # désormais le même channel_account.
    assert {r[2] for r in rows} == {"1"}
    vat_line = next(r for r in rows if r[4] == "4457100")
    # Net TVA = 20.00 - 10.00 = 10.00, côté crédit (net positif).
    assert Decimal(vat_line[12]) == Decimal("10.00") and Decimal(vat_line[11]) == Decimal("0.00")


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
