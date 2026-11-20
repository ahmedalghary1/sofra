"""Run with python manage.py test scripts.test_restaurant_ui (Playwright and Chrome)."""
from decimal import Decimal

from django.conf import settings
from django.contrib.auth.models import User
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.test import Client
from django.urls import reverse
from playwright.sync_api import expect, sync_playwright

from core.models import (
    Branch,
    Category,
    DiningTable,
    Membership,
    MenuItem,
    MenuSize,
    Order,
    Restaurant,
)


class ServiceAcrossRolesTests(StaticLiveServerTestCase):
    def test_branch_picker_and_order_headings_have_vertical_clearance(self):
        venue = Restaurant.objects.create(name='QA Café', slug='two-branches')
        Branch.objects.create(restaurant=venue, name='الفرع الرئيسي')
        Branch.objects.create(restaurant=venue, name='Second branch')
        owner = User.objects.create_user(username='layout-owner')
        Membership.objects.create(restaurant=venue, user=owner, role='owner')
        self.client.force_login(owner)
        url = self.live_server_url + reverse('manage_orders', args=[venue.slug])

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(channel='chrome')
            for width in (360, 1366):
                context = browser.new_context(viewport={'width': width, 'height': 850}, reduced_motion='reduce')
                context.add_cookies([{'name': settings.SESSION_COOKIE_NAME, 'url': self.live_server_url,
                                      'value': self.client.cookies[settings.SESSION_COOKIE_NAME].value}])
                page = context.new_page()
                for language in ('ar', 'en'):
                    with self.subTest(width=width, language=language):
                        page.goto(f'{url}?lang={language}')
                        page.evaluate('document.fonts.ready')
                        title = page.locator('.panel-head h2').first.bounding_box()
                        description = page.locator('.panel-head h2 + p').first.bounding_box()
                        self.assertGreaterEqual(description['y'] - (title['y'] + title['height']), 6)
                        if width == 360:
                            page.locator('#menuToggle').click()
                        card = page.locator('.restaurant-switch').bounding_box()
                        picker = page.locator('.branch-selector label').bounding_box()
                        self.assertGreaterEqual(picker['y'] - (card['y'] + card['height']), 6)
                context.close()
            browser.close()

    def test_guest_to_kitchen_waiter_and_cashier(self):
        venue = Restaurant.objects.create(name='QA Café', slug='qa-cafe')
        branch = Branch.objects.create(restaurant=venue)
        table = DiningTable.objects.create(branch=branch, name='Window 4')
        drinks = Category.objects.create(branch=branch, name='Drinks')
        item = MenuItem.objects.create(category=drinks, name='Latte', price='60.00')
        MenuSize.objects.create(item=item, name='Large', price='80.00')
        sessions = {}
        for role in ['kitchen', 'cashier', 'waiter']:
            member = Membership.objects.create(restaurant=venue, role=role,
                                               user=User.objects.create_user(username=role))
            member.branches.add(branch)
            client = Client()
            client.force_login(member.user)
            sessions[role] = client.cookies[settings.SESSION_COOKIE_NAME].value

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(channel='chrome')
            def open_page(name, url, language='en'):
                context = browser.new_context(viewport={'width': 390, 'height': 844}, reduced_motion='reduce')
                if name in sessions:
                    context.add_cookies([{'name': settings.SESSION_COOKIE_NAME, 'value': sessions[name],
                                          'url': self.live_server_url}])
                page = context.new_page()
                page.goto(self.live_server_url + url + '?lang=' + language)
                expect(page.locator('html')).to_have_attribute('dir', 'ltr' if language == 'en' else 'rtl')
                return page

            kitchen = open_page('kitchen', reverse('kitchen', args=[venue.slug]))
            kitchen.locator('#menuToggle').click()
            expect(kitchen.locator('#sidebar')).to_have_attribute('aria-modal', 'true')
            kitchen.keyboard.press('Escape')
            expect(kitchen.locator('#menuToggle')).to_be_focused()
            kitchen.locator('#kitchenSound').click()
            expect(kitchen.locator('#kitchenSound')).to_have_attribute('aria-pressed', 'true')
            guest = open_page('guest', reverse('public_menu', args=[table.public_token]))
            guest.locator('#menuSearch').fill('missing')
            expect(guest.locator('#menuEmpty')).to_be_visible()
            guest.locator('#menuSearch').fill('Latte')
            guest.get_by_role('button', name='Add Latte').click()
            guest.get_by_role('radio', name='Large', exact=False).check()
            guest.get_by_role('button', name='Add to cart', exact=False).click()
            guest.locator('#openCart').click()
            expect(guest.locator('.cart-total')).to_contain_text('80 EGP')
            guest.get_by_role('button', name='Confirm and place order').click()
            expect(guest.locator('.track-lines')).to_contain_text('Large')
            expect(guest.locator('.track-total')).to_contain_text('80.00')
            kitchen.evaluate("window.dispatchEvent(new Event('online'))")
            expect(kitchen.locator('.kitchen-ticket')).to_contain_text('Window 4', timeout=15000)
            expect(kitchen.locator('#kitchenSound')).to_have_attribute('aria-pressed', 'true')
            for label in ['Accept order', 'Start preparing', 'Ready to serve']:
                kitchen.get_by_role('button', name=label).click()
            expect(kitchen.locator('.kitchen-ticket')).to_have_attribute('data-status', 'ready')
            guest.get_by_role('link', name='Add another table order').click()
            guest.get_by_role('button', name='Request bill').click()
            waiter = open_page('waiter', reverse('manage_calls', args=[venue.slug]))
            waiter.get_by_role('button', name='Acknowledge').click()
            expect(waiter.locator('.call-card')).to_contain_text('Accepted')
            cashier = open_page('cashier', reverse('manage_orders', args=[venue.slug]), 'ar')
            cashier.get_by_role('button', name='تم التقديم').click()
            cashier.on('dialog', lambda dialog: dialog.accept())
            cashier.get_by_role('button', name='تسجيل التحصيل').click()
            expect(cashier.locator('.data-table')).to_contain_text('تحصيل مسجل')
            browser.close()
        order = Order.objects.get(table=table)
        self.assertEqual((order.total, order.status, order.payment_status), (Decimal('80.00'), 'served', 'paid'))
