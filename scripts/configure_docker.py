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


def configure(root, domain, email, *, local=False):
    domain = normalized_domain(domain, local=local)
    email = email.strip().lower()
    if len(email) > 150 or not re.fullmatch(r'[^\s@=]+@[^\s@=]+\.[^\s@=]+', email):
        raise ValueError('Enter a valid admin email (at most 150 characters).')
    root = Path(root)
    env_path = root / '.env.docker'
    secrets_dir = root / '.docker-secrets'
    if env_path.exists() or secrets_dir.exists():
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


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--domain', required=True)
    parser.add_argument('--admin-email', required=True)
    parser.add_argument('--local', action='store_true', help='Allow localhost for local HTTPS smoke tests only.')
    parser.add_argument('--directory', type=Path, default=Path.cwd())
    options = parser.parse_args()
    # Newly created files start private, including the window before chmod.
    os.umask(0o077)
    try:
        configure(options.directory, options.domain, options.admin_email, local=options.local)
    except (ValueError, OSError) as error:
        parser.exit(1, f'Configuration stopped: {error}\n')
    print('Created .env.docker and private .docker-secrets/. No credentials printed.')
    print('Configure SMTP, then run: docker compose up -d --build --wait --wait-timeout 300')
    print('Initial admin password is stored in .docker-secrets/admin_password.')


if __name__ == '__main__':
    main()
