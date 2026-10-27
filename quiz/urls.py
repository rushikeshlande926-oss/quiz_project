from django.urls import path
from . import views

urlpatterns = [
    path("", views.quiz_list, name="quiz_list"),
    path("register/", views.register, name="register"),
    path("join/", views.join_quiz, name="join_quiz"),
    path("join/<str:code>/", views.join_quiz, name="join_quiz_code"),
    path("upload/", views.upload_notes, name="upload_notes"),
    path("job/<int:job_id>/", views.job_page, name="job_page"),
    path("job/<int:job_id>/status/", views.job_status, name="job_status"),
    path("quiz/<int:quiz_id>/", views.take_quiz, name="take_quiz"),
    path("quiz/<int:quiz_id>/result/", views.result, name="result"),
    path("quiz/<int:quiz_id>/result.pdf", views.export_result_pdf, name="export_result_pdf"),
    path("quiz/<int:quiz_id>/leaderboard/", views.leaderboard, name="leaderboard"),
    path("quiz/<int:quiz_id>/leaderboard.csv", views.export_leaderboard, name="export_leaderboard"),
    path("quiz/<int:quiz_id>/analytics/", views.analytics, name="analytics"),
]
