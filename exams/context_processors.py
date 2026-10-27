def roles(request):
    """Lets every template know if the logged-in user is a teacher (used in the menu)."""
    user = getattr(request, "user", None)
    is_teacher = bool(
        user and user.is_authenticated and user.groups.filter(name="Teachers").exists()
    )
    return {"is_teacher": is_teacher}
