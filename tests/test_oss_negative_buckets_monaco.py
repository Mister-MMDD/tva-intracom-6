"""BUGFIX (point #6, README - évolution.md) : `suggest_negative_bucket_corrections`
comparait `res.sale.stock_country` brut (ex. "MC") à des `neg_keys` construites
avec des pays de départ déjà normalisés via `fiscal_equivalent_country()`
(Monaco -> "FR"), ce qui excluait silencieusement toute vente/avoir à stock
Monaco du rattachement automatique, même quand la vente d'origine matchant le
sale_id était bien présente dans le même jeu de données.
"""
from __future__ import annotations

from decimal import Decimal

from tva_intracom.models import BuyerType, Channel, Collector, Sale, Scenario, VatResult
from tva_intracom.oss_export import suggest_negative_bucket_corrections


def _vat_result(sale_id: str, amount_ht: str, transaction_date: str,
                 stock_country: str = "MC", buyer_country: str = "DE",
                 vat_rate: str = "19") -> VatResult:
    sale = Sale(
        sale_id=sale_id,
        amount_ht=Decimal(amount_ht),
        buyer_type=BuyerType.B2C,
        stock_country=stock_country,
        buyer_country=buyer_country,
        seller_country="FR",
        transaction_date=transaction_date,
    )
    rate = Decimal(vat_rate)
    return VatResult(
        sale=sale,
        scenario=Scenario.OSS_B2C,
        vat_country=buyer_country,
        vat_rate=rate,
        vat_amount=(sale.amount_ht * rate / Decimal("100")).quantize(Decimal("0.01")),
        collector=Collector.SELLER,
        channel=Channel.OSS,
        note="",
    )


def test_monaco_stock_refund_is_matched_to_origin_sale():
    """Vente d'origine (Q1) + avoir plus gros qui la rembourse (Q2, même
    sale_id) sur un stock Monaco -> le bucket (FR normalisé, DE, 19%) de Q2
    est négatif ; l'avoir DOIT être rattaché à son origine Q1 (matched),
    pas laissé `unmatched` par la seule faute de la clé "MC" vs "FR"."""
    sale_q1 = _vat_result("ORDER-1", "1000", "2024-01-15")   # Q1 : vente d'origine
    refund_q2 = _vat_result("ORDER-1", "-1500", "2024-04-10")  # Q2 : avoir > vente -> bucket Q2 négatif

    # On simule un fichier couvrant les deux trimestres, période déclarée = Q2
    # (celle où le bucket négatif apparaît).
    all_results = [sale_q1, refund_q2]

    suggestions = suggest_negative_bucket_corrections(all_results, period="2024-Q2")

    assert len(suggestions) == 1
    sug = suggestions[0]

    # Avant le correctif : sug.matched == [] et sug.unmatched_ht == 0 malgré
    # la présence de la vente d'origine dans `all_results` (clé "MC" != "FR").
    assert len(sug.matched) == 1, (
        "L'avoir sur stock Monaco n'a pas été rattaché à sa vente d'origine "
        "(régression du bug de normalisation MC/FR)."
    )
    assert sug.matched[0].sale_id == "ORDER-1"
    assert sug.matched[0].origin_period == "2024-Q1"
    assert sug.fully_resolved
    assert sug.unmatched_count == 0
