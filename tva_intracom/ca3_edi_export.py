"""CSV de préparation des principales rubriques EDI-TVA du formulaire 3310-CA3.

Ce fichier codé aide à la reprise par un professionnel ou un partenaire EDI ;
ce n'est ni un interchange EDIFACT ni une télédéclaration.
"""

from __future__ import annotations

import csv
import io
import re
from calendar import monthrange
from decimal import Decimal, ROUND_HALF_UP

from tva_intracom.ca3_report import compute_ca3_lines_v2
from tva_intracom.models import VatResult

_EURO = Decimal("1")
_VALID_REGIMES = ("trimestriel", "mensuel")


class Ca3EdiRegimeMismatchError(ValueError):
    """Levée quand le régime de périodicité déclaré (Art. 289 B CGI) est
    incohérent avec la période détectée par le moteur.

    Le régime de périodicité (mensuel vs trimestriel) est une décision
    administrative externe (notifiée par le SIE), jamais déductible des
    seules dates de vente : cette vérification ne fait que confronter une
    valeur déclarée par l'appelant à la période réellement détectée, elle
    ne la déduit jamais elle-même.
    """
_CSV_COLUMNS = (
    "formulaire",
    "version_champ",
    "code_donnee",
    "segment",
    "chemin_edifact",
    "valeur",
    "statut",
    "libelle",
    "commentaire",
)


def _whole_euros(amount: Decimal) -> Decimal:
    """Applique l'arrondi fiscal à l'euro le plus proche, 0,50 vers le haut."""
    return amount.quantize(_EURO, rounding=ROUND_HALF_UP)


def _safe_cell(value: object) -> str:
    """Empêche l'interprétation des cellules textuelles comme formules Excel."""
    text = "" if value is None else str(value)
    if text.lstrip(" \t\r\n").startswith(("=", "+", "-", "@")):
        return "'" + text
    return text


def _declared_civil_period(period_label: str) -> tuple[str, str] | None:
    """Retourne les bornes civiles uniquement pour une période unique reconnue."""
    label = (period_label or "").strip()
    month_match = re.fullmatch(r"(\d{4})-(0[1-9]|1[0-2])", label)
    if month_match:
        year, month = map(int, month_match.groups())
        return f"{year:04d}{month:02d}01", f"{year:04d}{month:02d}{monthrange(year, month)[1]:02d}"

    quarter_match = re.fullmatch(r"(\d{4})-Q([1-4])", label)
    if quarter_match:
        year, quarter = map(int, quarter_match.groups())
        first_month = (quarter - 1) * 3 + 1
        last_month = first_month + 2
        return f"{year:04d}{first_month:02d}01", f"{year:04d}{last_month:02d}{monthrange(year, last_month)[1]:02d}"

    return None


def _declared_period_shape(period_label: str) -> str | None:
    """Forme de la période détectée : "mois" (AAAA-MM), "trimestre"
    (AAAA-Qn) ou None (autre forme : année, semestre, plage, vide...).

    Une forme non reconnue (None) ne déclenche jamais d'erreur de cohérence
    avec le régime déclaré : on ne bloque que sur une incohérence certaine.
    """
    label = (period_label or "").strip()
    if re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", label):
        return "mois"
    if re.fullmatch(r"\d{4}-Q[1-4]", label):
        return "trimestre"
    return None


def validate_regime_periodicite(regime_periodicite: str | None, period_label: str) -> None:
    """Vérifie la cohérence régime déclaré / période détectée.

    Publique pour que l'UI puisse bloquer AVANT de proposer la génération
    (message d'erreur propre) ; `generate_ca3_edi_preparation_csv` la rappelle
    par sécurité. Lève ValueError (régime inconnu) ou
    Ca3EdiRegimeMismatchError (incohérence certaine). `None` (régime non
    déclaré) désactive le contrôle.
    """
    if regime_periodicite is None:
        return
    if regime_periodicite not in _VALID_REGIMES:
        raise ValueError(
            f"regime_periodicite invalide : {regime_periodicite!r} "
            f"(attendu : {', '.join(_VALID_REGIMES)})"
        )
    shape = _declared_period_shape(period_label)
    if regime_periodicite == "mensuel" and shape == "trimestre":
        raise Ca3EdiRegimeMismatchError(
            f"Régime déclaré mensuel (Art. 289 B CGI) mais période détectée "
            f"trimestrielle ({period_label!r})."
        )
    if regime_periodicite == "trimestriel" and shape == "mois":
        raise Ca3EdiRegimeMismatchError(
            f"Régime déclaré trimestriel mais période détectée mensuelle "
            f"({period_label!r})."
        )


