from django.contrib import admin
from .models import Attempt, Question, Quiz, QuizJob

admin.site.register(Quiz)
admin.site.register(Question)
admin.site.register(Attempt)
admin.site.register(QuizJob)
