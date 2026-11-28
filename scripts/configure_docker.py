"""One-time configuration, using Python's standard library (also usable via docker run)."""
import argparse
import ipaddress
import os
from pathlib import Path
import re
import secrets


def normalized_domain(value, *, local=False):
    value = value.strip().rstrip('.').lower()
    if local and value == 'localhost':
        return value
    try:
        value = value.encode('idna').decode('ascii')
        ipaddress.ip_address(value)
    except ValueError:
        pass
    else:
        raise ValueError('Use a domain with DNS, not an IP address.')
    if (len(value) > 253 or '.' not in value or
            any(not re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?', label)
                for label in value.split('.'))):
        raise ValueError('Enter only a domain such as app.example.com, without https:// or a port.')
    return value


def normalized_project(value):
    if len(value) > 40 or not re.fullmatch(r'[a-z][a-z0-9]*(?:-[a-z0-9]+)*', value):
        raise ValueError('Project name must be 1-40 lowercase letters/digits with single hyphens, starting with a letter.')
    return value


def gateway_route(domain, project):
    # Revalidate at this boundary: domain/project become Caddy configuration tokens.
    domain = normalized_domain(domain, local=domain == 'localhost')
    project = normalized_project(project)
    return (f'https://{domain} {{\n'
            f'\treverse_proxy {project}-proxy:8080 {{\n'
            '\t\theader_up X-Real-IP {remote_host}\n'
            '\t}\n}\n')


def configure(root, domain, email, *, local=False, project=None, shared_proxy=False,
              edge_ip='10.240.0.2', edge_network='serveza-edge'):
    domain = normalized_domain(domain, local=local)
    email = email.strip().lower()
    if len(email) > 150 or not re.fullmatch(r'[^\s@=]+@[^\s@=]+\.[^\s@=]+', email):
        raise ValueError('Enter a valid admin email (at most 150 characters).')
    if project is not None:
        normalized_project(project)
    if shared_proxy:
        if not project:
            raise ValueError('--shared-proxy requires a unique --project-name.')
        address = ipaddress.IPv4Address(edge_ip)
        if not any(address in ipaddress.IPv4Network(network)
                   for network in ('10.0.0.0/8', '172.16.0.0/12', '192.168.0.0/16')):
            raise ValueError('Use the private IPv4 address assigned to the shared gateway.')
        normalized_project(edge_network)
    root = Path(root)
    env_path = root / '.env.docker'
    secrets_dir = root / '.docker-secrets'
    compose_path = root / '.env.compose'
    route_path = root / '.gateway-route.caddy'
    if any(path.exists() for path in (env_path, secrets_dir, compose_path, route_path)):
        raise ValueError('Docker configuration already exists. Edit it manually; secrets were NOT reset.')
    secrets_dir.mkdir(mode=0o700)
    # Parent 0700 protects secrets on the host. Files must also be readable by UID 10001
    # inside Compose bind-mounted secrets (Compose does not remap file ownership).
    for name, value in {
        'django_secret_key': secrets.token_urlsafe(64),
        'db_password': secrets.token_urlsafe(48),
        'admin_password': secrets.token_urlsafe(32),
        'smtp_password': '',
    }.items():
        with (secrets_dir / name).open('x', encoding='utf-8', newline='\n') as handle:
            handle.write(value + '\n')
        (secrets_dir / name).chmod(0o444)
    text = (
        '# Generated once; secrets are in .docker-secrets/, never in this file.\n'
        f'SITE_URL=https://{domain}\nSITE_HOST={domain}\nBOOTSTRAP_ADMIN_EMAIL={email}\n'
        'WEB_WORKERS=3\nDJANGO_EMAIL_BACKEND=smtp\n'
        '# Configure SMTP here and put its password in .docker-secrets/smtp_password.\n'
        'DJANGO_EMAIL_HOST=smtp.example.com\nDJANGO_EMAIL_PORT=587\n'
        'DJANGO_EMAIL_USE_TLS=1\nDJANGO_EMAIL_HOST_USER=\n'
        f'DJANGO_DEFAULT_FROM_EMAIL=Serveza <no-reply@{domain}>\n'
    )
    with env_path.open('x', encoding='utf-8', newline='\n') as handle:
        handle.write(text)
    env_path.chmod(0o600)
    if project:
        files = 'docker-compose.yml' + (':docker-compose.multi.yml' if shared_proxy else '')
        compose_text = (f'COMPOSE_PROJECT_NAME={project}\nCOMPOSE_PATH_SEPARATOR=:\n'
                        f'COMPOSE_FILE={files}\n')
        if shared_proxy:
            compose_text += f'EDGE_PROXY_IP={edge_ip}\nEDGE_NETWORK={edge_network}\n'
        with compose_path.open('x', encoding='utf-8', newline='\n') as handle:
            handle.write(compose_text)
        compose_path.chmod(0o600)
    if shared_proxy:
        with route_path.open('x', encoding='utf-8', newline='\n') as handle:
            handle.write(gateway_route(domain, project))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--domain', required=True)
    parser.add_argument('--admin-email', required=True)
    parser.add_argument('--local', action='store_true', help='Allow localhost for local HTTPS smoke tests only.')
    parser.add_argument('--directory', type=Path, default=Path.cwd())
    parser.add_argument('--project-name', dest='project', help='Permanent unique name for this site (e.g. site-one).')
    parser.add_argument('--shared-proxy', action='store_true', help='Use the shared multi-site HTTPS gateway.')
    parser.add_argument('--edge-ip', default='10.240.0.2')
    parser.add_argument('--edge-network', default='serveza-edge')
    options = parser.parse_args()
    # Newly created files start private, including the window before chmod.
    os.umask(0o077)
    try:
        configure(options.directory, options.domain, options.admin_email, local=options.local,
                  project=options.project, shared_proxy=options.shared_proxy,
                  edge_ip=options.edge_ip, edge_network=options.edge_network)
    except (ValueError, OSError) as error:
        parser.exit(1, f'Configuration stopped: {error}\n')
    print('Created .env.docker and private .docker-secrets/. No credentials printed.')
    command = 'docker compose' + (' --env-file .env.compose' if options.project else '')
    print(f'Configure SMTP, then run: {command} up -d --build --wait --wait-timeout 300')
    if options.shared_proxy:
        print('Install .gateway-route.caddy in the shared gateway sites directory, then validate/reload it.')
    print('Initial admin password is stored in .docker-secrets/admin_password.')


if __name__ == '__main__':
    main()
