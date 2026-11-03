from django.contrib import admin
from .models import (
    Classroom, Enrollment, Exam, ExamAnswer, ExamAttempt, ExamAttemptHistory, ExamEvent, ExamQuestion,
    NotificationState, StudentProfile, TestCode, SiteSetting, AdminLog,
)

for model in (Exam, ExamQuestion, TestCode, ExamAttempt, ExamAnswer, ExamEvent, ExamAttemptHistory,
              StudentProfile, Classroom, Enrollment, NotificationState, SiteSetting, AdminLog):
    admin.site.register(model)
