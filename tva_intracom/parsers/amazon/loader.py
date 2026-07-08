"""Chargeur principal : load_amazon_report() et AmazonImportResult.

Orchestre les sous-modules :
  detect    → format + séparateur
  aggregate → pré-agrégation V5
  parsers   → extraction des champs bruts
  classify  → classification acheteur, conversion devise, construction Sale
"""

from __future__ import annotations

import csv
import logging
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Callable, List, Optional, Set

from ...models import BuyerType, Sale
from ...vies import _normalize_vat_id as normalize_vat
from .aggregate import preaggregate_v5
from .classify import (
    BuyerClassification,
    apply_vat_exception,
    classify_buyer,
    convert_amazon_vat,
    convert_currency,
)
from .constants import (
    CREDIT_NOTE_TYPES,
    INBOUND_TYPES,
    INVOICE_TYPES,
    REFUND_TYPES,
    SALE_TYPES,
    TRANSFER_TYPES,
    safe_decimal,
)
from .detect import EXPECTED_COLUMNS, detect_format, detect_separator, normalize_header
from .parsers import PARSERS

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Résultat d'import
# ---------------------------------------------------------------------------

@dataclass
class AmazonImportResult:
    sales: List[object]           # List[Sale]
    refunds: List[object]         # List[Sale]
    fc_transfers: List[dict]
    stock_countries: Set[str]
    skipped_rows: int = 0
    total_rows: int = 0
    warnings: List[str] = field(default_factory=list)
    detected_format: int = 0      # 1, 2, 3, 4 ou 5
    platform: str = "Amazon"
    # Lignes RETURN physiques (mouvement marchandise sans montant financier).
    # Distinct de skipped_rows : les RETURN sont normaux, le flux financier
    # est dans le REFUND jumeau.
    return_rows: int = 0
    # Écritures de facturation pure Amazon (régularisations de facture,
    # avoirs administratifs) : ni vente ni remboursement, comptées à part
    # pour visibilité — distinct de skipped_rows (type vraiment inconnu).
    invoice_rows: int = 0
    credit_note_rows: int = 0
    # Lignes brutes INVOICE / CREDIT_NOTE conservées pour l'onglet Excel dédié
    # (voir excel_report.py). Champs extraits via le parser du format détecté
    # + quelques colonnes brutes directement lues sur la ligne normalisée.
    invoice_credit_notes: List[dict] = field(default_factory=list)
    # Format 5 uniquement : Tax Reporting Scheme par sale_id
    # "VCS_EU_OSS" = déclarable OSS ; "" = domestique / hors OSS
    tax_scheme_by_sale_id: dict = field(default_factory=dict)
    # Commandes dont la date de commande et la date d'expédition (fait
    # générateur retenu) ne tombent pas dans le même mois civil — risque de
    # déclaration sur la mauvaise période si on s'était fié à la date de
    # commande. Une entrée par vente concernée : {sale_id, order_date,
    # shipment_date, amount_ht}. Format 5 uniquement (seul format où les
    # deux dates sont disponibles séparément).
    period_mismatches: List[dict] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Boucle principale de traitement
# ---------------------------------------------------------------------------

