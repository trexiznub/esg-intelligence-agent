from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import (
    SimpleDocTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
    PageBreak,
    KeepTogether,
)
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfbase import pdfmetrics
from reportlab.lib.colors import HexColor


GREEN = HexColor("#39ff88")
DARK = HexColor("#07130d")
LIGHT_GREEN = HexColor("#dfffea")
GREY = HexColor("#6b7280")
LIGHT_GREY = HexColor("#f3f4f6")
RED = HexColor("#dc2626")
AMBER = HexColor("#d97706")


def _safe(value, default="Not available"):
    if value is None or value == "":
        return default
    return str(value)


def _category_name(category):
    names = {
        "env": "Environmental",
        "environmental": "Environmental",
        "social": "Social",
        "gov": "Governance",
        "governance": "Governance",
    }
    return names.get(str(category).lower(), str(category).title())


def _status_label(status):
    if not status:
        return "Not available"

    mapping = {
        "Found": "Found",
        "Inferred": "Inferred",
        "Not found": "Not Found",
        "Requires further verification": "Needs Verification",
    }

    return mapping.get(status, str(status))


def _status_score(status):
    """
    Transparent coverage indicator.
    This is NOT an ESG score.

    Found = 100
    Inferred = 70
    Needs verification = 40
    Not found = 0
    """
    mapping = {
        "Found": 100,
        "Inferred": 70,
        "Requires further verification": 40,
        "Not found": 0,
    }
    return mapping.get(status, 0)


def _make_header_footer(canvas, doc):
    canvas.saveState()

    width, height = A4

    canvas.setStrokeColor(HexColor("#d1fae5"))
    canvas.setLineWidth(0.5)
    canvas.line(
        18 * mm,
        height - 15 * mm,
        width - 18 * mm,
        height - 15 * mm,
    )

    canvas.setFont("Helvetica", 8)
    canvas.setFillColor(GREY)

    canvas.drawString(
        18 * mm,
        9 * mm,
        "ESG Intelligence Agent · Ujwal Raj K.P.",
    )

    canvas.drawRightString(
        width - 18 * mm,
        9 * mm,
        f"Page {doc.page}",
    )

    canvas.restoreState()


