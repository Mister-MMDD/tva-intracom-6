"""PDF rendering and traceability for the applied-rates export."""

from __future__ import annotations

import hashlib
import io
import uuid
from datetime import datetime, timezone
from typing import Iterable
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from .i18n import _
from .models import VatResult
from .rates_evidence import extract_rates_evidence_records


def _paragraph(value: str, style: ParagraphStyle) -> Paragraph:
    return Paragraph(escape(value or "—"), style)


def generate_rates_evidence_pdf(
    results: Iterable[VatResult],
    period_label: str,
    *,
    company_name: str,
    siren: str,
    scope_id: str,
    xlsx_bytes: bytes | None = None,
    translator=None,
) -> bytes:
    """Render the rate evidence as a readable PDF with a unique reference and hashes."""
    translate = translator or _
    vat_records, fx_records = extract_rates_evidence_records(results, period_label, translator=translate)
    generated_at = datetime.now(timezone.utc)
    data_hash = hashlib.sha256(xlsx_bytes or b"").hexdigest()
    scope_hash = hashlib.sha256(scope_id.encode("utf-8")).hexdigest()[:16]
    document_id = uuid.uuid4().hex.upper()

    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=landscape(A4),
        topMargin=14 * mm,
        bottomMargin=14 * mm,
        leftMargin=14 * mm,
        rightMargin=14 * mm,
    )
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle("RatesTitle", parent=styles["Title"], fontSize=16, alignment=TA_CENTER)
    subtitle_style = ParagraphStyle(
        "RatesSubtitle", parent=styles["Normal"], fontSize=9,
        alignment=TA_CENTER, textColor=colors.grey,
    )
    section_style = ParagraphStyle(
        "RatesSection", parent=styles["Heading2"], fontSize=11, spaceBefore=8, spaceAfter=4,
    )
    small_style = ParagraphStyle("RatesSmall", parent=styles["Normal"], fontSize=7.5)
    trace_style = ParagraphStyle("RatesTrace", parent=styles["Normal"], fontSize=7, textColor=colors.grey)
    label_style = ParagraphStyle("RatesLabel", parent=small_style, fontName="Helvetica-Bold")

    elements = [
        Paragraph(escape(translate("rates_evidence_pdf_title")), title_style),
        Paragraph(escape(translate("rates_evidence_pdf_subtitle")), subtitle_style),
        Spacer(1, 7 * mm),
    ]
    header_rows = [
        [_paragraph(translate("vies_certificate_company"), label_style), _paragraph(company_name, small_style),
         _paragraph(translate("vies_certificate_siren"), label_style), _paragraph(siren, small_style)],
        [_paragraph(translate("vies_certificate_period"), label_style), _paragraph(period_label, small_style),
         _paragraph(translate("vies_certificate_generated_at"), label_style),
         _paragraph(generated_at.strftime("%Y-%m-%d %H:%M:%S UTC"), small_style)],
        [_paragraph(translate("rates_evidence_pdf_record_count"), label_style),
         _paragraph(str(len(vat_records) + len(fx_records)), small_style), "", ""],
    ]
    header_table = Table(header_rows, colWidths=[40 * mm, 105 * mm, 42 * mm, 70 * mm])
    header_table.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
        ("FONTNAME", (2, 0), (2, -1), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 8),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("LINEBELOW", (0, 0), (-1, -1), 0.3, colors.lightgrey),
    ]))
    elements.extend([header_table, Spacer(1, 6 * mm)])

    def add_table(title: str, headers: list[str], rows: list[list[str]], widths: list[float]) -> None:
        elements.append(Paragraph(escape(title), section_style))
        table_header_style = ParagraphStyle(
            "RatesTableHeader", parent=small_style, fontName="Helvetica-Bold", textColor=colors.white,
        )
        table_data = [[_paragraph(label, table_header_style) for label in headers]]
        table_data.extend([[_paragraph(value, small_style) for value in row] for row in rows])
        table = Table(table_data, colWidths=widths, repeatRows=1, hAlign="LEFT")
        table.setStyle(TableStyle([
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1f4e79")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("GRID", (0, 0), (-1, -1), 0.3, colors.lightgrey),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f5f6f8")]),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("TOPPADDING", (0, 0), (-1, -1), 3),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ]))
        elements.extend([table, Spacer(1, 4 * mm)])

    col = lambda key: translate(f"rates_evidence_col_{key}")
    fx_rows = [
        [rate_type, regime, dt, closing_dt, currency, str(rate) if rate is not None else "", source]
        for rate_type, regime, dt, closing_dt, currency, rate, source in fx_records
    ]
    vat_rows = [
        [country, str(vat_rate), first_d, last_d]
        for country, vat_rate, first_d, last_d in vat_records
    ]

    add_table(
        translate("rates_evidence_pdf_exchange_header"),
        [col("type"), col("regime"), col("date"), col("closing_date"), col("currency"), col("rate"), col("source")],
        fx_rows,
        [29 * mm, 30 * mm, 34 * mm, 34 * mm, 20 * mm, 40 * mm, 45 * mm],
    )
    add_table(
        translate("rates_evidence_pdf_vat_header"),
        [col("country"), col("vat_rate"), col("first_date"), col("last_date")],
        vat_rows,
        [40 * mm, 45 * mm, 75 * mm, 75 * mm],
    )

    elements.extend([
        Spacer(1, 5 * mm),
        Paragraph(escape(translate("vies_certificate_traceability_header")), section_style),
        Paragraph(
            f"{escape(translate('rates_evidence_pdf_document_id'))} : "
            f"<font name='Courier'>{document_id}</font><br/>"
            f"{escape(translate('vies_certificate_scope_id'))} : "
            f"<font name='Courier'>{scope_hash}</font><br/>"
            f"{escape(translate('vies_certificate_content_hash'))} (SHA-256) : "
            f"<font name='Courier'>{data_hash}</font><br/>"
            f"{escape(translate('rates_evidence_pdf_trace_note'))}",
            trace_style,
        ),
    ])
    doc.build(elements)
    return buf.getvalue()
