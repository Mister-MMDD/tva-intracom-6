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
from dataclasses import replace as _dc_replace
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
    ViesReclassification,
    ViesValidationSummary,
)
from .rates import is_eu, is_fiscal_eu, is_non_fiscal_eu, vat_rate
from .rates import DOMESTIC_REVERSE_CHARGE_COUNTRIES, oss_threshold_in_currency, OSS_THRESHOLD_FIXED_EQUIVALENTS
from datetime import date as _date
from .vies_engine import normalize_full_vat as _normalize_full_vat_canonical


def _note(fr_text: str, key: str, lang: str = "fr", **kwargs) -> str:
    """Texte de VatResult.note.

    En français : texte complet avec références légales.
    Dans les autres langues : note générique minimale.
    
    IMPORTANT : 'lang' doit être passé explicitement pour éviter les appels 
    à st.session_state dans les threads d'arrière-plan.
    """
    if lang == "fr":
        return fr_text
    from .i18n import _ as _i18n
    return _i18n(key, lang=lang, **kwargs)


def _resolve_lang() -> str:
    """Fallback pour usage hors boucle / bibliothèque."""
    try:
        import streamlit as _st
        # On n'accède à st.session_state QUE si on a un ScriptRunContext
        from streamlit.runtime.scriptrunner import get_script_run_ctx
        if get_script_run_ctx():
            return _st.session_state.get("language", "fr")
    except Exception:
        pass
    return "fr"

# Seuil de valeur intrinseque d'un envoi pour le regime IOSS (import).
IOSS_THRESHOLD = Decimal("150")
_CENT = Decimal("0.01")

def _round(amount: Decimal) -> Decimal:
    return amount.quantize(_CENT, rounding=ROUND_HALF_UP)

def _vat_amount(base: Decimal, rate: Decimal) -> Decimal:
    return _round(base * (rate / Decimal("100")))

