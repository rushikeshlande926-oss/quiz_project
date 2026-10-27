import random

from django.contrib.auth.models import User
from django.db import models
from django.db.models import Q

from .logic import normalize_answer

CODE_CHARS = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # no 0/O/1/I so codes are easy to read


class Quiz(models.Model):
    title = models.CharField(max_length=200)
    created_by = models.ForeignKey(User, on_delete=models.CASCADE)
    created_at = models.DateTimeField(auto_now_add=True)
    is_private = models.BooleanField(default=False)
    join_code = models.CharField(max_length=6, blank=True, default="", db_index=True)
    members = models.ManyToManyField(User, related_name="joined_quizzes", blank=True)

    def save(self, *args, **kwargs):
        if not self.join_code:
            self.join_code = self.make_code()
        super().save(*args, **kwargs)

    @classmethod
    def make_code(cls):
        while True:
            code = "".join(random.choices(CODE_CHARS, k=6))
            if not cls.objects.filter(join_code=code).exists():
                return code

    def can_access(self, user):
        """Public quizzes: everyone. Private quizzes: the creator and students who joined with the code."""
        if not self.is_private or self.created_by_id == user.id:
            return True
        return self.members.filter(id=user.id).exists()

    def __str__(self):
        return self.title


class Question(models.Model):
    QUESTION_TYPES = [
        ("mcq", "Multiple choice"),
        ("tf", "True / False"),
        ("fill", "Fill in the blank"),
    ]

    quiz = models.ForeignKey(Quiz, on_delete=models.CASCADE, related_name="questions")
    qtype = models.CharField(max_length=4, choices=QUESTION_TYPES, default="mcq")
    text = models.TextField()
    option_a = models.CharField(max_length=300, blank=True)
    option_b = models.CharField(max_length=300, blank=True)
    option_c = models.CharField(max_length=300, blank=True)
    option_d = models.CharField(max_length=300, blank=True)
    correct = models.IntegerField(default=0)  # mcq: 0-3 (A-D). tf: 0 = True, 1 = False
    answer_text = models.CharField(max_length=100, blank=True, default="")  # only for fill-in-the-blank

    @property
    def options(self):
        if self.qtype == "tf":
            return ["True", "False"]
        if self.qtype == "fill":
            return []
        return [self.option_a, self.option_b, self.option_c, self.option_d]

    def is_correct(self, answer):
        """Is this answer correct? (answer is an index for mcq/tf, or text for fill)"""
        if answer is None or answer == "":
            return False
        if self.qtype == "fill":
            return normalize_answer(answer) == normalize_answer(self.answer_text)
        return answer == self.correct

    def correct_label(self):
        return self.answer_text if self.qtype == "fill" else self.options[self.correct]

    def answer_label(self, answer):
        if answer is None or answer == "":
            return "Not answered"
        if self.qtype == "fill":
            return str(answer)
        if isinstance(answer, int) and 0 <= answer < len(self.options):
            return self.options[answer]
        return "Not answered"

    def __str__(self):
        return self.text[:60]


class Attempt(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    quiz = models.ForeignKey(Quiz, on_delete=models.CASCADE, related_name="attempts")
    score = models.IntegerField()
    total = models.IntegerField()
    time_taken = models.IntegerField(help_text="seconds")
    answers = models.JSONField(default=dict)  # {"question_id": index or text}
    is_practice = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            # one official attempt per student per quiz; practice attempts are unlimited
            models.UniqueConstraint(
                fields=["user", "quiz"],
                condition=Q(is_practice=False),
                name="one_official_attempt_per_quiz",
            ),
        ]

    @property
    def percent(self):
        return round(self.score * 100 / self.total) if self.total else 0

    @property
    def time_display(self):
        minutes, seconds = divmod(self.time_taken, 60)
        return f"{minutes}:{seconds:02d}"

    def __str__(self):
        return f"{self.user.username} - {self.quiz.title}: {self.score}/{self.total}"


class QuizJob(models.Model):
    """Tracks one 'generate quiz' task so the progress page can show real progress."""
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    title = models.CharField(max_length=200)
    status = models.CharField(max_length=10, default="working")  # working / done / failed
    progress = models.IntegerField(default=0)  # 0 to 100
    message = models.CharField(max_length=200, default="Starting...")
    quiz = models.ForeignKey(Quiz, null=True, blank=True, on_delete=models.SET_NULL)
    error = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.title} ({self.status})"
