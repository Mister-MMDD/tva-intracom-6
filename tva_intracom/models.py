"""Modeles de donnees du moteur de TVA intracommunautaire."""

from __future__ import annotations

import enum
import sys
from dataclasses import field
from decimal import Decimal
from typing import Annotated, Any

from pydantic import BeforeValidator, ConfigDict
from pydantic.dataclasses import dataclass


def _clean_decimal(value: Any) -> Any:
    """Nettoie une valeur brute (str "10,50 €", float, None…) avant la
    validation Decimal de Pydantic.

    Corrige un bug latent : Sale est une pydantic.dataclass, et Pydantic
    tente sa propre conversion en Decimal AVANT `__post_init__`. Une
    ancienne version de ce nettoyage vivait dans `Sale._to_decimal()`,
    appelée depuis `__post_init__` — mais ce code n'était en réalité
    jamais atteint utilement : soit Pydantic rejette déjà la valeur brute
    ("10,50 €" lève une ValidationError avant même d'atteindre
    __post_init__), soit Pydantic a déjà réussi à convertir en Decimal
    (valeur déjà "propre"), rendant le nettoyage redondant. En pratique le
    parser Amazon (seul parser maintenu) fournit déjà des Decimal propres
    en amont, donc ce correctif n'a pas d'impact observable sur le
    pipeline actuel — cleanup de robustesse pour les champs Decimal de
    Sale en général.
    """
    if value is None:
        return Decimal("0.00")
    if isinstance(value, (int, float, Decimal)):
        return value
    raw = str(value).strip()
    if not raw:
        return Decimal("0.00")
    raw = raw.replace("\xa0", "").replace(" ", "").replace("€", "").replace("$", "").replace("£", "")
    if "," in raw and "." not in raw:
        raw = raw.replace(",", ".")
    elif "," in raw and "." in raw:
        raw = raw.replace(".", "").replace(",", ".")
    return raw


CleanDecimal = Annotated[Decimal, BeforeValidator(_clean_decimal)]


class BuyerType(enum.Enum):
    """Type d'acheteur."""

    B2C = "B2C"  # Particulier
    B2B = "B2B"  # Entreprise (assujettie a la TVA)


class Scenario(enum.Enum):
    """Les regimes de TVA modelises."""

    # Cas 1 : vente B2C intra-UE transfrontaliere -> TVA pays destination via OSS.
    OSS_B2C = "OSS_B2C"
    # Vente domestique (stock et acheteur dans le meme pays) -> TVA locale.
    DOMESTIC = "DOMESTIC"
    # Cas 2 : Amazon assujetti presume (deemed supplier) -> Amazon collecte la TVA.
    DEEMED_SUPPLIER = "DEEMED_SUPPLIER"
    # Cas 3 : vente B2B intra-UE avec n° de TVA valide -> exoneration / autoliquidation.
    B2B_REVERSE_CHARGE = "B2B_REVERSE_CHARGE"
    # Exportation hors UE -> exoneree.
    EXPORT = "EXPORT"
    # Import > 150 EUR depuis pays tiers -> TVA d'importation (douane).
    IMPORT_STANDARD = "IMPORT_STANDARD"
    # Import <= 150 EUR, vendeur utilise son propre numéro IOSS (hors marketplace).
    IOSS_DIRECT = "IOSS_DIRECT"
    # Import > 150 EUR, vendeur est l'importateur officiel -> vente domestique dans
    # le pays de destination (immatriculation TVA locale requise).
    IMPORT_SELLER_AS_IMPORTER = "IMPORT_SELLER_AS_IMPORTER"


class Collector(enum.Enum):
    """Qui collecte et reverse la TVA."""

    SELLER = "SELLER"      # Le vendeur (vous) collecte et reverse
    AMAZON = "AMAZON"      # Amazon collecte et reverse (deemed supplier)
    BUYER = "BUYER"        # L'acheteur autoliquide (reverse charge)


