import pytest
from decimal import Decimal
from datetime import date
from tva_intracom.engine import compute_vat
from tva_intracom.models import Sale, BuyerType, Scenario, Collector, Channel
from tva_intracom.vies_engine import normalize_full_vat

def make_sale(**kwargs) -> Sale:
    """Helper pour créer une vente de test."""
    defaults = dict(
        sale_id="TEST-BUG",
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

# --- 1. TERRITOIRES SPÉCIAUX ---

def test_special_territory_canaries():
    """Vérifie que les Canaries (ES 35xxx) sont traitées en EXPORT."""
    # Format propre
    sale = make_sale(stock_country="FR", buyer_country="ES", arrival_post_code="35000")
    res = compute_vat(sale)
    assert res.scenario == Scenario.EXPORT
    assert "Territoire exclu" in res.note

    # Format avec espaces et préfixe pays
    sale_dirty = make_sale(stock_country="FR", buyer_country="ES", arrival_post_code=" ES 35-001 ")
    res_dirty = compute_vat(sale_dirty)
    assert res_dirty.scenario == Scenario.EXPORT

def test_special_territory_helgoland():
    """Vérifie que Heligoland (DE 27498) est traitée en EXPORT."""
    sale = make_sale(stock_country="FR", buyer_country="DE", arrival_post_code="27498")
    res = compute_vat(sale)
    assert res.scenario == Scenario.EXPORT

def test_special_territory_livigno():
    """Vérifie que Livigno (IT 23030) est traitée en EXPORT."""
    sale = make_sale(stock_country="FR", buyer_country="IT", arrival_post_code="23030")
    res = compute_vat(sale)
    assert res.scenario == Scenario.EXPORT

def test_special_territory_aland():
    """Vérifie que les îles Åland (FI 22xxx) sont traitées en EXPORT."""
    sale = make_sale(stock_country="FR", buyer_country="FI", arrival_post_code="22100")
    res = compute_vat(sale)
    assert res.scenario == Scenario.EXPORT

def test_special_territory_dom_fr():
    """Vérifie que la Guadeloupe (FR 971xx) est traitée en EXPORT."""
    sale = make_sale(stock_country="FR", buyer_country="FR", arrival_post_code="97100")
    res = compute_vat(sale)
    assert res.scenario == Scenario.EXPORT
    assert "Territoire exclu" in res.note

# --- 2. TAUX HISTORIQUES ---

def test_historical_rate_finland_2024():
    """Vérifie le passage du taux FI de 24% à 25.5% au 1er septembre 2024."""
    # Avant le changement
    sale_before = make_sale(buyer_country="FI", transaction_date="2024-08-31")
    res_before = compute_vat(sale_before)
    assert res_before.vat_rate == Decimal("24")

    # Après le changement
    sale_after = make_sale(buyer_country="FI", transaction_date="2024-09-01")
    res_after = compute_vat(sale_after)
    assert res_after.vat_rate == Decimal("25.5")

def test_historical_rate_slovakia_2025():
    """Vérifie le passage du taux SK de 20% à 23% au 1er janvier 2025."""
    sale_before = make_sale(buyer_country="SK", transaction_date="2024-12-31")
    assert compute_vat(sale_before).vat_rate == Decimal("20")

    sale_after = make_sale(buyer_country="SK", transaction_date="2025-01-01")
    assert compute_vat(sale_after).vat_rate == Decimal("23")

def test_historical_rate_estonia_2025():
    """Vérifie le passage du taux EE de 22% à 24% au 1er juillet 2025."""
    sale_before = make_sale(buyer_country="EE", transaction_date="2025-06-30")
    assert compute_vat(sale_before).vat_rate == Decimal("22")

    sale_after = make_sale(buyer_country="EE", transaction_date="2025-07-01")
    assert compute_vat(sale_after).vat_rate == Decimal("24")

# --- 3. MONACO ---

def test_monaco_cross_border():
    """Vérifie qu'une vente DE -> MC est traitée en OSS vers la France."""
    sale = make_sale(stock_country="DE", buyer_country="MC")
    res = compute_vat(sale)
    assert res.scenario == Scenario.OSS_B2C
    assert res.vat_country == "FR"
    assert res.vat_rate == Decimal("20")

def test_monaco_from_fr():
    """Vérifie qu'une vente FR -> MC est traitée en DOMESTIC FR."""
    sale = make_sale(stock_country="FR", buyer_country="MC")
    res = compute_vat(sale)
    assert res.scenario == Scenario.DOMESTIC
    assert res.vat_country == "FR"
    assert res.channel == Channel.FR_DOMESTIC

# --- 4. IOSS (SEUIL ET DEVISES) ---

def test_ioss_limit_150_eur():
    """Vérifie le seuil IOSS de 150€ HT."""
    # Pile 150€ -> IOSS (si activé)
    sale_150 = make_sale(
        stock_country="US", buyer_country="FR", 
        amount_ht=Decimal("150.00"), ioss_number="IM1234567890"
    )
    # On force ioss_own_number_active=True pour tester IOSS_DIRECT
    res_150 = compute_vat(sale_150, ioss_own_number_active=True)
    assert res_150.scenario == Scenario.IOSS_DIRECT

    # 150.01€ -> IMPORT_STANDARD (si non DDP)
    sale_150_01 = make_sale(
        stock_country="US", buyer_country="FR", 
        amount_ht=Decimal("150.01"), ioss_number="IM1234567890"
    )
    res_150_01 = compute_vat(sale_150_01, ioss_own_number_active=True)
    assert res_150_01.scenario == Scenario.IMPORT_STANDARD

def test_ioss_deemed_supplier_no_own_number():
    """Vérifie que sans option active, on reste en DEEMED_SUPPLIER même avec un n° IOSS."""
    sale = make_sale(
        stock_country="US", buyer_country="FR", 
        amount_ht=Decimal("100.00"), ioss_number="IM1234567890"
    )
    res = compute_vat(sale, ioss_own_number_active=False)
    assert res.scenario == Scenario.DEEMED_SUPPLIER
    assert res.collector == Collector.AMAZON

# --- 5. ART 194 (AUTOLIQUIDATION DOMESTIQUE) ---

def test_art_194_spain_vs_germany():
    """Vérifie que ES applique l'Art 194 (B2B domestique) mais pas DE."""
    # Espagne : Autoliquidation (BUYER)
    sale_es = make_sale(
        stock_country="ES", buyer_country="ES", 
        buyer_type=BuyerType.B2B, buyer_vat_number="ESB12345678"
    )
    res_es = compute_vat(sale_es)
    assert res_es.collector == Collector.BUYER
    assert res_es.vat_amount == 0

    # Allemagne : Pas d'autoliquidation nationale implémentée ici -> SELLER collect local VAT
    sale_de = make_sale(
        stock_country="DE", buyer_country="DE", 
        buyer_type=BuyerType.B2B, buyer_vat_number="DE123456789"
    )
    res_de = compute_vat(sale_de)
    assert res_de.collector == Collector.SELLER
    assert res_de.vat_rate == Decimal("19")

# --- 6. NORMALISATION TVA ---

def test_vat_normalization_aliases():
    """Vérifie les alias UK -> GB et EL -> GR."""
    assert normalize_full_vat("UK123456789", "GB") == "GB123456789"
    assert normalize_full_vat("EL123456789", "GR") == "GR123456789"

def test_vat_normalization_prefix_addition():
    """Vérifie l'ajout automatique du préfixe pays si manquant."""
    # Italie sans IT
    assert normalize_full_vat("12345678901", "IT") == "IT12345678901"
    # Espagne (NIF) sans ES
    assert normalize_full_vat("B71547129", "ES") == "ESB71547129"
    # Si le préfixe est déjà celui d'un autre pays EU, on ne le change pas
    assert normalize_full_vat("FR12345678901", "DE") == "FR12345678901"
