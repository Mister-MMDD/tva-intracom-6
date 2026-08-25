"""Génération d'un "Certificat de Validité VIES" en PDF.

Objectif : donner au vendeur une preuve de bonne foi opposable en cas de
contrôle fiscal — la piste d'audit `vies_check_history` (voir vies_engine.py)
prouve à quelle date CE compte a eu connaissance du statut VIES de chacun de
ses clients B2B, ce qui justifie l'application du régime d'exonération intra-
communautaire même si un numéro s'avère invalidé ultérieurement (bonne foi
au moment de la transaction — jurisprudence constante en la matière : la
charge de la preuve porte sur la vérification effectuée, pas sur le résultat
futur).

Bibliothèque : reportlab (pure Python, aucune dépendance système — contrai-
rement à weasyprint/wkhtmltopdf qui nécessitent des libs graphiques absentes
de l'environnement Streamlit Cloud). Ajouté à requirements.txt.
"""
from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from io import BytesIO

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import (
    SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer,
)


def _fmt_dt(value) -> str:
    if not value:
        return "—"
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return value[:10]
    return value.strftime("%d/%m/%Y")


def _fmt_dt_utc(value) -> str:
    """Comme _fmt_dt, mais avec l'heure UTC — nécessaire pour l'historique
    (plusieurs vérifications du même n° de TVA peuvent survenir le même
    jour ; la date seule ne suffit pas à les distinguer dans la piste
    d'audit)."""
    if not value:
        return "—"
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return value[:16]
    return value.strftime("%d/%m/%Y %H:%M")


def generate_vies_certificate_pdf(
    snapshot: list[dict],
    *,
    company_name: str,
    siren: str,
    scope_id: str,
    period_label: str = "",
    country_label_fn=None,
    translator=None,
) -> bytes:
    """Construit le certificat PDF à partir de `get_scope_vies_snapshot()`.

    Args:
        snapshot: sortie de `vies_engine.get_scope_vies_snapshot(scope_id)`.
        company_name, siren: identité affichée en en-tête.
        scope_id: portée du cache VIES (non affiché en clair si e-mail
                  personnel — seul un hash tronqué figure dans le document,
                  pour traçabilité sans exposer l'adresse e-mail).
        period_label: période fiscale couverte (facultatif, affichage seul).
        country_label_fn: callback optionnel pour libeller un code pays ISO2
                  (ex: tva_intracom.i18n.country_label) ; sinon le
                  code brut est utilisé.
        translator: callback optionnel pour traduire les libellés (ex: st.session_state._)
    """
    _ = translator or (lambda k, **kwargs: k)
    buf = BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=A4,
        topMargin=18 * mm, bottomMargin=18 * mm, leftMargin=16 * mm, rightMargin=16 * mm,
    )
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle("CertTitle", parent=styles["Title"], fontSize=16, alignment=TA_CENTER)
    subtitle_style = ParagraphStyle("CertSubtitle", parent=styles["Normal"], fontSize=9, alignment=TA_CENTER, textColor=colors.grey)
    section_style = ParagraphStyle("CertSection", parent=styles["Heading2"], fontSize=11, spaceBefore=10, spaceAfter=4)
    normal = styles["Normal"]
    small = ParagraphStyle("Small", parent=styles["Normal"], fontSize=8, textColor=colors.grey)
    label_style = ParagraphStyle("CertLabel", parent=styles["Normal"], fontSize=9, fontName="Helvetica-Bold")
    value_style = ParagraphStyle("CertValue", parent=styles["Normal"], fontSize=9)

    generated_at = datetime.now(timezone.utc)
    _country = country_label_fn or (lambda c: c)

    elements = []
    elements.append(Paragraph(_("vies_certificate_title"), title_style))
    elements.append(Paragraph(_("vies_certificate_subtitle"), subtitle_style))
    elements.append(Spacer(1, 10 * mm))

    header_rows = [
        [Paragraph(_("vies_certificate_company"), label_style), Paragraph(company_name or "—", value_style)],
        [Paragraph(_("vies_certificate_siren"), label_style), Paragraph(siren or "—", value_style)],
        [Paragraph(_("vies_certificate_period"), label_style), Paragraph(period_label or _("vies_certificate_full_history"), value_style)],
        [Paragraph(_("vies_certificate_generated_at"), label_style), Paragraph(generated_at.strftime("%d/%m/%Y à %H:%M")+" UTC", value_style)],
        # Ne compter que les vérifications automatiques VIES — une décision
        # manuelle n'est pas une preuve de vérification opposable au même
        # titre : elle n'a pas sa place dans un certificat de bonne foi VIES.
        [Paragraph(_("vies_certificate_count"), label_style),
         Paragraph(str(sum(1 for row in snapshot if row.get("source", "VIES") == "VIES")), value_style)],
    ]
    header_table = Table(header_rows, colWidths=[90 * mm, 80 * mm])
    header_table.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("LINEBELOW", (0, 0), (-1, -1), 0.3, colors.lightgrey),
    ]))
    elements.append(header_table)
    elements.append(Spacer(1, 8 * mm))

    elements.append(Paragraph(_("vies_certificate_detail_header"), section_style))
    elements.append(Paragraph(_("vies_certificate_detail_desc"), normal))
    elements.append(Spacer(1, 4 * mm))

    table_data = [[
        _("vies_certificate_col_vat"), _("vies_certificate_col_country"), 
        _("vies_certificate_col_status"), _("vies_certificate_col_first"), 
        _("vies_certificate_col_last"), _("vies_certificate_col_nb"), _("vies_certificate_col_source")
    ]]
    for row in snapshot:
        # Exclusion des overrides manuels : ce certificat atteste d'une
        # vérification VIES automatique, pas d'une décision interne du
        # cabinet/vendeur — les deux ne doivent jamais être confondues dans
        # une pièce présentée comme preuve de bonne foi en cas de contrôle.
        if row.get("source", "VIES") != "VIES":
            continue
        table_data.append([
            row["vat_id"],
            _country(row["country_code"]),
            _("vies_certificate_valide") if row["valid"] else _("vies_certificate_invalide"),
            _fmt_dt(row["first_checked_at"]),
            _fmt_dt(row["last_checked_at"]),
            str(row.get("nb_checks", "") or "—"),
            row.get("source", "VIES"),
        ])

    if len(table_data) == 1:
        elements.append(Paragraph(_("vies_certificate_empty_info"), normal))
    else:
        cert_table = Table(table_data, colWidths=[32 * mm, 20 * mm, 22 * mm, 24 * mm, 24 * mm, 18 * mm, 20 * mm], repeatRows=1)
        _style = [
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1f4e79")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTSIZE", (0, 0), (-1, -1), 7.5),
            ("GRID", (0, 0), (-1, -1), 0.3, colors.lightgrey),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f5f6f8")]),
            ("TOPPADDING", (0, 0), (-1, -1), 3),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ]
        for _i, row in enumerate([r for r in snapshot if r.get("source","VIES")=="VIES"], start=1):
            if not row["valid"]:
                _style.append(("TEXTCOLOR", (2, _i), (2, _i), colors.HexColor("#d62728")))
        cert_table.setStyle(TableStyle(_style))
        elements.append(cert_table)

    elements.append(Spacer(1, 10 * mm))
    elements.append(Paragraph(_("vies_certificate_traceability_header"), section_style))

    _scope_hash = hashlib.sha256(scope_id.encode("utf-8")).hexdigest()[:16]
    _content_hash = hashlib.sha256(
        "|".join(f"{r['vat_id']}:{r['valid']}:{r['last_checked_at']}" for r in snapshot).encode("utf-8")
    ).hexdigest()[:16]
    elements.append(Paragraph(
        f"{_('vies_certificate_scope_id')} : <font face='Courier'>{_scope_hash}</font> — "
        f"{_('vies_certificate_content_hash')} (SHA-256, 16 premiers caractères) : "
        f"<font face='Courier'>{_content_hash}</font>",
        small,
    ))
    elements.append(Paragraph(_("vies_certificate_footer_desc"), small))

    doc.build(elements)
    return buf.getvalue()


