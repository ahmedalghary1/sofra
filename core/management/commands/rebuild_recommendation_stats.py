from django.core.management.base import BaseCommand
from core.models import Branch
from core.recommendations import rebuild_stats


class Command(BaseCommand):
    help = 'Rebuild approved complementary-item pair statistics from completed orders in the last 90 days.'

    def add_arguments(self, parser):
        parser.add_argument('--branch', type=int, help='Limit the rebuild to one branch ID.')

    def handle(self, *args, **options):
        branches = Branch.objects.filter(active=True, archived=False)
        if options['branch'] is not None:
            branches = branches.filter(pk=options['branch'])
        total = sum(rebuild_stats(branch) for branch in branches.iterator())
        self.stdout.write(self.style.SUCCESS(f'Rebuilt {total} approved pair statistics.'))
