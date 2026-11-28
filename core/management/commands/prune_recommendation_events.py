from django.core.management.base import BaseCommand
from core.recommendations import prune_recommendation_events


class Command(BaseCommand):
    help = 'Aggregate and remove detailed recommendation exposures older than 90 days; keep order attribution.'

    def handle(self, *args, **options):
        total = prune_recommendation_events()
        self.stdout.write(self.style.SUCCESS(f'Aggregated and removed {total} old recommendation exposures.'))
