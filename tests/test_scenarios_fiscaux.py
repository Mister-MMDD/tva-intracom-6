"""Tests couvrant des scénarios fiscaux spécifiques.

- Monaco (assimilé FR)
- B2B avec autoliquidation domestique (Art. 194)
- Seuil OSS (pile 10 000€, multi-années)
- Retours de marchandises (avoirs) sur périodes glissantes
"""

from __future__ import annotations

from decimal import Decimal

from tva_intracom.engine import compute_vat, compute_all_with_vies
from tva_intracom.models import Sale, BuyerType, Scenario, Collector, Channel
from tva_intracom.oss_export import aggregate_oss_results
from tva_intracom.ca3_report import compute_ca3_lines_v2
from tva_intracom.rates import fiscal_equivalent_country


def make_sale(**kwargs) -> Sale:
    """Helper pour créer une vente de test."""
    defaults = dict(
        sale_id="TEST-001",
        amount_ht=Decimal("100.00"),
        buyer_type=BuyerType.B2C,
        stock_country="FR",
        buyer_country="DE",
        seller_country="FR",
        buyer_vat_valid=False,
        buyer_vat_number="",
        transaction_date="2024-01-15",
        product_category="STANDARD",
    )
    defaults.update(kwargs)
    return Sale(**defaults)

def test_monaco_assimilated_to_fr():
    """Vérifie que Monaco est traité comme la France (TVA FR collectée)."""
    # Cas nominal : stock FR -> buyer MC
    sale = make_sale(stock_country="FR", buyer_country="MC")
    res = compute_vat(sale)
    
    assert res.vat_country == "FR"
    assert res.scenario == Scenario.DOMESTIC
    assert res.collector == Collector.SELLER
    assert res.vat_rate == Decimal("20")
    assert "Monaco" in res.note

    # Cas cross-border : DE -> MC. Monaco = France fiscale -> OSS vers FR.
    sale_de = make_sale(stock_country="DE", buyer_country="MC")
    res_de = compute_vat(sale_de)
    assert res_de.scenario == Scenario.OSS_B2C
    assert res_de.vat_country == "FR"


def test_fiscal_equivalent_country_helper():
    """Le helper introduit le 2026-08-26 ne doit normaliser QUE Monaco -> FR,
    et laisser tout autre code pays inchangé (y compris en minuscules)."""
    assert fiscal_equivalent_country("MC") == "FR"
    assert fiscal_equivalent_country("mc") == "FR"
    assert fiscal_equivalent_country("FR") == "FR"
    assert fiscal_equivalent_country("DE") == "DE"
    assert fiscal_equivalent_country("XI") == "XI"


def test_monaco_stock_to_fr_is_domestic_not_oss():
    """CORRECTIF 2026-08-26 : cas symétrique du stock physiquement à Monaco.

    Avant ce correctif, stock_country="MC" + buyer_country="FR" tombait à
    tort en Scenario.OSS_B2C (comparaison stock_country == buyer_country
    échouant sur "MC" != "FR"), alors que Monaco est fiscalement la France
    (convention franco-monégasque du 18 mai 1963) et qu'une vente vers la
    France depuis un stock à Monaco doit être une vente domestique française
    classique, symétrique du cas stock=FR/buyer=MC déjà couvert ci-dessus.
    """
    sale = make_sale(stock_country="MC", buyer_country="FR", seller_country="FR")
    res = compute_vat(sale)

    assert res.scenario == Scenario.DOMESTIC
    assert res.vat_country == "FR"
    assert res.channel == Channel.FR_DOMESTIC
    assert res.collector == Collector.SELLER
    assert res.vat_rate == Decimal("20")
    assert res.vat_amount == Decimal("20.00")
    assert "Monaco" in res.note


def test_monaco_stock_cross_border_oss():
    """Stock à Monaco vers un autre pays UE (DE) : OSS classique vers ce pays,
    comme si le stock était en France (Monaco = France fiscale)."""
    sale = make_sale(stock_country="MC", buyer_country="DE", seller_country="FR")
    res = compute_vat(sale)

    assert res.scenario == Scenario.OSS_B2C
    assert res.vat_country == "DE"
    assert res.vat_rate == Decimal("19")