def generate_report(result, output_path):
    """
    Generate a professional ESG Intelligence PDF from the
    existing pipeline result structure.
    """

    meta = result.get("meta", {})
    findings = result.get("f", {}) or {}
    insights = result.get("ins", {}) or {}

    company = _safe(meta.get("company"), "Unknown Organization")
    report_type = _safe(meta.get("type"), "Sustainability Report")
    year = _safe(meta.get("year"), "Not specified")
    pages = _safe(meta.get("pages"), "Not available")

    doc = SimpleDocTemplate(
        output_path,
        pagesize=A4,
        rightMargin=18 * mm,
        leftMargin=18 * mm,
        topMargin=22 * mm,
        bottomMargin=18 * mm,
        title=f"ESG Intelligence Report - {company}",
        author="Ujwal Raj K.P.",
    )

    styles = getSampleStyleSheet()

    title_style = ParagraphStyle(
        "ReportTitle",
        parent=styles["Title"],
        fontName="Helvetica-Bold",
        fontSize=25,
        leading=30,
        textColor=DARK,
        alignment=TA_LEFT,
        spaceAfter=8,
    )

    subtitle_style = ParagraphStyle(
        "Subtitle",
        parent=styles["Normal"],
        fontSize=11,
        leading=16,
        textColor=GREY,
        spaceAfter=16,
    )

    section_style = ParagraphStyle(
        "Section",
        parent=styles["Heading2"],
        fontName="Helvetica-Bold",
        fontSize=16,
        leading=20,
        textColor=DARK,
        spaceBefore=8,
        spaceAfter=10,
    )

    body_style = ParagraphStyle(
        "Body",
        parent=styles["BodyText"],
        fontSize=9.5,
        leading=14,
        textColor=HexColor("#374151"),
        spaceAfter=6,
    )

    small_style = ParagraphStyle(
        "Small",
        parent=styles["BodyText"],
        fontSize=8,
        leading=11,
        textColor=GREY,
    )

    card_style = ParagraphStyle(
        "Card",
        parent=styles["BodyText"],
        fontSize=9,
        leading=13,
        textColor=DARK,
    )

    story = []

    # ---------------------------------------------------------
    # COVER / EXECUTIVE SUMMARY
    # ---------------------------------------------------------

    story.append(Spacer(1, 8 * mm))

    story.append(
        Paragraph(
            "ESG INTELLIGENCE<br/>REPORT",
            title_style,
        )
    )

    story.append(
        Paragraph(
            f"<b>{company}</b><br/>"
            f"{report_type} · {year}",
            subtitle_style,
        )
    )

    # Summary cards
    total_findings = 0
    found_count = 0
    inferred_count = 0
    verification_count = 0
    not_found_count = 0

    category_rows = []

    for category, items in findings.items():
        if not isinstance(items, list):
            continue

        total = len(items)
        total_findings += total

        found = sum(
            1 for x in items
            if x.get("status") == "Found"
        )

        inferred = sum(
            1 for x in items
            if x.get("status") == "Inferred"
        )

        verification = sum(
            1 for x in items
            if x.get("status") == "Requires further verification"
        )

        not_found = sum(
            1 for x in items
            if x.get("status") == "Not found"
        )

        found_count += found
        inferred_count += inferred
        verification_count += verification
        not_found_count += not_found

        category_rows.append(
            (
                _category_name(category),
                total,
                found,
                inferred,
                verification,
                not_found,
            )
        )

    if total_findings:
        coverage = round(
            (
                found_count * 100
                + inferred_count * 70
                + verification_count * 40
            )
            / total_findings,
            1,
        )
    else:
        coverage = 0

    cards = [
        [
            Paragraph(
                f"<b>{total_findings}</b><br/>ESG findings",
                card_style,
            ),
            Paragraph(
                f"<b>{found_count}</b><br/>Directly identified",
                card_style,
            ),
            Paragraph(
                f"<b>{not_found_count}</b><br/>Not found",
                card_style,
            ),
            Paragraph(
                f"<b>{coverage}%</b><br/>Disclosure coverage*",
                card_style,
            ),
        ]
    ]

    card_table = Table(
        cards,
        colWidths=[43 * mm] * 4,
    )

    card_table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), LIGHT_GREEN),
                ("BOX", (0, 0), (-1, -1), 0.5, HexColor("#bbf7d0")),
                ("INNERGRID", (0, 0), (-1, -1), 0.5, HexColor("#bbf7d0")),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("LEFTPADDING", (0, 0), (-1, -1), 9),
                ("RIGHTPADDING", (0, 0), (-1, -1), 9),
                ("TOPPADDING", (0, 0), (-1, -1), 10),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 10),
            ]
        )
    )

    story.append(card_table)
    story.append(Spacer(1, 5 * mm))

    story.append(
        Paragraph(
            "*Disclosure coverage is an analytical indicator based on the "
            "status of extracted findings. It is not an ESG performance score.",
            small_style,
        )
    )

    story.append(Spacer(1, 8 * mm))

    story.append(Paragraph("Executive Summary", section_style))

    highlights = insights.get("highlights", [])

    if highlights:
        for item in highlights:
            story.append(
                Paragraph(
                    f"• {_safe(item)}",
                    body_style,
                )
            )
    else:
        story.append(
            Paragraph(
                "No executive highlights were generated.",
                body_style,
            )
        )

    # ---------------------------------------------------------
    # ESG CATEGORY ANALYSIS
    # ---------------------------------------------------------

    story.append(Spacer(1, 5 * mm))
    story.append(Paragraph("ESG Disclosure Analysis", section_style))

    category_table_data = [
        [
            "Category",
            "Findings",
            "Found",
            "Inferred",
            "Verify",
            "Not Found",
        ]
    ]

    for row in category_rows:
        category_table_data.append(list(row))

    if len(category_table_data) > 1:
        table = Table(
            category_table_data,
            colWidths=[
                38 * mm,
                20 * mm,
                20 * mm,
                20 * mm,
                20 * mm,
                24 * mm,
            ],
            repeatRows=1,
        )

        table.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, 0), DARK),
                    ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                    ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                    ("FONTSIZE", (0, 0), (-1, -1), 8),
                    ("GRID", (0, 0), (-1, -1), 0.4, HexColor("#d1d5db")),
                    ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, LIGHT_GREY]),
                    ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                    ("TOPPADDING", (0, 0), (-1, -1), 6),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
                ]
            )
        )

        story.append(table)

    # ---------------------------------------------------------
    # INSIGHTS
    # ---------------------------------------------------------

    story.append(PageBreak())

    story.append(Paragraph("Key ESG Insights", title_style))
    story.append(
        Paragraph(
            "Insights generated from validated findings in the analysed document.",
            subtitle_style,
        )
    )

    insight_sections = [
        ("Environmental", insights.get("env", [])),
        ("Social", insights.get("social", [])),
        ("Governance", insights.get("gov", [])),
    ]

    for heading, items in insight_sections:
        story.append(Paragraph(heading, section_style))

        if items:
            for item in items:
                story.append(
                    Paragraph(
                        f"• {_safe(item)}",
                        body_style,
                    )
                )
        else:
            story.append(
                Paragraph(
                    "No specific insights generated.",
                    body_style,
                )
            )

    # ---------------------------------------------------------
    # REPORTING GAPS
    # ---------------------------------------------------------

    story.append(Spacer(1, 5 * mm))
    story.append(Paragraph("Reporting Gaps", section_style))

    gaps = insights.get("gaps", [])

    if gaps:
        gap_data = [
            ["Category", "Topic", "Assessment"]
        ]

        for gap in gaps:
            if isinstance(gap, dict):
                gap_data.append(
                    [
                        _category_name(gap.get("cat", "")),
                        _safe(gap.get("topic")),
                        _safe(gap.get("why")),
                    ]
                )

        gap_table = Table(
            gap_data,
            colWidths=[35 * mm, 55 * mm, 82 * mm],
            repeatRows=1,
        )

        gap_table.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, 0), DARK),
                    ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                    ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                    ("FONTSIZE", (0, 0), (-1, -1), 8),
                    ("GRID", (0, 0), (-1, -1), 0.4, HexColor("#d1d5db")),
                    ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, LIGHT_GREY]),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("TOPPADDING", (0, 0), (-1, -1), 6),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
                ]
            )
        )

        story.append(gap_table)
    else:
        story.append(
            Paragraph(
                "No reporting gaps were identified.",
                body_style,
            )
        )

    # ---------------------------------------------------------
    # RISKS & INVESTIGATION
    # ---------------------------------------------------------

    story.append(Spacer(1, 8 * mm))
    story.append(Paragraph("Risks & Areas for Investigation", section_style))

    risks = insights.get("risks", [])
    investigate = insights.get("investigate", [])

    if risks:
        story.append(Paragraph("<b>Potential risks</b>", body_style))
        for item in risks:
            story.append(
                Paragraph(
                    f"• {_safe(item)}",
                    body_style,
                )
            )

    if investigate:
        story.append(
            Paragraph(
                "<b>Requires further investigation</b>",
                body_style,
            )
        )

        for item in investigate:
            story.append(
                Paragraph(
                    f"• {_safe(item)}",
                    body_style,
                )
            )

    # ---------------------------------------------------------
    # FINDINGS & EVIDENCE
    # ---------------------------------------------------------

    story.append(PageBreak())

    story.append(Paragraph("Evidence & Findings", title_style))
    story.append(
        Paragraph(
            "Extracted findings are presented with source-page references and "
            "confidence indicators where available.",
            subtitle_style,
        )
    )

    for category, items in findings.items():

        if not isinstance(items, list) or not items:
            continue

        story.append(
            Paragraph(
                _category_name(category),
                section_style,
            )
        )

        evidence_data = [
            [
                "Topic",
                "Status",
                "Value",
                "Page",
                "Confidence",
            ]
        ]

        for item in items:

            evidence_data.append(
                [
                    _safe(item.get("topic")),
                    _status_label(item.get("status")),
                    _safe(item.get("value"), "—"),
                    _safe(item.get("page"), "—"),
                    _safe(item.get("confidence"), "—"),
                ]
            )

        evidence_table = Table(
            evidence_data,
            colWidths=[
                52 * mm,
                32 * mm,
                43 * mm,
                18 * mm,
                30 * mm,
            ],
            repeatRows=1,
        )

        evidence_table.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, 0), DARK),
                    ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                    ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                    ("FONTSIZE", (0, 0), (-1, -1), 7.5),
                    ("GRID", (0, 0), (-1, -1), 0.35, HexColor("#d1d5db")),
                    ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, LIGHT_GREY]),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("TOPPADDING", (0, 0), (-1, -1), 5),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
                ]
            )
        )

        story.append(evidence_table)
        story.append(Spacer(1, 5 * mm))

        # Evidence details
        for item in items:
            topic = _safe(item.get("topic"))

            quote = item.get("quote")
            context = item.get("context")
            note = item.get("note")

            if quote or context or note:

                evidence_text = f"<b>{topic}</b>"

                if quote:
                    evidence_text += (
                        f"<br/><i>Evidence:</i> "
                        f"“{_safe(quote)}”"
                    )

                if context:
                    evidence_text += (
                        f"<br/><i>Context:</i> "
                        f"{_safe(context)}"
                    )

                if note:
                    evidence_text += (
                        f"<br/><i>Note:</i> "
                        f"{_safe(note)}"
                    )

                story.append(
                    Paragraph(
                        evidence_text,
                        small_style,
                    )
                )

                story.append(Spacer(1, 2 * mm))

    # ---------------------------------------------------------
    # METHODOLOGY
    # ---------------------------------------------------------

    story.append(PageBreak())

    story.append(Paragraph("Methodology & Scope", title_style))

    methodology = [
        "The report is generated from the uploaded sustainability document.",
        "ESG findings are extracted by category and validated against document evidence.",
        "Finding statuses distinguish directly identified information from inferred, "
        "missing, or verification-required information.",
        "Disclosure coverage is an analytical indicator and should not be interpreted "
        "as an ESG performance rating.",
        "AI-generated insights are constrained to validated findings and should be "
        "reviewed by qualified sustainability professionals before external use.",
    ]

    for item in methodology:
        story.append(
            Paragraph(
                f"• {item}",
                body_style,
            )
        )

    story.append(Spacer(1, 10 * mm))

    source_table = Table(
        [
            ["Organization", company],
            ["Report Type", report_type],
            ["Reporting Year", year],
            ["Pages Analysed", pages],
        ],
        colWidths=[45 * mm, 120 * mm],
    )

    source_table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (0, -1), LIGHT_GREEN),
                ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
                ("FONTNAME", (1, 0), (1, -1), "Helvetica"),
                ("GRID", (0, 0), (-1, -1), 0.4, HexColor("#d1d5db")),
                ("FONTSIZE", (0, 0), (-1, -1), 9),
                ("TOPPADDING", (0, 0), (-1, -1), 7),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
            ]
        )
    )

    story.append(source_table)

    story.append(Spacer(1, 20 * mm))

    story.append(
        Paragraph(
            "<b>ESG Intelligence Agent</b><br/>"
            "AI-powered sustainability disclosure analysis<br/><br/>"
            "© 2026 Ujwal Raj K.P.",
            ParagraphStyle(
                "Final",
                parent=body_style,
                alignment=TA_CENTER,
                fontSize=9,
                leading=14,
                textColor=GREY,
            ),
        )
    )

    doc.build(
        story,
        onFirstPage=_make_header_footer,
        onLaterPages=_make_header_footer,
    )