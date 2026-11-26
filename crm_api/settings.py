import os
from pathlib import Path
from django.core.exceptions import ImproperlyConfigured

try:
    from dotenv import load_dotenv
except ImportError:
    load_dotenv = None

# Build paths inside the project like this: BASE_DIR / 'subdir'.
BASE_DIR = Path(__file__).resolve().parent.parent

if load_dotenv is not None:
    load_dotenv(BASE_DIR / '.env')

# Valores padrão exclusivos para desenvolvimento local (bloqueados quando DEBUG=False)
DEV_DEFAULT_SECRET_KEY = 'dev-only-local-secret-key-change-in-production'
DEV_DEFAULT_WEBHOOK_KEY = 'dev-local-webhook-key'

INSECURE_SECRET_KEYS = {
    '',
    DEV_DEFAULT_SECRET_KEY,
    'your-secret-key-here',
    'changeme',
    'secret',
}
INSECURE_WEBHOOK_KEYS = {
    '',
    DEV_DEFAULT_WEBHOOK_KEY,
    'your-webhook-api-key-here',
    'changeme',
}


def validate_production_secrets(debug: bool, secret_key: str, webhook_api_key: str) -> None:
    """Garante no startup que segredos padrão de desenvolvimento nunca rodem com DEBUG=False."""
    if debug:
        return
    if (
        not secret_key
        or secret_key in INSECURE_SECRET_KEYS
        or secret_key.startswith('django-insecure-')
    ):
        raise ImproperlyConfigured(
            'Configuração insegura: SECRET_KEY ausente ou usando valor padrão de desenvolvimento '
            'com DEBUG=False. Defina uma SECRET_KEY forte nas variáveis de ambiente.'
        )
    if not webhook_api_key or webhook_api_key in INSECURE_WEBHOOK_KEYS:
        raise ImproperlyConfigured(
            'Configuração insegura: WEBHOOK_API_KEY ausente ou usando valor padrão de desenvolvimento '
            'com DEBUG=False. Defina WEBHOOK_API_KEY nas variáveis de ambiente.'
        )


# Ambiente e Segredos Principais (estritamente via variáveis de ambiente)
DEBUG = os.environ.get('DEBUG', 'True').strip().lower() in ('1', 'true', 'yes', 'on')
SECRET_KEY = os.environ.get('SECRET_KEY', DEV_DEFAULT_SECRET_KEY)
WEBHOOK_API_KEY = os.environ.get('WEBHOOK_API_KEY', DEV_DEFAULT_WEBHOOK_KEY)
FIELD_ENCRYPTION_KEY = os.environ.get('FIELD_ENCRYPTION_KEY', '').strip()

validate_production_secrets(DEBUG, SECRET_KEY, WEBHOOK_API_KEY)

_default_hosts = '*' if DEBUG else 'localhost,127.0.0.1,18.220.198.164.sslip.io'
ALLOWED_HOSTS = [
    h.strip()
    for h in os.environ.get('ALLOWED_HOSTS', _default_hosts).split(',')
    if h.strip()
]

# Application definition
INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'rest_framework',
    'core',
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

ROOT_URLCONF = 'crm_api.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [BASE_DIR / 'templates'],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
                'core.context_processors.user_role_context',
            ],
        },
    },
]

WSGI_APPLICATION = 'crm_api.wsgi.application'

# Database (RDS PostgreSQL via DATABASE_URL ou fallback local SQLite)
DATABASE_URL = os.environ.get('DATABASE_URL', '').strip()
if DATABASE_URL:
    import dj_database_url
    DATABASES = {
        'default': dj_database_url.parse(
            DATABASE_URL,
            conn_max_age=int(os.environ.get('DB_CONN_MAX_AGE', '600')),
            ssl_require=os.environ.get('DB_SSL_REQUIRE', 'False').strip().lower() in ('1', 'true', 'yes'),
        )
    }
else:
    DATABASES = {
        'default': {
            'ENGINE': 'django.db.backends.sqlite3',
            'NAME': BASE_DIR / 'db.sqlite3',
        }
    }

# Cache (usado pelo Rate Limiting e controles de segurança)
CACHES = {
    'default': {
        'BACKEND': os.environ.get(
            'CACHE_BACKEND',
            'django.core.cache.backends.locmem.LocMemCache',
        ),
        'LOCATION': os.environ.get('CACHE_LOCATION', 'imobicrm-security-cache'),
    }
}

