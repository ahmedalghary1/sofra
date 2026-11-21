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
