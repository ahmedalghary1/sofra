import re
from datetime import timedelta

from django.contrib.auth.models import User
from django.core import mail
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .models import (
    AuditLog, Branch, DiningTable, Membership, Order, Plan, Restaurant,
    ServiceCall, Subscription, SubscriptionPayment,
)


@override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
class ManualActivationTests(TestCase):
    def setUp(self):
        self.email = 'venue@example.com'
        self.client.post(reverse('register'), {
            'email': self.email, 'password': 'Altitude!73840', 'restaurant': 'بيت الياسمين', 'name': 'سارة',
        })
        self.owner = User.objects.get(username=self.email)
        self.venue = Restaurant.objects.get(name='بيت الياسمين')
        self.workspace = reverse('dashboard', args=[self.venue.slug])
        self.waiting = reverse('subscription_activation', args=[self.venue.slug])
        self.update_url = reverse('platform_update_restaurant', args=[self.venue.pk])
        self.operator = User.objects.create_superuser('operator', 'support@example.com')
        self.admin_client = Client()
        self.admin_client.force_login(self.operator)
        token = re.search(r'/verify/([^/]+)/', mail.outbox[0].body).group(1)
        self.confirmation = self.client.get(reverse('verify_owner_email', args=[token]), follow=True)

    def test_verified_owner_waits_until_operator_activates_selected_terms(self):
        self.assertEqual(self.confirmation.request['PATH_INFO'], self.waiting)
        self.assertContains(self.confirmation, 'اطلب التواصل مع خدمة العملاء')
        self.assertIsNone(self.venue.subscription.plan_id)
        self.assertIsNone(self.venue.subscription.ends_at)
        self.assertRedirects(self.client.get(self.workspace), self.waiting)
        self.assertEqual(self.client.post(self.update_url, {}).status_code, 403)
        self.assertFalse(self.venue.subscription.valid())
        self.client.post(self.waiting)
        self.assertContains(self.client.post(self.waiting, follow=True), 'وصل طلبك إلى خدمة العملاء')
        self.assertEqual(AuditLog.objects.filter(restaurant=self.venue, actor=self.owner,
                                                 action='subscription.activation.request').count(), 1)

        queue = reverse('platform_dashboard') + '?status=pending'
        page = self.admin_client.get(queue)
        self.assertContains(page, 'طلب المالك التواصل')
        self.assertContains(page, 'mailto:' + self.email)
        plans = list(page.context['activation_plans'])
        self.assertEqual([(p.name, p.price_monthly, p.max_staff) for p in plans],
                         [('Starter', 600, 4), ('Growth', 1300, 8), ('Business', 2500, 15)])
        end = timezone.localdate() + timedelta(days=30)
        terms = {'action': 'subscription', 'reason': 'تمت مراجعة التحويل', 'plan_id': plans[1].pk,
                 'status': 'active', 'ends_at': end.isoformat()}
        private_plan = Plan.objects.create(name='Local demo')
        for bad in ({'plan_id': ''}, {'plan_id': private_plan.pk}, {'reason': ''}, {'status': 'trial'},
                    {'ends_at': (timezone.localdate() - timedelta(days=1)).isoformat()}):
            with self.subTest(bad=bad):
                self.assertEqual(self.admin_client.post(self.update_url, {**terms, **bad}).status_code, 302)
                self.assertTrue(Subscription.objects.filter(restaurant=self.venue, status='pending').exists())
        self.admin_client.post(self.update_url, terms)
        sub = Subscription.objects.get(restaurant=self.venue)
        self.assertTrue(sub.valid())
        self.assertEqual(sub.plan, plans[1])
        self.assertEqual(timezone.localdate(sub.ends_at), end)
        self.assertTrue(AuditLog.objects.filter(restaurant=self.venue, actor=self.operator,
            action='platform.subscription.update', detail__contains=terms['reason']).exists())
        self.assertFalse(SubscriptionPayment.objects.filter(restaurant=self.venue).exists())
        self.assertNotContains(self.admin_client.get(queue), self.venue.name)
        self.assertRedirects(self.client.get(self.waiting), self.workspace)
        self.assertEqual(self.client.get(reverse('staff', args=[self.venue.slug])).status_code, 200)

    def test_manual_collection_does_not_open_a_pending_workspace(self):
        today = timezone.localdate().isoformat()
        self.admin_client.post(reverse('platform_record_payment', args=[self.venue.pk]), {
            'amount': '600', 'method': 'bank', 'reference': 'REF-MANUAL',
            'period_start': today, 'period_end': today,
        })
        self.assertEqual(SubscriptionPayment.objects.filter(restaurant=self.venue).count(), 1)
        self.assertRedirects(self.client.get(self.workspace), self.waiting)

    def test_pending_workspace_blocks_staff_and_guest_operations(self):
        branch = self.venue.branches.get()
        table = DiningTable.objects.create(branch=branch, name='One')
        for name in ('staff', 'manage_menu', 'manage_tables', 'kitchen_feed', 'restaurant_settings'):
            with self.subTest(name=name):
                self.assertRedirects(self.client.get(reverse(name, args=[self.venue.slug])), self.waiting)
        self.assertRedirects(self.client.post(reverse('manage_tables', args=[self.venue.slug]),
            {'action': 'create', 'name': 'Two'}), self.waiting)
        self.assertEqual(branch.tables.count(), 1)
        token = [table.public_token]
        guest = Client()
        self.assertEqual(guest.get(reverse('public_menu', args=token)).status_code, 404)
        response = guest.post(reverse('place_order', args=token), {}, content_type='application/json')
        self.assertEqual(response.status_code, 400)
        self.assertIn('استقبال الطلبات متوقف', response.json()['error'])
        guest.post(reverse('create_call', args=token), {'kind': 'waiter', 'session_token': str(table.session_token)})
        self.assertFalse(Order.objects.exists())
        self.assertFalse(ServiceCall.objects.exists())

    def test_only_an_owner_can_request_contact_for_their_restaurant(self):
        stranger = User.objects.create_user(username='another')
        client = Client()
        self.assertRedirects(client.get(self.waiting), reverse('login'))
        client.force_login(stranger)
        self.assertEqual(client.get(self.waiting).status_code, 403)
        self.assertEqual(client.post(self.waiting).status_code, 403)
        Membership.objects.create(user=stranger, restaurant=self.venue, role='waiter')
        self.assertContains(client.get(self.waiting), 'يتولى مالك المطعم')
        self.assertEqual(client.post(self.waiting).status_code, 403)
        self.assertIsNone(Subscription.objects.get(restaurant=self.venue).activation_requested_at)

        active = Restaurant.objects.create(name='مطعم آخر', slug='other')
        Branch.objects.create(restaurant=active)
        Membership.objects.create(user=self.owner, restaurant=active, role='owner')
        Subscription.objects.create(restaurant=active, plan=Plan.objects.get(name='Business'),
                                    status='active', ends_at=timezone.now() + timedelta(days=1))
        self.assertContains(self.client.get(self.waiting), reverse('dashboard', args=[active.slug]))
        self.assertNotContains(client.get(self.waiting), active.name)
