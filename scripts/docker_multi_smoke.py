"""Build two isolated sites behind one HTTPS gateway on an empty Linux Docker host.

Only freshly generated smoke projects are removed. Source files and existing
deployments are never modified. Test HTTPS uses a private CA; public TLS still
requires verification on the target server.
"""
import http.client
import json
import os
from pathlib import Path
import shutil
import socket
import ssl
import subprocess
import sys
import tempfile
import time
import uuid

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
from scripts.configure_docker import configure

CONTEXT = ssl._create_unverified_context()


class LocalHTTPS(http.client.HTTPSConnection):
    def connect(self):
        self.sock = CONTEXT.wrap_socket(socket.create_connection(('127.0.0.1', 443), timeout=15),
                                       server_hostname=self.host)


def request(domain, path, headers=None):
    connection = LocalHTTPS(domain, timeout=15)
    try:
        connection.request('GET', path, headers=headers or {})
        response = connection.getresponse()
        return response.status, dict(response.getheaders()), response.read()
    finally:
        connection.close()


def wait_healthy(domain):
    for _ in range(30):
        try:
            if request(domain, '/health/')[0] == 200:
                return
        except (OSError, http.client.HTTPException):
            pass
        time.sleep(1)
    raise AssertionError(f'HTTPS health probe failed for {domain}.')


def main():
    suffix = uuid.uuid4().hex[:8]
    names = [f'serveza-smoke-one-{suffix}', f'serveza-smoke-two-{suffix}']
    gateway_name = f'serveza-smoke-edge-{suffix}'
    env = {key: value for key, value in os.environ.items()
           if not key.startswith('COMPOSE_') and key not in ('EDGE_NETWORK', 'EDGE_PROXY_IP', 'EDGE_SUBNET')}
    env['EDGE_NETWORK'] = gateway_name
    (REPO / 'tmp').mkdir(exist_ok=True)
    root = Path(tempfile.mkdtemp(prefix='docker-multi-', dir=REPO / 'tmp'))
    gateway = root / 'gateway'
    shutil.copytree(REPO / 'docker/gateway', gateway)
    # Private CA and a diagnostic view are confined to these staged test images.
    path = gateway / 'Caddyfile'
    path.write_text(path.read_text().replace('{\n', '{\n\tlocal_certs\n\tskip_install_trust\n', 1))
    sites = []
    for index, name in enumerate(names):
        site = root / name
        site.mkdir()
        for item in ('config', 'core', 'templates', 'static', 'docker', 'scripts'):
            shutil.copytree(REPO / item, site / item, ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
        for item in ('manage.py', 'requirements.txt', 'Dockerfile', '.dockerignore',
                     'docker-compose.yml', 'docker-compose.multi.yml'):
            shutil.copyfile(REPO / item, site / item)
        with (site / 'config/urls.py').open('a') as file:
            file.write('\nfrom django.http import JsonResponse\nfrom core.security import client_address\n'
                       'urlpatterns += [path("_docker_probe/", lambda request: JsonResponse('
                       '{"client": client_address(request), "secure": request.is_secure()}))]\n')
        domain = f'site-{index}.example.test'
        configure(site, domain, 'smoke@example.com', project=name, shared_proxy=True, edge_network=gateway_name)
        shutil.copyfile(site / '.gateway-route.caddy', gateway / 'sites' / (name + '.caddy'))
        sites.append((site, domain))

    def compose(directory, *args, capture=False, check=True):
        command = ['docker', 'compose']
        if directory == gateway:
            command += ['-p', gateway_name, '-f', 'compose.yml']
        else:
            command += ['--env-file', '.env.compose']
        return subprocess.run([*command, *args], cwd=directory, env=env, check=check,
                              text=True, capture_output=capture)

    try:
        compose(gateway, 'up', '-d', '--wait', '--wait-timeout', '120')
        for site, domain in sites:
            config = json.loads(compose(site, 'config', '--format', 'json', capture=True).stdout)
            assert not any(service.get('ports') for service in config['services'].values())
            compose(site, 'up', '-d', '--build', '--wait', '--wait-timeout', '300')
            compose(site, 'exec', '-T', 'web', 'python', '-c',
                    f'from pathlib import Path; Path("/app/media/shared.png").write_bytes({domain.encode()!r})')
            compose(site, 'exec', '-T', 'web', 'python', 'manage.py', 'shell', '-c',
                    f'from django.contrib.auth.models import User; User.objects.create_user(username={domain!r})')
            wait_healthy(domain)
            for language, direction in [('ar', 'rtl'), ('en', 'ltr')]:
                status, headers, body = request(domain, '/?lang=' + language)
                assert status == 200 and f'dir="{direction}"'.encode() in body
                assert 'Strict-Transport-Security' in headers
            assert request(domain, '/static/favicon.svg')[0] == 200
            assert request(domain, '/media/shared.png')[2] == domain.encode()
            status, _, body = request(domain, '/_docker_probe/', headers={
                'X-Real-IP': '203.0.113.99', 'X-Forwarded-For': '203.0.113.99', 'X-Forwarded-Proto': 'http'})
            probe = json.loads(body)
            assert status == 200 and probe['secure'] is True
            assert probe['client'] not in ('unknown', '203.0.113.99', '10.240.0.2')

        # An adjacent proxy cannot impersonate the gateway, even with forged headers.
        denied = compose(sites[0][0], 'exec', '-T', 'proxy', 'wget', '-S', '-O', '-',
                         '--header=Host: ' + sites[1][1], '--header=X-Real-IP: 203.0.113.99',
                         'http://' + names[1] + '-proxy:8080/media/shared.png', capture=True, check=False)
        assert denied.returncode != 0 and '403' in denied.stderr
        for index, (site, domain) in enumerate(sites):
            other = sites[1 - index][1]
            compose(site, 'exec', '-T', 'web', 'python', 'manage.py', 'shell', '-c',
                    'from django.contrib.auth.models import User; '
                    f'assert User.objects.filter(username={domain!r}).exists(); '
                    f'assert not User.objects.filter(username={other!r}).exists()')
        compose(sites[0][0], 'up', '-d', '--force-recreate', '--wait', '--wait-timeout', '300',
                'web', 'scheduler', 'proxy')
        for site, domain in sites:
            wait_healthy(domain)
            assert request(domain, '/media/shared.png')[2] == domain.encode()
        print('Two-site acceptance passed: HTTPS routing, RTL/LTR, private backends, client IP, isolated DB/media and independent restart.')
    except Exception:
        for directory in [gateway, *(site for site, _ in sites)]:
            compose(directory, 'logs', '--tail', '60', check=False)
        raise
    finally:
        # Names are generated here; never accept a user-supplied project for destructive cleanup.
        for site, _ in sites:
            compose(site, 'down', '-v', '--remove-orphans', check=False)
        compose(gateway, 'down', '-v', '--remove-orphans', check=False)


if __name__ == '__main__':
    main()
