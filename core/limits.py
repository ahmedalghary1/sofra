"""Server-side plan limits. Call inside transaction.atomic() before resource creation.

The subscription row lock serializes competing creations on PostgreSQL.
"""
from django.core.exceptions import ValidationError
from .models import MenuItem, Subscription

def check_creation_limit(restaurant, resource, branch=None):
    subscription = Subscription.objects.select_for_update().select_related('plan').get(restaurant=restaurant)
    if not subscription.valid():
        raise ValidationError('انتهت فترة التجربة أو الاشتراك. لا يمكن إضافة موارد جديدة.')
    if resource == 'branch':
        count = restaurant.branches.count()
        limit = subscription.plan.max_branches
        label = 'الفروع'
    elif resource == 'table':
        if branch is None or branch.restaurant_id != restaurant.pk:
            raise ValidationError('الفرع غير صالح.')
        count = branch.tables.count()
        limit = subscription.plan.max_tables
        label = 'الطاولات'
    elif resource == 'item':
        if branch is None or branch.restaurant_id != restaurant.pk:
            raise ValidationError('الفرع غير صالح.')
        count = MenuItem.objects.filter(category__branch=branch).count()
        limit = subscription.plan.max_items
        label = 'الأصناف'
    elif resource == 'staff':
        count = restaurant.memberships.count()
        limit = subscription.plan.max_staff
        label = 'أعضاء الفريق'
    else:
        raise ValueError(f'Unknown resource: {resource}')
    if count >= limit:
        raise ValidationError(f'وصلت إلى حد {label} في باقتك ({limit}).')
    return subscription
