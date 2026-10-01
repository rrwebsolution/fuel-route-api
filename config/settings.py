"""
Django settings for the fuel-route-api project.

Secrets and environment-specific values are read from environment variables
(optionally loaded from a local `.env` file). See `.env.example`.
"""

import os
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured
from dotenv import load_dotenv

# Build paths inside the project like this: BASE_DIR / 'subdir'.
BASE_DIR = Path(__file__).resolve().parent.parent

load_dotenv(BASE_DIR / ".env")


def env_bool(name, default=False):
    return os.environ.get(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}


def env_list(name, default=""):
    return [item.strip() for item in os.environ.get(name, default).split(",") if item.strip()]


DEBUG = env_bool("DJANGO_DEBUG", False)

SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY")
if not SECRET_KEY:
    if not DEBUG:
        raise ImproperlyConfigured("DJANGO_SECRET_KEY must be set when DJANGO_DEBUG is false.")
    SECRET_KEY = "django-insecure-local-development-only"

ALLOWED_HOSTS = env_list("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1")


# Application definition

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",

    "rest_framework",
    "routing",
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'config.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

WSGI_APPLICATION = 'config.wsgi.application'


# Database
# https://docs.djangoproject.com/en/6.1/ref/settings/#databases

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.mysql",
        "NAME": os.environ.get("DB_NAME", "fuel_route_db"),
        "USER": os.environ.get("DB_USER", "root"),
        "PASSWORD": os.environ.get("DB_PASSWORD", ""),
        "HOST": os.environ.get("DB_HOST", "127.0.0.1"),
        "PORT": os.environ.get("DB_PORT", "3306"),
        "OPTIONS": {
            "charset": "utf8mb4",
            # Strict mode for this app's connections, whatever the server default is
            # (XAMPP ships a non-strict sql_mode).
            "init_command": "SET sql_mode='STRICT_TRANS_TABLES'",
        },
    }
}


# Cache (geocoding and routing results). Local memory is enough for a single
# process; point this at Redis/Memcached when running several workers.

CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "fuel-route-api",
        "OPTIONS": {"MAX_ENTRIES": 1000},
    }
}


# Password validation
# https://docs.djangoproject.com/en/6.1/ref/settings/#auth-password-validators

AUTH_PASSWORD_VALIDATORS = [
    {
        'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator',
    },
]


# Internationalization
# https://docs.djangoproject.com/en/6.1/topics/i18n/

LANGUAGE_CODE = 'en-us'

TIME_ZONE = 'UTC'

USE_I18N = True

USE_TZ = True


# Static files (CSS, JavaScript, Images)
# https://docs.djangoproject.com/en/6.1/howto/static-files/

STATIC_URL = 'static/'

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"


# Email
# https://docs.djangoproject.com/en/6.1/topics/email/#topic-email-configuration

MAILERS = {
    'default': {
        'BACKEND': 'django.core.mail.backends.console.EmailBackend',
    },
}


# Django REST Framework

REST_FRAMEWORK = {
    "DEFAULT_RENDERER_CLASSES": ["rest_framework.renderers.JSONRenderer"]
    + (["rest_framework.renderers.BrowsableAPIRenderer"] if DEBUG else []),
    "DEFAULT_PARSER_CLASSES": ["rest_framework.parsers.JSONParser"],
    "DEFAULT_AUTHENTICATION_CLASSES": [],
    "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.AllowAny"],
    "UNAUTHENTICATED_USER": None,
}


# External services (free, no API key required)

# Nominatim usage policy requires an identifying User-Agent with contact info.
GEOCODING_URL = os.environ.get("GEOCODING_URL", "https://nominatim.openstreetmap.org/search")
GEOCODING_USER_AGENT = os.environ.get("GEOCODING_USER_AGENT", "fuel-route-api/1.0 (local development)")
ROUTING_URL = os.environ.get("ROUTING_URL", "https://router.project-osrm.org/route/v1/driving")
EXTERNAL_API_TIMEOUT_SECONDS = float(os.environ.get("EXTERNAL_API_TIMEOUT_SECONDS", "15"))
GEOCODING_CACHE_SECONDS = int(os.environ.get("GEOCODING_CACHE_SECONDS", str(60 * 60 * 24 * 30)))
ROUTING_CACHE_SECONDS = int(os.environ.get("ROUTING_CACHE_SECONDS", str(60 * 60 * 24)))


# Trip planning assumptions (see README "Assumptions")

VEHICLE_MAX_RANGE_MILES = 500
VEHICLE_MPG = 10
# Fraction of the tank that is full when the trip starts (1.0 = full tank).
VEHICLE_START_TANK_FRACTION = float(os.environ.get("VEHICLE_START_TANK_FRACTION", "1.0"))
# Minimum saving ($/gal) for a cheaper station to be worth an extra stop. 0 = strictly cheapest plan.
FUEL_MIN_SAVINGS_PER_GALLON = os.environ.get("FUEL_MIN_SAVINGS_PER_GALLON", "0.10")
# A station counts as "on the route" if it is within this many miles of it.
STATION_CORRIDOR_MILES = float(os.environ.get("STATION_CORRIDOR_MILES", "10"))
# Douglas-Peucker tolerance (degrees) used to shrink the geometry returned to clients.
ROUTE_GEOMETRY_SIMPLIFY_TOLERANCE = float(os.environ.get("ROUTE_GEOMETRY_SIMPLIFY_TOLERANCE", "0.0005"))
