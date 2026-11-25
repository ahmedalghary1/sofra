from datetime import timedelta
from decimal import Decimal
from importlib import import_module
from uuid import uuid4

from django.apps import apps
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.db import connection
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from .menu_import import commit_menu_import
from .models import Branch, Category, DiningTable, Membership, MenuImportBatch, MenuItem, Plan, Restaurant, Subscription, SubscriptionAgreement
from .online import create_online_order
from .plan_features import FEATURES, has_feature
from .services import create_order
from .subscriptions import freeze_plan_subscribers, record_agreement


class PlanServiceTests(TestCase):
    def setUp(self):
        self.plan=Plan.objects.get(name='Starter')
        self.venue=Restaurant.objects.create(name='Merchant name stays',slug='services',online_ordering_enabled=True,
            online_delivery_enabled=True,delivery_fee='5.50')
        self.branch=Branch.objects.create(restaurant=self.venue)
        self.table=DiningTable.objects.create(branch=self.branch,name='Garden')
        self.item=MenuItem.objects.create(category=Category.objects.create(branch=self.branch,name='Meals'),name='Original item',price='25.75')
        self.owner=User.objects.create_user('service-owner')
        Membership.objects.create(user=self.owner,restaurant=self.venue,role='owner')
        self.subscription=Subscription.objects.create(restaurant=self.venue,plan=self.plan,status='active',ends_at=timezone.now()+timedelta(days=30))
        self.client.force_login(self.owner)

    def payload(self):
        return {'idempotency_key':str(uuid4()),'lines':[{'item_id':self.item.pk,'quantity':1}],
                'fulfillment':'delivery','customer_name':'Guest','customer_phone':'01012345678',
                'delivery_address':'12 Test street','reviewed_total':'31.25','reviewed_delivery_fee':'5.50'}

    def select_plan(self,name):
        self.subscription.plan=Plan.objects.get(name=name)
        self.subscription.agreed_terms={}
        self.subscription.save()

    def test_starter_has_core_service_and_data_export_but_cannot_bypass_paid_services(self):
        create_order(self.table,{'idempotency_key':str(uuid4()),'lines':[{'item_id':self.item.pk,'quantity':2}]})
        for language in ('ar','en'):
            report=self.client.get(reverse('reports',args=[self.venue.slug]),{'lang':language})
            self.assertEqual(report.status_code,200)
            self.assertNotContains(report,'Preparation time' if language=='en' else 'أزمنة التشغيل')
            self.assertNotContains(report,'name="station"')
            self.assertNotContains(report,'Best-selling items' if language=='en' else 'أكثر الأصناف طلبًا')
        for export in ('csv','collections'):
            self.assertEqual(self.client.get(reverse('reports',args=[self.venue.slug]),{'export':export}).status_code,200)
        for params in ({'scope':'all'},{'station':'unassigned'}):
            self.assertEqual(self.client.get(reverse('reports',args=[self.venue.slug]),params).status_code,403)
        self.assertEqual(self.client.get(reverse('menu_import',args=[self.venue.slug])).status_code,403)
        self.assertEqual(self.client.post(reverse('menu_import',args=[self.venue.slug]),{'action':'preview'}).status_code,403)
        self.assertEqual(self.client.get(reverse('online_menu',args=[self.venue.slug])).status_code,404)
        self.assertEqual(self.client.post(reverse('restaurant_settings',args=[self.venue.slug]),{'action':'online'}).status_code,403)
        with self.assertRaises(ValidationError):create_online_order(self.branch,self.payload())

    def test_growth_and_business_unlock_their_real_workflows(self):
        self.select_plan('Growth')
        order=create_online_order(self.branch,self.payload())
        self.assertEqual(order.total,Decimal('31.25'))
        self.assertEqual(self.client.get(reverse('menu_import',args=[self.venue.slug])).status_code,200)
        self.assertContains(self.client.get(reverse('reports',args=[self.venue.slug]),{'lang':'en'}),'name="station"')
        self.assertEqual(self.client.get(reverse('reports',args=[self.venue.slug]),{'scope':'all'}).status_code,403)
        self.select_plan('Business')
        Branch.objects.create(restaurant=self.venue,name='Second branch')
        report=self.client.get(reverse('reports',args=[self.venue.slug]),{'scope':'all','lang':'en'})
        self.assertContains(report,'Branch comparison')
        self.assertContains(report,'Second branch')

    def test_retry_of_a_saved_order_keeps_snapshot_after_loss_of_service(self):
        self.select_plan('Growth')
        payload=self.payload()
        order=create_online_order(self.branch,payload)
        self.select_plan('Starter')
        self.assertEqual(create_online_order(self.branch,payload).pk,order.pk)
        with self.assertRaises(ValidationError):create_online_order(self.branch,self.payload())
        self.item.price='40.00';self.item.save()
        self.assertEqual(order.lines.get().unit_price,Decimal('25.75'))

    def test_import_commit_rechecks_feature_and_does_not_insert_rows(self):
        batch=MenuImportBatch.objects.create(branch=self.branch,user=self.owner,
            rows=[{'category':'New','name':'Imported','price':'10.00','description':''}])
        with self.assertRaises(ValidationError):commit_menu_import(batch,self.owner)
        self.assertEqual(MenuItem.objects.count(),1)
        self.select_plan('Growth')
        self.assertEqual(commit_menu_import(batch,self.owner),1)

    def test_plan_edits_freeze_services_and_only_explicit_agreement_changes_them(self):
        self.select_plan('Growth')
        freeze_plan_subscribers(self.subscription.plan_id,self.owner)
        Plan.objects.filter(pk=self.subscription.plan_id).update(enabled_features=[])
        self.subscription.refresh_from_db()
        self.assertTrue(has_feature(self.subscription,'online_ordering'))
        record_agreement(self.subscription,self.owner,'Services changed explicitly')
        self.assertFalse(has_feature(self.subscription,'online_ordering'))
        self.assertEqual(self.subscription.agreements.count(),2)

    def test_migration_preserves_existing_terms_and_historic_records(self):
        Plan.objects.filter(name='Growth').update(max_staff=12,enabled_features=None)
        self.select_plan('Growth')
        existing=SubscriptionAgreement.objects.create(subscription=self.subscription,terms={'historic':'unchanged'},
            status='active',starts_on=timezone.localdate(),ends_at=self.subscription.ends_at,reason='Historic record')
        import_module('core.migrations.0029_plan_services').divide_services(apps,type('Schema',(),{'connection':connection})())
        self.subscription.refresh_from_db();existing.refresh_from_db()
        self.assertEqual(self.subscription.limit('max_staff'),12)
        self.assertEqual(Plan.objects.get(name='Growth').max_staff,8)
        self.assertEqual(existing.terms,{'historic':'unchanged'})
        self.assertTrue(all(has_feature(self.subscription,key) for key in FEATURES))
        self.assertEqual(self.venue.name,'Merchant name stays')
        self.item.refresh_from_db()
        self.assertEqual(self.item.price,Decimal('25.75'))

    def test_legacy_custom_plans_keep_access_and_invalid_feature_values_are_rejected(self):
        self.subscription.plan=Plan.objects.create(name='Existing custom agreement')
        self.subscription.save()
        self.assertTrue(all(has_feature(self.subscription,key) for key in FEATURES))
        for value in ({},['unknown'],[1]):
            self.plan.enabled_features=value
            with self.assertRaises(ValidationError):self.plan.full_clean()

    def test_all_public_surfaces_share_services_and_realistic_team_totals(self):
        for lang in ('ar','en'):
            page=self.client.get(reverse('pricing'),{'lang':lang})
            plans=page.context['plans']
            self.assertEqual([p['limits']['max_staff'] for p in plans],[4,8,15])
            self.assertEqual([p['staff']['waiter'] for p in plans],[1,3,6])
            self.assertEqual([len(p['features']) for p in plans],[0,3,4])
            for plan in plans:
                key='data-service="online_ordering" data-included="'+('false' if plan['slug']=='starter' else 'true')+'"'
                self.assertContains(page,key)
                contact=self.client.get(reverse('contact'),{'plan':plan['slug'],'lang':lang})
                self.assertContains(contact,key)
                self.assertEqual(sum(plan['staff'].values()),plan['limits']['max_staff'])
            self.assertEqual(self.client.get(reverse('features'),{'q':'Excel','plan':'starter'}).context['entries'],[])
            self.assertEqual(len(self.client.get(reverse('features'),{'q':'Excel','plan':'growth','category':'digital-menu'}).context['entries']),1)

    def test_downgrade_preview_reports_lost_services_without_changing_the_contract(self):
        self.select_plan('Business')
        response=self.client.get(reverse('restaurant_settings',args=[self.venue.slug]),{'preview_plan':self.plan.pk,'lang':'en'})
        self.assertEqual(len(response.context['plan_preview']['removed_features']),4)
        self.subscription.refresh_from_db()
        self.assertEqual(self.subscription.plan.name,'Business')
