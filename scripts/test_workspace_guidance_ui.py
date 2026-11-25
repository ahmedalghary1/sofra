"""Verify role guidance leads to working tasks in both languages and directions."""
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

from django.conf import settings
from django.contrib.auth.models import User
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.test import Client, override_settings
from django.urls import reverse
from django.utils import timezone
from playwright.sync_api import expect, sync_playwright

from core.models import (
    Branch, Category, DiningTable, Membership, MenuItem, Order, Plan,
    Restaurant, StaffInvitation, Subscription,
)


@override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
class WorkspaceGuidanceBrowserTests(StaticLiveServerTestCase):
    def setUp(self):
        self.venue = Restaurant.objects.create(name='مطعم الحي · Local Kitchen', slug='guidance-ui')
        plan = Plan.objects.create(name='Guidance QA', max_staff=20, max_branches=3)
        Subscription.objects.create(restaurant=self.venue, plan=plan, status='active',
                                    ends_at=timezone.now() + timedelta(days=30))
        self.branch = Branch.objects.create(restaurant=self.venue, name='فرع النيل')
        self.category = Category.objects.create(branch=self.branch, name='أطباق اليوم')
        self.item = MenuItem.objects.create(category=self.category, name='طبق البيت', price='42.75')
        self.table = DiningTable.objects.create(branch=self.branch, name='T1')
        self.sessions = {}
        for role in ('owner', 'manager', 'cashier', 'kitchen', 'waiter'):
            user = User.objects.create_user('guidance-' + role)
            member = Membership.objects.create(user=user, restaurant=self.venue, role=role)
            member.branches.add(self.branch)
            client = Client()
            client.force_login(user)
            self.sessions[role] = client.cookies[settings.SESSION_COOKIE_NAME].value
        self.artifacts = Path('tmp/guidance-ui')
        self.artifacts.mkdir(parents=True, exist_ok=True)

    def context(self, browser, width, role=None):
        context = browser.new_context(viewport={'width': width, 'height': 800}, reduced_motion='reduce')
        if role:
            context.add_cookies([{'name': settings.SESSION_COOKIE_NAME,
                                 'value': self.sessions[role], 'url': self.live_server_url}])
        return context

    def visit(self, page, path, lang, width):
        separator = '&' if '?' in path else '?'
        response = page.goto(self.live_server_url + path + separator + 'lang=' + lang)
        self.assertEqual(response.status, 200)
        expect(page.locator('html')).to_have_attribute('dir', 'rtl' if lang == 'ar' else 'ltr')
        page.evaluate('document.fonts.ready')
        self.assertLessEqual(page.evaluate('document.documentElement.scrollWidth'), width + 1)

    def test_guest_can_discover_and_complete_ordering_and_sees_paused_or_empty_states(self):
        paused = Branch.objects.create(restaurant=self.venue, name='Paused', accepting_orders=False)
        paused_item = MenuItem.objects.create(category=Category.objects.create(branch=paused, name='Drinks'),
                                             name='Tea', price='10.00')
        paused_table = DiningTable.objects.create(branch=paused, name='P1')
        empty = Branch.objects.create(restaurant=self.venue, name='Empty')
        empty_table = DiningTable.objects.create(branch=empty, name='E1')
        with sync_playwright() as p:
            browser = p.chromium.launch(channel='chrome', headless=True)
            for width in (320, 1366):
                for lang in ('ar', 'en'):
                    with self.subTest(width=width, lang=lang):
                        context = self.context(browser, width)
                        page = context.new_page()
                        self.visit(page, reverse('public_menu', args=[self.table.public_token]), lang, width)
                        expect(page.get_by_role('heading', name='اطلب من هنا لطاولتك' if lang == 'ar'
                                                else 'Order here for your table')).to_be_visible()
                        add = page.locator('.menu-add').first
                        expect(add).to_have_text('أضف' if lang == 'ar' else 'Add')
                        self.assertLess(add.bounding_box()['y'] + add.bounding_box()['height'], 800)
                        page.screenshot(path=str(self.artifacts / f'guest-{width}-{lang}.png'))
                        page.get_by_role('link', name='ابدأ الطلب' if lang == 'ar' else 'Start ordering').click()
                        self.assertGreaterEqual(page.locator('.menu-product').first.bounding_box()['y'], 0)
                        add.click()
                        page.locator('#itemOptions button[type=submit]').click()
                        page.locator('#openCart').click()
                        expect(page.locator('.cart-list')).to_contain_text(self.item.name)
                        expect(page.locator('.cart-total')).to_contain_text('42.75')
                        page.get_by_role('button', name='تأكيد وإرسال الطلب' if lang == 'ar'
                                         else 'Confirm and place order', exact=False).click()
                        page.wait_for_url('**/track/**')
                        expect(page.locator('.track-card')).to_contain_text(self.item.name)
                        self.visit(page, reverse('public_menu', args=[paused_table.public_token]), lang, width)
                        expect(page.locator('.guest-order-paused')).to_be_visible()
                        expect(page.locator('.guest-order-start')).to_have_count(0)
                        expect(page.locator(f'.menu-add[data-id="{paused_item.pk}"]')).to_be_disabled()
                        page.screenshot(path=str(self.artifacts / f'paused-{width}-{lang}.png'))
                        self.visit(page, reverse('public_menu', args=[empty_table.public_token]), lang, width)
                        expect(page.locator('.guest-order-start')).to_have_count(0)
                        expect(page.locator('.empty-state')).to_be_visible()
                        self.visit(page, reverse('demo_menu'), lang, width)
                        expect(page.locator('.sample-cover')).to_contain_text('منيو تجريبية' if lang == 'ar' else 'Sample menu')
                        page.screenshot(path=str(self.artifacts / f'demo-{width}-{lang}.png'))
                        page.locator('.demo-add-item').first.click()
                        page.locator('#openCart').click()
                        page.get_by_role('button', name='أرسل الطلب التجريبي' if lang == 'ar'
                                         else 'Submit demo order', exact=False).click()
                        expect(page.get_by_role('dialog').get_by_role('heading')).to_have_text(
                            'تم تأكيد طلبك التجريبي' if lang == 'ar' else 'Demo order confirmed')
                        context.close()
            browser.close()
        self.assertEqual(Order.objects.filter(branch=self.branch).count(), 4)
        for order in Order.objects.filter(branch=self.branch):
            self.assertEqual(order.total, Decimal('42.75'))
            self.assertEqual(order.lines.get().item_name, self.item.name)

    def test_manager_shortcuts_open_real_product_branding_and_team_tasks(self):
        path = reverse('dashboard', args=[self.venue.slug])
        with sync_playwright() as p:
            browser = p.chromium.launch(channel='chrome', headless=True)
            for width in (360, 1366):
                for lang in ('ar', 'en'):
                    context = self.context(browser, width, 'owner' if width == 360 else 'manager')
                    page = context.new_page()
                    self.visit(page, path, lang, width)
                    tasks = page.locator('.workspace-tasks a')
                    expect(tasks).to_have_count(4)
                    page.screenshot(path=str(self.artifacts / f'manager-{width}-{lang}.png'))
                    tasks.nth(0).click()
                    expect(page.locator('#menuTools>details').first).to_have_attribute('open', '')
                    expect(page.locator('#menuTools input[name=name]').first).to_be_visible()
                    page.screenshot(path=str(self.artifacts / f'products-{width}-{lang}.png'))
                    self.assertLessEqual(page.evaluate('document.documentElement.scrollWidth'), width + 1)
                    self.visit(page, path, lang, width)
                    page.locator('.workspace-tasks a').nth(1).click()
                    expect(page.locator('#menuBrandingForm')).to_be_visible()
                    page.wait_for_function('document.getElementById("menuBranding").getBoundingClientRect().top < 180')
                    self.assertLess(page.locator('#menuBranding').bounding_box()['y'], 180)
                    page.screenshot(path=str(self.artifacts / f'branding-link-{width}-{lang}.png'))
                    self.visit(page, path, lang, width)
                    page.locator('.workspace-tasks a').nth(2).click()
                    expect(page.locator('#inviteStaff')).to_be_visible()
                    page.wait_for_function('document.getElementById("inviteStaff").getBoundingClientRect().top < 180')
                    form = page.locator('.staff-invite-form')
                    form.locator('input[name=email]').fill(f'new-{width}-{lang}@example.org')
                    form.locator('select[name=role]').select_option('cashier')
                    form.locator('select[name=branch_id]').select_option(str(self.branch.pk))
                    page.screenshot(path=str(self.artifacts / f'invitation-{width}-{lang}.png'))
                    self.assertLessEqual(page.evaluate('document.documentElement.scrollWidth'), width + 1)
                    form.get_by_role('button', name='إرسال الدعوة' if lang == 'ar' else 'Send invitation').click()
                    expect(page.locator('.invitation-row').filter(has_text=f'new-{width}-{lang}@example.org')).to_be_visible()
                    self.visit(page, path, lang, width)
                    page.locator('.workspace-tasks a').nth(3).click()
                    self.assertEqual(page.url.split('?')[0], self.live_server_url + reverse('manage_tables', args=[self.venue.slug]))
                    self.assertLessEqual(page.evaluate('document.documentElement.scrollWidth'), width + 1)
                    context.close()
            browser.close()
        self.assertEqual(StaffInvitation.objects.filter(restaurant=self.venue).count(), 4)

    def test_staff_start_tasks_match_their_role_and_remain_within_their_restaurant(self):
        other = Restaurant.objects.create(name='Other venue', slug='other-guidance')
        with sync_playwright() as p:
            browser = p.chromium.launch(channel='chrome', headless=True)
            for role, destinations in (
                ('kitchen', ['kitchen']),
                ('cashier', ['staff_order', 'manage_orders']),
                ('waiter', ['manage_calls', 'staff_order', 'manage_orders']),
            ):
                for width in (360, 1366):
                    for lang in ('ar', 'en'):
                        context = self.context(browser, width, role)
                        page = context.new_page()
                        self.visit(page, reverse('home'), lang, width)
                        self.assertIn(reverse('dashboard', args=[self.venue.slug]), page.url)
                        tasks = page.locator('.workspace-tasks a')
                        expect(tasks).to_have_count(len(destinations))
                        links = tasks.evaluate_all('(nodes) => nodes.map(node => node.getAttribute("href"))')
                        self.assertEqual(links, [reverse(name, args=[self.venue.slug]) for name in destinations])
                        page.screenshot(path=str(self.artifacts / f'{role}-{width}-{lang}.png'))
                        for target in links:
                            self.visit(page, target, lang, width)
                        for name in ('manage_menu', 'staff', 'restaurant_settings'):
                            response = page.goto(self.live_server_url + reverse(name, args=[self.venue.slug]))
                            self.assertEqual(response.status, 403)
                        response = page.goto(self.live_server_url + reverse('dashboard', args=[other.slug]))
                        self.assertEqual(response.status, 403)
                        context.close()
            browser.close()

    def test_empty_menu_starts_with_category_then_opens_first_product_form(self):
        branches = {lang: Branch.objects.create(restaurant=self.venue, name='New ' + lang)
                    for lang in ('ar', 'en')}
        with sync_playwright() as p:
            browser = p.chromium.launch(channel='chrome', headless=True)
            for lang, width in (('ar', 360), ('en', 1366)):
                context = self.context(browser, width, 'owner')
                page = context.new_page()
                self.visit(page, reverse('dashboard', args=[self.venue.slug]), lang, width)
                if width == 360:
                    page.locator('#menuToggle').click()
                page.locator('#branch-select').select_option(str(branches[lang].pk))
                page.wait_for_load_state('load')
                page.locator('.workspace-tasks a').first.click()
                tools = page.locator('#menuTools>details')
                expect(tools.nth(1)).to_have_attribute('open', '')
                expect(tools.first).not_to_have_attribute('open', '')
                form = page.locator('form').filter(has=page.locator('input[name=action][value=category]'))
                form.locator('input[name=name]').fill('مشروبات · Drinks')
                form.locator('button').click()
                expect(tools.first).to_have_attribute('open', '')
                form = page.locator('form').filter(has=page.locator('input[name=action][value=item]'))
                form.locator('input[name=name]').fill('عصير · Juice ' + lang)
                form.locator('select[name=category]').select_option(label='مشروبات · Drinks')
                form.locator('input[name=price]').fill('42.90')
                form.locator('button').click()
                expect(page.locator('.manage-item')).to_contain_text('عصير · Juice ' + lang)
                self.assertLessEqual(page.evaluate('document.documentElement.scrollWidth'), width + 1)
                page.screenshot(path=str(self.artifacts / f'first-product-{width}-{lang}.png'))
                context.close()
            browser.close()
        for lang, branch in branches.items():
            product = MenuItem.objects.get(category__branch=branch)
            self.assertEqual(product.name, 'عصير · Juice ' + lang)
            self.assertEqual(product.price, Decimal('42.90'))
        self.item.refresh_from_db()
        self.assertEqual(self.item.price, Decimal('42.75'))
