import os
import secrets
from pathlib import Path
import dj_database_url

BASE_DIR = Path(__file__).resolve().parent.parent
DEBUG = os.getenv('DJANGO_DEBUG', '1') == '1'
SECRET_KEY = os.getenv('DJANGO_SECRET_KEY')
if not SECRET_KEY:
    if not DEBUG:
        raise RuntimeError('Set DJANGO_SECRET_KEY for production')
    secret_path = BASE_DIR / '.local-secret'
    if not secret_path.exists():
        try:
            with secret_path.open('x') as file:
                file.write(secrets.token_urlsafe(48))
        except FileExistsError:
            pass
    SECRET_KEY = secret_path.read_text()
ALLOWED_HOSTS = os.getenv('DJANGO_ALLOWED_HOSTS', 'localhost,127.0.0.1,[::1]').split(',')
INSTALLED_APPS = ['django.contrib.admin', 'django.contrib.auth', 'django.contrib.contenttypes', 'django.contrib.sessions', 'django.contrib.messages', 'django.contrib.staticfiles', 'axes', 'storages', 'shop.apps.ShopConfig']
MIDDLEWARE = ['django.middleware.security.SecurityMiddleware', 'whitenoise.middleware.WhiteNoiseMiddleware', 'django.contrib.sessions.middleware.SessionMiddleware', 'django.middleware.common.CommonMiddleware', 'django.middleware.csrf.CsrfViewMiddleware', 'django.contrib.auth.middleware.AuthenticationMiddleware', 'django.contrib.messages.middleware.MessageMiddleware', 'django.middleware.clickjacking.XFrameOptionsMiddleware', 'axes.middleware.AxesMiddleware', 'shop.middleware.ErrorEnvelopeMiddleware']
AUTHENTICATION_BACKENDS = ['axes.backends.AxesStandaloneBackend', 'django.contrib.auth.backends.ModelBackend']
AXES_FAILURE_LIMIT = 5
AXES_COOLOFF_TIME = 1  # hours
AXES_RESET_ON_SUCCESS = True
AXES_LOCKOUT_TEMPLATE = 'account_locked.html'
ROOT_URLCONF = 'duka.urls'
TEMPLATES = [{'BACKEND': 'django.template.backends.django.DjangoTemplates', 'DIRS': [BASE_DIR / 'templates'], 'APP_DIRS': True, 'OPTIONS': {'context_processors': ['django.template.context_processors.request', 'django.contrib.auth.context_processors.auth', 'django.contrib.messages.context_processors.messages']}}]
WSGI_APPLICATION = 'duka.wsgi.application'
# DATABASE_URL (e.g. postgres://user:pass@host:5432/dbname) switches the engine to PostgreSQL
# in any environment that sets it; unset (local dev today) keeps the existing SQLite config
# untouched. conn_max_age enables persistent connections only once Postgres is in use.
if os.getenv('DATABASE_URL'):
    DATABASES = {'default': dj_database_url.parse(os.getenv('DATABASE_URL'), conn_max_age=600)}
else:
    DATABASES = {'default': {'ENGINE': 'django.db.backends.sqlite3', 'NAME': BASE_DIR / 'db.sqlite3', 'OPTIONS': {'timeout': 20}}}
