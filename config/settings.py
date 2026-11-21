import os
from pathlib import Path
from urllib.parse import urlparse
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / '.env', override=False, interpolate=False)
SECRET_KEY = os.getenv('DJANGO_SECRET_KEY', 'dev-only-change-this-key-before-deployment')
DEBUG = os.getenv('DJANGO_DEBUG', '1') == '1'
if not DEBUG and (SECRET_KEY in ('dev-only-change-this-key-before-deployment',
                                  'replace-with-a-long-random-production-secret') or len(SECRET_KEY) < 50):
    raise RuntimeError('Set a long random DJANGO_SECRET_KEY before running in production.')
ALLOWED_HOSTS = os.getenv('DJANGO_ALLOWED_HOSTS', 'localhost,127.0.0.1,testserver').split(',')
if not DEBUG and not os.getenv('DJANGO_ALLOWED_HOSTS'):
    raise RuntimeError('Set DJANGO_ALLOWED_HOSTS before running in production.')
# The public domain is proxied to a different PythonAnywhere host.
CSRF_TRUSTED_ORIGINS = ['https://servezaflow.com']
CSRF_TRUSTED_ORIGINS += [
    origin.strip() for origin in os.getenv('DJANGO_CSRF_TRUSTED_ORIGINS', '').split(',')
    if origin.strip()
]
INSTALLED_APPS = ['django.contrib.admin', 'django.contrib.auth', 'django.contrib.contenttypes', 'django.contrib.sessions', 'django.contrib.messages', 'django.contrib.staticfiles', 'core']
MIDDLEWARE = ['django.middleware.security.SecurityMiddleware', 'django.contrib.sessions.middleware.SessionMiddleware', 'django.middleware.common.CommonMiddleware', 'django.middleware.csrf.CsrfViewMiddleware', 'django.contrib.auth.middleware.AuthenticationMiddleware', 'django.contrib.messages.middleware.MessageMiddleware', 'django.middleware.clickjacking.XFrameOptionsMiddleware']
ROOT_URLCONF = 'config.urls'
TEMPLATES = [{'BACKEND': 'django.template.backends.django.DjangoTemplates', 'DIRS': [BASE_DIR / 'templates'], 'APP_DIRS': True, 'OPTIONS': {'context_processors': ['django.template.context_processors.debug', 'django.template.context_processors.request', 'django.contrib.auth.context_processors.auth', 'django.contrib.messages.context_processors.messages']}}]
WSGI_APPLICATION = 'config.wsgi.application'
MIDDLEWARE.insert(2, 'core.presentation.language_middleware')
TEMPLATES[0]['OPTIONS']['context_processors'].append('core.presentation.presentation')
ASGI_APPLICATION = 'config.asgi.application'
db_url = os.getenv('DATABASE_URL', '').strip()
if db_url.startswith(('postgres://', 'postgresql://')):
    db = urlparse(db_url)
    DATABASES = {'default': {'ENGINE': 'django.db.backends.postgresql', 'NAME': db.path.lstrip('/'), 'USER': db.username, 'PASSWORD': db.password, 'HOST': db.hostname, 'PORT': db.port or 5432}}
elif db_url:
    raise RuntimeError('DATABASE_URL must be a PostgreSQL URL. Leave it unset to use SQLite.')
elif DEBUG or os.getenv('DJANGO_ALLOW_SQLITE', '0') == '1':
    DATABASES = {'default': {'ENGINE': 'django.db.backends.sqlite3', 'NAME': BASE_DIR / 'db.sqlite3'}}
else:
    raise RuntimeError('Set a PostgreSQL DATABASE_URL or explicitly enable SQLite with DJANGO_ALLOW_SQLITE=1.')
AUTH_PASSWORD_VALIDATORS = [{'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'}, {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator'}, {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'}, {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'}]
LANGUAGE_CODE = 'ar'
TIME_ZONE = 'Africa/Cairo'
USE_I18N = True
USE_TZ = True
STATIC_URL = 'static/'
STATICFILES_DIRS = [BASE_DIR / 'static']
STATIC_ROOT = BASE_DIR / 'staticfiles'
MEDIA_URL = 'media/'
MEDIA_ROOT = BASE_DIR / 'media'
DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'
LOGIN_URL = 'login'
CSRF_FAILURE_VIEW = 'core.marketing.forbidden_form'
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = 'same-origin'
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SECURE = not DEBUG
CSRF_COOKIE_SECURE = not DEBUG
SECURE_SSL_REDIRECT = not DEBUG and os.getenv('DJANGO_SECURE_SSL_REDIRECT', '1') == '1'
SECURE_HSTS_SECONDS = 31536000 if not DEBUG else 0
if os.getenv('DJANGO_TRUST_PROXY_TLS', '0') == '1':
    SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')
PASSWORD_RESET_TIMEOUT = 3600
EMAIL_HOST = os.getenv('DJANGO_EMAIL_HOST', 'localhost')
EMAIL_PORT = int(os.getenv('DJANGO_EMAIL_PORT', '587'))
EMAIL_HOST_USER = os.getenv('DJANGO_EMAIL_HOST_USER', '').strip()
EMAIL_HOST_PASSWORD = os.getenv('DJANGO_EMAIL_HOST_PASSWORD', '')
EMAIL_USE_TLS = os.getenv('DJANGO_EMAIL_USE_TLS', '1') == '1'
EMAIL_TIMEOUT = 10
DEFAULT_FROM_EMAIL = os.getenv('DJANGO_DEFAULT_FROM_EMAIL') or f"Serveza <{EMAIL_HOST_USER or 'no-reply@example.com'}>"
mail_mode = os.getenv('DJANGO_EMAIL_BACKEND') or ('smtp' if EMAIL_HOST_USER or not DEBUG else 'console')
if mail_mode not in ('smtp', 'console'):
    raise ValueError('DJANGO_EMAIL_BACKEND must be smtp or console.')
EMAIL_BACKEND = f'django.core.mail.backends.{mail_mode}.EmailBackend'
PUBLIC_BASE_URL = os.getenv('DJANGO_PUBLIC_BASE_URL', '').strip().rstrip('/')
if PUBLIC_BASE_URL:
    origin = urlparse(PUBLIC_BASE_URL)
    if origin.scheme not in ('http', 'https') or not origin.hostname or origin.username or origin.password or origin.path or origin.query or origin.fragment:
        raise ValueError('DJANGO_PUBLIC_BASE_URL must be a website origin, such as https://your-domain.example.')
