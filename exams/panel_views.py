"""The admin panel: one place to manage users, classes, exams, practice quizzes and site settings.

v7: every list has search, filters with counts, sorting, pagination and (where it makes sense) bulk actions
and a CSV export. Users have a detail page. The overview shows real charts built from the database.
"""
import csv
from datetime import datetime, time, timedelta
from functools import wraps

from django.contrib import messages
from django.contrib.auth.models import Group, User
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.core.validators import validate_email
from django.db.models import Count, Q
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.crypto import get_random_string
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from quiz.models import Attempt as PracticeAttempt
from quiz.models import Quiz

from .access import is_admin
from .logic import bar_rows, donut_segments
from .models import (
    AdminLog, Classroom, Enrollment, Exam, ExamAttempt, ExamEvent, SiteSetting,
)

PER_PAGE_CHOICES = (10, 25, 50, 100)
DEFAULT_PER_PAGE = 25


# ===========================================================================
# Small helpers
# ===========================================================================

def admin_required(view):
    """Only admins (superusers or members of the "Admins" group) may open panel pages."""
    @wraps(view)
    def wrapper(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect(f"{reverse('login')}?next={request.path}")
        if not is_admin(request.user):
            messages.error(request, "That page is only for admins.")
            return redirect("quiz_list")
        return view(request, *args, **kwargs)
    return wrapper


def log(request, action, target=""):
    AdminLog.objects.create(actor=request.user, action=action[:200], target=str(target)[:200])


def group_names(user):
    return {g.name for g in user.groups.all()}  # use prefetch_related("groups") for lists


def role_of(user):
    names = group_names(user)
    if user.is_superuser or "Admins" in names:
        return "Admin"
    return "Teacher" if "Teachers" in names else "Student"


def set_role(user, role):
    teachers, _ = Group.objects.get_or_create(name="Teachers")
    admins, _ = Group.objects.get_or_create(name="Admins")
    user.groups.remove(teachers, admins)
    if role == "teacher":
        user.groups.add(teachers)
    elif role == "admin":
        user.groups.add(admins)


def safe_next(request, fallback):
    """Return the 'next' address only if it points to this site (no open redirects)."""
    target = request.POST.get("next") or request.GET.get("next") or ""
    if target and url_has_allowed_host_and_scheme(target, allowed_hosts={request.get_host()}):
        return target
    return reverse(fallback)


def get_page(request, items, default_per_page=DEFAULT_PER_PAGE):
    """Paginate a queryset or list. Returns (page, per_page, querystring-without-page for the pager links)."""
    try:
        per_page = int(request.GET.get("per_page") or default_per_page)
    except ValueError:
        per_page = default_per_page
    if per_page not in PER_PAGE_CHOICES:
        per_page = default_per_page
    page = Paginator(items, per_page).get_page(request.GET.get("page"))
    params = request.GET.copy()
    params.pop("page", None)
    return page, per_page, params.urlencode()


def pick_ids(request, field="ids"):
    """The numbers ticked in a list (checkboxes named 'ids')."""
    return [int(raw) for raw in request.POST.getlist(field) if raw.isdigit()]


def csv_response(filename, header, rows):
    response = HttpResponse(content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    response.write("\ufeff")  # so Excel opens UTF-8 names correctly
    writer = csv.writer(response)
    writer.writerow(header)
    for row in rows:
        # a cell that starts with = + - @ could run as a formula in Excel, so it gets a leading quote
        writer.writerow(["'" + c if isinstance(c, str) and c[:1] in ("=", "+", "-", "@") else c for c in row])
    return response


def can_manage(me, target):
    """Why `me` may NOT change `target` (text), or '' when allowed."""
    protected = target.is_superuser or "Admins" in group_names(target)
    if target.id == me.id:
        return "That is your own account."
    if target.is_superuser:
        return "Superuser accounts are managed from the Django admin site (/admin/)."
    if protected and not me.is_superuser:
        return "Only a superuser can change an admin account."
    return ""


def days_back(n):
    today = timezone.localdate()
    return [today - timedelta(days=i) for i in range(n - 1, -1, -1)]


def per_day(queryset, field, days):
    """Count rows per local day over the given days -> list of numbers (one per day)."""
    start = timezone.make_aware(datetime.combine(days[0], time.min))
    counts = {d: 0 for d in days}
    for stamp in queryset.filter(**{f"{field}__gte": start}).values_list(field, flat=True):
        day = timezone.localtime(stamp).date()
        if day in counts:
            counts[day] += 1
    return [counts[d] for d in days]


def spark(values, labels):
    """Numbers -> columns for the small bar chart (height 4 to 100 %)."""
    top = max(values) if values else 0
    return [{"value": v, "label": l, "height": max(4, round(v * 100 / top)) if top else 4} for v, l in zip(values, labels)]


# ===========================================================================
# Overview
# ===========================================================================

@admin_required
def panel_dashboard(request):
    users = User.objects.all()
    n_users = users.count()
    n_teachers = users.filter(groups__name="Teachers").distinct().count()
    n_admins = users.filter(Q(is_superuser=True) | Q(groups__name="Admins")).distinct().count()
    n_students = max(0, n_users - n_teachers - n_admins)
    exam_status = {r["status"]: r["n"] for r in Exam.objects.values("status").annotate(n=Count("id"))}
    week = timezone.now() - timedelta(days=7)

    stats = [
        {"label": "Users", "value": n_users, "icon": "users", "tone": "",
         "hint": f"{users.filter(date_joined__gte=week).count()} joined this week", "url": reverse("panel_users")},
        {"label": "Live exams", "value": exam_status.get("live", 0), "icon": "activity", "tone": "green",
         "hint": f"{sum(exam_status.values())} exams in total", "url": reverse("panel_exams") + "?status=live"},
        {"label": "Classes", "value": Classroom.objects.count(), "icon": "clipboard", "tone": "pink",
         "hint": f"{Enrollment.objects.count()} enrolments", "url": reverse("panel_classes")},
        {"label": "Flagged attempts", "value": ExamAttempt.objects.filter(flagged=True).count(), "icon": "alert", "tone": "amber",
         "hint": f"{ExamAttempt.objects.count()} exam attempts", "url": reverse("panel_exams")},
        {"label": "Practice quizzes", "value": Quiz.objects.count(), "icon": "book", "tone": "",
         "hint": f"{PracticeAttempt.objects.count()} attempts", "url": reverse("panel_quizzes")},
        {"label": "Blocked accounts", "value": users.filter(is_active=False).count(), "icon": "lock", "tone": "red",
         "hint": "cannot log in", "url": reverse("panel_users") + "?role=inactive"},
    ]

    days = days_back(14)
    labels = [d.strftime("%d %b") for d in days]
    signups = spark(per_day(User.objects.all(), "date_joined", days), labels)
    attempts = spark(per_day(ExamAttempt.objects.filter(started_at__isnull=False), "started_at", days), labels)

    role_segments, role_css, role_total = donut_segments(
        [("Students", n_students, "#8B5CF6"), ("Teachers", n_teachers, "#D946EF"), ("Admins", n_admins, "#EC4899")])
    exam_segments, exam_css, exam_total = donut_segments(
        [("Live", exam_status.get("live", 0), "#22C55E"), ("Draft", exam_status.get("draft", 0), "#F59E0B"),
         ("Closed", exam_status.get("closed", 0), "#6B7280")])

    top_teachers = User.objects.filter(exams__isnull=False).annotate(n=Count("exams", distinct=True)).order_by("-n")[:5]
    top_rows = bar_rows([(t.username, t.n) for t in top_teachers])

    live_exams = list(
        Exam.objects.filter(status="live").select_related("teacher", "classroom")
        .annotate(n_students=Count("attempts", distinct=True)).order_by("-started_at")[:6]
    )
    for e in live_exams:
        e.sync()

    return render(request, "panel/dashboard.html", {
        "stats": stats, "signups": signups, "attempts": attempts,
        "role_segments": role_segments, "role_css": role_css, "role_total": role_total,
        "exam_segments": exam_segments, "exam_css": exam_css, "exam_total": exam_total,
        "top_rows": top_rows, "live_exams": live_exams,
        "recent_users": User.objects.order_by("-date_joined")[:6],
        "recent_events": ExamEvent.objects.select_related("attempt", "exam").order_by("-id")[:8],
        "recent_log": AdminLog.objects.select_related("actor")[:6],
        "signups_total": sum(c["value"] for c in signups), "attempts_total": sum(c["value"] for c in attempts),
    })


# ===========================================================================
# Users
# ===========================================================================

USER_SORTS = {
    "newest": ("-date_joined", "Newest first"),
    "oldest": ("date_joined", "Oldest first"),
    "name": ("username", "Username A-Z"),
    "login": ("-last_login", "Last login"),
}


def filtered_users(request):
    q = (request.GET.get("q") or "").strip()
    role = request.GET.get("role") or ""
    sort = request.GET.get("sort") if request.GET.get("sort") in USER_SORTS else "newest"
    users = User.objects.all().prefetch_related("groups")
    if q:
        users = users.filter(Q(username__icontains=q) | Q(first_name__icontains=q) | Q(last_name__icontains=q) | Q(email__icontains=q))
    if role == "teacher":
        users = users.filter(groups__name="Teachers")
    elif role == "admin":
        users = users.filter(Q(is_superuser=True) | Q(groups__name="Admins"))
    elif role == "student":
        users = users.exclude(groups__name__in=["Teachers", "Admins"]).exclude(is_superuser=True)
    elif role == "inactive":
        users = users.filter(is_active=False)
    return users.distinct().order_by(USER_SORTS[sort][0], "-id"), q, role, sort


@admin_required
def panel_users(request):
    users, q, role, sort = filtered_users(request)
    page, per_page, qs = get_page(request, users)
    rows = [{"u": u, "role": role_of(u), "reason": can_manage(request.user, u)} for u in page.object_list]

    base = User.objects.all()
    total = base.count()
    n_t = base.filter(groups__name="Teachers").distinct().count()
    n_a = base.filter(Q(is_superuser=True) | Q(groups__name="Admins")).distinct().count()
    counts = {"": total, "student": max(0, total - n_t - n_a), "teacher": n_t, "admin": n_a,
              "inactive": base.filter(is_active=False).count()}
    tabs = [("", "All"), ("student", "Students"), ("teacher", "Teachers"), ("admin", "Admins"), ("inactive", "Blocked")]
    return render(request, "panel/users.html", {
        "rows": rows, "page": page, "per_page": per_page, "per_page_choices": PER_PAGE_CHOICES, "qs": qs,
        "q": q, "role": role, "sort": sort, "sorts": [(k, v[1]) for k, v in USER_SORTS.items()],
        "tabs": [(key, label, counts[key]) for key, label in tabs],
        "me": request.user, "i_am_superuser": request.user.is_superuser,
    })


@admin_required
def panel_users_export(request):
    users, *_ = filtered_users(request)
    rows = [[u.username, u.get_full_name(), u.email, role_of(u), "yes" if u.is_active else "no",
             timezone.localtime(u.date_joined).strftime("%Y-%m-%d %H:%M"),
             timezone.localtime(u.last_login).strftime("%Y-%m-%d %H:%M") if u.last_login else ""] for u in users]
    log(request, "Exported users (CSV)", f"{len(rows)} rows")
    return csv_response("users.csv", ["Username", "Name", "Email", "Role", "Active", "Joined", "Last login"], rows)


@admin_required
@require_POST
def panel_user_create(request):
    username = (request.POST.get("username") or "").strip()
    password = request.POST.get("password") or ""
    role = request.POST.get("role") or "student"
    if role not in ("student", "teacher", "admin"):
        role = "student"
    if role == "admin" and not request.user.is_superuser:
        messages.error(request, "Only a superuser can create another admin.")
    elif not username or User.objects.filter(username__iexact=username).exists():
        messages.error(request, "Enter a username that is not already used.")
    else:
        try:
            validate_password(password)
        except ValidationError as e:
            messages.error(request, " ".join(e.messages))
        else:
            user = User.objects.create_user(username=username, password=password)
            set_role(user, role)
            log(request, f"Created {role} account", username)
            messages.success(request, f"Account '{username}' created as {role}.")
    return redirect("panel_users")


def apply_user_action(request, target, action):
    """Do one action on one account. Returns (ok, message). Used by the row menu, the detail page and bulk actions."""
    me = request.user
    if not (action == "reset_password" and target.id == me.id):  # an admin may reset their own password
        why = can_manage(me, target)
        if why:
            return False, f"{target.username}: {why}"
    if action == "make_admin" and not me.is_superuser:
        return False, "Only a superuser can make someone an admin."
    if action in ("make_student", "make_teacher", "make_admin"):
        role = action.replace("make_", "")
        set_role(target, role)
        log(request, f"Changed role to {role}", target.username)
        return True, f"{target.username} is now a {role}."
    if action in ("activate", "deactivate"):
        target.is_active = action == "activate"
        target.save(update_fields=["is_active"])
        log(request, "Activated account" if target.is_active else "Deactivated account", target.username)
        return True, f"{target.username} is now {'active' if target.is_active else 'blocked from logging in'}."
    if action == "reset_password":
        temp = get_random_string(10)
        target.set_password(temp)
        target.save(update_fields=["password"])
        log(request, "Reset password", target.username)
        return True, f"Temporary password for {target.username}: {temp}  (tell them to change it)"
    if action == "delete":
        name = target.username
        target.delete()
        log(request, "Deleted account", name)
        return True, f"Account '{name}' and its data were deleted."
    return False, "Choose an action."


@admin_required
@require_POST
def panel_user_action(request, user_id):
    target = get_object_or_404(User, id=user_id)
    action = request.POST.get("action") or ""
    ok, text = apply_user_action(request, target, action)
    (messages.success if ok else messages.error)(request, text)
    if ok and action == "delete":
        return redirect("panel_users")
    return redirect(safe_next(request, "panel_users"))


@admin_required
@require_POST
def panel_users_bulk(request):
    action = request.POST.get("action") or ""
    ids = pick_ids(request)
    if action not in {"activate", "deactivate", "make_student", "make_teacher", "delete"}:
        messages.error(request, "Choose what to do with the selected accounts.")
    elif not ids:
        messages.error(request, "Tick at least one account first.")
    else:
        done, skipped = 0, []
        for target in User.objects.filter(id__in=ids):
            ok, text = apply_user_action(request, target, action)
            if ok:
                done += 1
            else:
                skipped.append(text)
        if done:
            messages.success(request, f"{action.replace('_', ' ').capitalize()}: {done} account(s) done.")
        if skipped:
            messages.error(request, f"{len(skipped)} skipped. " + skipped[0])
    return redirect(safe_next(request, "panel_users"))


@admin_required
def panel_user_detail(request, user_id):
    target = get_object_or_404(User.objects.prefetch_related("groups"), id=user_id)
    if request.method == "POST":
        why = can_manage(request.user, target) if target.id != request.user.id else ""
        if why:
            messages.error(request, why)
            return redirect("panel_user_detail", user_id=target.id)
        email = (request.POST.get("email") or "").strip()
        try:
            if email:
                validate_email(email)
        except ValidationError:
            messages.error(request, "That email address does not look right.")
            return redirect("panel_user_detail", user_id=target.id)
        target.first_name = (request.POST.get("first_name") or "").strip()[:150]
        target.last_name = (request.POST.get("last_name") or "").strip()[:150]
        target.email = email
        target.save(update_fields=["first_name", "last_name", "email"])
        log(request, "Edited profile", target.username)
        messages.success(request, "Profile saved.")
        return redirect("panel_user_detail", user_id=target.id)

    exams = Exam.objects.filter(teacher=target).annotate(n_students=Count("attempts", distinct=True)).order_by("-created_at")[:8]
    attempts = ExamAttempt.objects.filter(user=target).select_related("exam").order_by("-created_at")[:8]
    classes_teach = Classroom.objects.filter(teacher=target).annotate(n=Count("enrollments")).order_by("-created_at")[:8]
    classes_in = Enrollment.objects.filter(student=target).select_related("classroom")[:8]
    quizzes = Quiz.objects.filter(created_by=target).order_by("-created_at")[:6]
    facts = [
        ("Exams created", Exam.objects.filter(teacher=target).count()),
        ("Exam attempts", ExamAttempt.objects.filter(user=target).count()),
        ("Flagged", ExamAttempt.objects.filter(user=target, flagged=True).count()),
        ("Practice quizzes", Quiz.objects.filter(created_by=target).count()),
        ("Practice attempts", PracticeAttempt.objects.filter(user=target).count()),
    ]
    return render(request, "panel/user_detail.html", {
        "t": target, "role": role_of(target), "reason": can_manage(request.user, target), "me": request.user,
        "i_am_superuser": request.user.is_superuser, "facts": facts, "exams": exams, "attempts": attempts,
        "classes_teach": classes_teach, "classes_in": classes_in, "quizzes": quizzes,
        "log_rows": AdminLog.objects.filter(target=target.username).select_related("actor")[:8],
    })


# ===========================================================================
# Classes
# ===========================================================================

@admin_required
def panel_classes(request):
    q = (request.GET.get("q") or "").strip()
    ai = request.GET.get("ai") or ""
    classes = Classroom.objects.select_related("teacher").annotate(
        n_students=Count("enrollments", distinct=True), n_exams=Count("exams", distinct=True),
    )
    if q:
        classes = classes.filter(Q(name__icontains=q) | Q(teacher__username__icontains=q) | Q(join_code__icontains=q))
    if ai == "on":
        classes = classes.filter(allow_ai_quiz=True)
    elif ai == "off":
        classes = classes.filter(allow_ai_quiz=False)
    page, per_page, qs = get_page(request, classes.order_by("-created_at"))
    return render(request, "panel/classes.html", {
        "page": page, "per_page": per_page, "per_page_choices": PER_PAGE_CHOICES, "qs": qs, "q": q, "ai": ai,
        "total": Classroom.objects.count(), "students_total": Enrollment.objects.count(),
    })


@admin_required
@require_POST
def panel_class_toggle_ai(request, class_id):
    classroom = get_object_or_404(Classroom, id=class_id)
    classroom.allow_ai_quiz = not classroom.allow_ai_quiz
    classroom.save(update_fields=["allow_ai_quiz"])
    log(request, "Allowed AI quizzes for class" if classroom.allow_ai_quiz else "Blocked AI quizzes for class", classroom.name)
    messages.success(request, f"AI quiz creation is now {'allowed' if classroom.allow_ai_quiz else 'blocked'} for {classroom.name}.")
    return redirect(safe_next(request, "panel_classes"))


@admin_required
@require_POST
def panel_class_delete(request, class_id):
    classroom = get_object_or_404(Classroom, id=class_id)
    name = classroom.name
    classroom.delete()  # quizzes of this class are kept, but nobody can join them until a new class is chosen
    log(request, "Deleted class", name)
    messages.success(request, f"Class '{name}' deleted.")
    return redirect(safe_next(request, "panel_classes"))


@admin_required
@require_POST
def panel_classes_bulk(request):
    action = request.POST.get("action") or ""
    ids = pick_ids(request)
    classes = Classroom.objects.filter(id__in=ids)
    if not ids:
        messages.error(request, "Tick at least one class first.")
    elif action in ("allow_ai", "block_ai"):
        n = classes.update(allow_ai_quiz=action == "allow_ai")
        log(request, "Allowed AI quizzes for classes" if action == "allow_ai" else "Blocked AI quizzes for classes", f"{n} classes")
        messages.success(request, f"AI quiz creation updated for {n} class(es).")
    elif action == "delete":
        n = classes.count()
        classes.delete()
        log(request, "Deleted classes", f"{n} classes")
        messages.success(request, f"{n} class(es) deleted.")
    else:
        messages.error(request, "Choose what to do with the selected classes.")
    return redirect(safe_next(request, "panel_classes"))


# ===========================================================================
# Exams (live exams)
# ===========================================================================

def filtered_exams(request):
    q = (request.GET.get("q") or "").strip()
    status = request.GET.get("status") or ""
    exams = Exam.objects.select_related("teacher", "classroom").annotate(
        n_students=Count("attempts", distinct=True), n_questions=Count("questions", distinct=True),
    )
    if q:
        exams = exams.filter(Q(title__icontains=q) | Q(teacher__username__icontains=q) | Q(classroom__name__icontains=q))
    if status in ("draft", "live", "closed"):
        exams = exams.filter(status=status)
    return exams.order_by("-created_at"), q, status


@admin_required
def panel_exams(request):
    exams, q, status = filtered_exams(request)
    page, per_page, qs = get_page(request, exams)
    for e in page.object_list:
        e.sync()
    counts = {r["status"]: r["n"] for r in Exam.objects.values("status").annotate(n=Count("id"))}
    tabs = [("", "All", sum(counts.values())), ("live", "Live", counts.get("live", 0)),
            ("draft", "Draft", counts.get("draft", 0)), ("closed", "Closed", counts.get("closed", 0))]
    return render(request, "panel/exams.html", {
        "page": page, "per_page": per_page, "per_page_choices": PER_PAGE_CHOICES, "qs": qs,
        "q": q, "status": status, "tabs": tabs,
    })


@admin_required
def panel_exams_export(request):
    exams, *_ = filtered_exams(request)
    rows = [[e.title, e.session_label() if e.is_reconducted else "", e.teacher.username, e.classroom.name if e.classroom else "",
             e.status, e.n_questions, e.n_students, e.duration_minutes,
             timezone.localtime(e.created_at).strftime("%Y-%m-%d %H:%M")] for e in exams]
    log(request, "Exported exams (CSV)", f"{len(rows)} rows")
    return csv_response("exams.csv", ["Title", "Session", "Teacher", "Class", "Status", "Questions", "Students", "Minutes", "Created"], rows)


@admin_required
@require_POST
def panel_exam_delete(request, exam_id):
    exam = get_object_or_404(Exam, id=exam_id)
    title = exam.title
    exam.delete()  # also deletes its questions, attempts, answers and history
    log(request, "Deleted exam (with all results)", title)
    messages.success(request, f"Exam '{title}' and all its results were deleted.")
    return redirect(safe_next(request, "panel_exams"))


@admin_required
@require_POST
def panel_exams_bulk(request):
    action = request.POST.get("action") or ""
    ids = pick_ids(request)
    exams = list(Exam.objects.filter(id__in=ids))
    if not ids:
        messages.error(request, "Tick at least one exam first.")
    elif action == "close":
        closed = 0
        for e in exams:
            e.sync()
            if e.status == "live" and e.close("teacher"):
                closed += 1
        log(request, "Closed exams", f"{closed} exams")
        messages.success(request, f"{closed} live exam(s) closed. The others were not running.")
    elif action == "delete":
        for e in exams:
            e.delete()
        log(request, "Deleted exams (with all results)", f"{len(exams)} exams")
        messages.success(request, f"{len(exams)} exam(s) and all their results were deleted.")
    else:
        messages.error(request, "Choose what to do with the selected exams.")
    return redirect(safe_next(request, "panel_exams"))


# ===========================================================================
# Practice (AI) quizzes
# ===========================================================================

@admin_required
def panel_quizzes(request):
    q = (request.GET.get("q") or "").strip()
    vis = request.GET.get("vis") or ""
    quizzes = Quiz.objects.select_related("created_by").annotate(
        n_questions=Count("questions", distinct=True), n_attempts=Count("attempts", distinct=True),
    )
    if q:
        quizzes = quizzes.filter(Q(title__icontains=q) | Q(created_by__username__icontains=q))
    if vis == "private":
        quizzes = quizzes.filter(is_private=True)
    elif vis == "public":
        quizzes = quizzes.filter(is_private=False)
    page, per_page, qs = get_page(request, quizzes.order_by("-created_at"))
    total = Quiz.objects.count()
    private = Quiz.objects.filter(is_private=True).count()
    tabs = [("", "All", total), ("public", "Public", total - private), ("private", "Private", private)]
    return render(request, "panel/quizzes.html", {
        "page": page, "per_page": per_page, "per_page_choices": PER_PAGE_CHOICES, "qs": qs, "q": q, "vis": vis, "tabs": tabs,
    })


@admin_required
@require_POST
def panel_quiz_action(request, quiz_id):
    quiz = get_object_or_404(Quiz, id=quiz_id)
    action = request.POST.get("action")
    if action == "delete":
        title = quiz.title
        quiz.delete()
        log(request, "Deleted practice quiz", title)
        messages.success(request, f"Quiz '{title}' deleted.")
    elif action == "toggle_leaderboard":
        quiz.show_leaderboard = not quiz.show_leaderboard
        quiz.save(update_fields=["show_leaderboard"])
        log(request, "Showed leaderboard" if quiz.show_leaderboard else "Hid leaderboard", quiz.title)
        messages.success(request, "Leaderboard is now " + ("visible to students." if quiz.show_leaderboard else "hidden from students."))
    elif action == "toggle_private":
        quiz.is_private = not quiz.is_private
        quiz.save(update_fields=["is_private"])
        log(request, "Made quiz private" if quiz.is_private else "Made quiz public", quiz.title)
        messages.success(request, "Quiz is now " + ("private." if quiz.is_private else "public."))
    return redirect(safe_next(request, "panel_quizzes"))


@admin_required
@require_POST
def panel_quizzes_bulk(request):
    action = request.POST.get("action") or ""
    ids = pick_ids(request)
    quizzes = Quiz.objects.filter(id__in=ids)
    if not ids:
        messages.error(request, "Tick at least one quiz first.")
    elif action in ("make_public", "make_private", "show_board", "hide_board"):
        field, value = {"make_public": ("is_private", False), "make_private": ("is_private", True),
                        "show_board": ("show_leaderboard", True), "hide_board": ("show_leaderboard", False)}[action]
        n = quizzes.update(**{field: value})
        log(request, f"Bulk: {action.replace('_', ' ')}", f"{n} quizzes")
        messages.success(request, f"{n} quiz(zes) updated.")
    elif action == "delete":
        n = quizzes.count()
        quizzes.delete()
        log(request, "Deleted practice quizzes", f"{n} quizzes")
        messages.success(request, f"{n} quiz(zes) deleted.")
    else:
        messages.error(request, "Choose what to do with the selected quizzes.")
    return redirect(safe_next(request, "panel_quizzes"))


# ===========================================================================
# Activity log and site settings
# ===========================================================================

def filtered_log(request):
    q = (request.GET.get("q") or "").strip()
    entries = AdminLog.objects.select_related("actor")
    if q:
        entries = entries.filter(Q(action__icontains=q) | Q(target__icontains=q) | Q(actor__username__icontains=q))
    return entries, q


@admin_required
def panel_log(request):
    entries, q = filtered_log(request)
    page, per_page, qs = get_page(request, entries, default_per_page=50)
    return render(request, "panel/log.html", {
        "page": page, "per_page": per_page, "per_page_choices": PER_PAGE_CHOICES, "qs": qs, "q": q,
    })


@admin_required
def panel_log_export(request):
    entries, _ = filtered_log(request)
    rows = [[timezone.localtime(e.created_at).strftime("%Y-%m-%d %H:%M:%S"), e.actor.username if e.actor else "(deleted)", e.action, e.target]
            for e in entries]
    return csv_response("admin-log.csv", ["When", "Admin", "Action", "On"], rows)


@admin_required
def panel_settings(request):
    site = SiteSetting.get()
    if request.method == "POST":
        site.allow_teacher_registration = request.POST.get("allow_teacher_registration") == "on"
        site.allow_student_registration = request.POST.get("allow_student_registration") == "on"
        site.announcement_active = request.POST.get("announcement_active") == "on"
        site.announcement_text = (request.POST.get("announcement_text") or "").strip()[:300]
        level = request.POST.get("announcement_level") or "info"
        site.announcement_level = level if level in ("info", "warn", "ok") else "info"
        if site.announcement_active and not site.announcement_text:
            site.announcement_active = False
            messages.error(request, "Write the announcement text first. The banner was left off.")
        site.save()
        log(request, "Changed site settings",
            f"teacher sign-up {'on' if site.allow_teacher_registration else 'off'}, "
            f"student sign-up {'on' if site.allow_student_registration else 'off'}, "
            f"banner {'on' if site.announcement_active else 'off'}")
        messages.success(request, "Settings saved.")
        return redirect("panel_settings")
    return render(request, "panel/settings.html", {"site": site})