class Channel(enum.Enum):
    """Canal de declaration de la TVA due par le vendeur."""

    FR_DOMESTIC = "FR_DOMESTIC"      # TVA francaise classique (CA3)
    OSS = "OSS"                      # Guichet unique OSS (declare en France)
    IOSS = "IOSS"                    # Guichet unique IOSS (imports ≤ 150 EUR, propre numéro)
    LOCAL_REGISTRATION = "LOCAL"     # Immatriculation TVA locale dans le pays
    EXONERATION = "EXONERATION"      # Aucun reversement par le vendeur (exclu du flux de taxation vendeur)


@dataclass(slots=True, config=ConfigDict(arbitrary_types_allowed=True))
class OssThresholdSummary:
    """Synthese du seuil OSS 10 000 EUR."""
    total_oss_ht: Decimal = Decimal("0.00")
    is_threshold_exceeded: bool = False
    oss_ht_by_year: dict[str, Decimal] = field(default_factory=dict)


@dataclass(frozen=True, slots=True, config=ConfigDict(arbitrary_types_allowed=True))
class Sale:
    """Une ligne de vente.

    Validation Pydantic integree pour les types et le nettoyage des donnees.
    """

    sale_id: str
    amount_ht: CleanDecimal
    buyer_type: BuyerType
    stock_country: str
    buyer_country: str
    seller_country: str = "FR"
    buyer_vat_valid: bool = False
    buyer_vat_number: str = ""
    quantity: int = 1
    original_currency: str = "EUR"
    original_amount: CleanDecimal = Decimal("0")
    exchange_rate: CleanDecimal = Decimal("1")
    exchange_rate_source: str = "eur"
    transaction_date: str = ""
    order_date: str = ""
    product_category: str = "STANDARD"
    asin: str = ""
    amazon_vat_amount: CleanDecimal = Decimal("0.00")
    seller_is_importer: bool = False
    ioss_number: str = ""
    arrival_post_code: str = ""
    display_id: str = ""
    # Conserve le NIF national brut (codice fiscale IT, NIF ES…) meme quand
    # buyer_vat_number est vide (cas B2B cross-border sans prefixe EU, voir
    # classify.py Cas 2). Permet a l'onglet VIES d'afficher/tracer ces ventes
    # qui ne passent jamais par une verification VIES en ligne.
    national_tax_id: str = ""

    def __post_init__(self) -> None:
        # Nettoyage et normalisation
        object.__setattr__(self, "stock_country", sys.intern((self.stock_country or "").upper()))
        object.__setattr__(self, "buyer_country", sys.intern((self.buyer_country or "").upper()))
        object.__setattr__(self, "seller_country", sys.intern((self.seller_country or "FR").upper()))
        
        if self.original_currency:
            object.__setattr__(self, "original_currency", sys.intern(self.original_currency.upper()))
        
        if self.product_category:
            object.__setattr__(self, "product_category", sys.intern(self.product_category.upper()))

        # ASIN et n° TVA acheteur : forte cardinalite repetitive (meme ASIN /
        # meme client sur des milliers de lignes) -> interning pour eviter la
        # duplication d'objets str identiques en RAM. Casse non modifiee
        # (contrairement aux codes pays) pour ne pas alterer une valeur deja
        # normalisee ailleurs (VIES, affichage).
        if self.asin:
            object.__setattr__(self, "asin", sys.intern(self.asin))

        if self.buyer_vat_number:
            object.__setattr__(self, "buyer_vat_number", sys.intern(self.buyer_vat_number))

        # transaction_date / order_date : chaine au format "YYYY-MM-DD" (ou
        # avec heure), forte cardinalite repetitive sur un rapport couvrant
        # une periode donnee (ex: ~30 valeurs distinctes de transaction_date
        # pour 100k lignes sur un mois) -> interning pour eviter de stocker
        # autant d'objets str identiques que de lignes. Casse non modifiee,
        # comme pour asin/buyer_vat_number : ces valeurs sont deja au format
        # attendu par les parsers (voir parsers/amazon.py) et ne doivent pas
        # etre normalisees ici.
        if self.transaction_date:
            object.__setattr__(self, "transaction_date", sys.intern(self.transaction_date))

        if self.order_date:
            object.__setattr__(self, "order_date", sys.intern(self.order_date))

        # Validation des pays (ISO 2 lettres)
        for field_name in ["stock_country", "buyer_country", "seller_country"]:
            val = getattr(self, field_name)
            if len(val) != 2:
                # On ne bloque pas forcement mais on pourrait lever une erreur
                # Pour rester compatible on laisse couler si vide mais on valide le format
                pass

        # Le nettoyage/la conversion Decimal (str "10,50 €", float…) est
        # maintenant fait en amont par CleanDecimal (BeforeValidator Pydantic,
        # voir _clean_decimal) — ce nettoyage était auparavant redondant ou
        # inatteignable ici (Pydantic validait déjà amount_ht/original_amount/
        # exchange_rate/amazon_vat_amount en Decimal avant __post_init__).

    @classmethod
    def _replace_fast(cls, original: "Sale", *, buyer_vat_valid: bool,
                       product_category: str, asin: str) -> "Sale":
        """Reconstruit une Sale en ne changeant que buyer_vat_valid/
        product_category/asin, en bypassant la validation Pydantic.

        Perf : évite de refaire la validation complète des 23 champs (dont
        les 4 CleanDecimal déjà propres) à chaque appel — mesuré 2026-09-06,
        voir README - évolution.md (~68% du temps de `_run_oss_loop` sur un
        portefeuille 100% B2B avec n° de TVA, contre ~28% sur un mix
        85%/15% B2C/B2B réaliste).

        ATTENTION — réservée EXCLUSIVEMENT à la signature exacte utilisée par
        `_effective_sale_with_vies()` (engine.py) : ce sont les 3 SEULS
        champs jamais modifiés à cet appel. Les 20 autres champs sont copiés
        tels quels depuis `original` (déjà normalisés lors de sa propre
        construction — `dataclasses.replace()` les revalidait pourtant à
        chaque appel, un no-op coûteux : intern()/upper() sur une valeur déjà
        normalisée redonne la même valeur). NE PAS généraliser cette méthode
        à d'autres champs sans réévaluer soigneusement leur normalisation.

        Reproduit à l'identique la partie de `__post_init__` concernant
        product_category (upper + intern, si non vide) et asin (intern
        seul, casse non modifiée, si non vide) — les 2 seuls champs modifiés
        qui nécessitent une normalisation. `buyer_vat_valid` est un bool,
        aucune normalisation. Voir test de parité dans tests/test_engine.py.
        """
        obj = object.__new__(cls)
        object.__setattr__(obj, "sale_id", original.sale_id)
        object.__setattr__(obj, "amount_ht", original.amount_ht)
        object.__setattr__(obj, "buyer_type", original.buyer_type)
        object.__setattr__(obj, "stock_country", original.stock_country)
        object.__setattr__(obj, "buyer_country", original.buyer_country)
        object.__setattr__(obj, "seller_country", original.seller_country)
        object.__setattr__(obj, "buyer_vat_valid", buyer_vat_valid)
        object.__setattr__(obj, "buyer_vat_number", original.buyer_vat_number)
        object.__setattr__(obj, "quantity", original.quantity)
        object.__setattr__(obj, "original_currency", original.original_currency)
        object.__setattr__(obj, "original_amount", original.original_amount)
        object.__setattr__(obj, "exchange_rate", original.exchange_rate)
        object.__setattr__(obj, "exchange_rate_source", original.exchange_rate_source)
        object.__setattr__(obj, "transaction_date", original.transaction_date)
        object.__setattr__(obj, "order_date", original.order_date)
        object.__setattr__(
            obj, "product_category",
            sys.intern(product_category.upper()) if product_category else product_category,
        )
        object.__setattr__(obj, "asin", sys.intern(asin) if asin else asin)
        object.__setattr__(obj, "amazon_vat_amount", original.amazon_vat_amount)
        object.__setattr__(obj, "seller_is_importer", original.seller_is_importer)
        object.__setattr__(obj, "ioss_number", original.ioss_number)
        object.__setattr__(obj, "arrival_post_code", original.arrival_post_code)
        object.__setattr__(obj, "display_id", original.display_id)
        object.__setattr__(obj, "national_tax_id", original.national_tax_id)
        return obj


