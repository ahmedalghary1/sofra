from .validation import localized
from django.utils.translation import gettext

"""Server-side plan limits. Call inside transaction.atomic() before resource creation.

The subscription row lock serializes competing creations on PostgreSQL.
"""
from django.core.exceptions import ValidationError
from django.utils import timezone

from .models import KitchenStation, MenuItem, StaffInvitation, Subscription


def check_creation_limit(restaurant, resource, branch=None, *, invitation=None):
    subscription = Subscription.objects.select_for_update(of=('self',)).select_related('plan').filter(restaurant=restaurant).first()
    if not subscription or not subscription.valid():
        raise ValidationError('انتهت فترة التجربة أو الاشتراك. لا يمكن إضافة موارد جديدة.')
    if branch is not None:
        branch.refresh_from_db(fields=['archived', 'active'])
    if branch is not None and branch.archived:
        raise ValidationError(localized('استعد الفرع من الأرشيف قبل إضافة الموارد.', 'Restore the branch before adding resources.'))
    if resource == 'branch':
        count = restaurant.branches.filter(archived=False).count()
        limit = subscription.limit('max_branches')
        label = 'الفروع'
    elif resource == 'table':
        if branch is None or branch.restaurant_id != restaurant.pk:
            raise ValidationError('الفرع غير صالح.')
        count = branch.tables.filter(archived=False).count()
        limit = subscription.limit('max_tables', branch)
        label = 'الطاولات'
    elif resource == 'item':
        if branch is None or branch.restaurant_id != restaurant.pk:
            raise ValidationError('الفرع غير صالح.')
        count = MenuItem.objects.filter(category__branch=branch, archived=False).count()
        limit = subscription.limit('max_items', branch)
        label = 'الأصناف'
    elif resource == 'station':
        if branch is None or branch.restaurant_id != restaurant.pk:
            raise ValidationError('الفرع غير صالح.')
        count = KitchenStation.objects.filter(branch=branch, archived=False).count()
        limit = subscription.limit('max_stations', branch)
        label = 'محطات المطبخ'
    elif resource == 'staff':
        reserved = StaffInvitation.objects.filter(restaurant=restaurant, accepted_at__isnull=True,
            revoked_at__isnull=True, expires_at__gt=timezone.now())
        if invitation is not None:
            reserved = reserved.exclude(pk=invitation.pk)
        count = restaurant.memberships.filter(archived=False).count() + reserved.count()
        limit = subscription.limit('max_staff')
        label = 'أعضاء الفريق'
    else:
        raise ValueError(f'Unknown resource: {resource}')
    if count >= limit:
        raise ValidationError(gettext('وصلت إلى حد {label} في باقتك ({limit}).').format(label=gettext(label), limit=limit))
    return subscription

def check_activation_limit(restaurant, resource, branch=None):
    subscription = Subscription.objects.select_for_update(of=('self',)).select_related('plan').filter(restaurant=restaurant).first()
    if not subscription or not subscription.valid():
        raise ValidationError('انتهت فترة التجربة أو الاشتراك. لا يمكن تفعيل موارد جديدة.')
    if resource == 'branch':
        count, limit, label = restaurant.branches.filter(active=True).count(), subscription.limit('max_branches'), 'الفروع'
    elif resource == 'table' and branch and branch.restaurant_id == restaurant.pk:
        count, limit, label = branch.tables.filter(active=True).count(), subscription.limit('max_tables', branch), 'الطاولات'
    elif resource == 'item' and branch and branch.restaurant_id == restaurant.pk:
        count = MenuItem.objects.filter(category__branch=branch, available=True).count()
        limit, label = subscription.limit('max_items', branch), 'الأصناف'
    elif resource == 'staff':
        pending = StaffInvitation.objects.filter(restaurant=restaurant, accepted_at__isnull=True,
            revoked_at__isnull=True, expires_at__gt=timezone.now()).count()
        count, limit, label = restaurant.memberships.filter(active=True, archived=False).count() + pending, subscription.limit('max_staff'), 'أعضاء الفريق'
    else:
        raise ValidationError('المورد أو الفرع غير صالح.')
    if count >= limit:
        raise ValidationError(gettext('لا يمكن التفعيل: حد {label} في باقتك هو {limit}.').format(label=gettext(label), limit=limit))
    return subscription
