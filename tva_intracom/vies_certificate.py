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
from typing import Optional

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.platypus import (
    SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer,
)
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_CENTER


def _fmt_dt(value) -> str:
    if not value:
        return "—"
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return value[:10]
    return value.strftime("%d/%m/%Y")


def generate_vies_certificate_pdf(
    snapshot: list[dict],
    *,
    company_name: str,
    siren: str,
    scope_id: str,
    period_label: str = "",
    country_label_fn=None,
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
                  (ex: tva_intracom.ui.formatting._country_label) ; sinon le
                  code brut est utilisé.
    """
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

    generated_at = datetime.now(timezone.utc)
    _country = country_label_fn or (lambda c: c)

    elements = []
    elements.append(Paragraph("Certificat de Validité des Numéros de TVA Intracommunautaire", title_style))
    elements.append(Paragraph("Justificatif de vérification VIES (Système d'échange d'informations sur la TVA — Commission européenne)", subtitle_style))
    elements.append(Spacer(1, 10 * mm))

    header_rows = [
        ["Entreprise", company_name or "—"],
        ["SIREN", siren or "—"],
        ["Période couverte", period_label or "Historique complet"],
        ["Date de génération du certificat", generated_at.strftime("%d/%m/%Y à %H:%M")+" UTC"],
        ["Nombre de numéros de TVA vérifiés", str(len(snapshot))],
    ]
    header_table = Table(header_rows, colWidths=[65 * mm, 105 * mm])
    header_table.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("LINEBELOW", (0, 0), (-1, -1), 0.3, colors.lightgrey),
    ]))
    elements.append(header_table)
    elements.append(Spacer(1, 8 * mm))

    elements.append(Paragraph("Détail des vérifications", section_style))
    elements.append(Paragraph(
        "Ce tableau liste, pour chaque numéro de TVA intracommunautaire d'un client B2B, "
        "le statut retenu par le moteur de calcul TVA, tel que connu à la date de "
        "génération de ce document, ainsi que les dates de première et dernière "
        "vérification effectuées via le service VIES de la Commission européenne.",
        normal,
    ))
    elements.append(Spacer(1, 4 * mm))

    table_data = [["Numéro TVA", "Pays", "Statut", "1ère vérif.", "Dernière vérif.", "Nb vérif.", "Source"]]
    for row in snapshot:
        table_data.append([
            row["vat_id"],
            _country(row["country_code"]),
            "✅ Valide" if row["valid"] else "❌ Invalide",
            _fmt_dt(row["first_checked_at"]),
            _fmt_dt(row["last_checked_at"]),
            str(row.get("nb_checks", "") or "—"),
            row.get("source", "VIES"),
        ])

    if len(table_data) == 1:
        elements.append(Paragraph("Aucun numéro de TVA B2B n'a été vérifié pour ce compte.", normal))
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
        for _i, row in enumerate(snapshot, start=1):
            if not row["valid"]:
                _style.append(("TEXTCOLOR", (2, _i), (2, _i), colors.HexColor("#d62728")))
        cert_table.setStyle(TableStyle(_style))
        elements.append(cert_table)

    elements.append(Spacer(1, 10 * mm))
    elements.append(Paragraph("Traçabilité et intégrité du document", section_style))

    _scope_hash = hashlib.sha256(scope_id.encode("utf-8")).hexdigest()[:16]
    _content_hash = hashlib.sha256(
        "|".join(f"{r['vat_id']}:{r['valid']}:{r['last_checked_at']}" for r in snapshot).encode("utf-8")
    ).hexdigest()[:16]
    elements.append(Paragraph(
        f"Identifiant de portée (scope) : <font face='Courier'>{_scope_hash}</font> — "
        f"Empreinte du contenu (SHA-256, 16 premiers caractères) : "
        f"<font face='Courier'>{_content_hash}</font>",
        small,
    ))
    elements.append(Paragraph(
        "Ce document est généré automatiquement à partir de la piste d'audit conservée "
        "par l'outil (durée de conservation légale : 365 jours). Il ne constitue pas à lui "
        "seul une preuve d'exonération de TVA intracommunautaire, mais un élément "
        "justificatif de la diligence de vérification effectuée par le vendeur au moment "
        "de la transaction.",
        small,
    ))

    doc.build(elements)
    return buf.getvalue()
