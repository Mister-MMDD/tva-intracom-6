"""Tests de non-régression pour la série de correctifs du 2026-09-09
(voir README - evolution.md, entrée du même jour).

Un test par bug rapporté par Matthieu, avant/après quand c'est pertinent.
Ne couvre pas 2 (cache post-paiement) et 8b (jeton Stripe) : ce sont des
effets de bord sur des dépendances Streamlit/cookies difficiles à isoler
sans un vrai run Streamlit — vérifiés manuellement (voir README - evolution.md).
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from tva_intracom import BuyerType, Sale, Scenario, Channel, compute_all_with_vies
from tva_intracom.vies_engine import _clean_vat_number
from tva_intracom.ecb_rates import get_ioss_rate_date, get_oss_rate_date, month_end_date
from tva_intracom.ui.tabs.declarations import _aggregate_declarations_raw


# ---------------------------------------------------------------------------
# Bug 1 : ventes B2B locales "invisibles"
# ---------------------------------------------------------------------------

def test_b2b_domestic_reverse_charge_uses_local_registration_channel():
    """Stock IT -> client B2B italien (VIES valide) : autoliquidation
    nationale italienne. Doit rester channel=LOCAL_REGISTRATION (visible
    dans local_vat_report.py), plus jamais EXONERATION (invisible)."""
    sale = Sale(
        "s1", Decimal("500"), BuyerType.B2B,
        stock_country="IT", buyer_country="IT",
        buyer_vat_number="IT12345678901", buyer_vat_valid=True,
    )
    results = compute_all_with_vies([sale], scope_id="test-b2b-local")[0]
    assert len(results) == 1
    r = results[0]
    assert r.scenario == Scenario.DOMESTIC
    assert r.channel == Channel.LOCAL_REGISTRATION
    assert r.channel != Channel.EXONERATION
    assert r.vat_amount == Decimal("0.00")  # autoliquidation : TVA non collectée par le vendeur


# ---------------------------------------------------------------------------
# Bug 3 : numéros de TVA avec parenthèses
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("raw,expected_country,expected_number", [
    ("(FR)123456789", "FR", "123456789"),
    ("FR 12 345 678 9", "FR", "123456789"),
    ("DE-123456789", "DE", "123456789"),
])
def test_clean_vat_number_strips_parentheses(raw, expected_country, expected_number):
    country, number = _clean_vat_number(raw)
    assert country == expected_country
    assert number == expected_number


# ---------------------------------------------------------------------------
# Bug 5 : double comptage DDP vers le pays d'origine
# ---------------------------------------------------------------------------

def test_ddp_to_home_country_not_double_counted_in_dashboard():
    """Vente DDP (vendeur importateur officiel) vers son propre pays
    d'origine : déjà comptée dans home_ht_brut (channel=FR_DOMESTIC), ne
    doit PAS réapparaître une seconde fois dans ddp_agg."""
    sale = Sale(
        "s1", Decimal("1000"), BuyerType.B2C,
        stock_country="CN", buyer_country="FR",
        seller_is_importer=True, seller_country="FR",
    )
    results = compute_all_with_vies([sale], scope_id="test-ddp")[0]
    raw = _aggregate_declarations_raw(results, [], "test-ddp-calc-key")
    ddp_agg = raw["ddp_agg"]
    assert "FR" not in ddp_agg, (
        "La vente DDP vers le pays d'origine (FR) ne doit pas apparaître "
        "dans ddp_agg : elle est déjà comptée dans home_ht_brut."
    )


# ---------------------------------------------------------------------------
# Bug 6 : taux de change OSS (vendredi/lundi) et IOSS (mois/trimestre)
# ---------------------------------------------------------------------------

def test_month_end_date_parses_monthly_ioss_period():
    assert month_end_date("2026-03") == date(2026, 3, 31)
    assert month_end_date("2026-M03") == date(2026, 3, 31)
    assert month_end_date("2026-12") == date(2026, 12, 31)
    assert month_end_date("2026-Q1") is None  # format trimestriel, pas géré ici


def test_get_ioss_rate_date_uses_month_end_not_quarter_end():
    """BUGFIX : avant ce correctif, une période IOSS mensuelle non reconnue
    par quarter_end_date() retombait sur la fin du TRIMESTRE de la
    transaction. Doit désormais utiliser la fin du MOIS."""
    ioss_date = get_ioss_rate_date("2026-01", transaction_date=date(2026, 1, 15))
    assert ioss_date == date(2026, 1, 31)
    # Le comportement OSS (trimestriel) doit rester inchangé pour la même transaction.
    oss_date = get_oss_rate_date("2026-Q1", transaction_date=date(2026, 1, 15))
    assert oss_date == date(2026, 3, 31)
    assert ioss_date != oss_date


# ---------------------------------------------------------------------------
# Bug 7 : purge SIREN piégée dans le cache
# ---------------------------------------------------------------------------

def test_purge_expired_siren_removals_returns_deleted_count(monkeypatch):
    """_purge_expired_siren_removals doit désormais retourner le nombre de
    lignes supprimées (utilisé par can_register_new_siren pour décider
    d'invalider list_registered_sirens)."""
    from tva_intracom import billing

    captured = {}

    def _fake_run(fn):
        class _FakeCursor:
            rowcount = 2
            def execute(self, *a, **k):
                pass
        class _FakeConn:
            def commit(self):
                captured["committed"] = True
        return fn(_FakeConn(), _FakeCursor())

    monkeypatch.setattr(billing, "_run", _fake_run)
    deleted = billing._purge_expired_siren_removals("org-1")
    assert deleted == 2
    assert captured.get("committed") is True


def test_can_register_new_siren_survives_purge_failure(monkeypatch):
    """Une erreur DB pendant la purge (best-effort) ne doit jamais empêcher
    can_register_new_siren de statuer sur le quota."""
    from tva_intracom import billing

    def _boom(org_id):
        raise RuntimeError("DB indisponible")

    monkeypatch.setattr(billing, "_purge_expired_siren_removals", _boom)
    monkeypatch.setattr(billing, "get_siren_quota_status",
                         lambda org_id: billing.SirenQuotaStatus(registered_count=1, quota=3, over_quota_by=0))
    allowed, msg = billing.can_register_new_siren("org-1")
    assert allowed is True
    assert msg == ""


# ---------------------------------------------------------------------------
# Bug 8 : XSS company_name dans les rapports HTML
# ---------------------------------------------------------------------------

def test_local_vat_html_report_escapes_company_name():
    from tva_intracom.local_vat_report import generate_local_vat_html_report

    html_out = generate_local_vat_html_report(
        results=[], refund_results=[], vat_country="IT",
        company_name="<script>alert(1)</script>", siren="111222333",
        period_label="2026-Q1",
    )
    assert "<script>alert(1)</script>" not in html_out
    assert "&lt;script&gt;" in html_out


def test_ca3_html_report_escapes_company_name():
    from tva_intracom.ca3_report import generate_ca3_html_report_v2

    html_out = generate_ca3_html_report_v2(
        results=[], company_name="<script>alert(1)</script>", siren="111222333",
        period_label="2026-Q1",
    )
    assert "<script>alert(1)</script>" not in html_out
    assert "&lt;script&gt;" in html_out
