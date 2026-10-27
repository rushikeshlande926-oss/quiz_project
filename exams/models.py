import math
import random
from datetime import timedelta

from django.contrib.auth.models import User
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models, transaction
from django.db.models import F, Sum
from django.utils import timezone

from .logic import (
    code_expiry, exam_end_time, personal_deadline, seconds_left, warning_level,
)

CODE_CHARS = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # no 0/O/1/I so codes are easy to read


class StudentProfile(models.Model):
    """Remembers a student's name and roll number so they don't retype it for every exam."""
    user = models.OneToOneField(User, on_delete=models.CASCADE)
    full_name = models.CharField(max_length=100)
    roll_number = models.CharField(max_length=30)

    def __str__(self):
        return f"{self.full_name} ({self.roll_number})"


class Exam(models.Model):
    """A live, teacher-controlled quiz."""
    STATUS_CHOICES = [("draft", "Draft"), ("live", "Live"), ("closed", "Closed")]
    EXPIRY_CHOICES = [
        ("rotate", "Create a new code automatically, so more students can join"),
        ("lock", "Stop new students from joining"),
    ]
    LIMIT_CHOICES = [
        ("flag", "Only flag the student for the teacher"),
        ("submit", "Flag the student and submit their quiz automatically"),
        ("terminate", "Flag the student and terminate their quiz"),
    ]

    teacher = models.ForeignKey(User, on_delete=models.CASCADE, related_name="exams")
    title = models.CharField("Quiz title", max_length=200)
    description = models.TextField("Description / instructions", blank=True)
    duration_minutes = models.PositiveIntegerField(
        "Duration for each student (minutes)", default=30,
        validators=[MinValueValidator(1), MaxValueValidator(600)],
    )
    scheduled_start = models.DateTimeField("Scheduled start time (optional)", null=True, blank=True)
    deadline = models.DateTimeField("Deadline (optional)", null=True, blank=True)

    status = models.CharField(max_length=6, choices=STATUS_CHOICES, default="draft")
    started_at = models.DateTimeField(null=True, blank=True)
    ends_at = models.DateTimeField(null=True, blank=True)
    closed_at = models.DateTimeField(null=True, blank=True)
    close_reason = models.CharField(max_length=20, blank=True)  # "teacher" or "time_expired"

    # test code settings
    code_validity_minutes = models.PositiveIntegerField(
        "Code stays valid for (minutes, 0 = never expires)", default=10,
        validators=[MaxValueValidator(1440)],
    )
    on_code_expiry = models.CharField(
        "When the code expires", max_length=6, choices=EXPIRY_CHOICES, default="rotate",
    )

    # anti-cheating settings
    max_warnings = models.PositiveIntegerField(
        "Warnings allowed per student", default=3, validators=[MaxValueValidator(20)],
    )
    on_violation_limit = models.CharField(
        "When a student goes over the limit", max_length=9, choices=LIMIT_CHOICES, default="flag",
    )

    # time and result settings
    auto_submit_on_expiry = models.BooleanField(
        "When time runs out or you close the quiz, submit the saved answers automatically",
        default=True,
    )
    show_score = models.BooleanField("Show the score to students after they submit", default=False)

    # exam environment settings
    shuffle_questions = models.BooleanField("Show the questions in a different order to each student", default=True)
    shuffle_options = models.BooleanField("Show the options of each question in a different order", default=True)
    require_fullscreen = models.BooleanField(
        "Students must stay in fullscreen mode (leaving it counts as a warning)", default=True,
    )
    block_clipboard = models.BooleanField("Block copy, cut, paste and right-click on the quiz page", default=True)

    # set while the teacher has paused the whole quiz
    paused_at = models.DateTimeField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)

    # Reconducted tests: every session is its own Exam row (own code, own attempts, own results).
    # `original` points at the very first session; it is empty for the original test itself.
    original = models.ForeignKey(
        "self", null=True, blank=True, on_delete=models.SET_NULL, related_name="reconducted_sessions",
    )
    session_number = models.PositiveIntegerField(default=1)

    def __str__(self):
        return self.title

    # ---------- sessions ----------
    @property
    def root(self):
        return self.original or self

    @property
    def is_reconducted(self):
        return self.original_id is not None

    def session_label(self):
        return "Original test" if not self.is_reconducted else f"Reconducted - session {self.session_number}"

    def all_sessions(self):
        """Original test + every reconducted session, oldest first."""
        root = self.root
        return Exam.objects.filter(models.Q(id=root.id) | models.Q(original=root)).order_by("session_number", "id")

    def reconduct(self, teacher, data):
        """
        Creates a NEW draft session from this test: copies the questions and settings,
        applies the changed settings in `data` (a dict of Exam field values). Nothing about
        this session (answers, marks, attempts, codes) is touched.
        """
        with transaction.atomic():
            root = self.root
            last = Exam.objects.filter(models.Q(id=root.id) | models.Q(original=root)).aggregate(
                m=models.Max("session_number"))["m"] or 1
            new = Exam(teacher=teacher, original=root, session_number=last + 1, status="draft", **data)
            new.save()
            ExamQuestion.objects.bulk_create([
                ExamQuestion(
                    exam=new, order=q.order, text=q.text,
                    option_a=q.option_a, option_b=q.option_b, option_c=q.option_c, option_d=q.option_d,
                    correct=q.correct, marks=q.marks,
                )
                for q in self.questions.all()
            ])
        return new

    # ---------- simple info ----------
    def total_marks(self):
        return self.questions.aggregate(t=Sum("marks"))["t"] or 0

    @property
    def is_editable(self):
        return self.status == "draft"

    # ---------- pause and extra time for everyone ----------
    @property
    def is_paused(self):
        return self.paused_at is not None

    def running_pause_seconds(self, now=None):
        """How long the quiz has been paused right now (0 when it is not paused)."""
        if not self.paused_at:
            return 0.0
        return max(0.0, ((now or timezone.now()) - self.paused_at).total_seconds())

    def effective_end(self, now=None):
        """Closing time including the pause happening right now (so the clock is frozen while paused)."""
        if not self.ends_at:
            return None
        return self.ends_at + timedelta(seconds=self.running_pause_seconds(now))

    def seconds_until_end(self):
        if self.status == "live" and self.ends_at:
            now = timezone.now()
            return seconds_left(self.effective_end(now), now)
        return None

    def window_open(self, now=None):
        """True while the quiz is live and its closing time has not been reached."""
        now = now or timezone.now()
        end = self.effective_end(now)
        return self.status == "live" and end is not None and now < end

    def pause_all(self):
        """Freezes every student's timer and stops answering until the teacher resumes."""
        now = timezone.now()
        if not self.window_open(now):
            return False
        updated = Exam.objects.filter(id=self.id, status="live", paused_at__isnull=True).update(paused_at=now)
        self.refresh_from_db()
        if updated:
            for a in self.attempts.filter(status="active"):
                a.log("paused", detail="Paused for everyone")
        return bool(updated)

    def resume_all(self):
        """
        Unfreezes the quiz. The closing time moves later by the length of the pause, and every student
        who was taking the quiz gets exactly that time back, so nobody loses a second.
        """
        if not self.paused_at:
            return False
        now = timezone.now()
        with transaction.atomic():
            fresh = Exam.objects.select_for_update().get(id=self.id)
            if fresh.paused_at is None or fresh.status != "live":
                return False
            length = math.ceil((now - fresh.paused_at).total_seconds())
            updated = Exam.objects.filter(id=self.id, paused_at=fresh.paused_at).update(
                paused_at=None, ends_at=fresh.ends_at + timedelta(seconds=length),
            )
            if updated:
                # students who were paused one by one stay paused until the teacher resumes them
                self.attempts.filter(status="active", paused_at__isnull=True).update(
                    shift_seconds=F("shift_seconds") + length,
                )
        self.refresh_from_db()
        if updated:
            for a in self.attempts.filter(status="active"):
                a.log("resumed", detail="Quiz resumed for everyone")
        return bool(updated)

    def add_time_all(self, seconds):
        """Gives every student who is taking the quiz more time and moves the closing time later."""
        now = timezone.now()
        if seconds <= 0 or not self.window_open(now):
            return False
        with transaction.atomic():
            fresh = Exam.objects.select_for_update().get(id=self.id)
            Exam.objects.filter(id=self.id).update(ends_at=fresh.ends_at + timedelta(seconds=seconds))
            self.attempts.filter(status="active").update(shift_seconds=F("shift_seconds") + seconds)
        self.refresh_from_db()
        for a in self.attempts.filter(status="active"):
            a.log("extra_time", detail=f"+{round(seconds / 60)} min for everyone")
        return True

    # ---------- lifecycle ----------
    def start(self):
        """Open the quiz and create the first test code."""
        now = timezone.now()
        if self.deadline and self.deadline <= now:
            raise ValueError("The deadline has already passed. Edit the quiz and set a later deadline.")
        if not self.questions.exists():
            raise ValueError("Add at least one question before starting the quiz.")
        ends_at = exam_end_time(now, self.duration_minutes, self.deadline)
        # Conditional update: if two requests try to start at once, only one wins.
        updated = Exam.objects.filter(id=self.id, status="draft").update(
            status="live", started_at=now, ends_at=ends_at,
        )
        self.refresh_from_db()
        if updated:
            self.new_code()

    def close(self, reason):
        """Close the quiz for everyone and finish all unfinished attempts."""
        now = timezone.now()
        when = self.ends_at if (reason == "time_expired" and self.ends_at) else now
        updated = Exam.objects.filter(id=self.id, status="live").update(
            status="closed", closed_at=when, close_reason=reason,
        )
        self.refresh_from_db()
        if not updated:
            return False
        self.codes.filter(is_active=True).update(is_active=False, deactivated_reason="quiz_closed")
        for attempt in self.attempts.filter(status__in=["joined", "active"]).select_related("exam"):
            attempt.sync()
        return True

    def sync(self):
        """
        Applies time-based changes. It runs on every poll/request, so nothing needs a
        background scheduler: scheduled start, automatic close, code expiry, and
        finishing students whose own time is over.
        """
        now = timezone.now()
        if self.status == "draft" and self.scheduled_start and now >= self.scheduled_start:
            try:
                self.start()
            except ValueError:
                pass
        if self.status == "live":
            self.sync_code()
            for attempt in self.attempts.filter(status="active").select_related("exam"):
                attempt.sync()
            # The quiz closes by itself after its closing time, but only once nobody is still working
            # (a student the teacher gave extra time can keep going past the closing time).
            end = self.effective_end(now)
            if end and now >= end and not self.attempts.filter(status="active").exists():
                self.close("time_expired")

    # ---------- test codes ----------
    def current_code(self):
        return self.codes.filter(is_active=True).order_by("-id").first()

    def new_code(self):
        """Create a fresh code. The previous one stops working immediately."""
        self.codes.filter(is_active=True).update(is_active=False, deactivated_reason="replaced")
        return TestCode.objects.create(
            exam=self,
            code=TestCode.make_code(),
            expires_at=code_expiry(timezone.now(), self.code_validity_minutes),
        )

    def invalidate_code(self):
        self.codes.filter(is_active=True).update(is_active=False, deactivated_reason="invalidated")

    def sync_code(self):
        code = self.current_code()
        if code and code.expires_at and timezone.now() >= code.expires_at:
            expired_now = TestCode.objects.filter(id=code.id, is_active=True).update(
                is_active=False, deactivated_reason="expired",
            )
            if expired_now and self.on_code_expiry == "rotate":
                self.new_code()


