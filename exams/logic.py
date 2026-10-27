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
