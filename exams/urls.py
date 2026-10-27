from django.urls import path
from . import views

urlpatterns = [
    # ---- teacher ----
    path("teacher/login/", views.TeacherLoginView.as_view(), name="teacher_login"),
    path("teacher/register/", views.teacher_register, name="teacher_register"),
    path("teacher/", views.teacher_dashboard, name="teacher_dashboard"),
    path("teacher/exam/new/", views.exam_create, name="exam_create"),
    path("teacher/generate-questions/", views.exam_generate_questions, name="exam_generate_questions"),
    path("teacher/exam/<int:exam_id>/", views.exam_monitor, name="exam_monitor"),
    path("teacher/exam/<int:exam_id>/edit/", views.exam_edit, name="exam_edit"),
    path("teacher/reconduct/", views.reconduct_select, name="reconduct_select"),
    path("teacher/exam/<int:exam_id>/reconduct/", views.exam_reconduct, name="exam_reconduct"),
    path("teacher/exam/<int:exam_id>/delete/", views.exam_delete, name="exam_delete"),
    path("teacher/exam/<int:exam_id>/live/", views.exam_live, name="exam_live"),
    path("teacher/exam/<int:exam_id>/start/", views.exam_start, name="exam_start"),
    path("teacher/exam/<int:exam_id>/close/", views.exam_close, name="exam_close"),
    path("teacher/exam/<int:exam_id>/pause/", views.exam_pause, name="exam_pause"),
    path("teacher/exam/<int:exam_id>/resume/", views.exam_resume, name="exam_resume"),
    path("teacher/exam/<int:exam_id>/add-time/", views.exam_add_time, name="exam_add_time"),
    path("teacher/exam/<int:exam_id>/code/regenerate/", views.code_regenerate, name="code_regenerate"),
    path("teacher/exam/<int:exam_id>/code/invalidate/", views.code_invalidate, name="code_invalidate"),
    path("teacher/exam/<int:exam_id>/results.csv", views.exam_results_csv, name="exam_results_csv"),
    path("teacher/attempt/<int:attempt_id>/", views.attempt_detail, name="attempt_detail"),
    path("teacher/attempt/<int:attempt_id>/terminate/", views.attempt_terminate, name="attempt_terminate"),
    path("teacher/attempt/<int:attempt_id>/restart/", views.attempt_restart, name="attempt_restart"),
    path("teacher/attempt/<int:attempt_id>/pause/", views.attempt_pause, name="attempt_pause"),
    path("teacher/attempt/<int:attempt_id>/resume/", views.attempt_resume, name="attempt_resume"),
    path("teacher/attempt/<int:attempt_id>/add-time/", views.attempt_add_time, name="attempt_add_time"),

    # ---- student ----
    path("exam/join/", views.exam_join, name="exam_join"),
    path("exam/dashboard/", views.student_dashboard, name="student_dashboard"),
    path("exam/attempt/<int:attempt_id>/", views.attempt_open, name="attempt_open"),
    path("exam/attempt/<int:attempt_id>/instructions/", views.attempt_instructions, name="attempt_instructions"),
    path("exam/attempt/<int:attempt_id>/begin/", views.attempt_begin, name="attempt_begin"),
    path("exam/attempt/<int:attempt_id>/take/", views.attempt_take, name="attempt_take"),
    path("exam/attempt/<int:attempt_id>/done/", views.attempt_done, name="attempt_done"),

    # ---- student JSON endpoints used by the quiz page ----
    path("exam/attempt/<int:attempt_id>/api/state/", views.api_state, name="api_state"),
    path("exam/attempt/<int:attempt_id>/api/save/", views.api_save, name="api_save"),
    path("exam/attempt/<int:attempt_id>/api/event/", views.api_event, name="api_event"),
    path("exam/attempt/<int:attempt_id>/api/submit/", views.api_submit, name="api_submit"),
]
