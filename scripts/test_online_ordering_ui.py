"""Branded online ordering, pickup/delivery review, staff fulfillment and settings."""
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from decimal import Decimal
import io
from pathlib import Path
import tempfile

from PIL import Image
from django.conf import settings
from django.contrib.auth.models import User
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.core.files.base import ContentFile
from django.test import Client, override_settings
from django.urls import reverse
from django.utils import timezone
from playwright.sync_api import expect, sync_playwright

from core.models import Branch, Category, Membership, MenuItem, Order, Plan, Restaurant, Subscription


class OnlineOrderingBrowserTests(StaticLiveServerTestCase):
    @classmethod
    def setUpClass(cls):
        media = tempfile.TemporaryDirectory(prefix='serveza-online-ui-')
        cls.addClassCleanup(media.cleanup)
        override = override_settings(MEDIA_ROOT=media.name)
        override.enable()
        cls.addClassCleanup(override.disable)
        super().setUpClass()

    def setUp(self):
        self.venue = Restaurant.objects.create(name='مطبخ ورد · Ward kitchen', slug='online-browser',
            online_ordering_enabled=True, online_delivery_enabled=True, delivery_fee='15.25',
            menu_primary_color='#8f3b24', menu_background_color='#fff5e9', menu_text_color='#34221a')
        logo = io.BytesIO()
        Image.new('RGB', (80, 80), '#8f3b24').save(logo, 'PNG')
        self.venue.logo.save('online-fixture.png', ContentFile(logo.getvalue()), save=True)
        self.branch = Branch.objects.create(restaurant=self.venue, name='فرع النيل · Nile', address='١٢ شارع النيل · 12 Nile Street')
        self.second = Branch.objects.create(restaurant=self.venue, name='فرع الورد · Ward')
        self.item = MenuItem.objects.create(category=Category.objects.create(branch=self.branch, name='وجبات · Meals'),
                                           name='وجبة البيت · House meal', price='42.75')
        MenuItem.objects.create(category=Category.objects.create(branch=self.second, name='Drinks'), name='Ward tea', price='22.00')
        plan = Plan.objects.create(name='Online browser', max_branches=2)
        Subscription.objects.create(restaurant=self.venue, plan=plan, status='active', ends_at=timezone.now() + timedelta(days=30))
        self.sessions = {}
        for role in ('owner', 'kitchen', 'cashier'):
            user = User.objects.create_user('online-browser-' + role)
            member = Membership.objects.create(user=user, restaurant=self.venue, role=role)
            member.branches.add(self.branch)
            client = Client()
            client.force_login(user)
            self.sessions[role] = client.cookies[settings.SESSION_COOKIE_NAME].value
        self.artifacts = Path('tmp/online-ui')
        self.artifacts.mkdir(parents=True, exist_ok=True)

    def context(self, browser, width, role=None):
        context = browser.new_context(viewport={'width': width, 'height': 900}, reduced_motion='reduce')
        if role:
            context.add_cookies([{'name': settings.SESSION_COOKIE_NAME, 'value': self.sessions[role], 'url': self.live_server_url}])
        return context

    def visit(self, page, path, lang, width):
        response = page.goto(self.live_server_url + path + '?lang=' + lang)
        self.assertEqual(response.status, 200)
        expect(page.locator('html')).to_have_attribute('dir', 'rtl' if lang == 'ar' else 'ltr')
        page.evaluate('document.fonts.ready')
        self.assert_responsive(page, width)

    def assert_responsive(self, page, width):
        self.assertLessEqual(page.evaluate('document.documentElement.scrollWidth'), width + 1)

    def open_checkout(self, page):
        page.locator('.menu-add').first.click()
        page.locator('#itemOptions button[type=submit]').click()
        page.locator('#openCart').click()

    def test_branded_pickup_and_delivery_review_reach_the_branch_and_staff(self):
        path = reverse('online_menu', args=[self.venue.slug])
        with sync_playwright() as p:
            browser = p.chromium.launch(channel='chrome', headless=True)
            for width in (360, 1366):
                for lang in ('ar', 'en'):
                    for mode in ('pickup', 'delivery'):
                        context = self.context(browser, width)
                        page = context.new_page()
                        errors = []
                        page.on('pageerror', lambda error, errors=errors: errors.append(str(error)))
                        self.visit(page, path, lang, width)
                        expect(page.locator('.restaurant-logo')).to_be_visible()
                        self.assertEqual(page.locator('body').evaluate('el=>getComputedStyle(el).getPropertyValue("--menu-primary").trim()'), '#8f3b24')
                        page.screenshot(path=str(self.artifacts / f'menu-{width}-{lang}.png'))
                        self.open_checkout(page)
                        form = page.locator('#onlineCheckout')
                        form.locator('[name=fulfillment]').select_option(mode)
                        form.locator('[name=customer_name]').fill('عميل الاختبار · Test guest')
                        form.locator('[name=customer_phone]').fill('01012345678')
                        if mode == 'delivery':
                            form.locator('[name=delivery_address]').fill('١٢ شارع الورد، الدور ٢ · 12 Ward Street, floor 2')
                        else:
                            expect(form.locator('[data-delivery-address]')).to_be_hidden()
                        total = '58' if mode == 'delivery' else '42.75'
                        expect(page.locator('.cart-total')).to_contain_text(total)
                        expect(page.locator('.online-price-row').last).to_contain_text('15.25' if mode == 'delivery' else '0')
                        self.assert_responsive(page, width)
                        page.locator('.guest-modal').screenshot(path=str(self.artifacts / f'checkout-{mode}-{width}-{lang}.png'))
                        page.locator('.cart-total').scroll_into_view_if_needed()
                        page.locator('.guest-modal').screenshot(path=str(self.artifacts / f'costs-{mode}-{width}-{lang}.png'))
                        page.get_by_role('button', name='Confirm and place order' if lang == 'en'
                                         else 'تأكيد وإرسال الطلب', exact=False).click()
                        page.wait_for_url('**/track/**')
                        expect(page.locator('.online-track')).to_contain_text(self.item.name)
                        expect(page.locator('.track-total')).to_contain_text(total.replace('.', ',') if lang == 'ar' else total)
                        expect(page.locator('.online-contact')).to_contain_text('01012345678')
                        self.assert_responsive(page, width)
                        page.screenshot(path=str(self.artifacts / f'track-{mode}-{width}-{lang}.png'))
                        self.assertEqual(errors, [])
                        context.close()
            for role in ('kitchen', 'cashier'):
                for lang in ('ar', 'en'):
                    context = self.context(browser, 360, role)
                    page = context.new_page()
                    self.visit(page, reverse('kitchen' if role == 'kitchen' else 'manage_orders', args=[self.venue.slug]), lang, 360)
                    if role == 'kitchen':
                        page.locator('#kitchenTable').select_option('online')
                        expect(page.locator('.kitchen-ticket:visible')).to_have_count(8)
                        self.assertNotIn('01012345678', page.locator('#workspaceMain').inner_text())
                    else:
                        expect(page.locator('.order-table').first).to_contain_text('01012345678')
                    page.screenshot(path=str(self.artifacts / f'{role}-360-{lang}.png'))
                    context.close()
            browser.close()
        self.assertEqual(Order.objects.filter(source='online', table__isnull=True).count(), 8)
        self.assertEqual(Order.objects.filter(fulfillment='delivery', delivery_fee='15.25', total=58).count(), 4)
        self.assertEqual(Order.objects.filter(fulfillment='pickup', delivery_fee=0, total='42.75').count(), 4)

    def test_branch_switch_and_fee_change_require_fresh_review_and_retry_is_safe(self):
        with ThreadPoolExecutor(max_workers=1) as database, sync_playwright() as p:
            browser = p.chromium.launch(channel='chrome', headless=True)
            context = self.context(browser, 390)
            page = context.new_page()
            self.visit(page, reverse('online_menu', args=[self.venue.slug]), 'en', 390)
            self.open_checkout(page)
            page.locator('#closeModal').click()
            page.locator('#onlineBranch').select_option(str(self.second.pk))
            page.locator('.online-branch-form button').click()
            expect(page.locator('#foodGrid')).to_contain_text('Ward tea')
            expect(page.locator('#foodGrid')).not_to_contain_text(self.item.name)
            expect(page.locator('#cartBar')).to_be_hidden()
            page.locator('#onlineBranch').select_option(str(self.branch.pk))
            page.locator('.online-branch-form button').click()
            expect(page.locator('#cartCount')).to_have_text('1')
            page.locator('#openCart').click()
            form = page.locator('#onlineCheckout')
            form.locator('[name=fulfillment]').select_option('delivery')
            form.locator('[name=customer_name]').fill('Test guest')
            form.locator('[name=customer_phone]').fill('01012345678')
            form.locator('[name=delivery_address]').fill('12 Test street')
            database.submit(Restaurant.objects.filter(pk=self.venue.pk).update, delivery_fee='20.00').result()
            page.get_by_role('button', name='Confirm and place order').click()
            expect(page.locator('.cart-modal [role=alert]')).to_contain_text('Prices or delivery fees changed')
            page.get_by_role('button', name='Refresh menu and prices').click()
            page.locator('#openCart').click()
            form = page.locator('#onlineCheckout')
            form.locator('[name=fulfillment]').select_option('delivery')
            form.locator('[name=customer_name]').fill('Test guest')
            form.locator('[name=customer_phone]').fill('01012345678')
            form.locator('[name=delivery_address]').fill('12 Test street')
            expect(page.locator('.cart-total')).to_contain_text('62.75')
            def drop_confirmation(route):
                route.fetch()
                route.abort()
            page.route('**/submit/', drop_confirmation)
            page.get_by_role('button', name='Confirm and place order').click()
            expect(page.get_by_role('heading', name='Awaiting order confirmation')).to_be_visible()
            page.unroute('**/submit/', drop_confirmation)
            page.get_by_role('button', name='Check order', exact=True).click()
            page.wait_for_url('**/track/**')
            expect(page.locator('.track-total')).to_contain_text('62.75')
            context.close()
            browser.close()
        self.assertEqual(Order.objects.count(), 1)

    def test_owner_saves_enablement_and_fees_and_sees_clear_errors_in_both_languages(self):
        with sync_playwright() as p:
            browser = p.chromium.launch(channel='chrome', headless=True)
            for width in (360, 1366):
                for lang in ('ar', 'en'):
                    context = self.context(browser, width, 'owner')
                    page = context.new_page()
                    self.visit(page, reverse('restaurant_settings', args=[self.venue.slug]), lang, width)
                    panel = page.locator('#onlineOrdering')
                    panel.scroll_into_view_if_needed()
                    form = panel.locator('form')
                    form.locator('[name=online-online_ordering_enabled]').check()
                    form.locator('[name=online-online_pickup_enabled]').check()
                    form.locator('[name=online-online_delivery_enabled]').check()
                    form.locator('[name=online-delivery_fee]').fill('18.75')
                    form.locator('button').click()
                    expect(page.get_by_text('Online ordering settings saved.' if lang == 'en'
                                            else 'تم حفظ إعدادات الطلب أونلاين.', exact=True)).to_be_visible()
                    expect(page.locator('#onlineOrdering input[name=online-delivery_fee]')).to_have_value('18.75')
                    panel.screenshot(path=str(self.artifacts / f'settings-{width}-{lang}.png'))
                    self.assert_responsive(page, width)
                    form = panel.locator('form')
                    form.locator('[name=online-online_pickup_enabled]').uncheck()
                    form.locator('[name=online-online_delivery_enabled]').uncheck()
                    form.locator('button').click()
                    expect(panel.locator('.form-errors')).to_contain_text('Enable pickup or delivery' if lang == 'en'
                                                                         else 'فعّل الاستلام من الفرع أو التوصيل')
                    context.close()
            browser.close()
        self.venue.refresh_from_db()
        self.assertEqual(self.venue.delivery_fee, Decimal('18.75'))
        self.assertTrue(self.venue.online_pickup_enabled)
