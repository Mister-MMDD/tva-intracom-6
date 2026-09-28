from __future__ import annotations

import csv
import io
from decimal import Decimal

from tva_intracom.ca3_edi_export import generate_ca3_edi_preparation_csv
from tva_intracom.engine import compute_vat
from tva_intracom.models import BuyerType, Sale


def _rows(data: bytes) -> list[dict[str, str]]:
    return list(csv.DictReader(io.StringIO(data.decode("utf-8-sig"), newline=""), delimiter=";"))


def test_ca3_edi_preparation_maps_supported_lines_and_rounds_tax_after_base():
    result = compute_vat(Sale(
        sale_id="CA3-1",
        amount_ht=Decimal("100.50"),
        buyer_type=BuyerType.B2C,
        stock_country="FR",
        buyer_country="FR",
        seller_country="FR",
        transaction_date="2026-01-15",
        product_category="STANDARD",
    ))

    rows = _rows(generate_ca3_edi_preparation_csv([result], "Entreprise", "123456789", "2026-01"))
    values = {(row["formulaire"], row["chemin_edifact"]): row for row in rows}

    assert values[("3310CA3", "CA:C516:5004:1")]["valeur"] == "101"
    assert values[("3310CA3", "FP:C516:5004:1")]["valeur"] == "101"
    assert values[("3310CA3", "GP:C516:5004:1")]["valeur"] == "20"
    assert values[("T-IDENTIF", "AA:C082:3039:1")]["valeur"] == "123456789"
    assert values[("T-IDENTIF", "KD:C506:1154:1")]["valeur"] == ""
    assert values[("T-IDENTIF", "CA:C507:2380:1:102")]["valeur"] == "20260101"
    assert values[("T-IDENTIF", "CB:C507:2380:1:102")]["valeur"] == "20260131"
    assert values[("T-IDENTIF", "CA:C507:2380:1:102")]["statut"] == "prérempli — à vérifier"
    assert values[("3310CA3", "HA:C516:5004:1")]["valeur"] == ""
    assert values[("3310CA3", "HA:C516:5004:1")]["statut"] == "à compléter"
    assert any(row["statut"] == "non transmissible" for row in rows)


def test_ca3_edi_preparation_does_not_export_negative_moa_or_guess_identity():
    result = compute_vat(Sale(
        sale_id="CA3-2",
        amount_ht=Decimal("-100"),
        buyer_type=BuyerType.B2C,
        stock_country="FR",
        buyer_country="FR",
        seller_country="FR",
        transaction_date="2026-01-15",
        product_category="STANDARD",
    ))

    rows = _rows(generate_ca3_edi_preparation_csv([], "=1+1", "", "2026-01", refund_results=[result]))
    values = {(row["formulaire"], row["code_donnee"], row["chemin_edifact"]): row for row in rows}

    assert values[("T-IDENTIF", "AA", "AA:C082:3039:1")]["statut"] == "à corriger"
    assert values[("T-IDENTIF", "AA", "AA:C080:3036:1")]["valeur"] == "'=1+1"
    assert values[("T-IDENTIF", "CA", "CA:C507:2380:1:102")]["valeur"] == "20260101"
    assert values[("3310CA3", "CA", "CA:C516:5004:1")]["valeur"] == ""
    assert values[("3310CA3", "CA", "CA:C516:5004:1")]["statut"] == "à vérifier"
    assert not any(row["formulaire"] == "3310CA3G" for row in rows)


def test_ca3_edi_preparation_marks_aic_estimates_and_partial_deduction():
    sale = Sale(
        sale_id="CA3-AIC",
        amount_ht=Decimal("100"),
        buyer_type=BuyerType.B2C,
        stock_country="FR",
        buyer_country="FR",
        seller_country="FR",
        transaction_date="2026-01-15",
        product_category="STANDARD",
        asin="ASIN-1",
    )
    result = compute_vat(sale)

    rows = _rows(generate_ca3_edi_preparation_csv(
        [result], "Entreprise", "123456789", "2026-01",
        all_fc_transfers=[{"DEPARTURE_COUNTRY": "DE", "ARRIVAL_COUNTRY": "FR", "ASIN": "ASIN-1", "QTY": 1}],
    ))
    values = {(row["formulaire"], row["chemin_edifact"]): row for row in rows}

    assert values[("3310CA3", "CC:C516:5004:1")]["statut"] == "estimé"
    assert values[("3310CA3", "GJ:C516:5004:1")]["valeur"] == "20"
    assert values[("3310CA3", "GJ:C516:5004:1")]["statut"] == "estimé"
    assert values[("3310CA3", "HB:C516:5004:1")]["valeur"] == "20"
    assert values[("3310CA3", "HB:C516:5004:1")]["statut"] == "partiel — AIC estimée"
    assert "20 EUR" in values[("3310CA3", "HB:C516:5004:1")]["commentaire"]


def test_ca3_edi_preparation_uses_civil_quarter_and_leaves_ambiguous_period_blank():
    quarter = _rows(generate_ca3_edi_preparation_csv([], "Entreprise", "123456789", "2026-Q1"))
    quarter_values = {row["chemin_edifact"]: row for row in quarter}
    assert quarter_values["CA:C507:2380:1:102"]["valeur"] == "20260101"
    assert quarter_values["CB:C507:2380:1:102"]["valeur"] == "20260331"

    ambiguous = _rows(generate_ca3_edi_preparation_csv([], "Entreprise", "123456789", "2026-Q1_Q2"))
    ambiguous_values = {row["chemin_edifact"]: row for row in ambiguous}
    assert ambiguous_values["CA:C507:2380:1:102"]["valeur"] == ""
    assert ambiguous_values["CB:C507:2380:1:102"]["valeur"] == ""
    assert ambiguous_values["CA:C507:2380:1:102"]["statut"] == "à compléter"


def test_ca3_edi_regime_mensuel_rejects_quarter_period():
    import pytest
    from tva_intracom.ca3_edi_export import Ca3EdiRegimeMismatchError

    with pytest.raises(Ca3EdiRegimeMismatchError):
        generate_ca3_edi_preparation_csv(
            [], "Entreprise", "123456789", "2026-Q1", regime_periodicite="mensuel",
        )


def test_ca3_edi_regime_trimestriel_rejects_month_period():
    import pytest
    from tva_intracom.ca3_edi_export import Ca3EdiRegimeMismatchError

    with pytest.raises(Ca3EdiRegimeMismatchError):
        generate_ca3_edi_preparation_csv(
            [], "Entreprise", "123456789", "2026-01", regime_periodicite="trimestriel",
        )


def test_ca3_edi_regime_coherent_or_undeclared_is_accepted_and_reported():
    rows = _rows(generate_ca3_edi_preparation_csv(
        [], "Entreprise", "123456789", "2026-01", regime_periodicite="mensuel",
    ))
    regime = [r for r in rows if r["libelle"].startswith("Régime de périodicité")]
    assert len(regime) == 1 and regime[0]["valeur"] == "mensuel"
    # Forme non reconnue (ex. plage multi-trimestres) : jamais de blocage.
    generate_ca3_edi_preparation_csv([], "E", "123456789", "2026-Q1_Q2", regime_periodicite="mensuel")
    # Non déclaré : aucun contrôle.
    generate_ca3_edi_preparation_csv([], "E", "123456789", "2026-Q1")


def test_ca3_edi_regime_invalid_value_raises_value_error():
    import pytest

    with pytest.raises(ValueError):
        generate_ca3_edi_preparation_csv([], "E", "123456789", "2026-01", regime_periodicite="annuel")
