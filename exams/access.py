"""Who is allowed to do what. Small helpers shared by the exams and quiz apps."""
from .models import Enrollment, NotificationState


def is_teacher(user):
    return user.is_authenticated and user.groups.filter(name="Teachers").exists()


def in_group(user, name):
    return user.is_authenticated and user.groups.filter(name=name).exists()


def is_admin(user):
    """Admins run the whole software: superusers and members of the "Admins" group."""
    return user.is_authenticated and (user.is_superuser or in_group(user, "Admins"))


def can_create_ai_quiz(user):
    """Teachers always. Students only if one of their classes allows it (the teacher decides)."""
    if not user.is_authenticated:
        return False
    if is_admin(user) or is_teacher(user):
        return True
    return Enrollment.objects.filter(student=user, classroom__allow_ai_quiz=True).exists()


def cleared_at(user):
    """The time the user last cleared their notifications (or None)."""
    state = NotificationState.objects.filter(user=user).first()
    return state.cleared_at if state else None
