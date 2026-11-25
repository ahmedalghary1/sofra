from django.core.management.base import BaseCommand
from core.backup import create_backup


class Command(BaseCommand):
    help = 'Back up the database and media with hashes. Pause writes first; protect this private artifact.'

    def add_arguments(self, parser):
        parser.add_argument('--output', required=True)

    def handle(self, *args, **options):
        manifest = create_backup(options['output'])
        self.stdout.write(self.style.SUCCESS(f"Backup created: {len(manifest['files'])} verified files. Move it to protected independent storage."))