def _process_rows(
    rows_to_process: list[tuple[int, dict]],
    parser,
    fmt: int,
    seller_country: str,
    convert_currencies: bool,
    asin_to_category: Optional[dict[str, str]],
    result: AmazonImportResult,
    progress_callback: Optional[Callable[[int, int], None]] = None,
    progress_step: int = 500,
) -> None:
    """Traite chaque ligne agrégée et alimente result.sales / result.refunds.

    Séquence pour chaque ligne :
      1. Filtrage (FC Transfer, Inbound, type inconnu, montant nul)
      2. Extraction des champs bruts via parser
      3. Classification acheteur (placeholder / NIF national / B2B / B2C)
      4. Vérification territoire TVA exception (Canaries, DOM-TOM…)
      5. Conversion devise BCE si demandée
      6. Construction du Sale et routage vers sales ou refunds

    Args:
        progress_callback: callable(processed, total) optionnel, appelé tous les
            `progress_step` lignes (et une fois à la fin) pour permettre à l'appelant
            (ex: app.py / st.progress) de suivre l'avancement sur un gros fichier.
            N'est jamais appelé si None (comportement par défaut inchangé).
        progress_step: fréquence d'appel du callback, en nombre de lignes.
    """
    total = len(rows_to_process)
    for processed, (line_no, row) in enumerate(rows_to_process, start=1):
        if progress_callback is not None and (
            processed % progress_step == 0 or processed == total
        ):
            try:
                progress_callback(processed, total)
            except Exception:
                # Le callback ne doit jamais interrompre le parsing (ex: erreur
                # d'affichage Streamlit si le composant a été démonté entre-temps).
                logger.debug("progress_callback a levé une exception, ignorée.", exc_info=True)

        tx_type = parser.tx_type(row)

        # --- FC Transfer / Inbound ---
        if tx_type in TRANSFER_TYPES or tx_type in INBOUND_TYPES:
            dep = parser.departure(row)
            arr = parser.arrival(row)
            if dep:
                result.stock_countries.add(dep)
            if arr:
                result.stock_countries.add(arr)
            result.fc_transfers.append(row)
            continue

        # --- Écritures de facturation pure (INVOICE / CREDIT_NOTE) ---
        # Ni vente ni remboursement : comptées à part, jamais dans skipped_rows
        # (qui doit rester réservé aux types vraiment inconnus).
        if tx_type in INVOICE_TYPES or tx_type in CREDIT_NOTE_TYPES:
            try:
                amount = parser.amount_ht(row)
            except Exception:
                amount = Decimal("0")
            try:
                tx_date_val = parser.tx_date(row)
            except Exception:
                tx_date_val = ""
            result.invoice_credit_notes.append({
                "kind": "INVOICE" if tx_type in INVOICE_TYPES else "CREDIT_NOTE",
                "date": tx_date_val,
                "marketplace": row.get("marketplace", "").strip(),
                "program_type": row.get("program_type", "").strip(),
                "reference": (
                    row.get("vat_inv_number", "").strip()
                    or row.get("transaction_event_id", "").strip()
                ),
                "amount_ht": amount,
                "vat_amount": safe_decimal(row.get("total_activity_value_vat_amt", "")),
                "currency": row.get("transaction_currency_code", "").strip() or "EUR",
            })
            if tx_type in INVOICE_TYPES:
                result.invoice_rows += 1
            else:
                result.credit_note_rows += 1
            continue

        # --- Filtrage type inconnu ---
        if tx_type not in SALE_TYPES and tx_type not in REFUND_TYPES:
            if tx_type:
                logger.debug("Ligne %d ignorée (type=%s)", line_no, tx_type)
            result.skipped_rows += 1
            continue

        # --- Extraction champs bruts ---
        departure    = parser.departure(row)
        arrival      = parser.arrival(row)
        raw_vat      = parser.buyer_vat(row)
        amount_ht    = parser.amount_ht(row)
        tx_date_str  = parser.tx_date(row)
        order_date_str = parser.order_date(row)
        shipment_date_str = parser.shipment_date(row)
        order_date_str = parser.order_date(row)
        shipment_date_str = parser.shipment_date(row)
        qty          = parser.qty(row)
        row_asin     = parser.asin(row)
        currency     = parser.currency(row)
        arrival_pc   = parser.arrival_post_code(row)

        product_category = "STANDARD"
        if asin_to_category and row_asin in asin_to_category:
            product_category = asin_to_category[row_asin]

        # --- Pays manquants ---
        if not departure or not arrival:
            order_ref = (
                row.get("order_id", "")
                or row.get("vat_invoice_number", "")
                or f"L{line_no}"
            )
            result.warnings.append(
                f"Ligne {line_no} ({order_ref}) : pays départ/arrivée manquant — "
                "ligne incomplète (facture TVA Amazon non générée ?), ignorée."
            )
            result.skipped_rows += 1
            continue

        # --- Montant nul ---
        # Un montant à 0 ne change pas le type de la ligne (sale/refund) :
        # on la classe quand même (elle passera dans sales/refunds plus bas
        # comme toute autre ligne), avec une alerte pour vérification
        # manuelle plutôt qu'une exclusion silencieuse. Seul RETURN reste
        # traité à part (mouvement physique sans montant, cas normal —
        # le flux financier est porté par le REFUND jumeau).
        if amount_ht == 0:
            if tx_type == "return":
                result.return_rows += 1
                continue
            order_ref = (
                row.get("order_id", "")
                or row.get("vat_invoice_number", "")
                or f"L{line_no}"
            )
            result.warnings.append(
                f"Ligne {line_no} ({order_ref}) : {tx_type} à montant nul (0 €) — "
                "conservée dans le rapport, à vérifier."
            )

        # --- Signe remboursements ---
        if tx_type in REFUND_TYPES:
            amount_ht = -abs(amount_ht)

        # --- Classification acheteur ---
        classification = classify_buyer(
            raw_vat=raw_vat,
            arrival=arrival,
            departure=departure,
            normalize_fn=normalize_vat,
            BuyerType=BuyerType,
        )

        # --- Territoire TVA exception ---
        # apply_vat_exception remplace le code pays par "XX" si le code postal
        # désigne un territoire hors UE fiscale (Canaries, DOM-TOM, Åland…).
        # "XX" n'est pas dans EU_COUNTRIES → engine.py → EXPORT automatiquement.
        # arrival_pc est également conservé sur Sale pour is_fiscal_eu() dans engine.
        postal_code = arrival_pc or (
            row.get("ship_to_postal_code", "")
            or row.get("delivery_postal_code", "")
            or row.get("ship_to_zip", "")
        ).strip()
        arrival = apply_vat_exception(arrival, postal_code)

        # --- Conversion devise ---
        try:
            fx = convert_currency(
                amount_ht=amount_ht,
                currency=currency,
                tx_date_str=tx_date_str,
                tx_type=tx_type,
                fmt=fmt,
                row=row,
                convert_currencies=convert_currencies,
            )
        except ValueError as exc:
            result.warnings.append(
                f"Ligne {line_no} : conversion {currency}→EUR impossible ({exc}). "
                "Montant gardé en devise originale."
            )
            fx = type("_FX", (), {
                "amount_ht": amount_ht,
                "original_currency": currency,
                "original_amount": amount_ht,
                "exchange_rate": Decimal("1"),
                "exchange_rate_source": "eur",
            })()

        # --- TVA Amazon ---
        amazon_vat_raw = parser.amazon_vat(row)
        amazon_vat_amt = convert_amazon_vat(amazon_vat_raw, fx.exchange_rate, tx_type)

        # --- Construction Sale ---
        sale_id = parser.sale_id(row, line_no)
        # Identifiant à AFFICHER uniquement (TRANSACTION_EVENT_ID) — n'intervient
        # jamais dans sale_id (clé d'agrégation/matching, inchangée ci-dessus).
        # Absent des formats 3/5 : reste vide, l'affichage repliera sur sale_id.
        display_id = row.get("transaction_event_id", "").strip()
        sale = Sale(
            sale_id=sale_id,
            display_id=display_id,
            amount_ht=fx.amount_ht,
            buyer_type=classification.buyer_type,
            stock_country=departure,
            buyer_country=arrival,
            seller_country=seller_country.upper(),
            buyer_vat_valid=classification.buyer_vat_valid,
            buyer_vat_number=classification.buyer_vat,
            quantity=qty,
            original_currency=fx.original_currency,
            original_amount=fx.original_amount,
            exchange_rate=fx.exchange_rate,
            exchange_rate_source=fx.exchange_rate_source,
            transaction_date=tx_date_str,
            order_date=order_date_str if order_date_str != tx_date_str else "",
            product_category=product_category,
            asin=row_asin,
            amazon_vat_amount=amazon_vat_amt,
            arrival_post_code=postal_code,
        )

        # --- Écart de période commande / expédition (fait générateur) ---
        # Si la commande et l'expédition ne tombent pas dans le même mois
        # civil, la TVA est exigible sur le mois/trimestre de l'expédition
        # (transaction_date retenu) et non sur celui de la commande. On
        # journalise systématiquement le cas pour permettre une vérification
        # manuelle — notamment sur les commandes à cheval sur deux trimestres.
        if order_date_str and shipment_date_str and order_date_str != shipment_date_str:
            if order_date_str[:7] != shipment_date_str[:7]:
                result.period_mismatches.append({
                    "sale_id": sale_id,
                    "order_date": order_date_str,
                    "shipment_date": shipment_date_str,
                    "amount_ht": sale.amount_ht,
                })

        result.stock_countries.add(departure)

        # Format 5 : stocker le Tax Reporting Scheme pour audit OSS
        if fmt == 5:
            scheme = row.get("tax_reporting_scheme", "").strip()
            result.tax_scheme_by_sale_id[sale_id] = scheme

        # Garde : SHIPMENT avec montant négatif = retour non tagué RETURN
        if tx_type in SALE_TYPES and sale.amount_ht < 0:
            logger.warning(
                "Ligne %s : SHIPMENT montant négatif (%.2f %s) "
                "requalifié automatiquement en RETURN.",
                sale.sale_id, float(sale.amount_ht), sale.original_currency,
            )
            result.refunds.append(sale)
        elif tx_type in SALE_TYPES:
            result.sales.append(sale)
        else:
            result.refunds.append(sale)


