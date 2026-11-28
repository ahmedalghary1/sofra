"""Read runtime Docker secrets before base settings validate production configuration."""
from pathlib import Path
from urllib.parse import quote


def file_secret(environ, name, *, allow_empty=False):
    path = environ.get(f'{name}_FILE')
    if not path:
        raise RuntimeError(f'{name}_FILE is required for container deployment.')
    try:
        value = Path(path).read_text(encoding='utf-8').rstrip('\r\n')
    except OSError:
        raise RuntimeError(f'Cannot read {name}_FILE.') from None
    if not value and not allow_empty:
        raise RuntimeError(f'{name}_FILE must not be empty.')
    return value


def configure_environment(environ):
    environ['DJANGO_SECRET_KEY'] = file_secret(environ, 'DJANGO_SECRET_KEY')
    password = file_secret(environ, 'POSTGRES_PASSWORD')
    user = quote(environ.get('PGUSER', 'sofra'), safe='')
    database = quote(environ.get('PGDATABASE', 'sofra'), safe='')
    host = environ.get('PGHOST', 'db')
    port = environ.get('PGPORT', '5432')
    if host != 'db' or port != '5432':
        raise RuntimeError('Container settings require the private Compose db:5432 service.')
    environ['DATABASE_URL'] = f'postgresql://{user}:{quote(password, safe="")}@{host}:{port}/{database}'
    environ['DJANGO_EMAIL_HOST_PASSWORD'] = file_secret(
        environ, 'DJANGO_EMAIL_HOST_PASSWORD', allow_empty=True)
    environ['DJANGO_DEBUG'] = '0'
    environ['DJANGO_ALLOW_SQLITE'] = '0'
    environ['DJANGO_SECURE_SSL_REDIRECT'] = '1'
