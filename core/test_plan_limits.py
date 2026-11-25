from datetime import timedelta
from importlib import import_module
from types import SimpleNamespace

from django.apps import apps
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.db import connection, transaction
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from .limits import check_creation_limit
from .models import Branch, Category, DiningTable, KitchenStation, Membership, MenuItem, Plan, Restaurant, Subscription


class PublicPlanLimitTests(TestCase):
    def venue(self, plan_name, slug):
        plan = Plan.objects.get(name=plan_name)
        restaurant = Restaurant.objects.create(slug=slug, name=slug)
        subscription = Subscription.objects.create(
            restaurant=restaurant, plan=plan, status='active', ends_at=timezone.now() + timedelta(days=30),
        )
        owner = User.objects.create_user(username=f'{slug}-owner')
        Membership.objects.create(restaurant=restaurant, user=owner, role='owner')
        branch = Branch.objects.create(restaurant=restaurant)
        return restaurant, subscription, owner, branch

    def fill_branch(self, branch, table_count, item_count, station_count):
        DiningTable.objects.bulk_create([DiningTable(branch=branch, name=f'Table {i}') for i in range(table_count)])
        category = Category.objects.create(branch=branch, name='Menu')
        MenuItem.objects.bulk_create([MenuItem(category=category, name=f'Item {i}', price=1) for i in range(item_count)])
        KitchenStation.objects.bulk_create([
            KitchenStation(branch=branch, name=f'Station {i}') for i in range(station_count)
        ])

    def fill_seats(self, restaurant, count):
        users = User.objects.bulk_create([User(username=f'{restaurant.slug}-{i}') for i in range(count)])
        Membership.objects.bulk_create([Membership(restaurant=restaurant, user=user, role='waiter') for user in users])

    def assert_limit_reached(self, restaurant, branch):
        for resource in ('table', 'item', 'station', 'branch', 'staff'):
            with self.subTest(plan=restaurant.subscription.plan.name, resource=resource):
                with self.assertRaises(ValidationError), transaction.atomic():
                    check_creation_limit(restaurant, resource, branch)

    def test_new_plan_limits_are_enforced_for_every_resource(self):
        for plan_name, branches, tables, items, team, stations in (
            ('Starter', 1, 20, 100, 4, 1), ('Growth', 1, 60, 300, 8, 3), ('Business', 3, 120, 800, 15, 6),
        ):
            restaurant, _, owner, branch = self.venue(plan_name, plan_name.lower())
            Branch.objects.bulk_create([Branch(restaurant=restaurant, name=f'Extra {i}') for i in range(branches - 1)])
            self.fill_seats(restaurant, team - 1)
            self.fill_branch(branch, tables, items, stations)
            self.assert_limit_reached(restaurant, branch)
            self.client.force_login(owner)
            response = self.client.get(reverse('restaurant_settings', args=[restaurant.slug]))
            self.assertEqual(response.context['limits'], dict(branches=branches, tables=tables, items=items,
                                                              staff=team, stations=stations))
            menu_url = reverse('manage_menu', args=[restaurant.slug])
            self.client.post(menu_url, {'action': 'station_create', 'name': 'Extra station'})
            self.assertFalse(KitchenStation.objects.filter(branch=branch, name='Extra station').exists())

    def test_migration_preserves_existing_usage_by_subscription_and_branch(self):
        legacy, subscription, owner, branch = self.venue('Starter', 'legacy')
        other_branch = Branch.objects.create(restaurant=legacy, name='Other branch')
        fresh, fresh_subscription, _, fresh_branch = self.venue('Starter', 'fresh')
        self.fill_seats(legacy, 4)
        self.fill_branch(branch, 21, 101, 2)
        migration = import_module('core.migrations.0025_public_plan_limits')
        migration.update_public_plans(apps, SimpleNamespace(connection=connection))
        subscription.refresh_from_db()
        fresh_subscription.refresh_from_db()
        self.assertEqual(subscription.grandfathered_limits, {
            'max_staff': 5, 'max_branches': 2,
            'branches': {str(branch.pk): {'max_tables': 21, 'max_items': 101, 'max_stations': 2}},
        })
        self.assertEqual(fresh_subscription.grandfathered_limits, {})
        self.assertEqual(fresh_subscription.limit('max_items', fresh_branch), 100)
        self.assertEqual(subscription.limit('max_items', other_branch), 100)
        self.assertEqual(subscription.limit('max_items', branch), 101)
        self.assert_limit_reached(legacy, branch)

        self.client.force_login(owner)
        url = reverse('restaurant_settings', args=[legacy.slug])
        for lang in ('ar', 'en'):
            response = self.client.get(url, {'lang': lang, 'branch': branch.pk})
            self.assertTrue(response.context['grandfathered'])
            self.assertFalse(response.context['over_limit'])
        operator = User.objects.create_superuser(username='legacy-operator')
        self.client.force_login(operator)
        data = {'action': 'subscription', 'status': 'active', 'reason': 'Verified terms',
                'ends_at': (timezone.localdate() + timedelta(days=30)).isoformat()}
        endpoint = reverse('platform_update_restaurant', args=[legacy.pk])
        self.client.post(endpoint, {**data, 'plan_id': subscription.plan_id})
        self.assertTrue(Subscription.objects.get(pk=subscription.pk).grandfathered_limits)
        self.client.post(endpoint, {**data, 'plan_id': Plan.objects.get(name='Growth').pk})
        self.assertFalse(Subscription.objects.get(pk=subscription.pk).grandfathered_limits)

    def test_kitchen_plan_validation_reserves_a_seat_besides_owner(self):
        with self.assertRaises(ValidationError) as error:
            Plan(name='Invalid kitchen plan', max_staff=1, max_stations=1).full_clean()
        self.assertIn('max_staff', error.exception.message_dict)
