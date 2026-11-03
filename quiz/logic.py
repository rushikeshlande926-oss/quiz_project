"""Small helper functions with no Django code inside."""
import re


def normalize_answer(text):
    """Used for fill-in-the-blank: ignore capital letters, punctuation and extra spaces."""
    text = re.sub(r"[^\w\s]", "", str(text).lower())
    return " ".join(text.split())


FORMATS = """Return ONLY a JSON list (no extra text). Use these formats:
{"type": "mcq", "question": "...", "options": ["...", "...", "...", "..."], "answer": 0}
{"type": "tf", "question": "A statement that is either true or false", "answer": true}
{"type": "fill", "question": "A sentence with ______ in place of the missing word", "answer": "missing word"}
For "mcq", "answer" is the index (0 to 3) of the correct option."""

KIND_TEXT = {
    "mcq": "multiple choice questions (type \"mcq\"), each with exactly 4 options and exactly one correct option",
    "tf": "true/false statements (type \"tf\")",
    "fill": "fill-in-the-blank questions (type \"fill\"); the answer must be short, 1 to 3 words",
    "mix": "a mix of multiple choice (\"mcq\"), true/false (\"tf\") and fill-in-the-blank (\"fill\") questions",
}


def build_prompt(num_questions, question_type):
    kind = KIND_TEXT.get(question_type, KIND_TEXT["mcq"])
    return (
        "You are a quiz maker. Using ONLY the attached notes (they may be typed text, scanned pages "
        "or handwriting), create {n} {kind}.\n"
        "Write the questions in the same language as the notes.\n"
        "{formats}"
    ).format(n=num_questions, kind=kind, formats=FORMATS)


def parse_questions(data, question_type="mix"):
    """Check the list Gemini sent back and keep only the good questions."""
    allowed = {"mcq", "tf", "fill"} if question_type == "mix" else {question_type}
    default_type = "mcq" if question_type == "mix" else question_type
    clean = []
    for item in data:
        try:
            qtype = str(item.get("type") or default_type).lower()
            if qtype not in allowed:
                continue
            text = str(item["question"]).strip()
            if not text:
                continue

            if qtype == "mcq":
                options = [str(o).strip() for o in item["options"]]
                answer = int(item["answer"])
                if len(options) != 4 or not all(options) or not 0 <= answer <= 3:
                    continue
                clean.append({"type": "mcq", "question": text, "options": options,
                              "correct": answer, "answer_text": ""})
            elif qtype == "tf":
                answer = item["answer"]
                if isinstance(answer, str):
                    answer = answer.strip().lower() in ("true", "t", "yes")
                clean.append({"type": "tf", "question": text, "options": ["True", "False"],
                              "correct": 0 if answer else 1, "answer_text": ""})
            else:  # fill
                answer = str(item["answer"]).strip()
                if not answer:
                    continue
                clean.append({"type": "fill", "question": text, "options": [],
                              "correct": 0, "answer_text": answer[:100]})
        except (KeyError, TypeError, ValueError, AttributeError):
            continue
    return clean
