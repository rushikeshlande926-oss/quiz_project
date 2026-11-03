from django.contrib import admin
from django.urls import include, path

from exams.views import StudentLoginView

urlpatterns = [
    path("admin/", admin.site.urls),
    path("accounts/login/", StudentLoginView.as_view(), name="login"),  # same page, with a wrong-password limit
    path("accounts/", include("django.contrib.auth.urls")),  # login, logout
    path("", include("quiz.urls")),
    path("", include("exams.urls")),
]
