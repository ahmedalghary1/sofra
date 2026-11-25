from django.core.management.base import BaseCommand
from core.backup import restore_backup


class Command(BaseCommand):
    help = 'Verify and extract to an empty directory. Never overwrite the configured database or media.'

    def add_arguments(self, parser):
        parser.add_argument('--source', required=True)
        parser.add_argument('--destination', required=True)

    def handle(self, *args, **options):
        manifest = restore_backup(options['source'], options['destination'])
        message = ('SQLite integrity and table counts verified.' if manifest['vendor'] == 'sqlite' else
                   'PostgreSQL dump and file hashes verified. Restore the dump with pg_restore into a NEW staging database, then compare table counts.')
        self.stdout.write(self.style.SUCCESS(message))
