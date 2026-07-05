"""Logique de classification et de calcul de la TVA pour chaque vente.

Le moteur croise trois variables (ou est le stock, qui est l'acheteur, ou est
l'acheteur) pour determiner le regime applicable parmi les 4 cas principaux :

* Cas 1 : vente B2C intra-UE transfrontaliere -> TVA pays destination via OSS.
* Cas 2 : Amazon assujetti presume (deemed supplier) -> Amazon collecte la TVA.
* Cas 3 : vente B2B intra-UE (n° TVA valide) -> exonération / autoliquidation.
* Cas 4 : consequence des transferts de stock FBA -> immatriculation TVA locale
  (geree au niveau du reporting, voir report.py).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field, replace as _dc_replace
from decimal import ROUND_HALF_UP, Decimal

logger = logging.getLogger(__name__)

from .models import (
    BuyerType,
    Channel,
    Collector,
    Sale,
    Scenario,
    VatResult,
    OssThresholdSummary,
)
from .rates import is_eu, is_fiscal_eu, vat_rate, vat_rate_at_date, has_rate_changed
from .rates import DOMESTIC_REVERSE_CHARGE_COUNTRIES
from datetime import date as _date
from .vies import normalize_full_vat as _normalize_full_vat_canonical

# Seuil de valeur intrinseque d'un envoi pour le regime IOSS (import).
IOSS_THRESHOLD = Decimal("150")
_CENT = Decimal("0.01")

def _round(amount: Decimal) -> Decimal:
    return amount.quantize(_CENT, rounding=ROUND_HALF_UP)

def _vat_amount(base: Decimal, rate: Decimal) -> Decimal:
    return _round(base * (rate / Decimal("100")))

def compute_vat(sale: Sale, marketplace_name: str = "Amazon", product_category: str = "") -> VatResult:
    """Calcule le regime et le montant de TVA d'une vente en prenant en compte la catégorie produit."""
    
    # La catégorie produit effective : le paramètre explicite prime sur le champ Sale,
    # et le champ Sale prime sur le fallback STANDARD.
    effective_category = (product_category or sale.product_category or "STANDARD").strip().upper()

    seller_eu = is_eu(sale.seller_country)
    stock_eu = is_eu(sale.stock_country)
    # is_fiscal_eu() combine l'appartenance UE politique et les exclusions art.6
    # dir. 2006/112/CE (Canaries, Heligoland, Åland…). Une vente vers ces territoires
    # est une exportation exonérée, même si le code pays est "ES", "DE" ou "FI".
    buyer_eu = is_fiscal_eu(sale.buyer_country, sale.arrival_post_code or None)
    cross_border = sale.stock_country != sale.buyer_country

    # ------------------------------------------------------------------
    # SÉCURITÉ IMMÉDIATE : Cas d'exportation hors UE (ex: GB, US...)
    # On traite ce cas EN PREMIER pour éviter d'interroger vat_rate inutilement
    # ------------------------------------------------------------------
    if not buyer_eu:
        return VatResult(
            sale=sale,
            scenario=Scenario.EXPORT,
            vat_country="",
            vat_rate=Decimal("0"),
            vat_amount=Decimal("0.00"),
            collector=Collector.SELLER,
            channel=Channel.NONE,
            note=(
                "Exportation hors UE : exonérée de TVA (justificatif de sortie du "
                "territoire requis)."
            ),
        )

    # 1. Calcul du taux dynamique basé sur le pays, la catégorie et la date
    # La date de transaction est utilisée pour appliquer le taux historique correct
    # (ex: EE 22% avant juil.2025, RO 19% avant août 2025).
    _tx_date: _date | None = None
    if sale.transaction_date:
        try:
            _tx_date = _date.fromisoformat(sale.transaction_date[:10])
        except ValueError:
            pass  # date malformée → taux courant (pas de correctif historique)
    tax_rate = vat_rate(sale.buyer_country, effective_category, tx_date=_tx_date)
    tax_amount = _vat_amount(sale.amount_ht, tax_rate)

    # ------------------------------------------------------------------
    # Cas IOSS_DIRECT : import B2C ≤ 150 EUR, vendeur avec son propre
    # numéro IOSS (hors deemed supplier / marketplace).
    # Priorité avant le bloc deemed supplier car le vendeur a opté pour
    # le guichet IOSS en propre.
    # ------------------------------------------------------------------
    if (
        sale.buyer_type == BuyerType.B2C
        and buyer_eu
        and not stock_eu
        and sale.amount_ht <= IOSS_THRESHOLD
        and sale.ioss_number
    ):
        return VatResult(
            sale=sale,
            scenario=Scenario.IOSS_DIRECT,
            vat_country=sale.buyer_country,
            vat_rate=tax_rate,
            vat_amount=tax_amount,
            collector=Collector.SELLER,
            channel=Channel.IOSS,
            note=(
                f"Import ≤ {IOSS_THRESHOLD} EUR : TVA {tax_rate}% collectée par le vendeur "
                f"via son guichet IOSS ({sale.ioss_number}) — déclaration sur portail IOSS."
            ),
        )

    # ------------------------------------------------------------------
    # Cas 2 : Place de marché assujettie presumee (deemed supplier)
    # ------------------------------------------------------------------
    if sale.buyer_type == BuyerType.B2C and buyer_eu:
        seller_non_eu = not seller_eu
        low_value_import = (not stock_eu) and sale.amount_ht <= IOSS_THRESHOLD
        if seller_non_eu or low_value_import:
            return VatResult(
                sale=sale,
                scenario=Scenario.DEEMED_SUPPLIER,
                vat_country=sale.buyer_country,
                vat_rate=tax_rate,
                vat_amount=tax_amount,
                collector=Collector.AMAZON,
                channel=Channel.NONE,
                note=f"{marketplace_name} collecte la TVA ({tax_rate}%) sur {sale.buyer_country}."
            )

    # ------------------------------------------------------------------
    # Cas 3 : vente B2B intra-UE avec n° de TVA valide -> autoliquidation
    # ------------------------------------------------------------------
    if sale.buyer_type == BuyerType.B2B:
        if stock_eu and buyer_eu and cross_border and sale.buyer_vat_valid:
            return VatResult(
                sale=sale,
                scenario=Scenario.B2B_REVERSE_CHARGE,
                vat_country="",
                vat_rate=Decimal("0"),
                vat_amount=Decimal("0.00"),
                collector=Collector.BUYER,
                channel=Channel.NONE,
                note="Livraison intracommunautaire B2B exonérée (autoliquidation)."
            )

        # B2B cross-border sans TVA intracom valide (buyer_vat_valid=False) :
        # NIF national ES/IT détecté dans l'adaptateur (buyer_vat vidé pour éviter VIES),
        # ou numéro B2B sans validation VIES possible.
        #
        # Le régime OSS NE s'applique PAS aux ventes B2B — il est réservé au B2C.
        # Deux sous-cas selon le pays de destination :
        #
        #   a) Pays ayant adopté art.194 dir.2006/112/CE (ES, IT, PL, CZ, SK, HU, RO…) :
        #      L'acheteur assujetti autoliquide la TVA dans son pays → TVA=0.
        #
        #   b) Pays n'ayant PAS adopté art.194 (DE, FR, AT, BE, NL, DK…) :
        #      Vendeur collecte TVA locale → LOCAL_REGISTRATION ou FR_DOMESTIC.
        if stock_eu and buyer_eu and cross_border:
            if sale.buyer_country in DOMESTIC_REVERSE_CHARGE_COUNTRIES:
                return VatResult(
                    sale=sale,
                    scenario=Scenario.B2B_REVERSE_CHARGE,
                    vat_country=sale.buyer_country,
                    vat_rate=Decimal("0"),
                    vat_amount=Decimal("0.00"),
                    collector=Collector.BUYER,
                    channel=Channel.NONE,
                    note=(
                        f"Vente B2B cross-border {sale.stock_country}→{sale.buyer_country} : "
                        f"identifiant fiscal national sans préfixe TVA intracom. "
                        f"Art.194 dir.2006/112/CE adopté en {sale.buyer_country} : "
                        f"autoliquidation par l'acheteur assujetti — vendeur ne collecte pas."
                    ),
                )
            else:
                is_dest_fr = sale.buyer_country == "FR"
                channel = Channel.FR_DOMESTIC if is_dest_fr else Channel.LOCAL_REGISTRATION
                return VatResult(
                    sale=sale,
                    scenario=Scenario.DOMESTIC,
                    vat_country=sale.buyer_country,
                    vat_rate=tax_rate,
                    vat_amount=tax_amount,
                    collector=Collector.SELLER,
                    channel=channel,
                    note=(
                        f"Vente B2B cross-border {sale.stock_country}→{sale.buyer_country} : "
                        f"numéro TVA acheteur non valide VIES. "
                        f"Art.194 NON adopté en {sale.buyer_country} : "
                        f"vendeur collecte TVA {tax_rate}% — "
                        + (
                            "déclaration CA3 (France)."
                            if is_dest_fr
                            else f"immatriculation TVA locale requise en {sale.buyer_country}."
                        )
                    ),
                )

    # ------------------------------------------------------------------
    # Cas 1 : vente B2C intra-UE transfrontaliere (OSS par défaut)
    # ------------------------------------------------------------------
    if stock_eu and buyer_eu and cross_border:
        return VatResult(
            sale=sale,
            scenario=Scenario.OSS_B2C,
            vat_country=sale.buyer_country,
            vat_rate=tax_rate,
            vat_amount=tax_amount,
            collector=Collector.SELLER,
            channel=Channel.OSS,
            note=f"Vente OSS vers {sale.buyer_country} au taux de {tax_rate}%."
    )

    # ------------------------------------------------------------------
    # Fin de fonction : Différenciation Vente Locale / Importation
    # ------------------------------------------------------------------
    is_domestic = sale.stock_country == sale.buyer_country
    
    if is_domestic:
        is_fr = sale.stock_country == "FR"

        # Vente B2B domestique hors France : autoliquidation nationale.
        # En droit ES/IT/DE/etc., une vente entre deux assujettis dans le même pays
        # est soumise à autoliquidation par l'acheteur — que son n° TVA soit validé
        # par VIES ou non (VIES ne couvre que l'intracommunautaire).
        # Cas inclus :
        #   1. buyer_type = B2B (n° TVA intracom présent, validé ou non)
        #   2. buyer_type = B2C mais avec un numéro fiscal fourni (NIF national ES/IT/etc.)
        #      → Amazon transmet le NIF sans préfixe pays, _is_valid_vat_intracom le rejette
        #        et l'adaptateur classe la vente en B2C par précaution. Mais un NIF national
        #        sur une vente domestique indique un professionnel assujetti local.
        #        Le cabinet comptable ne taxe pas ces ventes (autoliquidation nationale).
        is_b2b_domestic = (
            sale.buyer_type == BuyerType.B2B
            or (sale.buyer_type == BuyerType.B2C and bool(sale.buyer_vat_number))
        )
        if is_b2b_domestic and not is_fr and sale.stock_country in DOMESTIC_REVERSE_CHARGE_COUNTRIES:
            return VatResult(
                sale=sale,
                scenario=Scenario.DOMESTIC,
                vat_country=sale.stock_country,
                vat_rate=Decimal("0"),
                vat_amount=Decimal("0.00"),
                collector=Collector.BUYER,
                channel=Channel.NONE,
                note=(
                    f"Vente B2B domestique {sale.stock_country} : autoliquidation nationale. "
                    f"L'acheteur assujetti (n° {'TVA: ' + sale.buyer_vat_number if sale.buyer_vat_number else 'inconnu'}) "
                    f"déclare et reverse la TVA — le vendeur ne collecte pas."
                ),
            )

        channel = Channel.FR_DOMESTIC if is_fr else Channel.LOCAL_REGISTRATION
        note = (
            f"Vente domestique France : TVA française {tax_rate}% à déclarer sur CA3."
            if is_fr else
            f"Vente domestique {sale.stock_country} : TVA {tax_rate}%. "
            f"Immatriculation TVA locale requise en {sale.stock_country}."
        )
        return VatResult(
            sale=sale,
            scenario=Scenario.DOMESTIC,
            vat_country=sale.stock_country,
            vat_rate=tax_rate,
            vat_amount=tax_amount,
            collector=Collector.SELLER,
            channel=channel,
            note=note,
        )
    else:
        # Import hors-UE > 150 EUR : deux sous-cas selon qui est l'importateur.
        if sale.seller_is_importer:
            # DDP (Delivered Duty Paid) : le vendeur dédouane la marchandise,
            # la vente redevient une livraison locale dans le pays de destination.
            # Une immatriculation TVA locale dans ce pays est obligatoire.
            is_dest_fr = sale.buyer_country == "FR"
            channel = Channel.FR_DOMESTIC if is_dest_fr else Channel.LOCAL_REGISTRATION
            note = (
                f"Import > {IOSS_THRESHOLD} EUR, vendeur importateur officiel (DDP) : "
                f"vente requalifiée en livraison domestique {sale.buyer_country}. "
                f"TVA locale {tax_rate}% — "
                + (
                    "déclaration CA3 (France)."
                    if is_dest_fr else
                    f"immatriculation TVA locale requise en {sale.buyer_country}."
                )
            )
            return VatResult(
                sale=sale,
                scenario=Scenario.IMPORT_SELLER_AS_IMPORTER,
                vat_country=sale.buyer_country,
                vat_rate=tax_rate,
                vat_amount=tax_amount,
                collector=Collector.SELLER,
                channel=channel,
                note=note,
            )
        else:
            # Régime standard : TVA d'importation due à la douane par l'acheteur.
            return VatResult(
                sale=sale,
                scenario=Scenario.IMPORT_STANDARD,
                vat_country=sale.buyer_country,
                vat_rate=tax_rate,
                vat_amount=tax_amount,
                collector=Collector.BUYER,
                channel=Channel.NONE,
                note=(
                    f"Import > {IOSS_THRESHOLD} EUR depuis pays tiers : TVA d'importation "
                    f"{sale.buyer_country} ({tax_rate}%) due a la douane par l'importateur "
                    "(hors guichet IOSS)."
                ),
            )