def test_monaco_stock_oss_aggregation_departure_is_fr_not_mc():
    """CORRECTIF 2026-08-26 : la clé de regroupement "pays de départ" utilisée
    par aggregate_oss_results() (et consommée telle quelle par oss_xml.py pour
    le <MemberStateOfSupply> du XML officiel) ne doit JAMAIS être "MC" — Monaco
    n'étant pas un État membre UE, ce code y serait invalide. Avant le
    correctif, res.sale.stock_country == "MC" fuyait tel quel dans cette clé.
    """
    sale = make_sale(stock_country="MC", buyer_country="DE", seller_country="FR")
    res = compute_vat(sale)

    aggregated = aggregate_oss_results([res])

    assert "MC" not in aggregated
    assert "FR" in aggregated
    assert "DE" in aggregated["FR"]


def test_monaco_stock_counts_as_national_for_ca3_and_oss_threshold():
    """CORRECTIF 2026-08-26 : les filtres CA3 comparant stock_country ==
    seller_country (vente domestique locale, seuil OSS national — case
    0038/A1 du Cerfa 3310-CA3-SD) doivent considérer un stock à Monaco comme
    "chez le vendeur" quand celui-ci est établi en France, exactement comme
    un stock physiquement en France.
    """
    sale = make_sale(stock_country="MC", buyer_country="FR", seller_country="FR")
    res = compute_vat(sale)
    assert res.channel == Channel.FR_DOMESTIC  # pré-requis du test suivant

    lines = compute_ca3_lines_v2([res], seller_country="FR")

    # La vente doit être comptée en A1 (domestique FR), pas disparaître
    # ni être comptée à tort dans une case OSS.
    assert lines["A1_base_ht"] == Decimal("100.00")
    assert lines["L08_tva_due"] == Decimal("20.00")


def test_b2b_art_194_reverse_charge():
    """Vérifie l'autoliquidation nationale (Art. 194) dans les pays l'ayant adoptée."""
    # Espagne (ES) a adopté l'Art. 194 pour les non-établis
    # Une vente stock ES -> client B2B ES doit être en autoliquidation (TVA 0% pour le vendeur)
    sale_es = make_sale(stock_country="ES", buyer_country="ES", buyer_type=BuyerType.B2B, buyer_vat_number="ESB12345678")
    res_es = compute_vat(sale_es)
    
    assert res_es.vat_amount == Decimal("0")
    assert res_es.collector == Collector.BUYER
    assert "autoliquidation nationale" in res_es.note

    # France (FR) n'a PAS adopté l'Art. 194 de la même manière (TVA toujours due par le vendeur sur ventes domestiques)
    sale_fr = make_sale(stock_country="FR", buyer_country="FR", buyer_type=BuyerType.B2B, buyer_vat_number="FR12345678901")
    res_fr = compute_vat(sale_fr)
    assert res_fr.vat_amount > 0
    assert res_fr.collector == Collector.SELLER

def test_oss_threshold_exactly_10000():
    """Vérifie le comportement quand on atteint pile 10 000€ de CA OSS."""
    sales = [
        make_sale(sale_id="S1", amount_ht=Decimal("9000.00"), buyer_country="DE", transaction_date="2024-01-01"),
        make_sale(sale_id="S2", amount_ht=Decimal("1000.00"), buyer_country="IT", transaction_date="2024-01-02"),
        make_sale(sale_id="S3", amount_ht=Decimal("0.01"), buyer_country="ES", transaction_date="2024-01-03"),
    ]
    
    # Sans option "apply_fr_under_threshold", tout est en OSS destination
    res_no_opt, _refund_no_opt, _vies_no_opt, summary_no_opt = compute_all_with_vies(
        sales, scope_id="test", apply_fr_under_threshold=False)
    assert summary_no_opt.is_threshold_exceeded is True # 10000.01 > 10000
    assert res_no_opt[0].vat_country == "DE"
    assert res_no_opt[1].vat_country == "IT"
    assert res_no_opt[2].vat_country == "ES"

    # Avec option "apply_fr_under_threshold"
    res_opt, _refund_opt, _vies_opt, summary_opt = compute_all_with_vies(
        sales, scope_id="test", apply_fr_under_threshold=True)
    # S1 (9000) <= 10000 -> FR
    assert res_opt[0].vat_country == "FR"
    # S2 (9000+1000=10000) <= 10000 -> FR
    assert res_opt[1].vat_country == "FR"
    # S3 (10000.01) > 10000 -> ES (destination)
    assert res_opt[2].vat_country == "ES"
    assert summary_opt.is_threshold_exceeded is True

