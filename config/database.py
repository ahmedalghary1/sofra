"""Parse the supported PostgreSQL DSN without dropping its security options."""
from urllib.parse import parse_qsl, unquote, urlsplit


def postgres_database(url):
    parsed = urlsplit(url)
    if parsed.scheme not in ('postgres', 'postgresql') or not parsed.hostname or not parsed.path.lstrip('/'):
        raise ValueError('DATABASE_URL must include a PostgreSQL host and database name.')
    allowed = {'sslmode', 'sslrootcert', 'sslcert', 'sslkey', 'sslpassword', 'connect_timeout',
               'application_name', 'target_session_attrs', 'options', 'channel_binding',
               'ssl_min_protocol_version', 'ssl_max_protocol_version', 'gssencmode'}
    pairs = parse_qsl(parsed.query, keep_blank_values=True, strict_parsing=True)
    if any(key not in allowed for key, _ in pairs) or len({key for key, _ in pairs}) != len(pairs):
        raise ValueError('DATABASE_URL contains an unsupported or repeated connection option.')
    options = dict(pairs)
    if 'sslmode' in options and options['sslmode'] not in ('disable', 'allow', 'prefer', 'require', 'verify-ca', 'verify-full'):
        raise ValueError('DATABASE_URL has an invalid sslmode.')
    return {'ENGINE': 'django.db.backends.postgresql', 'NAME': unquote(parsed.path.lstrip('/')),
            'USER': unquote(parsed.username or ''), 'PASSWORD': unquote(parsed.password or ''),
            'HOST': parsed.hostname, 'PORT': parsed.port or 5432, 'OPTIONS': options}
