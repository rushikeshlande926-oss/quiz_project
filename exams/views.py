import csv
import json
from functools import wraps

from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import Group
from django.contrib.auth.views import LoginView
from django.core.cache import cache
from django.db import IntegrityError, transaction
from django.db.models import Count
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from quiz.exports import safe_cell
from quiz.forms import MAX_FILE_MB, MAX_FILES, MAX_TOTAL_MB, MIME_TYPES
from quiz.gemini_service import generate_questions
from quiz.models import Attempt as PracticeAttempt

from .logic import percent, seeded_order
from .forms import ExamForm, JoinForm, ReconductForm, TeacherAuthForm, TeacherRegisterForm, is_teacher
from .models import (
    Exam, ExamAnswer, ExamAttempt, ExamEvent, ExamQuestion, StudentProfile, TestCode,
)


# ===========================================================================
# Helpers
# ===========================================================================

def teacher_required(view):
    """Only logged-in teachers may open the page."""
    @wraps(view)
    def wrapper(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect(f"{reverse('teacher_login')}?next={request.path}")
        if not is_teacher(request.user):
            messages.error(request, "That page is only for teachers.")
            return redirect("quiz_list")
        return view(request, *args, **kwargs)
    return wrapper


def own_exam(request, exam_id):
    """A teacher can only open their own quizzes."""
    return get_object_or_404(Exam, id=exam_id, teacher=request.user)


def own_attempt(request, attempt_id):
    return get_object_or_404(
        ExamAttempt.objects.select_related("exam"), id=attempt_id, exam__teacher=request.user,
    )


def body_json(request):
    try:
        data = json.loads(request.body or "{}")
        return data if isinstance(data, dict) else {}
    except ValueError:
        return {}


def back_to(request, default):
    """Where to go after a teacher action: the page it came from, but never another website."""
    target = request.POST.get("next") or ""
    if url_has_allowed_host_and_scheme(target, allowed_hosts={request.get_host()}):
        return target
    return default


def minutes_to_seconds(request, max_minutes=240):
    """Reads the 'minutes' field of a form. Returns seconds, or None if it is not a valid number."""
    try:
        minutes = int(request.POST.get("minutes", ""))
    except ValueError:
        return None
    return minutes * 60 if 1 <= minutes <= max_minutes else None


def local_time(value, fmt="%H:%M:%S"):
    return timezone.localtime(value).strftime(fmt) if value else ""


# ===========================================================================
# Teacher: accounts
# ===========================================================================

class TeacherLoginView(LoginView):
    template_name = "exams/teacher_login.html"
    authentication_form = TeacherAuthForm

    def get_success_url(self):
        return self.get_redirect_url() or reverse("teacher_dashboard")


def teacher_register(request):
    if request.method == "POST":
        form = TeacherRegisterForm(request.POST)
        if form.is_valid():
            user = form.save()
            group, _ = Group.objects.get_or_create(name="Teachers")
            user.groups.add(group)
            login(request, user)
            return redirect("teacher_dashboard")
    else:
        form = TeacherRegisterForm()
    return render(request, "exams/teacher_register.html", {"form": form})


# ===========================================================================
# Teacher: dashboard, creating / editing / deleting quizzes
# ===========================================================================

@teacher_required
def teacher_dashboard(request):
    exams = list(
        Exam.objects.filter(teacher=request.user)
        .select_related("original")
        .annotate(num_students=Count("attempts", distinct=True), num_questions=Count("questions", distinct=True))
        .order_by("-created_at")
    )
    for exam in exams:
        exam.sync()
    return render(request, "exams/dashboard.html", {"exams": exams})


def _exam_form_view(request, exam=None):
    if request.method == "POST":
        form = ExamForm(request.POST, instance=exam)
        if form.is_valid():
            with transaction.atomic():
                saved = form.save(commit=False)
                saved.teacher = request.user
                saved.save()
                saved.questions.all().delete()
                ExamQuestion.objects.bulk_create([
                    ExamQuestion(
                        exam=saved, order=i, text=q["text"],
                        option_a=q["options"][0], option_b=q["options"][1],
                        option_c=q["options"][2], option_d=q["options"][3],
                        correct=q["correct"], marks=q["marks"],
                    )
                    for i, q in enumerate(form.parsed_questions)
                ])
            messages.success(request, "Quiz saved.")
            return redirect("exam_monitor", exam_id=saved.id)
        try:  # keep what the teacher typed
            initial_questions = json.loads(form.data.get("questions_json") or "[]")
        except ValueError:
            initial_questions = []
    else:
        form = ExamForm(instance=exam)
        initial_questions = [
            {"text": q.text, "options": q.options, "correct": q.correct, "marks": q.marks}
            for q in exam.questions.all()
        ] if exam else []
    return render(request, "exams/exam_form.html", {
        "form": form, "exam": exam, "initial_questions": initial_questions,
    })


@teacher_required
def exam_create(request):
    return _exam_form_view(request)


@teacher_required
def exam_edit(request, exam_id):
    exam = own_exam(request, exam_id)
    exam.sync()
    if not exam.is_editable:
        messages.error(request, "A quiz can only be edited before it starts.")
        return redirect("exam_monitor", exam_id=exam.id)
    return _exam_form_view(request, exam)


@teacher_required
@require_POST
def exam_delete(request, exam_id):
    exam = own_exam(request, exam_id)
    exam.sync()
    if not exam.is_editable:
        messages.error(request, "A quiz can only be deleted before it starts.")
        return redirect("exam_monitor", exam_id=exam.id)
    exam.delete()
    messages.success(request, "Quiz deleted.")
    return redirect("teacher_dashboard")


# ===========================================================================
# Teacher: reconduct an existing test as a brand-new session
# ===========================================================================

@teacher_required
def reconduct_select(request):
    """Lets the teacher pick which existing test to conduct again."""
    exams = list(
        Exam.objects.filter(teacher=request.user)
        .exclude(status="draft")
        .select_related("original")
        .annotate(num_students=Count("attempts", distinct=True), num_questions=Count("questions", distinct=True))
        .order_by("-created_at")
    )
    exams = [e for e in exams if e.num_questions]
    return render(request, "exams/reconduct_select.html", {"exams": exams})


@teacher_required
def exam_reconduct(request, exam_id):
    source = own_exam(request, exam_id)
    source.sync()
    if source.status == "draft" or not source.questions.exists():
        messages.error(request, "Only a quiz that has already been conducted can be reconducted.")
        return redirect("exam_monitor", exam_id=source.id)

    if request.method == "POST":
        form = ReconductForm(request.POST)
        if form.is_valid():
            data = {name: form.cleaned_data[name] for name in form.Meta.fields}
            new = source.reconduct(request.user, data)
            if form.cleaned_data["start_now"]:
                try:
                    new.start()
                    messages.success(request, "New session started. Share the new test code with your students.")
                except ValueError as e:
                    messages.error(request, str(e))
            else:
                messages.success(request, "New session created from the existing questions. Start it when you are ready.")
            return redirect("exam_monitor", exam_id=new.id)
    else:
        # copy every setting from the source, but leave the dates empty (the old ones are in the past)
        initial = {
            name: getattr(source, name) for name in ReconductForm.Meta.fields
            if name not in ("scheduled_start", "deadline")
        }
        form = ReconductForm(initial=initial)
    return render(request, "exams/reconduct_form.html", {
        "form": form, "source": source,
        "question_count": source.questions.count(), "total_marks": source.total_marks(),
        "next_session": (source.all_sessions().order_by("-session_number").first().session_number + 1),
    })


# ===========================================================================
# Teacher: live monitoring and controls
# ===========================================================================

@teacher_required
def exam_monitor(request, exam_id):
    exam = own_exam(request, exam_id)
    exam.sync()
    return render(request, "exams/monitor.html", {
        "exam": exam,
        "question_count": exam.questions.count(),
        "total_marks": exam.total_marks(),
        "join_url": request.build_absolute_uri(reverse("exam_join")),
        "sessions": list(exam.all_sessions().filter(teacher=request.user)) if exam.is_reconducted or exam.reconducted_sessions.exists() else [],
        "config": {"liveUrl": reverse("exam_live", args=[exam.id]), "status": exam.status, "paused": exam.is_paused},
    })


@teacher_required
def exam_live(request, exam_id):
    """The monitor page asks this every few seconds and redraws itself from the answer."""
    exam = own_exam(request, exam_id)
    exam.sync()
    now = timezone.now()

    # ---- students ----
    question_count = exam.questions.count()
    attempts = list(
        exam.attempts.annotate(n_answers=Count("answers")).order_by("roll_number")
    )
    students = []
    for a in attempts:
        a.exam = exam  # reuse the object, avoids one query per student
        students.append({
            "id": a.id,
            "name": a.full_name,
            "roll": a.roll_number,
            "status_key": a.status_key,
            "status_label": a.display_status,
            "warnings": a.warning_count,
            "flagged": a.flagged,
            "answered": a.n_answers,
            "total_questions": question_count,
            "remaining": a.remaining_seconds() if a.status == "active" else None,
            "online": bool(a.last_seen and (now - a.last_seen).total_seconds() < 20) and a.status in ("joined", "active"),
            "score": f"{a.score}/{a.total_marks}" if a.status in ("submitted", "terminated") else "",
            "detail_url": reverse("attempt_detail", args=[a.id]),
            "terminate_url": reverse("attempt_terminate", args=[a.id]),
            "can_terminate": a.status in ("joined", "active"),
            "can_restart": a.can_restart(),
            "restart_url": reverse("attempt_restart", args=[a.id]),
            "attempt_number": a.attempt_number,
            "paused": a.status == "active" and a.is_paused,
            "can_pause": a.status == "active" and not a.is_paused,
            "can_resume": a.status == "active" and a.paused_at is not None and not exam.is_paused,
            "can_add_time": a.status == "active",
            "pause_url": reverse("attempt_pause", args=[a.id]),
            "resume_url": reverse("attempt_resume", args=[a.id]),
            "add_time_url": reverse("attempt_add_time", args=[a.id]),
        })

    # ---- events (only the new ones after `since`) ----
    try:
        since = int(request.GET.get("since", 0))
    except ValueError:
        since = 0
    events_qs = exam.events.select_related("attempt").filter(id__gt=since)
    events = list(events_qs.order_by("-id")[:50] if since == 0 else events_qs.order_by("id")[:200])
    if since == 0:
        events.reverse()
    event_rows = [{
        "id": e.id,
        "kind": e.kind,
        "label": e.get_kind_display(),
        "name": e.attempt.full_name,
        "roll": e.attempt.roll_number,
        "quiz": exam.title,
        "time": local_time(e.created_at),
        "warnings": e.warning_number,
        "away": e.away_seconds,
        "detail": e.detail,
    } for e in events]

    # ---- test code ----
    active = exam.current_code()
    last = exam.codes.order_by("-id").first()
    code_row = {"value": "", "state": "none", "expires_in": None}
    if active:
        code_row = {"value": active.code, "state": "active", "expires_in": active.seconds_left()}
    elif last:
        code_row = {"value": last.code, "state": last.deactivated_reason or "expired", "expires_in": None}

    starts_in = None
    if exam.status == "draft" and exam.scheduled_start:
        starts_in = max(0, int((exam.scheduled_start - now).total_seconds()))

    return JsonResponse({
        "exam": {
            "status": exam.status,
            "status_label": exam.get_status_display(),
            "remaining": exam.seconds_until_end(),
            "starts_in": starts_in,
            "close_reason": exam.close_reason,
            "paused": exam.is_paused,
            "window_over": exam.status == "live" and not exam.window_open(now),
        },
        "code": code_row,
        "students": students,
        "events": event_rows,
        "last_event_id": events[-1].id if events else since,
    })


@teacher_required
@require_POST
def exam_start(request, exam_id):
    exam = own_exam(request, exam_id)
    exam.sync()
    if exam.status != "draft":
        messages.error(request, "This quiz has already started.")
    else:
        try:
            exam.start()
            messages.success(request, "Quiz started. Share the test code with your students.")
        except ValueError as e:
            messages.error(request, str(e))
    return redirect("exam_monitor", exam_id=exam.id)


@teacher_required
@require_POST
def exam_close(request, exam_id):
    exam = own_exam(request, exam_id)
    exam.sync()
    if exam.status == "live" and exam.close("teacher"):
        messages.success(request, "Quiz closed for all students.")
    else:
        messages.error(request, "This quiz is not running.")
    return redirect("exam_monitor", exam_id=exam.id)


@teacher_required
@require_POST
def exam_pause(request, exam_id):
    exam = own_exam(request, exam_id)
    exam.sync()
    if exam.pause_all():
        messages.success(request, "Quiz paused for everyone. All timers are frozen and nobody can answer until you resume.")
    else:
        messages.error(request, "The quiz cannot be paused now. It must be running, before its closing time, and not already paused.")
    return redirect("exam_monitor", exam_id=exam.id)


@teacher_required
@require_POST
def exam_resume(request, exam_id):
    exam = own_exam(request, exam_id)
    exam.sync()
    if exam.resume_all():
        messages.success(request, "Quiz resumed. Every student got back the time they lost during the pause.")
    else:
        messages.error(request, "The quiz is not paused.")
    return redirect("exam_monitor", exam_id=exam.id)


@teacher_required
@require_POST
def exam_add_time(request, exam_id):
    """Extra time for everyone who is taking the quiz right now."""
    exam = own_exam(request, exam_id)
    exam.sync()
    seconds = minutes_to_seconds(request)
    if seconds is None:
        messages.error(request, "Enter the extra time in minutes (1 to 240).")
    elif exam.add_time_all(seconds):
        messages.success(request, f"{seconds // 60} minutes added for everyone taking the quiz. The closing time moved later too.")
    else:
        messages.error(request, "Time can only be added while the quiz is running and before its closing time. "
                                "For one student, use Add time on their row.")
    return redirect("exam_monitor", exam_id=exam.id)


@teacher_required
@require_POST
def attempt_pause(request, attempt_id):
    attempt = own_attempt(request, attempt_id)
    attempt.exam.sync()
    attempt.refresh_from_db()
    ok, why = attempt.pause(request.user)
    if ok:
        messages.success(request, f"{attempt.full_name} is paused. Their timer is frozen and they cannot answer. Other students are not affected.")
    else:
        messages.error(request, why)
    return redirect(back_to(request, reverse("exam_monitor", args=[attempt.exam_id])))


@teacher_required
@require_POST
def attempt_resume(request, attempt_id):
    attempt = own_attempt(request, attempt_id)
    attempt.exam.sync()
    attempt.refresh_from_db()
    ok, why = attempt.resume(request.user)
    if ok:
        messages.success(request, f"{attempt.full_name} can continue. The paused time was given back to them.")
    else:
        messages.error(request, why)
    return redirect(back_to(request, reverse("exam_monitor", args=[attempt.exam_id])))


@teacher_required
@require_POST
def attempt_add_time(request, attempt_id):
    attempt = own_attempt(request, attempt_id)
    attempt.exam.sync()
    attempt.refresh_from_db()
    seconds = minutes_to_seconds(request)
    if seconds is None:
        messages.error(request, "Enter the extra time in minutes (1 to 240).")
    else:
        ok, why = attempt.add_time(request.user, seconds)
        if ok:
            messages.success(request, f"{seconds // 60} extra minutes added for {attempt.full_name}.")
        else:
            messages.error(request, why)
    return redirect(back_to(request, reverse("exam_monitor", args=[attempt.exam_id])))


@teacher_required
@require_POST
def code_regenerate(request, exam_id):
    exam = own_exam(request, exam_id)
    exam.sync()
    if exam.status == "live":
        exam.new_code()
        messages.success(request, "New test code created. The old code no longer works. Students already inside are not affected.")
    else:
        messages.error(request, "The quiz is not running.")
    return redirect("exam_monitor", exam_id=exam.id)


@teacher_required
@require_POST
def code_invalidate(request, exam_id):
    exam = own_exam(request, exam_id)
    exam.sync()
    if exam.status == "live":
        exam.invalidate_code()
        messages.success(request, "Test code disabled. Nobody new can join until you create a new code.")
    else:
        messages.error(request, "The quiz is not running.")
    return redirect("exam_monitor", exam_id=exam.id)


@teacher_required
def attempt_detail(request, attempt_id):
    attempt = own_attempt(request, attempt_id)
    attempt.exam.sync()
    attempt.refresh_from_db()
    exam = attempt.exam
    chosen = dict(attempt.answers.values_list("question_id", "choice"))
    review = []
    for q in exam.questions.all():
        c = chosen.get(q.id)
        review.append({
            "question": q,
            "chosen": q.options[c] if c is not None else "Not answered",
            "correct": q.options[q.correct],
            "is_correct": c == q.correct,
            "answered": c is not None,
            "marks_awarded": q.marks if c == q.correct else 0,
        })
    return render(request, "exams/attempt_detail.html", {
        "attempt": attempt,
        "exam": exam,
        "review": review,
        "events": attempt.events.filter(attempt_number=attempt.attempt_number),
        "warnings": attempt.events.filter(kind__in=["tab_switch", "fullscreen_exit"], attempt_number=attempt.attempt_number),
        "history": list(attempt.history.select_related("restarted_by", "terminated_by")),
        "can_restart": attempt.can_restart(),
        "answered": len(chosen),
        "question_total": len(review),
        "remaining": attempt.remaining_seconds() if attempt.status == "active" else None,
    })


@teacher_required
@require_POST
def attempt_terminate(request, attempt_id):
    attempt = own_attempt(request, attempt_id)
    attempt.exam.sync()
    attempt.refresh_from_db()
    reason = (request.POST.get("reason") or "").strip()[:300]
    if attempt.terminate(request.user, reason):
        messages.success(request, f"{attempt.full_name}'s quiz was terminated. Their saved progress was kept.")
    else:
        messages.error(request, f"{attempt.full_name}'s quiz had already ended.")
    target = request.POST.get("next") or ""
    if not url_has_allowed_host_and_scheme(target, allowed_hosts={request.get_host()}):
        target = reverse("exam_monitor", args=[attempt.exam_id])  # never redirect to another website
    return redirect(target)


@teacher_required
@require_POST
def attempt_restart(request, attempt_id):
    """Restart ONE student's attempt. The old try is archived; nobody else is affected."""
    attempt = own_attempt(request, attempt_id)
    attempt.exam.sync()
    attempt.refresh_from_db()
    ok, why = attempt.restart(request.user, note=(request.POST.get("note") or "").strip())
    if ok:
        messages.success(
            request,
            f"{attempt.full_name}'s quiz was restarted (try {attempt.attempt_number}). "
            "The earlier attempt is saved in their attempt history.",
        )
    else:
        messages.error(request, why)
    target = request.POST.get("next") or ""
    if not url_has_allowed_host_and_scheme(target, allowed_hosts={request.get_host()}):
        target = reverse("exam_monitor", args=[attempt.exam_id])
    return redirect(target)


@teacher_required
def exam_results_csv(request, exam_id):
    exam = own_exam(request, exam_id)
    exam.sync()
    response = HttpResponse(content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = f'attachment; filename="exam_{exam.id}_results.csv"'
    response.write("\ufeff")
    writer = csv.writer(response)
    writer.writerow([
        "Roll number", "Name", "Status", "Score", "Total marks", "Percent",
        "Warnings", "Flagged", "Started", "Ended", "How it ended", "Termination reason",
        "Current try", "Times restarted", "Session",
    ])
    for a in exam.attempts.annotate(n_restarts=Count("history")).order_by("roll_number"):
        percent = round(a.score * 100 / a.total_marks) if a.total_marks else ""
        writer.writerow([
            safe_cell(a.roll_number), safe_cell(a.full_name), a.display_status, a.score, a.total_marks, percent,
            a.warning_count, "Yes" if a.flagged else "No",
            local_time(a.started_at, "%d-%m-%Y %H:%M:%S"), local_time(a.ended_at, "%d-%m-%Y %H:%M:%S"),
            a.end_reason, safe_cell(a.termination_reason),
            a.attempt_number, a.n_restarts, exam.session_label(),
        ])
    return response


# ===========================================================================
# Student: joining with the test code, instructions, taking the quiz
# ===========================================================================

def _too_many_wrong_codes(user):
    return cache.get(f"exam_join_fail_{user.id}", 0) >= 10


def _note_wrong_code(user):
    key = f"exam_join_fail_{user.id}"
    try:
        cache.incr(key)
    except ValueError:
        cache.set(key, 1, 300)  # counts wrong codes for 5 minutes


@login_required
def exam_join(request):
    profile = StudentProfile.objects.filter(user=request.user).first()
    initial = {"full_name": profile.full_name, "roll_number": profile.roll_number} if profile else {}
    form = JoinForm(request.POST or None, initial=initial)

    if request.method == "POST" and form.is_valid():
        if _too_many_wrong_codes(request.user):
            form.add_error("code", "Too many wrong codes. Please wait 5 minutes and try again.")
        else:
            error = None
            code = TestCode.objects.select_related("exam").filter(code=form.cleaned_data["code"]).first()
            if code is None:
                _note_wrong_code(request.user)
                error = "Invalid test code. Please check it with your teacher."
            else:
                exam = code.exam
                exam.sync()
                code.refresh_from_db()
                existing = ExamAttempt.objects.filter(exam=exam, user=request.user).first()
                if existing:
                    return redirect("attempt_open", attempt_id=existing.id)  # already joined: just continue
                if exam.status == "closed":
                    error = "This quiz has been closed."
                elif exam.status == "draft":
                    error = "This quiz has not started yet."
                elif not exam.window_open():
                    error = "The time for this quiz is over, so new students cannot join."
                elif not code.is_usable():
                    if code.deactivated_reason == "expired":
                        error = "This test code has expired. Ask your teacher for the new code."
                    elif code.deactivated_reason == "invalidated":
                        error = "This test code was disabled by the teacher."
                    else:
                        error = "This test code is no longer valid. Ask your teacher for the current code."
                elif ExamAttempt.objects.filter(exam=exam, roll_number=form.cleaned_data["roll_number"]).exists():
                    error = "This roll number has already joined this quiz. Check the number or ask your teacher."

            if error:
                form.add_error("code", error)
            else:
                cache.delete(f"exam_join_fail_{request.user.id}")
                StudentProfile.objects.update_or_create(
                    user=request.user,
                    defaults={"full_name": form.cleaned_data["full_name"],
                              "roll_number": form.cleaned_data["roll_number"]},
                )
                try:
                    attempt = ExamAttempt.objects.create(
                        exam=exam, user=request.user,
                        full_name=form.cleaned_data["full_name"],
                        roll_number=form.cleaned_data["roll_number"],
                    )
                except IntegrityError:
                    form.add_error("code", "Could not join. Please try again.")
                else:
                    attempt.log("joined")
                    return redirect("attempt_instructions", attempt_id=attempt.id)

    ongoing = ExamAttempt.objects.filter(
        user=request.user, exam__status="live", status__in=["joined", "active"],
    ).select_related("exam")
    return render(request, "exams/join.html", {"form": form, "ongoing": ongoing})


def _load_attempt(request, attempt_id):
    """The student's own attempt, with all time-based changes applied first."""
    attempt = get_object_or_404(ExamAttempt.objects.select_related("exam"), id=attempt_id, user=request.user)
    attempt.exam.sync()
    attempt.sync()
    return attempt


@login_required
def attempt_open(request, attempt_id):
    """Sends the student to the right page for their attempt's current state."""
    attempt = _load_attempt(request, attempt_id)
    if attempt.status == "joined":
        return redirect("attempt_instructions", attempt_id=attempt.id)
    if attempt.status == "active":
        return redirect("attempt_take", attempt_id=attempt.id)
    return redirect("attempt_done", attempt_id=attempt.id)


@login_required
def attempt_instructions(request, attempt_id):
    attempt = _load_attempt(request, attempt_id)
    if attempt.status != "joined":
        return redirect("attempt_open", attempt_id=attempt.id)
    exam = attempt.exam
    block = ""
    if not exam.window_open():
        block = "The time for this quiz is over, so it can no longer be started."
    elif exam.is_paused:
        block = "Your teacher has paused the quiz. You can start as soon as it is resumed. This page checks again by itself."
    return render(request, "exams/instructions.html", {
        "attempt": attempt, "exam": exam,
        "question_count": exam.questions.count(), "total_marks": exam.total_marks(),
        "time_for_you": min(exam.duration_minutes * 60, exam.seconds_until_end() or 0) // 60,
        "start_block": block,
    })


@login_required
@require_POST
def attempt_begin(request, attempt_id):
    attempt = _load_attempt(request, attempt_id)
    exam = attempt.exam
    if attempt.status == "joined" and exam.window_open() and not exam.is_paused:
        attempt.start()
    elif attempt.status == "joined":
        messages.error(request, "The quiz cannot be started right now.")
    return redirect("attempt_open", attempt_id=attempt.id)


@login_required
def attempt_take(request, attempt_id):
    attempt = _load_attempt(request, attempt_id)
    if attempt.status != "active":
        return redirect("attempt_open", attempt_id=attempt.id)
    attempt.ensure_seed()
    exam = attempt.exam
    saved = {str(qid): choice for qid, choice in attempt.answers.values_list("question_id", "choice")}

    # Every student gets their own repeatable order. "map" tells the page which ORIGINAL option each
    # shown option is, so saved answers and marking never depend on the order.
    questions = list(exam.questions.all())
    q_order = seeded_order(len(questions), attempt.shuffle_seed, "q") if exam.shuffle_questions else list(range(len(questions)))
    shown = []
    for qi in q_order:
        q = questions[qi]
        o_order = seeded_order(4, attempt.shuffle_seed, f"o{q.id}") if exam.shuffle_options else [0, 1, 2, 3]
        options = q.options
        shown.append({
            "id": q.id, "text": q.text, "marks": q.marks,
            "options": [options[i] for i in o_order], "map": o_order,
        })

    # NOTE: the correct answers are never sent to the browser.
    payload = {
        "questions": shown,
        "answers": saved,
        "remaining": attempt.remaining_seconds(),
        "paused": attempt.is_paused,
        "warnings": attempt.warning_count,
        "maxWarnings": exam.max_warnings,
        "requireFullscreen": exam.require_fullscreen,
        "blockClipboard": exam.block_clipboard,
        "urls": {
            "state": reverse("api_state", args=[attempt.id]),
            "save": reverse("api_save", args=[attempt.id]),
            "event": reverse("api_event", args=[attempt.id]),
            "submit": reverse("api_submit", args=[attempt.id]),
            "done": reverse("attempt_done", args=[attempt.id]),
        },
    }
    return render(request, "exams/take.html", {"attempt": attempt, "exam": exam, "payload": payload})


@login_required
def attempt_done(request, attempt_id):
    attempt = _load_attempt(request, attempt_id)
    if attempt.status in ("joined", "active"):
        return redirect("attempt_open", attempt_id=attempt.id)
    return render(request, "exams/done.html", {
        "attempt": attempt, "exam": attempt.exam, "info": attempt.end_info(),
        "show_score": attempt.exam.show_score and attempt.status == "submitted",
    })


# ===========================================================================
# Student: JSON endpoints (the quiz page talks to these in the background)
# ===========================================================================

def _state(attempt):
    data = {
        "status": attempt.status,
        "remaining": attempt.remaining_seconds() if attempt.status == "active" else None,
        "warnings": attempt.warning_count,
        "maxWarnings": attempt.exam.max_warnings,
        "flagged": attempt.flagged,
        "paused": attempt.status == "active" and attempt.is_paused,
    }
    if attempt.status != "active":
        data["end"] = attempt.end_info()
    return data


@login_required
def api_state(request, attempt_id):
    """Heartbeat: tells the page the official remaining time and whether the attempt is still open."""
    attempt = _load_attempt(request, attempt_id)
    attempt.touch()
    return JsonResponse(_state(attempt))


@login_required
@require_POST
def api_save(request, attempt_id):
    """Autosave one answer. Refused as soon as the attempt is over (checked on the SERVER)."""
    attempt = _load_attempt(request, attempt_id)
    if not attempt.is_open():
        return JsonResponse({"ok": False, **_state(attempt)})
    data = body_json(request)
    try:
        question_id, choice = int(data["question"]), int(data["choice"])
    except (KeyError, TypeError, ValueError):
        return JsonResponse({"ok": False, "error": "Bad request"}, status=400)
    if not 0 <= choice <= 3 or not attempt.exam.questions.filter(id=question_id).exists():
        return JsonResponse({"ok": False, "error": "Bad request"}, status=400)
    with transaction.atomic():
        fresh = ExamAttempt.objects.select_for_update().get(id=attempt.id)
        if fresh.status != "active" or fresh.attempt_number != attempt.attempt_number:
            return JsonResponse({"ok": False, **_state(fresh)})  # restarted or ended while saving
        ExamAnswer.objects.update_or_create(
            attempt=attempt, question_id=question_id, defaults={"choice": choice},
        )
    attempt.touch()
    return JsonResponse({"ok": True, **_state(attempt)})


@login_required
@require_POST
def api_event(request, attempt_id):
    """The page reports 'hidden' when the student leaves the tab and 'returned' when they come back."""
    attempt = _load_attempt(request, attempt_id)
    data = body_json(request)
    kind = data.get("type")

    if kind in ("hidden", "fullscreen_exit"):
        # Nothing is recorded while the quiz or this student is paused (is_open() is false then).
        if not attempt.is_open():
            return JsonResponse({"ok": False, **_state(attempt)})
        if kind == "fullscreen_exit" and not attempt.exam.require_fullscreen:
            return JsonResponse({"ok": False, **_state(attempt)})
        event, level = attempt.record_violation("tab_switch" if kind == "hidden" else "fullscreen_exit")
        return JsonResponse({"ok": True, "event_id": event.id, "level": level, "kind": event.kind, **_state(attempt)})

    if kind == "returned":
        try:
            event_id, away = int(data["event_id"]), int(data["away_seconds"])
        except (KeyError, TypeError, ValueError):
            return JsonResponse({"ok": False, "error": "Bad request"}, status=400)
        ExamEvent.objects.filter(id=event_id, attempt=attempt, kind__in=["tab_switch", "fullscreen_exit"]).update(
            away_seconds=max(0, min(away, 86400)),
        )
        return JsonResponse({"ok": True, **_state(attempt)})

    return JsonResponse({"ok": False, "error": "Bad request"}, status=400)


@login_required
@require_POST
def api_submit(request, attempt_id):
    attempt = _load_attempt(request, attempt_id)
    if attempt.is_open():
        attempt.submit()
    return JsonResponse({"ok": attempt.status == "submitted", **_state(attempt)})


# ===========================================================================
# Teacher: generate questions from notes with Gemini (used inside the quiz editor)
# ===========================================================================

@teacher_required
@require_POST
def exam_generate_questions(request):
    """
    Reads the uploaded notes, asks Gemini for multiple-choice questions and returns them as JSON.
    Nothing is saved here: the questions are added to the editor so the teacher can check and edit them.
    """
    key = f"exam_gen_{request.user.id}"
    if cache.get(key, 0) >= 10:
        return JsonResponse({"ok": False, "error": "You generated questions 10 times in the last 10 minutes. Please wait a little."}, status=429)

    def fail(message, status=400):
        return JsonResponse({"ok": False, "error": message}, status=status)

    files = request.FILES.getlist("files")
    if not files:
        return fail("Choose at least one notes file.")
    if len(files) > MAX_FILES:
        return fail(f"Please upload at most {MAX_FILES} files.")
    files_data, total = [], 0
    for f in files:
        ext = f.name.lower().rsplit(".", 1)[-1] if "." in f.name else ""
        if ext not in MIME_TYPES:
            return fail(f"'{f.name}': only PDF, PNG, JPG, WEBP or TXT files are allowed.")
        if f.size > MAX_FILE_MB * 1024 * 1024:
            return fail(f"'{f.name}' is larger than {MAX_FILE_MB} MB.")
        total += f.size
        files_data.append((f.name, MIME_TYPES[ext], f.read()))
    if total > MAX_TOTAL_MB * 1024 * 1024:
        return fail(f"The files are too large together (maximum {MAX_TOTAL_MB} MB).")
    try:
        count = int(request.POST.get("count", ""))
        marks = int(request.POST.get("marks", "1"))
    except ValueError:
        return fail("Enter the number of questions and the marks as numbers.")
    if not 3 <= count <= 30:
        return fail("Choose between 3 and 30 questions.")
    if not 1 <= marks <= 100:
        return fail("Marks per question must be between 1 and 100.")

    try:
        cache.incr(key)
    except ValueError:
        cache.set(key, 1, 600)
    try:
        items = generate_questions(files_data, count, "mcq")
    except Exception as e:  # network problems, wrong API key, unreadable file, empty answer...
        return fail(f"Gemini could not create the questions: {str(e)[:300]}", status=502)

    return JsonResponse({"ok": True, "questions": [
        {"text": it["question"], "options": it["options"], "correct": it["correct"], "marks": marks}
        for it in items
    ]})


# ===========================================================================
# Student: dashboard with past exams, scores and progress
# ===========================================================================

@login_required
def student_dashboard(request):
    attempts = list(
        ExamAttempt.objects.filter(user=request.user)
        .select_related("exam", "exam__original")
        .annotate(n_answers=Count("answers", distinct=True), n_questions=Count("exam__questions", distinct=True))
        .order_by("-created_at")
    )
    for a in attempts:
        if a.exam.status == "live":  # apply time-based changes so statuses are up to date
            a.exam.sync()
            a.sync()

    rows = []
    for a in attempts:
        # A score is shown only when the teacher allowed it and the quiz was really submitted.
        visible = bool(a.exam.show_score and a.status == "submitted" and a.total_marks)
        rows.append({
            "attempt": a,
            "exam": a.exam,
            "in_progress": a.status in ("joined", "active") and a.exam.status == "live",
            "visible": visible,
            "percent": percent(a.score, a.total_marks) if visible else None,
            "answered": a.n_answers,
            "questions": a.n_questions,
            "answered_percent": percent(a.n_answers, a.n_questions),
        })

    scored = [r for r in rows if r["visible"]]
    stats = {
        "taken": sum(1 for r in rows if not r["in_progress"]),
        "in_progress": sum(1 for r in rows if r["in_progress"]),
        "average": round(sum(r["percent"] for r in scored) / len(scored)) if scored else None,
        "best": max((r["percent"] for r in scored), default=None),
        "hidden": sum(1 for r in rows if r["attempt"].status == "submitted" and not r["visible"]),
    }
    trend = [{"title": r["exam"].title, "percent": r["percent"], "date": r["attempt"].created_at}
             for r in reversed(scored)][-12:]  # oldest to newest, last 12 results

    practice = list(
        PracticeAttempt.objects.filter(user=request.user, is_practice=False)
        .select_related("quiz").order_by("-created_at")[:10]
    )
    return render(request, "exams/student_dashboard.html", {
        "in_progress": [r for r in rows if r["in_progress"]],
        "past": [r for r in rows if not r["in_progress"]],
        "stats": stats,
        "trend": trend,
        "practice": practice,
    })