class ExamQuestion(models.Model):
    exam = models.ForeignKey(Exam, on_delete=models.CASCADE, related_name="questions")
    order = models.PositiveIntegerField(default=0)
    text = models.TextField()
    option_a = models.CharField(max_length=300)
    option_b = models.CharField(max_length=300)
    option_c = models.CharField(max_length=300)
    option_d = models.CharField(max_length=300)
    correct = models.IntegerField(default=0)  # 0 = A ... 3 = D
    marks = models.PositiveIntegerField(default=1)

    class Meta:
        ordering = ["order", "id"]

    @property
    def options(self):
        return [self.option_a, self.option_b, self.option_c, self.option_d]

    def __str__(self):
        return self.text[:60]


class TestCode(models.Model):
    exam = models.ForeignKey(Exam, on_delete=models.CASCADE, related_name="codes")
    code = models.CharField(max_length=6, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField(null=True, blank=True)
    is_active = models.BooleanField(default=True)
    deactivated_reason = models.CharField(max_length=20, blank=True)  # expired/replaced/invalidated/quiz_closed

    @classmethod
    def make_code(cls):
        while True:
            code = "".join(random.choices(CODE_CHARS, k=6))
            if not cls.objects.filter(code=code).exists():
                return code

    def is_usable(self):
        return self.is_active and (self.expires_at is None or timezone.now() < self.expires_at)

    def seconds_left(self):
        if self.expires_at is None:
            return None
        return seconds_left(self.expires_at, timezone.now())

    def __str__(self):
        return self.code


class ExamAttempt(models.Model):
    STATUS_CHOICES = [
        ("joined", "Joined"),
        ("active", "In progress"),
        ("submitted", "Submitted"),
        ("terminated", "Terminated"),
        ("closed", "Closed"),
    ]

    exam = models.ForeignKey(Exam, on_delete=models.CASCADE, related_name="attempts")
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="exam_attempts")
    full_name = models.CharField(max_length=100)
    roll_number = models.CharField(max_length=30)
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default="joined")

    started_at = models.DateTimeField(null=True, blank=True)
    ended_at = models.DateTimeField(null=True, blank=True)
    end_reason = models.CharField(max_length=30, blank=True)
    score = models.IntegerField(default=0)
    total_marks = models.IntegerField(default=0)

    warning_count = models.PositiveIntegerField(default=0)
    flagged = models.BooleanField(default=False)
    termination_reason = models.CharField(max_length=300, blank=True)
    terminated_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")

    last_seen = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    # restart support: attempt_number 1 = first try; each teacher restart adds one
    attempt_number = models.PositiveIntegerField(default=1)
    restart_notice = models.BooleanField(default=False)  # student has not yet seen "your quiz was restarted"

    # pause / extra time for this one student (see logic.personal_deadline)
    paused_at = models.DateTimeField(null=True, blank=True)
    shift_seconds = models.PositiveIntegerField(default=0)  # added by class-wide pause / extra time
    slack_seconds = models.PositiveIntegerField(default=0)  # added for this student only

    # decides this student's question and option order (0 = not created yet)
    shuffle_seed = models.PositiveIntegerField(default=0)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["exam", "user"], name="one_exam_attempt_per_student"),
            models.UniqueConstraint(fields=["exam", "roll_number"], name="one_exam_attempt_per_roll_number"),
        ]

    def __str__(self):
        return f"{self.full_name} - {self.exam.title}"

    # ---------- time (always decided by the server) ----------
    @property
    def is_paused(self):
        """Paused by the teacher, either just this student or the whole quiz."""
        return self.paused_at is not None or self.exam.is_paused

    def deadline_at(self, now):
        """When this student's time ends, counting every pause and every bit of extra time."""
        if not self.started_at:
            return None
        exam = self.exam
        class_run = exam.running_pause_seconds(now)
        own_run = max(0.0, (now - self.paused_at).total_seconds()) if self.paused_at else 0.0
        # a student who was paused one by one is not part of the whole-quiz pause
        shift = self.shift_seconds + (0 if self.paused_at else class_run)
        slack = self.slack_seconds + own_run
        end = exam.ends_at + timedelta(seconds=class_run) if exam.ends_at else None
        deadline = personal_deadline(self.started_at, exam.duration_minutes, end, shift, slack)
        if exam.status == "closed" and exam.closed_at:
            deadline = min(deadline, exam.closed_at)  # closing the quiz stops everyone
        return deadline

    @property
    def deadline(self):
        return self.deadline_at(timezone.now())

    def remaining_seconds(self):
        now = timezone.now()
        deadline = self.deadline_at(now)
        if deadline is None:
            return None
        return seconds_left(deadline, now)

    def is_open(self):
        """True only while the student may still answer or submit."""
        return (
            self.status == "active"
            and self.exam.status == "live"
            and not self.is_paused
            and self.remaining_seconds() > 0
        )

    # ---------- display ----------
    @property
    def status_key(self):
        if self.status != "active":
            return self.status
        if self.is_paused:
            return "paused"
        if self.flagged:
            return "flagged"
        return "warning" if self.warning_count else "active"

    @property
    def display_status(self):
        return {
            "joined": "Joined",
            "active": "Active",
            "paused": "Paused",
            "warning": "Warning Issued",
            "flagged": "Flagged",
            "submitted": "Submitted",
            "terminated": "Terminated",
            "closed": "Closed (not submitted)",
        }[self.status_key]

    def end_info(self):
        """Message shown to the student once the attempt is over."""
        s, r = self.status, self.end_reason
        if s == "submitted":
            if r == "student":
                return {"title": "Quiz submitted", "message": "Your answers have been submitted. Thank you!", "tone": "ok"}
            if r == "closed_by_teacher":
                return {"title": "Quiz closed by your teacher",
                        "message": "Your teacher closed the quiz. Your saved answers were submitted automatically.",
                        "tone": "warn"}
            if r == "violation_limit":
                return {"title": "Quiz submitted automatically",
                        "message": "You went over the warning limit, so your quiz was submitted automatically.",
                        "tone": "warn"}
            return {"title": "Time is up",
                    "message": "Your time ended, so your saved answers were submitted automatically.",
                    "tone": "ok"}
        if s == "terminated":
            who = "automatically" if self.terminated_by_id is None else "by your teacher"
            reason = self.termination_reason or "No reason was given."
            return {"title": "Quiz terminated",
                    "message": f"Your quiz was terminated {who}. Reason: {reason}",
                    "tone": "bad"}
        if s == "closed":
            if r == "quiz_closed_before_start":
                return {"title": "Quiz closed", "message": "The quiz was closed before you started it.", "tone": "warn"}
            return {"title": "Quiz closed",
                    "message": "The quiz ended before your answers were submitted, so they were not submitted.",
                    "tone": "warn"}
        if s == "joined" and self.restart_notice:
            return {"title": "Your quiz was restarted",
                    "message": "Your teacher restarted your quiz. Your earlier attempt is kept on record. "
                               "Press Continue to read the rules and start again from the beginning.",
                    "tone": "warn"}
        return {"title": "", "message": "", "tone": "ok"}

    def ensure_seed(self):
        """Gives this student their own random seed (used to shuffle questions and options)."""
        if not self.shuffle_seed:
            ExamAttempt.objects.filter(id=self.id, shuffle_seed=0).update(shuffle_seed=random.randint(1, 2**31 - 1))
            self.refresh_from_db()

    # ---------- events ----------
    def log(self, kind, detail="", warning_number=0):
        return ExamEvent.objects.create(
            exam_id=self.exam_id, attempt=self, kind=kind, detail=detail, warning_number=warning_number,
            attempt_number=self.attempt_number,
        )

    def touch(self):
        """Remember when the student's page last contacted the server (at most every 10 seconds)."""
        now = timezone.now()
        if not self.last_seen or (now - self.last_seen).total_seconds() > 10:
            ExamAttempt.objects.filter(id=self.id).update(last_seen=now)

    # ---------- answers and grading ----------
    def answered_count(self):
        return self.answers.count()

    def grade(self):
        questions = list(self.exam.questions.all())
        chosen = dict(self.answers.values_list("question_id", "choice"))
        score = sum(q.marks for q in questions if chosen.get(q.id) == q.correct)
        return score, sum(q.marks for q in questions)

    # ---------- state changes ----------
    def start(self):
        now = timezone.now()
        updated = ExamAttempt.objects.filter(id=self.id, status="joined").update(
            status="active", started_at=now, last_seen=now, restart_notice=False,
        )
        self.refresh_from_db()
        if updated:
            self.log("started")

    def _end(self, new_status, reason, event_kind, detail="", grade=True, ended_at=None, **extra):
        """Finishes the attempt exactly once (a conditional update protects against double-clicks)."""
        if grade:
            score, total = self.grade()
        else:
            score, total = 0, self.exam.total_marks()
        updated = ExamAttempt.objects.filter(id=self.id, status__in=["joined", "active"]).update(
            status=new_status, ended_at=ended_at or timezone.now(), end_reason=reason,
            score=score, total_marks=total, **extra,
        )
        self.refresh_from_db()
        if updated:
            self.log(event_kind, detail=detail)
        return bool(updated)

    def submit(self):
        return self._end("submitted", "student", "submitted")

    def terminate(self, by_user, reason):
        """Stops the student at once. Saved answers are kept and graded for the teacher to review."""
        return self._end(
            "terminated", "teacher", "terminated", detail=reason or "No reason given",
            termination_reason=reason, terminated_by=by_user,
        )

    def sync(self):
        """If this student's time is over, or the quiz was closed, finish the attempt."""
        exam = self.exam
        if self.status == "active":
            if exam.status == "closed" or self.remaining_seconds() == 0:
                reason = "closed_by_teacher" if exam.close_reason == "teacher" else "time_expired"
                if exam.auto_submit_on_expiry:
                    self._end("submitted", reason, "auto_submitted",
                              detail="Saved answers submitted automatically", ended_at=self.deadline)
                else:
                    self._end("closed", reason, "closed", detail="Time ended, answers not submitted",
                              grade=False, ended_at=self.deadline)
                return True
        elif self.status == "joined" and exam.status == "closed":
            self._end("closed", "quiz_closed_before_start", "closed", grade=False)
            return True
        return False

    def pause(self, by_user):
        """Freezes THIS student's timer. Returns (ok, message)."""
        if self.status != "active":
            return False, "Only a student who is taking the quiz can be paused."
        if self.exam.is_paused:
            return False, "The whole quiz is already paused."
        if self.paused_at:
            return False, "This student is already paused."
        if self.remaining_seconds() == 0:
            return False, "This student's time is already over."
        updated = ExamAttempt.objects.filter(id=self.id, status="active", paused_at__isnull=True).update(
            paused_at=timezone.now(),
        )
        self.refresh_from_db()
        if not updated:
            return False, "This student's status just changed. Please refresh."
        self.log("paused", detail=f"Paused by {by_user.username}")
        return True, ""

    def resume(self, by_user):
        """Unfreezes THIS student. The time they were paused is given back to them. Returns (ok, message)."""
        if self.exam.is_paused:
            return False, "The whole quiz is paused. Resume the whole quiz first."
        if self.status != "active" or not self.paused_at:
            return False, "This student is not paused."
        length = math.ceil((timezone.now() - self.paused_at).total_seconds())
        updated = ExamAttempt.objects.filter(id=self.id, status="active", paused_at=self.paused_at).update(
            paused_at=None, slack_seconds=F("slack_seconds") + length,
        )
        self.refresh_from_db()
        if not updated:
            return False, "This student's status just changed. Please refresh."
        self.log("resumed", detail=f"Resumed by {by_user.username}")
        return True, ""

    def add_time(self, by_user, seconds):
        """Gives THIS student extra time (it may go past the quiz closing time). Returns (ok, message)."""
        if self.status != "active":
            return False, "Extra time can only be added while the student is taking the quiz."
        if seconds <= 0:
            return False, "Enter the extra time in minutes."
        updated = ExamAttempt.objects.filter(id=self.id, status="active").update(
            slack_seconds=F("slack_seconds") + seconds,
        )
        self.refresh_from_db()
        if not updated:
            return False, "This student's status just changed. Please refresh."
        self.log("extra_time", detail=f"+{round(seconds / 60)} min by {by_user.username}")
        return True, ""

    def can_restart(self):
        """Restart only makes sense once the student has begun, and only while the exam session is running."""
        exam = self.exam
        return (
            self.status in ("active", "submitted", "terminated", "closed")
            and exam.status == "live"
            and exam.ends_at is not None
            and timezone.now() < exam.ends_at
        )

    def restart(self, by_user, note=""):
        """
        Gives THIS student a fresh start. The old attempt is copied into ExamAttemptHistory first
        (answers, marks, warnings, who/when), so nothing is lost. Other students are never touched.
        Returns (ok, message).
        """
        with transaction.atomic():
            # lock this one row (a no-op on SQLite, a real lock on PostgreSQL/MySQL)
            locked = ExamAttempt.objects.select_for_update().select_related("exam").get(id=self.id)
            if not locked.can_restart():
                return False, "This attempt cannot be restarted now (the quiz must still be running and the student must have started)."
            chosen = dict(locked.answers.values_list("question_id", "choice"))
            snapshot = []
            for q in locked.exam.questions.all():
                c = chosen.get(q.id)
                snapshot.append({
                    "question": q.text, "marks": q.marks,
                    "chosen": q.options[c] if c is not None else None,
                    "correct": q.options[q.correct],
                    "is_correct": c == q.correct,
                })
            score, total = locked.grade()
            if locked.status == "active":  # unfinished attempt: store the marks it had so far
                locked.score, locked.total_marks = score, total
            ExamAttemptHistory.objects.create(
                attempt=locked, attempt_number=locked.attempt_number, status=locked.status,
                end_reason=locked.end_reason, started_at=locked.started_at, ended_at=locked.ended_at,
                score=locked.score, total_marks=locked.total_marks or total,
                warning_count=locked.warning_count, flagged=locked.flagged,
                termination_reason=locked.termination_reason, terminated_by=locked.terminated_by,
                answers=snapshot, restarted_by=by_user, note=note[:300],
            )
            updated = ExamAttempt.objects.filter(id=locked.id, attempt_number=locked.attempt_number).update(
                status="joined", started_at=None, ended_at=None, end_reason="",
                score=0, total_marks=0, warning_count=0, flagged=False,
                termination_reason="", terminated_by=None, last_seen=None,
                attempt_number=F("attempt_number") + 1, restart_notice=True,
                paused_at=None, shift_seconds=0, slack_seconds=0, shuffle_seed=0,  # new order on the new try
            )
            if not updated:
                return False, "This attempt was just changed. Please refresh and try again."
            ExamAnswer.objects.filter(attempt_id=locked.id).delete()
            self.refresh_from_db()
            self.log("restarted", detail=f"Restarted by {by_user.username} (attempt {self.attempt_number - 1} archived)")
        return True, ""

    def record_tab_switch(self):
        return self.record_violation("tab_switch")

    def record_violation(self, kind="tab_switch"):
        """
        Stores one warning (tab switch or leaving fullscreen) and applies the teacher's rule
        when the limit is exceeded.
        """
        ExamAttempt.objects.filter(id=self.id).update(warning_count=F("warning_count") + 1)
        self.refresh_from_db()
        exam = self.exam
        event = self.log(kind, warning_number=self.warning_count)
        level = warning_level(self.warning_count, exam.max_warnings)
        if level == "exceeded":
            if not self.flagged:
                ExamAttempt.objects.filter(id=self.id).update(flagged=True)
                self.flagged = True
                self.log("flagged", detail=f"Went over the limit of {exam.max_warnings} warnings",
                         warning_number=self.warning_count)
            if exam.on_violation_limit == "submit":
                self._end("submitted", "violation_limit", "auto_submitted",
                          detail="Submitted automatically: warning limit exceeded")
            elif exam.on_violation_limit == "terminate":
                self._end("terminated", "violation_limit", "terminated",
                          detail="Terminated automatically: warning limit exceeded",
                          termination_reason="Automatically terminated: went over the warning limit.")
        return event, level