def test_oss_threshold_multi_year():
    """Vérifie que le seuil OSS est remis à zéro chaque année."""
    sales = [
        # Année 2023 : 9000€ (sous le seuil)
        make_sale(sale_id="2023-1", amount_ht=Decimal("9000.00"), buyer_country="DE", transaction_date="2023-12-31"),
        # Année 2024 : 2000€ (sous le seuil, car reset)
        make_sale(sale_id="2024-1", amount_ht=Decimal("2000.00"), buyer_country="IT", transaction_date="2024-01-01"),
    ]
    
    res, _refund_res, _vies_summary, summary = compute_all_with_vies(
        sales, scope_id="test", apply_fr_under_threshold=True)
    
    assert summary.is_threshold_exceeded is False
    assert res[0].vat_country == "FR"
    assert res[1].vat_country == "FR"
    assert summary.oss_ht_by_year["2023"] == Decimal("9000.00")
    assert summary.oss_ht_by_year["2024"] == Decimal("2000.00")

def test_oss_returns_impact():
    """Vérifie que les avoirs (remboursements) réduisent le cumul OSS."""
    sales = [
        make_sale(sale_id="S1", amount_ht=Decimal("9500.00"), buyer_country="DE", transaction_date="2024-01-01"),
        # Avoir de 1000€ -> cumul tombe à 8500€
    ]
    refunds = [
        make_sale(sale_id="R1", amount_ht=Decimal("-1000.00"), buyer_country="DE", transaction_date="2024-01-02"),
    ]
    # Vente suivante de 1000€ -> cumul remonte à 9500€ (toujours sous le seuil)
    sales.append(make_sale(sale_id="S2", amount_ht=Decimal("1000.00"), buyer_country="IT", transaction_date="2024-01-03"))
    
    res, _refund_res, _vies_summary, summary = compute_all_with_vies(
        sales, scope_id="test", refunds=refunds, apply_fr_under_threshold=True)
    
    assert summary.is_threshold_exceeded is False
    assert summary.total_oss_ht == Decimal("9500") # 9500 - 1000 + 1000
    assert res[0].vat_country == "FR"
    assert res[1].vat_country == "FR"

def test_oss_returns_different_years():
    """Vérifie qu'un avoir en N+1 ne réduit pas le cumul de l'année N."""
    sales = [
        make_sale(sale_id="S1", amount_ht=Decimal("12000.00"), buyer_country="DE", transaction_date="2023-12-31"),
    ]
    refunds = [
        # Avoir en 2024 pour une vente de 2023
        make_sale(sale_id="R1", amount_ht=Decimal("-5000.00"), buyer_country="DE", transaction_date="2024-01-01"),
    ]
    
    res, _refund_res, _vies_summary, summary = compute_all_with_vies(
        sales, scope_id="test", refunds=refunds, apply_fr_under_threshold=True)
    
    # En 2023, le seuil a été dépassé (12000 > 10000)
    assert summary.oss_ht_by_year["2023"] == Decimal("12000.00")
    # En 2024, le cumul commence à -5000 (ou 0 si on considère que les avoirs ne peuvent pas rendre le cumul négatif ? 
    # Le moteur actuel fait juste l'addition algébrique)
    assert summary.oss_ht_by_year["2024"] == Decimal("-5000.00")
    assert summary.is_threshold_exceeded is True # Car 2023 a dépassé
