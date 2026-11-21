from django.db import migrations, models
from django.utils import timezone


def update_public_plans(apps, schema_editor):
    db = schema_editor.connection.alias
    plans = apps.get_model('core', 'Plan').objects.using(db)
    subscriptions = apps.get_model('core', 'Subscription').objects.using(db)
    branches = apps.get_model('core', 'Branch').objects.using(db)
    tables = apps.get_model('core', 'DiningTable').objects.using(db)
    items = apps.get_model('core', 'MenuItem').objects.using(db)
    stations = apps.get_model('core', 'KitchenStation').objects.using(db)
    memberships = apps.get_model('core', 'Membership').objects.using(db)
    invitations = apps.get_model('core', 'StaffInvitation').objects.using(db)
    now = timezone.now()
    fields = ('price_monthly', 'max_branches', 'max_tables', 'max_items', 'max_staff', 'max_stations')
    for name, aliases, values, staffing in (
        ('Starter', ('ستارتر', 'البداية'), (600, 1, 20, 100, 4, 1), (1, 2, 1, 0)),
        ('Growth', ('النمو',), (1300, 1, 60, 300, 12, 3), (2, 5, 3, 2)),
        ('Business', ('الأعمال',), (2500, 3, 120, 800, 40, 6), (5, 20, 10, 5)),
    ):
        terms = dict(zip(fields, values))
        terms['staff_example'] = dict(zip(('admin', 'waiter', 'kitchen', 'cashier'), staffing))
        matches = plans.filter(models.Q(name__iexact=name) | models.Q(name__in=aliases))
        if not matches.exists():
            plans.create(name=name, **terms)
            continue
        for subscription in subscriptions.filter(plan__in=matches).iterator():
            restaurant_id = subscription.restaurant_id
            branch_ids = list(branches.filter(restaurant_id=restaurant_id).values_list('pk', flat=True))
            seats = memberships.filter(restaurant_id=restaurant_id).count()
            seats += invitations.filter(restaurant_id=restaurant_id, expires_at__gt=now,
                                        accepted_at__isnull=True, revoked_at__isnull=True).count()
            allowances = {
                key: count for key, count in (('max_branches', len(branch_ids)), ('max_staff', seats))
                if count > terms[key]
            }
            branch_allowances = {}
            for branch_id in branch_ids:
                counts = {
                    'max_tables': tables.filter(branch_id=branch_id).count(),
                    'max_items': items.filter(category__branch_id=branch_id).count(),
                    'max_stations': stations.filter(branch_id=branch_id).count(),
                }
                excess = {key: count for key, count in counts.items() if count > terms[key]}
                if excess:
                    branch_allowances[str(branch_id)] = excess
            if branch_allowances:
                allowances['branches'] = branch_allowances
            if allowances:
                subscriptions.filter(pk=subscription.pk).update(grandfathered_limits=allowances)
        matches.update(**terms)


class Migration(migrations.Migration):
    dependencies = [('core', '0024_demo_request_status')]
    operations = [
        migrations.AddField(
            model_name='plan', name='staff_example',
            field=models.JSONField(blank=True, default=dict, editable=False),
        ),
        migrations.AddField(
            model_name='subscription', name='grandfathered_limits',
            field=models.JSONField(blank=True, default=dict, editable=False),
        ),
        migrations.RunPython(update_public_plans),
    ]
