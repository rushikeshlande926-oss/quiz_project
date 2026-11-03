"""All the Gemini (AI) code is in this one file."""
import json

from django.conf import settings
from google import genai
from google.genai import types

from .logic import build_prompt, parse_questions


def generate_questions(files_data, num_questions, question_type):
    """
    files_data = list of (filename, mime_type, bytes).
    PDFs and images are sent to Gemini directly, so scanned and handwritten notes work too.
    """
    if not settings.GEMINI_API_KEY:
        raise ValueError("GEMINI_API_KEY is not set. See README step 5.")

    client = genai.Client(api_key=settings.GEMINI_API_KEY)

    parts = []
    for name, mime, raw in files_data:
        if mime == "text/plain":
            parts.append("NOTES (" + name + "):\n" + raw.decode("utf-8", errors="ignore")[:15000])
        else:
            parts.append(types.Part.from_bytes(data=raw, mime_type=mime))
    parts.append(build_prompt(num_questions, question_type))

    response = client.models.generate_content(
        model=settings.GEMINI_MODEL,
        contents=parts,
        config={"response_mime_type": "application/json"},
    )

    if not response.text:
        raise ValueError("Gemini sent an empty reply. Please try again.")
    text = response.text.strip()
    # Sometimes the model wraps the JSON in ```json ... ``` - remove that.
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:]

    data = json.loads(text)
    if isinstance(data, dict):  # in case it returns {"questions": [...]}
        data = data.get("questions", [])

    questions = parse_questions(data, question_type)
    if not questions:
        raise ValueError("Gemini did not return usable questions. Try again.")
    return questions
