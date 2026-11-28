from copy import deepcopy
from datetime import timedelta
from decimal import Decimal
import math
import time
from unittest.mock import patch
from uuid import uuid4

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from .geofencing import distance_m, presence_policy, validate_presence
from .models import AuditLog, Branch, Category, DiningTable, Membership, MenuItem, Order, OrderHistory, Plan, Restaurant, Subscription
from .services import create_order
from .table_codes import stable_table_url


class GeofencingTests(TestCase):
    def setUp(self):
        self.restaurant=Restaurant.objects.create(name='مطعم الحديقة',slug='geofence-test')
        self.plan=Plan.objects.create(name='Geofence test',max_branches=3)
        Subscription.objects.create(restaurant=self.restaurant,plan=self.plan,status='active',ends_at=timezone.now()+timedelta(days=7))
        self.branch=Branch.objects.create(restaurant=self.restaurant,name='الحديقة',latitude=30,longitude=31,geofence_enabled=True)
        self.other=Branch.objects.create(restaurant=self.restaurant,name='فرع آخر')
        self.table=DiningTable.objects.create(branch=self.branch,name='1')
        self.item=MenuItem.objects.create(category=Category.objects.create(branch=self.branch,name='أطباق'),name='طبق قديم',price='12.25')
        self.owner=User.objects.create_user('geofence-owner')
        self.manager=User.objects.create_user('geofence-manager')
        self.waiter=User.objects.create_user('geofence-waiter')
        Membership.objects.create(user=self.owner,restaurant=self.restaurant,role='owner')
        member=Membership.objects.create(user=self.manager,restaurant=self.restaurant,role='manager');member.branches.add(self.branch)
        member=Membership.objects.create(user=self.waiter,restaurant=self.restaurant,role='waiter');member.branches.add(self.branch)
        self.operator=User.objects.create_superuser('geofence-operator',email='operator@example.test')
        self.url=reverse('place_order',args=[self.table.public_token])
        self.settings=reverse('restaurant_settings',args=[self.restaurant.slug])

    def payload(self,metres=0):
        return {'idempotency_key':str(uuid4()),'session_token':str(self.table.session_token),
            'lines':[{'item_id':self.item.pk,'quantity':1}], 'note':'بدون ملح',
            'presence':{'challenge':presence_policy(self.table)['challenge'],'latitude':30+math.degrees(metres/6371008.8),
                'longitude':31,'accuracy':5,'timestamp':timezone.now().timestamp()*1000}}

    def post(self,payload):
        return self.client.post(self.url,payload,content_type='application/json')

    def form_data(self,branch=None,**changes):
        branch=branch or self.branch
        data={'action':'geofence','geofence-branch_id':branch.pk,'geofence-version':str(branch.geofence_version),
            'geofence-geofence_enabled':'on','geofence-latitude':'30.000000','geofence-longitude':'31.000000',
            'geofence-geofence_radius_m':'75'}
        data.update(changes)
        return data

    def test_inside_order_keeps_decimal_snapshots_and_stores_no_guest_coordinates(self):
        payload=self.payload(49.9);response=self.post(payload)
        self.assertEqual(response.status_code,200,response.content)
        order=Order.objects.get();line=order.lines.get()
        self.assertEqual((order.total,line.item_name,line.unit_price),(Decimal('12.25'),'طبق قديم',Decimal('12.25')))
        self.assertEqual(order.history.count(),1)
        self.assertFalse(any('latitude' in field.name or 'longitude' in field.name or 'presence' in field.name for field in Order._meta.fields))
        self.assertEqual(AuditLog.objects.count(),0)

    def test_outside_is_rejected_without_expanding_by_accuracy_or_writing(self):
        for metres in (50.1,75,10000):
            payload=self.payload(metres);payload['presence']['accuracy']=50
            response=self.post(payload)
            self.assertEqual(response.status_code,400)
            self.assertEqual(response.json()['code'],'presence_outside')
        self.assertEqual(Order.objects.count(),0);self.assertEqual(OrderHistory.objects.count(),0)

    def test_custom_radius_is_taken_from_database_even_with_stale_table_instance(self):
        self.branch.geofence_radius_m=150;self.branch.save()
        self.assertEqual(self.post(self.payload(100)).status_code,200)
        Branch.objects.filter(pk=self.branch.pk).update(geofence_radius_m=25,geofence_version=uuid4())
        response=self.post(self.payload(10))
        self.assertEqual(response.json()['code'],'presence_expired')

    def test_missing_invalid_and_non_finite_coordinates_fail_closed(self):
        for field,value in [('latitude',None),('longitude','31'),('latitude',True),('latitude',91),('longitude',181),
            ('latitude',float('nan')),('longitude',float('inf')),('accuracy',-1),('accuracy',True),('timestamp','now')]:
            with self.subTest(field=field,value=value):
                data=self.payload();data['presence'][field]=value
                with self.assertRaises(ValidationError):create_order(self.table,data,require_session=True)
        for proof in (None,[],True,{},'fake'):
            data=self.payload();data['presence']=proof
            self.assertEqual(self.post(data).status_code,400)
        self.assertEqual(Order.objects.count(),0)

    def test_stale_future_and_low_accuracy_positions_are_rejected(self):
        for offset in (-61000,16000):
            data=self.payload();data['presence']['timestamp']+=offset
            self.assertEqual(self.post(data).json()['code'],'presence_stale')
        data=self.payload();data['presence']['accuracy']=51
        self.assertEqual(self.post(data).json()['code'],'presence_accuracy')
        Branch.objects.filter(pk=self.branch.pk).update(geofence_radius_m=10)
        data=self.payload();data['presence']['accuracy']=11
        self.assertEqual(self.post(data).json()['code'],'presence_accuracy')

    def test_challenge_is_signed_short_lived_and_bound_to_table_session_and_configuration(self):
        data=self.payload();data['presence']['challenge']='fake'
        self.assertEqual(self.post(data).json()['code'],'presence_expired')
        data=self.payload()
        with patch('django.core.signing.time.time',return_value=time.time()+121):
            self.assertEqual(self.post(data).json()['code'],'presence_expired')
        second=DiningTable.objects.create(branch=self.branch,name='2')
        data=self.payload();data['session_token']=str(second.session_token)
        response=self.client.post(reverse('place_order',args=[second.public_token]),data,content_type='application/json')
        self.assertEqual(response.json()['code'],'presence_expired')
        data=self.payload();DiningTable.objects.filter(pk=self.table.pk).update(session_token=uuid4())
        self.assertEqual(self.post(data).status_code,400)

    def test_saved_order_recovers_without_location_and_after_configuration_or_session_changes(self):
        data=self.payload();first=self.post(data).json()
        data.pop('presence');data['recover_only']=True
        Branch.objects.filter(pk=self.branch.pk).update(geofence_radius_m=10,geofence_version=uuid4())
        DiningTable.objects.filter(pk=self.table.pk).update(session_token=uuid4())
        self.assertEqual(self.post(data).json(),first)
        data['note']='changed'
        self.assertEqual(self.post(data).status_code,400)
        self.assertEqual(Order.objects.count(),1)

    def test_recovery_probe_never_creates_an_order_even_when_feature_disabled(self):
        data=self.payload();data['recover_only']=True
        self.assertEqual(self.post(data).status_code,404)
        self.assertEqual(self.post(data).json()['code'],'order_not_found')
        Branch.objects.filter(pk=self.branch.pk).update(geofence_enabled=False)
        self.assertEqual(self.post(data).status_code,404)
        self.assertEqual(Order.objects.count(),0)

    def test_public_cannot_bypass_check_with_staff_or_distance_flags(self):
        data=self.payload(1000);data.update(user=self.owner.pk,staffMode=True,distance=0,geofence_enabled=False)
        self.assertEqual(self.post(data).json()['code'],'presence_outside')
        data.pop('presence')
        with self.assertRaises(ValidationError):create_order(self.table,data,require_session=False)
        order=create_order(self.table,data,user=self.waiter)
        self.assertEqual(order.source,'staff')

    def test_unconfigured_branches_default_to_off_and_qr_url_remains_fixed(self):
        self.assertFalse(self.other.geofence_enabled);self.assertEqual(self.other.geofence_radius_m,50)
        original=stable_table_url(self.table)
        self.client.force_login(self.owner)
        response=self.client.post(self.settings,self.form_data())
        self.assertEqual(response.status_code,302)
        self.table.refresh_from_db();self.assertEqual(stable_table_url(self.table),original)
        self.assertEqual(self.branch.tables.count(),1)

    def test_settings_scope_owner_manager_role_and_optimistic_version(self):
        self.client.force_login(self.owner);data=self.form_data()
        self.assertEqual(self.client.post(self.settings,data).status_code,302)
        self.branch.refresh_from_db();self.assertEqual(self.branch.geofence_radius_m,75)
        changed=self.branch.geofence_version
        self.assertEqual(self.client.post(self.settings,{**data,'geofence-geofence_radius_m':'200'}).status_code,200)
        self.branch.refresh_from_db();self.assertEqual(self.branch.geofence_version,changed)
        self.assertEqual(AuditLog.objects.filter(action='branch.geofence').count(),1)
        self.client.force_login(self.manager)
        self.assertEqual(self.client.post(self.settings,self.form_data()).status_code,302)
        self.assertEqual(self.client.post(self.settings,self.form_data(self.other)).status_code,200)
        self.other.refresh_from_db();self.assertFalse(self.other.geofence_enabled)
        self.client.force_login(self.waiter)
        self.assertEqual(self.client.post(self.settings,self.form_data()).status_code,403)

    def test_settings_cross_tenant_rejected_and_no_original_names_or_prices_change(self):
        other=Restaurant.objects.create(name='Other tenant',slug='geofence-other')
        foreign=Branch.objects.create(restaurant=other)
        self.client.force_login(self.owner)
        self.assertEqual(self.client.get(reverse('restaurant_settings',args=[other.slug])).status_code,403)
        self.assertEqual(self.client.post(self.settings,self.form_data(foreign)).status_code,200)
        self.restaurant.refresh_from_db();self.item.refresh_from_db()
        self.assertEqual((self.restaurant.name,self.item.name,self.item.price),('مطعم الحديقة','طبق قديم',Decimal('12.25')))

    def test_settings_invalid_bounds_missing_point_and_csrf(self):
        self.client.force_login(self.owner)
        for change in ({'geofence-geofence_radius_m':'9'},{'geofence-geofence_radius_m':'5001'},
            {'geofence-latitude':'91'},{'geofence-longitude':'-181'}, {'geofence-latitude':'NaN'},
            {'geofence-latitude':'','geofence-longitude':''},{'geofence-longitude':''}):
            response=self.client.post(self.settings,self.form_data(**change))
            self.assertEqual(response.status_code,200);self.assertContains(response,'error')
        csrf=Client(enforce_csrf_checks=True);csrf.force_login(self.owner)
        self.assertEqual(csrf.post(self.settings,self.form_data()).status_code,403)
        csrf=Client(enforce_csrf_checks=True)
        self.assertEqual(csrf.post(self.url,self.payload(),content_type='application/json').status_code,403)

    def test_platform_can_edit_each_restaurant_branch_only_with_reason(self):
        url=reverse('platform_geofence',args=[self.restaurant.pk,self.branch.pk])
        self.client.force_login(self.owner);self.assertEqual(self.client.get(url).status_code,403)
        self.client.force_login(self.operator)
        self.assertEqual(self.client.post(url,self.form_data()).status_code,200)
        self.assertEqual(self.client.post(url,self.form_data(**{'geofence-reason':'Venue floor expanded'})).status_code,302)
        self.assertEqual(AuditLog.objects.get(action='branch.geofence').changes['platform'],True)
        self.assertEqual(self.client.get(reverse('platform_geofence',args=[self.restaurant.pk+1,self.branch.pk])).status_code,404)

    def test_database_constraints_reject_partial_location_and_invalid_radius(self):
        for changes in ({'geofence_radius_m':9},{'latitude':None},{'longitude':181},
                        {'latitude':None,'longitude':None,'geofence_enabled':True}):
            with self.assertRaises(IntegrityError),transaction.atomic():
                Branch.objects.filter(pk=self.branch.pk).update(**changes)

    def test_zero_coordinates_and_dateline_are_valid_not_treated_as_missing(self):
        self.assertAlmostEqual(distance_m(0,0,0,0),0)
        self.assertLess(distance_m(0,179.9999,0,-179.9999),23)
        self.assertLess(distance_m(89.999,0,89.999,0.001),1)
        self.branch.latitude=Decimal('0');self.branch.longitude=Decimal('0');self.branch.save()
        data=self.payload();data['presence'].update(latitude=0,longitude=0)
        self.assertEqual(self.post(data).status_code,200)

    def test_policy_is_safe_no_cache_and_paused_branches_cannot_get_challenge(self):
        url=reverse('table_presence',args=[self.table.public_token])
        response=self.client.get(url)
        self.assertEqual(response.status_code,200);self.assertIn('no-store',response['Cache-Control'])
        self.assertNotIn('latitude',response.json());self.assertNotIn('longitude',response.json())
        self.assertEqual(self.client.post(url).status_code,405)
        self.branch.accepting_orders=False;self.branch.save()
        self.assertEqual(self.client.get(url).status_code,404)

    def test_disabled_check_and_online_ordering_do_not_require_coordinates(self):
        from .online import create_online_order
        self.branch.geofence_enabled=False;self.branch.save()
        data=self.payload() if self.branch.geofence_enabled else {'idempotency_key':str(uuid4()),'lines':[{'item_id':self.item.pk,'quantity':1}]}
        self.assertEqual(self.post({**data,'session_token':str(self.table.session_token)}).status_code,200)
        self.branch.geofence_enabled=True;self.branch.save()
        self.restaurant.online_ordering_enabled=True;self.restaurant.save()
        order=create_online_order(self.branch,{'idempotency_key':str(uuid4()),'lines':[{'item_id':self.item.pk,'quantity':1}],
            'fulfillment':'pickup','customer_name':'Test','customer_phone':'01012345678',
            'reviewed_total':'12.25','reviewed_delivery_fee':'0.00'})
        self.assertEqual(order.fulfillment,'pickup')


