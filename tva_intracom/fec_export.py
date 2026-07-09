"""Export des écritures comptables au format FEC (Fichier des Écritures
Comptables — art. A47 A-1 du LPF) pour import dans un logiciel comptable
(Sage, Ciel, Quadratus, ACD, Silae…).

Objectif : donner au cabinet comptable un journal des ventes prêt à
importer, sans attendre l'homologation EDI-TVA (voir Roadmap README.md).
Ne remplace PAS une télédéclaration : c'est un pré-remplissage des écritures
de vente, à relire par le cabinet avant validation.

Agrégation : une écriture par (période, régime fiscal, canal, pays de TVA,
taux) plutôt qu'une écriture par vente — un export de 3000 ventes tient donc
en quelques dizaines de lignes FEC, lisibles et vérifiables.

⚠️ AVERTISSEMENT IMPORTANT — à lire avant tout import comptable réel :
Le traitement comptable ci-dessous est une SIMPLIFICATION qui couvre le cas
général (vendeur collecte la TVA). Certains régimes demandent un traitement
spécifique que ce module ne fait PAS automatiquement :
  - DEEMED_SUPPLIER : Amazon collecte et reverse la TVA à la place du
    vendeur. Le montant réellement encaissé par le vendeur (règlement
    Amazon) est net de cette TVA — mais ce module ne connaît que le montant
    HT calculé par le moteur, pas le flux de règlement réel Amazon. Aucune
    ligne de TVA collectée n'est générée pour ce régime (cohérent avec le
    fait que le vendeur ne la doit pas), mais VÉRIFIEZ le rapprochement avec
    les relevés de règlement Amazon avant validation comptable.
  - B2B_REVERSE_CHARGE / EXPORT : TVA à 0, exonération à justifier (mention
    obligatoire sur facture, art. 262 ter / 283-2 CGI) — non gérée ici,
    à ajouter manuellement si votre logiciel comptable l'exige sur la pièce.
  - IOSS_DIRECT : traité comme TVA collectée par le vendeur (cohérent avec
    le moteur), à vérifier selon votre paramétrage IOSS réel.
Ce module n'est PAS un logiciel de comptabilité. Faites relire le premier
export par votre expert-comptable avant tout usage récurrent.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Iterable

from .models import Collector, Scenario, VatResult

# ---------------------------------------------------------------------------
# Plan comptable — comptes génériques paramétrables.
# Remplacez ces valeurs par la numérotation réelle de votre dossier avant tout
# import en comptabilité (demandez confirmation à votre cabinet comptable).
# ---------------------------------------------------------------------------
ACCOUNTS = {
    # Compte client générique (règlements Amazon) — souvent un compte
    # auxiliaire dédié plutôt qu'un 411 générique en pratique.
    "CLIENT": "4111000",
    # Compte de vente HT (produits/marchandises).
    "VENTE": "7071000",
    # TVA collectée FRANÇAISE (CA3) — régime FR_DOMESTIC.
    "TVA_COLLECTEE_FR": "4457100",
    # TVA collectée via OSS — compte dédié recommandé pour ne pas mélanger
    # avec la TVA française classique (facilite le rapprochement CA3/OSS).
    "TVA_COLLECTEE_OSS": "4457180",
    # TVA collectée via IOSS.
    "TVA_COLLECTEE_IOSS": "4457190",
    # TVA due dans un pays d'immatriculation locale (hors FR, hors OSS).
    "TVA_COLLECTEE_LOCAL": "4457200",
}

JOURNAL_CODE = "VE"
JOURNAL_LIB = "Journal des ventes"


@dataclass(frozen=True)
class _AggKey:
    period: str
    scenario: Scenario
    channel_account: str   # compte TVA à créditer, déjà résolu (ou "" si aucun)
    vat_country: str
    vat_rate: Decimal


@dataclass
class _AggBucket:
    base_ht: Decimal
    vat_amount: Decimal
    count: int


def _vat_account_for(result: VatResult) -> str:
    """Détermine le compte de TVA collectée à créditer pour ce résultat.

    Retourne "" si le vendeur ne doit rien collecter (DEEMED_SUPPLIER,
    B2B_REVERSE_CHARGE, EXPORT) — voir l'avertissement en tête de module.
    """
    if result.collector != Collector.SELLER:
        return ""
    if result.vat_amount <= Decimal("0.00"):
        return ""
    if result.scenario == Scenario.IOSS_DIRECT:
        return ACCOUNTS["TVA_COLLECTEE_IOSS"]
    if result.scenario == Scenario.OSS_B2C:
        return ACCOUNTS["TVA_COLLECTEE_OSS"]
    if result.scenario == Scenario.DOMESTIC and result.vat_country == "FR":
        return ACCOUNTS["TVA_COLLECTEE_FR"]
    if result.scenario in (Scenario.DOMESTIC, Scenario.IMPORT_SELLER_AS_IMPORTER):
        return ACCOUNTS["TVA_COLLECTEE_LOCAL"]
    # Cas résiduel (ex: IMPORT_STANDARD collecté par le vendeur dans un
    # scénario non prévu ci-dessus) : compte local par défaut, à vérifier.
    return ACCOUNTS["TVA_COLLECTEE_LOCAL"]


def aggregate_for_fec(
    results: Iterable[VatResult], period: str
) -> dict[_AggKey, _AggBucket]:
    """Agrège les VatResult par (période, régime, compte TVA, pays TVA, taux).

    Les avoirs (montants négatifs) sont agrégés dans le même bucket que les
    ventes du même groupe — le solde net apparaît directement dans la ligne
    FEC (pas de ligne séparée par avoir), cohérent avec la logique de
    regroupement demandée.
    """
    buckets: dict[_AggKey, _AggBucket] = {}
    for res in results:
        vat_account = _vat_account_for(res)
        key = _AggKey(
            period=period,
            scenario=res.scenario,
            channel_account=vat_account,
            vat_country=res.vat_country,
            vat_rate=res.vat_rate,
        )
        bucket = buckets.get(key)
        if bucket is None:
            bucket = _AggBucket(base_ht=Decimal("0.00"), vat_amount=Decimal("0.00"), count=0)
            buckets[key] = bucket
        bucket.base_ht += res.sale.amount_ht
        bucket.vat_amount += res.vat_amount
        bucket.count += 1
    return buckets


_FEC_HEADER = [
    "JournalCode", "JournalLib", "EcritureNum", "EcritureDate",
    "CompteNum", "CompteLib", "CompAuxNum", "CompAuxLib",
    "PieceRef", "PieceDate", "EcritureLib", "Debit", "Credit",
    "EcritureLet", "DateLet", "ValidDate", "Montantdevise", "Idevise",
]


def _fmt_amount(v: Decimal) -> str:
    """Format FEC standard : point décimal, 2 décimales, jamais négatif
    (un montant négatif dans Debit/Credit est invalide en FEC — voir
    l'inversion débit/credit ci-dessous pour les buckets nets négatifs)."""
    return f"{v.quantize(Decimal('0.01')):.2f}"


def _scenario_label(scenario: Scenario) -> str:
    return {
        Scenario.OSS_B2C: "Vente OSS",
        Scenario.DOMESTIC: "Vente domestique",
        Scenario.DEEMED_SUPPLIER: "Vente deemed supplier (Amazon)",
        Scenario.B2B_REVERSE_CHARGE: "Vente B2B exonérée (autoliquidation)",
        Scenario.EXPORT: "Export hors UE",
        Scenario.IMPORT_STANDARD: "Import standard",
        Scenario.IOSS_DIRECT: "Vente IOSS",
        Scenario.IMPORT_SELLER_AS_IMPORTER: "Import DDP (vendeur importateur)",
    }.get(scenario, scenario.value)


def build_fec_rows(
    results: Iterable[VatResult],
    period: str,
    ecriture_date: str,
    piece_ref: str = "",
) -> list[list[str]]:
    """Construit les lignes FEC (hors en-tête) pour la période donnée.

    Args:
        results:       VatResult du moteur pour la période (déjà filtrés).
        period:        libellé de période (ex: "2026-Q2"), utilisé dans le
                       libellé d'écriture, PAS dans EcritureDate (qui doit
                       être une date réelle — voir ecriture_date).
        ecriture_date: date de comptabilisation au format AAAAMMJJ (FEC),
                       typiquement le dernier jour de la période.
        piece_ref:     référence de pièce justificative (ex: nom du fichier
                       Amazon importé). Optionnel.

    Returns:
        Liste de lignes (chacune = liste de 18 champs texte, ordre
        _FEC_HEADER), équilibrées : chaque groupe génère une écriture Debit
        (compte client, TTC net du groupe) + une ou deux écritures Credit
        (vente HT + TVA collectée si applicable), Debit total == Credit total
        par EcritureNum.
    """
    buckets = aggregate_for_fec(results, period=period)
    rows: list[list[str]] = []
    ecriture_num = 1

    for key in sorted(buckets, key=lambda k: (k.scenario.value, k.vat_country, k.vat_rate)):
        bucket = buckets[key]
        net_ht = bucket.base_ht
        net_vat = bucket.vat_amount

        label = f"{_scenario_label(key.scenario)} {key.vat_country or 'N/A'} {key.vat_rate}% ({period}, {bucket.count} ventes)"
        num_str = str(ecriture_num)

        def _line(compte: str, compte_lib: str, debit: Decimal, credit: Decimal) -> list[str]:
            return [
                JOURNAL_CODE, JOURNAL_LIB, num_str, ecriture_date,
                compte, compte_lib, "", "",
                piece_ref, ecriture_date, label,
                _fmt_amount(debit), _fmt_amount(credit),
                "", "", "", "", "",
            ]

        abs_ht = abs(net_ht)
        abs_vat = abs(net_vat)
        has_vat_line = bool(key.channel_account) and abs_vat > Decimal("0.00")
        # Le débit client doit être égal à la somme des crédits générés pour
        # rester équilibré : HT seul si aucune TVA n'est collectée par le
        # vendeur (DEEMED_SUPPLIER, B2B_REVERSE_CHARGE, EXPORT — vat_amount
        # du moteur existe mais ne transite pas par la compta du vendeur
        # dans ces cas), HT+TVA sinon.
        abs_client = abs_ht + (abs_vat if has_vat_line else Decimal("0.00"))
        flip = (net_ht + (net_vat if has_vat_line else Decimal("0.00"))) < Decimal("0.00")

        if not flip:
            rows.append(_line(ACCOUNTS["CLIENT"], "Clients Amazon", abs_client, Decimal("0.00")))
            rows.append(_line(ACCOUNTS["VENTE"], "Ventes marchandises", Decimal("0.00"), abs_ht))
            if has_vat_line:
                rows.append(_line(key.channel_account, "TVA collectée", Decimal("0.00"), abs_vat))
        else:
            rows.append(_line(ACCOUNTS["CLIENT"], "Clients Amazon", Decimal("0.00"), abs_client))
            rows.append(_line(ACCOUNTS["VENTE"], "Ventes marchandises", abs_ht, Decimal("0.00")))
            if has_vat_line:
                rows.append(_line(key.channel_account, "TVA collectée", abs_vat, Decimal("0.00")))

        ecriture_num += 1

    return rows


def generate_fec_bytes(
    results: Iterable[VatResult],
    period: str,
    ecriture_date: str,
    piece_ref: str = "",
    encoding: str = "utf-8",
) -> bytes:
    """Génère le contenu FEC complet (en-tête + lignes), séparateur tabulation
    (accepté par la norme FEC, alternative au '|'), encodage paramétrable —
    latin-1 attendu par certains logiciels comptables historiques, utf-8
    plus sûr par défaut pour les caractères accentués des libellés.
    """
    rows = build_fec_rows(results, period=period, ecriture_date=ecriture_date, piece_ref=piece_ref)
    lines = ["\t".join(_FEC_HEADER)]
    lines.extend("\t".join(row) for row in rows)
    content = "\r\n".join(lines) + "\r\n"
    return content.encode(encoding, errors="replace")