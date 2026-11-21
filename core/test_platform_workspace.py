from django.contrib.admin.models import LogEntry
from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from datetime import timedelta

from core.models import Branch, DemoRequest, Membership, Plan, Restaurant, Subscription


class PlatformWorkspaceTests(TestCase):
    def setUp(self):
        self.operator = User.objects.create_superuser('operator', 'operator@example.com')
        self.client.force_login(self.operator)
        self.staff = User.objects.create_user('staff', 'staff@example.com', is_staff=True)
        self.lead = DemoRequest.objects.create(name='Mira', venue='Olive', email='lead@example.com', language='en')
        self.branch = Branch.objects.create(restaurant=Restaurant.objects.create(name='Olive', slug='olive'))
        Subscription.objects.create(restaurant=self.branch.restaurant, plan=Plan.objects.first(), status='active',
                                    ends_at=timezone.now() + timedelta(days=5))
        self.account_data = {'email': 'floor@example.com', 'name': 'Floor', 'password': 'Woodland!30528',
                             'access': 'member', 'branch': self.branch.pk, 'role': 'waiter', 'active': 'on', 'reason': 'Opening'}

    def test_workspace_is_self_contained_and_operator_only(self):
        for name in ('platform_dashboard', 'platform_accounts', 'platform_account_create', 'platform_activity',
                     'platform_requests', 'platform_receipts', 'platform_create_restaurant', 'platform_plan_create'):
            self.client.force_login(self.operator)
            self.assertNotContains(self.client.get(reverse(name)), '/django-admin/')
            self.client.force_login(self.staff)
            self.assertEqual(self.client.get(reverse(name)).status_code, 403)
        self.assertEqual(self.client.post(reverse('platform_account_create'), self.account_data).status_code, 403)
        self.assertEqual(self.client.post(reverse('platform_request_status', args=[self.lead.pk]), {'status':'closed'}).status_code, 403)

    def test_create_suspend_and_protect_operator(self):
        url = reverse('platform_account_create')
        self.assertRedirects(self.client.post(url, self.account_data), reverse('platform_accounts'))
        user = User.objects.get(username=self.account_data['email'])
        self.assertTrue(user.check_password(self.account_data['password']))
        self.assertTrue(Membership.objects.get(user=user).branches.filter(pk=self.branch.pk).exists())
        changes = self.account_data | {'branch':'', 'password':'', 'active':''}
        self.assertRedirects(self.client.post(reverse('platform_account_edit', args=[user.pk]), changes), reverse('platform_accounts'))
        user.refresh_from_db()
        self.assertFalse(user.is_active)
        changes['email'] = self.operator.email
        self.client.post(reverse('platform_account_edit', args=[self.operator.pk]), changes)
        self.operator.refresh_from_db()
        self.assertTrue(self.operator.is_superuser and self.operator.is_active)

    def test_follow_up_and_audit(self):
        url = reverse('platform_request_status', args=[self.lead.pk])
        self.assertEqual(self.client.post(url, {'status': 'missing'}).status_code, 400)
        self.assertRedirects(self.client.post(url, {'status': 'contacted'}), reverse('platform_requests'))
        self.assertNotContains(self.client.get(reverse('platform_requests'), {'filter':'new'}), 'lead@example.com')
        self.assertContains(self.client.get(reverse('platform_requests'), {'filter':'contacted'}), 'lead@example.com')
        self.assertContains(self.client.get(reverse('platform_activity'), {'filter':'global'}), 'new → contacted')
        self.assertEqual(LogEntry.objects.filter(user=self.operator).count(), 1)

    def test_edit_legacy_account_without_claiming_another_login(self):
        self.operator.email = 'Shared@example.com'
        self.operator.save(update_fields=['email'])
        other = User.objects.create_user('shared@example.com', 'shared@example.com')
        url = reverse('platform_account_edit', args=[self.operator.pk])
        payload = {'email': self.operator.email, 'name': 'Site operator', 'reason': 'Profile'}
        self.assertRedirects(self.client.post(url, payload), reverse('platform_accounts'))
        self.operator.refresh_from_db()
        self.assertEqual((self.operator.username, self.operator.email), ('operator', 'Shared@example.com'))
        other.email = 'different@example.com'
        other.save(update_fields=['email'])
        response = self.client.post(url, payload | {'email': other.email})
        self.assertIn('email', response.context['form'].errors)
        self.operator.refresh_from_db()
        self.assertEqual(self.operator.email, 'Shared@example.com')

    def test_username_only_operator_can_update_without_email(self):
        self.operator.email = ''
        self.operator.save(update_fields=['email'])
        url = reverse('platform_account_edit', args=[self.operator.pk])
        self.assertRedirects(self.client.post(url, {'name': 'Updated', 'reason': 'Profile'}), reverse('platform_accounts'))
        self.operator.refresh_from_db()
        self.assertEqual((self.operator.first_name, self.operator.username), ('Updated', 'operator'))
