"""Explicit archiving frees capacity while preserving historical relationships."""
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils.translation import get_language
from django.utils import timezone
import uuid

from .limits import check_creation_limit
from .models import Branch, DiningTable, KitchenStation, Membership, MenuItem, Order, Restaurant, ServiceCall, StaffInvitation, Subscription
from .services import audit


def fail(ar, en):
    raise ValidationError(en if get_language() == 'en' else ar)


def lock_resource(restaurant, model, pk):
    """Call inside atomic; all resource state changes share this lock order."""
    Subscription.objects.select_for_update().get(restaurant=restaurant)
    scope = ({'restaurant': restaurant} if model in (Branch, Membership) else
             {'category__branch__restaurant': restaurant} if model is MenuItem else
             {'branch__restaurant': restaurant})
    return model.objects.select_for_update(of=('self',)).get(pk=pk, **scope)


@transaction.atomic
def archive_resource(restaurant, actor, resource, pk, *, restore=False):
    models = {'branch': Branch, 'table': DiningTable, 'item': MenuItem, 'staff': Membership, 'station': KitchenStation}
    if resource not in models:
        fail('نوع المورد غير صالح.', 'Invalid resource type.')
    # Use the same lock order as resource creation: subscription, then resource.
    Subscription.objects.select_for_update().get(restaurant=restaurant)
    if resource == 'branch':
        # Match current intake: restaurant, branch, then tables. Do not lock
        # joined tenant rows implicitly when archiving another resource type.
        Restaurant.objects.select_for_update(of=('self',), no_key=True).get(pk=restaurant.pk)
        Branch.objects.select_for_update(of=('self',), no_key=True).filter(pk=pk, restaurant=restaurant).first()
        list(DiningTable.objects.select_for_update(of=('self',)).filter(branch_id=pk, branch__restaurant=restaurant).order_by('pk').values_list('pk', flat=True))
    query = models[resource].objects.select_for_update(of=('self',), no_key=resource == 'branch')
    scope = {'restaurant': restaurant} if resource in ('branch', 'staff') else (
        {'category__branch__restaurant': restaurant} if resource == 'item' else {'branch__restaurant': restaurant})
    obj = query.filter(pk=pk, **scope).first()
    if obj is None:
        fail('المورد غير متاح لهذا المطعم.', 'Resource unavailable for this restaurant.')
    membership = Membership.objects.filter(user=actor, restaurant=restaurant, active=True, archived=False,
        user__is_active=True, role__in=('owner','manager')).first()
    if membership is None or (resource == 'staff' and membership.role != 'owner' and obj.role in ('owner','manager')):
        raise PermissionDenied
    if obj.archived == (not restore):
        return obj
    branch = obj.category.branch if resource == 'item' else getattr(obj, 'branch', None)
    if resource == 'staff' and (obj.role == 'owner' or obj.user_id == actor.pk):
        fail('لا يمكن أرشفة المالك أو عضويتك الحالية.', 'The owner and your current membership cannot be archived.')
    if restore:
        check_creation_limit(restaurant, resource, branch)
        if branch and branch.archived:
            fail('استعد الفرع أولًا.', 'Restore the branch first.')
    else:
        orders = Order.objects.filter(branch=obj) if resource == 'branch' else (
            Order.objects.filter(table=obj) if resource == 'table' else Order.objects.none())
        if orders.exclude(status__in=('served', 'cancelled')).exists() or orders.filter(payment_status='unpaid').exclude(status='cancelled').exists():
            fail('أكمل الطلبات والتحصيل قبل الأرشفة.', 'Complete outstanding orders and collection before archiving.')
        if resource in ('branch', 'table'):
            calls = ServiceCall.objects.filter(table__branch=obj) if resource == 'branch' else ServiceCall.objects.filter(table=obj)
            if calls.exclude(status__in=('done', 'cancelled')).exists():
                fail('أغلق استدعاءات الخدمة أولًا.', 'Close service calls first.')
        if resource == 'station' and (obj.menuitem_set.filter(available=True, archived=False).exists()
                or obj.orderline_set.exclude(order__status__in=('served', 'cancelled')).exists()):
            fail('أعد توجيه الأصناف وأكمل التذاكر قبل أرشفة المحطة.', 'Reassign menu items and complete tickets before archiving this station.')
        if resource == 'branch':
            StaffInvitation.objects.filter(branch=obj, accepted_at__isnull=True, revoked_at__isnull=True).update(revoked_at=timezone.now())
    obj.archived = not restore
    flag = 'available' if resource == 'item' else 'active'
    # Restore reserves capacity, but activation remains an explicit existing action.
    setattr(obj, flag, restore if resource == 'station' else False)
    fields = ['archived', flag]
    if not restore and resource == 'table':
        obj.session_token = uuid.uuid4()
        fields.append('session_token')
    if resource == 'branch':
        obj.accepting_orders = False
        fields.append('accepting_orders')
        if not restore:
            for table in obj.tables.all():
                table.session_token = uuid.uuid4()
                table.save(update_fields=['session_token'])
    obj.save(update_fields=fields)
    audit(restaurant, actor, f'{resource}.{"restore" if restore else "archive"}', str(obj.pk),
          changes={'model': obj._meta.label_lower, 'id': obj.pk, 'archived': obj.archived})
    return obj