def generate_vies_history_pdf(
    history_rows: list[dict],
    *,
    company_name: str,
    siren: str,
    scope_id: str,
    period_label: str = "",
    country_label_fn=None,
    translator=None,
) -> bytes:
    """Construit le certificat PDF d'HISTORIQUE (une ligne par vérification
    VIES effectuée, pas une ligne par n° de TVA) — complément du certificat
    "situation à date" (generate_vies_certificate_pdf) ci-dessus, pour les cas
    où la piste d'audit doit montrer CHAQUE vérification dans le temps et pas
    seulement le dernier statut connu.

    Args:
        history_rows: sortie de `vies_engine.get_scope_vies_history_flat()` —
                  déjà filtrée par n° de TVA (mode "fichier") ou non (mode
                  "compte entier"), déjà triée par vat_id puis checked_at.
        (autres args : identiques à generate_vies_certificate_pdf)

    Mise en page paysage (landscape A4) : 6 colonnes (N° TVA, Date
    vérification UTC, Statut, Pays, Raison sociale, Erreur) — le mode
    portrait de generate_vies_certificate_pdf serait trop étroit pour la
    colonne "Raison sociale" (dénominations sociales souvent longues) une
    fois les 5 autres colonnes posées.
    """
    _ = translator or (lambda k, **kwargs: k)
    buf = BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=landscape(A4),
        topMargin=14 * mm, bottomMargin=14 * mm, leftMargin=14 * mm, rightMargin=14 * mm,
    )
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle("CertTitle", parent=styles["Title"], fontSize=16, alignment=TA_CENTER)
    subtitle_style = ParagraphStyle("CertSubtitle", parent=styles["Normal"], fontSize=9, alignment=TA_CENTER, textColor=colors.grey)
    section_style = ParagraphStyle("CertSection", parent=styles["Heading2"], fontSize=11, spaceBefore=10, spaceAfter=4)
    normal = styles["Normal"]
    small = ParagraphStyle("Small", parent=styles["Normal"], fontSize=8, textColor=colors.grey)
    label_style = ParagraphStyle("CertLabel", parent=styles["Normal"], fontSize=9, fontName="Helvetica-Bold")
    value_style = ParagraphStyle("CertValue", parent=styles["Normal"], fontSize=9)

    generated_at = datetime.now(timezone.utc)
    _country = country_label_fn or (lambda c: c)

    elements = []
    elements.append(Paragraph(_("vies_certificate_title"), title_style))
    elements.append(Paragraph(_("vies_certificate_history_subtitle"), subtitle_style))
    elements.append(Spacer(1, 10 * mm))

    header_rows = [
        [Paragraph(_("vies_certificate_company"), label_style), Paragraph(company_name or "—", value_style)],
        [Paragraph(_("vies_certificate_siren"), label_style), Paragraph(siren or "—", value_style)],
        [Paragraph(_("vies_certificate_period"), label_style), Paragraph(period_label or _("vies_certificate_full_history"), value_style)],
        [Paragraph(_("vies_certificate_generated_at"), label_style), Paragraph(generated_at.strftime("%d/%m/%Y à %H:%M")+" UTC", value_style)],
        [Paragraph(_("vies_certificate_history_count"), label_style), Paragraph(str(len(history_rows)), value_style)],
        [Paragraph(_("vies_certificate_history_unique_count"), label_style),
         Paragraph(str(len({r["vat_id"] for r in history_rows})), value_style)],
    ]
    header_table = Table(header_rows, colWidths=[90 * mm, 80 * mm])
    header_table.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("LINEBELOW", (0, 0), (-1, -1), 0.3, colors.lightgrey),
    ]))
    elements.append(header_table)
    elements.append(Spacer(1, 8 * mm))

    elements.append(Paragraph(_("vies_certificate_history_detail_header"), section_style))
    elements.append(Paragraph(_("vies_certificate_history_detail_desc"), normal))
    elements.append(Spacer(1, 4 * mm))

    table_data = [[
        _("xl_vies_col_vat"), _("xl_vies_col_checked_at"), _("xl_vies_col_status"),
        _("xl_vies_col_country"), _("xl_vies_col_name"), _("xl_vies_col_error"),
    ]]
    for row in history_rows:
        table_data.append([
            row["vat_id"],
            _fmt_dt_utc(row["checked_at"]),
            _("vies_certificate_valide") if row["valid"] else _("vies_certificate_invalide"),
            _country(row["country_code"]),
            row.get("name") or "—",
            row.get("error") or "—",
        ])

    if len(table_data) == 1:
        elements.append(Paragraph(_("vies_certificate_history_empty_info"), normal))
    else:
        # Colonnes larges : format paysage (269mm utiles vs ~174mm en
        # portrait) — nécessaire pour "Raison sociale" (dénominations
        # sociales longues) sans tronquer ni réduire excessivement la police.
        cert_table = Table(table_data, colWidths=[42 * mm, 32 * mm, 22 * mm, 22 * mm, 95 * mm, 56 * mm], repeatRows=1)
        _style = [
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1f4e79")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTSIZE", (0, 0), (-1, -1), 7.5),
            ("GRID", (0, 0), (-1, -1), 0.3, colors.lightgrey),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f5f6f8")]),
            ("TOPPADDING", (0, 0), (-1, -1), 3),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ]
        for _i, row in enumerate(history_rows, start=1):
            if not row["valid"]:
                _style.append(("TEXTCOLOR", (2, _i), (2, _i), colors.HexColor("#d62728")))
        cert_table.setStyle(TableStyle(_style))
        elements.append(cert_table)

    elements.append(Spacer(1, 10 * mm))
    elements.append(Paragraph(_("vies_certificate_traceability_header"), section_style))

    _scope_hash = hashlib.sha256(scope_id.encode("utf-8")).hexdigest()[:16]
    _content_hash = hashlib.sha256(
        "|".join(f"{r['vat_id']}:{r['checked_at']}:{r['valid']}" for r in history_rows).encode("utf-8")
    ).hexdigest()[:16]
    elements.append(Paragraph(
        f"{_('vies_certificate_scope_id')} : <font face='Courier'>{_scope_hash}</font> — "
        f"{_('vies_certificate_content_hash')} (SHA-256, 16 premiers caractères) : "
        f"<font face='Courier'>{_content_hash}</font>",
        small,
    ))
    elements.append(Paragraph(_("vies_certificate_footer_desc"), small))

    doc.build(elements)
    return buf.getvalue()
