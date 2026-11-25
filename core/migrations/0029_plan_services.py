from django.db import migrations, models
from django.utils import timezone


def divide_services(apps, schema_editor):
    db = schema_editor.connection.alias
    plans = apps.get_model('core', 'Plan').objects.using(db)
    subscriptions = apps.get_model('core', 'Subscription').objects.using(db)
    agreements = apps.get_model('core', 'SubscriptionAgreement').objects.using(db)
    features = ['online_ordering', 'menu_import', 'advanced_reports', 'multi_branch_reports']
    limit_fields = ('max_branches', 'max_tables', 'max_items', 'max_staff', 'max_stations')
    # Preserve current agreements and append a baseline; never rewrite historical records.
    for subscription in subscriptions.exclude(plan_id=None).select_related('plan').iterator():
        terms = dict(subscription.agreed_terms)
        if not terms:
            plan = subscription.plan
            terms = {'plan_id': plan.pk, 'plan_name': plan.name, 'currency': 'EGP',
                     'monthly_price': str(plan.price_monthly), 'baseline_only': True,
                     'limits': {key: getattr(plan, key) for key in limit_fields}}
        if 'features' not in terms:
            terms['features'] = features
            subscriptions.filter(pk=subscription.pk).update(agreed_terms=terms)
            agreements.create(subscription_id=subscription.pk, terms=terms, status=subscription.status,
                starts_on=timezone.localdate(), ends_at=subscription.ends_at,
                reason='Existing service access preserved before introducing public service tiers.')
    presets = (
        (('starter', 'ستارتر', 'البداية'), 4, 4, (1, 1, 1, 1), []),
        (('growth', 'النمو'), 12, 8, (1, 3, 2, 2), features[:3]),
        (('business', 'الأعمال'), 40, 15, (3, 6, 3, 3), features),
    )
    for plan in plans.all().iterator():
        for names, old_staff, new_staff, example, included in presets:
            if plan.name.casefold() not in names:
                continue
            updates = {'enabled_features': included}
            if plan.max_staff == old_staff:
                updates.update(max_staff=new_staff, staff_example=dict(zip(('admin', 'waiter', 'kitchen', 'cashier'), example)))
            plans.filter(pk=plan.pk).update(**updates)


class Migration(migrations.Migration):
    dependencies = [('core', '0028_online_ordering')]
    operations = [
        migrations.AddField(model_name='plan', name='enabled_features',
                            field=models.JSONField(blank=True, default=None, null=True)),
        migrations.RunPython(divide_services, migrations.RunPython.noop),
    ]
