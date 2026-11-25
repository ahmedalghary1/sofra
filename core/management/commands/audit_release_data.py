"""Read-only data gate. It intentionally prints counts, never credentials or email addresses."""
from pathlib import Path
import json

from django.conf import settings
from django.contrib.auth.models import User
from django.core.management.base import BaseCommand, CommandError
from django.db.models import Count, F
from django.db.models.functions import Lower

from core.models import Membership, MenuItem, Order, Plan, StaffInvitation


class Command(BaseCommand):
    help = 'Read-only audit of pre-existing data and media before deployment. Does not repair or delete anything.'

    def handle(self, *args, **options):
        counts = {
            'negative_plan_prices': Plan.objects.filter(price_monthly__lt=0).count(),
            'duplicate_nonempty_emails': User.objects.exclude(email='').annotate(normalized=Lower('email')).values('normalized').annotate(n=Count('pk')).filter(n__gt=1).count(),
            'order_table_branch_mismatch': Order.objects.filter(table__isnull=False).exclude(branch_id=F('table__branch_id')).count(),
            'item_station_branch_mismatch': MenuItem.objects.filter(station__isnull=False).exclude(category__branch_id=F('station__branch_id')).count(),
            'invitation_branch_mismatch': StaffInvitation.objects.exclude(restaurant_id=F('branch__restaurant_id')).count(),
            'membership_branch_mismatch': Membership.objects.filter(branches__isnull=False).exclude(restaurant_id=F('branches__restaurant_id')).distinct().count(),
        }
        media = Path(settings.MEDIA_ROOT)
        counts['unsafe_media_extensions'] = sum(path.is_file() and path.suffix.lower() not in ('.png', '.jpg', '.jpeg', '.webp')
                                              for path in media.rglob('*')) if media.exists() else 0
        self.stdout.write(json.dumps(counts, sort_keys=True))
        if any(counts.values()):
            raise CommandError('Review the reported legacy data before release. No records or files were changed.')
