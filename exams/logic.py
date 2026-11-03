"""Small helper functions with no Django code inside, so they are easy to test."""
import json
import math
from datetime import timedelta

MAX_QUESTIONS = 100


def parse_exam_questions(raw):
    """Check the questions typed by the teacher. Returns (questions, error_message)."""
    try:
        items = json.loads(raw or "[]")
    except ValueError:
        return [], "Could not read the questions. Please try again."
    if not isinstance(items, list) or not items:
        return [], "Add at least one question."
    if len(items) > MAX_QUESTIONS:
        return [], f"A quiz can have at most {MAX_QUESTIONS} questions."

    clean = []
    for n, item in enumerate(items, 1):
        if not isinstance(item, dict):
            return [], f"Question {n} is not valid."
        text = str(item.get("text", "")).strip()
        if not text:
            return [], f"Question {n}: please enter the question text."
        options = item.get("options")
        if not isinstance(options, list) or len(options) != 4:
            return [], f"Question {n}: exactly four options are needed."
        options = [str(o).strip() for o in options]
        if not all(options):
            return [], f"Question {n}: all four options must be filled in."
        if any(len(o) > 300 for o in options):
            return [], f"Question {n}: an option is too long (maximum 300 characters)."
        try:
            correct = int(item.get("correct"))
            marks = int(item.get("marks", 1))
        except (TypeError, ValueError):
            return [], f"Question {n}: choose the correct answer and enter valid marks."
        if not 0 <= correct <= 3:
            return [], f"Question {n}: choose the correct answer."
        if not 1 <= marks <= 100:
            return [], f"Question {n}: marks must be between 1 and 100."
        clean.append({"text": text, "options": options, "correct": correct, "marks": marks})
    return clean, ""


# ---------------- time rules (the SERVER decides all of these) ----------------

def exam_end_time(now, duration_minutes, deadline):
    """The whole quiz closes at the deadline if one is set, otherwise after one duration."""
    return deadline if deadline else now + timedelta(minutes=duration_minutes)


def personal_deadline(started_at, duration_minutes, exam_end, shift_seconds=0, slack_seconds=0):
    """
    When one student's time ends.

    A student gets the full duration from their own start, but never beyond the quiz end.
      shift_seconds = time added to every active student by the teacher (class-wide pause or extra time).
                      The quiz end is moved by the same amount, so it is NOT added to the cap here.
      slack_seconds = time given to THIS student only (their own pause or extra time). It moves both
                      their deadline and their cap, so the quiz end can never cut off time the
                      teacher gave them.
    """
    base = started_at + timedelta(minutes=duration_minutes, seconds=shift_seconds + slack_seconds)
    if exam_end is None:
        return base
    cap = exam_end + timedelta(seconds=slack_seconds)
    return min(base, cap)


def seconds_left(deadline, now):
    return max(0, math.ceil((deadline - now).total_seconds()))


def code_expiry(now, validity_minutes):
    """Expiry time of a test code (0 minutes means it never expires)."""
    return now + timedelta(minutes=validity_minutes) if validity_minutes else None


def warning_level(count, max_warnings):
    """ok = within the allowed warnings, final = last allowed warning, exceeded = over the limit."""
    if count > max_warnings:
        return "exceeded"
    if count == max_warnings:
        return "final"
    return "ok"


# ---------------- shuffling (every student sees a different order) ----------------
def seeded_order(n, seed, salt):
    """A repeatable shuffled list [0..n-1]: the same seed and salt always give the same order."""
    import random
    order = list(range(n))
    random.Random(f"{seed}:{salt}").shuffle(order)
    return order


def percent(score, total):
    return round(score * 100 / total) if total else 0


# ---------------- navigation and dashboard helpers ----------------
# Events a teacher should be told about (shown as "suspicious activity").
ALERT_KINDS = ("tab_switch", "fullscreen_exit", "flagged", "terminated")

NAV_SECTIONS = {
    # teacher
    "teacher_dashboard": "dashboard", "exam_monitor": "dashboard", "exam_edit": "dashboard",
    "attempt_detail": "dashboard", "exam_delete": "dashboard",
    "exam_create": "create",
    "reconduct_select": "reconduct", "exam_reconduct": "reconduct",
    # practice quizzes (the AI quiz app)
    "quiz_list": "practice", "take_quiz": "practice", "result": "practice", "leaderboard": "practice",
    "analytics": "practice", "join_quiz": "practice", "join_quiz_code": "practice",
    "upload_notes": "ai_quiz", "job_page": "ai_quiz",
    # student exams
    "student_dashboard": "my_exams", "attempt_open": "my_exams", "attempt_instructions": "my_exams",
    "attempt_take": "my_exams", "attempt_done": "my_exams",
    "exam_join": "join",
    # admin panel
    "panel_dashboard": "p_dash", "panel_users": "p_users", "panel_classes": "p_classes", "panel_exams": "p_exams",
    "panel_quizzes": "p_quizzes", "panel_log": "p_log", "panel_settings": "p_settings",
    "panel_user_detail": "p_users",
    # classes
    "classes_list": "classes", "class_detail": "classes", "my_classes": "classes",
}


def nav_section(url_name, is_teacher):
    """Which sidebar item is highlighted for the page being shown."""
    return NAV_SECTIONS.get(url_name or "", "")


def short(text, n=24):
    text = str(text)
    return text if len(text) <= n else text[: n - 1].rstrip() + "\u2026"


def bar_rows(items, max_value=None, low_below=None, suffix=""):
    """[(label, value), ...] -> rows for the horizontal bar chart (bar width 0 to 100)."""
    if not items:
        return []
    top = max_value if max_value else max(value for _, value in items)
    rows = []
    for label, value in items:
        width = round(value * 100 / top) if top else 0
        rows.append({
            "label": short(label), "full": str(label), "value": value,
            "percent": max(0, min(100, width)), "text": f"{value}{suffix}",
            "low": low_below is not None and value < low_below,
        })
    return rows


def donut_segments(parts):
    """[(label, value, colour), ...] -> (segments, css background for the donut, total)."""
    total = sum(value for _, value, _ in parts)
    segments, stops, start = [], [], 0.0
    for label, value, colour in parts:
        share = value * 100 / total if total else 0
        segments.append({"label": label, "value": value, "colour": colour, "percent": round(share)})
        if value:
            stops.append(f"{colour} {start:.2f}% {start + share:.2f}%")
            start += share
    css = "conic-gradient(" + ", ".join(stops) + ")" if stops else "conic-gradient(#353535 0 100%)"
    return segments, css, total