def generate_ca3_edi_preparation_csv(
    results: list[VatResult],
    company_name: str,
    siren: str,
    period_label: str,
    refund_results: list[VatResult] | None = None,
    all_fc_transfers: list | None = None,
    regime_periodicite: str | None = None,
) -> bytes:
    """Construit un CSV de préparation CA3 avec les codes EDI des formulaires 2026.

    Les cases non calculées ou qui requièrent une information absente restent
    vides et portent un statut explicite ; elles ne sont jamais remplacées par
    zéro. Les montants présents sont arrondis conformément au Volume III.

    `regime_periodicite` ("trimestriel" ou "mensuel"; None = non déclaré, aucun
    contrôle de cohérence) est une
    donnée déclarative fournie par l'appelant — reflet du régime de
    périodicité TVA réel de l'entreprise (Art. 289 B CGI), notifié par le
    SIE et jamais déductible des ventes importées. Une incohérence avec la
    période détectée (`period_label`) fait lever `Ca3EdiRegimeMismatchError`
    plutôt que de produire un CSV silencieusement erroné.
    """
    validate_regime_periodicite(regime_periodicite, period_label)

    lines = compute_ca3_lines_v2(
        results,
        refund_results,
        all_fc_transfers=all_fc_transfers,
        seller_country="FR",
    )
    rows: list[tuple[object, ...]] = []

    def add(
        form: str,
        version: str,
        code: str,
        segment: str,
        edi_path: str,
        value: object,
        status: str,
        label: str,
        comment: str = "",
    ) -> None:
        rows.append((form, version, code, segment, edi_path, value, status, label, comment))

    def add_amount(
        line_key: str,
        code: str,
        version: str,
        label: str,
        *,
        amount: Decimal | None = None,
        status: str = "calculé",
        comment: str = "",
    ) -> None:
        if status == "à compléter":
            add(
                "3310CA3", version, code, "MOA", f"{code}:C516:5004:1", "",
                status, label, comment,
            )
            return
        raw_amount = lines[line_key] if amount is None else amount
        rounded = _whole_euros(raw_amount)
        if rounded < 0:
            add(
                "3310CA3", version, code, "MOA", f"{code}:C516:5004:1", "",
                "à vérifier", label,
                "Montant négatif non exporté : vérifier son traitement avec le partenaire EDI.",
            )
            return
        add(
            "3310CA3", version, code, "MOA", f"{code}:C516:5004:1",
            format(rounded, "f"), status, label, comment,
        )

    siren_value = (siren or "").strip()
    siren_status = "prérempli à vérifier" if re.fullmatch(r"[0-9]{9}", siren_value) else "à corriger"
    add(
        "T-IDENTIF", "06/00", "AA", "NAD", "AA:C082:3039:1", siren_value,
        siren_status, "SIREN du redevable", "Le SIREN doit comporter 9 chiffres ; importer comme texte pour préserver un éventuel zéro initial.",
    )
    add(
        "T-IDENTIF", "06/00", "AA", "NAD", "AA:C080:3036:1", company_name or "",
        "prérempli à vérifier" if company_name else "à compléter", "Dénomination du redevable",
    )
    add(
        "T-IDENTIF", "06/00", "KD", "RFF", "KD:C506:1154:1", "", "à compléter",
        "Référence d'obligation fiscale (ROF)", "Valeur à confirmer auprès du SIE ou du partenaire EDI.",
    )
    civil_period = _declared_civil_period(period_label)
    if civil_period:
        period_start, period_end = civil_period
        period_status = "prérempli — à vérifier"
        period_comment = (
            "Bornes du mois/trimestre civil correspondant à la période détectée sur les ventes importées. "
            "Vérifier que cette période est bien celle de l'obligation CA3."
        )
    else:
        period_start = period_end = ""
        period_status = "à compléter"
        period_comment = (
            "Aucune période civile unique (mois ou trimestre) reconnue ; "
            "ne pas utiliser les seules dates min/max des ventes."
        )
    add(
        "T-IDENTIF", "06/00", "CA", "DTM", "CA:C507:2380:1:102", period_start, period_status,
        "Date de début de période (SSAAMMJJ)", period_comment,
    )
    add(
        "T-IDENTIF", "06/00", "CB", "DTM", "CB:C507:2380:1:102", period_end, period_status,
        "Date de fin de période (SSAAMMJJ)", period_comment,
    )
    # Ligne purement informative (aucun code EDI officiel associé) : rappelle
    # le régime de périodicité déclaré par l'appelant, vérifié cohérent avec
    # la période ci-dessus (sinon Ca3EdiRegimeMismatchError, plus haut).
    add(
        "T-IDENTIF", "", "", "", "", regime_periodicite or "",
        "déclaré par l'utilisateur" if regime_periodicite else "non déclaré",
        "Régime de périodicité TVA (Art. 289 B CGI)",
        "Information de contrôle, hors format EDI. Régime notifié par le SIE, non déduit des ventes.",
    )

    aic_present = lines["B2_base_ht"] != 0
    aic_note = "Base AIC estimée par le moteur à partir des transferts de stock." if aic_present else ""
    add_amount("A1_base_ht", "CA", "19/00", "A1 — ventes et prestations de services : base HT")
    add_amount("F2_base_ht", "DC", "19/00", "F2 — livraisons intracommunautaires : base HT")
    add_amount("E1_base_ht", "DA", "19/00", "E1 — exportations hors UE : base HT")
    add_amount("B2_base_ht", "CC", "19/00", "B2 — acquisitions intracommunautaires : base HT",
                status="estimé" if aic_present else "calculé", comment=aic_note)

    rates = (
        ("L08", "FP", "GP", "20/00", Decimal("0.20"), "20 %"),
        ("L09", "FB", "GB", "19/00", Decimal("0.055"), "5,5 %"),
        ("L9B", "FR", "GR", "20/00", Decimal("0.10"), "10 %"),
        ("LT6", "MF", "ME", "34/00", Decimal("0.021"), "2,1 %"),
    )
    for key, base_code, tax_code, version, rate, label in rates:
        base = _whole_euros(lines[f"{key}_base_ht"])
        base_status = "estimé" if key == "L08" and aic_present else "calculé"
        base_comment = "Inclut la base AIC estimée." if key == "L08" and aic_present else ""
        add_amount(f"{key}_base_ht", base_code, version, f"{key} — taux {label} : base HT",
                   amount=lines[f"{key}_base_ht"], status=base_status, comment=base_comment)
        tax_status = "estimé" if key == "L08" and aic_present else "calculé"
        if key == "L08" and aic_present:
            tax_amount = lines[f"{key}_tva_due"]
            tax_comment = "Montant moteur incluant une AIC estimée ; vérifier le classement par taux."
        else:
            tax_amount = base * rate
            tax_comment = "Calculé après arrondi fiscal de la base."
        add_amount(f"{key}_tva_due", tax_code, version, f"{key} — taux {label} : taxe due",
                   amount=tax_amount, status=tax_status, comment=tax_comment)

    add_amount(
        "L17_tva_aic", "GJ", "19/00", "L17 — TVA brute sur acquisitions intracommunautaires",
        amount=lines["L17_tva_aic"], status="estimé" if aic_present else "calculé",
        comment="Montant moteur estimé à partir des transferts de stock ; validation nécessaire." if aic_present else "",
    )
    add_amount("L18_tva_mc", "GK", "19/00", "L18 — TVA sur opérations à destination de Monaco")

    add_amount("L19_tva_ded", "HA", "19/00", "L19 — TVA déductible sur immobilisations",
                amount=Decimal("0"), status="à compléter", comment="Les achats ne sont pas récupérés par le moteur.")
    aic_deductible = lines["L20_tva_ded"]
    has_aic_deduction = aic_deductible != 0
    add_amount(
        "L20_tva_ded", "HB", "19/00", "L20 — TVA déductible sur autres biens et services",
        amount=aic_deductible,
        status="partiel — AIC estimée" if has_aic_deduction else "à compléter",
        comment=(
            f"Sous-total AIC déductible calculé par le moteur ({format(_whole_euros(aic_deductible), 'f')} EUR). "
            "Ajouter et vérifier la TVA déductible sur les achats et autres biens/services."
            if has_aic_deduction else "Les achats et autres TVA déductibles ne sont pas récupérés par le moteur."
        ),
    )
    add_amount("L22_credit", "HD", "19/00", "L22 — crédit de la période précédente",
                amount=Decimal("0"), status="à compléter", comment="Solde antérieur à saisir manuellement.")

    add(
        "INFO", "", "", "", "", "", "non transmissible", "Nature du fichier",
        "CSV préparatoire avec codes de données EDI ; ce n'est pas un message EDIFACT INFENT DT ni un fichier télétransmissible.",
    )
    add(
        "INFO", "", "", "", "", "", "validation requise", "Périmètre et compléments",
        f"Période détectée par l'application : {period_label or 'non précisée'}. Vérifier les dates civiles préremplies, la ROF et toutes les cases avec un partenaire EDI habilité. Les annexes 3310-A/3310-Ter/3310-TIC, le paiement 3310-CA3G, les données de paiement et les autres cases non calculées ne sont pas produits.",
    )

    output = io.StringIO(newline="")
    writer = csv.writer(output, delimiter=";", lineterminator="\r\n")
    writer.writerow(_CSV_COLUMNS)
    for row in rows:
        writer.writerow(tuple(_safe_cell(cell) for cell in row))
    return output.getvalue().encode("utf-8-sig")
