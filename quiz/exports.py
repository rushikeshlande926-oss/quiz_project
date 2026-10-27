"""CSV and PDF helpers."""
from io import BytesIO
from xml.sax.saxutils import escape


def safe_cell(value):
    """Stops Excel from running a username like '=cmd' as a formula."""
    text = str(value)
    return "'" + text if text[:1] in ("=", "+", "-", "@") else text


def build_result_pdf(quiz_title, username, score, total, percent, time_display,
                     rank, players, is_practice, review):
    # imported here so the website still starts even if reportlab is not installed
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import KeepTogether, Paragraph, SimpleDocTemplate, Spacer

    styles = getSampleStyleSheet()
    title = ParagraphStyle("t", parent=styles["Title"], fontSize=22, spaceAfter=4)
    sub = ParagraphStyle("s", parent=styles["Normal"], fontSize=12, textColor="#555555", spaceAfter=10)
    body = ParagraphStyle("b", parent=styles["Normal"], fontSize=11, leading=15)
    h2 = ParagraphStyle("h", parent=styles["Heading2"], spaceBefore=14, spaceAfter=8)

    story = [
        Paragraph("Quiz Result", title),
        Paragraph(escape(quiz_title), sub),
        Paragraph("<b>Student:</b> " + escape(username), body),
        Paragraph("<b>Score:</b> %s / %s (%s%%)" % (score, total, percent), body),
        Paragraph("<b>Time:</b> " + escape(time_display), body),
    ]
    if is_practice:
        story.append(Paragraph("<b>Type:</b> Practice attempt (not counted on the leaderboard)", body))
    else:
        story.append(Paragraph("<b>Rank:</b> #%s of %s" % (rank, players), body))

    story.append(Paragraph("Review", h2))
    for i, r in enumerate(review, 1):
        color = "#15803d" if r["is_correct"] else "#b91c1c"
        block = [
            Paragraph("<b>%d. %s</b>" % (i, escape(r["question"])), body),
            Paragraph('Your answer: <font color="%s">%s</font>' % (color, escape(str(r["your_answer"]))), body),
        ]
        if not r["is_correct"]:
            block.append(Paragraph(
                'Correct answer: <font color="#15803d">%s</font>' % escape(str(r["correct_answer"])), body))
        block.append(Spacer(1, 8))
        story.append(KeepTogether(block))

    buffer = BytesIO()
    SimpleDocTemplate(
        buffer, pagesize=A4, title="Quiz Result",
        leftMargin=20 * mm, rightMargin=20 * mm, topMargin=18 * mm, bottomMargin=18 * mm,
    ).build(story)
    return buffer.getvalue()
