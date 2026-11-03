import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

# For learning/college use only. Change this if you ever put the site online.
# Set these environment variables when you put the site online:
#   DJANGO_SECRET_KEY=<long random text>   DJANGO_DEBUG=0   DJANGO_ALLOWED_HOSTS=yourdomain.com
SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "change-this-to-any-long-random-text")
DEBUG = os.environ.get("DJANGO_DEBUG", "1") == "1"
ALLOWED_HOSTS = [h.strip() for h in os.environ.get("DJANGO_ALLOWED_HOSTS", "").split(",") if h.strip()]

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "quiz",
    "exams",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "quizproject.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "exams.context_processors.roles",
            ],
        },
    },
]

WSGI_APPLICATION = "quizproject.wsgi.application"

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / "db.sqlite3",
        "OPTIONS": {"timeout": 20},  # wait if the database is busy (background job + web page)
    }
}

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
]

LANGUAGE_CODE = "en-us"
TIME_ZONE = "Asia/Kolkata"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATICFILES_DIRS = [BASE_DIR / "static"]  # our stylesheet and scripts live in /static
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

LOGIN_URL = "login"
LOGIN_REDIRECT_URL = "quiz_list"
LOGOUT_REDIRECT_URL = "login"

# Gemini settings. The key is read from an environment variable (never write it in code).
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-3.8-flash")

# Teachers need this code to create a teacher account. CHANGE IT (or set the environment variable).
# With DEBUG on (your own computer) the default is "teacher123". With DEBUG off there is NO default:
# if TEACHER_INVITE_CODE is not set, nobody can register as a teacher.
TEACHER_INVITE_CODE = os.environ.get("TEACHER_INVITE_CODE", "teacher123" if DEBUG else "")
