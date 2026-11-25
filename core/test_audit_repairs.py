"""Regression cases for the 25 November audit: assert the corrected behavior."""
import io
import json
import re
import tempfile
from datetime import timedelta
from decimal import Decimal
from uuid import uuid4

from PIL import Image
from django.contrib.auth.models import User
from django.core import mail
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError, connection, transaction
from django.test import Client, RequestFactory, TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from config.database import postgres_database
from .accounts import account_for_email
from .admin import PlanAdmin
from .forms import OwnerRegistrationForm, PlatformRestaurantForm
from .invitations import accept_staff_invitation
from .menu_import import commit_menu_import, parse_menu_csv
from .models import (AuditLog, Branch, Category, DemoRequest, DiningTable, Membership,
                     MenuImportBatch, MenuItem, ModifierGroup, ModifierOption, Order, Plan,
                     Restaurant, StaffInvitation, Subscription, SubscriptionAgreement,
                     SubscriptionPayment, CollectionCorrection)
from .services import create_order, normalize_order_lines, record_manual_payment, correct_manual_collection
from .resources import archive_resource
from .security import client_address


@override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
class AuditRepairTests(TestCase):
    def setUp(self):
        self.plan = Plan.objects.create(name='Audit Plan', max_staff=2, max_branches=3,
                                       max_items=50, max_tables=20, max_stations=1)
        self.venue = Restaurant.objects.create(name='Audit', slug='audit')
        self.subscription = Subscription.objects.create(restaurant=self.venue, plan=self.plan,
            status='active', ends_at=timezone.now() + timedelta(days=30))
        self.branch = Branch.objects.create(restaurant=self.venue)
        self.table = DiningTable.objects.create(branch=self.branch, name='Audit table')
        self.category = Category.objects.create(branch=self.branch, name='Menu')
        self.item = MenuItem.objects.create(category=self.category, name='Tea', price='10.00')
        self.owner = User.objects.create_user(username='owner@audit.example')
        Membership.objects.create(user=self.owner, restaurant=self.venue, role='owner')
        self.client.force_login(self.owner)

    def payload(self, **fields):
        return {'idempotency_key': str(uuid4()), 'session_token': str(self.table.session_token),
                'lines': [{'item_id': self.item.pk, 'quantity': 1}], **fields}

    def test_uploaded_polyglot_is_reencoded_with_safe_name(self):
        png = io.BytesIO()
        Image.new('RGB', (2, 2), 'white').save(png, 'PNG')
        marker = b'<script>document.documentElement.dataset.audit="executed"</script>'
        upload = SimpleUploadedFile('audit.html', png.getvalue() + marker, content_type='image/png')
        with tempfile.TemporaryDirectory(prefix='serveza-image-test-') as media, override_settings(MEDIA_ROOT=media):
            response = self.client.post(reverse('manage_menu', args=[self.venue.slug]),
                {'action': 'item', 'category': self.category.pk, 'name': 'Image audit',
                 'price': '10.00', 'image': upload})
            self.assertEqual(response.status_code, 302)
            item = MenuItem.objects.get(name='Image audit')
            self.assertTrue(item.image.name.endswith('.png'))
            self.assertNotIn('audit.html', item.image.name)
            with item.image.open('rb') as image:
                self.assertNotIn(marker, image.read())
            with Image.open(item.image.path) as image:
                image.verify()

    def test_saved_order_replays_after_pause_expiry_and_session_rotation(self):
        payload = self.payload()
        order = create_order(self.table, payload, require_session=True)
        self.branch.accepting_orders = False
        self.branch.save()
        self.subscription.ends_at = timezone.now() - timedelta(days=1)
        self.subscription.save()
        self.table.session_token = uuid4()
        self.table.save()
        url = reverse('place_order', args=[self.table.public_token])
        response = self.client.post(url, json.dumps(payload), content_type='application/json')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['order_number'], order.pk)
        self.assertEqual(self.client.post(url, json.dumps(self.payload()), content_type='application/json').status_code, 400)
        changed = {**payload, 'note': 'Different'}
        self.assertEqual(self.client.post(url, json.dumps(changed), content_type='application/json').status_code, 400)
        self.assertEqual(Order.objects.count(), 1)

    def test_invitation_reservation_blocks_direct_and_platform_creation(self):
        url = reverse('staff', args=[self.venue.slug])
        self.client.post(url, {'action': 'invite', 'email': 'invited@audit.example',
                             'role': 'waiter', 'branch_id': self.branch.pk})
        token = re.search(r'/invitation/([^/]+)/', mail.outbox[-1].body).group(1)
        self.client.post(url, {'action': 'create', 'email': 'direct@audit.example',
                             'role': 'waiter', 'password': 'AuditStrongPass!123'})
        self.assertEqual(self.venue.memberships.count(), 1)
        operator = User.objects.create_superuser('operator')
        self.client.force_login(operator)
        response = self.client.post(reverse('platform_account_create'), {'name': 'New',
            'email': 'platform@audit.example', 'password': 'AuditStrongPass!123',
            'active': 'on', 'access': 'member', 'branch': self.branch.pk, 'role': 'waiter', 'reason': 'Audit'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.venue.memberships.count(), 1)
        accept_staff_invitation(token, 'Invited', 'AuditStrongPass!123')
        self.assertEqual(self.venue.memberships.count(), 2)

    def test_invalid_inputs_never_write_or_raise_500(self):
        url = reverse('place_order', args=[self.table.public_token])
        for value in (True, 1.5, float('inf'), [], {}, '9' * 100, 0):
            payload = self.payload(lines=[{'item_id': value, 'quantity': 1}])
            self.assertEqual(self.client.post(url, json.dumps(payload), content_type='application/json').status_code, 400)
        response = self.client.post(reverse('staff_order', args=[self.venue.slug]),
            json.dumps(self.payload(table_id='invalid')), content_type='application/json')
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.client.get(reverse('reports', args=[self.venue.slug]),
            {'from': '9999-12-31', 'to': '9999-12-31', 'export': 'csv'}).status_code, 400)
        self.assertEqual(Order.objects.count(), 0)

    def test_money_limits_apply_before_saving_any_order(self):
        self.item.price = Decimal('6000000.00')
        self.item.save()
        group = ModifierGroup.objects.create(item=self.item, name='Extra')
        option = ModifierOption.objects.create(group=group, name='Extra', price='6000000.00')
        with self.assertRaises(ValidationError):
            create_order(self.table, self.payload(lines=[{'item_id': self.item.pk, 'quantity': 1, 'options': [option.pk]}]))
        self.assertFalse(Order.objects.exists())

    def test_precision_policy_matches_import_and_manual_edit(self):
        self.client.post(reverse('manage_menu', args=[self.venue.slug]),
            {'action': 'price', 'item_id': self.item.pk, 'price': '1.999'})
        self.item.refresh_from_db()
        self.assertEqual(self.item.price, Decimal('10.00'))
        _, errors = parse_menu_csv(SimpleUploadedFile('menu.csv',
            b'category,name,price,description\nMenu,Other,1.999,\n'))
        self.assertTrue(errors)

    def test_admin_and_database_reject_negative_plan_price(self):
        form = PlanAdmin.PlanForm({'name': 'Negative', 'price_monthly': '-20.00',
            'max_tables': 20, 'max_items': 50, 'max_staff': 2, 'max_branches': 1, 'max_stations': 1})
        self.assertFalse(form.is_valid())
        with self.assertRaises(IntegrityError), transaction.atomic():
            Plan.objects.filter(pk=self.plan.pk).update(price_monthly=-1)

    def test_legacy_email_cannot_be_registered_again_or_renamed(self):
        legacy = User.objects.create_user('legacy-login', email='same@audit.example')
        form = OwnerRegistrationForm({'name': 'Another owner', 'restaurant': 'Another venue',
            'email': legacy.email, 'password': 'AuditStrongPass!123'})
        self.assertFalse(form.is_valid())
        self.assertEqual(account_for_email(legacy.email).pk, legacy.pk)
        legacy.refresh_from_db()
        self.assertEqual(legacy.username, 'legacy-login')

    def test_owner_email_fits_username_field(self):
        email = 'a' * 64 + '@' + 'b' * 60 + '.' + 'c' * 30 + '.com'
        form = PlatformRestaurantForm({'name': 'Long email', 'branch_name': 'Main',
            'owner_email': email, 'owner_name': 'Owner', 'owner_password': 'AuditStrongPass!123'})
        self.assertFalse(form.is_valid())

    def test_existing_long_legacy_email_is_linked_without_changing_username(self):
        email = 'a'*64 + '@' + 'b'*60 + '.' + 'c'*30 + '.com'
        user = User.objects.create_user('short-legacy-name', email=email)
        form = PlatformRestaurantForm({'name': 'Another venue', 'branch_name': 'Main', 'owner_email': email})
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(account_for_email(email), user)
        self.assertEqual(user.username, 'short-legacy-name')

    def test_csv_header_and_existing_item_conflicts(self):
        rows, errors = parse_menu_csv(SimpleUploadedFile('menu.csv',
            b'category, name,price,description\nMenu,Tea,10.00,\n'))
        self.assertFalse(errors)
        batch = MenuImportBatch.objects.create(branch=self.branch, user=self.owner, rows=rows)
        with self.assertRaises(ValidationError):
            commit_menu_import(batch, self.owner)
        self.assertEqual(MenuItem.objects.count(), 1)

    def test_price_change_has_before_after_and_actor(self):
        self.client.post(reverse('manage_menu', args=[self.venue.slug]),
            {'action': 'price', 'item_id': self.item.pk, 'price': '99.99'})
        log = AuditLog.objects.get(action='item.update')
        self.assertEqual(log.actor, self.owner)
        self.assertEqual(log.changes['before']['price'], '10.00')
        self.assertEqual(log.changes['after']['price'], '99.99')

    def test_normalization_uses_bounded_queries(self):
        rows = [{'item_id': self.item.pk, 'quantity': 1}] * 40
        with CaptureQueriesContext(connection) as queries:
            normalized, total = normalize_order_lines(self.branch, rows)
        self.assertLessEqual(len(queries), 4)
        self.assertEqual(len(normalized), 40)
        self.assertEqual(total, Decimal('400.00'))

    def test_dsn_preserves_encoded_credentials_and_ssl_options(self):
        parsed = postgres_database('postgresql://audit:p%40ss@db.invalid/audit?sslmode=require&connect_timeout=5')
        self.assertEqual(parsed['PASSWORD'], 'p@ss')
        self.assertEqual(parsed['OPTIONS']['sslmode'], 'require')
        with self.assertRaises(ValueError):
            postgres_database('postgresql://audit@db.invalid/audit?unknown=1')

    def test_operator_flags_allow_both_admin_surfaces(self):
        operator = User.objects.create_superuser('first-operator')
        self.client.force_login(operator)
        response = self.client.post(reverse('platform_account_create'), {
            'name': 'Second operator', 'email': 'second@audit.example',
            'password': 'AuditStrongPass!123', 'access': 'operator', 'active': 'on', 'reason': 'Audit'})
        self.assertEqual(response.status_code, 302)
        new = User.objects.get(username='second@audit.example')
        self.assertTrue(new.is_superuser and new.is_staff)
        self.client.force_login(new)
        self.assertEqual(self.client.get(reverse('admin:index')).status_code, 200)

    def test_admin_and_public_login_share_failed_attempt_limit(self):
        User.objects.create_superuser('audit-operator', password='AuditStrongPass!123')
        client = Client()
        for _ in range(8):
            self.assertEqual(client.post(reverse('login'), {'username': 'audit-operator', 'password': 'bad'}).status_code, 200)
        self.assertEqual(client.post(reverse('admin:login'), {'username': 'audit-operator', 'password': 'bad'}).status_code, 429)

    def test_contact_limit_applies_with_csrf_and_allows_exact_replay(self):
        client = Client(enforce_csrf_checks=True)
        url = reverse('contact')
        client.get(url)
        data = {'name': 'Audit', 'venue': 'Audit', 'email': 'lead@audit.example',
                'message': '', 'key': str(uuid4()), 'csrfmiddlewaretoken': client.cookies['csrftoken'].value}
        for _ in range(10):
            data['key'] = str(uuid4())
            self.assertEqual(client.post(url, data).status_code, 302)
        self.assertEqual(client.post(url, data).status_code, 302)
        data['key'] = str(uuid4())
        self.assertEqual(client.post(url, data).status_code, 429)
        self.assertEqual(DemoRequest.objects.count(), 10)

    def test_daily_collection_is_independent_of_order_creation_and_branch(self):
        order = create_order(self.table, self.payload())
        Order.objects.filter(pk=order.pk).update(created_at=timezone.now()-timedelta(days=2))
        record_manual_payment(order, self.owner)
        today = timezone.localdate().isoformat()
        response = self.client.get(reverse('reports', args=[self.venue.slug]), {'from': today, 'to': today})
        self.assertEqual(response.context['paid_value'], 0)
        self.assertEqual(response.context['collection_activity']['net'], Decimal('10.00'))
        self.assertEqual(response.context['total'], 0)

    def test_collection_correction_preserves_original_is_idempotent_and_rejects_stale(self):
        order = create_order(self.table, self.payload())
        order = record_manual_payment(order, self.owner)
        key = uuid4()
        old_time = order.paid_at
        correction = correct_manual_collection(order, self.owner, key, 'Wrong manual entry', expected_paid_at=old_time.isoformat())
        order.refresh_from_db()
        self.assertEqual(order.payment_status, 'unpaid')
        self.assertIsNone(order.paid_at)
        self.assertEqual(correction.original_paid_at, old_time)
        self.assertEqual(correction.amount, Decimal('10.00'))
        self.assertEqual(order.lines.get().item_name, 'Tea')
        self.assertEqual(correct_manual_collection(order, self.owner, key, 'Wrong manual entry', expected_paid_at=old_time.isoformat()).pk, correction.pk)
        order = record_manual_payment(order, self.owner)
        with self.assertRaises(ValidationError):
            correct_manual_collection(order, self.owner, uuid4(), 'Stale review', expected_paid_at=old_time.isoformat())
        self.assertEqual(CollectionCorrection.objects.count(), 1)
        self.assertEqual(self.client.get(reverse('reports', args=[self.venue.slug])).context['collection_activity']['net'], Decimal('10.00'))
        self.client.get(reverse('manage_orders', args=[self.venue.slug]), {'lang': 'en'})
        response = self.client.post(reverse('collection_correct', args=[self.venue.slug, order.pk]),
            {'key': 'bad', 'reason': 'Reviewed mistake', 'expected_paid_at': old_time.isoformat()}, follow=True)
        self.assertContains(response, 'Invalid collection correction key.')

    def test_cashier_cannot_correct_collection(self):
        user = User.objects.create_user('cashier')
        membership = Membership.objects.create(user=user, restaurant=self.venue, role='cashier')
        membership.branches.add(self.branch)
        order = record_manual_payment(create_order(self.table, self.payload()), self.owner)
        self.client.force_login(user)
        response = self.client.post(reverse('collection_correct', args=[self.venue.slug, order.pk]),
            {'key': str(uuid4()), 'reason': 'Wrong', 'expected_paid_at': order.paid_at.isoformat()})
        self.assertEqual(response.status_code, 403)
        order.refresh_from_db()
        self.assertEqual(order.payment_status, 'paid')
        self.assertFalse(CollectionCorrection.objects.exists())

    def test_archiving_frees_capacity_without_deleting_history_and_restore_is_limited(self):
        self.plan.max_tables = 1
        self.plan.save()
        order = create_order(self.table, self.payload())
        Order.objects.filter(pk=order.pk).update(status='served', payment_status='paid', paid_at=timezone.now())
        archive_resource(self.venue, self.owner, 'table', self.table.pk)
        self.table.refresh_from_db()
        self.assertTrue(self.table.archived)
        self.assertFalse(self.table.active)
        self.client.post(reverse('manage_tables', args=[self.venue.slug]), {'action': 'create', 'name': 'Replacement'})
        self.assertTrue(DiningTable.objects.filter(name='Replacement').exists())
        with self.assertRaises(ValidationError):
            archive_resource(self.venue, self.owner, 'table', self.table.pk, restore=True)
        self.assertTrue(Order.objects.filter(pk=order.pk, table=self.table, lines__item_name='Tea').exists())

    def test_archiving_rejects_open_orders_and_foreign_resources(self):
        create_order(self.table, self.payload())
        with self.assertRaises(ValidationError):
            archive_resource(self.venue, self.owner, 'table', self.table.pk)
        foreign = Restaurant.objects.create(name='Other', slug='other')
        other_table = DiningTable.objects.create(branch=Branch.objects.create(restaurant=foreign), name='Foreign')
        with self.assertRaises(ValidationError):
            archive_resource(self.venue, self.owner, 'table', other_table.pk)
        other_table.refresh_from_db()
        self.assertFalse(other_table.archived)

    def test_plan_edit_preserves_existing_terms_until_explicit_subscription_agreement(self):
        operator = User.objects.create_superuser('plan-operator')
        self.client.force_login(operator)
        data = {'name': self.plan.name, 'price_monthly': '750', 'max_staff': 7, 'max_branches': 3,
                'max_items': 50, 'max_tables': 20, 'max_stations': 1, 'reason': 'Updated public offer'}
        self.assertEqual(self.client.post(reverse('platform_plan_edit', args=[self.plan.pk]), data).status_code, 302)
        self.subscription.refresh_from_db()
        self.assertEqual(self.subscription.limit('max_staff'), 2)
        self.assertEqual(self.subscription.agreed_terms['monthly_price'], '0.00')
        data = {'action': 'subscription', 'plan_id': self.plan.pk, 'status': 'active',
                'ends_at': (timezone.localdate()+timedelta(days=30)).isoformat(), 'reason': 'Renewal confirmed',
                'billing': 'monthly'}
        self.client.post(reverse('platform_update_restaurant', args=[self.venue.pk]), data)
        self.subscription.refresh_from_db()
        self.assertEqual(self.subscription.limit('max_staff'), 7)
        self.assertEqual(self.subscription.agreed_terms['agreed_price'], '750.00')
        self.assertEqual(self.subscription.agreements.count(), 2)

    def test_trial_begins_once_without_collection_and_receipt_can_activate_atomically(self):
        Subscription.objects.filter(pk=self.subscription.pk).update(plan=None, status='pending', ends_at=None)
        operator = User.objects.create_superuser('trial-operator')
        self.client.force_login(operator)
        plan = Plan.objects.get(name='Starter')
        url = reverse('platform_start_trial', args=[self.venue.pk])
        data = {'plan_id': plan.pk, 'reason': 'First trial approved'}
        before = timezone.now()
        self.client.post(url, data)
        self.subscription.refresh_from_db()
        expiry = self.subscription.ends_at
        self.assertEqual(self.subscription.status, 'trial')
        self.assertGreaterEqual(expiry, before+timedelta(days=14))
        self.assertLess(expiry, timezone.now()+timedelta(days=14))
        self.client.post(url, data)
        self.subscription.refresh_from_db()
        self.assertEqual(self.subscription.ends_at, expiry)
        self.assertEqual(self.subscription.agreements.count(), 1)
        self.assertFalse(SubscriptionPayment.objects.exists())
        end = timezone.localdate()+timedelta(days=31)
        response = self.client.post(reverse('platform_record_payment', args=[self.venue.pk]),
            {'amount': '600', 'method': 'bank', 'reference': 'PAID-RENEWAL', 'period_start': timezone.localdate(),
             'period_end': end, 'activate': '1', 'activation_plan': plan.pk})
        self.assertEqual(response.status_code, 302)
        self.subscription.refresh_from_db()
        self.assertEqual(self.subscription.status, 'active')
        agreement = self.subscription.agreements.latest('pk')
        self.assertEqual(agreement.payment.reference, 'PAID-RENEWAL')
        self.assertEqual(agreement.terms['agreed_price'], '600.00')

    def test_all_branch_comparison_excludes_other_restaurants(self):
        create_order(self.table, self.payload())
        second = Branch.objects.create(restaurant=self.venue, name='Second')
        table = DiningTable.objects.create(branch=second, name='Two')
        item = MenuItem.objects.create(category=Category.objects.create(branch=second, name='Menu'), name='Coffee', price=20)
        record_manual_payment(create_order(table, self.payload(lines=[{'item_id': item.pk, 'quantity': 1}])), self.owner)
        foreign = Restaurant.objects.create(name='Other venue', slug='foreign')
        Branch.objects.create(restaurant=foreign, name='Foreign branch')
        response = self.client.get(reverse('reports', args=[self.venue.slug]), {'scope': 'all'})
        self.assertEqual(response.context['total'], 2)
        self.assertEqual(len(response.context['branch_comparison']), 2)
        self.assertEqual(response.context['collection_activity']['net'], Decimal('20'))
        self.assertNotContains(response, 'Foreign branch')

    def test_changed_screens_render_both_languages_without_markup_in_title(self):
        for lang, direction in [('ar', 'rtl'), ('en', 'ltr')]:
            for route in ('resource_archive_list', 'reports', 'restaurant_settings', 'manage_orders'):
                response = self.client.get(reverse(route, args=[self.venue.slug]), {'lang': lang})
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, f'dir="{direction}"')
                title = response.content.decode().split('<title>', 1)[1].split('</title>', 1)[0]
                self.assertNotIn('<section', title)

    def test_collection_csv_preserves_period_and_safe_merchant_text(self):
        self.table.name = '=SUM(1,2)'
        self.table.save()
        order = record_manual_payment(create_order(self.table, self.payload()), self.owner)
        Order.objects.filter(pk=order.pk).update(created_at=timezone.now()-timedelta(days=2))
        today = timezone.localdate().isoformat()
        for lang in ('ar', 'en'):
            response = self.client.get(reverse('reports', args=[self.venue.slug]),
                {'export': 'collections', 'from': today, 'to': today, 'lang': lang})
            content = b''.join(response.streaming_content).decode('utf-8-sig')
            self.assertIn("'=SUM(1,2)", content)
            self.assertIn('10.00', content)
            self.assertIn('Manual collection' if lang == 'en' else 'تحصيل يدوي', content)
        self.table.refresh_from_db()
        self.assertEqual(self.table.name, '=SUM(1,2)')

    def test_trusted_proxy_headers_are_opt_in(self):
        request = RequestFactory().get('/', REMOTE_ADDR='127.0.0.1', HTTP_X_REAL_IP='192.0.2.20')
        with override_settings(TRUSTED_PROXY_NETWORKS=[]):
            self.assertEqual(client_address(request), '127.0.0.1')
        with override_settings(TRUSTED_PROXY_NETWORKS=['127.0.0.1/32']):
            self.assertEqual(client_address(request), '192.0.2.20')
        request.META['REMOTE_ADDR'] = '198.51.100.10'
        with override_settings(TRUSTED_PROXY_NETWORKS=['127.0.0.1/32']):
            self.assertEqual(client_address(request), '198.51.100.10')
        request.META['REMOTE_ADDR'] = ''
        with override_settings(TRUSTED_UNIX_PROXY=False):
            self.assertEqual(client_address(request), 'unknown')
        with override_settings(TRUSTED_UNIX_PROXY=True):
            self.assertEqual(client_address(request), '192.0.2.20')

    def test_request_timing_excludes_tokens_and_query_strings(self):
        with self.assertLogs('serveza.requests', level='INFO') as logged:
            response = self.client.get(reverse('public_menu', args=[self.table.public_token]), {'note': 'private-value'})
        self.assertEqual(len(response['X-Request-ID']), 32)
        self.assertEqual(logged.records[0].route, 'public_menu')
        self.assertNotIn(self.table.public_token, str(logged.output))
        self.assertNotIn('private-value', str(logged.output))

    def test_registration_is_limited_before_creating_accounts_or_sending_email(self):
        client = Client()
        for _ in range(5):
            self.assertEqual(client.post(reverse('register'), {'email': 'bad'}).status_code, 200)
        self.assertEqual(client.post(reverse('register'), {'email': 'new@example.test'}).status_code, 429)
        self.assertEqual(User.objects.count(), 1)