AUTH_PASSWORD_VALIDATORS = [{'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'}, {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator'}, {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'}, {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'}]
LANGUAGE_CODE = 'en'
TIME_ZONE = 'Africa/Dar_es_Salaam'
USE_I18N = True
USE_TZ = True
STATIC_URL = '/static/'
STATICFILES_DIRS = [BASE_DIR / 'static']
STATIC_ROOT = BASE_DIR / 'staticfiles'
# The manifest-hashed storage requires `collectstatic` to have already run (it reads
# staticfiles.json); that's true for a real deployment (see PRODUCTION_DEPLOYMENT.md) but not
# for local `manage.py test` or CI, which don't run collectstatic first. Gate on DATABASE_URL —
# the same "is this a real deployment" signal the DATABASES block above already uses — so tests
# keep working locally and in CI without requiring an extra build step.
STORAGES = {
    'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage'},
    'staticfiles': {'BACKEND': 'whitenoise.storage.CompressedManifestStaticFilesStorage' if os.getenv('DATABASE_URL') else 'django.contrib.staticfiles.storage.StaticFilesStorage'},
}
MEDIA_URL = '/media/'
MEDIA_ROOT = BASE_DIR / 'media'
# Product photos on S3-compatible object storage (e.g. Supabase Storage), so uploads survive
# redeploys and don't depend on any one instance's local disk (see PRODUCTION_DEPLOYMENT.md
# item 4 and RENDER_DEPLOY.md). Local dev keeps writing to media/ on disk when this is unset.
if os.getenv('SUPABASE_S3_ENDPOINT'):
    STORAGES['default'] = {'BACKEND': 'storages.backends.s3.S3Storage'}
    AWS_ACCESS_KEY_ID = os.getenv('SUPABASE_S3_ACCESS_KEY_ID')
    AWS_SECRET_ACCESS_KEY = os.getenv('SUPABASE_S3_SECRET_ACCESS_KEY')
    AWS_STORAGE_BUCKET_NAME = os.getenv('SUPABASE_S3_BUCKET', 'duka-media')
    AWS_S3_ENDPOINT_URL = os.getenv('SUPABASE_S3_ENDPOINT')
    AWS_S3_REGION_NAME = os.getenv('SUPABASE_S3_REGION', 'us-east-1')
    AWS_S3_ADDRESSING_STYLE = 'path'
    AWS_DEFAULT_ACL = None
    AWS_QUERYSTRING_AUTH = False
    AWS_S3_FILE_OVERWRITE = False
    # django-storages builds file URLs from AWS_S3_CUSTOM_DOMAIN, not from Django's global
    # MEDIA_URL — without this, S3Storage.url() falls back to the S3 API endpoint itself
    # (AWS_S3_ENDPOINT_URL), which requires a signed request and 403s on a plain GET. Supabase
    # serves public-bucket objects from a separate REST path, so that's what's set here.
    _supabase_host = os.getenv('SUPABASE_PUBLIC_URL', '').removeprefix('https://').removeprefix('http://').rstrip('/')
    AWS_S3_CUSTOM_DOMAIN = f'{_supabase_host}/storage/v1/object/public/{AWS_STORAGE_BUCKET_NAME}'
    MEDIA_URL = f'https://{AWS_S3_CUSTOM_DOMAIN}/'
DATA_UPLOAD_MAX_MEMORY_SIZE = 7 * 1024 * 1024
FILE_UPLOAD_MAX_MEMORY_SIZE = 5 * 1024 * 1024
CREDENTIAL_ENCRYPTION_KEY = os.getenv('CREDENTIAL_ENCRYPTION_KEY', '')
AI_MODEL = os.getenv('AI_MODEL', 'gpt-4.1-mini')
# Platform-level OTP delivery for signup and password reset (see shop/providers.py:
# email_otp/whatsapp_otp). Unlike Ghala/Snippe/OpenAI these are not per-shop credentials —
# there's one Duka-owned Resend/Africa's Talking account, since a brand-new signup has no shop
# yet to hold a per-merchant credential. Left unset, both raise ProviderError immediately rather
# than silently no-op — see OWNER_ACTION_REQUIRED.md for account setup.
RESEND_API_KEY = os.getenv('RESEND_API_KEY', '')
RESEND_FROM_EMAIL = os.getenv('RESEND_FROM_EMAIL', 'Duka <no-reply@duka.example.com>')
AFRICASTALKING_USERNAME = os.getenv('AFRICASTALKING_USERNAME', '')
AFRICASTALKING_API_KEY = os.getenv('AFRICASTALKING_API_KEY', '')
AFRICASTALKING_WA_NUMBER = os.getenv('AFRICASTALKING_WA_NUMBER', '')
AFRICASTALKING_WA_TEMPLATE_ID = os.getenv('AFRICASTALKING_WA_TEMPLATE_ID', '')
OTP_TTL_SECONDS = int(os.getenv('OTP_TTL_SECONDS', '300'))
LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'formatters': {'json': {'()': 'duka.logging.JsonFormatter'}},
    'handlers': {'console': {'class': 'logging.StreamHandler', 'formatter': 'json'}},
    'root': {'handlers': ['console'], 'level': 'INFO'},
    'loggers': {'django': {'handlers': ['console'], 'level': 'INFO', 'propagate': False}},
}
SENTRY_DSN = os.getenv('SENTRY_DSN', '')
if SENTRY_DSN:
    import sentry_sdk
    sentry_sdk.init(dsn=SENTRY_DSN, traces_sample_rate=0.1, send_default_pii=False)
DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'
LOGIN_URL = '/login/'
LOGIN_REDIRECT_URL = '/'
LOGOUT_REDIRECT_URL = '/login/'
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = 'Lax'
if not DEBUG:
    SECURE_SSL_REDIRECT = True
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_HSTS_SECONDS = 31536000
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    SECURE_HSTS_PRELOAD = True
