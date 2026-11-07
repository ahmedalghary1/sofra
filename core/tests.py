import json
import re
import uuid
from decimal import Decimal
from datetime import timedelta
from django.contrib.auth.models import User
from django.contrib.admin.models import LogEntry
from django.core import mail
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from unittest.mock import patch
from django.test import TestCase
from django.test import Client
from django.urls import reverse
from django.utils import timezone
from .models import AuditLog, Branch, Category, DiningTable, LoginAttempt, Membership, MenuImportBatch, MenuItem, MenuSize, ModifierGroup, ModifierOption, Order, OrderRevision, Plan, Restaurant, ServiceCall, StaffInvitation, Subscription, SubscriptionPayment
from .services import advance_call, advance_order, create_order, create_service_call, edit_order

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

    def test_manager_can_correct_one_step_with_reason_and_audit(self):
        order = create_order(self.table, self.payload())
        advance_order(order, self.owner, 'accepted')
        advance_order(order, self.owner, 'preparing')
        self.client.login(username=self.owner.username, password='StrongPass!123')
        endpoint = reverse('order_status_correct', args=[self.restaurant.slug, order.pk])
        signature = self.client.get(reverse('kitchen_feed', args=[self.restaurant.slug])).json()['signature']
        self.assertContains(self.client.get(reverse('manage_orders', args=[self.restaurant.slug])), 'تصحيح الحالة السابقة')
        self.client.post(endpoint, {'reason': ' ', 'expected_status': 'preparing'})
        order.refresh_from_db()
        self.assertEqual(order.status, 'preparing')
        self.client.post(endpoint, {'reason': 'ضغطت بالخطأ', 'expected_status': 'preparing'})
        order.refresh_from_db()
        self.assertEqual(order.status, 'accepted')
        history = order.history.order_by('-pk').first()
        self.assertEqual((history.old_status, history.new_status, history.actor, history.reason),
                         ('preparing', 'accepted', self.owner, 'ضغطت بالخطأ'))
        self.assertTrue(AuditLog.objects.filter(restaurant=self.restaurant, action='order.status.correct').exists())
        self.assertNotEqual(signature, self.client.get(reverse('kitchen_feed', args=[self.restaurant.slug])).json()['signature'])
        self.client.post(endpoint, {'reason': 'ضغطت بالخطأ', 'expected_status': 'preparing'})
        order.refresh_from_db()
        self.assertEqual(order.status, 'accepted')
        change = {'edit_key': str(uuid.uuid4()), 'expected_version': 0, 'reason': 'تغيير الصنف',
                  'existing': [{'id': order.lines.get().pk, 'quantity': 1}], 'add': []}
        with self.assertRaises(ValidationError):
            edit_order(order, self.owner, change)
        manager = User.objects.create_user(username='correction-manager@example.com', password='StrongPass!123')
        Membership.objects.create(user=manager, restaurant=self.restaurant, role='manager')
        self.client.login(username=manager.username, password='StrongPass!123')
        self.client.post(endpoint, {'reason': 'تصحيح استلام الطلب', 'expected_status': 'accepted'})
        order.refresh_from_db()
        self.assertEqual(order.status, 'new')

    def test_correction_rejects_other_roles_foreign_paid_and_terminal_orders(self):
        order = create_order(self.table, self.payload())
        advance_order(order, self.owner, 'accepted')
        endpoint = reverse('order_status_correct', args=[self.restaurant.slug, order.pk])
        waiter = User.objects.create_user(username='correction-waiter@example.com', password='StrongPass!123')
        member = Membership.objects.create(user=waiter, restaurant=self.restaurant, role='waiter')
        member.branches.add(self.branch)
        self.client.login(username=waiter.username, password='StrongPass!123')
        self.assertEqual(self.client.post(endpoint, {'reason': 'خطأ تشغيلي', 'expected_status': 'accepted'}).status_code, 403)
        self.client.login(username=self.other.username, password='StrongPass!123')
        self.assertEqual(self.client.post(endpoint, {'reason': 'خطأ تشغيلي', 'expected_status': 'accepted'}).status_code, 403)
        self.client.login(username=self.owner.username, password='StrongPass!123')
        order.payment_status = 'paid'; order.save(update_fields=['payment_status'])
        self.client.post(endpoint, {'reason': 'خطأ تشغيلي', 'expected_status': 'accepted'})
        order.refresh_from_db()
        self.assertEqual(order.status, 'accepted')
        order.payment_status = 'unpaid'; order.save(update_fields=['payment_status'])
        for status in ('preparing', 'ready', 'served'):
            advance_order(order, self.owner, status)
        self.client.post(endpoint, {'reason': 'خطأ تشغيلي', 'expected_status': 'served'})
        order.refresh_from_db()
        self.assertEqual(order.status, 'served')

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

    def test_service_call_response_thresholds_use_server_timestamps(self):
        self.branch.waiter_response_minutes = 3
        self.branch.bill_response_minutes = 10
        self.branch.save(update_fields=['waiter_response_minutes', 'bill_response_minutes'])
        waiter_call = create_service_call(self.table, 'waiter')
        bill_call = create_service_call(self.table, 'bill')
        past = timezone.now() - timedelta(minutes=5)
        ServiceCall.objects.filter(pk__in=[waiter_call.pk, bill_call.pk]).update(created_at=past)
        self.client.login(username=self.owner.username, password='StrongPass!123')
        response = self.client.get(reverse('manage_calls', args=[self.restaurant.slug]))
        self.assertEqual(response.context['overdue_count'], 1)
        self.assertContains(response, 'تجاوز حد الاستجابة وينتظر الاستلام', count=1)
        advance_call(waiter_call, self.owner, 'acknowledged')
        response = self.client.get(reverse('manage_calls', args=[self.restaurant.slug]))
        self.assertEqual(response.context['overdue_count'], 0)
        self.assertContains(response, 'استجابة بعد الحد المحدد')

    def test_response_threshold_settings_are_scoped_and_validated(self):
        self.client.login(username=self.owner.username, password='StrongPass!123')
        endpoint = reverse('restaurant_settings', args=[self.restaurant.slug])
        data = {'name': self.restaurant.name, 'branch_name': self.branch.name,
                'waiter_response_minutes': '7', 'bill_response_minutes': '12'}
        self.assertEqual(self.client.post(endpoint, data).status_code, 302)
        self.branch.refresh_from_db()
        self.assertEqual((self.branch.waiter_response_minutes, self.branch.bill_response_minutes), (7, 12))
        response = self.client.post(endpoint, {**data, 'waiter_response_minutes': '0'})
        self.assertEqual(response.status_code, 200)
        self.branch.refresh_from_db()
        self.assertEqual(self.branch.waiter_response_minutes, 7)
        self.client.login(username=self.other.username, password='StrongPass!123')
        self.assertEqual(self.client.post(endpoint, data).status_code, 403)

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

    def test_subscription_payment_is_recorded_once_and_visible_only_to_own_owner(self):
        endpoint = reverse('platform_record_payment', args=[self.restaurant.pk])
        data = {'amount': '799.50', 'period_start': '2026-11-01', 'period_end': '2026-11-30',
                'method': 'bank', 'reference': ' transfer-123 '}
        self.client.login(username=self.owner.username, password='StrongPass!123')
        self.assertEqual(self.client.post(endpoint, data).status_code, 403)
        operator = User.objects.create_superuser(username='billing@example.com', email='billing@example.com',
                                                 password='StrongPass!123')
        self.client.login(username=operator.username, password='StrongPass!123')
        original_end = self.restaurant.subscription.ends_at
        self.assertContains(self.client.get(reverse('platform_dashboard')), 'تسجيل تحصيل اشتراك')
        self.assertEqual(self.client.post(endpoint, data).status_code, 302)
        payment = SubscriptionPayment.objects.get(restaurant=self.restaurant)
        self.assertEqual((payment.amount, payment.currency, payment.reference, payment.recorded_by),
                         (Decimal('799.50'), 'EGP', 'TRANSFER-123', operator))
        self.assertEqual(payment.period_start.isoformat(), '2026-11-01')
        self.restaurant.subscription.refresh_from_db()
        self.assertEqual(self.restaurant.subscription.ends_at, original_end)
        self.assertTrue(AuditLog.objects.filter(restaurant=self.restaurant,
                        action='platform.subscription.payment', actor=operator).exists())
        self.client.post(endpoint, {**data, 'reference': 'TRANSFER-123'})
        self.assertEqual(SubscriptionPayment.objects.count(), 1)
        self.client.login(username=self.owner.username, password='StrongPass!123')
        self.assertContains(self.client.get(reverse('restaurant_settings', args=[self.restaurant.slug])), 'TRANSFER-123')
        Subscription.objects.create(restaurant=self.other_restaurant, plan=self.restaurant.subscription.plan,
                                    ends_at=timezone.now()+timedelta(days=14))
        self.client.login(username=self.other.username, password='StrongPass!123')
        self.assertNotContains(self.client.get(reverse('restaurant_settings', args=[self.other_restaurant.slug])),
                               'TRANSFER-123')

    def test_subscription_payment_rejects_invalid_amount_period_and_reference(self):
        operator = User.objects.create_superuser(username='billing-check@example.com',
            email='billing-check@example.com', password='StrongPass!123')
        self.client.login(username=operator.username, password='StrongPass!123')
        endpoint = reverse('platform_record_payment', args=[self.restaurant.pk])
        valid = {'amount': '10.00', 'period_start': '2026-11-01', 'period_end': '2026-11-30',
                 'method': 'cash', 'reference': 'CASH-1'}
        for changes in ({'amount': '-10'}, {'amount': 'NaN'}, {'amount': '1.001'},
                        {'period_end': '2026-10-31'}, {'method': 'card'}, {'reference': '  '}):
            with self.subTest(changes=changes):
                self.client.post(endpoint, {**valid, **changes})
                self.assertFalse(SubscriptionPayment.objects.exists())

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
        self.assertContains(response, 'class="setup-count">2 من 4')
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

    def test_size_price_and_name_are_snapshotted_with_modifiers(self):
        size = MenuSize.objects.create(item=self.item, name='كبير', price=Decimal('150.00'))
        group = ModifierGroup.objects.create(item=self.item, name='إضافات', min_select=0, max_select=1)
        option = ModifierOption.objects.create(group=group, name='جبن', price=Decimal('20.00'))
        payload = self.payload([option.pk])
        payload['lines'][0]['size_id'] = size.pk
        order = create_order(self.table, payload)
        line = order.lines.get()
        self.assertEqual(order.total, Decimal('340.00'))
        self.assertEqual(line.base_price, Decimal('150.00'))
        self.assertEqual(line.size_name, 'كبير')
        self.assertEqual(line.unit_price, Decimal('170.00'))
        size.name, size.price = 'أكبر', Decimal('200.00')
        size.save(update_fields=['name', 'price'])
        line.refresh_from_db()
        self.assertEqual((line.size_name, line.base_price), ('كبير', Decimal('150.00')))
        self.assertEqual(create_order(self.table, payload).pk, order.pk)

    def test_size_must_belong_to_item_and_remain_available(self):
        size = MenuSize.objects.create(item=self.item, name='صغير', price=Decimal('80.00'))
        foreign_category = Category.objects.create(branch=self.other_branch, name='أطباق أخرى')
        foreign_item = MenuItem.objects.create(category=foreign_category, name='صنف آخر', price=Decimal('25.00'))
        foreign_size = MenuSize.objects.create(item=foreign_item, name='كبير', price=Decimal('40.00'))
        payload = self.payload(); payload['lines'][0]['size_id'] = foreign_size.pk
        with self.assertRaises(ValidationError): create_order(self.table, payload)
        size.available = False; size.save(update_fields=['available'])
        payload['lines'][0]['size_id'] = size.pk
        with self.assertRaises(ValidationError): create_order(self.table, payload)
        payload['lines'][0]['size_id'] = float(size.pk)
        with self.assertRaises(ValidationError): create_order(self.table, payload)
        self.assertEqual(Order.objects.count(), 0)

    def test_size_management_is_tenant_scoped(self):
        self.client.login(username=self.owner.username, password='StrongPass!123')
        endpoint = reverse('manage_menu', args=[self.restaurant.slug])
        self.assertEqual(self.client.post(endpoint, {'action': 'size', 'item_id': self.item.pk,
            'name': 'كبير', 'price': '155.00'}).status_code, 302)
        size = MenuSize.objects.get(item=self.item)
        menu = self.client.get(reverse('public_menu', args=[self.table.public_token]))
        self.assertEqual(menu.context['items_json'][0]['sizes'][0]['name'], 'كبير')
        self.client.post(endpoint, {'action': 'size_edit', 'size_id': size.pk, 'name': 'وسط', 'price': '125.00'})
        size.refresh_from_db()
        self.assertEqual(size.price, Decimal('125.00'))
        self.client.post(endpoint, {'action': 'size_toggle', 'size_id': size.pk})
        size.refresh_from_db()
        self.assertFalse(size.available)
        self.client.login(username=self.other.username, password='StrongPass!123')
        Subscription.objects.create(restaurant=self.other_restaurant,
            plan=self.restaurant.subscription.plan, ends_at=timezone.now()+timedelta(days=14))
        other_endpoint = reverse('manage_menu', args=[self.other_restaurant.slug])
        self.assertEqual(self.client.post(other_endpoint, {'action': 'size_edit', 'size_id': size.pk,
            'name': 'مخترق', 'price': '1'}).status_code, 404)
        size.refresh_from_db()
        self.assertEqual(size.name, 'وسط')

    def test_order_edit_preserves_existing_prices_and_records_revision(self):
        order = create_order(self.table, self.payload())
        old_line = order.lines.get()
        self.item.price = Decimal('150.00'); self.item.save(update_fields=['price'])
        second = MenuItem.objects.create(category=self.category, name='مشروب', price=Decimal('50.00'))
        key = str(uuid.uuid4())
        change = {'edit_key': key, 'expected_version': 0, 'reason': 'تغيير طلب العميل',
                  'existing': [{'id': old_line.pk, 'quantity': 1}],
                  'add': [{'item_id': second.pk, 'quantity': 1, 'options': []}]}
        edited = edit_order(order, self.owner, change)
        self.assertEqual((edited.total, edited.version), (Decimal('150.00'), 1))
        old_line.refresh_from_db()
        self.assertEqual((old_line.unit_price, old_line.line_total), (Decimal('100.00'), Decimal('100.00')))
        revision = OrderRevision.objects.get(order=order)
        self.assertEqual((revision.old_total, revision.new_total), (Decimal('200.00'), Decimal('150.00')))
        self.assertEqual(revision.before[0]['quantity'], 2)
        self.assertEqual(len(revision.after), 2)
        self.assertEqual(edit_order(order, self.owner, change).pk, order.pk)
        self.assertEqual(OrderRevision.objects.filter(order=order).count(), 1)
        with self.assertRaises(ValidationError): edit_order(order, self.owner, {**change, 'reason': 'تغيير آخر'})
        with self.assertRaises(ValidationError): edit_order(order, self.owner, {**change, 'edit_key': str(uuid.uuid4())})

    def test_order_edit_rejects_paid_preparing_and_foreign_lines(self):
        order = create_order(self.table, self.payload())
        foreign_table = DiningTable.objects.create(branch=self.other_branch, name='بعيدة')
        foreign_category = Category.objects.create(branch=self.other_branch, name='منيو')
        foreign_item = MenuItem.objects.create(category=foreign_category, name='غريب', price=10)
        foreign_order = create_order(foreign_table, {'idempotency_key': str(uuid.uuid4()),
            'lines': [{'item_id': foreign_item.pk, 'quantity': 1, 'options': []}]})
        change = {'edit_key': str(uuid.uuid4()), 'expected_version': 0, 'reason': 'تعديل تجريبي',
                  'existing': [{'id': foreign_order.lines.get().pk, 'quantity': 1}], 'add': []}
        with self.assertRaises(ValidationError): edit_order(order, self.owner, change)
        self.assertEqual(order.total, Decimal('200.00'))
        change['existing'] = [{'id': order.lines.get().pk, 'quantity': 1}]
        order.payment_status = 'paid'; order.save(update_fields=['payment_status'])
        with self.assertRaises(ValidationError): edit_order(order, self.owner, change)
        order.payment_status = 'unpaid'; order.status = 'preparing'
        order.save(update_fields=['payment_status', 'status'])
        with self.assertRaises(ValidationError): edit_order(order, self.owner, change)
        self.assertEqual(OrderRevision.objects.count(), 0)

    def test_order_edit_endpoint_scopes_branch_and_refreshes_kitchen(self):
        order = create_order(self.table, self.payload())
        self.client.login(username=self.owner.username, password='StrongPass!123')
        endpoint = reverse('order_edit', args=[self.restaurant.slug, order.pk])
        self.assertContains(self.client.get(endpoint), 'سبب التعديل')
        before = self.client.get(reverse('kitchen_feed', args=[self.restaurant.slug])).json()['signature']
        change = {'edit_key': str(uuid.uuid4()), 'expected_version': 0, 'reason': 'تعديل الكمية',
                  'existing': [{'id': order.lines.get().pk, 'quantity': 1}], 'add': []}
        response = self.client.post(endpoint, data=json.dumps(change), content_type='application/json')
        self.assertEqual(response.status_code, 200)
        order.refresh_from_db()
        self.assertEqual(order.total, Decimal('100.00'))
        self.assertNotEqual(before, self.client.get(reverse('kitchen_feed', args=[self.restaurant.slug])).json()['signature'])
        self.assertEqual(self.client.get(reverse('order_edit', args=[self.other_restaurant.slug, order.pk])).status_code, 403)
        self.client.login(username=self.other.username, password='StrongPass!123')
        self.assertEqual(self.client.get(reverse('order_edit', args=[self.other_restaurant.slug, order.pk])).status_code, 404)

    def test_menu_import_preview_commit_and_replay(self):
        self.client.login(username=self.owner.username, password='StrongPass!123')
        endpoint = reverse('menu_import', args=[self.restaurant.slug])
        template = self.client.get(endpoint + '?template=1')
        self.assertEqual(template.status_code, 200)
        self.assertIn(b'category,name,price,description', template.content)
        content = 'category,name,price,description\nمشروبات,عصير,25.50,طازج\nأطباق,أرز,40.00,\n'
        upload = SimpleUploadedFile('menu.csv', content.encode('utf-8'), content_type='text/csv')
        preview = self.client.post(endpoint, {'action': 'preview', 'file': upload})
        self.assertEqual(preview.status_code, 200)
        batch = preview.context['batch']
        self.assertIsNotNone(batch)
        self.assertEqual(MenuItem.objects.count(), 1)
        commit = self.client.post(endpoint, {'action': 'commit', 'batch': str(batch.token)})
        self.assertEqual(commit.status_code, 302)
        self.assertEqual(MenuItem.objects.filter(category__branch=self.branch).count(), 3)
        self.assertTrue(Category.objects.filter(branch=self.branch, name='مشروبات').exists())
        self.assertEqual(self.client.post(endpoint, {'action': 'commit', 'batch': str(batch.token)}).status_code, 302)
        self.assertEqual(MenuItem.objects.count(), 3)
        self.assertEqual(AuditLog.objects.filter(action='menu.import', restaurant=self.restaurant).count(), 1)

    def test_menu_import_rejects_bad_rows_and_plan_change_atomically(self):
        self.client.login(username=self.owner.username, password='StrongPass!123')
        endpoint = reverse('menu_import', args=[self.restaurant.slug])
        bad = SimpleUploadedFile('bad.csv', b'category,name,price,description\nDrinks,Tea,NaN,\n')
        response = self.client.post(endpoint, {'action': 'preview', 'file': bad})
        self.assertContains(response, 'السعر يجب أن يكون')
        self.assertIsNone(response.context['batch'])
        self.assertEqual(MenuImportBatch.objects.count(), 0)
        content = b'category,name,price,description\nDrinks,Tea,20.00,\nDrinks,Coffee,30.00,\n'
        preview = self.client.post(endpoint, {'action': 'preview', 'file': SimpleUploadedFile('ok.csv', content)})
        batch = preview.context['batch']
        plan = self.restaurant.subscription.plan
        plan.max_items = 2; plan.save(update_fields=['max_items'])
        response = self.client.post(endpoint, {'action': 'commit', 'batch': str(batch.token)})
        self.assertContains(response, 'يتجاوز حد الأصناف')
        self.assertEqual(MenuItem.objects.count(), 1)
        self.assertFalse(Category.objects.filter(branch=self.branch, name='Drinks').exists())
        batch.refresh_from_db()
        self.assertIsNone(batch.committed_at)

    def test_menu_import_batch_cannot_cross_restaurants(self):
        self.client.login(username=self.owner.username, password='StrongPass!123')
        endpoint = reverse('menu_import', args=[self.restaurant.slug])
        upload = SimpleUploadedFile('menu.csv', b'category,name,price,description\nDrinks,Tea,20.00,\n')
        batch = self.client.post(endpoint, {'action': 'preview', 'file': upload}).context['batch']
        Subscription.objects.create(restaurant=self.other_restaurant,
            plan=self.restaurant.subscription.plan, ends_at=timezone.now()+timedelta(days=14))
        self.client.login(username=self.other.username, password='StrongPass!123')
        other_endpoint = reverse('menu_import', args=[self.other_restaurant.slug])
        self.assertEqual(self.client.post(other_endpoint, {'action': 'commit', 'batch': str(batch.token)}).status_code, 404)
        self.assertEqual(MenuItem.objects.count(), 1)

    def test_reports_filter_period_applies_to_metrics_and_csv(self):
        old = create_order(self.table, self.payload())
        Order.objects.filter(pk=old.pk).update(created_at=timezone.now()-timedelta(days=10))
        recent = create_order(self.table, self.payload())
        advance_order(recent, self.owner, 'accepted')
        self.client.login(username=self.owner.username, password='StrongPass!123')
        day = timezone.localdate().isoformat()
        endpoint = reverse('reports', args=[self.restaurant.slug])
        response = self.client.get(endpoint, {'from': day, 'to': day})
        self.assertEqual(response.context['total'], 1)
        self.assertEqual(response.context['accepted_samples'], 1)
        self.assertEqual(response.context['ready_samples'], 0)
        self.assertEqual(response.context['top_items'][0]['quantity'], 2)
        export = self.client.get(endpoint, {'from': day, 'to': day, 'export': 'csv'})
        self.assertEqual(export.status_code, 200)
        self.assertIn(str(recent.pk).encode(), export.content)
        self.assertFalse(any(row.startswith(f'{old.pk},'.encode()) for row in export.content.splitlines()[1:]))

    def test_reports_reject_invalid_period_without_silent_export(self):
        self.client.login(username=self.owner.username, password='StrongPass!123')
        endpoint = reverse('reports', args=[self.restaurant.slug])
        response = self.client.get(endpoint, {'from': '2026-02-30', 'to': '2026-03-01'})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'تعرض الأرقام أدناه جميع الفترات')
        self.assertEqual(self.client.get(endpoint, {'from': '2026-02-30', 'to': '2026-03-01',
            'export': 'csv'}).status_code, 400)

    def test_kitchen_filters_render_only_current_branch_options(self):
        create_order(self.table, self.payload())
        foreign_table = DiningTable.objects.create(branch=self.other_branch, name='طاولة أخرى')
        self.client.login(username=self.owner.username, password='StrongPass!123')
        response = self.client.get(reverse('kitchen', args=[self.restaurant.slug]))
        self.assertContains(response, 'id="kitchenStatus"')
        self.assertContains(response, f'data-table="{self.table.pk}"')
        self.assertNotContains(response, f'<option value="{foreign_table.pk}">{foreign_table.name}</option>')

    @override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
    def test_staff_invitation_new_user_accepts_once_in_own_branch(self):
        self.client.login(username=self.owner.username, password='StrongPass!123')
        endpoint = reverse('staff', args=[self.restaurant.slug])
        response = self.client.post(endpoint, {'action': 'invite', 'email': 'newstaff@example.com',
            'role': 'waiter', 'branch_id': self.branch.pk})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(len(mail.outbox), 1)
        invitation = StaffInvitation.objects.get(email='newstaff@example.com')
        token = re.search(r'/invitation/([^/]+)/', mail.outbox[0].body).group(1)
        self.assertNotEqual(token, invitation.token_hash)
        self.client.logout()
        accept = reverse('accept_staff_invitation', args=[token])
        self.assertContains(self.client.get(accept), 'newstaff@example.com')
        response = self.client.post(accept, {'name': 'ليلى', 'password': 'StrongPass!123'})
        self.assertRedirects(response, reverse('dashboard', args=[self.restaurant.slug]))
        member = Membership.objects.get(user__username='newstaff@example.com', restaurant=self.restaurant)
        self.assertEqual(member.role, 'waiter')
        self.assertEqual(list(member.branches.all()), [self.branch])
        invitation.refresh_from_db()
        self.assertIsNotNone(invitation.accepted_at)
        self.assertEqual(self.client.get(accept).status_code, 410)
        self.assertTrue(AuditLog.objects.filter(restaurant=self.restaurant, action='staff.invite.accept').exists())

    @override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
    def test_existing_user_invitation_preserves_other_restaurant_role(self):
        self.other.email = self.other.username
        self.other.save(update_fields=['email'])
        self.client.login(username=self.owner.username, password='StrongPass!123')
        response = self.client.post(reverse('staff', args=[self.restaurant.slug]), {'action': 'invite',
            'email': self.other.email, 'role': 'cashier', 'branch_id': self.branch.pk})
        self.assertEqual(len(mail.outbox), 1, [str(message) for message in response.wsgi_request._messages])
        token = re.search(r'/invitation/([^/]+)/', mail.outbox[-1].body).group(1)
        accept = reverse('accept_staff_invitation', args=[token])
        self.client.logout()
        response = self.client.get(accept)
        self.assertEqual(response.status_code, 302)
        self.assertIn('/login/?next=', response.url)
        login_response = self.client.post(reverse('login'), {'email': self.other.email,
            'password': 'StrongPass!123', 'next': accept})
        self.assertRedirects(login_response, accept)
        self.assertEqual(self.client.post(accept).status_code, 302)
        self.assertEqual(Membership.objects.get(user=self.other, restaurant=self.restaurant).role, 'cashier')
        self.assertEqual(Membership.objects.get(user=self.other, restaurant=self.other_restaurant).role, 'owner')

    @override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
    def test_existing_user_without_membership_can_log_in_for_valid_invitation_only(self):
        unattached = User.objects.create_user(username='unattached@example.com',
            email='unattached@example.com', password='StrongPass!123')
        self.client.login(username=self.owner.username, password='StrongPass!123')
        self.client.post(reverse('staff', args=[self.restaurant.slug]), {'action': 'invite',
            'email': unattached.email, 'role': 'waiter', 'branch_id': self.branch.pk})
        token = re.search(r'/invitation/([^/]+)/', mail.outbox[-1].body).group(1)
        accept = reverse('accept_staff_invitation', args=[token])
        self.client.logout()
        login_url = reverse('login')
        self.assertEqual(self.client.post(login_url, {'email': unattached.email,
            'password': 'StrongPass!123', 'next': '/'}).status_code, 200)
        self.assertRedirects(self.client.post(login_url, {'email': unattached.email,
            'password': 'StrongPass!123', 'next': accept}), accept)
        self.assertRedirects(self.client.post(accept), reverse('dashboard', args=[self.restaurant.slug]))
        self.assertTrue(Membership.objects.filter(user=unattached, restaurant=self.restaurant).exists())

    @override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
    def test_invitation_permissions_expiry_limit_and_delivery_failure(self):
        manager = User.objects.create_user(username='manager@example.com', password='StrongPass!123')
        Membership.objects.create(user=manager, restaurant=self.restaurant, role='manager')
        endpoint = reverse('staff', args=[self.restaurant.slug])
        self.client.login(username=manager.username, password='StrongPass!123')
        self.client.post(endpoint, {'action': 'invite', 'email': 'bad@example.com',
            'role': 'manager', 'branch_id': self.branch.pk})
        self.assertFalse(StaffInvitation.objects.filter(email='bad@example.com').exists())
        self.client.post(endpoint, {'action': 'create', 'email': 'direct@example.com',
            'role': 'manager', 'password': 'StrongPass!123'})
        self.assertFalse(Membership.objects.filter(user__username='direct@example.com').exists())
        self.client.post(endpoint, {'action': 'invite', 'email': 'bad@example.com',
            'role': 'waiter', 'branch_id': self.other_branch.pk})
        self.assertFalse(StaffInvitation.objects.filter(email='bad@example.com').exists())
        with patch('core.views.send_mail', side_effect=OSError('mail unavailable')):
            self.client.post(endpoint, {'action': 'invite', 'email': 'bad@example.com',
                'role': 'waiter', 'branch_id': self.branch.pk})
        self.assertFalse(StaffInvitation.objects.filter(email='bad@example.com').exists())
        plan = self.restaurant.subscription.plan
        plan.max_staff = 2; plan.save(update_fields=['max_staff'])
        self.client.post(endpoint, {'action': 'invite', 'email': 'full@example.com',
            'role': 'waiter', 'branch_id': self.branch.pk})
        self.assertFalse(StaffInvitation.objects.filter(email='full@example.com').exists())
        plan.max_staff = 5; plan.save(update_fields=['max_staff'])
        self.client.post(endpoint, {'action': 'invite', 'email': 'pending@example.com',
            'role': 'waiter', 'branch_id': self.branch.pk})
        invitation = StaffInvitation.objects.get(email='pending@example.com')
        token = re.search(r'/invitation/([^/]+)/', mail.outbox[-1].body).group(1)
        invitation.expires_at = timezone.now()-timedelta(seconds=1)
        invitation.save(update_fields=['expires_at'])
        self.client.logout()
        self.assertEqual(self.client.get(reverse('accept_staff_invitation', args=[token])).status_code, 410)

    @override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
    def test_revoked_invitation_cannot_be_accepted(self):
        self.client.login(username=self.owner.username, password='StrongPass!123')
        endpoint = reverse('staff', args=[self.restaurant.slug])
        self.client.post(endpoint, {'action': 'invite', 'email': 'revoked@example.com',
            'role': 'kitchen', 'branch_id': self.branch.pk})
        invitation = StaffInvitation.objects.get(email='revoked@example.com')
        token = re.search(r'/invitation/([^/]+)/', mail.outbox[-1].body).group(1)
        self.assertEqual(self.client.post(endpoint, {'action': 'invite_revoke',
            'invitation_id': invitation.pk}).status_code, 302)
        self.client.logout()
        self.assertEqual(self.client.get(reverse('accept_staff_invitation', args=[token])).status_code, 410)
        self.assertFalse(Membership.objects.filter(user__username='revoked@example.com').exists())
