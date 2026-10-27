from django.contrib import admin
from .models import (
    Exam, ExamAnswer, ExamAttempt, ExamAttemptHistory, ExamEvent, ExamQuestion, StudentProfile, TestCode,
)

for model in (Exam, ExamQuestion, TestCode, ExamAttempt, ExamAnswer, ExamEvent, ExamAttemptHistory, StudentProfile):
    admin.site.register(model)