# Django REST Framework — Autenticação exigida por padrão
REST_FRAMEWORK = {
    'DEFAULT_PERMISSION_CLASSES': [
        'rest_framework.permissions.IsAuthenticated',
    ],
    'DEFAULT_AUTHENTICATION_CLASSES': [
        'rest_framework.authentication.SessionAuthentication',
        'rest_framework.authentication.BasicAuthentication',
    ],
}

# Password validation
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
LANGUAGE_CODE = 'pt-br'
TIME_ZONE = 'America/Sao_Paulo'
USE_I18N = True
USE_TZ = True

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

# Static files (CSS, JavaScript, Images)
STATIC_URL = '/static/'
STATIC_ROOT = BASE_DIR / 'staticfiles'
STATICFILES_DIRS = [
    BASE_DIR / 'static',
]

# Media files & AWS S3 Storage (estritamente via variáveis de ambiente)
MEDIA_URL = '/media/'
MEDIA_ROOT = BASE_DIR / 'media'

AWS_ACCESS_KEY_ID = os.environ.get('AWS_ACCESS_KEY_ID', '').strip()
AWS_SECRET_ACCESS_KEY = os.environ.get('AWS_SECRET_ACCESS_KEY', '').strip()
AWS_STORAGE_BUCKET_NAME = os.environ.get('AWS_STORAGE_BUCKET_NAME', '').strip()
AWS_S3_REGION_NAME = os.environ.get('AWS_S3_REGION_NAME', 'us-east-1').strip()
AWS_S3_CUSTOM_DOMAIN = os.environ.get('AWS_S3_CUSTOM_DOMAIN', '').strip() or None
AWS_S3_FILE_OVERWRITE = False
AWS_DEFAULT_ACL = None
AWS_QUERYSTRING_AUTH = os.environ.get('AWS_QUERYSTRING_AUTH', 'True').strip().lower() in ('1', 'true', 'yes')

if AWS_STORAGE_BUCKET_NAME and AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY:
    if 'storages' not in INSTALLED_APPS:
        INSTALLED_APPS.append('storages')
    STORAGES = {
        'default': {
            'BACKEND': 'storages.backends.s3.S3Storage',
        },
        'staticfiles': {
            'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage',
        },
    }

# Email
MAILERS = {
    'default': {
        'BACKEND': os.environ.get(
            'EMAIL_BACKEND',
            'django.core.mail.backends.console.EmailBackend',
        ),
    },
}

# Origens confiáveis CSRF
_env_csrf_origins = [
    o.strip()
    for o in os.environ.get('CSRF_TRUSTED_ORIGINS', '').split(',')
    if o.strip()
]
CSRF_TRUSTED_ORIGINS = _env_csrf_origins or [
    'https://18.220.198.164.sslip.io',
]

CSRF_COOKIE_HTTPONLY = False
CSRF_COOKIE_SAMESITE = 'Lax'
SESSION_COOKIE_SAMESITE = 'Lax'
SESSION_COOKIE_HTTPONLY = True
CSRF_COOKIE_SECURE = False
SESSION_COOKIE_SECURE = False
SECURE_CONTENT_TYPE_NOSNIFF = True
X_FRAME_OPTIONS = 'DENY'
SECURE_REFERRER_POLICY = 'strict-origin-when-cross-origin'

if DEBUG:
    CSRF_TRUSTED_ORIGINS = list(set(CSRF_TRUSTED_ORIGINS + [
        'http://localhost:8000',
        'http://127.0.0.1:8000',
        'http://192.168.0.10:8000',
        'http://192.168.1.10:8000',
    ]))
else:
    # Endurecimento completo de cabeçalhos e cookies em Produção (DEBUG=False)
    SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SESSION_COOKIE_HTTPONLY = True
    SECURE_HSTS_SECONDS = 31536000
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    SECURE_CONTENT_TYPE_NOSNIFF = True
    X_FRAME_OPTIONS = 'DENY'
    SECURE_REFERRER_POLICY = 'strict-origin-when-cross-origin'

# Login
LOGIN_URL = '/login/'
LOGIN_REDIRECT_URL = '/'
LOGOUT_REDIRECT_URL = '/login/'

# Credenciais Google OAuth (estritamente via variáveis de ambiente)
GOOGLE_CLIENT_ID = os.environ.get('GOOGLE_CLIENT_ID', '').strip()
GOOGLE_CLIENT_SECRET = os.environ.get('GOOGLE_CLIENT_SECRET', '').strip()
GOOGLE_REDIRECT_URI = os.environ.get('GOOGLE_REDIRECT_URI', '').strip()