def compute_vat(sale: Sale, marketplace_name: str = "Amazon", product_category: str = "", lang: str | None = None) -> VatResult:
    """Calcule le regime et le montant de TVA d'une vente en prenant en compte la catégorie produit."""
    # Résolu une seule fois par appel si l'appelant (ex: _run_oss_loop) ne l'a
    # pas déjà résolu pour tout le lot — évite un lookup Streamlit par vente
    # en usage isolé/bibliothèque, tout en restant rétro-compatible.
    if lang is None:
        lang = _resolve_lang()

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

    # Date de transaction (déplacée ici, avant le cas Monaco ET le cas export,
    # pour que les deux puissent appliquer un taux historique correct — ex:
    # changement de taux FR au fil du temps).
    _tx_date: _date | None = None
    if sale.transaction_date:
        try:
            _tx_date = _date.fromisoformat(sale.transaction_date[:10])
        except ValueError:
            pass  # date malformée → taux courant (pas de correctif historique)

    # ------------------------------------------------------------------
    # Monaco (MC) : assimilé au territoire français pour la TVA (convention
    # fiscale franco-monégasque du 18 mai 1963, droits indirects). Sans ce
    # cas spécial, "MC" n'étant reconnu ni par is_eu() ni par is_fiscal_eu(),
    # une vente vers Monaco tomberait à tort dans EXPORT (exonérée) alors
    # qu'elle doit être taxée comme une vente domestique française standard.
    # ------------------------------------------------------------------
    if sale.buyer_country == "MC":
        mc_rate = vat_rate("FR", effective_category, tx_date=_tx_date)
        mc_amount = _vat_amount(sale.amount_ht, mc_rate)
        
        if sale.stock_country == "FR":
            # Si le vendeur est établi en France, c'est du domestique FR_DOMESTIC
            # Sinon, c'est du LOCAL_REGISTRATION en France.
            is_home = sale.stock_country == sale.seller_country
            channel = Channel.FR_DOMESTIC if is_home else Channel.LOCAL_REGISTRATION
            
            return VatResult(
                sale=sale,
                scenario=Scenario.DOMESTIC,
                vat_country="FR",
                vat_rate=mc_rate,
                vat_amount=mc_amount,
                collector=Collector.SELLER,
                channel=channel,
                note=_note(
                    "Vente vers Monaco depuis un stock français : assimilée à une "
                    "vente domestique française (convention fiscale franco-monégasque "
                    "du 18 mai 1963 — https://bit.ly/Conv-FR-MC) — TVA FR "
                    f"{mc_rate}% collectée.",
                    "engine_note_monaco_home", lang=lang, rate=mc_rate,
                ),
            )
        else:
            # Cas stock_country != "FR" (ex: ES -> MC)
            # Monaco étant fiscalement la France, c'est une vente OSS vers la France.
            return VatResult(
                sale=sale,
                scenario=Scenario.OSS_B2C,
                vat_country="FR",
                vat_rate=mc_rate,
                vat_amount=mc_amount,
                collector=Collector.SELLER,
                channel=Channel.OSS,
                note=_note(
                    f"Vente vers Monaco depuis un stock {sale.stock_country} : "
                    "assimilée à une vente OSS vers la France (Convention fiscale "
                    "franco-monégasque — Monaco est traité comme le territoire "
                    f"français pour la TVA) — TVA FR {mc_rate}%.",
                    "engine_note_monaco_oss", lang=lang, stock=sale.stock_country, rate=mc_rate,
                ),
            )

    # ------------------------------------------------------------------
    # SÉCURITÉ IMMÉDIATE : Cas d'exportation hors UE (ex: GB, US...)
    # On traite ce cas EN PREMIER pour éviter d'interroger vat_rate inutilement
    # ------------------------------------------------------------------
    if not buyer_eu:
        # On affine la note selon que le pays est hors-UE ou s'il s'agit d'un 
        # territoire d'un pays membre exclu du territoire fiscal (ex: Canaries).
        is_excl_territory = is_eu(sale.buyer_country) and is_non_fiscal_eu(sale.buyer_country, sale.arrival_post_code)
        prefix_note = (
            "Territoire exclu du territoire fiscal de l'UE" 
            if is_excl_territory 
            else "Exportation hors UE"
        )
        return VatResult(
            sale=sale,
            scenario=Scenario.EXPORT,
            vat_country="",
            vat_rate=Decimal("0"),
            vat_amount=Decimal("0.00"),
            collector=Collector.SELLER,
            channel=Channel.EXONERATION,
            note=_note(
                f"{prefix_note} : exonérée de TVA (Art. 262 du CGI — "
                "https://bit.ly/Art262CGI). Justificatif de sortie du "
                "territoire requis.",
                "engine_note_export", lang=lang,
            ),
        )

    # 1. Calcul du taux dynamique basé sur le pays, la catégorie et la date
    # La date de transaction est utilisée pour appliquer le taux historique correct
    # (ex: EE 22% avant juil.2025, RO 19% avant août 2025).
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
            note=_note(
                f"Import ≤ {IOSS_THRESHOLD} EUR : TVA {tax_rate}% collectée par le vendeur "
                f"via son guichet IOSS ({sale.ioss_number}) — déclaration sur portail IOSS "
                "(BOI-TVA-CHAMP-20-20-30 — https://bit.ly/Bofip-IOSS).",
                "engine_note_ioss_direct", lang=lang, rate=tax_rate, ioss=sale.ioss_number,
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
                channel=Channel.EXONERATION,
                note=_note(
                    f"{marketplace_name} collecte la TVA ({tax_rate}%) sur {sale.buyer_country}.",
                    "engine_note_deemed_supplier", lang=lang, platform=marketplace_name, rate=tax_rate, country=sale.buyer_country,
                )
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
                channel=Channel.EXONERATION,
                note=_note(
                    "Livraison intracommunautaire B2B exonérée avec autoliquidation "
                    "par l'acquéreur (Art. 262 ter du CGI — https://bit.ly/Art262ter).",
                    "engine_note_b2b_reverse_charge", lang=lang,
                )
            )

        # B2B cross-border sans TVA intracom valide (buyer_vat_valid=False) :
        # La livraison ne peut pas être exonérée (Art. 138 Directive 2006/112/CE).
        #
        # L'art.194 dir. 2006/112/CE (autoliquidation domestique) n'a AUCUN effet
        # en cross-border — c'était l'erreur historique (exonération à tort). Il
        # ne sert ici qu'à identifier les pays où l'ancien moteur appliquait à
        # tort cette exonération (ES, IT, PL, CZ, SK, HU, RO…) :
        #
        #   a) buyer_country dans cette liste : on corrige l'ancienne exonération
        #      à tort. Le lieu de livraison reste le pays de départ (Art. 31
        #      Directive 2006/112/CE) → le vendeur collecte la TVA de départ.
        #
        #   b) buyer_country hors de cette liste : pas d'exonération à corriger
        #      ici — la vente est simplement reclassifiée B2C (numéro TVA
        #      invalide = pas de preuve de statut assujetti) et suit le régime
        #      normal des ventes à distance (Art. 33) : OSS, taxation à
        #      destination — exactement comme n'importe quelle vente B2C
        #      cross-border (voir Cas 1 plus bas).
        #
        # Note : Si la vente est reclassifiée en B2C par le moteur VIES, elle basculera
        # alors dans le régime OSS (TVA destination) — voir bloc Cas 1 plus bas.
        if stock_eu and buyer_eu and cross_border:
            if sale.buyer_country in DOMESTIC_REVERSE_CHARGE_COUNTRIES:
                departure_rate = vat_rate(sale.stock_country, effective_category, tx_date=_tx_date)
                departure_amount = _vat_amount(sale.amount_ht, departure_rate)

                is_stock_home = sale.stock_country == sale.seller_country
                channel = Channel.FR_DOMESTIC if is_stock_home else Channel.LOCAL_REGISTRATION

                return VatResult(
                    sale=sale,
                    scenario=Scenario.DOMESTIC,
                    vat_country=sale.stock_country,
                    vat_rate=departure_rate,
                    vat_amount=departure_amount,
                    collector=Collector.SELLER,
                    channel=channel,
                    note=_note(
                        f"Vente B2B cross-border {sale.stock_country}→{sale.buyer_country} : "
                        f"numéro TVA acheteur non valide VIES. L'art.194 (adopté en "
                        f"{sale.buyer_country}) ne s'applique qu'au national, pas en "
                        f"cross-border — l'exonération est refusée (Art. 138 Directive "
                        f"2006/112/CE) — taxation au pays de départ ({sale.stock_country}) "
                        f"au taux de {departure_rate}% collecté par le vendeur.",
                        "engine_note_b2b_no_vies_departure", lang=lang, stock=sale.stock_country,
                        buyer=sale.buyer_country, rate=departure_rate,
                    ),
                )
            else:
                # Le numéro TVA est invalide : l'exonération B2B (Art. 138) est
                # refusée. Contrairement à la branche ci-dessus, le pays de
                # destination n'a pas de régime d'autoliquidation généralisée
                # concurrent à écarter — il n'y a donc pas d'obstacle à traiter
                # la vente comme une vente à distance B2C classique (Art. 33
                # Directive 2006/112/CE) : la vente est reclassifiée B2C et
                # suit le régime OSS, taxée au pays de destination.
                return VatResult(
                    sale=sale,
                    scenario=Scenario.OSS_B2C,
                    vat_country=sale.buyer_country,
                    vat_rate=tax_rate,
                    vat_amount=tax_amount,
                    collector=Collector.SELLER,
                    channel=Channel.OSS,
                    note=_note(
                        f"Vente B2B cross-border {sale.stock_country}→{sale.buyer_country} : "
                        f"numéro TVA acheteur non valide VIES. L'exonération est refusée "
                        f"(Art. 138 Directive 2006/112/CE) — la vente est reclassifiée B2C "
                        f"et taxée au pays de destination ({sale.buyer_country}) au taux de "
                        f"{tax_rate}% via le régime OSS (BOI-TVA-CHAMP-20-20-30 — "
                        f"https://bit.ly/Bofip-OSS).",
                        "engine_note_b2b_no_vies_destination_oss", lang=lang, stock=sale.stock_country,
                        buyer=sale.buyer_country, rate=tax_rate,
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
            note=_note(
                f"Vente OSS vers {sale.buyer_country} au taux de {tax_rate}% "
                "(BOI-TVA-CHAMP-20-20-30 — https://bit.ly/Bofip-OSS).",
                "engine_note_oss_b2c", lang=lang, country=sale.buyer_country, rate=tax_rate,
            )
    )

    # ------------------------------------------------------------------
    # Fin de fonction : Différenciation Vente Locale / Importation
    # ------------------------------------------------------------------
    is_domestic = sale.stock_country == sale.buyer_country
    
    if is_domestic:
        # is_home : le stock est dans le pays d'origine (établissement) du
        # vendeur — sale.seller_country, pas littéralement "FR" (réglage de
        # compte global, voir auth.py). Nommé is_fr historiquement, renommé
        # is_home pour éviter toute confusion : reste vrai pour un vendeur
        # français par défaut (seller_country="FR"), mais se généralise à
        # tout pays d'origine choisi par le compte.
        is_home = sale.stock_country == sale.seller_country
        is_fr = is_home  # alias conservé pour lisibilité du reste du bloc

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
                channel=Channel.EXONERATION,
                note=_note(
                    f"Vente B2B domestique {sale.stock_country} : autoliquidation nationale. "
                    f"L'acheteur assujetti (n° {'TVA: ' + sale.buyer_vat_number if sale.buyer_vat_number else 'inconnu'}) "
                    f"déclare et reverse la TVA — le vendeur ne collecte pas.",
                    "engine_note_b2b_domestic_rc", lang=lang, country=sale.stock_country,
                ),
            )

        channel = Channel.FR_DOMESTIC if is_fr else Channel.LOCAL_REGISTRATION
        note = (
            _note(
                f"Vente domestique {sale.seller_country} : TVA {tax_rate}% à déclarer en local.",
                "engine_note_domestic_home", lang=lang, country=sale.seller_country, rate=tax_rate,
            )
            if is_fr else
            _note(
                f"Vente domestique {sale.stock_country} : TVA {tax_rate}%. "
                f"Immatriculation TVA locale requise en {sale.stock_country}.",
                "engine_note_domestic_local", lang=lang, country=sale.stock_country, rate=tax_rate,
            )
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
            is_dest_home = sale.buyer_country == sale.seller_country
            channel = Channel.FR_DOMESTIC if is_dest_home else Channel.LOCAL_REGISTRATION
            note = _note(
                f"Import > {IOSS_THRESHOLD} EUR, vendeur importateur officiel (DDP) : "
                f"vente requalifiée en livraison domestique {sale.buyer_country}. "
                f"TVA locale {tax_rate}% — "
                + (
                    f"déclaration domestique ({sale.seller_country})."
                    if is_dest_home else
                    f"immatriculation TVA locale requise en {sale.buyer_country}."
                ),
                "engine_note_ddp_import", lang=lang, country=sale.buyer_country, rate=tax_rate, home=sale.seller_country,
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
                channel=Channel.EXONERATION,
                note=_note(
                    f"Import > {IOSS_THRESHOLD} EUR depuis pays tiers : TVA d'importation "
                    f"{sale.buyer_country} ({tax_rate}%) due a la douane par l'importateur "
                    "(hors guichet IOSS).",
                    "engine_note_import_standard", lang=lang, country=sale.buyer_country, rate=tax_rate,
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


def _oss_threshold_display(cumulative_eur: Decimal, currency: str = "EUR", symbol: str = "€") -> tuple[str, str, str]:
    """Cumul et seuil OSS à afficher dans la note. 
    Les paramètres currency et symbol doivent être passés par l'appelant 
    pour éviter d'accéder à st.session_state dans un thread d'arrière-plan.
    """
    if not currency or currency.upper() == "EUR":
        return f"{cumulative_eur:,.2f}", f"{Decimal('10000.00'):,.2f}", "€"

    eur_rate = None
    if currency.upper() not in OSS_THRESHOLD_FIXED_EQUIVALENTS:
        try:
            from .ecb_rates import get_rate
            from datetime import date as _d
            eur_rate = get_rate(currency, _d.today())
        except Exception:
            eur_rate = None

    limit_local = oss_threshold_in_currency(currency, eur_rate)
    _ratio = (limit_local / Decimal("10000.00")) if limit_local else Decimal("1")
    cumulative_local = cumulative_eur * _ratio
    return f"{cumulative_local:,.2f}", f"{limit_local:,.2f}", symbol


def _build_oss_note(res: VatResult, cumulative: Decimal, limit: Decimal,
                    sale: Sale, product_category: str,
                    apply_fr_under_threshold: bool, lang: str | None = None,
                    currency: str = "EUR", symbol: str = "€") -> VatResult:
    """Applique la logique du seuil OSS à un VatResult déjà calculé."""
    if lang is None:
        lang = _resolve_lang()
    if not apply_fr_under_threshold:
        return res

    prev_cumul = cumulative - sale.amount_ht

    if cumulative <= Decimal("10000.00"):
        origin_country = sale.seller_country
        _oss_tx_date = None
        if sale.transaction_date:
            try:
                from datetime import date as _d
                _oss_tx_date = _d.fromisoformat(sale.transaction_date[:10])
            except ValueError:
                pass
        home_rate = vat_rate(origin_country, product_category, tx_date=_oss_tx_date)
        home_vat_amount = _vat_amount(sale.amount_ht, home_rate)
        _cumul_disp, _limit_disp, _sym_disp = _oss_threshold_display(cumulative, currency, symbol)
        return VatResult(
            sale=sale, scenario=Scenario.DOMESTIC,
            vat_country=origin_country,
            vat_rate=home_rate, vat_amount=home_vat_amount,
            collector=Collector.SELLER, channel=Channel.FR_DOMESTIC,
            note=_note(
                f"Sous le seuil OSS ({_cumul_disp}/{_limit_disp}{_sym_disp}). "
                f"Option TVA {origin_country} activée.",
                "engine_note_oss_under_threshold", lang=lang, country=origin_country,
                cumulative=_cumul_disp, limit=_limit_disp, currency=_sym_disp,
            ),
        )
    elif prev_cumul <= Decimal("10000.00"):
        # Cette vente est celle qui franchit le seuil : alerte
        return VatResult(
            sale=res.sale, scenario=res.scenario, vat_country=res.vat_country,
            vat_rate=res.vat_rate, vat_amount=res.vat_amount,
            collector=res.collector, channel=res.channel,
            note=_note(
                f"FRANCHISSEMENT DU SEUIL OSS ! Vente vers {res.vat_country}.",
                "engine_note_oss_threshold_crossed", lang=lang, country=res.vat_country,
            ),
        )
    return res


def _sale_key(sale: Sale) -> tuple[str, str]:
    """Clé composite (sale_id, montant_ht) identifiant une ligne de façon stable.

    Remplace id(sale) : un id() Python n'est valable que tant que l'objet
    Sale d'origine reste physiquement le même jusqu'au point de lecture
    (aucune copie/recréation via dataclasses.replace entre-temps) — une
    contrainte fragile et invisible pour quiconque retouche ce chemin plus
    tard. Le montant (avec son signe : positif=vente, négatif=avoir) évite
    toute collision avec un remboursement partageant le même sale_id.
    """
    return (sale.sale_id, str(sale.amount_ht))


def _year_of(sale: Sale) -> str:
    """Extrait l'année YYYY d'une transaction_date 'YYYY-MM-DD'. Retourne '' si absent."""
    d = sale.transaction_date or ""
    return d[:4] if len(d) >= 4 else ""


def _chronological_sort_key(sale: Sale) -> str:
    """Clé de tri chronologique robuste pour les ventes/avoirs.

    transaction_date est censée être normalisée en amont (detect.parse_date),
    mais deux cas dégradés existent en pratique :
      - date vide (colonne source vide dans le fichier Amazon) ;
      - format non reconnu par parse_date(), renvoyé tel quel sans validation.

    Dans ces deux cas, la valeur ne se compare pas forcément correctement à
    un 'YYYY-MM-DD' — elle est donc écartée du tri normal (fallback
    "9999-12-31", classée en DERNIER, jamais en premier). But : éviter qu'une
    vente à date invalide ne s'intercale silencieusement en tête de flux et
    ne fausse le cumul OSS (reset annuel, seuil 10 000 €) des ventes qui la
    suivent ; classée en dernier, seul son propre traitement est affecté.
    """
    raw = (sale.transaction_date or "")[:10]
    try:
        _date.fromisoformat(raw)
        return raw
    except ValueError:
        return "9999-12-31"


def _run_oss_loop(
    sorted_items: list[Sale],
    refund_ids: set[int],
    marketplace_name: str,
    asin_to_category: dict[str, str],
    apply_fr_under_threshold: bool,
    effective_sale_fn=None,
    lang: str = "fr",
    currency: str = "EUR",
    symbol: str = "€"
) -> tuple[list[VatResult], OssThresholdSummary]:
    """Boucle chronologique OSS."""
    results: list[VatResult] = []
    cumulative_oss_ht = Decimal("0.00")
    current_year = ""
    oss_ht_by_year: dict[str, Decimal] = {}
    
    # On utilise les paramètres passés plutôt que _resolve_lang() pour le thread-safety
    _lang = lang

    for sale in sorted_items:
        is_from_refunds = id(sale) in refund_ids
        product_asin = getattr(sale, "asin", "")
        product_category = (
            asin_to_category.get(product_asin, "")
            or asin_to_category.get(product_asin.upper(), "STANDARD")
        )

        year = _year_of(sale)
        if year and year != current_year:
            if current_year:
                oss_ht_by_year[current_year] = cumulative_oss_ht
            current_year = year
            cumulative_oss_ht = oss_ht_by_year.get(year, Decimal("0.00"))

        effective_sale = (
            effective_sale_fn(sale, product_category)
            if effective_sale_fn is not None
            else sale
        )

        res = compute_vat(effective_sale, marketplace_name, product_category=product_category, lang=_lang)

        if _oss_eligible(effective_sale):
            cumulative_oss_ht += effective_sale.amount_ht
            if not is_from_refunds:
                res = _build_oss_note(
                    res, cumulative_oss_ht, Decimal("10000.00"),
                    effective_sale, product_category, apply_fr_under_threshold, 
                    lang=_lang, currency=currency, symbol=symbol
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
    lang: str = "fr",
    currency: str = "EUR",
    symbol: str = "€",
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
        lang, currency, symbol: contexte de présentation passé explicitement pour 
                 éviter les appels à st.session_state dans les threads.
    """
    if asin_to_category is None:
        asin_to_category = {}

    # IMPORT DIRECT DE TON MODULE VIES
    from .vies_engine import (
        validate_vat_numbers_parallel,
        _is_unreliable as _vies_is_unreliable,
    )

    def _is_uncertain(vr) -> bool:
        """Un résultat VIES est \"incertain\" (à traiter comme B2C par sécurité,
        avec motif affiché) s'il s'agit d'une erreur transitoire explicite."""
        return _vies_is_unreliable(vr)

    vies_summary = ViesValidationSummary()

    # Un seul tri chronologique global (ventes + avoirs).
    refund_ids: set[int] = {id(r) for r in (refunds or [])}
    all_items = list(sales) + list(refunds or [])
    all_items_sorted = sorted(all_items, key=_chronological_sort_key)

    # ------------------------------------------------------------------------
    # PREPARATION : normalisation des numéros TVA + index sale_id -> full_vat
    # On construit l'index ici pour éviter de recalculer full_vat dans la boucle
    # principale (source du bug de non-matching).
    #
    # IMPORTANT (fix comptage VIES) : on parcourt all_items_sorted (ventes +
    # avoirs), PAS uniquement les ventes. Avant ce correctif, un numéro de TVA
    # présent UNIQUEMENT sur un avoir (ex: remboursement d'une vente d'une
    # période antérieure non présente dans l'import courant) n'était jamais
    # ajouté à vats_to_check ici : il n'était donc jamais compté dans
    # vies_summary (le total "vérifiés" affiché à l'écran), alors même qu'il
    # était bel et bien interrogé et écrit en cache (vies_scope_cache) par le
    # second appel dédié aux avoirs dans app.py (compute_all_with_vies(refunds,
    # ...)) — d'où l'écart observé entre le compteur affiché et le certificat
    # PDF (qui lit, lui, TOUT le cache scope via get_scope_vies_snapshot()).
    # Le fait de checker les avoirs ici ne casse pas le compteur OSS cumulatif
    # (_run_oss_loop reste inchangé : is_from_refunds continue d'exclure les
    # avoirs de `results` et de la note OSS) — on ne touche qu'à la collecte
    # des numéros à vérifier / au comptage, pas à la boucle de calcul.
    # ------------------------------------------------------------------------
    vats_to_check = []
    vat_seen = set()
    # NIF/identifiants fiscaux nationaux vus (dédupliqués), pour compter des
    # numéros uniques comme vat_seen et non un par vente.
    national_ids_seen = set()
    # Clé composite (sale_id, buyer_vat_number) → full_vat normalisé.
    # sale_id seul n'est pas unique (commandes multi-articles / avoirs partagent
    # le même identifiant) ; l'ajout du numéro TVA brut garantit l'unicité de
    # la correspondance vente ↔ résultat VIES.
    sale_vat_index: dict[tuple[str, str], str] = {}  # (sale_id, buyer_vat_number) -> full_vat

    # _normalize_full_vat est la fonction canonique définie dans vies.py
    # et importée en tête de module comme _normalize_full_vat_canonical.
    _normalize_full_vat = _normalize_full_vat_canonical

    vat_to_sale_ids: dict[str, list[str]] = {}  # full_vat -> [sale_id, ...]

    for sale in all_items_sorted:
        # buyer_vat_valid=False dès classify.py signale un NIF/identifiant fiscal
        # national (pas un vrai n° de TVA intracom, cf. is_national_tax_id) — que
        # ce soit un cas domestique (buyer_vat_number conservé pour l'autoliquidation
        # art.194) ou cross-border (déjà écarté plus haut via national_tax_id).
        # On ne l'envoie JAMAIS à VIES : ce n'est pas un numéro interrogeable, et
        # l'autoliquidation domestique ne dépend pas de sa validité (engine.py
        # ligne ~375). Sans ce filtre, ces NIF apparaissent à tort dans la liste
        # "N° TVA rejeté" alors qu'ils n'ont jamais été un numéro de TVA valide.
        if sale.buyer_type == BuyerType.B2B and sale.buyer_vat_number and sale.buyer_vat_valid:
            full_vat = _normalize_full_vat(sale.buyer_vat_number, sale.buyer_country)
            sale_vat_index[(sale.sale_id, sale.buyer_vat_number)] = full_vat
            if full_vat:
                # On utilise l'identifiant d'affichage (TRANSACTION_EVENT_ID) s'il existe
                display_label = getattr(sale, "display_id", "") or sale.sale_id
                vat_to_sale_ids.setdefault(full_vat, []).append(display_label)
                if full_vat not in vat_seen:
                    vat_seen.add(full_vat)
                    vats_to_check.append(full_vat)

    vies_summary.vat_to_display_ids = vat_to_sale_ids

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
                from .vies_engine import validate_vat_numbers
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
    # On n'applique l'override QUE si la validation VIES automatique a échoué
    # (inconclusif, erreur serveur ou réponse vide) ou n'a pas été effectuée.
    # Si VIES répond avec un résultat net (Valide ou Invalide), il reprend la priorité
    # et on supprime l'override de la base (nettoyage automatique).
    try:
        from .vies_engine import get_manual_overrides, delete_manual_override
        from types import SimpleNamespace as _SN
        # On récupère tous les overrides, même expirés, pour pouvoir les nettoyer
        _all_overrides = get_manual_overrides(scope_id, include_expired=True)

        for _fv, _is_valid in _all_overrides.items():
            _current_res = checked_vats.get(_fv)
            # Un résultat est considéré comme un "échec de vérification" si :
            # 1. Il est absent (non testé ou erreur fatale)
            # 2. Il est marqué comme non fiable (erreur transitoire/timeout explicite)
            # Une réponse "vide" (VIES répond False sans nom/adresse et sans erreur)
            # n'est PAS un échec : c'est la forme normale et définitive d'un numéro
            # réellement invalide — elle doit être acceptée comme résultat automatique
            # concluant (voir vies_engine.validate_vat_numbers_parallel, qui la met
            # désormais en cache comme telle, protégée par _is_downgrade contre une
            # vraie dégradation silencieuse d'un numéro précédemment valide).
            _is_failed = (
                _current_res is None
                or _is_uncertain(_current_res)
            )

            if _fv in vat_seen and _is_failed:
                checked_vats[_fv] = _SN(
                    valid=_is_valid,
                    error=None,
                    name="[Classification manualle]",
                    address="",
                    stale_fallback=False,
                    is_manual_override=True,
                )
            elif _fv in checked_vats and not _is_failed:
                # VIES a réussi (concluant), on nettoie l'override devenu inutile
                try:
                    delete_manual_override(scope_id, _fv)
                    logger.info("Override VIES [%s] : nettoyage auto car VIES est désormais concluant.", _fv)
                except Exception:
                    pass
    except Exception as exc_overrides:
        logger.warning(
            "Impossible de charger les overrides manuels VIES (%s). "
            "Les classifications manuelles ne seront pas appliquées.",
            exc_overrides,
        )

    # Compteurs sur numéros UNIQUES (pas par vente)
    #
    # Quatre catégories distinctes (voir ViesValidationSummary) :
    #   - valid_count / invalid_count : vérification AUTOMATIQUE fraîche
    #     (VIES ou cache non expiré), seule catégorie fiable à 100%.
    #   - manual_override_count : classification saisie par l'utilisateur,
    #     pas une vérification automatique — comptée à part, jamais fusionnée
    #     avec valid_count/invalid_count pour ne pas gonfler artificiellement
    #     le taux de vérification automatique affiché.
    #   - inconclusive_count : aucun résultat exploitable du tout (ni cache
    #     frais, ni override disponible).
    vies_summary.total_checked = len(vat_seen)
    for fv, vr in checked_vats.items():
        if getattr(vr, "is_manual_override", False):
            vies_summary.manual_override_count += 1
        elif getattr(vr, "valid", False):
            vies_summary.valid_count += 1
        elif _vies_is_unreliable(vr):
            vies_summary.inconclusive_count += 1
            vies_summary.inconclusive_vats.append(fv)
            vies_summary.inconclusive_vat_details.append({
                "vat": fv,
                "country": fv[:2] if len(fv) >= 2 and fv[:2].isalpha() else "",
                "sale_ids": vat_to_sale_ids.get(fv, []),
                "reason": "inconclusive",
            })
        else:
            vies_summary.invalid_count += 1

    # -----------------------------------------------------------------------
    # Boucle principale : classification VIES + OSS via _run_oss_loop
    # La logique VIES est encapsulée dans effective_sale_fn (closure) ;
    # le reset annuel OSS, l'éligibilité et le build note sont délégués
    # à _run_oss_loop.
    # -----------------------------------------------------------------------

    # État mutable partagé avec la closure (suivi des reclassifications)
    _vies_state = {"last_classified_sale_id": None}

    def _effective_sale_with_vies(sale: Sale, product_category: str) -> Sale:
        """Applique la classification VIES sur la vente et retourne l'objet effectif."""
        # Les avoirs ne passent pas par VIES (leur numéro a déjà été traité).
        if id(sale) in refund_ids:
            return sale

        product_asin = getattr(sale, "asin", "")

        # Cas particulier : NIF national sans préfixe EU en cross-border
        # (classify.py Cas 2). buyer_vat_number est vide par construction —
        # aucun appel VIES n'est jamais tenté — mais la vente est bien taxée
        # au départ ou à destination selon le même arbitrage art.194 dans
        # compute_vat. On l'enregistre quand même dans les reclassifications
        # pour qu'elle apparaisse dans l'onglet VIES (sinon invisible).
        if (
            sale.buyer_type == BuyerType.B2B
            and not sale.buyer_vat_number
            and getattr(sale, "national_tax_id", "")
            and sale.stock_country != sale.buyer_country
        ):
            vies_summary.reclassifications.append(ViesReclassification(
                sale_id=sale.sale_id,
                buyer_vat_number=sale.national_tax_id,
                buyer_country=sale.buyer_country,
                amount_ht=sale.amount_ht,
                vat_avoided=Decimal("0.00"),
                reason="Identifiant fiscal national (pas un n° de TVA intracommunautaire)",
                display_id=getattr(sale, "display_id", ""),
                stock_country=sale.stock_country,
            ))
            if sale.national_tax_id not in national_ids_seen:
                national_ids_seen.add(sale.national_tax_id)
                vies_summary.national_id_count += 1
            vies_summary.vies_affected_sale_ids.add(_sale_key(sale))
            _vies_state["last_classified_sale_id"] = sale.sale_id
            return sale

        if not (sale.buyer_type == BuyerType.B2B and sale.buyer_vat_number and sale.buyer_vat_valid):
            return sale
        full_vat = sale_vat_index.get((sale.sale_id, sale.buyer_vat_number), "")
        vies_res = checked_vats.get(full_vat) if full_vat else None
        
        # Un résultat VIES n'est valide que si valid=True.
        is_valid = bool(getattr(vies_res, "valid", False)) if vies_res else False
        is_inconclusive = (
            vies_res is not None and not is_valid
            and _vies_is_unreliable(vies_res)
        )

        if is_valid:
            effective = _dc_replace(sale, buyer_vat_valid=True,
                                    product_category=product_category, asin=product_asin)
        else:
            # Numéro invalide ou inconclusive (service VIES indisponible).
            # On l'ajoute à la liste des anomalies VIES pour affichage dans l'onglet VIES,
            # même si on ne change pas forcément le type en B2C.
            reason = "Numéro invalide ou introuvable"
            if is_inconclusive:
                reason = "Service VIES indisponible (incertain)"
            
            vies_summary.reclassifications.append(ViesReclassification(
                sale_id=sale.sale_id, buyer_vat_number=sale.buyer_vat_number,
                buyer_country=sale.buyer_country, amount_ht=sale.amount_ht,
                vat_avoided=Decimal("0.00"), reason=reason,
                display_id=getattr(sale, "display_id", ""),
                stock_country=sale.stock_country,
            ))

            # IMPORTANT : Pour les ventes B2B cross-border dont le n° TVA est invalide,
            # on ne reclassifie PLUS en B2C. On garde BuyerType. B2B mais avec
            # buyer_vat_valid=False. Cela permet à compute_vat d'appliquer la TVA
            # au départ (Origin VAT) plutôt que l'OSS (Destination VAT).
            # Pour les ventes domestiques, le comportement reste identique.
            effective = _dc_replace(sale, buyer_vat_valid=False,
                                    product_category=product_category, asin=product_asin)

            if sale.stock_country != sale.buyer_country:
                vies_summary.vies_affected_sale_ids.add(_sale_key(effective))

        _vies_state["last_classified_sale_id"] = sale.sale_id
        return effective

    _lang, _curr, _sym = lang, currency, symbol

    results, oss_summary = _run_oss_loop(
        all_items_sorted, refund_ids, marketplace_name,
        asin_to_category, apply_fr_under_threshold,
        effective_sale_fn=_effective_sale_with_vies,
        lang=_lang, currency=_curr, symbol=_sym
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
        taxed_at_departure = (
            is_cross_border and res.vat_country == res.sale.stock_country
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
            display_id=reclass.display_id,
            stock_country=reclass.stock_country,
            taxed_at_departure=taxed_at_departure,
        )

    return results, vies_summary, oss_summary