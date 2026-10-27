import hmac
from datetime import timedelta

from django import forms
from django.conf import settings
from django.contrib.auth.forms import AuthenticationForm, UserCreationForm
from django.contrib.auth.models import User
from django.utils import timezone

from .logic import parse_exam_questions
from .models import Exam

DATETIME_FORMATS = ["%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M:%S"]


def is_teacher(user):
    return user.is_authenticated and user.groups.filter(name="Teachers").exists()


# ----------------------------- teacher accounts -----------------------------

class TeacherAuthForm(AuthenticationForm):
    """Teacher login: refuses accounts that are not teachers."""
    def confirm_login_allowed(self, user):
        super().confirm_login_allowed(user)
        if not is_teacher(user):
            raise forms.ValidationError(
                "This is not a teacher account. Students should use the student login.",
                code="not_teacher",
            )


class TeacherRegisterForm(UserCreationForm):
    invite_code = forms.CharField(
        label="Teacher invite code",
        widget=forms.PasswordInput(attrs={"autocomplete": "off"}),
        help_text="Ask the project owner for this code. It stops students from creating teacher accounts.",
    )

    class Meta(UserCreationForm.Meta):
        model = User
        fields = ("username",)

    def clean_invite_code(self):
        entered = self.cleaned_data["invite_code"].strip()
        expected = settings.TEACHER_INVITE_CODE
        if not hmac.compare_digest(entered.encode(), expected.encode()):
            raise forms.ValidationError("Wrong invite code.")
        return entered


# ----------------------------- creating a quiz ------------------------------

class ExamForm(forms.ModelForm):
    questions_json = forms.CharField(widget=forms.HiddenInput, required=False)

    class Meta:
        model = Exam
        fields = [
            "title", "description", "duration_minutes", "scheduled_start", "deadline",
            "code_validity_minutes", "on_code_expiry",
            "max_warnings", "on_violation_limit",
            "auto_submit_on_expiry", "show_score",
            "shuffle_questions", "shuffle_options", "require_fullscreen", "block_clipboard",
        ]
        widgets = {
            "title": forms.TextInput(attrs={"placeholder": "e.g. Operating System - Unit Test 1"}),
            "description": forms.Textarea(attrs={
                "rows": 3,
                "placeholder": "Instructions or topics covered. Students read this before they start.",
            }),
            "scheduled_start": forms.DateTimeInput(format="%Y-%m-%dT%H:%M", attrs={"type": "datetime-local"}),
            "deadline": forms.DateTimeInput(format="%Y-%m-%dT%H:%M", attrs={"type": "datetime-local"}),
            "on_code_expiry": forms.RadioSelect,
            "on_violation_limit": forms.RadioSelect,
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name in ("scheduled_start", "deadline"):
            self.fields[name].input_formats = DATETIME_FORMATS
            self.fields[name].required = False
        self.parsed_questions = []

    def clean(self):
        cleaned = super().clean()
        now = timezone.now()
        start, deadline = cleaned.get("scheduled_start"), cleaned.get("deadline")
        if start and start < now - timedelta(minutes=1):
            self.add_error("scheduled_start", "The start time must be in the future (or leave it empty).")
        if deadline and deadline <= now:
            self.add_error("deadline", "The deadline must be in the future (or leave it empty).")
        if start and deadline and deadline <= start:
            self.add_error("deadline", "The deadline must be after the start time.")

        questions, error = parse_exam_questions(cleaned.get("questions_json"))
        if error:
            self.add_error(None, error)
        self.parsed_questions = questions
        return cleaned


class ReconductForm(forms.ModelForm):
    """Settings for a reconducted session. Questions are copied from the original, so they are not asked again."""
    start_now = forms.BooleanField(
        required=False, label="Start this session right now (a new test code is created immediately)",
    )

    class Meta:
        model = Exam
        fields = [
            "title", "description", "duration_minutes", "scheduled_start", "deadline",
            "code_validity_minutes", "on_code_expiry",
            "max_warnings", "on_violation_limit",
            "auto_submit_on_expiry", "show_score",
            "shuffle_questions", "shuffle_options", "require_fullscreen", "block_clipboard",
        ]
        widgets = ExamForm.Meta.widgets

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name in ("scheduled_start", "deadline"):
            self.fields[name].input_formats = DATETIME_FORMATS
            self.fields[name].required = False

    def clean(self):
        cleaned = super().clean()
        now = timezone.now()
        start, deadline = cleaned.get("scheduled_start"), cleaned.get("deadline")
        if start and start < now - timedelta(minutes=1):
            self.add_error("scheduled_start", "The start time must be in the future (or leave it empty).")
        if deadline and deadline <= now:
            self.add_error("deadline", "The deadline must be in the future (or leave it empty).")
        if start and deadline and deadline <= start:
            self.add_error("deadline", "The deadline must be after the start time.")
        if cleaned.get("start_now") and start:
            self.add_error("start_now", "Choose either a scheduled start time or 'start right now', not both.")
        return cleaned


# ----------------------------- students joining -----------------------------

class JoinForm(forms.Form):
    full_name = forms.CharField(
        max_length=100, label="Full name",
        widget=forms.TextInput(attrs={"placeholder": "As in the college records"}),
    )
    roll_number = forms.CharField(
        max_length=30, label="Roll number",
        widget=forms.TextInput(attrs={"placeholder": "e.g. 23"}),
    )
    code = forms.CharField(
        max_length=6, min_length=6, label="Test code",
        widget=forms.TextInput(attrs={
            "placeholder": "6-character code", "autocomplete": "off",
            "style": "text-transform: uppercase; letter-spacing: 3px; font-weight: 700;",
        }),
    )

    def clean_full_name(self):
        return " ".join(self.cleaned_data["full_name"].split())

    def clean_roll_number(self):
        return self.cleaned_data["roll_number"].strip().upper()

    def clean_code(self):
        return self.cleaned_data["code"].strip().upper()
