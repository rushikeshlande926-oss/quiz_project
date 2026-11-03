from datetime import timedelta

from django.utils import timezone

from .logic import ALERT_KINDS, nav_section


def _announcement():
    """The banner the admin can switch on in Panel > Settings (shown at the top of every page)."""
    from .models import SiteSetting  # imported here so this file loads before the apps are ready
    try:
        site = SiteSetting.objects.filter(pk=1).first()
    except Exception:  # table not created yet (before migrate)
        site = None
    if site and site.announcement_active and site.announcement_text.strip():
        return {"site_announcement": site.announcement_text.strip(), "site_announcement_level": site.announcement_level}
    return {"site_announcement": "", "site_announcement_level": "info"}


def roles(request):
    """
    Information every page needs: is this a teacher, which sidebar item is active, the name for the
    profile menu, and (teachers only) how many suspicious events happened in the last 24 hours.
    """
    context = {"is_teacher": False, "nav_section": "", "alert_count": 0, "display_name": "", "initial": "?",
               "can_create_ai_quiz": False, "is_admin": False}
    user = getattr(request, "user", None)
    context.update(_announcement())
    if not (user and user.is_authenticated):
        return context

    is_teacher = user.groups.filter(name="Teachers").exists()
    match = getattr(request, "resolver_match", None)
    context.update(
        is_teacher=is_teacher,
        nav_section=nav_section(match.url_name if match else "", is_teacher),
        display_name=user.get_full_name() or user.username,
        initial=(user.username[:1] or "?").upper(),
    )
    from .access import can_create_ai_quiz, cleared_at, is_admin  # imported here so this file loads before the apps are ready
    context["can_create_ai_quiz"] = can_create_ai_quiz(user)
    context["is_admin"] = is_admin(user)
    if context["is_admin"]:
        from .models import Exam
        context["panel_live_exams"] = Exam.objects.filter(status="live").count()
        # admins use the panel menu: map the teacher pages they can open to the matching panel items
        section = {"dashboard": "p_exams", "classes": "p_classes", "create": "p_exams", "reconduct": "p_exams",
                   "practice": "p_quizzes", "ai_quiz": "p_quizzes"}.get(context["nav_section"], context["nav_section"])
        context["nav_section"] = section
    if is_teacher:
        from .models import ExamEvent
        since = timezone.now() - timedelta(hours=24)
        cleared = cleared_at(user)
        if cleared and cleared > since:
            since = cleared
        context["alert_count"] = ExamEvent.objects.filter(
            exam__teacher=user, kind__in=ALERT_KINDS, created_at__gte=since,
        ).count()
    return context
