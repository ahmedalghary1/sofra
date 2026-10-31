import json
import re
import uuid
from decimal import Decimal
from datetime import timedelta
from django.contrib.auth.models import User
from django.contrib.admin.models import LogEntry
from django.core import mail
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.test import Client
from django.urls import reverse
from django.utils import timezone
from .models import AuditLog, Branch, Category, DiningTable, LoginAttempt, Membership, MenuItem, ModifierGroup, ModifierOption, Order, Plan, Restaurant, ServiceCall, Subscription
from .services import advance_call, advance_order, create_order, create_service_call

class SafraTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(username='owner@example.com', email='owner@example.com', password='StrongPass!123', first_name='منى')
        self.other = User.objects.create_user(username='other@example.com', password='StrongPass!123')
        self.restaurant = Restaurant.objects.create(name='مطعم سفرة', slug='safra-test')
        plan = Plan.objects.create(name='Test', max_tables=10, max_items=50, max_staff=5)
        Subscription.objects.create(restaurant=self.restaurant, plan=plan, ends_at=timezone.now()+timedelta(days=14))
        self.branch = Branch.objects.create(restaurant=self.restaurant)
        Membership.objects.create(user=self.owner, restaurant=self.restaurant, role='owner')
        self.category = Category.objects.create(branch=self.branch, name='الأطباق')
        self.item = MenuItem.objects.create(category=self.category, name='طبق', price=Decimal('100.00'))
        self.table = DiningTable.objects.create(branch=self.branch, name='طاولة 1')
        self.other_restaurant = Restaurant.objects.create(name='مطعم آخر', slug='other-test')
        self.other_branch = Branch.objects.create(restaurant=self.other_restaurant)
        Membership.objects.create(user=self.other, restaurant=self.other_restaurant, role='owner')

    def payload(self, options=None):
        return {'idempotency_key': str(uuid.uuid4()), 'lines': [{'item_id': self.item.pk, 'quantity': 2, 'options': options or []}]}

    def test_order_price_snapshot_and_idempotency(self):
        payload = self.payload()
        order = create_order(self.table, payload)
        self.assertEqual(order.total, Decimal('200.00'))
        self.item.price = Decimal('150.00'); self.item.save()
        self.assertEqual(create_order(self.table, payload).pk, order.pk)
        self.assertEqual(order.lines.get().unit_price, Decimal('100.00'))
        self.assertEqual(Order.objects.count(), 1)

    def test_reject_foreign_item_and_modifier(self):
        foreign_category = Category.objects.create(branch=self.other_branch, name='غير متاح')
        foreign_item = MenuItem.objects.create(category=foreign_category, name='آخر', price=10)
        bad = self.payload(); bad['lines'][0]['item_id'] = foreign_item.pk
        with self.assertRaises(ValidationError): create_order(self.table, bad)
        group = ModifierGroup.objects.create(item=foreign_item, name='إضافات')
        option = ModifierOption.objects.create(group=group, name='جبن', price=10)
        with self.assertRaises(ValidationError): create_order(self.table, self.payload([option.pk]))

    def test_valid_modifier_total_and_closed_table(self):
        group = ModifierGroup.objects.create(item=self.item, name='حجم', min_select=1, max_select=1)
        option = ModifierOption.objects.create(group=group, name='كبير', price=Decimal('20.00'))
        with self.assertRaises(ValidationError): create_order(self.table, self.payload())
        order = create_order(self.table, self.payload([option.pk]))
        self.assertEqual(order.total, Decimal('240.00'))
        self.table.active = False; self.table.save()
        with self.assertRaises(ValidationError): create_order(self.table, self.payload([option.pk]))

    def test_order_transitions(self):
        order = create_order(self.table, self.payload())
        with self.assertRaises(ValidationError): advance_order(order, self.owner, 'ready')
        for status in ('accepted','preparing','ready','served'):
            advance_order(order, self.owner, status)
        order.refresh_from_db()
        self.assertEqual(order.status, 'served')
        self.assertEqual(order.history.count(), 5)

    def test_tenant_access_and_public_order(self):
        self.client.login(username='other@example.com', password='StrongPass!123')
        self.assertEqual(self.client.get(reverse('dashboard', args=[self.restaurant.slug])).status_code, 403)
        self.assertEqual(self.client.get(reverse('manage_menu', args=[self.restaurant.slug])).status_code, 403)
        self.assertEqual(self.client.get(reverse('reports', args=[self.restaurant.slug])+'?export=csv').status_code, 403)
        self.client.logout()
        self.assertEqual(self.client.get(reverse('public_menu', args=[self.table.public_token])).status_code, 200)
        response = self.client.post(reverse('place_order', args=[self.table.public_token]), data=json.dumps(self.payload()), content_type='application/json')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(Order.objects.count(), 1)

    def test_service_call_rate_and_duplicate(self):
        create_service_call(self.table, 'waiter')
        with self.assertRaises(ValidationError): create_service_call(self.table, 'waiter')
        create_service_call(self.table, 'bill')
        self.assertEqual(ServiceCall.objects.count(), 2)

    def test_admin_pages_render(self):
        self.client.login(username='owner@example.com', password='StrongPass!123')
        for name in ('dashboard','manage_menu','manage_tables','manage_orders','kitchen','manage_calls','reports','staff','restaurant_settings'):
            with self.subTest(name=name):
                response = self.client.get(reverse(name, args=[self.restaurant.slug]))
                self.assertEqual(response.status_code, 200)

    def test_registration_creates_isolated_restaurant(self):
        response = self.client.post(reverse('register'), {'name':'ليلى','email':'new@example.com','restaurant':'بيتنا','password':'StrongPass!123'})
        self.assertEqual(response.status_code, 302)
        created = Restaurant.objects.get(name='بيتنا')
        self.assertTrue(Membership.objects.filter(user__username='new@example.com',restaurant=created,role='owner').exists())
        self.assertEqual(created.branches.count(), 1)
        self.assertTrue(created.subscription.valid())

    def test_public_order_requires_csrf(self):
        client = Client(enforce_csrf_checks=True)
        endpoint = reverse('place_order', args=[self.table.public_token])
        self.assertEqual(client.post(endpoint, data=json.dumps(self.payload()), content_type='application/json').status_code, 403)
        response = client.get(reverse('public_menu', args=[self.table.public_token]))
        self.assertEqual(response.status_code, 200)
        token = client.cookies['csrftoken'].value
        self.assertEqual(client.post(endpoint, data=json.dumps(self.payload()), content_type='application/json', HTTP_X_CSRFTOKEN=token).status_code, 200)

    def test_role_cannot_change_order_outside_permissions(self):
        waiter = User.objects.create_user(username='waiter@example.com',password='StrongPass!123')
        waiter_membership = Membership.objects.create(user=waiter,restaurant=self.restaurant,role='waiter')
        waiter_membership.branches.add(self.branch)
        order = create_order(self.table,self.payload())
        self.client.login(username='waiter@example.com',password='StrongPass!123')
        response = self.client.post(reverse('order_status_change',args=[self.restaurant.slug,order.pk]),{'status':'accepted'})
        self.assertEqual(response.status_code,403)
        order.refresh_from_db()
        self.assertEqual(order.status,'new')

    def test_expired_subscription_blocks_new_orders_and_items(self):
        subscription = self.restaurant.subscription
        subscription.status = 'expired'; subscription.save()
        with self.assertRaises(ValidationError): create_order(self.table, self.payload())
        self.client.login(username='owner@example.com',password='StrongPass!123')
        count = MenuItem.objects.count()
        self.client.post(reverse('manage_menu',args=[self.restaurant.slug]),{'action':'item','category':self.category.pk,'name':'جديد','price':'10'})
        self.assertEqual(MenuItem.objects.count(),count)

    def test_end_to_end_management_to_kitchen(self):
        self.client.login(username='owner@example.com',password='StrongPass!123')
        response = self.client.post(reverse('manage_tables',args=[self.restaurant.slug]),{'action':'create','name':'طاولة 2'})
        self.assertEqual(response.status_code,302)
        table = DiningTable.objects.get(branch=self.branch,name='طاولة 2')
        self.assertEqual(self.client.get(reverse('table_qr',args=[self.restaurant.slug,table.pk])).get('Content-Type'),'image/png')
        self.client.logout()
        data = self.payload(); data['lines'][0]['quantity'] = 1
        response = self.client.post(reverse('place_order',args=[table.public_token]),data=json.dumps(data),content_type='application/json')
        self.assertEqual(response.status_code,200)
        order = Order.objects.get(table=table)
        self.client.login(username='owner@example.com',password='StrongPass!123')
        self.assertContains(self.client.get(reverse('kitchen',args=[self.restaurant.slug])),f'طلب #{order.pk}')
        self.client.post(reverse('order_status_change',args=[self.restaurant.slug,order.pk]),{'status':'accepted'})
        order.refresh_from_db()
        self.assertEqual(order.status,'accepted')

    def test_plan_table_limit(self):
        plan = self.restaurant.subscription.plan
        plan.max_tables = 1; plan.save()
        self.client.login(username='owner@example.com',password='StrongPass!123')
        self.client.post(reverse('manage_tables',args=[self.restaurant.slug]),{'action':'create','name':'طاولة 2'})
        self.assertEqual(DiningTable.objects.filter(branch=self.branch).count(),1)

    def test_branch_plan_limit_and_staff_assignment(self):
        self.client.login(username='owner@example.com',password='StrongPass!123')
        endpoint = reverse('manage_branches',args=[self.restaurant.slug])
        self.client.post(endpoint,{'action':'create','name':'فرع جديد'})
        self.assertEqual(self.restaurant.branches.count(),1)
        plan = self.restaurant.subscription.plan
        plan.max_branches = 2; plan.save()
        self.client.post(endpoint,{'action':'create','name':'فرع جديد'})
        second = self.restaurant.branches.get(name='فرع جديد')
        self.client.post(reverse('select_branch',args=[self.restaurant.slug]),{'branch_id':second.pk})
        self.client.post(reverse('staff',args=[self.restaurant.slug]),{
            'action':'create','email':'newstaff@example.com','role':'waiter','password':'StrongPass!123'})
        member = Membership.objects.get(user__username='newstaff@example.com',restaurant=self.restaurant)
        self.assertEqual(list(member.branches.values_list('id',flat=True)),[second.pk])

    def test_branch_selection_scopes_pages_mutations_and_export(self):
        second = Branch.objects.create(restaurant=self.restaurant, name='الفرع الثاني')
        second_category = Category.objects.create(branch=second, name='وجبات ثانية')
        second_item = MenuItem.objects.create(category=second_category, name='صنف ثاني', price=Decimal('30.00'))
        second_table = DiningTable.objects.create(branch=second, name='طاولة ثانية')
        first_order = create_order(self.table, self.payload())
        second_order = create_order(second_table, {'idempotency_key':str(uuid.uuid4()), 'lines':[{'item_id':second_item.pk,'quantity':1,'options':[]}]})
        self.client.login(username='owner@example.com',password='StrongPass!123')
        self.assertEqual(self.client.post(reverse('select_branch',args=[self.restaurant.slug]),{'branch_id':second.pk}).status_code,302)
        self.assertContains(self.client.get(reverse('manage_menu',args=[self.restaurant.slug])),'صنف ثاني')
        self.assertNotContains(self.client.get(reverse('manage_menu',args=[self.restaurant.slug])),'طبق')
        kitchen = self.client.get(reverse('kitchen',args=[self.restaurant.slug]))
        self.assertContains(kitchen,f'طلب #{second_order.pk}')
        self.assertNotContains(kitchen,f'طلب #{first_order.pk}')
        export = self.client.get(reverse('reports',args=[self.restaurant.slug])+'?export=csv').content.decode('utf-8-sig')
        self.assertIn(str(second_order.pk),export)
        self.assertNotIn(f'{first_order.pk},',export)
        self.assertEqual(self.client.post(reverse('order_status_change',args=[self.restaurant.slug,first_order.pk]),{'status':'accepted'}).status_code,404)
        self.assertEqual(self.client.get(reverse('table_qr',args=[self.restaurant.slug,self.table.pk])).status_code,404)

    def test_staff_cannot_switch_to_unassigned_branch(self):
        second = Branch.objects.create(restaurant=self.restaurant, name='الفرع الثاني')
        waiter = User.objects.create_user(username='branchwaiter@example.com',password='StrongPass!123')
        membership = Membership.objects.create(user=waiter,restaurant=self.restaurant,role='waiter')
        membership.branches.add(self.branch)
        self.client.login(username=waiter.username,password='StrongPass!123')
        self.assertEqual(self.client.post(reverse('select_branch',args=[self.restaurant.slug]),{'branch_id':second.pk}).status_code,404)
        self.assertEqual(self.client.get(reverse('dashboard',args=[self.restaurant.slug])).status_code,200)

    def test_disabling_staff_revokes_access_and_login(self):
        waiter = User.objects.create_user(username='disabled@example.com',password='StrongPass!123')
        member = Membership.objects.create(user=waiter,restaurant=self.restaurant,role='waiter')
        member.branches.add(self.branch)
        self.client.login(username='owner@example.com',password='StrongPass!123')
        response = self.client.post(reverse('staff',args=[self.restaurant.slug]),{'action':'toggle','membership_id':member.pk})
        self.assertEqual(response.status_code,302)
        member.refresh_from_db()
        self.assertFalse(member.active)
        self.client.logout()
        self.client.post(reverse('login'),{'email':waiter.username,'password':'StrongPass!123'})
        self.assertFalse('_auth_user_id' in self.client.session)

    def test_password_reset_does_not_enumerate_accounts_and_changes_password(self):
        response = self.client.post(reverse('password_reset'), {'email':'missing@example.com'})
        self.assertRedirects(response, reverse('password_reset_done'))
        self.assertEqual(len(mail.outbox), 0)
        response = self.client.post(reverse('password_reset'), {'email':self.owner.email})
        self.assertRedirects(response, reverse('password_reset_done'))
        self.assertEqual(len(mail.outbox), 1)
        link = re.search(r'https?://testserver(/reset/[^\s]+)', mail.outbox[0].body)
        self.assertIsNotNone(link)
        response = self.client.get(link.group(1))
        self.assertEqual(response.status_code, 302)
        response = self.client.post(response['Location'], {'new_password1':'NewStrongPass!456','new_password2':'NewStrongPass!456'})
        self.assertRedirects(response, reverse('password_reset_complete'))
        self.owner.refresh_from_db()
        self.assertTrue(self.owner.check_password('NewStrongPass!456'))

    def test_order_actions_match_employee_role(self):
        order = create_order(self.table,self.payload())
        waiter = User.objects.create_user(username='waiteractions@example.com',password='StrongPass!123')
        waiter_member = Membership.objects.create(user=waiter,restaurant=self.restaurant,role='waiter')
        waiter_member.branches.add(self.branch)
        self.client.login(username=waiter.username,password='StrongPass!123')
        page = self.client.get(reverse('manage_orders',args=[self.restaurant.slug]))
        self.assertNotContains(page,'استلام ←')
        self.client.logout()
        cook = User.objects.create_user(username='cook@example.com',password='StrongPass!123')
        cook_member = Membership.objects.create(user=cook,restaurant=self.restaurant,role='kitchen')
        cook_member.branches.add(self.branch)
        self.client.login(username=cook.username,password='StrongPass!123')
        self.assertContains(self.client.get(reverse('manage_orders',args=[self.restaurant.slug])),'استلام ←')

    def test_menu_edit_keeps_order_snapshot_and_hides_category(self):
        old_order = create_order(self.table,self.payload())
        self.client.login(username='owner@example.com',password='StrongPass!123')
        endpoint = reverse('manage_menu',args=[self.restaurant.slug])
        response = self.client.post(endpoint,{'action':'item_edit','item_id':self.item.pk,
            'category':self.category.pk,'name':'طبق جديد','description':'وصف جديد','price':'125.50'})
        self.assertEqual(response.status_code,302)
        self.item.refresh_from_db()
        self.assertEqual(self.item.price,Decimal('125.50'))
        self.assertEqual(old_order.lines.get().item_name,'طبق')
        self.assertEqual(old_order.lines.get().unit_price,Decimal('100.00'))
        self.client.post(endpoint,{'action':'category_toggle','category_id':self.category.pk})
        self.assertNotContains(self.client.get(reverse('public_menu',args=[self.table.public_token])),'طبق جديد')
        with self.assertRaises(ValidationError): create_order(self.table,self.payload())

    def test_unavailable_modifier_and_foreign_menu_edit(self):
        group = ModifierGroup.objects.create(item=self.item,name='إضافات',max_select=1)
        option = ModifierOption.objects.create(group=group,name='جبن',price=Decimal('10.00'))
        foreign_item = MenuItem.objects.create(category=Category.objects.create(branch=self.other_branch,name='أخرى'),name='أجنبي',price=10)
        self.client.login(username='owner@example.com',password='StrongPass!123')
        endpoint = reverse('manage_menu',args=[self.restaurant.slug])
        response = self.client.post(endpoint,{'action':'item_edit','item_id':foreign_item.pk,
            'category':self.category.pk,'name':'تغيير','price':'20'})
        self.assertEqual(response.status_code,404)
        self.client.post(endpoint,{'action':'modifier_toggle','option_id':option.pk})
        option.refresh_from_db()
        self.assertFalse(option.available)
        with self.assertRaises(ValidationError): create_order(self.table,self.payload([option.pk]))

    def test_kitchen_feed_changes_and_rejects_other_roles(self):
        self.client.login(username='owner@example.com',password='StrongPass!123')
        endpoint = reverse('kitchen_feed',args=[self.restaurant.slug])
        before = self.client.get(endpoint)
        self.assertEqual(before.status_code,200)
        self.assertEqual(before.json()['count'],0)
        self.assertEqual(before['Cache-Control'],'no-store')
        order = create_order(self.table,self.payload())
        after = self.client.get(endpoint)
        self.assertEqual(after.json()['count'],1)
        self.assertNotEqual(before.json()['signature'],after.json()['signature'])
        advance_order(order,self.owner,'accepted')
        self.assertNotEqual(after.json()['signature'],self.client.get(endpoint).json()['signature'])
        self.client.logout()
        waiter = User.objects.create_user(username='feedwaiter@example.com',password='StrongPass!123')
        member = Membership.objects.create(user=waiter,restaurant=self.restaurant,role='waiter')
        member.branches.add(self.branch)
        self.client.login(username=waiter.username,password='StrongPass!123')
        self.assertEqual(self.client.get(endpoint).status_code,403)

    def test_downgrade_preserves_table_and_blocks_reactivation_above_limit(self):
        second = DiningTable.objects.create(branch=self.branch,name='طاولة إضافية',active=False)
        plan = self.restaurant.subscription.plan
        plan.max_tables = 1; plan.save()
        self.client.login(username='owner@example.com',password='StrongPass!123')
        endpoint = reverse('manage_tables',args=[self.restaurant.slug])
        response = self.client.post(endpoint,{'action':'toggle','table_id':second.pk})
        self.assertEqual(response.status_code,302)
        second.refresh_from_db()
        self.assertFalse(second.active)
        self.assertEqual(DiningTable.objects.filter(branch=self.branch).count(),2)

    def test_staff_order_multiple_items_modifiers_and_tenant_scope(self):
        second = MenuItem.objects.create(category=self.category,name='مشروب',price=Decimal('50.00'))
        group = ModifierGroup.objects.create(item=self.item,name='الحجم',min_select=1,max_select=1)
        option = ModifierOption.objects.create(group=group,name='كبير',price=Decimal('20.00'))
        self.client.login(username='owner@example.com',password='StrongPass!123')
        endpoint = reverse('staff_order',args=[self.restaurant.slug])
        self.assertContains(self.client.get(endpoint),'اختر طاولة الطلب')
        payload = {'table_id':self.table.pk,'idempotency_key':str(uuid.uuid4()),'note':'بدون ملح',
                   'lines':[{'item_id':self.item.pk,'quantity':1,'options':[option.pk]},
                            {'item_id':second.pk,'quantity':2,'options':[]}]}
        response = self.client.post(endpoint,data=json.dumps(payload),content_type='application/json')
        self.assertEqual(response.status_code,200)
        order = Order.objects.get(table=self.table)
        self.assertEqual(order.source,'staff')
        self.assertEqual(order.total,Decimal('220.00'))
        self.assertEqual(order.lines.count(),2)
        self.client.post(endpoint,data=json.dumps(payload),content_type='application/json')
        self.assertEqual(Order.objects.count(),1)
        foreign_table = DiningTable.objects.create(branch=self.other_branch,name='غريبة')
        payload['table_id'] = foreign_table.pk
        payload['idempotency_key'] = str(uuid.uuid4())
        self.assertEqual(self.client.post(endpoint,data=json.dumps(payload),content_type='application/json').status_code,404)

    def test_manual_payment_is_separate_and_scoped(self):
        order = create_order(self.table,self.payload())
        foreign_table = DiningTable.objects.create(branch=self.other_branch,name='بعيدة')
        foreign_category = Category.objects.create(branch=self.other_branch,name='منيو')
        foreign_item = MenuItem.objects.create(category=foreign_category,name='صنف',price=10)
        foreign_order = create_order(foreign_table,{'idempotency_key':str(uuid.uuid4()),'lines':[{'item_id':foreign_item.pk,'quantity':1,'options':[]}]})
        self.client.login(username='owner@example.com',password='StrongPass!123')
        endpoint = reverse('order_payment_change',args=[self.restaurant.slug,order.pk])
        self.assertEqual(self.client.post(endpoint).status_code,302)
        order.refresh_from_db()
        self.assertEqual(order.status,'new')
        self.assertEqual(order.payment_status,'paid')
        self.assertIsNotNone(order.paid_at)
        self.assertEqual(self.client.post(reverse('order_payment_change',args=[self.restaurant.slug,foreign_order.pk])).status_code,404)
        report = self.client.get(reverse('reports',args=[self.restaurant.slug]))
        self.assertEqual(report.context['paid_value'],Decimal('200.00'))
        self.assertEqual(report.context['total'],1)

    def test_cancellation_requires_reason_and_preserves_history(self):
        order = create_order(self.table,self.payload())
        self.client.login(username='owner@example.com',password='StrongPass!123')
        endpoint = reverse('order_status_change',args=[self.restaurant.slug,order.pk])
        self.client.post(endpoint,{'status':'cancelled','reason':''})
        order.refresh_from_db()
        self.assertEqual(order.status,'new')
        self.client.post(endpoint,{'status':'cancelled','reason':'طلب مكرر'})
        order.refresh_from_db()
        self.assertEqual(order.status,'cancelled')
        self.assertEqual(order.history.last().reason,'طلب مكرر')

    def test_operational_timing_reports_use_actual_transitions(self):
        self.client.login(username='owner@example.com',password='StrongPass!123')
        endpoint = reverse('reports',args=[self.restaurant.slug])
        empty = self.client.get(endpoint)
        self.assertEqual(empty.context['accepted_time'],'لا توجد بيانات')
        order = create_order(self.table,self.payload())
        for status in ('accepted','preparing','ready'):
            advance_order(order,self.owner,status)
        call = create_service_call(self.table,'waiter')
        advance_call(call,self.owner,'acknowledged')
        report = self.client.get(endpoint)
        self.assertIn('ث',report.context['accepted_time'])
        self.assertIn('ث',report.context['ready_time'])
        self.assertIn('ث',report.context['call_response_time'])

    def test_platform_actions_require_superuser_and_audited_reason(self):
        endpoint = reverse('platform_update_restaurant',args=[self.restaurant.pk])
        self.client.login(username='owner@example.com',password='StrongPass!123')
        self.assertEqual(self.client.get(reverse('platform_dashboard')).status_code,403)
        self.assertEqual(self.client.post(endpoint,{'action':'toggle','reason':'اختبار'}).status_code,403)
        self.client.logout()
        admin = User.objects.create_superuser(username='platform@example.com',email='platform@example.com',password='StrongPass!123')
        self.client.login(username=admin.username,password='StrongPass!123')
        self.assertContains(self.client.get(reverse('platform_dashboard')),'مطعم سفرة')
        self.client.post(endpoint,{'action':'toggle','reason':''})
        self.restaurant.refresh_from_db()
        self.assertTrue(self.restaurant.active)
        self.client.post(endpoint,{'action':'toggle','reason':'صيانة مجدولة'})
        self.restaurant.refresh_from_db()
        self.assertFalse(self.restaurant.active)
        self.assertTrue(AuditLog.objects.filter(restaurant=self.restaurant,action='platform.restaurant.toggle',detail__contains='صيانة مجدولة').exists())
        plan = Plan.objects.create(name='Growth',max_branches=3)
        self.client.post(endpoint,{'action':'subscription','plan_id':plan.pk,'status':'active','ends_at':'2027-01-01','reason':'تجديد يدوي'})
        self.restaurant.subscription.refresh_from_db()
        self.assertEqual(self.restaurant.subscription.plan_id,plan.pk)
        self.assertEqual(self.restaurant.subscription.status,'active')
        self.assertTrue(AuditLog.objects.filter(restaurant=self.restaurant,action='platform.subscription.update').exists())

    def test_suspended_restaurant_is_read_only_for_staff(self):
        self.restaurant.active = False
        self.restaurant.save(update_fields=['active'])
        self.client.login(username='owner@example.com',password='StrongPass!123')
        endpoint = reverse('manage_menu',args=[self.restaurant.slug])
        self.assertEqual(self.client.get(endpoint).status_code,200)
        self.assertEqual(self.client.post(endpoint,{'action':'category','name':'جديد'}).status_code,403)
        self.assertFalse(Category.objects.filter(branch=self.branch,name='جديد').exists())
        self.assertEqual(self.client.get(reverse('reports',args=[self.restaurant.slug])+'?export=csv').status_code,200)

    def test_admin_plan_change_requires_reason_and_operational_models_are_read_only(self):
        admin = User.objects.create_superuser(username='adminplan@example.com',email='adminplan@example.com',password='StrongPass!123')
        self.client.login(username=admin.username,password='StrongPass!123')
        self.assertEqual(self.client.get(f'/django-admin/core/restaurant/{self.restaurant.pk}/change/').status_code,200)
        self.assertEqual(self.client.post(f'/django-admin/core/restaurant/{self.restaurant.pk}/change/',{'name':'تغيير'}).status_code,403)
        plan = self.restaurant.subscription.plan
        endpoint = f'/django-admin/core/plan/{plan.pk}/change/'
        self.assertContains(self.client.get(endpoint),'change_reason')
        data = {'name':plan.name,'price_monthly':'0','max_tables':'20','max_items':'50',
                'max_staff':'5','max_branches':'1','change_reason':''}
        self.assertEqual(self.client.post(endpoint,data).status_code,200)
        plan.refresh_from_db()
        self.assertEqual(plan.max_tables,10)
        data['change_reason'] = 'تعديل تعاقدي'
        self.assertEqual(self.client.post(endpoint,data).status_code,302)
        plan.refresh_from_db()
        self.assertEqual(plan.max_tables,20)
        self.assertTrue(LogEntry.objects.filter(user=admin,change_message__contains='تعديل تعاقدي').exists())

    def test_login_rate_limit_expires_without_exposing_account(self):
        endpoint = reverse('login')
        for _ in range(8):
            response = self.client.post(endpoint,{'email':self.owner.email,'password':'wrong'})
            self.assertEqual(response.status_code,200)
        response = self.client.post(endpoint,{'email':self.owner.email,'password':'StrongPass!123'})
        self.assertEqual(response.status_code,429)
        self.assertNotIn('_auth_user_id',self.client.session)
        LoginAttempt.objects.update(created_at=timezone.now()-timedelta(minutes=16))
        response = self.client.post(endpoint,{'email':self.owner.email,'password':'StrongPass!123'})
        self.assertEqual(response.status_code,302)

    def test_password_reset_rate_limit_keeps_same_response(self):
        endpoint = reverse('password_reset')
        for _ in range(6):
            response = self.client.post(endpoint,{'email':self.owner.email})
            self.assertRedirects(response,reverse('password_reset_done'))
        self.assertEqual(len(mail.outbox),5)

    def test_onboarding_steps_follow_current_branch_data(self):
        self.client.login(username=self.owner.username, password='StrongPass!123')
        endpoint = reverse('dashboard', args=[self.restaurant.slug])
        response = self.client.get(endpoint)
        self.assertEqual(response.context['setup_done'], 2)
        self.assertContains(response, 'جهّز الفرع الرئيسي')
        self.restaurant.description = 'مطبخ مصري'
        self.restaurant.save(update_fields=['description'])
        chef = User.objects.create_user(username='chef@example.com', password='StrongPass!123')
        chef_membership = Membership.objects.create(user=chef, restaurant=self.restaurant, role='kitchen')
        second_branch = Branch.objects.create(restaurant=self.restaurant, name='الثاني')
        chef_membership.branches.add(second_branch)
        self.assertEqual(self.client.get(endpoint).context['setup_done'], 3)
        chef_membership.branches.set([self.branch])
        response = self.client.get(endpoint)
        self.assertEqual(response.context['setup_done'], 4)
        self.assertNotContains(response, 'setup-panel')
        self.item.available = False
        self.item.save(update_fields=['available'])
        self.assertEqual(self.client.get(endpoint).context['setup_done'], 3)

    def test_onboarding_is_hidden_from_non_manager(self):
        waiter = User.objects.create_user(username='waiter@example.com', password='StrongPass!123')
        membership = Membership.objects.create(user=waiter, restaurant=self.restaurant, role='waiter')
        membership.branches.add(self.branch)
        self.client.login(username=waiter.username, password='StrongPass!123')
        response = self.client.get(reverse('dashboard', args=[self.restaurant.slug]))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'setup-panel')

    def test_profile_validation_and_public_contact_opt_in(self):
        self.client.login(username=self.owner.username, password='StrongPass!123')
        endpoint = reverse('restaurant_settings', args=[self.restaurant.slug])
        menu_url = reverse('public_menu', args=[self.table.public_token])
        data = {'name': 'مطعم جديد', 'name_en': 'New Restaurant', 'description': 'وصف جديد',
                'branch_name': 'وسط البلد', 'address': '١ شارع النيل', 'phone': '+20 100 123 4567',
                'opening_hours': 'يوميًا ١٠–٢٢', 'accepting_orders': 'on'}
        response = self.client.post(endpoint, {**data, 'phone': 'javascript:alert(1)'})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'أدخل رقم تواصل صالحًا')
        self.restaurant.refresh_from_db(); self.branch.refresh_from_db()
        self.assertEqual(self.restaurant.name, 'مطعم سفرة')
        self.assertEqual(self.branch.name, 'الفرع الرئيسي')
        self.assertEqual(self.client.post(endpoint, data).status_code, 302)
        self.branch.refresh_from_db(); self.restaurant.refresh_from_db()
        self.assertEqual(self.branch.phone, '+20 100 123 4567')
        self.assertFalse(self.branch.show_contact_on_menu)
        self.assertNotContains(self.client.get(menu_url), '١ شارع النيل')
        self.assertNotContains(self.client.get(menu_url), '+20 100 123 4567')
        self.assertEqual(self.client.post(endpoint, {**data, 'show_contact_on_menu': 'on'}).status_code, 302)
        response = self.client.get(menu_url)
        self.assertContains(response, '١ شارع النيل')
        self.assertContains(response, '+20 100 123 4567')
        self.assertContains(response, 'New Restaurant')
        self.assertTrue(AuditLog.objects.filter(restaurant=self.restaurant, action='restaurant.settings').exists())

    def test_profile_update_is_scoped_to_current_restaurant_and_branch(self):
        self.client.login(username=self.owner.username, password='StrongPass!123')
        second = Branch.objects.create(restaurant=self.restaurant, name='فرع ثان')
        data = {'name': 'سفرة', 'branch_name': 'الفرع الجديد', 'address': 'عنوان محلي',
                'phone': '', 'description': '', 'opening_hours': ''}
        endpoint = reverse('restaurant_settings', args=[self.restaurant.slug])
        self.assertEqual(self.client.post(endpoint, data).status_code, 302)
        second.refresh_from_db(); self.other_branch.refresh_from_db()
        self.assertEqual(second.name, 'فرع ثان')
        self.assertEqual(self.other_branch.address, '')
        self.assertEqual(self.client.post(reverse('restaurant_settings', args=[self.other_restaurant.slug]), data).status_code, 403)
