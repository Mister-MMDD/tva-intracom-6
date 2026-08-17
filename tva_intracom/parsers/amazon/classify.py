"""Classification acheteur, conversion devise et construction du Sale.

Chaque fonction a une responsabilité unique et est testable isolément.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date as _date
from decimal import Decimal, ROUND_HALF_UP
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    # Import réservé au typage statique : BuyerType est injecté en
    # paramètre (voir classify_buyer) pour éviter l'import circulaire
    # réel au runtime avec models.py. `from __future__ import annotations`
    # (en tête de fichier) rend cette annotation paresseuse, donc cet
    # import ne s'exécute jamais en dehors des vérificateurs de type.
    from ...models import BuyerType

from .constants import (
    REFUND_TYPES,
    is_national_tax_id,
    is_vat_exception_territory,
    is_vat_placeholder,
    safe_decimal,
)
from ...ecb_rates import convert_to_currency  # noqa: E402

logger = logging.getLogger(__name__)

_CENT = Decimal("0.01")


# ---------------------------------------------------------------------------
# Résultat intermédiaire de la classification acheteur
# ---------------------------------------------------------------------------

@dataclass
class BuyerClassification:
    buyer_vat: str         # numéro normalisé (vide si B2C)
    buyer_type: "BuyerType"  # BuyerType.B2C ou BuyerType.B2B
    buyer_vat_valid: bool  # présence d'un numéro (pas validation VIES)
    national_tax_id: str = ""  # NIF national brut conservé même si buyer_vat est vidé
    # (cross-border sans préfixe EU, cf. Cas 2) — pour
    # traçabilité dans le rapport VIES (jamais envoyé à VIES).


def classify_buyer(
        raw_vat: str,
        arrival: str,
        departure: str,
        normalize_fn,   # _normalize_vat_id_vies depuis vies.py
        BuyerType: "type[BuyerType]",  # enum injecté pour éviter l'import circulaire
) -> BuyerClassification:
    """Classifie l'acheteur (B2B/B2C) et nettoie le numéro TVA.

    Trois cas :
    1. Placeholder Amazon (FRINV…) → B2C, numéro vidé
    2. NIF national sans préfixe EU (codice fiscale IT, NIF ES…) →
       B2B, numéro vidé si cross-border (art.194 via moteur), conservé si domestic
    3. Vrai numéro TVA intracommunautaire → B2B après normalisation
    4. Pas de numéro → B2C
    """
    buyer_vat = raw_vat.strip()

    # Cas 1 : placeholder Amazon
    if buyer_vat and is_vat_placeholder(buyer_vat):
        return BuyerClassification(
            buyer_vat="",
            buyer_type=BuyerType.B2C,
            buyer_vat_valid=False,
        )

    # Cas 2 : NIF fiscal national sans préfixe EU
    if buyer_vat and is_national_tax_id(buyer_vat, arrival):
        # Cross-border : on vide le numéro → VIES jamais consulté, engine applique art.194
        # Domestic (ex: IT→IT avec codice fiscale) : on garde pour que le moteur
        #   détecte bool(buyer_vat_number)=True → is_b2b_domestic → art.194 local
        vat_out = "" if departure != arrival else buyer_vat
        return BuyerClassification(
            buyer_vat=vat_out,
            buyer_type=BuyerType.B2B,
            buyer_vat_valid=False,
            national_tax_id=buyer_vat if departure != arrival else "",
        )

    # Cas 3 : vrai numéro TVA intracommunautaire (ou vide → B2C)
    if buyer_vat:
        buyer_vat = buyer_vat.replace(" ", "").strip()
        try:
            buyer_vat = normalize_fn(buyer_vat)
        except ValueError:
            pass  # garder le numéro brut, le moteur VIES le rejettera
        return BuyerClassification(
            buyer_vat=buyer_vat,
            buyer_type=BuyerType.B2B,
            buyer_vat_valid=True,
        )

    # Cas 4 : pas de numéro → B2C
    return BuyerClassification(
        buyer_vat="",
        buyer_type=BuyerType.B2C,
        buyer_vat_valid=False,
    )


# ---------------------------------------------------------------------------
# Résultat de conversion devise
# ---------------------------------------------------------------------------

@dataclass
class CurrencyResult:
    amount_ht: Decimal
    original_currency: str
    original_amount: Decimal
    exchange_rate: Decimal
    exchange_rate_source: str
    # Date effectivement utilisée pour choisir le taux BCE, et si elle
    # provient bien de la ligne (tx_date_str) ou d'un repli (voir
    # `convert_currency` / `last_valid_date`). Permet à l'appelant
    # (loader.py) de maintenir "la date valide la plus récente vue jusque là
    # dans le fichier" sans avoir à reparser tx_date_str lui-même.
    transaction_date_used: _date | None = None
    date_was_fallback: bool = False


def convert_currency(
        amount_ht: Decimal,
        currency: str,
        tx_date_str: str,
        tx_type: str,
        fmt: int,
        row: dict,
        convert_currencies: bool,
        target_currency: str = "EUR",
        last_valid_date: _date | None = None,
) -> CurrencyResult:
    """Convertit le montant vers la devise cible (EUR par défaut) si nécessaire.

    Retourne un CurrencyResult avec les métadonnées de conversion pour
    traçabilité (source du taux, taux utilisé, montant original).

    `last_valid_date` : dernière date de transaction valide rencontrée plus
    haut dans le même fichier (maintenue par l'appelant, voir loader.py).
    Utilisée comme repli si `tx_date_str` est illisible, à la place de
    `date.today()` — un fichier de 2024 traité aujourd'hui ne doit pas faire
    retomber une ligne malformée sur le taux BCE de 2026 (écart de change
    potentiellement significatif). Si aucune date valide n'a encore été vue
    dans le fichier (ex. toute première ligne malformée), on retombe sur
    `date.today()` en dernier recours — il n'y a alors rien de mieux.
    """
    original_currency = currency
    original_amount   = amount_ht
    exchange_rate     = Decimal("1")
    exchange_rate_source = target_currency.lower()

    if not convert_currencies or currency == target_currency:
        return CurrencyResult(
            amount_ht=amount_ht,
            original_currency=original_currency,
            original_amount=original_amount,
            exchange_rate=exchange_rate,
            exchange_rate_source=exchange_rate_source,
        )

    # Récupération du taux Amazon comme fallback BCE (basé sur EUR)
    # Amazon donne généralement le taux CCY/EUR.
    if fmt == 5:
        # Format 5 : INVOICE_LEVEL_EXCHANGE_RATE (plus précis, lié à la facture)
        raw_fx = row.get("invoice_level_exchange_rate", "").strip()
        amazon_rate = safe_decimal(raw_fx) if raw_fx else None
        if amazon_rate and amazon_rate == Decimal("0"):
            amazon_rate = None
    else:
        # Formats 3/4 : EXCHANGE_RATE fourni par Amazon
        raw_fx = row.get("exchange_rate", "").strip()
        amazon_rate = safe_decimal(raw_fx) if raw_fx else None
        if amazon_rate and amazon_rate == Decimal("0"):
            amazon_rate = None

    # Parsing de la date de transaction
    tx_date: _date | None = None
    date_was_fallback = False
    if tx_date_str:
        try:
            parts = tx_date_str.split("-")
            tx_date = _date(int(parts[0]), int(parts[1]), int(parts[2]))
        except (ValueError, IndexError):
            logger.warning(
                "convert_currency : date de transaction illisible ('%s') — "
                "taux BCE de la dernière date valide vue dans le fichier "
                "utilisé à la place (repli sur date.today() uniquement si "
                "aucune date valide n'a encore été vue).",
                tx_date_str,
            )
    if tx_date is None:
        date_was_fallback = True
        tx_date = last_valid_date or _date.today()

    # Conversion vers la devise cible via EUR
    converted, exchange_rate, exchange_rate_source = convert_to_currency(
        abs(amount_ht), currency, target_currency, tx_date, fallback_rate=amazon_rate,
    )
    amount_ht = -converted if tx_type in REFUND_TYPES else converted

    return CurrencyResult(
        amount_ht=amount_ht,
        original_currency=original_currency,
        original_amount=original_amount,
        exchange_rate=exchange_rate,
        exchange_rate_source=exchange_rate_source,
        transaction_date_used=tx_date,
        date_was_fallback=date_was_fallback,
    )


def convert_amazon_vat(
        amazon_vat_raw: Decimal,
        exchange_rate: Decimal,
        tx_type: str,
) -> Decimal:
    """Convertit la TVA Amazon dans la même devise/taux que amount_ht.

    exchange_rate : unités de devise pour 1 EUR (format BCE) → on divise.
    """
    if exchange_rate and exchange_rate != Decimal("1") and amazon_vat_raw:
        amt = (abs(amazon_vat_raw) / exchange_rate).quantize(_CENT, rounding=ROUND_HALF_UP)
    else:
        amt = abs(amazon_vat_raw)

    return -amt if tx_type in REFUND_TYPES else amt


def apply_vat_exception(arrival: str, postal_code: str) -> str:
    """Retourne "XX" si le code postal indique un territoire hors TVA UE.

    "XX" est le code sentinelle → EXPORT dans engine.py.
    Sinon retourne le pays d'arrivée inchangé.
    """
    if _is_exception := is_vat_exception_territory(arrival, postal_code):
        logger.debug(
            "Territoire d'exception TVA détecté (pays=%s, CP=%s) → EXPORT.",
            arrival, postal_code,
        )
        return "XX"
    return arrival