# ---------------------------------------------------------------------------
# Point d'entrée public
# ---------------------------------------------------------------------------

def load_amazon_report(
    path: "Path | str",
    seller_country: str = "FR",
    encoding: str = "utf-8",
    convert_currencies: bool = False,
    asin_to_category: Optional[dict[str, str]] = None,
    progress_callback: Optional[Callable[[int, int], None]] = None,
) -> AmazonImportResult:
    """Charge un fichier Amazon VAT Transactions Report (formats 1 à 5).

    La détection du format est automatique sur le header.
    Interface publique identique à l'ancienne version monolithique
    (le nouveau paramètre progress_callback est optionnel, valeur par
    défaut None → aucun changement de comportement pour les appelants
    existants).

    Args:
        progress_callback: callable(processed, total) optionnel, appelé
            périodiquement pendant le traitement ligne par ligne (utile
            pour afficher une barre de progression sur un gros fichier,
            ex: st.progress dans app.py).
    """
    path = Path(path)
    result = AmazonImportResult(
        sales=[], refunds=[], fc_transfers=[], stock_countries=set()
    )

    with path.open(encoding=encoding, errors="replace", newline="") as handle:
        first_line = handle.readline()
        sep = detect_separator(first_line)
        handle.seek(0)

        # ------------------------------------------------------------------
        # Lecture du fichier : pandas (parseur C) en priorité.
        #
        # Sur un rapport Amazon de plusieurs centaines de milliers de lignes,
        # le parseur C de pandas est nettement plus rapide que csv.DictReader
        # + une dict-comprehension par ligne en pur Python. On lit tout en
        # dtype=str (aucune inférence de type — la conversion des montants
        # reste déléguée à safe_decimal() dans constants.py, qui gère déjà
        # les virgules décimales FR, espaces milliers, et symboles monétaires
        # — on ne duplique pas cette logique ici), puis on normalise les en-têtes
        # vectoriellement via un simple rename de colonnes (une seule passe,
        # pas une par cellule). La conversion finale en liste de dict reste
        # nécessaire pour ne pas toucher à l'architecture des parsers par
        # format (chaque _RowParser attend un dict par ligne) — c'est la
        # lecture/normalisation qui est vectorisée, pas la logique métier
        # par ligne (classification B2B/B2C, devise, etc.), qui reste légitimement
        # ligne par ligne car elle diffère par format et a des branches métier
        # (cf. parsers.py / classify.py).
        #
        # Repli automatique sur csv.DictReader si pandas échoue (CSV malformé,
        # encodage incohérent ligne à ligne, etc.) pour ne jamais bloquer un
        # import qui fonctionnait auparavant.
        # ------------------------------------------------------------------
        raw_rows: list[dict] = []
        try:
            import pandas as pd  # import local : évite la dépendance dure si non installé

            df = pd.read_csv(
                handle,
                sep=sep,
                dtype=str,
                keep_default_na=False,
                na_filter=False,
                engine="c",
                on_bad_lines="warn",
            )
            df.columns = [normalize_header(str(c)) for c in df.columns]
            raw_rows = df.to_dict("records")
        except Exception as exc:
            logger.warning(
                "Lecture pandas du CSV échouée (%s) — repli sur csv.DictReader.", exc
            )
            handle.seek(0)
            reader = csv.DictReader(handle, delimiter=sep)
            raw_fieldnames = reader.fieldnames
            if raw_fieldnames:
                reader.fieldnames = [normalize_header(f) for f in raw_fieldnames]
            raw_rows = [
                {normalize_header(k): v for k, v in row.items() if k}
                for row in reader
            ]

        headers = set(raw_rows[0].keys()) if raw_rows else set()
        fmt = detect_format(headers)
        result.detected_format = fmt
        parser = PARSERS[fmt]
        logger.info(
            "Format Amazon détecté : %d (fichier: %s, séparateur: %r)",
            fmt, path.name, sep,
        )

        # Warning colonnes critiques manquantes
        if fmt in EXPECTED_COLUMNS:
            missing = [c for c in EXPECTED_COLUMNS[fmt] if c not in headers]
            if missing:
                msg = (
                    f"Format {fmt} détecté mais colonnes attendues absentes : "
                    f"{', '.join(missing)}. Vérifier la compatibilité du fichier."
                )
                logger.warning(msg)
                result.warnings.append(msg)

        result.total_rows = len(raw_rows)

        # Format 5 : pré-agrégation multi-juridictions
        if fmt == 5:
            rows_to_process, multi_asin_orders = preaggregate_v5(raw_rows, parser)
            parser._multi_asin_orders = multi_asin_orders  # type: ignore[attr-defined]
        else:
            rows_to_process = list(enumerate(raw_rows, start=2))

    # Traitement principal (hors contexte fichier : fichier fermé proprement)
    _process_rows(
        rows_to_process=rows_to_process,
        parser=parser,
        fmt=fmt,
        seller_country=seller_country,
        convert_currencies=convert_currencies,
        asin_to_category=asin_to_category,
        result=result,
        progress_callback=progress_callback,
    )

    logger.info(
        "Import Amazon (format %d) : %d ventes, %d remboursements, "
        "%d transferts FC, %d retours physiques, %d invoice, %d credit_note, "
        "%d ignorées",
        result.detected_format,
        len(result.sales),
        len(result.refunds),
        len(result.fc_transfers),
        result.return_rows,
        result.invoice_rows,
        result.credit_note_rows,
        result.skipped_rows,
    )
    return result