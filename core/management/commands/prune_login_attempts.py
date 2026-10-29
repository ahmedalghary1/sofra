from datetime import timedelta
from django.core.management.base import BaseCommand
from django.utils import timezone
from core.models import LoginAttempt

class Command(BaseCommand):
    help = 'Remove expired login rate-limit records.'

    def handle(self, *args, **options):
        deleted, _ = LoginAttempt.objects.filter(created_at__lt=timezone.now()-timedelta(days=1)).delete()
        self.stdout.write(f'Removed {deleted} expired login attempts.')
