from tva_intracom.audit_classify import is_non_eu_flow, is_vies_risk_gap


def test_non_eu_flows():
    assert is_non_eu_flow("FR", "GB") and is_non_eu_flow("GB", "FR")
    for c in ("JP", "US", "CA", "AU", "CH", "CN"):
        assert is_non_eu_flow("FR", c)
    assert not is_non_eu_flow("FR", "ES")
    assert not is_non_eu_flow("FR", "FR")


def test_vies_risk_only_when_amazon_exempted():
    assert is_vies_risk_gap(True, 0)            # Amazon a exonéré un VIES invalide
    assert not is_vies_risk_gap(True, 22.0)     # Amazon a taxé : simple écart de taux
    assert not is_vies_risk_gap(False, 0)


# ---------------------------------------------------------------------------
# Règle Art. 194 unifiée UI / Excel (2026-10-05)
# ---------------------------------------------------------------------------
from tva_intracom.audit_classify import is_domestic_reverse_charge_gap


def test_art194_cross_border_never_art194():
    # Stock DE -> client ES, moteur 0 (LIC), Amazon > 0 : écart de taux/VIES, pas art. 194
    assert is_domestic_reverse_charge_gap(False, "DE", "ES", 0.0, 21.0) is False


def test_art194_domestic_local_nif_buyer_is_art194():
    # Stock ES -> client ES (NIF local, non typé B2B), moteur 0, Amazon > 0
    assert is_domestic_reverse_charge_gap(False, "ES", "ES", 0.0, 21.0) is True


def test_art194_requires_amazon_vat_and_zero_engine_vat():
    assert is_domestic_reverse_charge_gap(False, "ES", "ES", 0.0, 0.0) is False
    assert is_domestic_reverse_charge_gap(False, "ES", "ES", 10.0, 21.0) is False


def test_art194_engine_reclassification_wins_even_cross_border_flag():
    # reclassification domestique déjà décidée par le moteur
    assert is_domestic_reverse_charge_gap(True, "ES", "ES", 5.0, 0.0) is True


# ---------------------------------------------------------------------------
# Excel : la catégorie VIES tient compte des reclassifications, comme l'UI (2026-10-05)
# ---------------------------------------------------------------------------
def _audit_tab_natures(reclassifications, affected=None):
    from decimal import Decimal
    from types import SimpleNamespace
    from openpyxl import Workbook
    from tva_intracom import excel_report as xr
    from tva_intracom.i18n import _ as tr

    sale = SimpleNamespace(
        sale_id="S1", display_id="S1", amount_ht=Decimal("100.00"),
        stock_country="DE", buyer_country="ES", amazon_vat_amount=Decimal("0"),
        transaction_date="2026-03-01", buyer_type=None, original_currency="EUR",
    )
    res = SimpleNamespace(sale=sale, vat_amount=Decimal("21.00"), vat_rate=Decimal("21"),
                          scenario=SimpleNamespace(value="X"), channel=SimpleNamespace(value="Y"),
                          vat_country="ES")
    summary = SimpleNamespace(reclassifications=reclassifications, nif_affected_sale_ids=set(),
                              vies_affected_sale_ids=set())
    wb = Workbook()
    ws = wb.active
    xr._write_audit_tab(ws, [res], affected or set(), vies_summary=summary)
    texts = {str(c.value) for row in ws.iter_rows() for c in row if c.value is not None}
    return texts, tr("xl_audit_nature_vies"), tr("xl_audit_nature_taux")


def test_excel_audit_vies_category_uses_reclassifications():
    from types import SimpleNamespace
    rc = SimpleNamespace(sale_id="S1", is_domestic_reverse_charge=False, is_national_tax_id=False)
    texts, vies_lbl, taux_lbl = _audit_tab_natures([rc])
    assert vies_lbl in texts and taux_lbl not in texts


def test_excel_audit_without_reclassification_stays_rate_gap():
    texts, vies_lbl, taux_lbl = _audit_tab_natures([])
    assert taux_lbl in texts and vies_lbl not in texts
