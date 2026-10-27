import csv
import threading
import time
from datetime import timedelta

from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import UserCreationForm
from django.db import connection
from django.db.models import Count, Q
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone

from . import exports
from .forms import UploadNotesForm, mime_for
from .gemini_service import generate_questions
from .models import Attempt, Question, Quiz, QuizJob


# ---------------------------------------------------------------------------
# Small helpers used by several views
# ---------------------------------------------------------------------------

def load_quiz(request, quiz_id):
    """Returns (quiz, None) if the student may open it, otherwise (None, redirect)."""
    quiz = get_object_or_404(Quiz, id=quiz_id)
    if quiz.can_access(request.user):
        return quiz, None
    messages.error(request, "This quiz is private. Enter its join code to play.")
    return None, redirect("quiz_list")


def official_attempts(quiz):
    """Only these count for the leaderboard and analytics (practice attempts do not)."""
    return quiz.attempts.filter(is_practice=False)


def rank_of(quiz, attempt):
    """Rank = how many students did better than you, plus one."""
    better = official_attempts(quiz).filter(
        Q(score__gt=attempt.score) | Q(score=attempt.score, time_taken__lt=attempt.time_taken)
    ).count()
    return better + 1


def pick_attempt(request, quiz):
    """The official attempt by default, or a practice attempt if ?attempt=<id> is given."""
    attempt_id = request.GET.get("attempt", "")
    if attempt_id.isdigit():
        return get_object_or_404(Attempt, id=int(attempt_id), user=request.user, quiz=quiz)
    return get_object_or_404(Attempt, user=request.user, quiz=quiz, is_practice=False)


def build_review(quiz, attempt):
    review = []
    for q in quiz.questions.all():
        chosen = attempt.answers.get(str(q.id))
        review.append({
            "question": q.text,
            "your_answer": q.answer_label(chosen),
            "correct_answer": q.correct_label(),
            "is_correct": q.is_correct(chosen),
        })
    return review


# ---------------------------------------------------------------------------
# Accounts, quiz list, joining with a code
# ---------------------------------------------------------------------------

def register(request):
    """Sign-up page for new students."""
    if request.method == "POST":
        form = UserCreationForm(request.POST)
        if form.is_valid():
            user = form.save()
            login(request, user)
            return redirect("quiz_list")
    else:
        form = UserCreationForm()
    return render(request, "registration/register.html", {"form": form})


@login_required
def quiz_list(request):
    """Public quizzes + your own quizzes + private quizzes you joined with a code."""
    joined_ids = list(request.user.joined_quizzes.values_list("id", flat=True))
    quizzes = list(
        Quiz.objects.filter(Q(is_private=False) | Q(created_by=request.user) | Q(id__in=joined_ids))
        .select_related("created_by")
        .annotate(
            num_questions=Count("questions", distinct=True),
            num_players=Count("attempts", filter=Q(attempts__is_practice=False), distinct=True),
        )
        .order_by("-created_at")
    )
    my_attempts = {a.quiz_id: a for a in Attempt.objects.filter(user=request.user, is_practice=False)}
    for quiz in quizzes:
        quiz.my_attempt = my_attempts.get(quiz.id)  # None if not attempted yet
        if quiz.created_by_id == request.user.id and not quiz.join_code:
            quiz.save()  # older quizzes get a join code the first time their owner sees them
    return render(request, "quiz/quiz_list.html", {"quizzes": quizzes})


@login_required
def join_quiz(request, code=None):
    """Student types a join code (or opens a shared link) to get into a private quiz."""
    if request.method == "POST":
        code = request.POST.get("code", "")
    code = (code or "").strip().upper()
    if not code:
        return redirect("quiz_list")

    quiz = Quiz.objects.filter(join_code=code).first()
    if quiz is None:
        messages.error(request, "No quiz found with that code. Please check it and try again.")
    else:
        quiz.members.add(request.user)
        messages.success(request, f'You joined "{quiz.title}". You can start it now.')
    return redirect("quiz_list")


# ---------------------------------------------------------------------------
# Creating a quiz: upload -> background job -> progress page
# ---------------------------------------------------------------------------

