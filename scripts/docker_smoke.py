"""Isolated Compose acceptance check; requires a running Linux Docker engine.

Run only with generated localhost config and an EMPTY dedicated Compose project.
Leaves volumes intact for inspection; CI performs explicit cleanup afterwards.
"""
import base64
import http.cookiejar
import os
from pathlib import Path
import re
import ssl
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request

CONTEXT = ssl._create_unverified_context()  # localhost's private Caddy CA, never production verification


def compose(*args, capture=False):
    return subprocess.run(['docker', 'compose', *args], check=True, text=True,
                          stdout=subprocess.PIPE if capture else None)


def request(path):
    return urllib.request.urlopen('https://localhost' + path, context=CONTEXT, timeout=15)


def django(source):
    return compose('exec', '-T', 'web', 'python', 'manage.py', 'shell', '-c', source, capture=True).stdout


def main():
    if not os.environ.get('COMPOSE_PROJECT_NAME', '').startswith('serveza-smoke-'):
        raise SystemExit('Use a dedicated COMPOSE_PROJECT_NAME starting with serveza-smoke-.')
    if 'SITE_URL=https://localhost\n' not in Path('.env.docker').read_text():
        raise SystemExit('Only a generated localhost configuration is accepted.')
    compose('config', '--quiet')
    volumes = subprocess.run(
        ['docker', 'volume', 'ls', '--quiet', '--filter',
         'label=com.docker.compose.project=' + os.environ['COMPOSE_PROJECT_NAME']],
        check=True, capture_output=True, text=True).stdout.strip()
    if volumes or compose('ps', '--all', '--quiet', capture=True).stdout.strip():
        raise SystemExit('The smoke project must be empty; existing containers/volumes were left untouched.')
    compose('build', 'web')
    compose('up', '-d', '--wait', '--wait-timeout', '300')
    # First boot may still be issuing its internal CA certificate.
    for attempt in range(30):
        try:
            with request('/health/') as response:
                assert response.status == 200
            break
        except (OSError, urllib.error.URLError):
            if attempt == 29:
                raise
            time.sleep(2)
    for language, direction in [('ar', 'rtl'), ('en', 'ltr')]:
        with request('/?lang=' + language) as response:
            html = response.read().decode()
            assert f'dir="{direction}"' in html, language
            assert response.headers.get('Strict-Transport-Security'), 'Django must see HTTPS'
    # urllib normally follows redirects, so inspect the HTTP response directly.
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *_args, **_kwargs):
            return None
    try:
        urllib.request.build_opener(NoRedirect()).open('http://localhost/health/', timeout=10)
    except urllib.error.HTTPError as error:
        assert error.code in (301, 308)
        assert error.headers['Location'] == 'https://localhost/health/'
    else:
        raise AssertionError('HTTP must redirect to HTTPS.')
    with request('/static/favicon.svg') as response:
        assert response.status == 200 and b'<svg' in response.read()
        assert response.headers['X-Content-Type-Options'] == 'nosniff'
    image = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII=')
    compose('exec', '-T', 'web', 'python', '-c',
            'from pathlib import Path; import base64; '
            f'Path("/app/media/docker-smoke.png").write_bytes(base64.b64decode({base64.b64encode(image).decode()!r})); '
            'Path("/app/media/docker-smoke.html").write_text("unsafe"); '
            'assert not Path("/app/.env").exists(); assert not Path("/app/db.sqlite3").exists()')
    with request('/media/docker-smoke.png') as response:
        assert response.read() == image
    try:
        request('/media/docker-smoke.html')
    except urllib.error.HTTPError as error:
        assert error.code == 404
    else:
        raise AssertionError('Uploaded HTML must not be served.')
    # Test actual credential hash, trusted client-IP handling and persistence across recreation.
    before = django('from django.contrib.auth.models import User; from pathlib import Path; '
                    'from django.test import RequestFactory; from core.security import client_address; '
                    'u=User.objects.get(username="smoke@example.com"); '
                    'assert u.check_password(Path("/run/secrets/admin_password").read_text().strip()); '
                    'assert client_address(RequestFactory().get("/", REMOTE_ADDR="", HTTP_X_REAL_IP="203.0.113.8")) == "203.0.113.8"; '
                    'print("HASH=" + u.password)').split('HASH=')[-1].strip()
    compose('up', '-d', '--force-recreate', '--wait', '--wait-timeout', '300', 'web', 'scheduler', 'proxy')
    after = django('from django.contrib.auth.models import User; print("HASH=" + User.objects.get(username="smoke@example.com").password)').split('HASH=')[-1].strip()
    assert before == after, 'Startup must not reset credentials'
    with request('/media/docker-smoke.png') as response:
        assert response.read() == image
    compose('stop', 'web', 'scheduler')
    compose('run', '--rm', '--no-deps', 'web', 'python', 'manage.py', 'backup_application', '--output', '/backups/smoke.zip')
    compose('run', '--rm', '--no-deps', 'web', 'python', 'manage.py', 'restore_application_backup',
            '--source', '/backups/smoke.zip', '--destination', '/backups/verified')
    # pg_restore into a separate empty database, never the live database.
    compose('exec', '-T', 'db', 'createdb', '-U', 'sofra', 'smoke_restore')
    compose('run', '--rm', '--no-deps', 'web', 'python', '-c',
            'from config.container_env import file_secret; import os, subprocess; '
            'subprocess.run(["pg_restore", "--exit-on-error", "--no-owner", "--no-acl", "-h", "db", "-U", "sofra", "-d", "smoke_restore", "/backups/verified/database/database.dump"], '
            'check=True, env=dict(os.environ, PGPASSWORD=file_secret(os.environ,"POSTGRES_PASSWORD")))')
    compose('run', '--rm', '--no-deps', 'web', 'python', '-c',
            'import json, os, psycopg; from pathlib import Path; from config.container_env import file_secret; '
            'from psycopg import sql; '
            'manifest=json.loads(Path("/backups/verified/manifest.json").read_text()); '
            'db=psycopg.connect(host="db", user="sofra", dbname="smoke_restore", password=file_secret(os.environ,"POSTGRES_PASSWORD")); '
            'counts={name: db.execute(sql.SQL("SELECT COUNT(*) FROM {}").format(sql.Identifier(name))).fetchone()[0] for name in manifest["tables"]}; '
            'assert counts == manifest["tables"]; '
            'assert Path("/backups/verified/media/docker-smoke.png").read_bytes() == Path("/app/media/docker-smoke.png").read_bytes()')
    result = compose('exec', '-T', 'db', 'psql', '-U', 'sofra', '-d', 'smoke_restore', '-Atc',
                     'SELECT count(*) FROM auth_user WHERE is_superuser = true', capture=True).stdout
    assert result.strip() == '1', 'Restored operator must be preserved'
    compose('up', '-d', '--wait', '--wait-timeout', '300')
    # Verify the privileged account can actually authenticate through the HTTPS proxy.
    # Cookie handling enforces the production secure-session and CSRF path.
    cookies = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPSHandler(context=CONTEXT),
                                       urllib.request.HTTPCookieProcessor(cookies))
    with opener.open('https://localhost/django-admin/login/') as response:
        token = re.search(r'name="csrfmiddlewaretoken" value="([^"]+)"', response.read().decode()).group(1)
    data = urllib.parse.urlencode({
        'username': 'smoke@example.com',
        'password': Path('.docker-secrets/admin_password').read_text().strip(),
        'csrfmiddlewaretoken': token, 'next': '/django-admin/',
    }).encode()
    login = urllib.request.Request('https://localhost/django-admin/login/', data=data,
                                   headers={'Referer': 'https://localhost/django-admin/login/'})
    with opener.open(login) as response:
        assert response.geturl() == 'https://localhost/django-admin/'
    assert any(cookie.name == 'sessionid' and cookie.secure for cookie in cookies)
    print('Docker acceptance passed: HTTPS, RTL/LTR, static/media, startup, persistence, backup and PostgreSQL restore.')


if __name__ == '__main__':
    main()
