import json
import re
import uuid
from decimal import Decimal
from datetime import timedelta
from django.contrib.auth.models import User
from django.core import mail
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.test import Client
from django.urls import reverse
from django.utils import timezone
from .models import Branch, Category, DiningTable, Membership, MenuItem, ModifierGroup, ModifierOption, Order, Plan, Restaurant, ServiceCall, Subscription
from .services import advance_order, create_order, create_service_call

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
