from django import forms

MIME_TYPES = {
    "pdf": "application/pdf",
    "png": "image/png",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "webp": "image/webp",
    "txt": "text/plain",
}
MAX_FILES = 10
MAX_FILE_MB = 10
MAX_TOTAL_MB = 18


def mime_for(filename):
    return MIME_TYPES[filename.lower().rsplit(".", 1)[-1]]


# Django needs these two small classes to allow selecting several files at once.
class MultipleFileInput(forms.FileInput):
    allow_multiple_selected = True


class MultipleFileField(forms.FileField):
    def __init__(self, *args, **kwargs):
        kwargs.setdefault("widget", MultipleFileInput())
        super().__init__(*args, **kwargs)

    def clean(self, data, initial=None):
        single_clean = super().clean
        if isinstance(data, (list, tuple)):
            return [single_clean(d, initial) for d in data]
        return [single_clean(data, initial)]


class UploadNotesForm(forms.Form):
    title = forms.CharField(
        max_length=200,
        label="Quiz title",
        widget=forms.TextInput(attrs={"placeholder": "e.g. Operating System - Unit 1"}),
    )
    files = MultipleFileField(
        label="Your notes",
        widget=MultipleFileInput(attrs={"multiple": True, "accept": ".pdf,.png,.jpg,.jpeg,.webp,.txt"}),
    )
    question_type = forms.ChoiceField(
        label="Question type",
        choices=[
            ("mcq", "Multiple choice"),
            ("tf", "True / False"),
            ("fill", "Fill in the blank"),
            ("mix", "Mix of all three"),
        ],
        initial="mcq",
    )
    num_questions = forms.IntegerField(label="Number of questions", min_value=3, max_value=20, initial=10)
    is_private = forms.BooleanField(
        required=False,
        label="Private quiz: only students with the join code can play",
    )

    def clean_files(self):
        files = self.cleaned_data["files"]
        if len(files) > MAX_FILES:
            raise forms.ValidationError(f"Please upload at most {MAX_FILES} files.")
        total = 0
        for f in files:
            ext = f.name.lower().rsplit(".", 1)[-1] if "." in f.name else ""
            if ext not in MIME_TYPES:
                raise forms.ValidationError(f"'{f.name}': only PDF, PNG, JPG, WEBP or TXT files are allowed.")
            if f.size > MAX_FILE_MB * 1024 * 1024:
                raise forms.ValidationError(f"'{f.name}' is larger than {MAX_FILE_MB} MB.")
            total += f.size
        if total > MAX_TOTAL_MB * 1024 * 1024:
            raise forms.ValidationError(f"Total size is too large. Maximum is {MAX_TOTAL_MB} MB.")
        return files
