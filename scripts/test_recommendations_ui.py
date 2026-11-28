"""Real cart suggestions, customizations, attribution, recovery and bilingual layout."""
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

from django.conf import settings
from django.contrib.auth.models import User
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.test import Client
from django.urls import reverse
from django.utils import timezone
from playwright.sync_api import expect, sync_playwright

from core.models import (Branch, Category, DiningTable, ItemRecommendation, Membership, MenuItem,
    MenuSize, ModifierGroup, ModifierOption, Order, RecommendationConversion, RecommendationExposure,
    Restaurant, Plan, Subscription)


class RecommendationBrowserTests(StaticLiveServerTestCase):
    def setUp(self):
        self.restaurant = Restaurant.objects.create(name='مطعم الحديقة · Garden restaurant', slug='recommendation-ui',
            online_ordering_enabled=True, online_delivery_enabled=True, delivery_fee='10.25')
        Subscription.objects.create(restaurant=self.restaurant, plan=Plan.objects.create(name='Recommendation UI'),
                                    status='active', ends_at=timezone.now()+timedelta(days=30))
        self.branch = Branch.objects.create(restaurant=self.restaurant, recommendations_enabled=True)
        category = Category.objects.create(branch=self.branch, name='وجبات ومشروبات · Meals and drinks')
        self.burger = MenuItem.objects.create(category=category, name='برجر الحديقة · Garden burger', price='100.25')
        self.fries = MenuItem.objects.create(category=category, name='بطاطس مقرمشة مع صوص البيت الخاص · Crispy fries with house sauce', price='25.50')
        self.drink = MenuItem.objects.create(category=category, name='عصير · Juice', price='15.75')
        MenuSize.objects.create(item=self.fries, name='كبير · Large', price='35.50')
        group = ModifierGroup.objects.create(item=self.fries, name='الصوص · Sauce', min_select=1, max_select=1)
        self.sauce = ModifierOption.objects.create(group=group, name='صوص البيت · House sauce', price='3.25')
        ItemRecommendation.objects.create(branch=self.branch, source_item=self.burger, target_item=self.fries)
        ItemRecommendation.objects.create(branch=self.branch, source_item=self.burger, target_item=self.drink, priority=1)
        self.table = DiningTable.objects.create(branch=self.branch, name='Garden table')
        user = User.objects.create_user('recommendation-ui-owner')
        Membership.objects.create(user=user, restaurant=self.restaurant, role='owner')
        client = Client(); client.force_login(user)
        self.session = client.cookies[settings.SESSION_COOKIE_NAME].value
        self.output = Path('tmp/recommendations-ui'); self.output.mkdir(parents=True, exist_ok=True)

    def visit(self, page, name='public_menu', language='en'):
        args = [self.table.public_token] if name == 'public_menu' else [self.restaurant.slug]
        response = page.goto(self.live_server_url+reverse(name, args=args)+'?lang='+language)
        self.assertEqual(response.status, 200)
        expect(page.locator('html')).to_have_attribute('dir', 'rtl' if language == 'ar' else 'ltr')
        page.set_default_timeout(10000)

    def checkout(self, page):
        page.locator(f'.menu-add[data-id="{self.burger.pk}"]').click()
        page.locator('#itemOptions button[type=submit]').click()
        page.locator('#openCart').click()
        expect(page.locator('[data-recommendation-item]')).to_have_count(2)

    def add_fries(self, page):
        page.locator(f'[data-recommendation-item="{self.fries.pk}"] button').click()
        page.locator('#itemOptions input[name=size]').last.check()
        page.locator('#itemOptions input[type=checkbox]').check()
        page.locator('#itemOptions button[type=submit]').click()
        expect(page.locator('.cart-line')).to_have_count(2)
        expect(page.locator('[data-recommendation-item]')).to_have_count(1)
        expect(page.locator(f'[data-recommendation-item="{self.fries.pk}"]')).to_have_count(0)

    def layout(self, page):
        self.assertLessEqual(page.evaluate('document.documentElement.scrollWidth'), page.viewport_size['width']+1)
        self.assertFalse(page.locator('.guest-modal').evaluate('(n)=>n.scrollWidth>n.clientWidth+1'))

    def test_qr_and_online_journeys_in_both_languages_and_responsive_sizes(self):
        errors = []
        with sync_playwright() as p:
            browser = p.chromium.launch(channel='chrome', headless=True)
            for mode in ('public_menu', 'online_menu'):
                for language in ('ar', 'en'):
                    for width in (320, 390, 768, 1366):
                        context = browser.new_context(viewport={'width': width, 'height': 900}, reduced_motion='reduce')
                        page = context.new_page(); page.on('pageerror', lambda error: errors.append(str(error)))
                        self.visit(page, mode, language); self.checkout(page)
                        page.locator('.note-label textarea').fill('بدون بصل · No onions')
                        if mode == 'online_menu':
                            page.locator('[name=fulfillment]').select_option('delivery')
                            for field, value in [('customer_name','Test guest'),('customer_phone','01012345678'),('delivery_address','12 Garden street')]:
                                page.locator(f'[name={field}]').fill(value)
                        self.add_fries(page)
                        expect(page.locator('.note-label textarea')).to_have_value('بدون بصل · No onions')
                        if mode == 'online_menu':
                            expect(page.locator('[name=delivery_address]')).to_have_value('12 Garden street')
                            expect(page.locator('[name=fulfillment]')).to_have_value('delivery')
                        self.layout(page)
                        page.locator('.cart-recommendations').scroll_into_view_if_needed()
                        page.screenshot(path=str(self.output/f'{mode}-{language}-{width}.png'))
                        page.locator('.cart-review-footer button').click()
                        page.wait_for_url('**/track/**')
                        context.close()
            browser.close()
        self.assertFalse(errors, errors)
        self.assertEqual(Order.objects.count(), 16)
        self.assertEqual(RecommendationConversion.objects.count(), 16)
        self.assertEqual(set(RecommendationConversion.objects.values_list('amount',flat=True)), {Decimal('38.75')})

    def test_cancel_customization_reload_and_lost_confirmation_preserve_one_order(self):
        with sync_playwright() as p:
            browser = p.chromium.launch(channel='chrome', headless=True)
            page = browser.new_page(viewport={'width':390,'height':900})
            self.visit(page); self.checkout(page)
            page.locator('.cart-modal textarea').fill('Keep this note')
            page.locator(f'[data-recommendation-item="{self.fries.pk}"] button').click()
            page.keyboard.press('Escape')
            expect(page.locator('.cart-line')).to_have_count(1)
            expect(page.locator('.cart-modal textarea')).to_have_value('Keep this note')
            self.add_fries(page)
            page.reload(); page.locator('#openCart').click()
            expect(page.locator('.cart-line')).to_have_count(2)
            expect(page.locator('.cart-modal textarea')).to_have_value('Keep this note')
            def lose_confirmation(route):
                route.fetch()
                route.abort()
            page.route('**/order/',lose_confirmation,times=1)
            page.locator('.cart-review-footer button').click()
            expect(page.locator('#modalContent h2')).to_have_text('Awaiting order confirmation')
            page.reload()
            expect(page.locator('#modalContent h2')).to_have_text('Awaiting order confirmation')
            expect(page.locator('.cart-recommendations')).to_have_count(0)
            page.get_by_role('button',name='Check order',exact=True).click()
            page.wait_for_url('**/track/**')
            browser.close()
        self.assertEqual(Order.objects.count(),1)
        self.assertEqual(RecommendationConversion.objects.count(),1)

    def test_suggestion_failure_does_not_block_checkout(self):
        with sync_playwright() as p:
            browser=p.chromium.launch(channel='chrome',headless=True)
            page=browser.new_page()
            self.visit(page)
            page.route('**/recommendations/',lambda route:route.abort())
            page.locator(f'.menu-add[data-id="{self.burger.pk}"]').click()
            page.locator('#itemOptions button[type=submit]').click();page.locator('#openCart').click()
            page.locator('.cart-review-footer button').click();page.wait_for_url('**/track/**')
            browser.close()
        self.assertEqual(Order.objects.count(),1)

    def test_late_response_is_discarded_after_cart_changes(self):
        with sync_playwright() as p:
            browser=p.chromium.launch(channel='chrome',headless=True)
            page=browser.new_page()
            self.visit(page)
            def stale_response(route):
                response=route.fetch()
                page.evaluate("document.querySelector('.cart-line .remove-item').click()")
                route.fulfill(response=response)
            page.route('**/recommendations/',stale_response,times=1)
            page.locator(f'.menu-add[data-id="{self.burger.pk}"]').click()
            page.locator('#itemOptions button[type=submit]').click();page.locator('#openCart').click()
            expect(page.locator('#modalContent')).to_contain_text('Your cart is empty.')
            expect(page.locator('[data-recommendation-item]')).to_have_count(0)
            browser.close()

    def test_management_and_report_are_bilingual_responsive_and_keyboard_operable(self):
        with sync_playwright() as p:
            browser=p.chromium.launch(channel='chrome',headless=True)
            for language in ('ar','en'):
                for width in (320,390,768,1366):
                    context=browser.new_context(viewport={'width':width,'height':900})
                    context.add_cookies([{'name':settings.SESSION_COOKIE_NAME,'value':self.session,'url':self.live_server_url}])
                    page=context.new_page();self.visit(page,'manage_menu',language)
                    editor=page.locator(f'.menu-entry:has(input[name=item_id][value="{self.burger.pk}"])').first.locator('.menu-editor')
                    editor.locator('summary').press('Enter')
                    expect(editor.locator('.recommendation-create')).to_be_visible()
                    search=editor.locator('[data-recommendation-search]');search.fill('عصير')
                    select=editor.locator('.recommendation-create select')
                    self.assertEqual(select.locator('option:not([hidden])').count(),2)
                    self.assertLessEqual(page.evaluate('document.documentElement.scrollWidth'),width+1)
                    editor.locator('.recommendation-editor').scroll_into_view_if_needed()
                    page.screenshot(path=str(self.output/f'management-{language}-{width}.png'))
                    self.visit(page,'reports',language)
                    expect(page.locator('[data-recommendation-report]')).to_be_visible()
                    self.assertLessEqual(page.evaluate('document.documentElement.scrollWidth'),width+1)
                    page.locator('[data-recommendation-report]').scroll_into_view_if_needed()
                    page.screenshot(path=str(self.output/f'report-{language}-{width}.png'))
                    context.close()
            browser.close()

    def test_manager_can_configure_suggestions_without_javascript(self):
        dessert=MenuItem.objects.create(category=self.burger.category,name='براوني · Brownie',price='40.00')
        with sync_playwright() as p:
            browser=p.chromium.launch(channel='chrome',headless=True)
            context=browser.new_context(java_script_enabled=False,viewport={'width':1366,'height':900})
            context.add_cookies([{'name':settings.SESSION_COOKIE_NAME,'value':self.session,'url':self.live_server_url}])
            page=context.new_page();self.visit(page,'manage_menu','en')
            editor=page.locator(f'.menu-entry:has(input[name=item_id][value="{self.burger.pk}"])').first.locator('.menu-editor')
            editor.locator('summary').click()
            editor.locator(f'a[href*="recommend_item={self.burger.pk}"]').click()
            expect(editor).to_have_attribute('open','')
            form=editor.locator('.recommendation-create')
            form.locator('select[name=target_item]').select_option(str(dessert.pk))
            form.locator('button').click()
            expect(editor.locator('.recommendation-link')).to_have_count(3)
            context.close();browser.close()
        self.assertTrue(ItemRecommendation.objects.filter(source_item=self.burger,target_item=dessert,active=True).exists())
