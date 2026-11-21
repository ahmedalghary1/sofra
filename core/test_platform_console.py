from datetime import timedelta

from django.contrib.auth.models import User
from django.contrib.admin.models import LogEntry
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from core.models import AuditLog, Branch, Membership, Plan, Restaurant, Subscription


class PlatformConsoleTests(TestCase):
    def setUp(self):
        self.operator = User.objects.create_superuser('operator')
        self.client.force_login(self.operator)
        self.venue = Restaurant.objects.create(name='Olive', slug='olive')
        self.other = Restaurant.objects.create(name='Lemon', slug='lemon')
        self.branch = Branch.objects.create(restaurant=self.venue)
        self.member = Membership.objects.create(restaurant=self.venue, user=User.objects.create_user('sam'), role='manager')
        self.url = reverse('platform_member_update', args=[self.venue.pk, self.member.pk])
        self.payload = dict(role='waiter', branch=self.branch.pk, state='active', reason='Floor service')

    def test_active_list_counts_only_current_subscriptions(self):
        for venue, days in [(self.venue, 2), (self.other, -2)]:
            Subscription.objects.create(restaurant=venue, status='trial', plan=Plan.objects.first(),
                                        ends_at=timezone.now() + timedelta(days=days))
        response = self.client.get(reverse('platform_dashboard'), {'status': 'active'})
        self.assertEqual(response.context['active_total'], 1)
        self.assertContains(response, 'Olive')
        self.assertNotContains(response, 'Lemon')

    def test_permissions_are_scoped_and_audited(self):
        foreign = Branch.objects.create(restaurant=self.other)
        self.assertEqual(self.client.post(self.url, self.payload | {'branch': foreign.pk}).status_code, 404)
        self.client.post(self.url, self.payload)
        self.member.refresh_from_db()
        self.assertEqual(self.member.role, 'waiter')
        self.assertEqual(list(self.member.branches.all()), [self.branch])
        self.assertTrue(AuditLog.objects.filter(actor=self.operator, restaurant=self.venue,
                        action='platform.member.update', detail__contains='Floor service').exists())
        self.member.role = 'owner'
        self.member.save()
        self.assertEqual(self.client.post(self.url, self.payload).status_code, 403)
        self.client.force_login(self.member.user)
        self.assertEqual(self.client.post(self.url, self.payload).status_code, 403)
        for section in ('people', 'plans', 'pending', 'active'):
            self.assertEqual(self.client.get(reverse('platform_dashboard'), {'status': section}).status_code, 403)

    def test_plan_create_and_edit_validate_and_record_actor(self):
        create = reverse('platform_plan_create')
        terms = dict(name='Olive Plus', price_monthly='750.25', max_tables=25, max_items=75,
                     max_branches=2, max_staff=8, max_stations=2, reason='Agreed terms')
        self.assertEqual(self.client.post(create, terms).status_code, 302)
        plan = Plan.objects.get(name='Olive Plus')
        edit = reverse('platform_plan_edit', args=[plan.pk])
        self.assertEqual(self.client.post(edit, terms | {'price_monthly': '-1'}).status_code, 200)
        self.assertEqual(self.client.post(edit, terms | {'max_staff': 10}).status_code, 302)
        plan.refresh_from_db()
        self.assertEqual(plan.max_staff, 10)
        self.assertEqual(str(plan.price_monthly), '750.25')
        self.assertEqual(LogEntry.objects.filter(user=self.operator, object_repr=plan.name).count(), 2)
        self.client.force_login(self.member.user)
        self.assertEqual(self.client.post(edit, terms).status_code, 403)
        self.assertEqual(self.client.post(create, terms).status_code, 403)
