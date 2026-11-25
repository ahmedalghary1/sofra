"""Offline backup artifacts and verification; restore never overwrites the active DB."""
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import sqlite3
import stat
import subprocess
import tempfile
import zipfile
from contextlib import closing

from django.conf import settings
from django.core.management.base import CommandError
from django.db import connection
from django.utils import timezone


def digest_file(path):
    value = hashlib.sha256()
    with Path(path).open('rb') as source:
        for block in iter(lambda: source.read(1024*1024), b''):
            value.update(block)
    return value.hexdigest()


def database_counts():
    with connection.cursor() as cursor:
        result = {}
        for name in connection.introspection.table_names(cursor):
            cursor.execute('SELECT COUNT(*) FROM ' + connection.ops.quote_name(name))
            result[name] = cursor.fetchone()[0]
        return result


def create_backup(output):
    if connection.in_atomic_block:
        raise CommandError('Run the backup outside application transactions, after pausing writes.')
    output = Path(output).resolve()
    media = Path(settings.MEDIA_ROOT).resolve()
    if output == media or media in output.parents or output.exists():
        raise CommandError('Choose a new backup file outside MEDIA_ROOT. Existing files are never overwritten.')
    output.parent.mkdir(parents=True, exist_ok=True)
    manifest = {'format': 1, 'created_at': timezone.now().isoformat(), 'vendor': connection.vendor,
                'tables': database_counts(), 'files': {}}
    with tempfile.TemporaryDirectory(prefix='serveza-backup-') as stage:
        stage = Path(stage)
        if connection.vendor == 'sqlite':
            dump = stage / 'database.sqlite3'
            connection.ensure_connection()
            with closing(sqlite3.connect(dump)) as target:
                connection.connection.backup(target)
        elif connection.vendor == 'postgresql':
            if not shutil.which('pg_dump'):
                raise CommandError('Install PostgreSQL client tools (pg_dump) before backing up PostgreSQL.')
            dump = stage / 'database.dump'
            db = connection.settings_dict
            env = dict(os.environ, PGPASSWORD=db.get('PASSWORD') or '')
            for key, value in db.get('OPTIONS', {}).items():
                mapping = {'sslmode': 'PGSSLMODE', 'sslrootcert': 'PGSSLROOTCERT', 'sslcert': 'PGSSLCERT',
                           'sslkey': 'PGSSLKEY', 'connect_timeout': 'PGCONNECT_TIMEOUT', 'options': 'PGOPTIONS'}
                if key in mapping:
                    env[mapping[key]] = str(value)
            command = ['pg_dump', '--format=custom', '--no-owner', '--no-acl', '--host', str(db['HOST']),
                       '--port', str(db.get('PORT') or 5432), '--username', db['USER'], '--dbname', db['NAME'],
                       '--file', str(dump)]
            result = subprocess.run(command, env=env, capture_output=True)
            if result.returncode:
                raise CommandError('pg_dump failed. Check database access and client/server compatibility; credentials were not logged.')
        else:
            raise CommandError('Only SQLite and PostgreSQL backups are supported.')
        entries = [('database/' + dump.name, dump)]
        if media.exists():
            for path in sorted(media.rglob('*')):
                if path.is_symlink():
                    raise CommandError('Media contains a symbolic link. Review it before creating a portable backup.')
                if path.is_file():
                    entries.append(('media/' + path.relative_to(media).as_posix(), path))
        # Exclusive creation prevents silently replacing a concurrently created artifact.
        with zipfile.ZipFile(output, 'x', compression=zipfile.ZIP_DEFLATED) as archive:
            for name, path in entries:
                manifest['files'][name] = {'sha256': digest_file(path), 'bytes': path.stat().st_size}
                archive.write(path, name)
            archive.writestr('manifest.json', json.dumps(manifest, ensure_ascii=False, indent=2))
    return manifest


def restore_backup(source, destination):
    destination = Path(destination).resolve()
    if destination.exists() and (not destination.is_dir() or any(destination.iterdir())):
        raise CommandError('Restore requires a new or empty directory. The active database is never replaced.')
    with zipfile.ZipFile(source) as archive:
        infos = archive.infolist()
        names = [info.filename for info in infos]
        if len(names) != len(set(names)) or 'manifest.json' not in names:
            raise CommandError('Invalid or duplicate archive entries.')
        if sum(info.file_size for info in infos) > 20*1024**3:
            raise CommandError('Archive exceeds the supported restore size; review it separately.')
        for info in infos:
            parts = PurePosixPath(info.filename)
            if (parts.is_absolute() or '..' in parts.parts or '\\' in info.filename or ':' in info.filename
                    or stat.S_ISLNK(info.external_attr >> 16)):
                raise CommandError('Unsafe archive path or symbolic link.')
        if archive.getinfo('manifest.json').file_size > 10_000_000:
            raise CommandError('Invalid backup manifest size.')
        manifest = json.loads(archive.read('manifest.json'))
        if manifest.get('format') != 1 or set(manifest.get('files', {})) | {'manifest.json'} != set(names):
            raise CommandError('Backup manifest and file list do not match.')
        destination.mkdir(parents=True, exist_ok=True)
        for name, expected in manifest['files'].items():
            target = destination / name
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(name) as incoming, target.open('xb') as outgoing:
                shutil.copyfileobj(incoming, outgoing, 1024*1024)
            if target.stat().st_size != expected['bytes'] or digest_file(target) != expected['sha256']:
                raise CommandError('Backup integrity check failed; do not use the extracted data.')
        if manifest['vendor'] == 'sqlite':
            database = destination / 'database/database.sqlite3'
            with closing(sqlite3.connect(f'{database.as_uri()}?mode=ro', uri=True)) as db:
                if db.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                    raise CommandError('Restored SQLite integrity check failed.')
                for table, count in manifest['tables'].items():
                    quoted = '"' + table.replace('"', '""') + '"'
                    if db.execute('SELECT COUNT(*) FROM ' + quoted).fetchone()[0] != count:
                        raise CommandError('Restored row counts differ from the backup manifest. Pause writes when taking backups.')
        (destination / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
    return manifest
