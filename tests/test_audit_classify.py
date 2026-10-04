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