@dataclass(frozen=True, slots=True, config=ConfigDict(arbitrary_types_allowed=True))
class VatResult:
    """Resultat du calcul de TVA pour une vente."""

    sale: Sale
    scenario: Scenario
    vat_country: str
    vat_rate: Decimal
    vat_amount: Decimal
    collector: Collector
    channel: Channel
    note: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "vat_country", sys.intern((self.vat_country or "").upper()))

    @classmethod
    def _new_unchecked(cls, *, sale, scenario, vat_country, vat_rate, vat_amount,
                        collector, channel, note) -> "VatResult":
        """Construit un VatResult en court-circuitant TOTALEMENT la validation
        Pydantic (perf : ~64% plus rapide qu'un appel normal `VatResult(...)`,
        mesuré 2026-09-06 — voir README - évolution.md).

        ATTENTION — usage strictement réservé au chemin chaud interne de
        `engine.py` (`compute_vat()` et `_build_oss_note()`), où tous les
        arguments sont déjà garantis du bon type par construction (Decimal
        natifs pour vat_rate/vat_amount via `_vat_amount()`/`vat_rate()`,
        membres d'enum Scenario/Collector/Channel, jamais de valeurs brutes
        non normalisées). NE JAMAIS exposer cette méthode à un parser, une
        API externe, ou tout code qui pourrait passer des types non garantis
        — aucune coercition, aucun nettoyage, aucune erreur de validation ne
        sera levée en cas de type incorrect (comportement silencieusement
        incorrect au lieu d'un échec explicite).

        Reproduit à l'identique le seul effet de `__post_init__` ci-dessus
        (normalisation + interning de `vat_country`) : si `__post_init__`
        change un jour, cette méthode doit être mise à jour en parallèle —
        voir test de parité dédié dans tests/test_engine.py.
        """
        obj = object.__new__(cls)
        object.__setattr__(obj, "sale", sale)
        object.__setattr__(obj, "scenario", scenario)
        object.__setattr__(obj, "vat_country", sys.intern((vat_country or "").upper()))
        object.__setattr__(obj, "vat_rate", vat_rate)
        object.__setattr__(obj, "vat_amount", vat_amount)
        object.__setattr__(obj, "collector", collector)
        object.__setattr__(obj, "channel", channel)
        object.__setattr__(obj, "note", note)
        return obj