def _oss_eligible(sale: Sale) -> bool:
    """Vrai si une vente (ou un avoir) entre dans le calcul du seuil OSS 10 000 €.

    Critères art. 59 ter directive 2006/112/CE :
      - acheteur B2C
      - stock ET acheteur dans l'UE
      - vente cross-border (stock_country ≠ buyer_country)
    Les avoirs (amount_ht < 0) sont éligibles et réduisent le cumul.
    """
    return (
        sale.buyer_type == BuyerType.B2C
        and is_eu(sale.stock_country)
        and is_fiscal_eu(sale.buyer_country, sale.arrival_post_code or None)
        and sale.stock_country != sale.buyer_country
    )


def _build_oss_note(res: VatResult, cumulative: Decimal, limit: Decimal,
                    sale: Sale, product_category: str,
                    apply_fr_under_threshold: bool) -> VatResult:
    """Applique la logique du seuil OSS à un VatResult déjà calculé.

    - Sous le seuil et option FR activée → reclassifie en DOMESTIC FR.
    - Vente de franchissement → ajoute note d'alerte.
    - Sinon → retourne le résultat inchangé.

    Cette fonction est la source unique de la logique OSS partagée entre
    compute_all() et compute_all_with_vies() — corriger ici corrige les deux.
    """
    if not apply_fr_under_threshold:
        return res

    prev_cumul = cumulative - sale.amount_ht   # cumul AVANT cette vente

    if cumulative <= Decimal("10000.00"):
        # Encore sous le seuil : TVA FR.
        # On force "FR" comme pays de taxation — le vendeur assujetti en France
        # déclare cette TVA sur sa CA3 française, indépendamment de seller_country
        # (qui peut être mal renseigné dans un fichier tiers ou vide).
        # Recalcul local de la date (sale disponible dans ce scope)
        _oss_tx_date = None
        if sale.transaction_date:
            try:
                from datetime import date as _d
                _oss_tx_date = _d.fromisoformat(sale.transaction_date[:10])
            except ValueError:
                pass
        fr_rate = vat_rate("FR", product_category, tx_date=_oss_tx_date)
        fr_vat_amount = _vat_amount(sale.amount_ht, fr_rate)
        return VatResult(
            sale=sale, scenario=Scenario.DOMESTIC,
            vat_country="FR",
            vat_rate=fr_rate, vat_amount=fr_vat_amount,
            collector=Collector.SELLER, channel=Channel.FR_DOMESTIC,
            note=(
                f"Sous le seuil OSS ({cumulative:,.2f}/{Decimal('10000.00'):,.2f}€). "
                "Option TVA FR activée."
            ),
        )
    elif prev_cumul <= Decimal("10000.00"):
        # Cette vente est celle qui franchit le seuil : alerte
        return VatResult(
            sale=res.sale, scenario=res.scenario, vat_country=res.vat_country,
            vat_rate=res.vat_rate, vat_amount=res.vat_amount,
            collector=res.collector, channel=res.channel,
            note=f"FRANCHISSEMENT DU SEUIL OSS ! Vente vers {res.vat_country}.",
        )
    return res


