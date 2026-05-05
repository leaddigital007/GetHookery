"""
Django settings for the GetHookery / Kubricon investor admin.

The same Heroku app serves the public landing (from `public/` via WhiteNoise)
and the closed admin under `/admin/`.
"""
from pathlib import Path

import dj_database_url
import environ

BASE_DIR = Path(__file__).resolve().parent.parent

env = environ.Env(
    DJANGO_DEBUG=(bool, False),
)
environ.Env.read_env(BASE_DIR / ".env")

SECRET_KEY = env("DJANGO_SECRET_KEY", default="insecure-dev-key-change-me")

DEBUG = env("DJANGO_DEBUG")

ALLOWED_HOSTS = [
    h.strip()
    for h in env("DJANGO_ALLOWED_HOSTS", default="localhost,127.0.0.1").split(",")
    if h.strip()
]

CSRF_TRUSTED_ORIGINS = [
    f"https://{host}" for host in ALLOWED_HOSTS if host and not host.startswith(".")
] + [
    f"https://*{host}" for host in ALLOWED_HOSTS if host.startswith(".")
]

INSTALLED_APPS = [
    "whitenoise.runserver_nostatic",
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "import_export",
    "apps.investors",
    "apps.site",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"

# Postgres URL: prefer DATABASE_URL, fall back to the Stackhero addon's
# variable name on Heroku. SQLite is the local-dev fallback.
_database_url = env(
    "DATABASE_URL",
    default=env(
        "STACKHERO_POSTGRESQL_DATABASE_URL",
        default="sqlite:///" + str(BASE_DIR / "db.sqlite3"),
    ),
)
DATABASES = {
    "default": dj_database_url.parse(
        _database_url,
        conn_max_age=600,
    ),
}

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

# Static files
#
# Two roles handled by WhiteNoise:
#   1. Django's collected staticfiles (admin assets, import-export JS) are
#      served at /static/ from STATIC_ROOT after `collectstatic`.
#   2. The public landing in `public/` is served at root URLs via
#      WHITENOISE_ROOT, so existing paths like /css/styles.css and
#      /images/hero-bg.png keep working unchanged. WHITENOISE_INDEX_FILE
#      makes `/` resolve to `public/index.html`.
STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"},
}
WHITENOISE_ROOT = BASE_DIR / "public"
WHITENOISE_INDEX_FILE = True

# Hardening when running behind Heroku's TLS terminator.
if not DEBUG:
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_SSL_REDIRECT = True
    SECURE_HSTS_SECONDS = 60 * 60 * 24 * 30
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    SECURE_HSTS_PRELOAD = True

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

LOGIN_URL = "admin:login"
LOGIN_REDIRECT_URL = "admin:index"

# django-import-export: keep imports synchronous and limit batch size for the
# Heroku web dyno's request budget.
IMPORT_EXPORT_USE_TRANSACTIONS = True
IMPORT_EXPORT_SKIP_ADMIN_LOG = False