from django.test import TransactionTestCase, skipUnlessDBFeature


class GeofenceConcurrencyTests(TransactionTestCase):
    @skipUnlessDBFeature('has_select_for_update')
    def test_waiting_order_rechecks_committed_location_policy_before_inserting(self):
        from concurrent.futures import ThreadPoolExecutor
        from threading import Event
        from django.db import connections
        venue=Restaurant.objects.create(name='Concurrent location',slug='geofence-concurrency')
        Subscription.objects.create(restaurant=venue,plan=Plan.objects.create(name='Geofence race'),status='active',
                                    ends_at=timezone.now()+timedelta(days=1))
        branch=Branch.objects.create(restaurant=venue,latitude=0,longitude=0,geofence_enabled=True)
        table=DiningTable.objects.create(branch=branch,name='1')
        item=MenuItem.objects.create(category=Category.objects.create(branch=branch,name='Meals'),name='Meal',price=10)
        data={'idempotency_key':str(uuid4()),'lines':[{'item_id':item.pk,'quantity':1}],
              'presence':{'challenge':presence_policy(table)['challenge'],'latitude':0,'longitude':0,'accuracy':5,
                          'timestamp':timezone.now().timestamp()*1000}}
        locked,started,release=Event(),Event(),Event()
        def configure():
            try:
                with transaction.atomic():
                    current=Branch.objects.select_for_update().get(pk=branch.pk)
                    current.geofence_radius_m=10;current.geofence_version=uuid4();current.save()
                    locked.set();release.wait(timeout=10)
            finally:connections.close_all()
        def order():
            try:
                started.set()
                try:create_order(table,data)
                except ValidationError as error:return error.code
                return 'created'
            finally:connections.close_all()
        with ThreadPoolExecutor(max_workers=2) as pool:
            setting=pool.submit(configure);self.assertTrue(locked.wait(timeout=10))
            pending=pool.submit(order);self.assertTrue(started.wait(timeout=10));release.set()
            setting.result(timeout=20);self.assertEqual(pending.result(timeout=20),'presence_expired')
        self.assertEqual(Order.objects.count(),0)
