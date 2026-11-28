"""Adversarial URL, role, tenant, quota and delegated-admin regression checks."""
from datetime import timedelta
from decimal import Decimal
from hashlib import sha256
from unittest.mock import patch
from uuid import uuid4

from django.contrib.auth.models import Permission, User
from django.db import transaction
from django.test import Client, TestCase, TransactionTestCase, skipUnlessDBFeature
from django.urls import reverse
from django.utils import timezone

from .models import (AuditLog, Branch, Category, DiningTable, KitchenStation, Membership, MenuItem, MenuSize,
    ModifierGroup, ModifierOption, Order, Plan, Restaurant, ServiceCall, StaffInvitation, Subscription)
from .services import create_order

ROLES=('owner','manager','cashier','kitchen','waiter')
MANAGEMENT={'owner','manager'}
SALES={'owner','manager','cashier','waiter'}
PREP={'owner','manager','kitchen'}
SERVICE={'owner','manager','cashier','waiter'}


class AccessAuditTests(TestCase):
    def setUp(self):
        self.venue=Restaurant.objects.create(name='Venue original',slug='access-audit')
        self.plan=Plan.objects.create(name='Access audit',max_branches=2,max_tables=2,max_items=2,max_staff=8,max_stations=2)
        self.sub=Subscription.objects.create(restaurant=self.venue,plan=self.plan,status='active',ends_at=timezone.now()+timedelta(days=7))
        self.branch=Branch.objects.create(restaurant=self.venue,name='First')
        self.second=Branch.objects.create(restaurant=self.venue,name='Second')
        self.foreign=Restaurant.objects.create(name='Other tenant',slug='access-foreign')
        Subscription.objects.create(restaurant=self.foreign,plan=self.plan,status='active',ends_at=timezone.now()+timedelta(days=7))
        self.foreign_branch=Branch.objects.create(restaurant=self.foreign)
        self.table=DiningTable.objects.create(branch=self.branch,name='Original table')
        self.category=Category.objects.create(branch=self.branch,name='Original category')
        self.item=MenuItem.objects.create(category=self.category,name='Original item',price='25.75')
        self.station=KitchenStation.objects.create(branch=self.branch,name='Original station')
        self.size=MenuSize.objects.create(item=self.item,name='Original size',price='30.00')
        self.group=ModifierGroup.objects.create(item=self.item,name='Options')
        self.option=ModifierOption.objects.create(group=self.group,name='Sauce',price='2.00')
        self.order=create_order(self.table,self.payload())
        self.call=ServiceCall.objects.create(table=self.table,kind='waiter')
        self.users={};self.members={}
        for role in ROLES:
            user=User.objects.create_user('access-'+role,email='access-'+role+'@example.test')
            member=Membership.objects.create(user=user,restaurant=self.venue,role=role)
            member.branches.add(self.branch)
            self.users[role]=user;self.members[role]=member
        self.operator=User.objects.create_superuser('access-operator',email='operator@example.test')

    def payload(self):
        return {'idempotency_key':str(uuid4()),'lines':[{'item_id':self.item.pk,'quantity':1}]}

    def url(self,name,*args,foreign=False):
        return reverse(name,args=[self.foreign.slug if foreign else self.venue.slug,*args])

    def get_routes(self):
        all_roles=set(ROLES)
        return [
            ('dashboard',[],all_roles),('manage_orders',[],all_roles),('manage_menu',[],MANAGEMENT),
            ('menu_import',[],MANAGEMENT),('manage_tables',[],MANAGEMENT),('table_qr',[self.table.pk],MANAGEMENT),
            ('table_qr_pack',[self.branch.pk],MANAGEMENT),('table_summary',[self.table.pk],{'owner','manager','cashier'}),
            ('staff_order',[],SALES),('order_edit',[self.order.pk],SALES),('kitchen',[],PREP),('kitchen_feed',[],PREP),
            ('manage_calls',[],SERVICE),('service_call_feed',[],SERVICE),('reports',[],MANAGEMENT),
            ('staff',[],MANAGEMENT),('manage_branches',[],MANAGEMENT),('restaurant_settings',[],MANAGEMENT),
            ('resource_archive_list',[],MANAGEMENT)]

    def mutation_routes(self):
        return [('manage_menu',[],MANAGEMENT,{'action':'price','item_id':self.item.pk,'price':'99'}),
            ('manage_tables',[],MANAGEMENT,{'action':'create','name':'Unauthorized table'}),
            ('manage_orders',[],SALES,{'table_id':self.table.pk,'payload':'{}'}),
            ('staff_order',[],SALES,{}),('order_edit',[self.order.pk],SALES,{}),
            ('staff',[],MANAGEMENT,{'action':'toggle','membership_id':self.members['waiter'].pk}),
            ('manage_branches',[],MANAGEMENT,{'action':'toggle','branch_id':self.branch.pk}),
            ('restaurant_settings',[],MANAGEMENT,{'name':'Unauthorized name'}),
            ('order_status_change',[self.order.pk],MANAGEMENT,{'status':'cancelled','reason':'Unauthorized cancellation'}),
            ('order_line_status_change',[self.order.pk,self.order.lines.get().pk],PREP,{'station':'unassigned','status':'preparing'}),
            ('order_status_correct',[self.order.pk],MANAGEMENT,{'reason':'Unauthorized correction'}),
            ('order_payment_change',[self.order.pk],{'owner','manager','cashier'},{}),
            ('collection_correct',[self.order.pk],MANAGEMENT,{}),
            ('call_status_change',[self.call.pk],SERVICE,{'status':'acknowledged'}),
            ('resource_archive',['item',self.item.pk],MANAGEMENT,{})]

    def snapshot(self):
        models=(Restaurant,Branch,DiningTable,MenuItem,Membership,Order,ServiceCall,AuditLog)
        return {model._meta.label:list(model.objects.order_by('pk').values()) for model in models}

    def test_staff_get_role_matrix_and_anonymous_access_for_all_workspace_views(self):
        for role in ROLES:
            self.client.force_login(self.users[role])
            for name,args,allowed in self.get_routes():
                with self.subTest(role=role,view=name):
                    response=self.client.get(self.url(name,*args))
                    self.assertEqual(response.status_code,200 if role in allowed else 403)
                    if getattr(response,'streaming',False):response.close()
        self.client.logout()
        for name,args,_ in self.get_routes():
            response=self.client.get(self.url(name,*args));self.assertEqual(response.status_code,302,name)
            self.assertIn(reverse('login'),response.url)

    def test_denied_roles_and_anonymous_cannot_mutate_even_using_direct_post(self):
        for role in (*ROLES,None):
            self.client.logout()
            if role:self.client.force_login(self.users[role])
            for name,args,allowed,data in self.mutation_routes():
                if role in allowed:continue
                with self.subTest(role=role,view=name):
                    before=self.snapshot()
                    response=self.client.post(self.url(name,*args),data,
                        content_type='application/json' if name in ('staff_order','order_edit') else 'application/x-www-form-urlencoded') if name in ('staff_order','order_edit') else self.client.post(self.url(name,*args),data)
                    self.assertEqual(response.status_code,403 if role else 302)
                    self.assertEqual(self.snapshot(),before)

    def test_cross_tenant_get_and_post_matrix_cannot_read_or_write_any_workspace(self):
        self.client.force_login(self.users['owner'])
        for name,args,_ in self.get_routes():
            response=self.client.get(self.url(name,*args,foreign=True));self.assertEqual(response.status_code,403,name)
        for name,args,_,data in self.mutation_routes():
            before=self.snapshot()
            self.assertEqual(self.client.post(self.url(name,*args,foreign=True),data).status_code,403,name)
            self.assertEqual(self.snapshot(),before,name)

    def test_other_branch_objects_and_forged_branch_selection_cannot_escape_staff_assignment(self):
        foreign_table=DiningTable.objects.create(branch=self.second,name='Second table')
        foreign_item=MenuItem.objects.create(category=Category.objects.create(branch=self.second,name='Second category'),name='Other item',price=17)
        other_order=create_order(foreign_table,{'idempotency_key':str(uuid4()),'lines':[{'item_id':foreign_item.pk,'quantity':1}]})
        for role in ('cashier','kitchen','waiter'):
            self.client.force_login(self.users[role]);session=self.client.session
            session[f'safra_branch_{self.venue.pk}']=self.second.pk;session.save()
            self.assertEqual(self.client.post(self.url('select_branch'),{'branch_id':self.second.pk}).status_code,404)
            self.assertEqual(self.client.get(self.url('manage_orders'),{'table':foreign_table.pk}).status_code,404)
            self.assertEqual(self.client.post(self.url('order_status_change',other_order.pk),{'status':'served'}).status_code,404)
            response=self.client.get(self.url('manage_orders'),{'branch':self.second.pk,'restaurant_id':self.foreign.pk})
            self.assertNotContains(response,'Other item')
        self.assertEqual(Order.objects.get(pk=other_order.pk).status,'new')

    def test_same_tenant_other_branch_menu_objects_cannot_be_mutated_from_current_branch(self):
        category=Category.objects.create(branch=self.second,name='Second')
        item=MenuItem.objects.create(category=category,name='Second',price='7.25')
        table=DiningTable.objects.create(branch=self.second,name='Second')
        self.client.force_login(self.users['owner'])
        for action,data in [('price',{'item_id':item.pk,'price':'0.01'}),('item',{'category':category.pk,'name':'Injected','price':'1'}),
                            ('category_toggle',{'category_id':category.pk})]:
            self.assertEqual(self.client.post(self.url('manage_menu'),{'action':action,**data}).status_code,404)
        self.assertEqual(self.client.get(self.url('table_qr',table.pk)).status_code,404)
        self.assertEqual(self.client.get(self.url('table_qr_pack',self.second.pk)).status_code,409)
        item.refresh_from_db();self.assertEqual(item.price,Decimal('7.25'))

    def test_revoked_membership_blocks_existing_sessions_on_reads_posts_and_feeds(self):
        for flag in ({'active':False},{'archived':True}):
            self.client.force_login(self.users['owner'])
            Membership.objects.filter(pk=self.members['owner'].pk).update(**flag)
            for name,args,_ in self.get_routes():self.assertEqual(self.client.get(self.url(name,*args)).status_code,403,name)
            for name,args,_,data in self.mutation_routes():self.assertEqual(self.client.post(self.url(name,*args),data).status_code,403,name)
            Membership.objects.filter(pk=self.members['owner'].pk).update(active=True,archived=False)

    def test_platform_routes_require_superuser_even_for_owner_and_django_staff(self):
        routes=[('platform_dashboard',[]),('platform_accounts',[]),('platform_requests',[]),('platform_receipts',[]),
            ('platform_activity',[]),('platform_plan_create',[]),('platform_plan_edit',[self.plan.pk]),
            ('platform_create_restaurant',[]),('platform_account_create',[]),('platform_account_edit',[self.users['owner'].pk]),
            ('platform_geofence',[self.venue.pk,self.branch.pk])]
        posts=[('platform_branch_change',[self.venue.pk]),('platform_update_restaurant',[self.venue.pk]),
            ('platform_record_payment',[self.venue.pk]),('platform_start_trial',[self.venue.pk]),
            ('platform_member_update',[self.venue.pk,self.members['waiter'].pk])]
        for role in ROLES:
            self.client.force_login(self.users[role])
            for name,args in routes:self.assertEqual(self.client.get(reverse(name,args=args)).status_code,403,name)
            for name,args in posts:self.assertEqual(self.client.post(reverse(name,args=args),{'reason':'Forged'}).status_code,403,name)

    def test_django_model_permissions_do_not_grant_tenant_staff_global_admin_access(self):
        staff=self.users['manager'];staff.is_staff=True;staff.save(update_fields=['is_staff'])
        staff.user_permissions.set(Permission.objects.all())
        self.client.force_login(staff)
        for name,args in [('admin:index',[]),('admin:core_restaurant_changelist',[]),('admin:core_plan_changelist',[]),
                          ('admin:core_plan_change',[self.plan.pk]),('admin:auth_user_changelist',[])]:
            response=self.client.get(reverse(name,args=args))
            self.assertIn(response.status_code,(302,403),name)
        self.assertNotEqual(self.client.post(reverse('admin:core_plan_change',args=[self.plan.pk]),
            {'name':'Forged global plan','price_monthly':'0','max_tables':999,'change_reason':'Forged'}).status_code,200)
        self.plan.refresh_from_db();self.assertEqual(self.plan.name,'Access audit')
        self.client.force_login(self.operator)
        self.assertEqual(self.client.get(reverse('admin:core_restaurant_changelist')).status_code,200)

    def test_manager_cannot_promote_self_manage_owner_or_other_manager(self):
        self.client.force_login(self.users['manager'])
        for target in ('owner','manager'):
            for action in ('role','toggle','branch'):
                self.assertEqual(self.client.post(self.url('staff'),{'action':action,'membership_id':self.members[target].pk,
                    'role':'manager','branch_id':self.second.pk}).status_code,403)
            self.assertEqual(self.client.post(self.url('resource_archive','staff',self.members[target].pk)).status_code,403)
        self.client.post(self.url('staff'),{'action':'create','email':'escalated@example.test','password':'UnusualPassword!147','role':'manager'})
        self.assertFalse(User.objects.filter(email='escalated@example.test').exists())

    def test_staff_toggle_rechecks_target_role_after_acquiring_resource_lock(self):
        self.client.force_login(self.users['manager'])
        from .resources import lock_resource
        def promote_before_lock(restaurant,model,pk):
            Membership.objects.filter(pk=pk).update(role='manager')
            return lock_resource(restaurant,model,pk)
        with patch('core.views.lock_resource',side_effect=promote_before_lock):
            self.client.post(self.url('staff'),{'action':'toggle','membership_id':self.members['waiter'].pk})
        self.members['waiter'].refresh_from_db();self.assertTrue(self.members['waiter'].active)

    def test_archived_members_free_invitation_capacity_and_import_preview_capacity(self):
        self.client.force_login(self.users['owner'])
        Membership.objects.filter(pk=self.members['waiter'].pk).update(active=False,archived=True)
        Plan.objects.filter(pk=self.plan.pk).update(max_staff=5)
        self.client.post(self.url('staff'),{'action':'invite','role':'waiter','branch_id':self.branch.pk,'email':'replacement@example.test'})
        self.assertTrue(StaffInvitation.objects.filter(email='replacement@example.test').exists())
        MenuItem.objects.create(category=self.category,name='Archived',price=12,available=False,archived=True)
        response=self.client.get(self.url('menu_import'))
        self.assertEqual(response.context['remaining'],1)

    def test_table_limit_cannot_be_reset_by_deactivation_single_bulk_or_restore(self):
        from .resources import archive_resource
        self.client.force_login(self.users['owner'])
        second=DiningTable.objects.create(branch=self.branch,name='Capacity',active=False)
        self.client.post(self.url('manage_tables'),{'action':'create','name':'Over limit'})
        self.client.post(self.url('manage_tables'),{'action':'bulk_create','branch_id':self.branch.pk,'submission_key':str(uuid4()),'count':1,'start_number':90})
        self.assertEqual(self.branch.tables.filter(archived=False).count(),2)
        archive_resource(self.venue,self.users['owner'],'table',second.pk)
        self.client.post(self.url('manage_tables'),{'action':'create','name':'Replacement'})
        self.client.post(self.url('resource_archive','table',second.pk),{'action':'restore'})
        second.refresh_from_db();self.assertTrue(second.archived)
        self.assertEqual(self.branch.tables.filter(archived=False).count(),2)

    def test_item_station_branch_and_staff_limits_cannot_be_forged_in_post(self):
        self.client.force_login(self.users['owner'])
        MenuItem.objects.create(category=self.category,name='Capacity',price=3,available=False)
        KitchenStation.objects.create(branch=self.branch,name='Capacity',active=False)
        Plan.objects.filter(pk=self.plan.pk).update(max_staff=5)
        attempts=[('manage_menu',{'action':'item','name':'Extra','category':self.category.pk,'price':'1','max_items':999}),
            ('manage_menu',{'action':'station_create','name':'Extra','max_stations':999}),
            ('manage_branches',{'action':'create','name':'Extra','plan_id':Plan.objects.get(name='Business').pk}),
            ('staff',{'action':'create','email':'extra@example.test','password':'UnusualPassword!147','role':'waiter','max_staff':999}),
            ('staff',{'action':'invite','email':'invite-extra@example.test','role':'waiter','branch_id':self.branch.pk})]
        for name,data in attempts:self.client.post(self.url(name),data)
        self.assertEqual(self.category.items.filter(archived=False).count(),2)
        self.assertEqual(KitchenStation.objects.filter(branch=self.branch).count(),2)
        self.assertEqual(self.venue.branches.count(),2);self.assertEqual(self.venue.memberships.count(),5)
        self.assertFalse(StaffInvitation.objects.exists())

    def test_expired_and_suspended_subscription_deny_new_resources_but_keep_history(self):
        self.client.force_login(self.users['owner'])
        for state in ('expired','suspended'):
            if state=='expired':Subscription.objects.filter(pk=self.sub.pk).update(ends_at=timezone.now()-timedelta(seconds=1))
            else:Restaurant.objects.filter(pk=self.venue.pk).update(active=False)
            before=self.snapshot()
            for name,data in [('manage_tables',{'action':'create','name':'Extra'}),
                ('manage_menu',{'action':'category','name':'Extra'}),('manage_branches',{'action':'create','name':'Extra'}),
                ('staff',{'action':'invite','email':'expired@example.test','role':'waiter','branch_id':self.branch.pk})]:
                self.client.post(self.url(name),data)
            self.assertEqual(self.snapshot(),before)
            self.assertEqual(self.client.get(self.url('reports'),{'export':'csv'}).status_code,200)
            self.assertEqual(Order.objects.get(pk=self.order.pk).lines.get().unit_price,Decimal('25.75'))

    def test_public_qr_token_cannot_be_used_as_staff_authentication(self):
        self.client.logout()
        for name,args,_ in self.get_routes():
            response=self.client.get(self.url(name,*args),{'token':self.table.public_token,'session_token':str(self.table.session_token),
                                                         'role':'owner','restaurant_id':self.venue.pk})
            self.assertEqual(response.status_code,302,name)

    def test_unsafe_workspace_and_platform_mutations_require_csrf(self):
        client=Client(enforce_csrf_checks=True);client.force_login(self.users['owner'])
        for name,args,_,data in self.mutation_routes():self.assertEqual(client.post(self.url(name,*args),data).status_code,403,name)
        client.force_login(self.operator)
        self.assertEqual(client.post(reverse('platform_update_restaurant',args=[self.venue.pk]),{'action':'toggle','reason':'CSRF'}).status_code,403)

    def test_owner_cannot_change_agreed_limits_or_subscription_through_settings_or_resource_payloads(self):
        self.client.force_login(self.users['owner'])
        from .subscriptions import record_agreement
        record_agreement(self.sub,self.operator,'Existing agreed contract')
        before=list(Subscription.objects.filter(pk=self.sub.pk).values())
        self.client.post(self.url('restaurant_settings'),{'action':'subscription','plan_id':Plan.objects.get(name='Business').pk,
            'status':'active','max_staff':999,'agreed_terms':'{}','ends_at':'2099-12-31'})
        self.client.post(self.url('manage_tables'),{'action':'create','name':'Allowed extra','max_tables':999,'plan_id':self.plan.pk})
        self.assertEqual(list(Subscription.objects.filter(pk=self.sub.pk).values()),before)
        self.assertEqual(self.sub.agreements.count(),1)

    def test_downgrade_blocks_activation_beyond_active_limits_without_deleting_history(self):
        self.client.force_login(self.users['owner'])
        second=DiningTable.objects.create(branch=self.branch,name='Inactive',active=False)
        Plan.objects.filter(pk=self.plan.pk).update(max_tables=1,max_items=1,max_branches=1,max_staff=4)
        extra=MenuItem.objects.create(category=self.category,name='Inactive',price=5,available=False)
        Branch.objects.filter(pk=self.second.pk).update(active=False)
        self.client.post(self.url('manage_tables'),{'action':'toggle','table_id':second.pk})
        self.client.post(self.url('manage_menu'),{'action':'toggle','item_id':extra.pk})
        self.client.post(self.url('manage_branches'),{'action':'toggle','branch_id':self.second.pk})
        Membership.objects.filter(pk=self.members['waiter'].pk).update(active=False)
        self.client.post(self.url('staff'),{'action':'toggle','membership_id':self.members['waiter'].pk})
        second.refresh_from_db();extra.refresh_from_db();self.second.refresh_from_db();self.members['waiter'].refresh_from_db()
        self.assertFalse(second.active or extra.available or self.second.active or self.members['waiter'].active)
        self.assertEqual(Order.objects.get(pk=self.order.pk).total,Decimal('25.75'))
        self.assertEqual(self.venue.memberships.count(),5)

    def test_import_over_quota_is_atomic_and_checks_the_current_agreement(self):
        from .menu_import import commit_menu_import
        from .models import MenuImportBatch
        from django.core.exceptions import ValidationError
        batch=MenuImportBatch.objects.create(branch=self.branch,user=self.users['owner'],rows=[
            {'category':'New category','name':f'Imported {i}','description':'','price':'1.00'} for i in range(2)])
        with self.assertRaises(ValidationError):commit_menu_import(batch,self.users['owner'])
        self.assertFalse(Category.objects.filter(branch=self.branch,name='New category').exists())
        self.assertEqual(self.category.items.count(),1);batch.refresh_from_db();self.assertIsNone(batch.committed_at)

    def test_pending_invitation_reserves_a_seat_for_direct_staff_creation_and_acceptance(self):
        from .invitations import accept_staff_invitation
        from django.core.exceptions import ValidationError
        Plan.objects.filter(pk=self.plan.pk).update(max_staff=6)
        token='audit-reserved-token'
        invite=StaffInvitation.objects.create(restaurant=self.venue,branch=self.branch,email='reserved@example.test',
            role='waiter',invited_by=self.users['owner'],token_hash=sha256(token.encode()).hexdigest(),expires_at=timezone.now()+timedelta(days=1))
        self.client.force_login(self.users['owner'])
        self.client.post(self.url('staff'),{'action':'create','email':'over-reservation@example.test','role':'waiter','password':'UnusualPassword!147'})
        self.assertFalse(User.objects.filter(email='over-reservation@example.test').exists())
        user,_=accept_staff_invitation(token,'Test guest','UnusualPassword!147')
        self.assertEqual(self.venue.memberships.count(),6)
        self.assertTrue(Membership.objects.filter(user=user,restaurant=self.venue).exists())
        with self.assertRaises(ValidationError):accept_staff_invitation(token,'Test guest','UnusualPassword!147')

    def test_invitation_cannot_be_accepted_after_inviter_is_archived_or_loses_management(self):
        from .invitations import accept_staff_invitation
        from django.core.exceptions import ValidationError
        token='audit-manager-invite'
        StaffInvitation.objects.create(restaurant=self.venue,branch=self.branch,email='revoked-inviter@example.test',
            role='waiter',invited_by=self.users['manager'],token_hash=sha256(token.encode()).hexdigest(),expires_at=timezone.now()+timedelta(days=1))
        for change in ({'archived':True},{'role':'waiter'}):
            Membership.objects.filter(pk=self.members['manager'].pk).update(active=True,archived=False,role='manager')
            Membership.objects.filter(pk=self.members['manager'].pk).update(**change)
            with self.assertRaises(ValidationError):accept_staff_invitation(token,'Guest','UnusualPassword!147')
        self.assertFalse(User.objects.filter(email='revoked-inviter@example.test').exists())

    def test_resource_archiving_rechecks_manager_protection_in_locked_service(self):
        from .resources import archive_resource
        self.client.force_login(self.users['manager'])
        def promoted(venue,actor,resource,pk,**kwargs):
            Membership.objects.filter(pk=pk).update(role='manager')
            return archive_resource(venue,actor,resource,pk,**kwargs)
        with patch('core.views.archive_resource',side_effect=promoted):
            self.assertEqual(self.client.post(self.url('resource_archive','staff',self.members['waiter'].pk)).status_code,403)
        self.members['waiter'].refresh_from_db();self.assertFalse(self.members['waiter'].archived)


