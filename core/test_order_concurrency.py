from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier
from uuid import uuid4

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.db import connections
from django.test import TransactionTestCase, skipUnlessDBFeature
from django.utils import timezone

from .models import AuditLog, Branch, Category, DiningTable, MenuItem, Restaurant, Subscription, Plan
from .services import advance_order, create_order, record_manual_payment

from django.test import Client
from django.urls import reverse
from django.db import transaction
from hashlib import sha256
from .limits import check_creation_limit
from .models import Membership, StaffInvitation


class OrderCollectionConcurrencyTests(TransactionTestCase):
    @skipUnlessDBFeature('has_select_for_update')
    def test_collection_and_cancellation_compete_for_the_same_order(self):
        restaurant = Restaurant.objects.create(name='Race venue', slug='collection-race')
        plan = Plan.objects.create(name='Race test')
        Subscription.objects.create(restaurant=restaurant, plan=plan, ends_at=timezone.now() + timedelta(days=1))
        branch = Branch.objects.create(restaurant=restaurant)
        item = MenuItem.objects.create(category=Category.objects.create(branch=branch, name='Menu'),
                                       name='Tea', price='25.00')
        order = create_order(DiningTable.objects.create(branch=branch, name='Window'), {
            'idempotency_key': str(uuid4()), 'lines': [{'item_id': item.pk, 'quantity': 1}],
        })
        cashier = User.objects.create_user('race-cashier')
        manager = User.objects.create_user('race-manager')
        barrier = Barrier(2)

        def run(action, *args):
            try:
                barrier.wait(timeout=10)
                action(order, *args)
                return True
            except ValidationError:
                return False
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=2) as executor:
            collection = executor.submit(run, record_manual_payment, cashier)
            cancellation = executor.submit(run, advance_order, manager, 'cancelled', 'Wrong table')
            was_collected, was_cancelled = collection.result(), cancellation.result()

        self.assertNotEqual(was_collected, was_cancelled)
        order.refresh_from_db()
        if was_collected:
            self.assertEqual((order.status, order.payment_status), ('new', 'paid'))
            self.assertIsNotNone(order.paid_at)
            self.assertEqual(order.history.count(), 1)
        else:
            self.assertEqual((order.status, order.payment_status), ('cancelled', 'unpaid'))
            self.assertIsNone(order.paid_at)
            self.assertEqual(order.history.get(new_status='cancelled').actor_id, manager.pk)
        self.assertEqual(AuditLog.objects.filter(restaurant=restaurant,
                                               action__in=['order.status', 'order.payment.manual']).count(), 1)


class AuditRepairConcurrencyTests(TransactionTestCase):
    def setUp(self):
        self.restaurant = Restaurant.objects.create(name='Race venue', slug='audit-race')
        self.plan = Plan.objects.create(name='Audit race', max_staff=3)
        Subscription.objects.create(restaurant=self.restaurant, plan=self.plan, ends_at=timezone.now()+timedelta(days=1))
        self.branch = Branch.objects.create(restaurant=self.restaurant)
        self.table = DiningTable.objects.create(branch=self.branch, name='One')
        self.item = MenuItem.objects.create(category=Category.objects.create(branch=self.branch, name='Menu'), name='Tea', price=10)
        self.owner = User.objects.create_user('audit-race-owner')
        Membership.objects.create(user=self.owner, restaurant=self.restaurant, role='owner')

    def parallel(self, first, second):
        barrier = Barrier(2)
        def run(callback):
            try:
                barrier.wait(timeout=10)
                return callback()
            finally:
                connections.close_all()
        with ThreadPoolExecutor(max_workers=2) as executor:
            a, b = executor.submit(run, first), executor.submit(run, second)
            return a.result(timeout=30), b.result(timeout=30)

    @skipUnlessDBFeature('has_select_for_update')
    def test_same_key_simultaneous_submission_creates_one_order(self):
        payload = {'idempotency_key': str(uuid4()), 'session_token': str(self.table.session_token),
                   'lines': [{'item_id': self.item.pk, 'quantity': 1}]}
        def submit():
            table = DiningTable.objects.get(pk=self.table.pk)
            return create_order(table, payload, require_session=True).pk
        first, second = self.parallel(submit, submit)
        self.assertEqual(first, second)
        self.assertEqual(Order.objects.count(), 1)
        self.assertEqual(Order.objects.get().history.count(), 1)

    @skipUnlessDBFeature('has_select_for_update')
    def test_competing_staff_creations_preserve_reserved_invitation(self):
        StaffInvitation.objects.create(restaurant=self.restaurant, branch=self.branch, email='reserved@example.test',
            role='waiter', token_hash=sha256(b'reserved').hexdigest(), invited_by=self.owner,
            expires_at=timezone.now()+timedelta(days=1))
        users = [User.objects.create_user('candidate-a'), User.objects.create_user('candidate-b')]
        def add(user):
            try:
                with transaction.atomic():
                    check_creation_limit(self.restaurant, 'staff')
                    Membership.objects.create(restaurant=self.restaurant, user=user, role='waiter')
                return True
            except ValidationError:
                return False
        results = self.parallel(lambda: add(users[0]), lambda: add(users[1]))
        self.assertEqual(sum(results), 1)
        self.assertEqual(self.restaurant.memberships.count(), 2)
        self.assertEqual(self.restaurant.invitations.count(), 1)

    @skipUnlessDBFeature('has_select_for_update')
    def test_submission_competes_with_actual_close_session_endpoint(self):
        payload = {'idempotency_key': str(uuid4()), 'session_token': str(self.table.session_token),
                   'lines': [{'item_id': self.item.pk, 'quantity': 1}]}
        client = Client()
        client.force_login(self.owner)
        cookie = client.cookies['sessionid'].value
        def close():
            worker = Client()
            worker.cookies['sessionid'] = cookie
            return worker.post(reverse('manage_tables', args=[self.restaurant.slug]),
                {'action': 'close_session', 'table_id': self.table.pk}).status_code
        def submit():
            try:
                create_order(DiningTable.objects.get(pk=self.table.pk), payload, require_session=True)
                return True
            except ValidationError:
                return False
        submitted, close_status = self.parallel(submit, close)
        self.assertEqual(close_status, 302)
        self.table.refresh_from_db()
        rotated = str(self.table.session_token) != payload['session_token']
        self.assertNotEqual(submitted, rotated)
        self.assertEqual(Order.objects.count(), int(submitted))