class ExamAnswer(models.Model):
    """One saved answer. A separate row per answer means autosaves can never overwrite each other."""
    attempt = models.ForeignKey(ExamAttempt, on_delete=models.CASCADE, related_name="answers")
    question = models.ForeignKey(ExamQuestion, on_delete=models.CASCADE)
    choice = models.IntegerField()  # 0 = A ... 3 = D
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["attempt", "question"], name="one_answer_per_question"),
        ]


class ExamEvent(models.Model):
    """Every notable thing a student does: joined, tab switch, flagged, submitted, terminated..."""
    KINDS = [
        ("joined", "Joined the quiz"),
        ("started", "Started the quiz"),
        ("tab_switch", "Switched tab or left the quiz page"),
        ("flagged", "Flagged: warning limit exceeded"),
        ("submitted", "Submitted the quiz"),
        ("auto_submitted", "Submitted automatically"),
        ("terminated", "Quiz terminated"),
        ("closed", "Quiz closed"),
        ("restarted", "Quiz restarted by teacher"),
        ("fullscreen_exit", "Left fullscreen mode"),
        ("paused", "Paused by teacher"),
        ("resumed", "Resumed"),
        ("extra_time", "Extra time added"),
    ]
    exam = models.ForeignKey(Exam, on_delete=models.CASCADE, related_name="events")
    attempt = models.ForeignKey(ExamAttempt, on_delete=models.CASCADE, related_name="events")
    kind = models.CharField(max_length=15, choices=KINDS)
    detail = models.CharField(max_length=300, blank=True)
    warning_number = models.PositiveIntegerField(default=0)
    away_seconds = models.PositiveIntegerField(null=True, blank=True)
    attempt_number = models.PositiveIntegerField(default=1)  # which try of the student this event belongs to
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["id"]


class ExamAttemptHistory(models.Model):
    """A frozen copy of one finished-or-interrupted try, written when a teacher restarts a student."""
    attempt = models.ForeignKey(ExamAttempt, on_delete=models.CASCADE, related_name="history")
    attempt_number = models.PositiveIntegerField()
    status = models.CharField(max_length=10)
    end_reason = models.CharField(max_length=30, blank=True)
    started_at = models.DateTimeField(null=True, blank=True)
    ended_at = models.DateTimeField(null=True, blank=True)
    score = models.IntegerField(default=0)
    total_marks = models.IntegerField(default=0)
    warning_count = models.PositiveIntegerField(default=0)
    flagged = models.BooleanField(default=False)
    termination_reason = models.CharField(max_length=300, blank=True)
    terminated_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    answers = models.JSONField(default=list)  # [{question, marks, chosen, correct, is_correct}]
    restarted_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    restarted_at = models.DateTimeField(auto_now_add=True)
    note = models.CharField(max_length=300, blank=True)

    class Meta:
        ordering = ["attempt_number"]

    def events(self):
        return self.attempt.events.filter(attempt_number=self.attempt_number)