def run_job(job_id, user_id, title, files_data, num_questions, question_type, is_private):
    """Runs in a background thread so the progress page can show live progress."""
    try:
        QuizJob.objects.filter(id=job_id).update(progress=35, message="Gemini is reading your notes...")
        items = generate_questions(files_data, num_questions, question_type)

        QuizJob.objects.filter(id=job_id).update(progress=90, message="Saving your quiz...")
        quiz = Quiz.objects.create(title=title, created_by_id=user_id, is_private=is_private)
        questions = []
        for it in items:
            opts = it["options"] + [""] * (4 - len(it["options"]))
            questions.append(Question(
                quiz=quiz,
                qtype=it["type"],
                text=it["question"],
                option_a=opts[0], option_b=opts[1], option_c=opts[2], option_d=opts[3],
                correct=it["correct"],
                answer_text=it["answer_text"],
            ))
        Question.objects.bulk_create(questions)

        QuizJob.objects.filter(id=job_id).update(
            status="done", progress=100, message="Your quiz is ready!", quiz=quiz
        )
    except Exception as e:
        QuizJob.objects.filter(id=job_id).update(
            status="failed", message="Something went wrong.", error=str(e)[:500]
        )
    finally:
        connection.close()  # each thread must close its own database connection


@login_required
def upload_notes(request):
    if request.method == "POST":
        form = UploadNotesForm(request.POST, request.FILES)
        if form.is_valid():
            # Read the files now: uploaded files are closed when this request ends.
            files_data = [(f.name, mime_for(f.name), f.read()) for f in form.cleaned_data["files"]]

            ok = True
            if all(mime == "text/plain" for _, mime, _ in files_data):
                all_text = "".join(raw.decode("utf-8", errors="ignore") for _, _, raw in files_data)
                if len(all_text.strip()) < 100:
                    form.add_error("files", "Not enough text in these files to make a quiz.")
                    ok = False

            if ok:
                title = form.cleaned_data["title"]
                job = QuizJob.objects.create(
                    user=request.user, title=title, progress=20, message="Notes received. Contacting Gemini..."
                )
                threading.Thread(
                    target=run_job,
                    args=(
                        job.id, request.user.id, title, files_data,
                        form.cleaned_data["num_questions"],
                        form.cleaned_data["question_type"],
                        form.cleaned_data["is_private"],
                    ),
                    daemon=True,
                ).start()
                return redirect("job_page", job_id=job.id)
    else:
        form = UploadNotesForm()
    return render(request, "quiz/upload.html", {"form": form})


@login_required
def job_page(request, job_id):
    job = get_object_or_404(QuizJob, id=job_id, user=request.user)
    return render(request, "quiz/job.html", {"job": job})


@login_required
def job_status(request, job_id):
    """The progress page asks this URL every second and gets JSON back."""
    job = get_object_or_404(QuizJob, id=job_id, user=request.user)

    # Safety: if a job is stuck for 5+ minutes, mark it failed.
    if job.status == "working" and timezone.now() - job.created_at > timedelta(minutes=5):
        job.status = "failed"
        job.error = "It took too long. Please try again."
        job.save()

    quiz = job.quiz
    return JsonResponse({
        "status": job.status,
        "progress": job.progress,
        "message": job.message,
        "error": job.error,
        "quiz_url": reverse("take_quiz", args=[quiz.id]) if quiz else "",
        "join_code": quiz.join_code if quiz and quiz.is_private else "",
    })


# ---------------------------------------------------------------------------
# Taking a quiz (official attempt or practice)
# ---------------------------------------------------------------------------

@login_required
def take_quiz(request, quiz_id):
    quiz, denied = load_quiz(request, quiz_id)
    if denied:
        return denied

    practice = request.GET.get("practice") == "1" or request.POST.get("practice") == "1"
    official_done = Attempt.objects.filter(user=request.user, quiz=quiz, is_practice=False).exists()

    if practice:
        # Practice shows the answers, so it opens only after your real attempt (or for the quiz creator).
        if not (official_done or quiz.created_by_id == request.user.id):
            messages.error(request, "Finish the quiz once before you practise.")
            return redirect("quiz_list")
    elif official_done:
        return redirect("result", quiz_id=quiz.id)  # one official attempt per student

    questions = list(quiz.questions.all())
    session_key = f"start_{quiz.id}"

    if request.method == "POST":
        start = request.session.get(session_key, time.time())
        time_taken = max(0, int(time.time() - start))

        score = 0
        answers = {}
        for q in questions:
            raw = request.POST.get(f"q_{q.id}", "").strip()
            if q.qtype == "fill":
                if raw:
                    answers[str(q.id)] = raw[:200]
            elif raw.isdigit() and int(raw) < len(q.options):
                answers[str(q.id)] = int(raw)
            if q.is_correct(answers.get(str(q.id))):
                score += 1

        request.session.pop(session_key, None)
        data = {"score": score, "total": len(questions), "time_taken": time_taken, "answers": answers}

        if practice:
            attempt = Attempt.objects.create(user=request.user, quiz=quiz, is_practice=True, **data)
            return redirect(f"{reverse('result', args=[quiz.id])}?attempt={attempt.id}")

        Attempt.objects.get_or_create(user=request.user, quiz=quiz, is_practice=False, defaults=data)
        return redirect("result", quiz_id=quiz.id)

    request.session[session_key] = time.time()  # start the timer

    # NOTE: we send only the text and options to the browser, never the correct answers.
    questions_data = [
        {"id": q.id, "type": q.qtype, "text": q.text, "options": q.options} for q in questions
    ]
    return render(request, "quiz/take_quiz.html", {
        "quiz": quiz, "questions_data": questions_data, "practice": practice,
    })


