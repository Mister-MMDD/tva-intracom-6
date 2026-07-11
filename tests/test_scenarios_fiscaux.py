"""Tests couvrant des scénarios fiscaux spécifiques.

- Monaco (assimilé FR)
- B2B avec autoliquidation domestique (Art. 194)
- Seuil OSS (pile 10 000€, multi-années)
- Retours de marchandises (avoirs) sur périodes glissantes
"""

from __future__ import annotations
from decimal import Decimal
from tva_intracom.engine import compute_vat, compute_all
from tva_intracom.models import Sale, BuyerType, Scenario, Collector

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

    # Cas cross-border (non couvert par la convention bilatérale FR-MC dans le moteur)
    # ex: DE -> MC. Devrait tomber en EXPORT ou rester standard ? 
    # Le moteur actuel dit : reste EXPORT si non FR->MC.
    sale_de = make_sale(stock_country="DE", buyer_country="MC")
    res_de = compute_vat(sale_de)
    assert res_de.scenario == Scenario.EXPORT

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
    res_no_opt, summary_no_opt = compute_all(sales, apply_fr_under_threshold=False)
    assert summary_no_opt.is_threshold_exceeded is True # 10000.01 > 10000
    assert res_no_opt[0].vat_country == "DE"
    assert res_no_opt[1].vat_country == "IT"
    assert res_no_opt[2].vat_country == "ES"

    # Avec option "apply_fr_under_threshold"
    res_opt, summary_opt = compute_all(sales, apply_fr_under_threshold=True)
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
    
    res, summary = compute_all(sales, apply_fr_under_threshold=True)
    
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
    
    res, summary = compute_all(sales, refunds=refunds, apply_fr_under_threshold=True)
    
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
    
    res, summary = compute_all(sales, refunds=refunds, apply_fr_under_threshold=True)
    
    # En 2023, le seuil a été dépassé (12000 > 10000)
    assert summary.oss_ht_by_year["2023"] == Decimal("12000.00")
    # En 2024, le cumul commence à -5000 (ou 0 si on considère que les avoirs ne peuvent pas rendre le cumul négatif ? 
    # Le moteur actuel fait juste l'addition algébrique)
    assert summary.oss_ht_by_year["2024"] == Decimal("-5000.00")
    assert summary.is_threshold_exceeded is True # Car 2023 a dépassé