class AccessQuotaConcurrencyTests(TransactionTestCase):
    def venue(self,slug):
        venue=Restaurant.objects.create(name='Concurrency audit',slug=slug)
        plan=Plan.objects.create(name=slug,max_branches=2,max_tables=1,max_items=1,max_staff=2,max_stations=1)
        Subscription.objects.create(restaurant=venue,plan=plan,status='active',ends_at=timezone.now()+timedelta(days=7))
        branch=Branch.objects.create(restaurant=venue)
        owner=User.objects.create_user(slug+'-owner')
        Membership.objects.create(user=owner,restaurant=venue,role='owner')
        return venue,branch,owner

    def parallel(self,first,second):
        from concurrent.futures import ThreadPoolExecutor
        from threading import Barrier
        from django.db import connections
        barrier=Barrier(2)
        def run(operation):
            try:
                barrier.wait(timeout=10)
                return operation()
            finally:connections.close_all()
        with ThreadPoolExecutor(max_workers=2) as pool:
            a,b=pool.submit(run,first),pool.submit(run,second)
            return a.result(timeout=30),b.result(timeout=30)

    @skipUnlessDBFeature('has_select_for_update')
    def test_competing_http_posts_cannot_exceed_each_of_the_five_resource_limits(self):
        for resource in ('table','item','staff','station','branch'):
            with self.subTest(resource=resource):
                venue,branch,owner=self.venue('quota-race-'+resource)
                category=Category.objects.create(branch=branch,name='Meals')
                client=Client();client.force_login(owner);cookie=client.cookies['sessionid'].value
                def submit(suffix):
                    worker=Client();worker.cookies['sessionid']=cookie
                    if resource=='table':
                        name='manage_tables';data={'action':'create','name':suffix}
                    elif resource=='item':
                        name='manage_menu';data={'action':'item','name':suffix,'price':'1','category':category.pk}
                    elif resource=='station':
                        name='manage_menu';data={'action':'station_create','name':suffix}
                    elif resource=='branch':
                        name='manage_branches';data={'action':'create','name':suffix}
                    else:
                        name='staff';data={'action':'create','role':'waiter','email':suffix+'@example.test','password':'UnusualPassword!147'}
                    return worker.post(reverse(name,args=[venue.slug]),data).status_code
                self.assertEqual(self.parallel(lambda:submit('candidate-a'),lambda:submit('candidate-b')),(302,302))
                actual={'table':DiningTable.objects.filter(branch=branch).count(),
                        'item':MenuItem.objects.filter(category__branch=branch).count(),
                        'staff':Membership.objects.filter(restaurant=venue).count(),
                        'station':KitchenStation.objects.filter(branch=branch).count(),
                        'branch':Branch.objects.filter(restaurant=venue).count()}
                self.assertEqual(actual[resource],2 if resource in ('branch','staff') else 1)

    @skipUnlessDBFeature('has_select_for_update')
    def test_restore_and_create_compete_for_the_same_reserved_capacity(self):
        from .resources import archive_resource
        from django.core.exceptions import ValidationError
        from .limits import check_creation_limit
        venue,branch,owner=self.venue('quota-restore-race')
        archived=DiningTable.objects.create(branch=branch,name='Archived',archived=True,active=False)
        def restore():
            try:archive_resource(venue,owner,'table',archived.pk,restore=True);return True
            except ValidationError:return False
        def create():
            try:
                with transaction.atomic():
                    check_creation_limit(venue,'table',branch)
                    DiningTable.objects.create(branch=branch,name='New')
                return True
            except ValidationError:return False
        self.assertEqual(sum(self.parallel(restore,create)),1)
        self.assertEqual(DiningTable.objects.filter(branch=branch,archived=False).count(),1)

    @skipUnlessDBFeature('has_select_for_update')
    def test_branch_archive_and_intake_cannot_both_succeed_or_deadlock(self):
        from .resources import archive_resource
        from django.core.exceptions import ValidationError
        venue,branch,owner=self.venue('quota-archive-race')
        table=DiningTable.objects.create(branch=branch,name='1')
        item=MenuItem.objects.create(category=Category.objects.create(branch=branch,name='Meals'),name='Meal',price=10)
        def archive():
            try:archive_resource(venue,owner,'branch',branch.pk);return True
            except ValidationError:return False
        def order():
            try:create_order(table,{'idempotency_key':str(uuid4()),'lines':[{'item_id':item.pk,'quantity':1}]});return True
            except ValidationError:return False
        results=self.parallel(archive,order)
        self.assertEqual(sum(results),1)
        branch.refresh_from_db();self.assertEqual(branch.archived,results[0])
        self.assertEqual(Order.objects.filter(branch=branch).count(),int(results[1]))