def _year_of(sale: Sale) -> str:
    """Extrait l'année YYYY d'une transaction_date 'YYYY-MM-DD'. Retourne '' si absent."""
    d = sale.transaction_date or ""
    return d[:4] if len(d) >= 4 else ""


def _run_oss_loop(
    sorted_items: list[Sale],
    refund_ids: set[int],
    marketplace_name: str,
    asin_to_category: dict[str, str],
    apply_fr_under_threshold: bool,
    effective_sale_fn=None,
) -> tuple[list[VatResult], OssThresholdSummary]:
    """Boucle chronologique OSS partagée entre compute_all et compute_all_with_vies.

    Factorise le reset annuel du cumul OSS, l'éligibilité OSS, le build de la
    note sous/sur seuil, et la construction de OssThresholdSummary.

    Args:
        sorted_items       : ventes + avoirs triés chronologiquement.
        refund_ids         : set d'id() Python des objets avoirs.
        marketplace_name   : nom de la marketplace (pour compute_vat).
        asin_to_category   : mapping ASIN → catégorie produit.
        apply_fr_under_threshold : appliquer TVA FR sous le seuil OSS.
        effective_sale_fn  : callable(sale, product_category) → Sale modifié.
                             Utilisé par compute_all_with_vies pour injecter
                             les reclassifications VIES. None = identité.

    Returns:
        (results, oss_summary)
    """
    results: list[VatResult] = []
    cumulative_oss_ht = Decimal("0.00")
    current_year = ""
    oss_ht_by_year: dict[str, Decimal] = {}

    for sale in sorted_items:
        is_from_refunds = id(sale) in refund_ids
        product_asin = getattr(sale, "asin", "")
        product_category = (
            asin_to_category.get(product_asin, "")
            or asin_to_category.get(product_asin.upper(), "STANDARD")
        )

        # Reset annuel du cumul OSS (art. 59 ter directive 2006/112/CE).
        year = _year_of(sale)
        if year and year != current_year:
            if current_year:
                oss_ht_by_year[current_year] = cumulative_oss_ht
                logger.info(
                    "Changement d'année %s → %s : cumul OSS remis à zéro "
                    "(était %.2f €).", current_year, year, cumulative_oss_ht
                )
            current_year = year
            cumulative_oss_ht = oss_ht_by_year.get(year, Decimal("0.00"))

        # Injection des reclassifications VIES si fournie (compute_all_with_vies).
        effective_sale = (
            effective_sale_fn(sale, product_category)
            if effective_sale_fn is not None
            else sale
        )

        res = compute_vat(effective_sale, marketplace_name, product_category=product_category)

        if _oss_eligible(effective_sale):
            cumulative_oss_ht += effective_sale.amount_ht
            if not is_from_refunds:
                res = _build_oss_note(
                    res, cumulative_oss_ht, Decimal("10000.00"),
                    effective_sale, product_category, apply_fr_under_threshold,
                )

        if not is_from_refunds:
            results.append(res)

    if current_year:
        oss_ht_by_year[current_year] = cumulative_oss_ht

    oss_summary = OssThresholdSummary(
        total_oss_ht=cumulative_oss_ht,
        is_threshold_exceeded=any(v > Decimal("10000.00") for v in oss_ht_by_year.values()),
        oss_ht_by_year=oss_ht_by_year,
    )
    return results, oss_summary