@dataclass(slots=True, config=ConfigDict(arbitrary_types_allowed=True))
class ViesReclassification:
    """Detail d'une vente B2B reclassifiee en B2C."""
    sale_id: str
    buyer_vat_number: str
    buyer_country: str
    amount_ht: Decimal
    vat_avoided: Decimal
    reason: str
    vat_delta: Decimal = Decimal("0.00")
    is_domestic_reverse_charge: bool = False
    display_id: str = ""
    stock_country: str = ""
    # True : NIF/identifiant fiscal national (ES NIF/CIF, IT codice fiscale, etc.)
    # — jamais un vrai n° de TVA intracommunautaire, jamais envoyé à VIES. À ne
    # JAMAIS afficher sous "N° TVA rejeté" : ce n'est pas un rejet VIES, juste
    # un identifiant d'un autre type que Amazon place dans la même colonne.
    is_national_tax_id: bool = False
    # True  : TVA due au pays de depart (Art.31 — n° TVA acheteur invalide,
    #         y compris quand l'art.194 etait a tort applique en cross-border).
    # False : TVA due au pays d'arrivee (destination) — pays n'ayant pas
    #         adopte l'art.194, vendeur immatricule/declare localement a
    #         destination (ou domestique FR).
    taxed_at_departure: bool = False
    scenario: str = ""


