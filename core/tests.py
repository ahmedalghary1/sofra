import json
import io
import csv
import os
import re
import subprocess
import sys
import uuid
from urllib.parse import urlparse
from decimal import Decimal
from openpyxl import Workbook
from datetime import timedelta
from django.conf import settings
from django.contrib.auth.models import User
from django.contrib.admin.models import LogEntry
from django.core import mail
from django.core.management import call_command
from django.core.management.base import CommandError
from django.core.exceptions import ValidationError
from django.db import DatabaseError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from unittest.mock import patch
from django.test import SimpleTestCase, TestCase
from django.test import Client
from django.urls import reverse
from django.utils import timezone
from .models import AuditLog, Branch, Category, DiningTable, KitchenStation, LoginAttempt, Membership, MenuImportBatch, MenuItem, MenuSize, ModifierGroup, ModifierOption, Order, OrderHistory, OrderLinePrepHistory, OrderRevision, OwnerEmailVerification, Plan, Restaurant, ServiceCall, StaffInvitation, Subscription, SubscriptionPayment
from .services import advance_call, advance_order, advance_order_line, correct_order_status, create_order, create_service_call, edit_order, record_manual_payment


class MarketingMenuTests(SimpleTestCase):
    def test_each_sample_dish_has_a_distinct_local_photo_in_both_languages(self):
        from django.contrib.staticfiles import finders
        from .demo_menu import sample_menu
        from .menu_photos import menu_photo
        _, arabic = sample_menu()
        _, english = sample_menu(english=True)
        photos = [item['photo'] for item in arabic]
        self.assertEqual(photos, [item['photo'] for item in english])
        self.assertEqual(len(set(photos)), len(arabic))
        for photo in photos:
            self.assertNotIn('place-setting', photo)
            self.assertTrue(finders.find(photo.split('/static/')[-1]))
        self.assertEqual(menu_photo('Latte', '/media/menu/my-latte.jpg'), '/media/menu/my-latte.jpg')
        self.assertTrue(menu_photo('Special of the day').endswith('/place-setting.webp'))

    def test_anonymous_preview_needs_no_database(self):
        url = reverse('demo_menu')
        preview = self.client.get(url)
        self.assertContains(preview, 'دار الليمون')
        self.assertContains(preview, 'شيش طاووق على الفحم')
        self.assertContains(preview, 'للعرض فقط')
        self.assertContains(preview, 'أضف للسلة')
        self.assertContains(preview, reverse('place_demo_order'))
        self.assertContains(preview, 'id="openCart"')
        self.assertEqual(self.client.head(url).status_code, 200)
        self.assertEqual(self.client.post(url).status_code, 405)

    def test_demo_checkout_recalculates_total_without_database(self):
        client = Client(enforce_csrf_checks=True)
        url = reverse('place_demo_order')
        lines = [{'item_id': 4, 'quantity': 2, 'price': '0.01'}, {'item_id': 10, 'quantity': 1}]
        self.assertEqual(client.get(url).status_code, 405)
        self.assertEqual(client.post(url, {'lines': lines}, content_type='application/json').status_code, 403)

        client.get(reverse('demo_menu'))
        response = client.post(
            url, {'lines': lines, 'total': '0.01'}, content_type='application/json',
            HTTP_X_CSRFTOKEN=client.cookies['csrftoken'].value,
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {'ok': True, 'total': '560.00'})

    def test_demo_checkout_rejects_invalid_carts(self):
        for lines in ([], [{'item_id': 99, 'quantity': 1}], [{'item_id': 4, 'quantity': 31}],
                      [{'item_id': 4, 'quantity': 1.5}], [{'item_id': 4, 'quantity': True}], [None]):
            with self.subTest(lines=lines):
                response = self.client.post(reverse('place_demo_order'), {'lines': lines},
                                            content_type='application/json')
                self.assertEqual(response.status_code, 400)
                self.assertFalse(response.json()['ok'])


class PlatformProvisioningTests(TestCase):
    def setUp(self):
        self.operator = User.objects.create_superuser(
            username='operator@example.com', email='operator@example.com', password='StrongPass!123')
        self.regular = User.objects.create_user(username='regular@example.com', password='StrongPass!123')
        self.plan = Plan.objects.create(name='إدارة فروع', max_branches=2)

    def restaurant_data(self, name='مطعم الشرق', email='east-owner@example.com', **changes):
        return {
            'name': name, 'description': 'مطعم تجريبي', 'branch_name': 'الفرع الرئيسي',
            'branch_address': 'القاهرة', 'owner_email': email, 'owner_name': 'مالك المطعم',
            'owner_password': 'StrongPass!123', 'plan': self.plan.pk,
            'ends_at': (timezone.localdate() + timedelta(days=30)).isoformat(), **changes,
        }

    def create_restaurant(self, **changes):
        self.client.force_login(self.operator)
        response = self.client.post(reverse('platform_create_restaurant'),
                                    self.restaurant_data(**changes))
        self.assertEqual(response.status_code, 302)
        return Restaurant.objects.get(name=changes.get('name', 'مطعم الشرق'))

    def test_operator_creates_multiple_restaurants_and_manages_branches(self):
        self.client.force_login(self.operator)
        self.assertContains(self.client.get(reverse('platform_dashboard')), 'إضافة مطعم')
        self.assertContains(self.client.get(reverse('platform_create_restaurant')), 'الفرع الأول')
        first = self.create_restaurant()
        second = self.create_restaurant(name='مطعم الغرب', email='west-owner@example.com')
        self.assertNotEqual(first.slug, second.slug)
        self.assertEqual(first.branches.count(), 1)
        self.assertEqual(second.branches.count(), 1)
        self.assertEqual(first.subscription.plan, self.plan)
        self.assertEqual(first.subscription.status, 'active')
        self.assertEqual(timezone.localdate(first.subscription.ends_at), timezone.localdate() + timedelta(days=30))
        owner = User.objects.get(username='east-owner@example.com')
        self.assertTrue(owner.check_password('StrongPass!123'))
        self.assertEqual(Membership.objects.get(user=owner, restaurant=first).role, 'owner')
        self.assertTrue(AuditLog.objects.filter(
            restaurant=first, actor=self.operator, action='platform.restaurant.create').exists())

        endpoint = reverse('platform_branch_change', args=[first.pk])
        self.client.post(endpoint, {'action': 'create', 'name': 'فرع مدينة نصر', 'address': 'مدينة نصر'})
        branch = first.branches.get(name='فرع مدينة نصر')
        self.assertEqual(branch.address, 'مدينة نصر')
        self.assertEqual(first.branches.count(), 2)
        self.assertEqual(second.branches.count(), 1)
        self.assertTrue(AuditLog.objects.filter(
            restaurant=first, actor=self.operator, action='platform.branch.create').exists())
        self.assertContains(self.client.get(reverse('platform_dashboard')), 'فرع مدينة نصر')
        self.client.post(endpoint, {'action': 'create', 'name': 'فرع ثالث'})
        self.assertEqual(first.branches.count(), 2)
        self.client.post(endpoint, {'action': 'toggle', 'branch_id': branch.pk})
        branch.refresh_from_db()
        self.assertTrue(branch.active)
        self.client.post(endpoint, {'action': 'toggle', 'branch_id': branch.pk,
                                    'reason': 'إغلاق مؤقت'})
        branch.refresh_from_db()
        self.assertFalse(branch.active)
        self.assertFalse(branch.accepting_orders)
        self.client.post(endpoint, {'action': 'toggle', 'branch_id': branch.pk,
                                    'reason': 'إعادة التشغيل'})
        branch.refresh_from_db()
        self.assertTrue(branch.active)
        self.assertEqual(self.client.post(reverse('platform_branch_change', args=[second.pk]), {
            'action': 'toggle', 'branch_id': branch.pk, 'reason': 'اختبار'}).status_code, 404)

        self.client.force_login(owner)
        self.assertEqual(self.client.get(reverse('dashboard', args=[first.slug])).status_code, 200)
        self.assertEqual(self.client.get(reverse('dashboard', args=[second.slug])).status_code, 403)
        self.assertEqual(self.client.post(endpoint, {'action': 'create', 'name': 'غير مصرح'}).status_code, 403)

    def test_existing_owner_can_switch_between_restaurants(self):
        first = self.create_restaurant()
        second = self.create_restaurant(name='مطعم ثانٍ', email='east-owner@example.com',
                                        owner_name='', owner_password='')
        owner = User.objects.get(username='east-owner@example.com')
        self.assertEqual(User.objects.filter(username=owner.username).count(), 1)
        self.assertEqual(owner.memberships.filter(role='owner').count(), 2)
        self.client.force_login(owner)
        page = self.client.get(reverse('dashboard', args=[first.slug]))
        self.assertContains(page, 'تغيير المطعم')
        self.assertContains(page, second.name)
        self.assertEqual(self.client.get(reverse('dashboard', args=[second.slug])).status_code, 200)

    def test_existing_owner_password_is_not_reset_and_invalid_branch_is_not_saved(self):
        first = self.create_restaurant()
        owner = User.objects.get(username='east-owner@example.com')
        original_password = owner.password
        response = self.client.post(reverse('platform_create_restaurant'), self.restaurant_data(
            name='مطعم مكرر', email=owner.username, owner_password='AnotherStrong!123'))
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Restaurant.objects.filter(name='مطعم مكرر').exists())
        owner.refresh_from_db()
        self.assertEqual(owner.password, original_password)

        self.plan.max_branches = 3
        self.plan.save(update_fields=['max_branches'])
        endpoint = reverse('platform_branch_change', args=[first.pk])
        self.client.post(endpoint, {'action': 'create', 'name': 'فرع جديد'})
        self.client.post(endpoint, {'action': 'create', 'name': 'فرع جديد'})
        self.assertEqual(first.branches.count(), 2)
        first.subscription.ends_at = timezone.now()-timedelta(days=1)
        first.subscription.save(update_fields=['ends_at'])
        self.client.post(endpoint, {'action': 'create', 'name': 'فرع آخر'})
        self.assertEqual(first.branches.count(), 2)

    def test_operator_creation_requires_permission_and_valid_owner(self):
        endpoint = reverse('platform_create_restaurant')
        self.assertEqual(self.client.get(endpoint).status_code, 302)
        self.client.force_login(self.regular)
        self.assertEqual(self.client.get(endpoint).status_code, 403)
        self.assertEqual(self.client.post(endpoint, self.restaurant_data()).status_code, 403)
        self.assertFalse(Restaurant.objects.exists())
        self.client.force_login(self.operator)
        for changes in ({'owner_password': 'short'}, {'owner_email': 'not-an-email'},
                        {'plan': 999999}, {'branch_name': ''}, {'ends_at': ''},
                        {'ends_at': (timezone.localdate() - timedelta(days=1)).isoformat()}):
            with self.subTest(changes=changes):
                response = self.client.post(endpoint, self.restaurant_data(**changes))
                self.assertEqual(response.status_code, 200)
                self.assertFalse(Restaurant.objects.exists())
                self.assertFalse(User.objects.filter(username='east-owner@example.com').exists())

    def test_operator_can_leave_a_new_restaurant_pending(self):
        self.client.force_login(self.operator)
        response = self.client.post(reverse('platform_create_restaurant'),
                                    self.restaurant_data(plan='', ends_at=''))
        self.assertEqual(response.status_code, 302)
        restaurant = Restaurant.objects.get(name='مطعم الشرق')
        self.assertEqual(restaurant.subscription.status, 'pending')
        self.assertIsNone(restaurant.subscription.plan_id)
        self.assertIsNone(restaurant.subscription.ends_at)
        self.assertEqual(restaurant.branches.count(), 1)

    def test_operator_creation_form_keeps_csrf_protection(self):
        client = Client(enforce_csrf_checks=True, HTTP_HOST='localhost:8000')
        client.force_login(self.operator)
        endpoint = reverse('platform_create_restaurant')
        self.assertEqual(client.post(endpoint, self.restaurant_data()).status_code, 403)
        self.assertFalse(Restaurant.objects.exists())
        page = client.get(endpoint)
        self.assertEqual(page.status_code, 200)
        token = client.cookies[settings.CSRF_COOKIE_NAME].value
        response = client.post(endpoint, {
            **self.restaurant_data(), 'csrfmiddlewaretoken': token,
        }, HTTP_ORIGIN='http://localhost:8000')
        self.assertEqual(response.status_code, 302)
        self.assertEqual(Restaurant.objects.count(), 1)


@override_settings(DEBUG=True)
class DemoSeedTests(TestCase):
    def seed(self):
        output = io.StringIO()
        call_command('seed_demo', stdout=output)
        return output.getvalue()

    def test_seed_is_repeatable_and_guest_order_reaches_kitchen(self):
        output = self.seed()
        restaurant = Restaurant.objects.get(slug='demo-sofra')
        self.assertIn('demo-owner@example.com', output)
        self.assertIn('demo-kitchen@example.com', output)
        self.assertEqual(restaurant.branches.count(), 2)
        main = restaurant.branches.get(name='الفرع الرئيسي')
        self.assertEqual(main.stations.count(), 2)
        self.assertEqual(main.tables.count(), 3)
        self.assertEqual(MenuItem.objects.filter(category__branch=main).count(), 5)
        self.assertEqual(Order.objects.filter(branch=main, status='served', payment_status='paid').count(), 3)
        admin_user = User.objects.get(username='demo-admin@example.com')
        self.assertTrue(admin_user.is_superuser)
        admin_client = Client()
        admin_client.force_login(admin_user)
        self.assertEqual(admin_client.get(reverse('platform_dashboard')).status_code, 200)
        self.assertEqual(
            Membership.objects.get(user__username='demo-owner@example.com', restaurant=restaurant).role,
            'owner')
        kitchen_user = User.objects.get(username='demo-kitchen@example.com')
        self.assertEqual(
            Membership.objects.get(user=kitchen_user, restaurant=restaurant).branches.get(), main)

        kitchen_client = Client()
        kitchen_password = re.search(r'demo-kitchen@example\.com \| كلمة المرور: ([a-f0-9]+)', output)
        self.assertIsNotNone(kitchen_password)
        self.assertEqual(kitchen_client.post(reverse('login'), {
            'email': kitchen_user.username, 'password': kitchen_password.group(1),
        }).status_code, 302)
        kitchen_url = reverse('kitchen', args=[restaurant.slug])
        feed_url = reverse('kitchen_feed', args=[restaurant.slug])
        self.assertEqual(kitchen_client.get(kitchen_url).status_code, 200)
        self.assertEqual(kitchen_client.get(feed_url).json()['count'], 0)

        table = main.tables.get(name='طاولة 01')
        guest_client = Client(enforce_csrf_checks=True)
        menu = guest_client.get(reverse('public_menu', args=[table.public_token]))
        self.assertContains(menu, 'id="openCart"')
        self.assertContains(menu, reverse('place_order', args=[table.public_token]))
        item = MenuItem.objects.get(category__branch=main, name='برجر سفرة')
        payload = {'idempotency_key': str(uuid.uuid4()), 'session_token': str(table.session_token),
                   'lines': [{'item_id': item.pk, 'quantity': 1, 'options': []}]}
        response = guest_client.post(reverse('place_order', args=[table.public_token]),
                                     data=json.dumps(payload), content_type='application/json',
                                     HTTP_X_CSRFTOKEN=guest_client.cookies['csrftoken'].value)
        self.assertEqual(response.status_code, 200, response.content)
        self.assertTrue(response.json()['ok'])
        order = Order.objects.get(pk=response.json()['order_number'])
        self.assertEqual(order.source, 'qr')
        self.assertEqual(order.table, table)
        self.assertEqual(order.total, Decimal('175.00'))
        self.assertEqual(order.lines.get().item_name, 'برجر سفرة')
        self.assertContains(guest_client.get(response.json()['order_url']), 'برجر سفرة')
        self.assertEqual(kitchen_client.get(feed_url).json()['count'], 1)
        self.assertContains(kitchen_client.get(kitchen_url), f'طلب #{order.pk}')

        old_password = kitchen_user.password
        old_public_token = table.public_token
        self.seed()
        kitchen_user.refresh_from_db()
        table.refresh_from_db()
        self.assertNotEqual(kitchen_user.password, old_password)
        self.assertEqual(table.public_token, old_public_token)
        self.assertEqual(Order.objects.filter(branch=main).count(), 4)

    @override_settings(DEBUG=False)
    def test_seed_refuses_non_debug_mode(self):
        with self.assertRaises(CommandError):
            self.seed()
        self.assertFalse(Restaurant.objects.filter(slug='demo-sofra').exists())

    def test_seed_refuses_existing_non_demo_account_without_partial_data(self):
        existing = User.objects.create_user(username='demo-owner@example.com', password='Original!123')
        with self.assertRaises(CommandError):
            self.seed()
        existing.refresh_from_db()
        self.assertTrue(existing.check_password('Original!123'))
        self.assertFalse(Restaurant.objects.filter(slug='demo-sofra').exists())

class SafraTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(username='owner@example.com', email='owner@example.com', password='StrongPass!123', first_name='منى')
        self.other = User.objects.create_user(username='other@example.com', password='StrongPass!123')
        self.restaurant = Restaurant.objects.create(name='مطعم سفرة', slug='safra-test')
        plan = Plan.objects.create(name='Test', max_tables=10, max_items=50, max_staff=5)
        Subscription.objects.create(restaurant=self.restaurant, plan=plan, ends_at=timezone.now()+timedelta(days=14))
        self.branch = Branch.objects.create(restaurant=self.restaurant)
        Membership.objects.create(user=self.owner, restaurant=self.restaurant, role='owner')
        self.category = Category.objects.create(branch=self.branch, name='الأطباق')
        self.item = MenuItem.objects.create(category=self.category, name='طبق', price=Decimal('100.00'))
        self.table = DiningTable.objects.create(branch=self.branch, name='طاولة 1')
        self.other_restaurant = Restaurant.objects.create(name='مطعم آخر', slug='other-test')
        Subscription.objects.create(restaurant=self.other_restaurant, plan=plan,
                                    ends_at=timezone.now()+timedelta(days=14))
        self.other_branch = Branch.objects.create(restaurant=self.other_restaurant)
        Membership.objects.create(user=self.other, restaurant=self.other_restaurant, role='owner')

    def payload(self, options=None):
        return {'idempotency_key': str(uuid.uuid4()), 'lines': [{'item_id': self.item.pk, 'quantity': 2, 'options': options or []}]}

    def test_cancel_and_collect_are_mutually_exclusive_and_legacy_collection_reconciles(self):
        collected = create_order(self.table, self.payload())
        record_manual_payment(collected, self.owner)
        with self.assertRaises(ValidationError):
            advance_order(collected, self.owner, 'cancelled', 'Wrong order')
        collected.refresh_from_db()
        self.assertEqual((collected.status, collected.payment_status), ('new', 'paid'))
        self.assertEqual(collected.history.count(), 1)
        cancelled = create_order(self.table, self.payload())
        advance_order(cancelled, self.owner, 'cancelled', 'Wrong order')
        with self.assertRaises(ValidationError):
            record_manual_payment(cancelled, self.owner)
        cancelled.refresh_from_db()
        self.assertEqual((cancelled.status, cancelled.payment_status), ('cancelled', 'unpaid'))
        self.assertIsNone(cancelled.paid_at)

        Order.objects.filter(pk=collected.pk).update(status='cancelled')
        outstanding = create_order(self.table, self.payload())
        self.client.force_login(self.owner)
        summary = reverse('table_summary', args=[self.restaurant.slug, self.table.pk])
        reports = reverse('reports', args=[self.restaurant.slug])
        for lang, label in [('ar', 'تحصيل مسجّل لطلبات ملغاة'), ('en', 'Recorded collection on cancelled orders')]:
            page = self.client.get(summary, {'lang': lang})
            self.assertContains(page, label)
            self.assertEqual(page.context['paid'], collected.total)
            self.assertEqual(page.context['remaining'], outstanding.total)
            self.assertEqual([row.pk for row in page.context['cancelled_collections']], [collected.pk])
            report = self.client.get(reports, {'lang': lang})
            self.assertEqual(report.context['paid_value'], collected.total)
            self.assertEqual(report.context['cancelled_collections'], {'count': 1, 'total': collected.total})

    def test_quantity_validation_is_atomic_for_checkout_and_edit(self):
        guest_url = reverse('place_order', args=[self.table.public_token])
        staff_url = reverse('staff_order', args=[self.restaurant.slug])
        self.client.force_login(self.owner)
        for value in (0, 31, 1.5, 1.0, True, '', '1.5'):
            with self.subTest(value=value):
                payload = self.payload()
                payload['lines'].append(dict(payload['lines'][0], quantity=value))
                payload.update(session_token=str(self.table.session_token), table_id=self.table.pk)
                for url in (guest_url, staff_url):
                    response = self.client.post(url, payload, content_type='application/json')
                    self.assertEqual(response.status_code, 400)
                self.assertFalse(Order.objects.exists())
        payload = self.payload()
        payload['lines'] = [dict(payload['lines'][0], quantity=value) for value in (1, '30')]
        order = create_order(self.table, payload)
        self.assertEqual(create_order(self.table, payload).pk, order.pk)
        self.assertEqual(order.total, Decimal('3100.00'))
        line = order.lines.order_by('pk').first()
        change = {'edit_key': str(uuid.uuid4()), 'expected_version': 0, 'reason': 'Review quantity',
                  'existing': [{'id': line.pk, 'quantity': 1.5}], 'add': []}
        edit_url = reverse('order_edit', args=[self.restaurant.slug, order.pk])
        self.assertEqual(self.client.post(edit_url, change, content_type='application/json').status_code, 400)
        change['existing'][0]['quantity'] = 1
        change['add'] = [{'item_id': self.item.pk, 'quantity': 1.5}]
        self.assertEqual(self.client.post(edit_url, change, content_type='application/json').status_code, 400)
        order.refresh_from_db()
        self.assertEqual((order.version, order.total, order.lines.count()), (0, Decimal('3100.00'), 2))
        self.assertFalse(order.revisions.exists())

    def test_corrected_order_is_read_only_and_counts_first_transition(self):
        order = create_order(self.table, self.payload())
        self.client.force_login(self.owner)
        edit_url = reverse('order_edit', args=[self.restaurant.slug, order.pk])
        ledger_url = reverse('manage_orders', args=[self.restaurant.slug])
        advance_order(order, self.owner, 'accepted')
        self.assertTrue(self.client.get(edit_url).context['can_edit'])
        advance_order(order, self.owner, 'preparing')
        change = {'edit_key': str(uuid.uuid4()), 'expected_version': 0, 'reason': 'Late edit',
                  'existing': [{'id': order.lines.get().pk, 'quantity': 1}], 'add': []}
        self.assertEqual(self.client.post(edit_url, change, content_type='application/json').status_code, 400)
        correct_order_status(order, self.owner, 'Wrong step', 'preparing')
        for lang in ('ar', 'en'):
            page = self.client.get(edit_url, {'lang': lang})
            self.assertFalse(page.context['can_edit'])
            self.assertContains(page, f'?table={self.table.pk}')
            self.assertNotContains(self.client.get(ledger_url, {'lang': lang}), edit_url)
        correct_order_status(order, self.owner, 'Wrong step', 'accepted')
        advance_order(order, self.owner, 'accepted')
        advance_order(order, self.owner, 'preparing')
        advance_order(order, self.owner, 'ready')
        correct_order_status(order, self.owner, 'Wrong step', 'ready')
        advance_order(order, self.owner, 'ready')
        for state in ('accepted', 'ready'):
            events = list(order.history.filter(new_status=state).order_by('pk'))
            for index, event in enumerate(events):
                seconds = 120 + 480 * index
                OrderHistory.objects.filter(pk=event.pk).update(created_at=order.created_at + timedelta(seconds=seconds))
        report = self.client.get(reverse('reports', args=[self.restaurant.slug]), {'lang': 'ar'})
        self.assertEqual((report.context['accepted_samples'], report.context['ready_samples']), (1, 1))
        self.assertEqual(report.context['accepted_time'], '2 د 0 ث')
        self.assertEqual(report.context['ready_time'], '2 د 0 ث')
        self.assertEqual(order.history.filter(new_status='ready').count(), 2)

    def test_missing_subscription_blocks_operations_until_operator_configures_it(self):
        self.restaurant.subscription.delete()
        self.client.force_login(self.owner)
        args = [self.restaurant.slug]
        activation = reverse('subscription_activation', args=args)
        self.assertRedirects(self.client.get(reverse('manage_menu', args=args)), activation)
        self.assertContains(self.client.get(activation, {'lang': 'en'}), 'no subscription record')
        self.client.post(activation)
        self.assertFalse(Subscription.objects.filter(restaurant=self.restaurant).exists())
        table_args = [self.table.public_token]
        self.assertEqual(self.client.get(reverse('public_menu', args=table_args)).status_code, 404)
        self.assertEqual(self.client.post(reverse('place_order', args=table_args), self.payload(),
                                          content_type='application/json').status_code, 400)
        self.client.post(reverse('create_call', args=table_args),
                         {'kind': 'bill', 'session_token': str(self.table.session_token)})
        self.assertFalse(ServiceCall.objects.exists())
        self.client.force_login(User.objects.create_superuser(username='recovery-operator'))
        plan, _ = Plan.objects.get_or_create(name='Starter')
        self.client.post(reverse('platform_update_restaurant', args=[self.restaurant.pk]), {
            'action': 'subscription', 'status': 'active', 'plan_id': plan.pk, 'reason': 'Verified terms',
            'ends_at': (timezone.localdate() + timedelta(days=10)).isoformat(),
        })
        self.assertTrue(Subscription.objects.get(restaurant=self.restaurant).valid())
        self.assertEqual(self.client.get(reverse('public_menu', args=table_args)).status_code, 200)

    @override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
    def test_invitation_matches_legacy_email_without_creating_duplicate(self):
        account = User.objects.create_user('legacy-cashier', 'legacy@example.org', 'StrongPass!123')
        account_without_membership = User.objects.create_user('legacy-waiter', 'waiter@example.org', 'StrongPass!123')
        Membership.objects.create(restaurant=self.other_restaurant, user=account, role='manager')
        User.objects.create_user('shared-a', 'shared@example.org')
        User.objects.create_user('shared-b', 'shared@example.org')
        staff = reverse('staff', args=[self.restaurant.slug])
        self.client.force_login(self.owner)
        details = {'action': 'invite', 'branch_id': self.branch.pk, 'role': 'cashier'}
        self.client.post(staff, dict(details, email='shared@example.org'))
        self.assertFalse(mail.outbox)
        for user in (account, account_without_membership):
            self.client.force_login(self.owner)
            self.client.post(staff, dict(details, email=user.email))
            invite_path = urlparse(mail.outbox[-1].body.split()[-1]).path
            self.client.logout()
            self.assertEqual(self.client.get(invite_path).status_code, 302)
            login_response = self.client.post(reverse('login'), {'username': user.email,
                                              'password': 'StrongPass!123', 'next': invite_path})
            self.assertRedirects(login_response, invite_path)
            self.assertEqual(self.client.post(invite_path).status_code, 302)
            self.assertEqual(self.client.get(invite_path).status_code, 410)
            self.assertEqual(User.objects.filter(email=user.email).count(), 1)
            self.assertEqual(user.memberships.get(restaurant=self.restaurant).role, 'cashier')
        self.assertEqual(account.memberships.get(restaurant=self.other_restaurant).role, 'manager')

    def test_admin_language_switch_preserves_query_and_csv_localizes_labels(self):
        self.client.force_login(User.objects.create_superuser(username='language-operator'))
        for name in ('admin:core_restaurant_changelist', 'admin:core_order_changelist'):
            url = reverse(name)
            response = self.client.get(url, {'q': 'Cafe', 'o': '1', 'lang': 'en'}, follow=True)
            self.assertEqual(response.redirect_chain, [(url + '?q=Cafe&o=1', 302)])
            self.assertEqual(response['Content-Language'], 'en')
            self.assertRedirects(self.client.get(url, {'q': 'Cafe', 'lang': 'ar'}), url + '?q=Cafe')
        create_order(self.table, self.payload())
        self.client.force_login(self.owner)
        report = reverse('reports', args=[self.restaurant.slug])
        rows = {}
        for lang in ('ar', 'en'):
            stream = self.client.get(report, {'export': 'csv', 'lang': lang}).streaming_content
            rows[lang] = list(csv.reader(io.StringIO(b''.join(stream).decode('utf-8-sig'))))
        self.assertEqual(rows['en'][0][0], 'Order number')
        self.assertEqual(rows['en'][1][2:4], ['New', 'Unpaid'])
        self.assertEqual(rows['ar'][1][2:4], ['جديد', 'غير مدفوع'])
        self.assertEqual(rows['ar'][1][:2] + rows['ar'][1][4:], rows['en'][1][:2] + rows['en'][1][4:])

    def test_local_login_referrer_policy_preserves_csrf_origin_check(self):
        client = Client(enforce_csrf_checks=True, HTTP_HOST='localhost:8000')
        page = client.get(reverse('login'))
        self.assertEqual(page.status_code, 200)
        self.assertEqual(page['Referrer-Policy'], 'same-origin')
        self.assertIn(settings.CSRF_COOKIE_NAME, client.cookies)
        self.assertContains(page, 'name="csrfmiddlewaretoken"')
        token = client.cookies[settings.CSRF_COOKIE_NAME].value
        response = client.post(reverse('login'), {
            'email': self.owner.username, 'password': 'StrongPass!123',
            'csrfmiddlewaretoken': token,
        }, HTTP_ORIGIN='http://localhost:8000')
        self.assertEqual(response.status_code, 302)
        another_client = Client(enforce_csrf_checks=True, HTTP_HOST='localhost:8000')
        another_client.get(reverse('login'))
        null_origin = another_client.post(reverse('login'), {
            'email': self.owner.username, 'password': 'StrongPass!123',
            'csrfmiddlewaretoken': another_client.cookies[settings.CSRF_COOKIE_NAME].value,
        }, HTTP_ORIGIN='null')
        self.assertEqual(null_origin.status_code, 403)
        self.assertEqual(Client(enforce_csrf_checks=True).post(reverse('login'), {
            'email': self.owner.username, 'password': 'StrongPass!123',
        }).status_code, 403)

    def test_order_price_snapshot_and_idempotency(self):
        payload = self.payload()
        order = create_order(self.table, payload)
        self.assertEqual(order.total, Decimal('200.00'))
        self.item.price = Decimal('150.00'); self.item.save()
        self.assertEqual(create_order(self.table, payload).pk, order.pk)
        self.assertEqual(order.lines.get().unit_price, Decimal('100.00'))
        self.assertEqual(Order.objects.count(), 1)

    def test_guest_rechecks_the_reviewed_total_before_creating_an_order(self):
        endpoint = reverse('place_order', args=[self.table.public_token])
        payload = {**self.payload(), 'session_token': str(self.table.session_token), 'reviewed_total': '180.00'}
        rejected = self.client.post(endpoint + '?lang=en', payload, content_type='application/json')
        self.assertEqual(rejected.status_code, 400)
        self.assertIn('Prices changed', rejected.json()['error'])
        self.assertFalse(Order.objects.filter(table=self.table).exists())
        payload['reviewed_total'] = '200.00'
        accepted = self.client.post(endpoint, payload, content_type='application/json')
        self.assertEqual(accepted.status_code, 200)
        self.item.price = Decimal('125.00')
        self.item.save()
        replay = self.client.post(endpoint, payload, content_type='application/json')
        self.assertEqual(replay.json()['order_url'], accepted.json()['order_url'])
        self.assertEqual(Order.objects.get(table=self.table).total, Decimal('200.00'))

    def test_print_summary_uses_session_snapshots_and_existing_cashier_scope(self):
        first = create_order(self.table, self.payload())
        Order.objects.filter(pk=first.pk).update(payment_status='paid')
        current = create_order(self.table, self.payload())
        cancelled = create_order(self.table, self.payload())
        Order.objects.filter(pk=cancelled.pk).update(status='cancelled')
        previous = create_order(self.table, self.payload())
        Order.objects.filter(pk=previous.pk).update(session_token=uuid.uuid4())
        self.item.name = 'Changed after purchase'
        self.item.save(update_fields=['name'])
        link = reverse('table_summary', args=[self.restaurant.slug, self.table.pk])
        self.assertEqual(self.client.get(link).status_code, 302)
        self.client.force_login(self.other)
        self.assertEqual(self.client.get(link).status_code, 403)
        cashier = User.objects.create_user(username='summary-cashier')
        membership = Membership.objects.create(restaurant=self.restaurant, user=cashier, role='cashier')
        membership.branches.add(self.branch)
        self.client.force_login(cashier)
        page = self.client.get(link, {'lang':'en'})
        self.assertContains(page, 'Print summary')
        self.assertNotContains(page, self.item.name)
        self.assertEqual([o.pk for o in page.context['orders']], [first.pk, current.pk])
        self.assertEqual(page.context['total'], Decimal('400.00'))
        self.assertEqual(page.context['remaining'], Decimal('200.00'))
        self.assertEqual(self.client.post(link).status_code, 405)
        membership.role = 'waiter'
        membership.save(update_fields=['role'])
        self.assertEqual(self.client.get(link).status_code, 403)

    def test_owner_activity_and_workspace_language_are_branch_scoped(self):
        self.client.force_login(self.owner)
        create_order(self.table, self.payload())
        response = self.client.get(reverse('dashboard', args=[self.restaurant.slug]), {'period': 14, 'lang': 'en'})
        self.assertContains(response, 'Order activity')
        self.assertContains(response, 'aria-current="page"')
        self.assertEqual(len(response.context['activity']), 14)
        self.assertEqual(sum(day['count'] for day in response.context['activity']), 1)
        self.assertEqual(response.context['activity_average'], Decimal('200.00'))
        self.assertContains(self.client.get(reverse('manage_orders', args=[self.restaurant.slug])), 'Order history')
        self.client.force_login(self.other)
        other = self.client.get(reverse('dashboard', args=[self.other_restaurant.slug]))
        self.assertEqual(other.context['activity_value'], 0)
        self.assertEqual(sum(day['count'] for day in other.context['activity']), 0)

    def test_health_checks_database_without_exposing_failure_details(self):
        endpoint = reverse('health')
        response = self.client.get(endpoint)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {'status': 'ok'})
        self.assertEqual(response['Cache-Control'], 'no-store')
        with patch('core.views.connection.cursor', side_effect=DatabaseError('private database detail')):
            response = self.client.get(endpoint)
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json(), {'status': 'unavailable'})
        self.assertNotIn(b'private database detail', response.content)

    def test_production_settings_require_explicit_sqlite_opt_in(self):
        env = {**os.environ, 'DJANGO_DEBUG': '0', 'DJANGO_ALLOWED_HOSTS': 'example.com',
               'DJANGO_SECRET_KEY': 'replace-with-a-long-random-production-secret',
               'DJANGO_ALLOW_SQLITE': '1', 'DJANGO_SECURE_SSL_REDIRECT': '1'}
        env.pop('DATABASE_URL', None)
        command = [sys.executable, '-c', 'import config.settings']
        invalid_secret = subprocess.run(command, env=env, capture_output=True, text=True)
        self.assertNotEqual(invalid_secret.returncode, 0)
        self.assertIn('DJANGO_SECRET_KEY', invalid_secret.stderr)
        env['DJANGO_SECRET_KEY'] = 'a' * 60
        env['DJANGO_ALLOW_SQLITE'] = '0'
        missing_database = subprocess.run(command, env=env, capture_output=True, text=True)
        self.assertNotEqual(missing_database.returncode, 0)
        self.assertIn('DATABASE_URL', missing_database.stderr)

        env['DJANGO_ALLOW_SQLITE'] = '1'
        sqlite_config = subprocess.run([sys.executable, '-c', (
            'from config import settings as s\n'
            'assert s.DATABASES["default"]["ENGINE"] == "django.db.backends.sqlite3"\n'
            'assert s.DATABASES["default"]["NAME"] == s.BASE_DIR / "db.sqlite3"\n'
            'assert s.DEBUG is False\n'
            'assert s.SESSION_COOKIE_SECURE and s.CSRF_COOKIE_SECURE and s.SECURE_SSL_REDIRECT\n'
        )], env=env, capture_output=True, text=True)
        self.assertEqual(sqlite_config.returncode, 0, sqlite_config.stderr)

        env['DATABASE_URL'] = 'sqlite:///another.sqlite3'
        invalid_database = subprocess.run(command, env=env, capture_output=True, text=True)
        self.assertNotEqual(invalid_database.returncode, 0)
        self.assertIn('DATABASE_URL', invalid_database.stderr)

        env['DATABASE_URL'] = 'postgresql://test:test@localhost:5432/test'
        postgres_config = subprocess.run([sys.executable, '-c', (
            'from config import settings as s\n'
            'assert s.DATABASES["default"]["ENGINE"] == "django.db.backends.postgresql"\n'
        )], env=env, capture_output=True, text=True)
        self.assertEqual(postgres_config.returncode, 0, postgres_config.stderr)

    def test_stale_table_state_is_rechecked_before_order_and_call_creation(self):
        stale = DiningTable.objects.get(pk=self.table.pk)
        DiningTable.objects.filter(pk=self.table.pk).update(active=False)
        with self.assertRaises(ValidationError):
            create_order(stale, self.payload())
        with self.assertRaises(ValidationError):
            create_service_call(stale, 'waiter', session_token=stale.session_token)
        self.assertFalse(Order.objects.exists())
        self.assertFalse(ServiceCall.objects.exists())
        DiningTable.objects.filter(pk=self.table.pk).update(active=True, session_token=uuid.uuid4())
        with self.assertRaises(ValidationError):
            create_service_call(stale, 'waiter', session_token=stale.session_token)

    def test_order_rate_limit_is_enforced_within_table_transaction(self):
        for _ in range(12):
            create_order(self.table, self.payload())
        with self.assertRaises(ValidationError):
            create_order(self.table, self.payload())
        self.assertEqual(Order.objects.count(), 12)

    def test_reject_foreign_item_and_modifier(self):
        foreign_category = Category.objects.create(branch=self.other_branch, name='غير متاح')
        foreign_item = MenuItem.objects.create(category=foreign_category, name='آخر', price=10)
        bad = self.payload(); bad['lines'][0]['item_id'] = foreign_item.pk
        with self.assertRaises(ValidationError): create_order(self.table, bad)
        group = ModifierGroup.objects.create(item=foreign_item, name='إضافات')
        option = ModifierOption.objects.create(group=group, name='جبن', price=10)
        with self.assertRaises(ValidationError): create_order(self.table, self.payload([option.pk]))

    def test_valid_modifier_total_and_closed_table(self):
        group = ModifierGroup.objects.create(item=self.item, name='حجم', min_select=1, max_select=1)
        option = ModifierOption.objects.create(group=group, name='كبير', price=Decimal('20.00'))
        with self.assertRaises(ValidationError): create_order(self.table, self.payload())
        order = create_order(self.table, self.payload([option.pk]))
        self.assertEqual(order.total, Decimal('240.00'))
        self.table.active = False; self.table.save()
        with self.assertRaises(ValidationError): create_order(self.table, self.payload([option.pk]))

    def test_order_transitions(self):
        order = create_order(self.table, self.payload())
        with self.assertRaises(ValidationError): advance_order(order, self.owner, 'ready')
        for status in ('accepted','preparing','ready','served'):
            advance_order(order, self.owner, status)
        order.refresh_from_db()
        self.assertEqual(order.status, 'served')
        self.assertEqual(order.history.count(), 5)

    def test_manager_can_correct_one_step_with_reason_and_audit(self):
        order = create_order(self.table, self.payload())
        advance_order(order, self.owner, 'accepted')
        advance_order(order, self.owner, 'preparing')
        self.client.login(username=self.owner.username, password='StrongPass!123')
        endpoint = reverse('order_status_correct', args=[self.restaurant.slug, order.pk])
        signature = self.client.get(reverse('kitchen_feed', args=[self.restaurant.slug])).json()['signature']
        self.assertContains(self.client.get(reverse('manage_orders', args=[self.restaurant.slug])), 'تصحيح الحالة السابقة')
        self.client.post(endpoint, {'reason': ' ', 'expected_status': 'preparing'})
        order.refresh_from_db()
        self.assertEqual(order.status, 'preparing')
        self.client.post(endpoint, {'reason': 'ضغطت بالخطأ', 'expected_status': 'preparing'})
        order.refresh_from_db()
        self.assertEqual(order.status, 'accepted')
        history = order.history.order_by('-pk').first()
        self.assertEqual((history.old_status, history.new_status, history.actor, history.reason),
                         ('preparing', 'accepted', self.owner, 'ضغطت بالخطأ'))
        self.assertTrue(AuditLog.objects.filter(restaurant=self.restaurant, action='order.status.correct').exists())
        self.assertNotEqual(signature, self.client.get(reverse('kitchen_feed', args=[self.restaurant.slug])).json()['signature'])
        self.client.post(endpoint, {'reason': 'ضغطت بالخطأ', 'expected_status': 'preparing'})
        order.refresh_from_db()
        self.assertEqual(order.status, 'accepted')
        change = {'edit_key': str(uuid.uuid4()), 'expected_version': 0, 'reason': 'تغيير الصنف',
                  'existing': [{'id': order.lines.get().pk, 'quantity': 1}], 'add': []}
        with self.assertRaises(ValidationError):
            edit_order(order, self.owner, change)
        manager = User.objects.create_user(username='correction-manager@example.com', password='StrongPass!123')
        Membership.objects.create(user=manager, restaurant=self.restaurant, role='manager')
        self.client.login(username=manager.username, password='StrongPass!123')
        self.client.post(endpoint, {'reason': 'تصحيح استلام الطلب', 'expected_status': 'accepted'})
        order.refresh_from_db()
        self.assertEqual(order.status, 'new')

    def test_correction_rejects_other_roles_foreign_paid_and_terminal_orders(self):
        order = create_order(self.table, self.payload())
        advance_order(order, self.owner, 'accepted')
        endpoint = reverse('order_status_correct', args=[self.restaurant.slug, order.pk])
        waiter = User.objects.create_user(username='correction-waiter@example.com', password='StrongPass!123')
        member = Membership.objects.create(user=waiter, restaurant=self.restaurant, role='waiter')
        member.branches.add(self.branch)
        self.client.login(username=waiter.username, password='StrongPass!123')
        self.assertEqual(self.client.post(endpoint, {'reason': 'خطأ تشغيلي', 'expected_status': 'accepted'}).status_code, 403)
        self.client.login(username=self.other.username, password='StrongPass!123')
        self.assertEqual(self.client.post(endpoint, {'reason': 'خطأ تشغيلي', 'expected_status': 'accepted'}).status_code, 403)
        self.client.login(username=self.owner.username, password='StrongPass!123')
        order.payment_status = 'paid'; order.save(update_fields=['payment_status'])
        self.client.post(endpoint, {'reason': 'خطأ تشغيلي', 'expected_status': 'accepted'})
        order.refresh_from_db()
        self.assertEqual(order.status, 'accepted')
        order.payment_status = 'unpaid'; order.save(update_fields=['payment_status'])
        for status in ('preparing', 'ready', 'served'):
            advance_order(order, self.owner, status)
        self.client.post(endpoint, {'reason': 'خطأ تشغيلي', 'expected_status': 'served'})
        order.refresh_from_db()
        self.assertEqual(order.status, 'served')

    def test_tenant_access_and_public_order(self):
        self.client.login(username='other@example.com', password='StrongPass!123')
        self.assertEqual(self.client.get(reverse('dashboard', args=[self.restaurant.slug])).status_code, 403)
        self.assertEqual(self.client.get(reverse('manage_menu', args=[self.restaurant.slug])).status_code, 403)
        self.assertEqual(self.client.get(reverse('reports', args=[self.restaurant.slug])+'?export=csv').status_code, 403)
        self.client.logout()
        self.assertEqual(self.client.get(reverse('public_menu', args=[self.table.public_token])).status_code, 200)
        response = self.client.post(reverse('place_order', args=[self.table.public_token]), data=json.dumps({**self.payload(), 'session_token': str(self.table.session_token)}), content_type='application/json')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(Order.objects.count(), 1)

    def test_service_call_rate_and_duplicate(self):
        create_service_call(self.table, 'waiter')
        with self.assertRaises(ValidationError): create_service_call(self.table, 'waiter')
        create_service_call(self.table, 'bill')
        self.assertEqual(ServiceCall.objects.count(), 2)

    def test_service_call_response_thresholds_use_server_timestamps(self):
        self.branch.waiter_response_minutes = 3
        self.branch.bill_response_minutes = 10
        self.branch.save(update_fields=['waiter_response_minutes', 'bill_response_minutes'])
        waiter_call = create_service_call(self.table, 'waiter')
        bill_call = create_service_call(self.table, 'bill')
        past = timezone.now() - timedelta(minutes=5)
        ServiceCall.objects.filter(pk__in=[waiter_call.pk, bill_call.pk]).update(created_at=past)
        self.client.login(username=self.owner.username, password='StrongPass!123')
        response = self.client.get(reverse('manage_calls', args=[self.restaurant.slug]))
        self.assertEqual(response.context['overdue_count'], 1)
        self.assertContains(response, 'تجاوز حد الاستجابة وينتظر الاستلام', count=1)
        advance_call(waiter_call, self.owner, 'acknowledged')
        response = self.client.get(reverse('manage_calls', args=[self.restaurant.slug]))
        self.assertEqual(response.context['overdue_count'], 0)
        self.assertContains(response, 'استجابة بعد الحد المحدد')

    def test_response_threshold_settings_are_scoped_and_validated(self):
        self.client.login(username=self.owner.username, password='StrongPass!123')
        endpoint = reverse('restaurant_settings', args=[self.restaurant.slug])
        data = {'name': self.restaurant.name, 'branch_name': self.branch.name,
                'waiter_response_minutes': '7', 'bill_response_minutes': '12'}
        self.assertEqual(self.client.post(endpoint, data).status_code, 302)
        self.branch.refresh_from_db()
        self.assertEqual((self.branch.waiter_response_minutes, self.branch.bill_response_minutes), (7, 12))
        response = self.client.post(endpoint, {**data, 'waiter_response_minutes': '0'})
        self.assertEqual(response.status_code, 200)
        self.branch.refresh_from_db()
        self.assertEqual(self.branch.waiter_response_minutes, 7)
        self.client.login(username=self.other.username, password='StrongPass!123')
        self.assertEqual(self.client.post(endpoint, data).status_code, 403)

    def test_admin_pages_render(self):
        self.client.login(username='owner@example.com', password='StrongPass!123')
        for name in ('dashboard','manage_menu','manage_tables','manage_orders','kitchen','manage_calls','reports','staff','restaurant_settings'):
            with self.subTest(name=name):
                response = self.client.get(reverse(name, args=[self.restaurant.slug]))
                self.assertEqual(response.status_code, 200)

    @override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
    def test_english_owner_and_team_action_emails_open_in_english(self):
        registration = {'name': 'Mona', 'email': 'english@example.com', 'restaurant': 'Window Café',
                        'password': 'StrongPass!123'}
        self.client.post(reverse('register') + '?lang=en', registration)
        self.client.post(reverse('resend_owner_verification'), registration)
        self.client.force_login(self.owner)
        self.client.post(reverse('staff', args=[self.restaurant.slug]), {
            'action': 'invite', 'email': 'crew@example.com', 'role': 'waiter', 'branch_id': self.branch.pk,
        })
        self.assertEqual([message.subject for message in mail.outbox], [
            'Verify your Serveza owner email', 'Your new Serveza verification link', 'Invitation to join a Serveza team',
        ])
        self.assertIn('Hello Mona', mail.outbox[0].body)
        self.assertIn('24 hours', mail.outbox[1].body)
        self.assertIn('within 7 days', mail.outbox[2].body)
        links = [re.search(r'https?://[^\s]+', message.body).group() for message in mail.outbox]
        for link in links:
            self.assertTrue(link.endswith('/?lang=en'))
        reader = Client()
        expired = reader.get(links[0])
        self.assertContains(expired, 'Invalid verification link', status_code=400)
        self.assertEqual(reader.get(links[1]).status_code, 302)
        invitation = Client().get(links[2])
        self.assertContains(invitation, 'Accept invitation')
        self.assertContains(invitation, 'lang="en"')

    def test_invalid_registration_keeps_contact_fields_and_marks_only_invalid_inputs(self):
        values = {'name': 'Nour', 'restaurant': 'Nour Café', 'email': 'nour@example.com', 'password': '12345678'}
        response = self.client.post(reverse('register') + '?lang=en', values)
        for field in ('name', 'restaurant', 'email'):
            self.assertContains(response, f'value="{values[field]}"')
        self.assertContains(response, 'aria-describedby="id_password_error"')
        self.assertContains(response, 'id="id_password_error"')
        self.assertNotContains(response, 'value="12345678"')
        self.assertContains(response, 'contact customer service')
        self.assertFalse(User.objects.filter(username=values['email']).exists())

    @override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend', SITE_URL='https://menu.example')
    def test_registration_creates_isolated_restaurant(self):
        response = self.client.post(reverse('register'), {'name':'ليلى','email':'new@example.com','restaurant':'بيتنا','password':'StrongPass!123'})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse('verification_pending'))
        created = Restaurant.objects.get(name='بيتنا')
        self.assertTrue(Membership.objects.filter(user__username='new@example.com',restaurant=created,role='owner').exists())
        self.assertEqual(created.branches.count(), 1)
        self.assertEqual(created.subscription.status, 'pending')
        self.assertFalse(created.subscription.valid())
        new_owner = User.objects.get(username='new@example.com')
        self.assertFalse(new_owner.is_active)
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn('https://menu.example/verify/', mail.outbox[0].body)
        html, content_type = mail.outbox[0].alternatives[0]
        self.assertEqual(content_type, 'text/html')
        self.assertIn('href="https://menu.example/verify/', html)
        self.assertIn('تأكيد بريدي الإلكتروني', html)
        raw_token = re.search(r'/verify/([^/]+)/', mail.outbox[0].body).group(1)
        self.assertNotEqual(raw_token, new_owner.owner_email_verification.token_hash)
        self.assertFalse(self.client.login(username=new_owner.username, password='StrongPass!123'))
        self.assertEqual(self.client.get(reverse('verify_owner_email', args=[raw_token])).status_code, 302)
        new_owner.refresh_from_db()
        self.assertTrue(new_owner.is_active)
        self.assertIsNotNone(new_owner.owner_email_verification.verified_at)
        self.assertEqual(self.client.get(reverse('verify_owner_email', args=[raw_token])).status_code, 400)

    @override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
    def test_owner_verification_resend_rotates_expired_token_and_limits_attempts(self):
        self.client.post(reverse('register'), {'name':'ليلى','email':'new@example.com',
            'restaurant':'بيتنا','password':'StrongPass!123'})
        verification = OwnerEmailVerification.objects.get(user__username='new@example.com')
        old_token = re.search(r'/verify/([^/]+)/', mail.outbox[0].body).group(1)
        verification.expires_at = timezone.now()-timedelta(seconds=1)
        verification.save(update_fields=['expires_at'])
        self.assertEqual(self.client.get(reverse('verify_owner_email', args=[old_token])).status_code, 400)
        resend = reverse('resend_owner_verification')
        self.client.post(resend, {'email':'new@example.com','password':'wrong'})
        self.assertEqual(len(mail.outbox), 1)
        self.client.post(resend, {'email':'new@example.com','password':'StrongPass!123'})
        self.assertEqual(len(mail.outbox), 2)
        new_token = re.search(r'/verify/([^/]+)/', mail.outbox[1].body).group(1)
        self.assertNotEqual(old_token, new_token)
        self.assertEqual(self.client.get(reverse('verify_owner_email', args=[old_token])).status_code, 400)
        self.client.post(resend, {'email':'new@example.com','password':'StrongPass!123'})
        self.assertEqual(len(mail.outbox), 3)
        self.client.post(resend, {'email':'new@example.com','password':'StrongPass!123'})
        self.assertEqual(len(mail.outbox), 3)
        latest = re.search(r'/verify/([^/]+)/', mail.outbox[-1].body).group(1)
        self.assertEqual(self.client.get(reverse('verify_owner_email', args=[latest])).status_code, 302)

    @override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
    def test_registration_rolls_back_when_verification_email_fails(self):
        with patch('core.views.send_mail', side_effect=OSError('SMTP unavailable')):
            response = self.client.post(reverse('register'), {'name':'ليلى','email':'new@example.com',
                'restaurant':'بيتنا','password':'StrongPass!123'})
        self.assertEqual(response.status_code, 200)
        self.assertFalse(User.objects.filter(username='new@example.com').exists())
        self.assertFalse(Restaurant.objects.filter(name='بيتنا').exists())

    def test_public_order_requires_csrf(self):
        client = Client(enforce_csrf_checks=True)
        endpoint = reverse('place_order', args=[self.table.public_token])
        self.assertEqual(client.post(endpoint, data=json.dumps(self.payload()), content_type='application/json').status_code, 403)
        response = client.get(reverse('public_menu', args=[self.table.public_token]))
        self.assertEqual(response.status_code, 200)
        token = client.cookies[settings.CSRF_COOKIE_NAME].value
        self.assertEqual(client.post(endpoint, data=json.dumps({**self.payload(), 'session_token': str(self.table.session_token)}), content_type='application/json', HTTP_X_CSRFTOKEN=token).status_code, 200)

    def test_table_session_close_rejects_active_orders_then_rotates_token(self):
        old_token = str(self.table.session_token)
        payload = {**self.payload(), 'session_token': old_token}
        public_endpoint = reverse('place_order', args=[self.table.public_token])
        response = self.client.post(public_endpoint, data=json.dumps(payload), content_type='application/json')
        self.assertEqual(response.status_code, 200)
        order = Order.objects.get(table=self.table)
        self.assertEqual(str(order.session_token), old_token)
        track_url = reverse('track_order', args=[order.public_token])
        self.assertContains(self.client.get(track_url), 'أضف طلبًا جديدًا للطاولة')
        self.client.login(username=self.owner.username, password='StrongPass!123')
        endpoint = reverse('manage_tables', args=[self.restaurant.slug])
        self.client.post(endpoint, {'action': 'close_session', 'table_id': self.table.pk})
        self.table.refresh_from_db()
        self.assertEqual(str(self.table.session_token), old_token)
        for status in ('accepted', 'preparing', 'ready', 'served'):
            advance_order(order, self.owner, status)
        self.client.post(endpoint, {'action': 'close_session', 'table_id': self.table.pk})
        self.table.refresh_from_db()
        self.assertEqual(str(self.table.session_token), old_token)
        order.payment_status = 'paid'; order.save(update_fields=['payment_status'])
        self.client.post(endpoint, {'action': 'close_session', 'table_id': self.table.pk})
        self.table.refresh_from_db()
        self.assertNotEqual(str(self.table.session_token), old_token)
        self.assertNotContains(self.client.get(track_url), 'أضف طلبًا جديدًا للطاولة')
        self.assertNotContains(self.client.get(track_url), reverse('public_menu', args=[self.table.public_token]))
        self.assertNotContains(self.client.get(reverse('manage_orders', args=[self.restaurant.slug])),
                               f'{reverse("staff_order", args=[self.restaurant.slug])}?table={self.table.pk}')
        self.assertEqual(order.history.count(), 5)
        self.assertTrue(AuditLog.objects.filter(restaurant=self.restaurant, action='table.close_session').exists())
        self.client.logout()
        stale = self.client.post(public_endpoint, data=json.dumps({**self.payload(), 'session_token': old_token}), content_type='application/json')
        self.assertEqual(stale.status_code, 400)
        fresh = self.client.post(public_endpoint, data=json.dumps({**self.payload(), 'session_token': str(self.table.session_token)}), content_type='application/json')
        self.assertEqual(fresh.status_code, 200)

    def test_session_close_scopes_table_and_waits_for_service_calls(self):
        call = create_service_call(self.table, 'waiter')
        self.client.login(username=self.owner.username, password='StrongPass!123')
        endpoint = reverse('manage_tables', args=[self.restaurant.slug])
        original = self.table.session_token
        self.client.post(endpoint, {'action': 'close_session', 'table_id': self.table.pk})
        self.table.refresh_from_db()
        self.assertEqual(self.table.session_token, original)
        for status in ('acknowledged', 'in_progress', 'done'):
            advance_call(call, self.owner, status)
        self.client.post(endpoint, {'action': 'close_session', 'table_id': self.table.pk})
        self.table.refresh_from_db()
        self.assertNotEqual(self.table.session_token, original)
        self.client.login(username=self.other.username, password='StrongPass!123')
        self.assertEqual(self.client.post(reverse('manage_tables', args=[self.other_restaurant.slug]),
            {'action': 'close_session', 'table_id': self.table.pk}).status_code, 404)

    def test_role_cannot_change_order_outside_permissions(self):
        waiter = User.objects.create_user(username='waiter@example.com',password='StrongPass!123')
        waiter_membership = Membership.objects.create(user=waiter,restaurant=self.restaurant,role='waiter')
        waiter_membership.branches.add(self.branch)
        order = create_order(self.table,self.payload())
        self.client.login(username='waiter@example.com',password='StrongPass!123')
        response = self.client.post(reverse('order_status_change',args=[self.restaurant.slug,order.pk]),{'status':'accepted'})
        self.assertEqual(response.status_code,403)
        order.refresh_from_db()
        self.assertEqual(order.status,'new')

    def test_expired_subscription_blocks_new_orders_and_items(self):
        subscription = self.restaurant.subscription
        subscription.status = 'expired'; subscription.save()
        with self.assertRaises(ValidationError): create_order(self.table, self.payload())
        self.client.login(username='owner@example.com',password='StrongPass!123')
        count = MenuItem.objects.count()
        self.client.post(reverse('manage_menu',args=[self.restaurant.slug]),{'action':'item','category':self.category.pk,'name':'جديد','price':'10'})
        self.assertEqual(MenuItem.objects.count(),count)

    def test_end_to_end_management_to_kitchen(self):
        self.client.login(username='owner@example.com',password='StrongPass!123')
        response = self.client.post(reverse('manage_tables',args=[self.restaurant.slug]),{'action':'create','name':'طاولة 2'})
        self.assertEqual(response.status_code,302)
        table = DiningTable.objects.get(branch=self.branch,name='طاولة 2')
        self.assertEqual(self.client.get(reverse('table_qr',args=[self.restaurant.slug,table.pk])).get('Content-Type'),'image/png')
        self.client.logout()
        data = self.payload(); data['lines'][0]['quantity'] = 1; data['session_token'] = str(table.session_token)
        response = self.client.post(reverse('place_order',args=[table.public_token]),data=json.dumps(data),content_type='application/json')
        self.assertEqual(response.status_code,200)
        order = Order.objects.get(table=table)
        self.client.login(username='owner@example.com',password='StrongPass!123')
        self.assertContains(self.client.get(reverse('kitchen',args=[self.restaurant.slug])),f'طلب #{order.pk}')
        self.client.post(reverse('order_status_change',args=[self.restaurant.slug,order.pk]),{'status':'accepted'})
        order.refresh_from_db()
        self.assertEqual(order.status,'accepted')

    def test_plan_table_limit(self):
        plan = self.restaurant.subscription.plan
        plan.max_tables = 1; plan.save()
        self.client.login(username='owner@example.com',password='StrongPass!123')
        self.client.post(reverse('manage_tables',args=[self.restaurant.slug]),{'action':'create','name':'طاولة 2'})
        self.assertEqual(DiningTable.objects.filter(branch=self.branch).count(),1)

    def test_branch_plan_limit_and_staff_assignment(self):
        self.client.login(username='owner@example.com',password='StrongPass!123')
        endpoint = reverse('manage_branches',args=[self.restaurant.slug])
        self.client.post(endpoint,{'action':'create','name':'فرع جديد'})
        self.assertEqual(self.restaurant.branches.count(),1)
        plan = self.restaurant.subscription.plan
        plan.max_branches = 2; plan.save()
        self.client.post(endpoint,{'action':'create','name':'فرع جديد'})
        second = self.restaurant.branches.get(name='فرع جديد')
        self.client.post(reverse('select_branch',args=[self.restaurant.slug]),{'branch_id':second.pk})
        self.client.post(reverse('staff',args=[self.restaurant.slug]),{
            'action':'create','email':'newstaff@example.com','role':'waiter','password':'StrongPass!123'})
        member = Membership.objects.get(user__username='newstaff@example.com',restaurant=self.restaurant)
        self.assertEqual(list(member.branches.values_list('id',flat=True)),[second.pk])

    def test_branch_selection_scopes_pages_mutations_and_export(self):
        second = Branch.objects.create(restaurant=self.restaurant, name='الفرع الثاني')
        second_category = Category.objects.create(branch=second, name='وجبات ثانية')
        second_item = MenuItem.objects.create(category=second_category, name='صنف ثاني', price=Decimal('30.00'))
        second_table = DiningTable.objects.create(branch=second, name='طاولة ثانية')
        first_order = create_order(self.table, self.payload())
        second_order = create_order(second_table, {'idempotency_key':str(uuid.uuid4()), 'lines':[{'item_id':second_item.pk,'quantity':1,'options':[]}]})
        self.client.login(username='owner@example.com',password='StrongPass!123')
        self.assertEqual(self.client.post(reverse('select_branch',args=[self.restaurant.slug]),{'branch_id':second.pk}).status_code,302)
        self.assertContains(self.client.get(reverse('manage_menu',args=[self.restaurant.slug])),'صنف ثاني')
        self.assertNotContains(self.client.get(reverse('manage_menu',args=[self.restaurant.slug])),'طبق')
        kitchen = self.client.get(reverse('kitchen',args=[self.restaurant.slug]))
        self.assertContains(kitchen,f'طلب #{second_order.pk}')
        self.assertNotContains(kitchen,f'طلب #{first_order.pk}')
        export_response = self.client.get(reverse('reports',args=[self.restaurant.slug])+'?export=csv')
        self.assertTrue(export_response.streaming)
        export = b''.join(export_response.streaming_content).decode('utf-8-sig')
        self.assertIn(str(second_order.pk),export)
        self.assertNotIn(f'{first_order.pk},',export)
        self.assertEqual(self.client.post(reverse('order_status_change',args=[self.restaurant.slug,first_order.pk]),{'status':'accepted'}).status_code,404)
        self.assertEqual(self.client.get(reverse('table_qr',args=[self.restaurant.slug,self.table.pk])).status_code,404)

    def test_staff_cannot_switch_to_unassigned_branch(self):
        second = Branch.objects.create(restaurant=self.restaurant, name='الفرع الثاني')
        waiter = User.objects.create_user(username='branchwaiter@example.com',password='StrongPass!123')
        membership = Membership.objects.create(user=waiter,restaurant=self.restaurant,role='waiter')
        membership.branches.add(self.branch)
        self.client.login(username=waiter.username,password='StrongPass!123')
        self.assertEqual(self.client.post(reverse('select_branch',args=[self.restaurant.slug]),{'branch_id':second.pk}).status_code,404)
        self.assertEqual(self.client.get(reverse('dashboard',args=[self.restaurant.slug])).status_code,200)

    def test_disabling_staff_revokes_access_and_login(self):
        waiter = User.objects.create_user(username='disabled@example.com',password='StrongPass!123')
        member = Membership.objects.create(user=waiter,restaurant=self.restaurant,role='waiter')
        member.branches.add(self.branch)
        self.client.login(username='owner@example.com',password='StrongPass!123')
        response = self.client.post(reverse('staff',args=[self.restaurant.slug]),{'action':'toggle','membership_id':member.pk})
        self.assertEqual(response.status_code,302)
        member.refresh_from_db()
        self.assertFalse(member.active)
        self.client.logout()
        self.client.post(reverse('login'),{'email':waiter.username,'password':'StrongPass!123'})
        self.assertFalse('_auth_user_id' in self.client.session)

    def test_password_reset_does_not_enumerate_accounts_and_changes_password(self):
        response = self.client.post(reverse('password_reset'), {'email':'missing@example.com'})
        self.assertRedirects(response, reverse('password_reset_done'))
        self.assertEqual(len(mail.outbox), 0)
        response = self.client.post(reverse('password_reset'), {'email':self.owner.email})
        self.assertRedirects(response, reverse('password_reset_done'))
        self.assertEqual(len(mail.outbox), 1)
        link = re.search(r'https?://testserver(/reset/[^\s]+)', mail.outbox[0].body)
        self.assertIsNotNone(link)
        response = self.client.get(link.group(1))
        self.assertEqual(response.status_code, 302)
        response = self.client.post(response['Location'], {'new_password1':'NewStrongPass!456','new_password2':'NewStrongPass!456'})
        self.assertRedirects(response, reverse('password_reset_complete'))
        self.owner.refresh_from_db()
        self.assertTrue(self.owner.check_password('NewStrongPass!456'))

    def test_order_actions_match_employee_role(self):
        order = create_order(self.table,self.payload())
        waiter = User.objects.create_user(username='waiteractions@example.com',password='StrongPass!123')
        waiter_member = Membership.objects.create(user=waiter,restaurant=self.restaurant,role='waiter')
        waiter_member.branches.add(self.branch)
        self.client.login(username=waiter.username,password='StrongPass!123')
        page = self.client.get(reverse('manage_orders',args=[self.restaurant.slug]))
        accept_action = '<input type="hidden" name="status" value="accepted">'
        self.assertNotContains(page, accept_action, html=True)
        self.client.logout()
        cook = User.objects.create_user(username='cook@example.com',password='StrongPass!123')
        cook_member = Membership.objects.create(user=cook,restaurant=self.restaurant,role='kitchen')
        cook_member.branches.add(self.branch)
        self.client.login(username=cook.username,password='StrongPass!123')
        self.assertContains(self.client.get(reverse('manage_orders',args=[self.restaurant.slug])), accept_action, html=True)

    def test_menu_edit_keeps_order_snapshot_and_hides_category(self):
        old_order = create_order(self.table,self.payload())
        self.client.login(username='owner@example.com',password='StrongPass!123')
        endpoint = reverse('manage_menu',args=[self.restaurant.slug])
        response = self.client.post(endpoint,{'action':'item_edit','item_id':self.item.pk,
            'category':self.category.pk,'name':'طبق جديد','description':'وصف جديد','price':'125.50'})
        self.assertEqual(response.status_code,302)
        self.item.refresh_from_db()
        self.assertEqual(self.item.price,Decimal('125.50'))
        self.assertEqual(old_order.lines.get().item_name,'طبق')
        self.assertEqual(old_order.lines.get().unit_price,Decimal('100.00'))
        self.client.post(endpoint,{'action':'category_toggle','category_id':self.category.pk})
        self.assertNotContains(self.client.get(reverse('public_menu',args=[self.table.public_token])),'طبق جديد')
        with self.assertRaises(ValidationError): create_order(self.table,self.payload())

    def test_unavailable_modifier_and_foreign_menu_edit(self):
        group = ModifierGroup.objects.create(item=self.item,name='إضافات',max_select=1)
        option = ModifierOption.objects.create(group=group,name='جبن',price=Decimal('10.00'))
        foreign_item = MenuItem.objects.create(category=Category.objects.create(branch=self.other_branch,name='أخرى'),name='أجنبي',price=10)
        self.client.login(username='owner@example.com',password='StrongPass!123')
        endpoint = reverse('manage_menu',args=[self.restaurant.slug])
        response = self.client.post(endpoint,{'action':'item_edit','item_id':foreign_item.pk,
            'category':self.category.pk,'name':'تغيير','price':'20'})
        self.assertEqual(response.status_code,404)
        self.client.post(endpoint,{'action':'modifier_toggle','option_id':option.pk})
        option.refresh_from_db()
        self.assertFalse(option.available)
        with self.assertRaises(ValidationError): create_order(self.table,self.payload([option.pk]))

    def test_modifier_edits_keep_order_snapshot_and_validate_required_options(self):
        group = ModifierGroup.objects.create(item=self.item, name='إضافات', min_select=1, max_select=1)
        option = ModifierOption.objects.create(group=group, name='جبن', price=Decimal('10.00'))
        order = create_order(self.table, self.payload([option.pk]))
        self.client.login(username=self.owner.username, password='StrongPass!123')
        endpoint = reverse('manage_menu', args=[self.restaurant.slug])
        self.assertContains(self.client.get(endpoint), 'حفظ المجموعة')
        self.client.post(endpoint, {'action': 'modifier_option_edit', 'option_id': option.pk,
                                    'name': 'جبن إضافي', 'price': '20.00'})
        option.refresh_from_db()
        self.assertEqual((option.name, option.price), ('جبن إضافي', Decimal('20.00')))
        line = order.lines.get()
        self.assertEqual(line.modifiers[0], {'name': 'جبن', 'price': '10.00'})
        self.assertEqual(line.unit_price, Decimal('110.00'))
        self.client.post(endpoint, {'action': 'modifier_toggle', 'option_id': option.pk})
        option.refresh_from_db()
        self.assertTrue(option.available)
        self.client.post(endpoint, {'action': 'modifier_group_edit', 'group_id': group.pk,
                                    'name': 'إضافات جديدة', 'min_select': '2', 'max_select': '2'})
        group.refresh_from_db()
        self.assertEqual(group.min_select, 1)
        self.client.post(endpoint, {'action': 'modifier_group_edit', 'group_id': group.pk,
                                    'name': 'إضافات جديدة', 'min_select': '0', 'max_select': '1'})
        group.refresh_from_db()
        self.assertEqual((group.name, group.min_select), ('إضافات جديدة', 0))
        self.client.post(endpoint, {'action': 'modifier_toggle', 'option_id': option.pk})
        option.refresh_from_db()
        self.assertFalse(option.available)
        self.assertTrue(AuditLog.objects.filter(restaurant=self.restaurant,
                        action='modifier.option.edit').exists())

    def test_modifier_edit_scopes_branch_and_rejects_invalid_price(self):
        group = ModifierGroup.objects.create(item=self.item, name='إضافات')
        option = ModifierOption.objects.create(group=group, name='جبن', price=Decimal('10.00'))
        self.client.login(username=self.other.username, password='StrongPass!123')
        endpoint = reverse('manage_menu', args=[self.other_restaurant.slug])
        self.assertEqual(self.client.post(endpoint, {'action': 'modifier_group_edit',
            'group_id': group.pk, 'name': 'اختراق', 'min_select': '0', 'max_select': '1'}).status_code, 404)
        self.assertEqual(self.client.post(endpoint, {'action': 'modifier_option_edit',
            'option_id': option.pk, 'name': 'اختراق', 'price': '1'}).status_code, 404)
        self.client.login(username=self.owner.username, password='StrongPass!123')
        endpoint = reverse('manage_menu', args=[self.restaurant.slug])
        self.client.post(endpoint, {'action': 'modifier_option_edit', 'option_id': option.pk,
                                    'name': 'غير صالح', 'price': 'NaN'})
        option.refresh_from_db()
        self.assertEqual((option.name, option.price), ('جبن', Decimal('10.00')))

    def test_modifier_order_and_delete_preserve_order_snapshot(self):
        group = ModifierGroup.objects.create(item=self.item, name='إضافات', min_select=1, max_select=2)
        first = ModifierOption.objects.create(group=group, name='جبن', price=Decimal('10.00'))
        second = ModifierOption.objects.create(group=group, name='صلصة', price=Decimal('5.00'))
        order = create_order(self.table, self.payload([first.pk]))
        self.client.login(username=self.owner.username, password='StrongPass!123')
        endpoint = reverse('manage_menu', args=[self.restaurant.slug])
        self.client.post(endpoint, {'action': 'modifier_option_edit', 'option_id': second.pk,
                                    'name': 'صلصة', 'price': '5.00', 'sort_order': '0'})
        self.client.post(endpoint, {'action': 'modifier_option_edit', 'option_id': first.pk,
                                    'name': 'جبن', 'price': '10.00', 'sort_order': '2'})
        options = self.client.get(reverse('public_menu', args=[self.table.public_token])).context['items_json'][0]['groups'][0]['options']
        self.assertEqual([option['name'] for option in options], ['صلصة', 'جبن'])
        self.client.post(endpoint, {'action': 'modifier_option_delete', 'option_id': first.pk})
        self.assertFalse(ModifierOption.objects.filter(pk=first.pk).exists())
        self.assertEqual(order.lines.get().modifiers[0], {'name': 'جبن', 'price': '10.00'})
        self.client.post(endpoint, {'action': 'modifier_option_delete', 'option_id': second.pk})
        self.assertTrue(ModifierOption.objects.filter(pk=second.pk).exists())
        self.assertTrue(AuditLog.objects.filter(restaurant=self.restaurant,
                        action='modifier.option.delete').exists())

    def test_modifier_sort_and_delete_are_scoped_and_validated(self):
        group = ModifierGroup.objects.create(item=self.item, name='إضافات')
        option = ModifierOption.objects.create(group=group, name='جبن', price=Decimal('10.00'))
        self.client.login(username=self.other.username, password='StrongPass!123')
        foreign_endpoint = reverse('manage_menu', args=[self.other_restaurant.slug])
        self.assertEqual(self.client.post(foreign_endpoint, {'action': 'modifier_option_delete',
                         'option_id': option.pk}).status_code, 404)
        self.client.login(username=self.owner.username, password='StrongPass!123')
        endpoint = reverse('manage_menu', args=[self.restaurant.slug])
        self.client.post(endpoint, {'action': 'modifier_option_edit', 'option_id': option.pk,
                                    'name': 'تغيير', 'price': '1.00', 'sort_order': '-1'})
        option.refresh_from_db()
        self.assertEqual((option.name, option.sort_order), ('جبن', 0))

    def test_kitchen_feed_changes_and_rejects_other_roles(self):
        self.client.login(username='owner@example.com',password='StrongPass!123')
        endpoint = reverse('kitchen_feed',args=[self.restaurant.slug])
        before = self.client.get(endpoint)
        self.assertEqual(before.status_code,200)
        self.assertEqual(before.json()['count'],0)
        self.assertEqual(before['Cache-Control'],'no-store')
        order = create_order(self.table,self.payload())
        after = self.client.get(endpoint)
        self.assertEqual(after.json()['count'],1)
        self.assertNotEqual(before.json()['signature'],after.json()['signature'])
        advance_order(order,self.owner,'accepted')
        self.assertNotEqual(after.json()['signature'],self.client.get(endpoint).json()['signature'])
        self.client.logout()
        waiter = User.objects.create_user(username='feedwaiter@example.com',password='StrongPass!123')
        member = Membership.objects.create(user=waiter,restaurant=self.restaurant,role='waiter')
        member.branches.add(self.branch)
        self.client.login(username=waiter.username,password='StrongPass!123')
        self.assertEqual(self.client.get(endpoint).status_code,403)

    def test_downgrade_preserves_table_and_blocks_reactivation_above_limit(self):
        second = DiningTable.objects.create(branch=self.branch,name='طاولة إضافية',active=False)
        plan = self.restaurant.subscription.plan
        plan.max_tables = 1; plan.save()
        self.client.login(username='owner@example.com',password='StrongPass!123')
        endpoint = reverse('manage_tables',args=[self.restaurant.slug])
        response = self.client.post(endpoint,{'action':'toggle','table_id':second.pk})
        self.assertEqual(response.status_code,302)
        second.refresh_from_db()
        self.assertFalse(second.active)
        self.assertEqual(DiningTable.objects.filter(branch=self.branch).count(),2)

    def test_plan_preview_counts_every_branch_without_changing_subscription(self):
        second = Branch.objects.create(restaurant=self.restaurant, name='فرع آخر')
        DiningTable.objects.create(branch=self.branch, name='معطلة', active=False)
        DiningTable.objects.create(branch=second, name='طاولة أخرى')
        Category.objects.create(branch=second, name='تصنيف آخر')
        preview = Plan.objects.create(name='باقة أصغر', max_branches=1, max_tables=1,
                                      max_items=1, max_staff=1)
        self.client.login(username=self.owner.username, password='StrongPass!123')
        url = reverse('restaurant_settings', args=[self.restaurant.slug])
        response = self.client.get(url, {'preview_plan': preview.pk})
        self.assertEqual(response.status_code, 200)
        data = response.context['plan_preview']
        self.assertEqual(data['branch_excess'], 1)
        self.assertEqual([(branch.name, branch.table_excess) for branch in data['branches']],
                         [(self.branch.name, 1), (second.name, 0)])
        self.assertContains(response, 'يسري فور حفظه')
        self.restaurant.subscription.refresh_from_db()
        self.assertNotEqual(self.restaurant.subscription.plan_id, preview.pk)
        self.assertEqual(self.client.get(url, {'preview_plan': 'bad'}).status_code, 200)

    def test_plan_preview_is_only_for_owner_and_scoped_to_restaurant(self):
        preview = Plan.objects.create(name='باقة معاينة', max_branches=1)
        Category.objects.create(branch=self.other_branch, name='أجنبية')
        DiningTable.objects.create(branch=self.other_branch, name='أجنبية')
        manager = User.objects.create_user(username='preview-manager@example.com', password='StrongPass!123')
        Membership.objects.create(user=manager, restaurant=self.restaurant, role='manager')
        url = reverse('restaurant_settings', args=[self.restaurant.slug])
        self.client.login(username=manager.username, password='StrongPass!123')
        self.assertIsNone(self.client.get(url, {'preview_plan': preview.pk}).context['plan_preview'])
        self.client.login(username=self.owner.username, password='StrongPass!123')
        response = self.client.get(url, {'preview_plan': preview.pk})
        self.assertEqual(response.context['plan_preview']['branch_count'], 1)
        self.assertNotContains(response, 'أجنبية')

    def test_staff_order_multiple_items_modifiers_and_tenant_scope(self):
        second = MenuItem.objects.create(category=self.category,name='مشروب',price=Decimal('50.00'))
        group = ModifierGroup.objects.create(item=self.item,name='الحجم',min_select=1,max_select=1)
        option = ModifierOption.objects.create(group=group,name='كبير',price=Decimal('20.00'))
        self.client.login(username='owner@example.com',password='StrongPass!123')
        endpoint = reverse('staff_order',args=[self.restaurant.slug])
        self.assertContains(self.client.get(endpoint),'اختر طاولة الطلب')
        payload = {'table_id':self.table.pk,'idempotency_key':str(uuid.uuid4()),'note':'بدون ملح',
                   'lines':[{'item_id':self.item.pk,'quantity':1,'options':[option.pk]},
                            {'item_id':second.pk,'quantity':2,'options':[]}]}
        response = self.client.post(endpoint,data=json.dumps(payload),content_type='application/json')
        self.assertEqual(response.status_code,200)
        order = Order.objects.get(table=self.table)
        self.assertEqual(order.source,'staff')
        self.assertEqual(order.total,Decimal('220.00'))
        self.assertEqual(order.lines.count(),2)
        self.client.post(endpoint,data=json.dumps(payload),content_type='application/json')
        self.assertEqual(Order.objects.count(),1)
        foreign_table = DiningTable.objects.create(branch=self.other_branch,name='غريبة')
        payload['table_id'] = foreign_table.pk
        payload['idempotency_key'] = str(uuid.uuid4())
        self.assertEqual(self.client.post(endpoint,data=json.dumps(payload),content_type='application/json').status_code,404)

    def test_staff_additional_order_is_separate_and_table_scoped(self):
        original = create_order(self.table, self.payload())
        advance_order(original, self.owner, 'accepted')
        advance_order(original, self.owner, 'preparing')
        self.client.login(username=self.owner.username, password='StrongPass!123')
        list_url = reverse('manage_orders', args=[self.restaurant.slug])
        add_url = reverse('staff_order', args=[self.restaurant.slug])
        self.assertContains(self.client.get(list_url), f'{add_url}?table={self.table.pk}')
        filtered = self.client.get(list_url, {'table': self.table.pk})
        self.assertEqual(len(filtered.context['orders']), 1)
        self.assertContains(self.client.get(add_url, {'table': self.table.pk}),
                            f'<option value="{self.table.pk}" selected>')
        payload = self.payload()
        payload['table_id'] = self.table.pk
        response = self.client.post(add_url, data=json.dumps(payload), content_type='application/json')
        self.assertEqual(response.status_code, 200)
        additional = Order.objects.exclude(pk=original.pk).get()
        original.refresh_from_db()
        self.assertEqual(original.status, 'preparing')
        self.assertEqual(additional.status, 'new')
        self.assertEqual(additional.session_token, original.session_token)
        self.assertEqual(additional.table_id, original.table_id)
        self.assertEqual(len(self.client.get(list_url, {'table': self.table.pk}).context['orders']), 2)
        foreign = DiningTable.objects.create(branch=self.other_branch, name='أجنبية')
        self.assertEqual(self.client.get(list_url, {'table': foreign.pk}).status_code, 404)
        self.assertEqual(self.client.get(add_url, {'table': foreign.pk}).status_code, 404)
        self.assertEqual(self.client.get(list_url, {'table': 'bad'}).status_code, 404)
        self.assertEqual(self.client.get(add_url, {'table': '9'*100}).status_code, 404)

    def test_order_list_paginates_without_losing_table_filter(self):
        for _ in range(27):
            Order.objects.create(branch=self.branch, table=self.table,
                idempotency_key=uuid.uuid4(), request_hash='test', total=Decimal('0.00'))
        self.client.login(username=self.owner.username, password='StrongPass!123')
        endpoint = reverse('manage_orders', args=[self.restaurant.slug])
        first = self.client.get(endpoint, {'table': self.table.pk})
        self.assertEqual(first.context['orders'].paginator.count, 27)
        self.assertEqual(len(first.context['orders']), 25)
        self.assertContains(first, f'?page=2&amp;table={self.table.pk}')
        second = self.client.get(endpoint, {'table': self.table.pk, 'page': 2})
        self.assertEqual(len(second.context['orders']), 2)
        self.assertEqual(second.context['orders'].number, 2)

    def test_order_status_and_table_filters_work_together(self):
        dining_room = DiningTable.objects.create(branch=self.branch, name='Dining room')
        for table, status in [(self.table, 'new'), (self.table, 'ready'), (dining_room, 'ready')]:
            Order.objects.create(branch=self.branch, table=table, status=status, total=Decimal('1.00'),
                                 idempotency_key=uuid.uuid4(), request_hash='test')
        self.client.force_login(self.owner)
        endpoint = reverse('manage_orders', args=[self.restaurant.slug])
        page = self.client.get(endpoint, {'table': self.table.pk, 'status': 'ready', 'lang': 'en'})
        self.assertEqual([(o.table_id, o.status) for o in page.context['orders']], [(self.table.pk, 'ready')])
        empty = self.client.get(endpoint, {'status': 'accepted', 'lang': 'en'})
        self.assertContains(empty, 'No orders match these filters')
        self.assertEqual(self.client.get(endpoint, {'status': 'unknown'}).context['orders'].paginator.count, 3)

    def test_manual_payment_is_separate_and_scoped(self):
        order = create_order(self.table,self.payload())
        foreign_table = DiningTable.objects.create(branch=self.other_branch,name='بعيدة')
        foreign_category = Category.objects.create(branch=self.other_branch,name='منيو')
        foreign_item = MenuItem.objects.create(category=foreign_category,name='صنف',price=10)
        foreign_order = create_order(foreign_table,{'idempotency_key':str(uuid.uuid4()),'lines':[{'item_id':foreign_item.pk,'quantity':1,'options':[]}]})
        self.client.login(username='owner@example.com',password='StrongPass!123')
        endpoint = reverse('order_payment_change',args=[self.restaurant.slug,order.pk])
        self.assertEqual(self.client.post(endpoint).status_code,302)
        order.refresh_from_db()
        self.assertEqual(order.status,'new')
        self.assertEqual(order.payment_status,'paid')
        self.assertIsNotNone(order.paid_at)
        self.assertEqual(self.client.post(reverse('order_payment_change',args=[self.restaurant.slug,foreign_order.pk])).status_code,404)
        report = self.client.get(reverse('reports',args=[self.restaurant.slug]))
        self.assertEqual(report.context['paid_value'],Decimal('200.00'))
        self.assertEqual(report.context['total'],1)

    def test_cancellation_requires_reason_and_preserves_history(self):
        order = create_order(self.table,self.payload())
        self.client.login(username='owner@example.com',password='StrongPass!123')
        endpoint = reverse('order_status_change',args=[self.restaurant.slug,order.pk])
        self.client.post(endpoint,{'status':'cancelled','reason':''})
        order.refresh_from_db()
        self.assertEqual(order.status,'new')
        self.client.post(endpoint,{'status':'cancelled','reason':'طلب مكرر'})
        order.refresh_from_db()
        self.assertEqual(order.status,'cancelled')
        self.assertEqual(order.history.last().reason,'طلب مكرر')

    def test_operational_timing_reports_use_actual_transitions(self):
        self.client.login(username='owner@example.com',password='StrongPass!123')
        endpoint = reverse('reports',args=[self.restaurant.slug])
        empty = self.client.get(endpoint)
        self.assertEqual(empty.context['accepted_time'],'لا توجد بيانات')
        order = create_order(self.table,self.payload())
        for status in ('accepted','preparing','ready'):
            advance_order(order,self.owner,status)
        call = create_service_call(self.table,'waiter')
        advance_call(call,self.owner,'acknowledged')
        report = self.client.get(endpoint)
        self.assertIn('ث',report.context['accepted_time'])
        self.assertIn('ث',report.context['ready_time'])
        self.assertIn('ث',report.context['call_response_time'])

    def test_station_timing_report_uses_line_history_and_branch_scope(self):
        grill = KitchenStation.objects.create(branch=self.branch, name='الشواية')
        drinks = KitchenStation.objects.create(branch=self.branch, name='المشروبات')
        foreign = KitchenStation.objects.create(branch=self.other_branch, name='محطة أخرى')
        self.item.station = grill
        self.item.save(update_fields=['station'])
        drink = MenuItem.objects.create(category=self.category, name='عصير', price=Decimal('20.00'), station=drinks)
        payload = self.payload()
        payload['lines'].append({'item_id': drink.pk, 'quantity': 1, 'options': []})
        order = create_order(self.table, payload)
        advance_order(order, self.owner, 'accepted')
        advance_order(order, self.owner, 'preparing')
        for line in order.lines.all():
            advance_order_line(line, self.owner, 'preparing')
            advance_order_line(line, self.owner, 'ready')
        self.client.login(username=self.owner.username, password='StrongPass!123')
        endpoint = reverse('reports', args=[self.restaurant.slug])
        whole = self.client.get(endpoint)
        self.assertEqual(whole.context['station_samples'], 2)
        self.assertIn('ث', whole.context['station_time'])
        selected = self.client.get(endpoint, {'station': grill.pk})
        self.assertEqual(selected.context['station_samples'], 1)
        self.assertContains(selected, 'الشواية')
        self.assertEqual(self.client.get(endpoint, {'station': drinks.pk}).context['station_samples'], 1)
        self.assertEqual(self.client.get(endpoint, {'station': 'unassigned'}).context['station_samples'], 0)
        self.assertEqual(self.client.get(endpoint, {'station': foreign.pk}).status_code, 404)
        self.assertEqual(self.client.get(endpoint, {'station': 'bad'}).status_code, 404)
        Order.objects.filter(pk=order.pk).update(created_at=timezone.now()-timedelta(days=5))
        today = timezone.localdate().isoformat()
        self.assertEqual(self.client.get(endpoint, {'station': grill.pk, 'from': today,
            'to': today}).context['station_samples'], 0)

    def test_platform_actions_require_superuser_and_audited_reason(self):
        endpoint = reverse('platform_update_restaurant',args=[self.restaurant.pk])
        self.client.login(username='owner@example.com',password='StrongPass!123')
        self.assertEqual(self.client.get(reverse('platform_dashboard')).status_code,403)
        self.assertEqual(self.client.post(endpoint,{'action':'toggle','reason':'اختبار'}).status_code,403)
        self.client.logout()
        admin = User.objects.create_superuser(username='platform@example.com',email='platform@example.com',password='StrongPass!123')
        self.client.login(username=admin.username,password='StrongPass!123')
        self.assertContains(self.client.get(reverse('platform_dashboard')),'مطعم سفرة')
        self.client.post(endpoint,{'action':'toggle','reason':''})
        self.restaurant.refresh_from_db()
        self.assertTrue(self.restaurant.active)
        self.client.post(endpoint,{'action':'toggle','reason':'صيانة مجدولة'})
        self.restaurant.refresh_from_db()
        self.assertFalse(self.restaurant.active)
        self.assertTrue(AuditLog.objects.filter(restaurant=self.restaurant,action='platform.restaurant.toggle',detail__contains='صيانة مجدولة').exists())
        plan = Plan.objects.get(name='Growth')
        self.client.post(endpoint,{'action':'subscription','plan_id':plan.pk,'status':'active',
            'ends_at': (timezone.localdate() + timedelta(days=30)).isoformat(), 'reason':'تجديد يدوي'})
        self.restaurant.subscription.refresh_from_db()
        self.assertEqual(self.restaurant.subscription.plan_id,plan.pk)
        self.assertEqual(self.restaurant.subscription.status,'active')
        self.assertTrue(AuditLog.objects.filter(restaurant=self.restaurant,action='platform.subscription.update').exists())

    def test_subscription_payment_is_recorded_once_and_visible_only_to_own_owner(self):
        endpoint = reverse('platform_record_payment', args=[self.restaurant.pk])
        data = {'amount': '799.50', 'period_start': '2026-11-01', 'period_end': '2026-11-30',
                'method': 'bank', 'reference': ' transfer-123 '}
        self.client.login(username=self.owner.username, password='StrongPass!123')
        self.assertEqual(self.client.post(endpoint, data).status_code, 403)
        operator = User.objects.create_superuser(username='billing@example.com', email='billing@example.com',
                                                 password='StrongPass!123')
        self.client.login(username=operator.username, password='StrongPass!123')
        original_end = self.restaurant.subscription.ends_at
        self.assertContains(self.client.get(reverse('platform_dashboard')), 'تسجيل تحصيل اشتراك')
        self.assertEqual(self.client.post(endpoint, data).status_code, 302)
        payment = SubscriptionPayment.objects.get(restaurant=self.restaurant)
        self.assertEqual((payment.amount, payment.currency, payment.reference, payment.recorded_by),
                         (Decimal('799.50'), 'EGP', 'TRANSFER-123', operator))
        self.assertEqual(payment.period_start.isoformat(), '2026-11-01')
        self.restaurant.subscription.refresh_from_db()
        self.assertEqual(self.restaurant.subscription.ends_at, original_end)
        self.assertTrue(AuditLog.objects.filter(restaurant=self.restaurant,
                        action='platform.subscription.payment', actor=operator).exists())
        self.client.post(endpoint, {**data, 'reference': 'TRANSFER-123'})
        self.assertEqual(SubscriptionPayment.objects.count(), 1)
        self.client.login(username=self.owner.username, password='StrongPass!123')
        self.assertContains(self.client.get(reverse('restaurant_settings', args=[self.restaurant.slug])), 'TRANSFER-123')
        self.client.login(username=self.other.username, password='StrongPass!123')
        self.assertNotContains(self.client.get(reverse('restaurant_settings', args=[self.other_restaurant.slug])),
                               'TRANSFER-123')

    def test_subscription_payment_rejects_invalid_amount_period_and_reference(self):
        operator = User.objects.create_superuser(username='billing-check@example.com',
            email='billing-check@example.com', password='StrongPass!123')
        self.client.login(username=operator.username, password='StrongPass!123')
        endpoint = reverse('platform_record_payment', args=[self.restaurant.pk])
        valid = {'amount': '10.00', 'period_start': '2026-11-01', 'period_end': '2026-11-30',
                 'method': 'cash', 'reference': 'CASH-1'}
        for changes in ({'amount': '-10'}, {'amount': 'NaN'}, {'amount': '1.001'},
                        {'period_end': '2026-10-31'}, {'method': 'card'}, {'reference': '  '}):
            with self.subTest(changes=changes):
                self.client.post(endpoint, {**valid, **changes})
                self.assertFalse(SubscriptionPayment.objects.exists())

    def test_suspended_restaurant_is_read_only_for_staff(self):
        self.restaurant.active = False
        self.restaurant.save(update_fields=['active'])
        self.client.login(username='owner@example.com',password='StrongPass!123')
        endpoint = reverse('manage_menu',args=[self.restaurant.slug])
        self.assertEqual(self.client.get(endpoint).status_code,200)
        self.assertEqual(self.client.post(endpoint,{'action':'category','name':'جديد'}).status_code,403)
        self.assertFalse(Category.objects.filter(branch=self.branch,name='جديد').exists())
        self.assertEqual(self.client.get(reverse('reports',args=[self.restaurant.slug])+'?export=csv').status_code,200)

    def test_admin_plan_change_requires_reason_and_operational_models_are_read_only(self):
        admin = User.objects.create_superuser(username='adminplan@example.com',email='adminplan@example.com',password='StrongPass!123')
        self.client.login(username=admin.username,password='StrongPass!123')
        self.assertEqual(self.client.get(f'/django-admin/core/restaurant/{self.restaurant.pk}/change/').status_code,200)
        self.assertEqual(self.client.post(f'/django-admin/core/restaurant/{self.restaurant.pk}/change/',{'name':'تغيير'}).status_code,403)
        plan = self.restaurant.subscription.plan
        endpoint = f'/django-admin/core/plan/{plan.pk}/change/'
        self.assertContains(self.client.get(endpoint),'change_reason')
        data = {'name':plan.name,'price_monthly':'0','max_tables':'20','max_items':'50',
                'max_staff':'5','max_branches':'1','max_stations':'1','change_reason':''}
        self.assertEqual(self.client.post(endpoint,data).status_code,200)
        plan.refresh_from_db()
        self.assertEqual(plan.max_tables,10)
        data['change_reason'] = 'تعديل تعاقدي'
        self.assertEqual(self.client.post(endpoint,data).status_code,302)
        plan.refresh_from_db()
        self.assertEqual(plan.max_tables,20)
        self.assertTrue(LogEntry.objects.filter(user=admin,change_message__contains='تعديل تعاقدي').exists())

    def test_login_rate_limit_expires_without_exposing_account(self):
        endpoint = reverse('login')
        for _ in range(8):
            response = self.client.post(endpoint,{'email':self.owner.email,'password':'wrong'})
            self.assertEqual(response.status_code,200)
        response = self.client.post(endpoint,{'email':self.owner.email,'password':'StrongPass!123'})
        self.assertEqual(response.status_code,429)
        self.assertNotIn('_auth_user_id',self.client.session)
        LoginAttempt.objects.update(created_at=timezone.now()-timedelta(minutes=16))
        response = self.client.post(endpoint,{'email':self.owner.email,'password':'StrongPass!123'})
        self.assertEqual(response.status_code,302)

    def test_password_reset_rate_limit_keeps_same_response(self):
        endpoint = reverse('password_reset')
        for _ in range(6):
            response = self.client.post(endpoint,{'email':self.owner.email})
            self.assertRedirects(response,reverse('password_reset_done'))
        self.assertEqual(len(mail.outbox),5)

    def test_onboarding_steps_follow_current_branch_data(self):
        self.client.login(username=self.owner.username, password='StrongPass!123')
        endpoint = reverse('dashboard', args=[self.restaurant.slug])
        response = self.client.get(endpoint)
        self.assertEqual(response.context['setup_done'], 2)
        self.assertContains(response, 'جهّز الفرع الرئيسي')
        self.assertContains(response, 'class="setup-count">2 من 5')
        self.restaurant.description = 'مطبخ مصري'
        self.restaurant.save(update_fields=['description'])
        chef = User.objects.create_user(username='chef@example.com', password='StrongPass!123')
        chef_membership = Membership.objects.create(user=chef, restaurant=self.restaurant, role='kitchen')
        second_branch = Branch.objects.create(restaurant=self.restaurant, name='الثاني')
        chef_membership.branches.add(second_branch)
        self.assertEqual(self.client.get(endpoint).context['setup_done'], 3)
        chef_membership.branches.set([self.branch])
        response = self.client.get(endpoint)
        self.assertEqual(response.context['setup_done'], 4)
        self.assertContains(response, 'افتح شاشة المطبخ')
        self.assertEqual(self.client.get(reverse('kitchen', args=[self.restaurant.slug])).status_code, 200)
        self.branch.refresh_from_db()
        self.assertIsNotNone(self.branch.kitchen_opened_at)
        self.assertEqual(AuditLog.objects.filter(restaurant=self.restaurant,
            action='kitchen.opened').count(), 1)
        self.client.get(reverse('kitchen', args=[self.restaurant.slug]))
        self.assertEqual(AuditLog.objects.filter(restaurant=self.restaurant,
            action='kitchen.opened').count(), 1)
        response = self.client.get(endpoint)
        self.assertEqual(response.context['setup_done'], 5)
        self.assertNotContains(response, 'setup-panel')
        self.item.available = False
        self.item.save(update_fields=['available'])
        self.assertEqual(self.client.get(endpoint).context['setup_done'], 4)

    def test_onboarding_is_hidden_from_non_manager(self):
        waiter = User.objects.create_user(username='waiter@example.com', password='StrongPass!123')
        membership = Membership.objects.create(user=waiter, restaurant=self.restaurant, role='waiter')
        membership.branches.add(self.branch)
        self.client.login(username=waiter.username, password='StrongPass!123')
        response = self.client.get(reverse('dashboard', args=[self.restaurant.slug]))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'setup-panel')

    def test_profile_validation_and_public_contact_opt_in(self):
        self.client.login(username=self.owner.username, password='StrongPass!123')
        endpoint = reverse('restaurant_settings', args=[self.restaurant.slug])
        menu_url = reverse('public_menu', args=[self.table.public_token])
        data = {'name': 'مطعم جديد', 'name_en': 'New Restaurant', 'description': 'وصف جديد',
                'branch_name': 'وسط البلد', 'address': '١ شارع النيل', 'phone': '+20 100 123 4567',
                'opening_hours': 'يوميًا ١٠–٢٢', 'accepting_orders': 'on'}
        response = self.client.post(endpoint, {**data, 'phone': 'javascript:alert(1)'})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'أدخل رقم تواصل صالحًا')
        self.restaurant.refresh_from_db(); self.branch.refresh_from_db()
        self.assertEqual(self.restaurant.name, 'مطعم سفرة')
        self.assertEqual(self.branch.name, 'الفرع الرئيسي')
        self.assertEqual(self.client.post(endpoint, data).status_code, 302)
        self.branch.refresh_from_db(); self.restaurant.refresh_from_db()
        self.assertEqual(self.branch.phone, '+20 100 123 4567')
        self.assertFalse(self.branch.show_contact_on_menu)
        self.assertNotContains(self.client.get(menu_url), '١ شارع النيل')
        self.assertNotContains(self.client.get(menu_url), '+20 100 123 4567')
        self.assertEqual(self.client.post(endpoint, {**data, 'show_contact_on_menu': 'on'}).status_code, 302)
        response = self.client.get(menu_url)
        self.assertContains(response, '١ شارع النيل')
        self.assertContains(response, '+20 100 123 4567')
        self.assertContains(response, 'New Restaurant')
        self.assertTrue(AuditLog.objects.filter(restaurant=self.restaurant, action='restaurant.settings').exists())

    def test_profile_update_is_scoped_to_current_restaurant_and_branch(self):
        self.client.login(username=self.owner.username, password='StrongPass!123')
        second = Branch.objects.create(restaurant=self.restaurant, name='فرع ثان')
        data = {'name': 'سفرة', 'branch_name': 'الفرع الجديد', 'address': 'عنوان محلي',
                'phone': '', 'description': '', 'opening_hours': ''}
        endpoint = reverse('restaurant_settings', args=[self.restaurant.slug])
        self.assertEqual(self.client.post(endpoint, data).status_code, 302)
        second.refresh_from_db(); self.other_branch.refresh_from_db()
        self.assertEqual(second.name, 'فرع ثان')
        self.assertEqual(self.other_branch.address, '')
        self.assertEqual(self.client.post(reverse('restaurant_settings', args=[self.other_restaurant.slug]), data).status_code, 403)

    def test_size_price_and_name_are_snapshotted_with_modifiers(self):
        size = MenuSize.objects.create(item=self.item, name='كبير', price=Decimal('150.00'))
        group = ModifierGroup.objects.create(item=self.item, name='إضافات', min_select=0, max_select=1)
        option = ModifierOption.objects.create(group=group, name='جبن', price=Decimal('20.00'))
        payload = self.payload([option.pk])
        payload['lines'][0]['size_id'] = size.pk
        order = create_order(self.table, payload)
        line = order.lines.get()
        self.assertEqual(order.total, Decimal('340.00'))
        self.assertEqual(line.base_price, Decimal('150.00'))
        self.assertEqual(line.size_name, 'كبير')
        self.assertEqual(line.unit_price, Decimal('170.00'))
        size.name, size.price = 'أكبر', Decimal('200.00')
        size.save(update_fields=['name', 'price'])
        line.refresh_from_db()
        self.assertEqual((line.size_name, line.base_price), ('كبير', Decimal('150.00')))
        self.assertEqual(create_order(self.table, payload).pk, order.pk)

    def test_size_must_belong_to_item_and_remain_available(self):
        size = MenuSize.objects.create(item=self.item, name='صغير', price=Decimal('80.00'))
        foreign_category = Category.objects.create(branch=self.other_branch, name='أطباق أخرى')
        foreign_item = MenuItem.objects.create(category=foreign_category, name='صنف آخر', price=Decimal('25.00'))
        foreign_size = MenuSize.objects.create(item=foreign_item, name='كبير', price=Decimal('40.00'))
        payload = self.payload(); payload['lines'][0]['size_id'] = foreign_size.pk
        with self.assertRaises(ValidationError): create_order(self.table, payload)
        size.available = False; size.save(update_fields=['available'])
        payload['lines'][0]['size_id'] = size.pk
        with self.assertRaises(ValidationError): create_order(self.table, payload)
        payload['lines'][0]['size_id'] = float(size.pk)
        with self.assertRaises(ValidationError): create_order(self.table, payload)
        self.assertEqual(Order.objects.count(), 0)

    def test_size_management_is_tenant_scoped(self):
        self.client.login(username=self.owner.username, password='StrongPass!123')
        endpoint = reverse('manage_menu', args=[self.restaurant.slug])
        self.assertEqual(self.client.post(endpoint, {'action': 'size', 'item_id': self.item.pk,
            'name': 'كبير', 'price': '155.00'}).status_code, 302)
        size = MenuSize.objects.get(item=self.item)
        menu = self.client.get(reverse('public_menu', args=[self.table.public_token]))
        self.assertEqual(menu.context['items_json'][0]['sizes'][0]['name'], 'كبير')
        self.client.post(endpoint, {'action': 'size_edit', 'size_id': size.pk, 'name': 'وسط', 'price': '125.00'})
        size.refresh_from_db()
        self.assertEqual(size.price, Decimal('125.00'))
        self.client.post(endpoint, {'action': 'size_toggle', 'size_id': size.pk})
        size.refresh_from_db()
        self.assertFalse(size.available)
        self.client.login(username=self.other.username, password='StrongPass!123')
        other_endpoint = reverse('manage_menu', args=[self.other_restaurant.slug])
        self.assertEqual(self.client.post(other_endpoint, {'action': 'size_edit', 'size_id': size.pk,
            'name': 'مخترق', 'price': '1'}).status_code, 404)
        size.refresh_from_db()
        self.assertEqual(size.name, 'وسط')

    def test_order_edit_preserves_existing_prices_and_records_revision(self):
        order = create_order(self.table, self.payload())
        old_line = order.lines.get()
        self.item.price = Decimal('150.00'); self.item.save(update_fields=['price'])
        second = MenuItem.objects.create(category=self.category, name='مشروب', price=Decimal('50.00'))
        key = str(uuid.uuid4())
        change = {'edit_key': key, 'expected_version': 0, 'reason': 'تغيير طلب العميل',
                  'existing': [{'id': old_line.pk, 'quantity': 1}],
                  'add': [{'item_id': second.pk, 'quantity': 1, 'options': []}]}
        edited = edit_order(order, self.owner, change)
        self.assertEqual((edited.total, edited.version), (Decimal('150.00'), 1))
        old_line.refresh_from_db()
        self.assertEqual((old_line.unit_price, old_line.line_total), (Decimal('100.00'), Decimal('100.00')))
        revision = OrderRevision.objects.get(order=order)
        self.assertEqual((revision.old_total, revision.new_total), (Decimal('200.00'), Decimal('150.00')))
        self.assertEqual(revision.before[0]['quantity'], 2)
        self.assertEqual(len(revision.after), 2)
        self.assertEqual(edit_order(order, self.owner, change).pk, order.pk)
        self.assertEqual(OrderRevision.objects.filter(order=order).count(), 1)
        with self.assertRaises(ValidationError): edit_order(order, self.owner, {**change, 'reason': 'تغيير آخر'})
        with self.assertRaises(ValidationError): edit_order(order, self.owner, {**change, 'edit_key': str(uuid.uuid4())})

    def test_order_edit_rejects_paid_preparing_and_foreign_lines(self):
        order = create_order(self.table, self.payload())
        foreign_table = DiningTable.objects.create(branch=self.other_branch, name='بعيدة')
        foreign_category = Category.objects.create(branch=self.other_branch, name='منيو')
        foreign_item = MenuItem.objects.create(category=foreign_category, name='غريب', price=10)
        foreign_order = create_order(foreign_table, {'idempotency_key': str(uuid.uuid4()),
            'lines': [{'item_id': foreign_item.pk, 'quantity': 1, 'options': []}]})
        change = {'edit_key': str(uuid.uuid4()), 'expected_version': 0, 'reason': 'تعديل تجريبي',
                  'existing': [{'id': foreign_order.lines.get().pk, 'quantity': 1}], 'add': []}
        with self.assertRaises(ValidationError): edit_order(order, self.owner, change)
        self.assertEqual(order.total, Decimal('200.00'))
        change['existing'] = [{'id': order.lines.get().pk, 'quantity': 1}]
        order.payment_status = 'paid'; order.save(update_fields=['payment_status'])
        with self.assertRaises(ValidationError): edit_order(order, self.owner, change)
        order.payment_status = 'unpaid'; order.status = 'preparing'
        order.save(update_fields=['payment_status', 'status'])
        with self.assertRaises(ValidationError): edit_order(order, self.owner, change)
        self.assertEqual(OrderRevision.objects.count(), 0)

    def test_order_edit_endpoint_scopes_branch_and_refreshes_kitchen(self):
        order = create_order(self.table, self.payload())
        self.client.login(username=self.owner.username, password='StrongPass!123')
        endpoint = reverse('order_edit', args=[self.restaurant.slug, order.pk])
        self.assertContains(self.client.get(endpoint), 'سبب التعديل')
        before = self.client.get(reverse('kitchen_feed', args=[self.restaurant.slug])).json()['signature']
        change = {'edit_key': str(uuid.uuid4()), 'expected_version': 0, 'reason': 'تعديل الكمية',
                  'existing': [{'id': order.lines.get().pk, 'quantity': 1}], 'add': []}
        response = self.client.post(endpoint, data=json.dumps(change), content_type='application/json')
        self.assertEqual(response.status_code, 200)
        order.refresh_from_db()
        self.assertEqual(order.total, Decimal('100.00'))
        self.assertNotEqual(before, self.client.get(reverse('kitchen_feed', args=[self.restaurant.slug])).json()['signature'])
        self.assertEqual(self.client.get(reverse('order_edit', args=[self.other_restaurant.slug, order.pk])).status_code, 403)
        self.client.login(username=self.other.username, password='StrongPass!123')
        self.assertEqual(self.client.get(reverse('order_edit', args=[self.other_restaurant.slug, order.pk])).status_code, 404)

    def test_menu_import_preview_commit_and_replay(self):
        self.client.login(username=self.owner.username, password='StrongPass!123')
        endpoint = reverse('menu_import', args=[self.restaurant.slug])
        template = self.client.get(endpoint + '?template=1')
        self.assertEqual(template.status_code, 200)
        self.assertIn(b'category,name,price,description', template.content)
        content = 'category,name,price,description\nمشروبات,عصير,25.50,طازج\nأطباق,أرز,40.00,\n'
        upload = SimpleUploadedFile('menu.csv', content.encode('utf-8'), content_type='text/csv')
        preview = self.client.post(endpoint, {'action': 'preview', 'file': upload})
        self.assertEqual(preview.status_code, 200)
        batch = preview.context['batch']
        self.assertIsNotNone(batch)
        self.assertEqual(MenuItem.objects.count(), 1)
        commit = self.client.post(endpoint, {'action': 'commit', 'batch': str(batch.token)})
        self.assertEqual(commit.status_code, 302)
        self.assertEqual(MenuItem.objects.filter(category__branch=self.branch).count(), 3)
        self.assertTrue(Category.objects.filter(branch=self.branch, name='مشروبات').exists())
        self.assertEqual(self.client.post(endpoint, {'action': 'commit', 'batch': str(batch.token)}).status_code, 302)
        self.assertEqual(MenuItem.objects.count(), 3)
        self.assertEqual(AuditLog.objects.filter(action='menu.import', restaurant=self.restaurant).count(), 1)

    def test_menu_import_rejects_bad_rows_and_plan_change_atomically(self):
        self.client.login(username=self.owner.username, password='StrongPass!123')
        endpoint = reverse('menu_import', args=[self.restaurant.slug])
        bad = SimpleUploadedFile('bad.csv', b'category,name,price,description\nDrinks,Tea,NaN,\n')
        response = self.client.post(endpoint, {'action': 'preview', 'file': bad})
        self.assertContains(response, 'السعر يجب أن يكون')
        self.assertIsNone(response.context['batch'])
        self.assertEqual(MenuImportBatch.objects.count(), 0)
        content = b'category,name,price,description\nDrinks,Tea,20.00,\nDrinks,Coffee,30.00,\n'
        preview = self.client.post(endpoint, {'action': 'preview', 'file': SimpleUploadedFile('ok.csv', content)})
        batch = preview.context['batch']
        plan = self.restaurant.subscription.plan
        plan.max_items = 2; plan.save(update_fields=['max_items'])
        response = self.client.post(endpoint, {'action': 'commit', 'batch': str(batch.token)})
        self.assertContains(response, 'يتجاوز حد الأصناف')
        self.assertEqual(MenuItem.objects.count(), 1)
        self.assertFalse(Category.objects.filter(branch=self.branch, name='Drinks').exists())
        batch.refresh_from_db()
        self.assertIsNone(batch.committed_at)

    def test_menu_import_batch_cannot_cross_restaurants(self):
        self.client.login(username=self.owner.username, password='StrongPass!123')
        endpoint = reverse('menu_import', args=[self.restaurant.slug])
        upload = SimpleUploadedFile('menu.csv', b'category,name,price,description\nDrinks,Tea,20.00,\n')
        batch = self.client.post(endpoint, {'action': 'preview', 'file': upload}).context['batch']
        self.client.login(username=self.other.username, password='StrongPass!123')
        other_endpoint = reverse('menu_import', args=[self.other_restaurant.slug])
        self.assertEqual(self.client.post(other_endpoint, {'action': 'commit', 'batch': str(batch.token)}).status_code, 404)
        self.assertEqual(MenuItem.objects.count(), 1)

    def test_xlsx_import_previews_then_commits_with_csv_rules(self):
        workbook = Workbook()
        sheet = workbook.active
        sheet.append(['category', 'name', 'price', 'description'])
        sheet.append(['مشروبات', 'عصير', 25.5, 'طازج'])
        buffer = io.BytesIO()
        workbook.save(buffer)
        self.client.login(username=self.owner.username, password='StrongPass!123')
        endpoint = reverse('menu_import', args=[self.restaurant.slug])
        response = self.client.post(endpoint, {'action': 'preview', 'file':
            SimpleUploadedFile('menu.xlsx', buffer.getvalue())})
        self.assertEqual(response.status_code, 200)
        batch = response.context['batch']
        self.assertIsNotNone(batch)
        self.assertEqual(batch.rows[0]['price'], '25.50')
        self.assertEqual(MenuItem.objects.count(), 1)
        self.client.post(endpoint, {'action': 'commit', 'batch': str(batch.token)})
        self.assertEqual(MenuItem.objects.get(name='عصير').price, Decimal('25.50'))

    def test_xlsx_import_rejects_formula_and_bad_workbook(self):
        workbook = Workbook()
        sheet = workbook.active
        sheet.append(['category', 'name', 'price', 'description'])
        sheet.append(['مشروبات', 'عصير', '=10+15', ''])
        buffer = io.BytesIO()
        workbook.save(buffer)
        self.client.login(username=self.owner.username, password='StrongPass!123')
        endpoint = reverse('menu_import', args=[self.restaurant.slug])
        response = self.client.post(endpoint, {'action': 'preview', 'file':
            SimpleUploadedFile('formula.xlsx', buffer.getvalue())})
        self.assertIsNone(response.context['batch'])
        self.assertEqual(MenuImportBatch.objects.count(), 0)
        response = self.client.post(endpoint, {'action': 'preview', 'file':
            SimpleUploadedFile('bad.xlsx', b'not-an-excel-file')})
        self.assertIsNone(response.context['batch'])
        self.assertEqual(MenuItem.objects.count(), 1)

    def test_reports_filter_period_applies_to_metrics_and_csv(self):
        old = create_order(self.table, self.payload())
        Order.objects.filter(pk=old.pk).update(created_at=timezone.now()-timedelta(days=10))
        recent = create_order(self.table, self.payload())
        advance_order(recent, self.owner, 'accepted')
        self.client.login(username=self.owner.username, password='StrongPass!123')
        day = timezone.localdate().isoformat()
        endpoint = reverse('reports', args=[self.restaurant.slug])
        response = self.client.get(endpoint, {'from': day, 'to': day})
        self.assertEqual(response.context['total'], 1)
        self.assertEqual(response.context['accepted_samples'], 1)
        self.assertEqual(response.context['ready_samples'], 0)
        self.assertEqual(response.context['top_items'][0]['quantity'], 2)
        export = self.client.get(endpoint, {'from': day, 'to': day, 'export': 'csv'})
        self.assertEqual(export.status_code, 200)
        content = b''.join(export.streaming_content)
        self.assertIn(str(recent.pk).encode(), content)
        self.assertFalse(any(row.startswith(f'{old.pk},'.encode()) for row in content.splitlines()[1:]))

    def test_csv_export_streams_and_escapes_formula_after_whitespace(self):
        self.table.name = '  =HYPERLINK("bad")'
        self.table.save(update_fields=['name'])
        create_order(self.table, self.payload())
        self.client.login(username=self.owner.username, password='StrongPass!123')
        response = self.client.get(reverse('reports', args=[self.restaurant.slug]), {'export': 'csv'})
        self.assertTrue(response.streaming)
        self.assertEqual(response['Cache-Control'], 'no-store')
        content = b''.join(response.streaming_content)
        self.assertTrue(content.startswith(b'\xef\xbb\xbf'))
        self.assertEqual(content.count(b'\xef\xbb\xbf'), 1)
        rows = list(csv.reader(io.StringIO(content.decode('utf-8-sig'))))
        self.assertEqual(rows[1][1], "'  =HYPERLINK(\"bad\")")

    def test_reports_reject_invalid_period_without_silent_export(self):
        self.client.login(username=self.owner.username, password='StrongPass!123')
        endpoint = reverse('reports', args=[self.restaurant.slug])
        response = self.client.get(endpoint, {'from': '2026-02-30', 'to': '2026-03-01'})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'تعرض الأرقام أدناه جميع الفترات')
        self.assertEqual(self.client.get(endpoint, {'from': '2026-02-30', 'to': '2026-03-01',
            'export': 'csv'}).status_code, 400)

    def test_kitchen_filters_render_only_current_branch_options(self):
        create_order(self.table, self.payload())
        foreign_table = DiningTable.objects.create(branch=self.other_branch, name='طاولة أخرى')
        self.client.login(username=self.owner.username, password='StrongPass!123')
        response = self.client.get(reverse('kitchen', args=[self.restaurant.slug]))
        self.assertContains(response, 'id="kitchenStatus"')
        self.assertContains(response, f'data-table="{self.table.pk}"')
        self.assertNotContains(response, f'<option value="{foreign_table.pk}">{foreign_table.name}</option>')

    def test_station_routing_keeps_line_snapshot_and_feed_scoped(self):
        station = KitchenStation.objects.create(branch=self.branch, name='المشروبات')
        second = MenuItem.objects.create(category=self.category, name='عصير', price=Decimal('20.00'))
        second.station = station; second.save(update_fields=['station'])
        payload = self.payload()
        payload['lines'].append({'item_id': second.pk, 'quantity': 1, 'options': []})
        order = create_order(self.table, payload)
        self.assertEqual(order.lines.get(item=second).station_id, station.pk)
        second.station = None; second.save(update_fields=['station'])
        self.client.login(username=self.owner.username, password='StrongPass!123')
        base = reverse('kitchen', args=[self.restaurant.slug])
        station_page = self.client.get(base, {'station': station.pk})
        self.assertContains(station_page, 'عصير')
        self.assertNotContains(station_page, 'طبق</span>')
        self.assertNotContains(station_page, 'تأكيد الاستلام')
        self.assertEqual(self.client.get(reverse('kitchen_feed', args=[self.restaurant.slug]),
                                         {'station': station.pk}).json()['count'], 1)
        unassigned = self.client.get(base, {'station': 'unassigned'})
        self.assertContains(unassigned, 'طبق')
        self.assertNotContains(unassigned, 'عصير</span>')
        self.assertContains(self.client.get(base), 'صنف جارٍ بلا محطة تحضير')
        self.assertEqual(self.client.get(base, {'station': 'bad'}).status_code, 404)

    def test_station_limit_assignment_and_foreign_station_rejected(self):
        self.client.login(username=self.owner.username, password='StrongPass!123')
        endpoint = reverse('manage_menu', args=[self.restaurant.slug])
        self.client.post(endpoint, {'action': 'station_create', 'name': 'الشواية'})
        station = KitchenStation.objects.get(branch=self.branch)
        self.client.post(endpoint, {'action': 'station_create', 'name': 'المشروبات'})
        self.assertEqual(KitchenStation.objects.filter(branch=self.branch).count(), 1)
        self.client.post(endpoint, {'action': 'station_assign', 'item_id': self.item.pk,
                                    'station_id': station.pk})
        self.item.refresh_from_db()
        self.assertEqual(self.item.station_id, station.pk)
        settings_page = self.client.get(reverse('restaurant_settings', args=[self.restaurant.slug]),
                                        {'preview_plan': self.restaurant.subscription.plan_id})
        self.assertEqual(settings_page.context['plan_preview']['branches'][0].station_count, 1)
        foreign = KitchenStation.objects.create(branch=self.other_branch, name='أجنبية')
        self.assertEqual(self.client.post(endpoint, {'action': 'station_assign',
            'item_id': self.item.pk, 'station_id': foreign.pk}).status_code, 404)
        self.item.refresh_from_db()
        self.assertEqual(self.item.station_id, station.pk)

    def test_station_lines_require_independent_readiness_and_keep_history(self):
        station = KitchenStation.objects.create(branch=self.branch, name='المشروبات')
        drink = MenuItem.objects.create(category=self.category, name='عصير', price=Decimal('20.00'), station=station)
        payload = self.payload()
        payload['lines'].append({'item_id': drink.pk, 'quantity': 1, 'options': []})
        order = create_order(self.table, payload)
        advance_order(order, self.owner, 'accepted')
        advance_order(order, self.owner, 'preparing')
        with self.assertRaises(ValidationError):
            advance_order(order, self.owner, 'ready')
        self.client.login(username=self.owner.username, password='StrongPass!123')
        feed = reverse('kitchen_feed', args=[self.restaurant.slug])
        before = self.client.get(feed, {'station': station.pk}).json()['signature']
        drink_line = order.lines.get(item=drink)
        url = reverse('order_line_status_change', args=[self.restaurant.slug, order.pk, drink_line.pk])
        self.assertContains(self.client.get(reverse('kitchen', args=[self.restaurant.slug]),
            {'station': station.pk}), 'بدء الصنف')
        self.assertEqual(self.client.post(url, {'station': station.pk, 'status': 'preparing'}).status_code, 302)
        self.assertNotEqual(before, self.client.get(feed, {'station': station.pk}).json()['signature'])
        self.assertEqual(self.client.post(url, {'station': station.pk, 'status': 'ready'}).status_code, 302)
        self.assertEqual(list(OrderLinePrepHistory.objects.filter(line=drink_line).values_list(
            'old_status', 'new_status', 'actor_id')),
            [('new', 'preparing', self.owner.pk), ('preparing', 'ready', self.owner.pk)])
        self.assertEqual(self.client.post(url, {'station': station.pk, 'status': 'ready'}).status_code, 302)
        self.assertEqual(OrderLinePrepHistory.objects.filter(line=drink_line).count(), 2)
        with self.assertRaises(ValidationError):
            advance_order(order, self.owner, 'ready')
        food_line = order.lines.get(item=self.item)
        food_url = reverse('order_line_status_change', args=[self.restaurant.slug, order.pk, food_line.pk])
        for status in ('preparing', 'ready'):
            self.client.post(food_url, {'station': 'unassigned', 'status': status})
        advance_order(order, self.owner, 'ready')
        order.refresh_from_db()
        self.assertEqual(order.status, 'ready')
        self.assertEqual(self.client.post(food_url, {'station': 'unassigned', 'status': 'preparing'}).status_code, 302)
        self.assertEqual(OrderLinePrepHistory.objects.filter(line=food_line).count(), 2)

    def test_station_line_update_rejects_wrong_station_and_foreign_branch(self):
        station = KitchenStation.objects.create(branch=self.branch, name='الشواية')
        self.item.station = station
        self.item.save(update_fields=['station'])
        order = create_order(self.table, self.payload())
        line = order.lines.get()
        url = reverse('order_line_status_change', args=[self.restaurant.slug, order.pk, line.pk])
        self.client.login(username=self.owner.username, password='StrongPass!123')
        self.assertEqual(self.client.post(url, {'station': 'unassigned', 'status': 'preparing'}).status_code, 404)
        self.assertEqual(self.client.post(url, {'station': station.pk, 'status': 'preparing'}).status_code, 302)
        self.assertFalse(OrderLinePrepHistory.objects.filter(line=line).exists())
        advance_order(order, self.owner, 'accepted')
        advance_order(order, self.owner, 'preparing')
        foreign_station = KitchenStation.objects.create(branch=self.other_branch, name='أجنبية')
        self.assertEqual(self.client.post(url, {'station': foreign_station.pk, 'status': 'preparing'}).status_code, 404)
        self.client.login(username=self.other.username, password='StrongPass!123')
        self.assertIn(self.client.post(url, {'station': station.pk, 'status': 'preparing'}).status_code, (403, 404))
        self.assertFalse(OrderLinePrepHistory.objects.filter(line=line).exists())

    def test_kitchen_delay_uses_server_time_and_updates_feed(self):
        self.branch.kitchen_delay_minutes = 5
        self.branch.save(update_fields=['kitchen_delay_minutes'])
        order = create_order(self.table, self.payload())
        self.client.login(username=self.owner.username, password='StrongPass!123')
        page_url = reverse('kitchen', args=[self.restaurant.slug])
        feed_url = reverse('kitchen_feed', args=[self.restaurant.slug])
        before = self.client.get(feed_url).json()['signature']
        self.assertNotContains(self.client.get(page_url), 'تجاوز حد التنبيه')
        Order.objects.filter(pk=order.pk).update(created_at=timezone.now()-timedelta(minutes=6))
        self.assertContains(self.client.get(page_url), 'تجاوز حد التنبيه')
        self.assertNotEqual(self.client.get(feed_url).json()['signature'], before)
        for status in ('accepted', 'preparing', 'ready'):
            advance_order(order, self.owner, status)
        self.assertNotContains(self.client.get(page_url), 'تجاوز حد التنبيه')

    def test_kitchen_delay_setting_is_validated_and_scoped(self):
        self.client.login(username=self.owner.username, password='StrongPass!123')
        endpoint = reverse('restaurant_settings', args=[self.restaurant.slug])
        data = {'name': self.restaurant.name, 'branch_name': self.branch.name,
                'kitchen_delay_minutes': '12'}
        self.assertEqual(self.client.post(endpoint, data).status_code, 302)
        self.branch.refresh_from_db()
        self.assertEqual(self.branch.kitchen_delay_minutes, 12)
        self.assertEqual(self.client.post(endpoint, {**data, 'kitchen_delay_minutes': '0'}).status_code, 200)
        self.branch.refresh_from_db()
        self.assertEqual(self.branch.kitchen_delay_minutes, 12)

    @override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
    def test_staff_invitation_new_user_accepts_once_in_own_branch(self):
        self.client.login(username=self.owner.username, password='StrongPass!123')
        endpoint = reverse('staff', args=[self.restaurant.slug])
        response = self.client.post(endpoint, {'action': 'invite', 'email': 'newstaff@example.com',
            'role': 'waiter', 'branch_id': self.branch.pk})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(len(mail.outbox), 1)
        invitation = StaffInvitation.objects.get(email='newstaff@example.com')
        token = re.search(r'/invitation/([^/]+)/', mail.outbox[0].body).group(1)
        self.assertNotEqual(token, invitation.token_hash)
        self.client.logout()
        accept = reverse('accept_staff_invitation', args=[token])
        self.assertContains(self.client.get(accept), 'newstaff@example.com')
        response = self.client.post(accept, {'name': 'ليلى', 'password': 'StrongPass!123'})
        self.assertRedirects(response, reverse('dashboard', args=[self.restaurant.slug]))
        member = Membership.objects.get(user__username='newstaff@example.com', restaurant=self.restaurant)
        self.assertEqual(member.role, 'waiter')
        self.assertEqual(list(member.branches.all()), [self.branch])
        invitation.refresh_from_db()
        self.assertIsNotNone(invitation.accepted_at)
        self.assertEqual(self.client.get(accept).status_code, 410)
        self.assertTrue(AuditLog.objects.filter(restaurant=self.restaurant, action='staff.invite.accept').exists())

    @override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
    def test_existing_user_invitation_preserves_other_restaurant_role(self):
        self.other.email = self.other.username
        self.other.save(update_fields=['email'])
        self.client.login(username=self.owner.username, password='StrongPass!123')
        response = self.client.post(reverse('staff', args=[self.restaurant.slug]), {'action': 'invite',
            'email': self.other.email, 'role': 'cashier', 'branch_id': self.branch.pk})
        self.assertEqual(len(mail.outbox), 1, [str(message) for message in response.wsgi_request._messages])
        token = re.search(r'/invitation/([^/]+)/', mail.outbox[-1].body).group(1)
        accept = reverse('accept_staff_invitation', args=[token])
        self.client.logout()
        response = self.client.get(accept)
        self.assertEqual(response.status_code, 302)
        self.assertIn('/login/?next=', response.url)
        login_response = self.client.post(reverse('login'), {'email': self.other.email,
            'password': 'StrongPass!123', 'next': accept})
        self.assertRedirects(login_response, accept)
        self.assertEqual(self.client.post(accept).status_code, 302)
        self.assertEqual(Membership.objects.get(user=self.other, restaurant=self.restaurant).role, 'cashier')
        self.assertEqual(Membership.objects.get(user=self.other, restaurant=self.other_restaurant).role, 'owner')

    @override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
    def test_existing_user_without_membership_can_log_in_for_valid_invitation_only(self):
        unattached = User.objects.create_user(username='unattached@example.com',
            email='unattached@example.com', password='StrongPass!123')
        self.client.login(username=self.owner.username, password='StrongPass!123')
        self.client.post(reverse('staff', args=[self.restaurant.slug]), {'action': 'invite',
            'email': unattached.email, 'role': 'waiter', 'branch_id': self.branch.pk})
        token = re.search(r'/invitation/([^/]+)/', mail.outbox[-1].body).group(1)
        accept = reverse('accept_staff_invitation', args=[token])
        self.client.logout()
        login_url = reverse('login')
        self.assertEqual(self.client.post(login_url, {'email': unattached.email,
            'password': 'StrongPass!123', 'next': '/'}).status_code, 200)
        self.assertRedirects(self.client.post(login_url, {'email': unattached.email,
            'password': 'StrongPass!123', 'next': accept}), accept)
        self.assertRedirects(self.client.post(accept), reverse('dashboard', args=[self.restaurant.slug]))
        self.assertTrue(Membership.objects.filter(user=unattached, restaurant=self.restaurant).exists())

    @override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
    def test_invitation_permissions_expiry_limit_and_delivery_failure(self):
        manager = User.objects.create_user(username='manager@example.com', password='StrongPass!123')
        Membership.objects.create(user=manager, restaurant=self.restaurant, role='manager')
        endpoint = reverse('staff', args=[self.restaurant.slug])
        self.client.login(username=manager.username, password='StrongPass!123')
        self.client.post(endpoint, {'action': 'invite', 'email': 'bad@example.com',
            'role': 'manager', 'branch_id': self.branch.pk})
        self.assertFalse(StaffInvitation.objects.filter(email='bad@example.com').exists())
        self.client.post(endpoint, {'action': 'create', 'email': 'direct@example.com',
            'role': 'manager', 'password': 'StrongPass!123'})
        self.assertFalse(Membership.objects.filter(user__username='direct@example.com').exists())
        self.client.post(endpoint, {'action': 'invite', 'email': 'bad@example.com',
            'role': 'waiter', 'branch_id': self.other_branch.pk})
        self.assertFalse(StaffInvitation.objects.filter(email='bad@example.com').exists())
        with patch('core.views.send_mail', side_effect=OSError('mail unavailable')):
            self.client.post(endpoint, {'action': 'invite', 'email': 'bad@example.com',
                'role': 'waiter', 'branch_id': self.branch.pk})
        self.assertFalse(StaffInvitation.objects.filter(email='bad@example.com').exists())
        plan = self.restaurant.subscription.plan
        plan.max_staff = 2; plan.save(update_fields=['max_staff'])
        self.client.post(endpoint, {'action': 'invite', 'email': 'full@example.com',
            'role': 'waiter', 'branch_id': self.branch.pk})
        self.assertFalse(StaffInvitation.objects.filter(email='full@example.com').exists())
        plan.max_staff = 5; plan.save(update_fields=['max_staff'])
        self.client.post(endpoint, {'action': 'invite', 'email': 'pending@example.com',
            'role': 'waiter', 'branch_id': self.branch.pk})
        invitation = StaffInvitation.objects.get(email='pending@example.com')
        token = re.search(r'/invitation/([^/]+)/', mail.outbox[-1].body).group(1)
        invitation.expires_at = timezone.now()-timedelta(seconds=1)
        invitation.save(update_fields=['expires_at'])
        self.client.logout()
        self.assertEqual(self.client.get(reverse('accept_staff_invitation', args=[token])).status_code, 410)

    @override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
    def test_revoked_invitation_cannot_be_accepted(self):
        self.client.login(username=self.owner.username, password='StrongPass!123')
        endpoint = reverse('staff', args=[self.restaurant.slug])
        self.client.post(endpoint, {'action': 'invite', 'email': 'revoked@example.com',
            'role': 'kitchen', 'branch_id': self.branch.pk})
        invitation = StaffInvitation.objects.get(email='revoked@example.com')
        token = re.search(r'/invitation/([^/]+)/', mail.outbox[-1].body).group(1)
        self.assertEqual(self.client.post(endpoint, {'action': 'invite_revoke',
            'invitation_id': invitation.pk}).status_code, 302)
        self.client.logout()
        self.assertEqual(self.client.get(reverse('accept_staff_invitation', args=[token])).status_code, 410)
        self.assertFalse(Membership.objects.filter(user__username='revoked@example.com').exists())
