"""Run with python manage.py test scripts.test_restaurant_ui (Playwright and Chrome)."""
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from uuid import uuid4

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
    ModifierGroup,
    ModifierOption,
    Order,
    Restaurant,
)


class ServiceAcrossRolesTests(StaticLiveServerTestCase):
    def test_pinned_category_picker_shows_matching_items_at_the_start_of_results(self):
        venue = Restaurant.objects.create(name='Menu QA', slug='menu-qa')
        branch = Branch.objects.create(restaurant=venue)
        table = DiningTable.objects.create(branch=branch, name='Table A')
        for index in range(10):
            category = Category.objects.create(branch=branch, name=f'Category {index}')
            MenuItem.objects.bulk_create([MenuItem(category=category, name=f'Plate {index}-{n}', price=25) for n in range(8)])
        url = self.live_server_url + reverse('public_menu', args=[table.public_token])
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(channel='chrome')
            for width, language, columns in [(320, 'ar', 2), (768, 'en', 3), (1440, 'ar', 4)]:
                page = browser.new_page(viewport={'width': width, 'height': 800})
                page.goto(url + '?lang=' + language)
                page.evaluate('document.fonts.ready')
                self.assertEqual(page.locator('#foodGrid').evaluate("n => getComputedStyle(n).gridTemplateColumns.split(' ').length"), columns)
                page.evaluate('window.scrollTo(0,1200)')
                bar = page.locator('.menu-filter-bar')
                self.assertAlmostEqual(bar.bounding_box()['y'], 0, delta=1)
                last = page.locator('#categoryPills button').last
                picker = page.locator('.menu-category-picker select')
                if width == 320:
                    expect(picker).to_be_visible()
                    picker.select_option(last.get_attribute('data-category'))
                else:
                    last.click()
                expect(last).to_have_attribute('aria-pressed', 'true')
                cards = page.locator('.menu-product:visible')
                expect(cards).to_have_count(8)
                self.assertGreaterEqual(cards.first.bounding_box()['y'], bar.bounding_box()['height'] - 1)
                self.assertLess(cards.first.bounding_box()['y'], 270)
                page.close()
            browser.close()

    def test_guest_draft_is_table_session_scoped_and_rechecks_catalog(self):
        venue = Restaurant.objects.create(name='مطعم الحي · Neighborhood Kitchen', slug='draft-menu')
        branch = Branch.objects.create(restaurant=venue, name='الفرع الرئيسي · Main branch')
        table = DiningTable.objects.create(branch=branch, name='Window')
        other = DiningTable.objects.create(branch=branch, name='Patio')
        menu = Category.objects.create(branch=branch, name='Drinks')
        item = MenuItem.objects.create(category=menu, name='Latte', price='60.00')
        group = ModifierGroup.objects.create(item=item, name='Milk', min_select=1)
        oat = ModifierOption.objects.create(group=group, name='Oat', price='10.00')
        url = self.live_server_url + reverse('public_menu', args=[table.public_token])
        with ThreadPoolExecutor(max_workers=1) as database, sync_playwright() as playwright:
            browser = playwright.chromium.launch(channel='chrome')
            page = browser.new_page(viewport={'width': 360, 'height': 740})
            page.goto(url + '?lang=en')
            page.evaluate('document.fonts.ready')
            brand = page.locator('.guest-brand').bounding_box()
            self.assertLessEqual(brand['y'] + brand['height'], page.locator('.guest-hero').bounding_box()['y'])
            add = page.get_by_role('button', name='Add Latte')
            self.assertLess(add.bounding_box()['y'] + add.bounding_box()['height'], 740)
            add.click()
            page.get_by_label('Oat', exact=False).check()
            page.get_by_label('Increase quantity', exact=True).click()
            page.get_by_role('button', name='Add to cart', exact=False).click()
            page.goto(self.live_server_url + reverse('public_menu', args=[other.public_token]))
            expect(page.locator('#cartBar')).to_be_hidden()
            page.goto(url + '?lang=ar')
            expect(page.locator('#cartCount')).to_have_text('2')
            expect(page.locator('#cartTotal')).to_have_text('140')
            oat.price = Decimal('20')
            database.submit(oat.save, update_fields=['price']).result()
            page.reload()
            expect(page.locator('#cartMenuChange')).to_be_visible()
            expect(page.locator('#cartTotal')).to_have_text('160')
            table.session_token = uuid4()
            database.submit(table.save, update_fields=['session_token']).result()
            page.reload()
            expect(page.locator('#cartBar')).to_be_hidden()
            browser.close()

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
            guest.locator('textarea').fill('Less foam')
            guest.reload()
            guest.locator('#openCart').click()
            expect(guest.locator('textarea')).to_have_value('Less foam')
            expect(guest.locator('.cart-total')).to_contain_text('80 EGP')
            endpoint = '**' + reverse('place_order', args=[table.public_token])
            def lose_response(route):
                self.assertEqual(route.fetch().status, 200)
                route.abort()
            guest.route(endpoint, lose_response, times=1)
            guest.get_by_role('button', name='Confirm and place order').click()
            expect(guest.get_by_role('button', name='Check order')).to_be_visible()
            guest.reload()
            guest.get_by_role('button', name='Check order').click()
            expect(guest.locator('.track-lines')).to_contain_text('Large')
            expect(guest.locator('.track-total')).to_contain_text('80.00')
            kitchen.evaluate("window.dispatchEvent(new Event('online'))")
            expect(kitchen.locator('.kitchen-ticket')).to_contain_text('Window 4', timeout=15000)
            expect(kitchen.locator('#kitchenSound')).to_have_attribute('aria-pressed', 'true')
            for label in ['Accept order', 'Start preparing', 'Ready to serve']:
                kitchen.get_by_role('button', name=label).click()
            expect(kitchen.locator('.kitchen-ticket')).to_have_attribute('data-status', 'ready')
            guest.get_by_role('link', name='Add another table order').click()
            expect(guest.locator('#cartBar')).to_be_hidden()
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
        self.assertEqual(order.note, 'Less foam')
        self.assertEqual((order.total, order.status, order.payment_status), (Decimal('80.00'), 'served', 'paid'))