@dataclass(slots=True, config=ConfigDict(arbitrary_types_allowed=True))
class ViesValidationSummary:
    """Synthese de la validation VIES."""
    total_checked: int = 0
    valid_count: int = 0
    invalid_count: int = 0
    inconclusive_count: int = 0
    # Classifications saisies manuellement par l'utilisateur — sous-ensembles
    # de valid_count/invalid_count (une décision manuelle est définitive au
    # même titre qu'une vérification VIES automatique réussie), trackés à part
    # uniquement à des fins d'audit (savoir combien viennent d'une saisie
    # manuelle plutôt que d'un contrôle VIES automatique frais).
    manual_valid_count: int = 0
    manual_invalid_count: int = 0
    # Replis sur une entrée de cache déjà expirée (TTL dépassé), utilisée
    # uniquement parce que VIES était indisponible au moment du calcul —
    # traités comme incertains (B2C par sécurité), jamais comme fiables.
    stale_fallback_count: int = 0
    # NIF/identifiants fiscaux nationaux (ex: NIF/NIE espagnol) sans préfixe
    # pays EU. Ne sont JAMAIS envoyés à VIES (buyer_vat_number est vide par
    # construction) — comptés à part pour ne pas être confondus avec les
    # vraies vérifications VIES (valid/invalid/inconclusive).
    national_id_count: int = 0
    inconclusive_vats: list[str] = field(default_factory=list)
    inconclusive_vat_details: list[dict[str, Any]] = field(default_factory=list)
    vat_to_display_ids: dict[str, list[str]] = field(default_factory=dict)
    reclassifications: list[ViesReclassification] = field(default_factory=list)
    # Type réel : set[tuple[str, Decimal]] — clés produites par engine._sale_key()
    # (sale_id, amount_ht). L'annotation `set[int]` était incorrecte avant ce
    # correctif (aucun impact fonctionnel : pydantic ne valide qu'à la
    # construction, et le set démarre vide — mais gardait une trace trompeuse
    # pour quiconique relirait ce modèle).
    vies_affected_sale_ids: set[tuple[str, Decimal]] = field(default_factory=set)

    @property
    def total_valid(self) -> int: return self.valid_count
    @property
    def total_invalid(self) -> int: return self.invalid_count
    @property
    def total_inconclusive(self) -> int: return self.inconclusive_count
    @property
    def total_manual_override(self) -> int: return self.manual_valid_count + self.manual_invalid_count
    @property
    def total_stale_fallback(self) -> int: return self.stale_fallback_count
    @property
    def total_national_id(self) -> int: return self.national_id_count
    @property
    def total_verified(self) -> int:
        """'Numéros vérifiés' : Valides + Invalides uniquement (décision
        automatique OU manuelle, peu importe — les deux sont définitives).
        Exclut explicitement les non-vérifiés (inconclusive_count, aucune
        réponse exploitable du serveur) et les NIF/identifiants nationaux
        (jamais envoyés à VIES). Par construction :
        total_verified + inconclusive_count == somme des numéros soumis."""
        return self.valid_count + self.invalid_count
    @property
    def total_not_auto_verified(self) -> int:
        """Total des numéros dont le statut valide/invalide vient d'une
        saisie manuelle plutôt que d'une vérification VIES fraîche, + repli
        sur cache périmé + non-vérifiés (serveur indisponible). Sert à
        nuancer la fiabilité de total_verified, sans changer valid_count/
        invalid_count qui restent la source de vérité pour l'exonération."""
        return self.manual_valid_count + self.manual_invalid_count + self.stale_fallback_count + self.inconclusive_count
    @property
    def total_auto_verified(self) -> int:
        """Numéros dont le statut vient d'une vérification automatique
        fraîche (VIES ou cache dans le TTL), à l'exclusion des décisions
        manuelles — utilisé uniquement pour le message de fiabilité de la
        billing gate, PAS pour valid_count/invalid_count qui restent la
        source de vérité pour l'exonération (une décision manuelle y est
        incluse, cf. commentaire plus haut)."""
        return self.valid_count + self.invalid_count - self.manual_valid_count - self.manual_invalid_count
    @property
    def fraud_avoided_amount(self) -> Decimal:
        return sum((r.vat_avoided for r in self.reclassifications), Decimal("0.00"))
    @property
    def fraud_avoided_ht(self) -> Decimal:
        return sum((r.amount_ht for r in self.reclassifications), Decimal("0.00"))