def compute_all(
    sales: list[Sale],
    marketplace_name: str = "Amazon",
    asin_to_category: dict[str, str] = None,
    apply_fr_under_threshold: bool = False,
    refunds: list[Sale] | None = None,
) -> tuple[list[VatResult], OssThresholdSummary]:
    """Calcule la TVA pour une liste de ventes en gérant le seuil OSS 10 000 €.

    Le seuil OSS est annuel (1er janvier — 31 décembre, art. 59 ter directive
    2006/112/CE). Si le fichier couvre plusieurs années civiles, le cumul
    est remis à zéro au changement d'année.

    Les remboursements sont intégrés chronologiquement : un avoir réduit
    le cumul OSS au moment où il se produit.

    Args:
        refunds: avoirs (amount_ht négatif). Intégrés chronologiquement —
                 ne sont PAS ajoutés à `results` (traités séparément dans app.py).
    """
    if asin_to_category is None:
        asin_to_category = {}
    refund_ids: set[int] = {id(r) for r in (refunds or [])}
    all_items = list(sales) + list(refunds or [])
    sorted_items = sorted(all_items, key=lambda s: s.transaction_date or "")
    return _run_oss_loop(
        sorted_items, refund_ids, marketplace_name,
        asin_to_category, apply_fr_under_threshold,
    )


