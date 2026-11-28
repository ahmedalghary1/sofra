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
SITE_URL = os.getenv('SITE_URL', 'https://servezaflow.com').strip().rstrip('/')
site_origin = urlparse(SITE_URL)
if (site_origin.scheme not in (('http', 'https') if DEBUG else ('https',))
        or not site_origin.hostname or site_origin.username or site_origin.password
        or site_origin.path or site_origin.params or site_origin.query or site_origin.fragment):
    raise ValueError('SITE_URL must be a website origin. Production requires HTTPS.')
site_hosts = [site_origin.hostname]
site_origins = [SITE_URL]
if '.' in site_origin.hostname and not site_origin.hostname.startswith('www.'):
    site_hosts.append(f'www.{site_origin.hostname}')
    site_origins.append(f'{site_origin.scheme}://www.{site_origin.netloc}')
local_hosts = 'localhost,127.0.0.1,[::1],testserver' if DEBUG else ''
ALLOWED_HOSTS = list(dict.fromkeys(site_hosts + [
    host.strip() for host in os.getenv('DJANGO_ALLOWED_HOSTS', local_hosts).split(',') if host.strip()
]))
CSRF_TRUSTED_ORIGINS = list(dict.fromkeys(site_origins + [
    origin.strip() for origin in os.getenv('DJANGO_CSRF_TRUSTED_ORIGINS', '').split(',') if origin.strip()
]))
CANONICAL_HOST_REDIRECT = not DEBUG
SERVEZA_PUBLIC_EMAIL = os.getenv('SERVEZA_PUBLIC_EMAIL', '').strip()
SERVEZA_LEGAL_NAME = os.getenv('SERVEZA_LEGAL_NAME', '').strip()
SERVEZA_SOCIAL_URLS = [url.strip() for url in os.getenv(
    'SERVEZA_SOCIAL_URLS', 'https://www.facebook.com/profile.php?id=61596269777742&locale=ar_AR',
).split(',')
                       if urlparse(url.strip()).scheme == 'https' and urlparse(url.strip()).hostname]
SERVEZA_ALLOW_CCBOT = os.getenv('SERVEZA_ALLOW_CCBOT', '1').strip() == '1'
INSTALLED_APPS = ['config.admin_apps.PlatformAdminConfig', 'django.contrib.auth', 'django.contrib.contenttypes', 'django.contrib.sessions', 'django.contrib.messages', 'django.contrib.staticfiles', 'django.contrib.sitemaps', 'core']
MIDDLEWARE = ['core.presentation.canonical_host_middleware', 'django.middleware.security.SecurityMiddleware', 'django.contrib.sessions.middleware.SessionMiddleware', 'django.middleware.common.CommonMiddleware', 'django.middleware.csrf.CsrfViewMiddleware', 'django.contrib.auth.middleware.AuthenticationMiddleware', 'django.contrib.messages.middleware.MessageMiddleware', 'django.middleware.clickjacking.XFrameOptionsMiddleware']
ROOT_URLCONF = 'config.urls'
TEMPLATES = [{'BACKEND': 'django.template.backends.django.DjangoTemplates', 'DIRS': [BASE_DIR / 'templates'], 'APP_DIRS': True, 'OPTIONS': {'context_processors': ['django.template.context_processors.debug', 'django.template.context_processors.request', 'django.contrib.auth.context_processors.auth', 'django.contrib.messages.context_processors.messages']}}]
WSGI_APPLICATION = 'config.wsgi.application'
MIDDLEWARE.insert(3, 'core.presentation.language_middleware')
TEMPLATES[0]['OPTIONS']['context_processors'].append('core.presentation.site_metadata')
TEMPLATES[0]['OPTIONS']['context_processors'].append('core.presentation.presentation')
ASGI_APPLICATION = 'config.asgi.application'
db_url = os.getenv('DATABASE_URL', '').strip()
if db_url.startswith(('postgres://', 'postgresql://')):
    from .database import postgres_database
    DATABASES = {'default': postgres_database(db_url)}
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
SECURE_REDIRECT_EXEMPT = [r'^health/$']
SECURE_HSTS_SECONDS = int(os.getenv('DJANGO_SECURE_HSTS_SECONDS', '300')) if not DEBUG else 0
if os.getenv('DJANGO_TRUST_PROXY_TLS', '0') == '1':
    SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')
    USE_X_FORWARDED_HOST = os.getenv('DJANGO_USE_X_FORWARDED_HOST', '0') == '1'
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
TRUSTED_PROXY_NETWORKS = [network.strip() for network in os.getenv('DJANGO_TRUSTED_PROXY_NETWORKS', '').split(',') if network.strip()]
TRUSTED_UNIX_PROXY = os.getenv('DJANGO_TRUST_UNIX_PROXY_IP', '0') == '1'
import ipaddress
for trusted_network in TRUSTED_PROXY_NETWORKS:
    ipaddress.ip_network(trusted_network)
MIDDLEWARE.append('core.security.authentication_limits_middleware')
MIDDLEWARE.insert(0, 'core.observability.request_timing_middleware')
LOGGING = {
    'version': 1, 'disable_existing_loggers': False,
    'formatters': {'request_json': {'()': 'core.observability.RequestFormatter'}},
    'handlers': {'request_console': {'class': 'logging.StreamHandler', 'formatter': 'request_json'}},
    'loggers': {'serveza.requests': {'handlers': ['request_console'],
                                  'level': 'WARNING' if DEBUG else 'INFO', 'propagate': False}},
}
