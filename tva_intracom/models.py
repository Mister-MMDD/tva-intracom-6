"""Modeles de donnees du moteur de TVA intracommunautaire."""

from __future__ import annotations

import enum
from dataclasses import dataclass
from decimal import Decimal


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
    NONE = "NONE"                    # Aucun reversement par le vendeur


@dataclass
class OssThresholdSummary:
    """Synthese du seuil OSS 10 000 EUR.

    total_oss_ht         : cumul de la DERNIÈRE année traitée (pour affichage
                           de la barre de progression sur la période courante).
    oss_ht_by_year       : cumul OSS HT par année civile {\"YYYY\": Decimal}.
                           Permet de détecter un dépassement sur une année
                           antérieure quand le fichier couvre plusieurs années.
    is_threshold_exceeded: True si AU MOINS UNE année dépasse 10 000 €.
    """
    total_oss_ht: Decimal = Decimal("0.00")
    is_threshold_exceeded: bool = False
    oss_ht_by_year: dict = None   # type: dict[str, Decimal]

    def __post_init__(self):
        if self.oss_ht_by_year is None:
            object.__setattr__(self, "oss_ht_by_year", {})


@dataclass(frozen=True)
class Sale:
    """Une ligne de vente.

    Attributs:
        sale_id: identifiant libre de la vente.
        amount_ht: base imposable (prix hors taxe), en euros.
        buyer_type: B2B ou B2C.
        stock_country: pays ou se trouve le stock / d'ou part la marchandise.
        buyer_country: pays de l'acheteur (destination).
        seller_country: pays d'etablissement du vendeur (par defaut FR).
        buyer_vat_valid: pour le B2B, True si le n° de TVA intra a ete valide
            (VIES). Ignore pour le B2C.
        buyer_vat_number: numero de TVA intracommunautaire de l'acheteur
            (ex: 'DE123456789'). Utilise pour la validation VIES automatique.
        quantity: quantite (informatif, non utilise dans le calcul).
        original_currency: devise originale si != EUR (ex: 'GBP').
        original_amount: montant dans la devise originale.
        exchange_rate: taux de change utilise (unites de devise pour 1 EUR).
        exchange_rate_source: source du taux ('ecb', 'fallback', 'eur').
        transaction_date: date de la transaction (pour le taux de change).
        product_category: type de taux de TVA (standard ou réduit)
        seller_is_importer: si True pour un import hors-UE > 150 EUR, le vendeur
            est l'importateur officiel (DDP) → la vente redevient domestique dans
            le pays de destination avec immatriculation TVA locale obligatoire.
        ioss_number: numéro IOSS propre du vendeur (ex: 'IM1234567890'). Si renseigné
            et que la vente est un import B2C ≤ 150 EUR hors marketplace deemed-supplier,
            le vendeur collecte lui-même la TVA via son guichet IOSS.
    """

    sale_id: str
    amount_ht: Decimal
    buyer_type: BuyerType
    stock_country: str
    buyer_country: str
    seller_country: str = "FR"
    buyer_vat_valid: bool = False
    buyer_vat_number: str = ""
    quantity: int = 1
    original_currency: str = "EUR"
    original_amount: Decimal = Decimal("0")
    exchange_rate: Decimal = Decimal("1")
    exchange_rate_source: str = "eur"
    transaction_date: str = ""        # Fait générateur de la TVA (date d'expédition/
                                       # facturation si disponible — art. 65/66 Dir. TVA —
                                       # sinon date de commande en repli).
    order_date: str = ""              # Date de commande brute, si distincte de
                                       # transaction_date. Vide si non disponible ou si
                                       # identique à transaction_date. Sert uniquement au
                                       # rapport de réconciliation des écarts de période.
    product_category: str = "STANDARD"
    asin: str = ""
    amazon_vat_amount: Decimal = Decimal("0.00")
    seller_is_importer: bool = False   # DDP : vendeur = importateur officiel
    ioss_number: str = ""              # Numéro IOSS propre du vendeur (hors marketplace)
    arrival_post_code: str = ""        # Code postal de destination (ARRIVAL_POST_CODE Amazon)
                                       # Utilisé pour détecter les territoires hors UE fiscale
                                       # (Canaries, Heligoland, Åland…) — voir rates.is_fiscal_eu()
    order_date: str = ""               # Date de commande (ORDER_DATE, format V5 uniquement).
                                       # Distincte de transaction_date qui porte la date
                                       # d'EXIGIBILITÉ retenue pour le calcul fiscal (en
                                       # principe la date de livraison/expédition — art. 65
                                       # Dir. 2006/112/CE). Champ informatif/audit : permet
                                       # de détecter et signaler les commandes à cheval sur
                                       # deux périodes de déclaration (voir loader.py).

    def __post_init__(self) -> None:
        object.__setattr__(self, "stock_country", self.stock_country.upper())
        object.__setattr__(self, "buyer_country", self.buyer_country.upper())
        object.__setattr__(self, "seller_country", self.seller_country.upper())
        if not isinstance(self.amount_ht, Decimal):
            # Nettoyage défensif pour les valeurs issues de CSV mal formatés :
            # - espaces et espaces insécables utilisés comme séparateurs de milliers (FR)
            # - virgule décimale FR : "1 234,56" → "1234.56"
            # - symboles monétaires résiduels : "€", "$", "£"
            raw = str(self.amount_ht)
            raw = raw.replace("\xa0", "").replace(" ", "")   # espaces milliers
            raw = raw.replace("€", "").replace("$", "").replace("£", "")
            # Virgule décimale FR : seulement si pas déjà un point décimal
            if "," in raw and "." not in raw:
                raw = raw.replace(",", ".")
            elif "," in raw and "." in raw:
                # Format "1.234,56" → supprimer le point millier, convertir virgule
                raw = raw.replace(".", "").replace(",", ".")
            try:
                object.__setattr__(self, "amount_ht", Decimal(raw))
            except Exception:
                raise ValueError(
                    f"Impossible de convertir '{self.amount_ht}' en montant décimal "
                    f"pour la vente '{self.sale_id}'. "
                    "Vérifiez le format du fichier source (séparateur décimal, devise)."
                )


@dataclass(frozen=True)
class VatResult:
    """Resultat du calcul de TVA pour une vente."""

    sale: Sale
    scenario: Scenario
    vat_country: str          # Pays dont le taux de TVA s'applique ("" si exonere)
    vat_rate: Decimal         # Taux applique, en %
    vat_amount: Decimal       # Montant de TVA, en euros
    collector: Collector      # Qui collecte/reverse
    channel: Channel          # Canal de declaration cote vendeur
    note: str                 # Explication lisible