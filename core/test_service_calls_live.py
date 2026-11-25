from datetime import timedelta
from uuid import uuid4

from django.contrib.auth.models import User
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from .models import Branch, DiningTable, Membership, Order, Plan, Restaurant, ServiceCall, Subscription


class LiveServiceCallTests(TestCase):
    def setUp(self):
        self.venue=Restaurant.objects.create(name='Service cafe',slug='service-live')
        plan=Plan.objects.create(name='Service test',max_branches=2,max_staff=8)
        self.subscription=Subscription.objects.create(restaurant=self.venue,plan=plan,status='active',ends_at=timezone.now()+timedelta(days=30))
        self.branch=Branch.objects.create(restaurant=self.venue,name='Window branch')
        self.second=Branch.objects.create(restaurant=self.venue,name='Second branch')
        self.table=DiningTable.objects.create(branch=self.branch,name='Garden table')
        self.other_table=DiningTable.objects.create(branch=self.second,name='Hidden table')
        self.waiters=[]
        for index in range(2):
            user=User.objects.create_user('service-waiter-'+str(index),first_name='Waiter '+str(index))
            member=Membership.objects.create(restaurant=self.venue,user=user,role='waiter')
            member.branches.add(self.branch)
            self.waiters.append(user)
        self.url=reverse('create_call',args=[self.table.public_token])
        self.feed=reverse('service_call_feed',args=[self.venue.slug])
        self.status=reverse('table_service_status',args=[self.table.public_token])

    def submit(self,kind='waiter',**extra):
        return self.client.post(self.url,dict(kind=kind,session_token=str(self.table.session_token),**extra),HTTP_ACCEPT='application/json')

    def staff_client(self,user):
        client=Client();client.force_login(user);return client

    def test_waiter_and_bill_requests_are_saved_without_orders_or_payment_and_duplicates_are_rejected(self):
        for language in ('ar','en'):
            with self.subTest(language=language):
                result=self.client.post(self.url+'?lang='+language,{'kind':'waiter','session_token':self.table.session_token},HTTP_ACCEPT='application/json')
                self.assertEqual(result.status_code,200)
                self.assertTrue(result.json()['ok'])
                self.assertEqual(self.submit().status_code,400)
                self.assertEqual(self.submit('bill').status_code,200)
                self.assertEqual(ServiceCall.objects.count(),2)
                self.assertFalse(Order.objects.exists())
                ServiceCall.objects.all().delete()

    def test_both_waiter_accounts_receive_changes_and_acknowledgement_is_not_overwritten(self):
        self.submit();self.submit('bill')
        first,second=[self.staff_client(user) for user in self.waiters]
        data=first.get(self.feed,{'board':'1','queue':'open','lang':'en'}).json()
        self.assertEqual(data['active_count'],2)
        self.assertEqual({call['kind'] for call in data['latest']},{'waiter','bill'})
        self.assertIn('Garden table',data['html'])
        self.assertIn('no-store',first.get(self.feed)['Cache-Control'])
        self.assertEqual(second.get(self.feed).json()['active_count'],2)
        unchanged=first.get(self.feed,{'board':'1','queue':'open','signature':data['signature']}).json()
        self.assertNotIn('html',unchanged)
        call=ServiceCall.objects.get(kind='waiter')
        action=reverse('call_status_change',args=[self.venue.slug,call.pk])
        first.post(action,{'status':'acknowledged'})
        second.post(action,{'status':'acknowledged'})
        call.refresh_from_db();self.assertEqual(call.assigned_to_id,self.waiters[0].pk)
        changed=second.get(self.feed,{'board':'1','queue':'open','signature':data['signature']}).json()
        self.assertNotEqual(changed['signature'],data['signature'])
        self.assertIn('Waiter 0',changed['html'])
        for status in ('in_progress','done'):first.post(action,{'status':status})
        public=self.client.get(self.status,HTTP_X_TABLE_SESSION=str(self.table.session_token)).json()
        self.assertNotIn('waiter',public['calls'])
        self.assertEqual(second.get(self.feed).json()['active_count'],1)

    def test_feed_is_scoped_to_the_membership_branch_and_role(self):
        self.submit()
        ServiceCall.objects.create(table=self.other_table,kind='bill')
        client=self.staff_client(self.waiters[0])
        session=client.session;session[f'safra_branch_{self.venue.pk}']=self.second.pk;session.save()
        response=client.get(self.feed,{'board':'1','branch':self.second.pk})
        self.assertEqual(response.json()['branch'],self.branch.pk)
        self.assertEqual(response.json()['active_count'],1)
        self.assertNotIn('Hidden table',response.json()['html'])
        foreign=Restaurant.objects.create(name='Foreign',slug='service-foreign')
        self.assertEqual(client.get(reverse('service_call_feed',args=[foreign.slug])).status_code,403)
        kitchen=User.objects.create_user('service-kitchen')
        member=Membership.objects.create(restaurant=self.venue,user=kitchen,role='kitchen');member.branches.add(self.branch)
        self.assertEqual(self.staff_client(kitchen).get(self.feed).status_code,403)
        self.assertEqual(self.client.get(self.feed).status_code,302)
        Membership.objects.filter(user=self.waiters[0]).update(active=False)
        self.assertEqual(client.get(self.feed).status_code,403)

    def test_public_status_requires_the_current_table_session_and_exposes_only_states(self):
        self.submit()
        ServiceCall.objects.create(table=self.other_table,kind='bill')
        self.assertEqual(self.client.get(self.status).status_code,409)
        self.assertEqual(self.client.get(self.status,HTTP_X_TABLE_SESSION='invalid').status_code,409)
        result=self.client.get(self.status,HTTP_X_TABLE_SESSION=str(self.table.session_token))
        self.assertEqual(result.json(),{'ok':True,'available':True,'calls':{'waiter':{'status':'open'}}})
        self.assertIn('no-store',result['Cache-Control'])
        old=self.table.session_token
        DiningTable.objects.filter(pk=self.table.pk).update(session_token=uuid4())
        self.assertEqual(self.client.get(self.status,HTTP_X_TABLE_SESSION=str(old)).status_code,409)
        self.assertEqual(self.submit('bill').status_code,400)

    def test_service_posts_require_csrf_and_preserve_the_no_javascript_fallback(self):
        guest=Client(enforce_csrf_checks=True)
        self.assertEqual(guest.post(self.url,{'kind':'waiter','session_token':self.table.session_token}).status_code,403)
        self.assertEqual(self.client.get(self.url).status_code,405)
        response=self.client.post(self.url,{'kind':'bill','session_token':self.table.session_token})
        self.assertRedirects(response,reverse('public_menu',args=[self.table.public_token]))
        self.assertEqual(ServiceCall.objects.count(),1)

    def test_paused_archived_and_expired_tables_cannot_submit_service_requests(self):
        for field,value in [('active',False),('archived',True)]:
            DiningTable.objects.filter(pk=self.table.pk).update(**{field:value})
            self.assertEqual(self.submit().status_code,400)
            DiningTable.objects.filter(pk=self.table.pk).update(**{field:not value})
        self.branch.accepting_orders=False;self.branch.save()
        self.assertEqual(self.submit().status_code,400)
        self.branch.accepting_orders=True;self.branch.save()
        self.subscription.ends_at=timezone.now()-timedelta(days=1);self.subscription.save()
        self.assertEqual(self.submit().status_code,400)
        self.assertFalse(ServiceCall.objects.exists())

    def test_feed_filters_paginates_and_escapes_merchant_names(self):
        self.table.name='<script>alert(1)</script>';self.table.save()
        self.submit()
        for i in range(30):
            table=DiningTable.objects.create(branch=self.branch,name='Queue '+str(i))
            ServiceCall.objects.create(table=table,kind='bill')
        client=self.staff_client(self.waiters[0])
        data=client.get(self.feed,{'board':'1','page':'2'}).json()
        self.assertEqual(data['active_count'],31)
        self.assertNotIn('<script>alert',data['html'])
        self.assertIn('&lt;script&gt;',data['html'])
        bills=client.get(self.feed,{'board':'1','queue':'bill'}).json()
        self.assertNotIn('&lt;script&gt;',bills['html'])
