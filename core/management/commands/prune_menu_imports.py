from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from core.models import MenuImportBatch


class Command(BaseCommand):
    help = 'Remove expired menu import previews and old committed batch payloads.'

    def handle(self, *args, **options):
        deleted, _ = MenuImportBatch.objects.filter(
            created_at__lt=timezone.now() - timedelta(days=1)).delete()
        self.stdout.write(f'Removed {deleted} old menu import batches.')
