"""Online pickup/delivery: tenant boundaries, fee snapshots, retries and existing operations."""
import json
from datetime import timedelta
from decimal import Decimal
from uuid import uuid4

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from .models import AuditLog, Branch, Category, DiningTable, Membership, MenuItem, Order, Plan, Restaurant, Subscription
from .online import OnlineOrderingForm, create_online_order
from .services import advance_order, create_order, edit_order, record_manual_payment


class OnlineOrderingTests(TestCase):
    def setUp(self):
        self.plan = Plan.objects.create(name='Online QA', max_branches=3)
        self.venue = Restaurant.objects.create(name='مطعم الحي', slug='online-qa', online_ordering_enabled=True,
                                               online_delivery_enabled=True, delivery_fee='12.50')
        self.subscription = Subscription.objects.create(restaurant=self.venue, plan=self.plan, status='active',
                                                        ends_at=timezone.now() + timedelta(days=30))
        self.branch = Branch.objects.create(restaurant=self.venue, name='النيل', address='عنوان الفرع')
        self.second = Branch.objects.create(restaurant=self.venue, name='وسط البلد')
        self.item = MenuItem.objects.create(category=Category.objects.create(branch=self.branch, name='وجبات'),
                                           name='طبق محفوظ', price='30.25')
        self.second_item = MenuItem.objects.create(category=Category.objects.create(branch=self.second, name='Drinks'),
                                                  name='Tea', price='10.00')
        self.other = Restaurant.objects.create(name='Other', slug='other-online', online_ordering_enabled=True)
        Subscription.objects.create(restaurant=self.other, plan=self.plan, status='active',
                                    ends_at=timezone.now() + timedelta(days=30))
        self.other_branch = Branch.objects.create(restaurant=self.other)
        self.owner = User.objects.create_user('online-owner')
        Membership.objects.create(user=self.owner, restaurant=self.venue, role='owner')
        self.endpoint = reverse('place_online_order', args=[self.venue.slug, self.branch.pk])

    def payload(self, **changes):
        data = {'idempotency_key': str(uuid4()), 'lines': [{'item_id': self.item.pk, 'quantity': 2, 'options': []}],
                'fulfillment': 'delivery', 'customer_name': 'ضيف', 'customer_phone': '01012345678',
                'delivery_address': 'شارع النيل، عمارة ١٢، الدور ٢', 'reviewed_total': '73.00',
                'reviewed_delivery_fee': '12.50'}
        data.update(changes)
        return data

    def post(self, data, **kwargs):
        return self.client.post(self.endpoint, json.dumps(data), content_type='application/json', **kwargs)

    def test_delivery_and_pickup_have_real_branch_orders_and_immutable_snapshots(self):
        delivery = create_online_order(self.branch, self.payload())
        pickup = create_online_order(self.branch, self.payload(fulfillment='pickup', reviewed_total='60.50',
                                                              reviewed_delivery_fee='0.00'))
        self.assertEqual((delivery.source, delivery.fulfillment, delivery.table_id, delivery.session_token),
                         ('online', 'delivery', None, None))
        self.assertEqual((delivery.items_subtotal, delivery.delivery_fee, delivery.total),
                         (Decimal('60.50'), Decimal('12.50'), Decimal('73.00')))
        self.assertEqual((pickup.delivery_fee, pickup.total, pickup.delivery_address),
                         (Decimal('0'), Decimal('60.50'), ''))
        self.assertEqual(delivery.history.get().new_status, 'new')
        self.venue.delivery_fee = '50.00'
        self.venue.save(update_fields=['delivery_fee'])
        self.item.name, self.item.price = 'اسم جديد', Decimal('90.00')
        self.item.save(update_fields=['name', 'price'])
        delivery.refresh_from_db()
        self.assertEqual(delivery.total, Decimal('73.00'))
        self.assertEqual((delivery.lines.get().item_name, delivery.lines.get().unit_price), ('طبق محفوظ', Decimal('30.25')))

    def test_retry_after_fee_or_availability_change_returns_same_order_and_conflict_is_rejected(self):
        payload = self.payload()
        order = create_online_order(self.branch, payload)
        Restaurant.objects.filter(pk=self.venue.pk).update(online_ordering_enabled=False, delivery_fee='80.00')
        self.assertEqual(create_online_order(self.branch, payload).pk, order.pk)
        changed = dict(payload, delivery_address='عنوان آخر')
        with self.assertRaises(ValidationError):
            create_online_order(self.branch, changed)
        self.assertEqual(Order.objects.count(), 1)

    def test_only_selected_branch_menu_is_orderable_and_injected_total_or_fee_never_saves(self):
        for changes in ({'reviewed_total': '60.50'}, {'reviewed_delivery_fee': '0.00'},
                        {'lines': [{'item_id': self.second_item.pk, 'quantity': 1, 'options': []}]},
                        {'lines': []}, {'lines': None}, {'note': 'x' * 501}):
            with self.subTest(changes=changes), self.assertRaises(ValidationError):
                create_online_order(self.branch, self.payload(**changes))
        self.assertEqual(Order.objects.count(), 0)
        self.assertEqual(self.client.post(reverse('place_online_order', args=[self.venue.slug, self.other_branch.pk]),
                         json.dumps(self.payload()), content_type='application/json').status_code, 404)

    def test_availability_is_rechecked_on_server_and_existing_tracking_survives_disable(self):
        order = create_online_order(self.branch, self.payload())
        for model, pk, field, value, restore in (
            (Restaurant, self.venue.pk, 'online_ordering_enabled', False, True),
            (Restaurant, self.venue.pk, 'active', False, True),
            (Branch, self.branch.pk, 'active', False, True),
            (Branch, self.branch.pk, 'archived', True, False),
            (Branch, self.branch.pk, 'accepting_orders', False, True),
            (Subscription, self.subscription.pk, 'status', 'expired', 'active'),
            (Subscription, self.subscription.pk, 'ends_at', timezone.now() - timedelta(days=1), timezone.now() + timedelta(days=30)),
        ):
            model.objects.filter(pk=pk).update(**{field: value})
            with self.subTest(field=field), self.assertRaises(ValidationError):
                create_online_order(self.branch, self.payload())
            tracked = self.client.get(reverse('track_order', args=[order.public_token]))
            self.assertEqual(tracked.status_code, 200)
            self.assertIn('no-store', tracked['Cache-Control'])
            model.objects.filter(pk=pk).update(**{field: restore})
        self.assertEqual(Order.objects.count(), 1)
        Subscription.objects.filter(pk=self.subscription.pk).update(status='pending', plan=None, ends_at=None)
        with self.assertRaises(ValidationError):
            create_online_order(self.branch, self.payload())
        self.assertEqual(self.client.get(reverse('online_menu', args=[self.venue.slug])).status_code, 404)

    def test_methods_and_customer_fields_are_validated(self):
        for changes in ({'customer_phone': '123'}, {'customer_phone': '<script>'}, {'customer_name': ''},
                        {'customer_name': ['bad']}, {'delivery_address': ''}, {'fulfillment': 'dine_in'},
                        {'delivery_address': 'x' * 501}, {'customer_phone': '1' * 25}):
            with self.subTest(changes=changes), self.assertRaises(ValidationError):
                create_online_order(self.branch, self.payload(**changes))
        Restaurant.objects.filter(pk=self.venue.pk).update(online_delivery_enabled=False)
        self.assertEqual(self.post(self.payload()).status_code, 400)
        Restaurant.objects.filter(pk=self.venue.pk).update(online_pickup_enabled=False)
        self.assertEqual(self.post(self.payload(fulfillment='pickup', reviewed_total='60.50',
                                                reviewed_delivery_fee='0.00')).status_code, 400)

    def test_settings_only_change_authenticated_restaurant_and_validate_fee_and_methods(self):
        path = reverse('restaurant_settings', args=[self.venue.slug])
        self.client.force_login(self.owner)
        data = {'action': 'online', 'online-online_ordering_enabled': 'on', 'online-online_delivery_enabled': 'on',
                'online-delivery_fee': '18.75', 'restaurant_id': self.other.pk}
        response = self.client.post(path, data)
        self.assertEqual(response.status_code, 302)
        self.venue.refresh_from_db()
        self.other.refresh_from_db()
        self.assertEqual(self.venue.delivery_fee, Decimal('18.75'))
        self.assertEqual(self.other.delivery_fee, 0)
        self.assertEqual(AuditLog.objects.get(action='restaurant.online').restaurant, self.venue)
        for invalid in ('-1', '1.001', 'NaN', '10000000', 'Infinity'):
            self.assertEqual(self.client.post(path, dict(data, **{'online-delivery_fee': invalid})).status_code, 200)
            self.venue.refresh_from_db()
            self.assertEqual(self.venue.delivery_fee, Decimal('18.75'))
        form = OnlineOrderingForm({'online_ordering_enabled': True, 'delivery_fee': '0'})
        self.assertFalse(form.is_valid())
        for role in ('cashier', 'waiter', 'kitchen'):
            user = User.objects.create_user('online-' + role)
            member = Membership.objects.create(user=user, restaurant=self.venue, role=role)
            member.branches.add(self.branch)
            self.client.force_login(user)
            self.assertEqual(self.client.post(path, data).status_code, 403)

    def test_public_menu_respects_enablement_branch_selection_and_branding(self):
        path = reverse('online_menu', args=[self.venue.slug])
        for lang in ('ar', 'en'):
            page = self.client.get(path, {'branch': self.second.pk, 'lang': lang})
            self.assertContains(page, 'Tea')
            self.assertNotContains(page, self.item.name)
            self.assertContains(page, self.venue.name)
            self.assertEqual(page.context['branch'], self.second)
            self.assertEqual(self.client.get(path, {'branch': self.other_branch.pk}).status_code, 404)
        Restaurant.objects.filter(pk=self.venue.pk).update(online_ordering_enabled=False)
        self.assertEqual(self.client.get(path).status_code, 404)
        fresh = Restaurant.objects.create(name='Fresh', slug='fresh-online')
        self.assertFalse(fresh.online_ordering_enabled)

    def test_guest_checkout_uses_csrf_and_limits_source_without_creating_orders_on_error(self):
        client = Client(enforce_csrf_checks=True)
        page = client.get(reverse('online_menu', args=[self.venue.slug]))
        self.assertEqual(page.status_code, 200)
        data = json.dumps(self.payload())
        self.assertEqual(client.post(self.endpoint, data, content_type='application/json').status_code, 403)
        response = client.post(self.endpoint, data, content_type='application/json',
                               HTTP_X_CSRFTOKEN=client.cookies['csrftoken'].value)
        self.assertEqual(response.status_code, 200)
        for _ in range(19):
            self.assertEqual(self.post({}).status_code, 400)
        blocked = self.post({})
        self.assertEqual(blocked.status_code, 429)
        self.assertEqual(blocked['Retry-After'], '60')
        self.assertEqual(Order.objects.count(), 1)

    def test_online_edit_and_collection_preserve_original_delivery_fee(self):
        order = create_online_order(self.branch, self.payload())
        Restaurant.objects.filter(pk=self.venue.pk).update(delivery_fee='99.00')
        payload = {'edit_key': str(uuid4()), 'expected_version': order.version, 'reason': 'Guest changed quantity',
                   'existing': [{'id': order.lines.get().pk, 'quantity': 3}], 'add': []}
        edited = edit_order(order, self.owner, payload)
        self.assertEqual((edited.total, edited.delivery_fee), (Decimal('103.25'), Decimal('12.50')))
        record_manual_payment(edited, self.owner)
        edited.refresh_from_db()
        self.assertEqual(edited.payment_status, 'paid')
        self.client.force_login(self.owner)
        for name in ('manage_orders', 'dashboard', 'kitchen', 'reports'):
            self.assertEqual(self.client.get(reverse(name, args=[self.venue.slug])).status_code, 200)
        for export in ('csv', 'collections'):
            response = self.client.get(reverse('reports', args=[self.venue.slug]), {'export': export, 'lang': 'en'})
            self.assertEqual(response.status_code, 200)
            self.assertIn('103.25', b''.join(response.streaming_content).decode('utf-8-sig'))

    def test_kitchen_has_fulfillment_without_customer_contact_and_order_tracking_is_private(self):
        order = create_online_order(self.branch, self.payload())
        chef = User.objects.create_user('online-chef')
        member = Membership.objects.create(user=chef, restaurant=self.venue, role='kitchen')
        member.branches.add(self.branch)
        self.client.force_login(chef)
        for name in ('kitchen', 'manage_orders'):
            response = self.client.get(reverse(name, args=[self.venue.slug]))
            self.assertContains(response, 'توصيل')
            self.assertNotContains(response, order.customer_phone)
            self.assertNotContains(response, order.delivery_address)
        self.client.logout()
        self.assertEqual(self.client.get(reverse('manage_orders', args=[self.venue.slug])).status_code, 302)
        self.assertEqual(self.client.get(reverse('track_order', args=['unknown'])).status_code, 404)
        for status in ('accepted', 'preparing', 'ready', 'served'):
            order = advance_order(order, self.owner, status)
        page = self.client.get(reverse('track_order', args=[order.public_token]), {'lang': 'en'})
        self.assertContains(page, 'Delivered')

    def test_qr_order_still_requires_session_and_does_not_accept_delivery_metadata(self):
        table = DiningTable.objects.create(branch=self.branch, name='T1')
        payload = self.payload()
        with self.assertRaises(ValidationError):
            create_order(table, payload, require_session=True)
        payload['session_token'] = str(table.session_token)
        payload['reviewed_total'] = '60.50'
        order = create_order(table, payload, require_session=True)
        self.assertEqual((order.source, order.fulfillment, order.delivery_fee), ('qr', 'dine_in', Decimal('0')))
        self.assertEqual(order.customer_phone, '')

    def test_database_rejects_online_table_context_and_duplicate_online_keys(self):
        order = create_online_order(self.branch, self.payload())
        with self.assertRaises(IntegrityError), transaction.atomic():
            Order.objects.create(branch=self.branch, table=None, source='online', fulfillment='pickup',
                total=1, idempotency_key=order.idempotency_key, request_hash='different')
        table = DiningTable.objects.create(branch=self.branch, name='T1')
        with self.assertRaises(IntegrityError), transaction.atomic():
            Order.objects.filter(pk=order.pk).update(table=table)
