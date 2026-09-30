from decimal import Decimal
from unittest.mock import patch

from tva_intracom.local_vat_report import compute_local_vat_lines


def test_local_report_accepts_all_aic_helper_values():
    with patch(
        "tva_intracom.ca3_report._compute_aic_from_fc_transfers",
        return_value=(Decimal("100"), Decimal("20"), 0, 0),
    ):
        lines = compute_local_vat_lines(
            [], [], "DE", all_fc_transfers=[{"ARRIVAL_COUNTRY": "DE"}]
        )

    assert lines["aic_base_ht"] == Decimal("100.00")
    assert lines["aic_vat"] == Decimal("20.00")
    assert lines["total_base_net_avec_aic"] == Decimal("100.00")
    assert lines["total_tva_net_avec_aic"] == Decimal("20.00")