@login_required
def result(request, quiz_id):
    quiz, denied = load_quiz(request, quiz_id)
    if denied:
        return denied
    attempt = pick_attempt(request, quiz)

    return render(request, "quiz/result.html", {
        "quiz": quiz,
        "attempt": attempt,
        "review": build_review(quiz, attempt),
        "rank": None if attempt.is_practice else rank_of(quiz, attempt),
        "players": official_attempts(quiz).count(),
    })


# ---------------------------------------------------------------------------
# Leaderboard, analytics, exports
# ---------------------------------------------------------------------------

@login_required
def leaderboard(request, quiz_id):
    quiz, denied = load_quiz(request, quiz_id)
    if denied:
        return denied
    # Highest score first; if equal, the faster student ranks higher.
    attempts = official_attempts(quiz).select_related("user").order_by("-score", "time_taken")
    return render(request, "quiz/leaderboard.html", {
        "quiz": quiz,
        "attempts": attempts,
        "is_creator": quiz.created_by_id == request.user.id,
    })


@login_required
def analytics(request, quiz_id):
    """Teacher page: average score, hardest question, all participants."""
    quiz = get_object_or_404(Quiz, id=quiz_id)
    if quiz.created_by_id != request.user.id and not request.user.is_superuser:
        messages.error(request, "Only the person who created this quiz can see its analytics.")
        return redirect("leaderboard", quiz_id=quiz.id)

    if not quiz.join_code:
        quiz.save()

    attempts = list(official_attempts(quiz).select_related("user").order_by("-score", "time_taken"))
    questions = list(quiz.questions.all())
    n = len(attempts)

    stats = None
    performance = []
    hardest = None
    if n:
        avg_time = round(sum(a.time_taken for a in attempts) / n)
        stats = {
            "players": n,
            "avg_percent": round(sum(a.percent for a in attempts) / n),
            "best": max(a.percent for a in attempts),
            "avg_time": f"{avg_time // 60}:{avg_time % 60:02d}",
        }
        for q in questions:
            correct = sum(1 for a in attempts if q.is_correct(a.answers.get(str(q.id))))
            performance.append({
                "question": q,
                "correct": correct,
                "percent": round(correct * 100 / n),
            })
        performance.sort(key=lambda p: p["percent"])  # hardest first
        hardest = performance[0] if performance else None

    return render(request, "quiz/analytics.html", {
        "quiz": quiz,
        "attempts": attempts,
        "stats": stats,
        "performance": performance,
        "hardest": hardest,
        "n": n,
        "join_url": request.build_absolute_uri(reverse("join_quiz_code", args=[quiz.join_code])),
    })


@login_required
def export_leaderboard(request, quiz_id):
    quiz, denied = load_quiz(request, quiz_id)
    if denied:
        return denied
    attempts = official_attempts(quiz).select_related("user").order_by("-score", "time_taken")

    response = HttpResponse(content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = f'attachment; filename="leaderboard_quiz_{quiz.id}.csv"'
    response.write("\ufeff")  # makes Excel read non-English names correctly
    writer = csv.writer(response)
    writer.writerow(["Rank", "Student", "Score", "Total", "Percent", "Time (m:ss)"])
    for i, a in enumerate(attempts, 1):
        writer.writerow([i, exports.safe_cell(a.user.username), a.score, a.total, a.percent, a.time_display])
    return response


@login_required
def export_result_pdf(request, quiz_id):
    quiz, denied = load_quiz(request, quiz_id)
    if denied:
        return denied
    attempt = pick_attempt(request, quiz)

    pdf = exports.build_result_pdf(
        quiz_title=quiz.title,
        username=request.user.username,
        score=attempt.score,
        total=attempt.total,
        percent=attempt.percent,
        time_display=attempt.time_display,
        rank=None if attempt.is_practice else rank_of(quiz, attempt),
        players=official_attempts(quiz).count(),
        is_practice=attempt.is_practice,
        review=build_review(quiz, attempt),
    )
    response = HttpResponse(pdf, content_type="application/pdf")
    response["Content-Disposition"] = f'attachment; filename="result_quiz_{quiz.id}.pdf"'
    return response