@dataclass
class ViesReclassification:
    """Detail d'une vente B2B reclassifiee en B2C par la validation VIES."""
    sale_id: str
    buyer_vat_number: str
    buyer_country: str
    amount_ht: Decimal
    vat_avoided: Decimal  # TVA récupérée (0 si vente domestique déjà taxée)
    reason: str
    vat_delta: Decimal = Decimal("0.00")  # TVA supplémentaire générée par la reclassification
    is_domestic_reverse_charge: bool = False  # True = autoliquidation nationale (art.194)


@dataclass
class ViesValidationSummary:
    """Synthese de la validation VIES pour affichage dans le rapport."""
    total_checked: int = 0
    valid_count: int = 0
    invalid_count: int = 0
    inconclusive_count: int = 0   # erreurs temporaires après retry — résultat non fiable
    inconclusive_vats: list[str] = field(default_factory=list)  # numéros à revérifier
    # Détail enrichi pour affichage : {"vat": full_vat, "country": cc,
    # "sale_ids": [sale_id, ...]} — un numéro peut couvrir plusieurs ventes.
    inconclusive_vat_details: list[dict] = field(default_factory=list)
    reclassifications: list[ViesReclassification] = field(default_factory=list)
    # Identité Python (id()) des objets Sale (effective_sale) B2B
    # cross-border dont le résultat fiscal a été déterminé par le statut
    # VIES (invalide confirmé OU non vérifié).
    #
    # IMPORTANT : on utilise id(effective_sale) et NON sale.sale_id, car
    # sale_id (transaction_event_id / order_id côté Amazon) n'est PAS unique
    # — une même commande multi-articles ou un même avoir peut être splitté
    # sur plusieurs lignes partageant le même sale_id. Utiliser sale_id
    # ferait "fuiter" le statut VIES d'une ligne vers toutes les autres
    # lignes partageant le même identifiant, faussant l'audit.
    #
    # Utilisé par excel_report.py pour distinguer "Risque VIES réel" des
    # autres écarts Amazon (B2B domestique, B2C, etc.) sans rapport avec VIES.
    vies_affected_sale_ids: set[int] = field(default_factory=set)

    @property
    def total_valid(self) -> int:
        return self.valid_count

    @property
    def total_invalid(self) -> int:
        return self.invalid_count

    @property
    def total_inconclusive(self) -> int:
        return self.inconclusive_count

    @property
    def fraud_avoided_amount(self) -> Decimal:
        return sum((r.vat_avoided for r in self.reclassifications), Decimal("0.00"))

    @property
    def fraud_avoided_ht(self) -> Decimal:
        return sum((r.amount_ht for r in self.reclassifications), Decimal("0.00"))


