import io
import json
from pathlib import Path
import tempfile
import zipfile
from PIL import Image

from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import connection
from django.test import TransactionTestCase, override_settings

from .backup import create_backup, restore_backup
from .models import Restaurant


class BackupVerificationTests(TransactionTestCase):
    def test_sqlite_database_and_media_roundtrip_preserves_counts_and_bytes(self):
        if connection.vendor != 'sqlite':
            self.skipTest('SQLite restore verification; PostgreSQL dump restore is a staging gate.')
        Restaurant.objects.create(name='Backup verification', slug='backup-verification')
        with tempfile.TemporaryDirectory(prefix='serveza-restore-test-') as directory:
            root = Path(directory)
            media = root / 'media'
            media.mkdir()
            Image.new('RGB', (2, 2), 'white').save(media / 'sample.png', 'PNG')
            original_image = (media / 'sample.png').read_bytes()
            with override_settings(MEDIA_ROOT=media):
                manifest = create_backup(root / 'backup.zip')
                restored = restore_backup(root / 'backup.zip', root / 'restore')
            self.assertEqual(restored['tables'], manifest['tables'])
            self.assertEqual((root / 'restore/media/sample.png').read_bytes(), original_image)
            self.assertTrue(Restaurant.objects.filter(slug='backup-verification').exists())
            with self.assertRaises(CommandError):
                restore_backup(root / 'backup.zip', root / 'restore')

    def test_restore_rejects_traversal_before_extracting(self):
        with tempfile.TemporaryDirectory(prefix='serveza-unsafe-test-') as directory:
            root = Path(directory)
            with zipfile.ZipFile(root / 'unsafe.zip', 'w') as archive:
                archive.writestr('../outside.txt', 'do not extract')
                archive.writestr('manifest.json', json.dumps({'format': 1, 'files': {}}))
            with self.assertRaises(CommandError):
                restore_backup(root / 'unsafe.zip', root / 'restore')
            self.assertFalse((root / 'outside.txt').exists())
            self.assertFalse((root / 'restore').exists())

    def test_data_audit_does_not_print_personal_data_or_mutate(self):
        with tempfile.TemporaryDirectory(prefix='serveza-data-audit-') as media, override_settings(MEDIA_ROOT=media):
            out = io.StringIO()
            call_command('audit_release_data', stdout=out)
            data = json.loads(out.getvalue())
            self.assertFalse(any(data.values()))
            self.assertNotIn('@', out.getvalue())