def compute_all_with_vies(
    sales: list[Sale],
    scope_id: str,
    asin_to_category: dict[str, str] = None,
    on_invalid: str = "reclassify",
    marketplace_name: str = "Amazon",
    check_vies_func=None,  # Conservé pour ne pas faire planter app.py
    apply_fr_under_threshold: bool = False,
    refunds: list[Sale] | None = None,
    vies_progress_callback=None,
) -> tuple[list[VatResult], ViesValidationSummary, OssThresholdSummary]:
    """Calcule la TVA avec validation VIES en gérant le seuil de 10 000 € OSS.
    
    Args:
        scope_id: portée de cache VIES du compte appelant (voir
                  vies.resolve_scope_id) — isole le cache et l'historique
                  d'audit entre comptes/domaines, transmise telle quelle à
                  validate_vat_numbers_parallel et get_manual_overrides.
        vies_progress_callback: optionnel, callable(done: int, total: int)
                  appelé pendant la validation VIES en lot, pour afficher
                  une progression côté app.py (ex: st.progress).
        refunds: liste des remboursements (montants négatifs). S'ils sont fournis,
                 leur montant OSS-éligible est déduit du cumul pour que le seuil
                 affiché reflète le CA OSS net (conformément à l'art. 59 ter directive TVA).
    """
    if asin_to_category is None:
        asin_to_category = {}

    # IMPORT DIRECT DE TON MODULE VIES
    from .vies import validate_vat_numbers_parallel, _is_unreliable as _vies_is_unreliable

    vies_summary = ViesValidationSummary()

    # Tri chronologique sur les VENTES UNIQUEMENT pour construire l'index VIES.
    # Les avoirs n'ont pas de numéro TVA acheteur à valider.
    sorted_sales = sorted(sales, key=lambda s: s.transaction_date or "")

    # ------------------------------------------------------------------------
    # PREPARATION : normalisation des numéros TVA + index sale_id -> full_vat
    # On construit l'index ici pour éviter de recalculer full_vat dans la boucle
    # principale (source du bug de non-matching).
    # ------------------------------------------------------------------------
    vats_to_check = []
    vat_seen = set()
    # Clé composite (sale_id, buyer_vat_number) → full_vat normalisé.
    # sale_id seul n'est pas unique (commandes multi-articles / avoirs partagent
    # le même identifiant) ; l'ajout du numéro TVA brut garantit l'unicité de
    # la correspondance vente ↔ résultat VIES.
    sale_vat_index: dict[tuple[str, str], str] = {}  # (sale_id, buyer_vat_number) -> full_vat

    # _normalize_full_vat est la fonction canonique définie dans vies.py
    # et importée en tête de module comme _normalize_full_vat_canonical.
    _normalize_full_vat = _normalize_full_vat_canonical

    vat_to_sale_ids: dict[str, list[str]] = {}  # full_vat -> [sale_id, ...]

    for sale in sorted_sales:
        if sale.buyer_type == BuyerType.B2B and sale.buyer_vat_number:
            full_vat = _normalize_full_vat(sale.buyer_vat_number, sale.buyer_country)
            sale_vat_index[(sale.sale_id, sale.buyer_vat_number)] = full_vat
            if full_vat:
                vat_to_sale_ids.setdefault(full_vat, []).append(sale.sale_id)
                if full_vat not in vat_seen:
                    vat_seen.add(full_vat)
                    vats_to_check.append(full_vat)

    # Appel de la validation VIES parallèle (validate_vat_numbers_parallel importée
    # en tête de fonction depuis vies.py). En cas d'erreur réseau ou VIES indisponible,
    # on dégrade vers la version séquentielle, puis vers un dict vide avec log explicite.
    checked_vats: dict = {}
    if vats_to_check:
        try:
            checked_vats = validate_vat_numbers_parallel(
                scope_id, vats_to_check, progress_callback=vies_progress_callback
            )
        except Exception as exc_parallel:
            logger.warning(
                "validate_vat_numbers_parallel a échoué (%s) — "
                "tentative avec validate_vat_numbers (séquentiel).",
                exc_parallel,
            )
            try:
                from .vies import validate_vat_numbers
                checked_vats = validate_vat_numbers(
                    scope_id, vats_to_check, progress_callback=vies_progress_callback
                )
            except Exception as exc_seq:
                logger.error(
                    "Validation VIES entièrement indisponible (%s). "
                    "Toutes les ventes B2B seront traitées sans validation — "
                    "aucune reclassification ne sera effectuée.",
                    exc_seq,
                )
                checked_vats = {}

    # Injection des classifications manuelles (overrides utilisateur).
    # Elles écrasent le résultat VIES pour les numéros non vérifiables (inconclusifs).
    # get_manual_overrides() renvoie {full_vat: True|False}.
    try:
        from .vies import get_manual_overrides
        from types import SimpleNamespace as _SN
        for _fv, _is_valid in get_manual_overrides(scope_id).items():
            # On surcharge même si le numéro n'était pas dans le batch
            # (cas où l'override a été posé avant l'upload du fichier)
            if _fv in checked_vats or _fv in vat_seen:
                checked_vats[_fv] = _SN(
                    valid=_is_valid,
                    error=None,
                    name="[Classification manuelle]",
                    address="",
                )
    except Exception as exc_overrides:
        logger.warning(
            "Impossible de charger les overrides manuels VIES (%s). "
            "Les classifications manuelles ne seront pas appliquées.",
            exc_overrides,
        )

    # Compteurs sur numéros UNIQUES (pas par vente)
    vies_summary.total_checked = len(vat_seen)
    for fv, vr in checked_vats.items():
        if getattr(vr, "valid", False):
            vies_summary.valid_count += 1
        elif _vies_is_unreliable(vr):
            vies_summary.inconclusive_count += 1
            vies_summary.inconclusive_vats.append(fv)
            vies_summary.inconclusive_vat_details.append({
                "vat": fv,
                "country": fv[:2] if len(fv) >= 2 and fv[:2].isalpha() else "",
                "sale_ids": vat_to_sale_ids.get(fv, []),
            })
        else:
            vies_summary.invalid_count += 1

    # -----------------------------------------------------------------------
    # Boucle principale : classification VIES + OSS via _run_oss_loop
    # La logique VIES est encapsulée dans effective_sale_fn (closure) ;
    # le reset annuel OSS, l'éligibilité et le build note sont délégués
    # à _run_oss_loop pour éviter la duplication avec compute_all().
    # -----------------------------------------------------------------------

    # État mutable partagé avec la closure (suivi des reclassifications)
    _vies_state = {"last_classified_sale_id": None}

    def _effective_sale_with_vies(sale: Sale, product_category: str) -> Sale:
        """Applique la classification VIES sur la vente et retourne l'objet effectif."""
        # Les avoirs ne passent pas par VIES (leur numéro a déjà été traité).
        if id(sale) in refund_ids:
            return sale
        if not (sale.buyer_type == BuyerType.B2B and sale.buyer_vat_number):
            return sale

        product_asin = getattr(sale, "asin", "")
        full_vat = sale_vat_index.get((sale.sale_id, sale.buyer_vat_number), "")
        vies_res = checked_vats.get(full_vat) if full_vat else None
        is_valid = getattr(vies_res, "valid", False) if vies_res else False
        is_inconclusive = (
            vies_res is not None and not is_valid and _vies_is_unreliable(vies_res)
        )

        if is_valid:
            effective = _dc_replace(sale, buyer_vat_valid=True,
                                    product_category=product_category, asin=product_asin)
        elif is_inconclusive:
            effective = _dc_replace(sale, buyer_vat_valid=False,
                                    product_category=product_category, asin=product_asin)
            if sale.stock_country != sale.buyer_country:
                vies_summary.vies_affected_sale_ids.add(id(effective))
        else:
            chosen_type = BuyerType.B2C if on_invalid == "reclassify" else BuyerType.B2B
            if on_invalid == "reclassify":
                vies_summary.reclassifications.append(ViesReclassification(
                    sale_id=sale.sale_id, buyer_vat_number=sale.buyer_vat_number,
                    buyer_country=sale.buyer_country, amount_ht=sale.amount_ht,
                    vat_avoided=Decimal("0.00"), reason="Numéro invalide ou introuvable",
                ))
            effective = _dc_replace(sale, buyer_type=chosen_type, buyer_vat_valid=False,
                                    product_category=product_category, asin=product_asin)
            if sale.stock_country != sale.buyer_country:
                vies_summary.vies_affected_sale_ids.add(id(effective))

        _vies_state["last_classified_sale_id"] = sale.sale_id
        return effective

    refund_ids: set[int] = {id(r) for r in (refunds or [])}
    all_items = list(sales) + list(refunds or [])
    all_items_sorted = sorted(all_items, key=lambda s: s.transaction_date or "")

    results, oss_summary = _run_oss_loop(
        all_items_sorted, refund_ids, marketplace_name,
        asin_to_category, apply_fr_under_threshold,
        effective_sale_fn=_effective_sale_with_vies,
    )

    # Mise à jour des montants TVA évités dans les reclassifications
    # (on ne peut le faire qu'après compute_vat, donc en post-processing sur results).
    result_by_sale_id: dict[str, VatResult] = {r.sale.sale_id: r for r in results}
    for i, reclass in enumerate(vies_summary.reclassifications):
        res = result_by_sale_id.get(reclass.sale_id)
        if res is None:
            continue
        is_cross_border = res.sale.stock_country != res.sale.buyer_country
        real_vat_avoided = res.vat_amount if is_cross_border else Decimal("0.00")
        is_dom_rc = (
            not is_cross_border
            and res.sale.stock_country in DOMESTIC_REVERSE_CHARGE_COUNTRIES
        )
        vies_summary.reclassifications[i] = ViesReclassification(
            sale_id=reclass.sale_id,
            buyer_vat_number=reclass.buyer_vat_number,
            buyer_country=reclass.buyer_country,
            amount_ht=reclass.amount_ht,
            vat_avoided=real_vat_avoided,
            reason=reclass.reason,
            vat_delta=real_vat_avoided,
            is_domestic_reverse_charge=is_dom_rc,
        )

    return results, vies_summary, oss_summary