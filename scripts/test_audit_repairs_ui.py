"""Browser regression for uncertain staff submissions and changed bilingual screens."""
import json
from datetime import timedelta
from pathlib import Path

from django.conf import settings
from django.contrib.auth.models import User
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.test import Client, override_settings
from django.urls import reverse
from django.utils import timezone
from playwright.sync_api import expect, sync_playwright

from core.models import Branch, Category, CollectionCorrection, DiningTable, Membership, MenuItem, Order, Plan, Restaurant, Subscription
from core.services import create_order, record_manual_payment
from uuid import uuid4


@override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
class AuditRepairBrowserTests(StaticLiveServerTestCase):
    def setUp(self):
        self.plan, _ = Plan.objects.get_or_create(name='Starter', defaults={
            'price_monthly': 600, 'max_staff': 4, 'max_tables': 20, 'max_items': 100,
            'max_branches': 1, 'max_stations': 1})
        self.venue = Restaurant.objects.create(name='مقهى المراجعة', slug='repair-cafe')
        Subscription.objects.create(restaurant=self.venue, plan=self.plan, status='active',
                                    ends_at=timezone.now()+timedelta(days=30))
        self.branch = Branch.objects.create(restaurant=self.venue, name='الفرع الرئيسي')
        self.table = DiningTable.objects.create(branch=self.branch, name='Window')
        self.item = MenuItem.objects.create(category=Category.objects.create(branch=self.branch, name='Drinks'),
                                           name='Tea', price='10.00')
        self.owner = User.objects.create_user('repair-owner')
        Membership.objects.create(user=self.owner, restaurant=self.venue, role='owner')
        self.paid_order = self.paid_fixture()
        self.operator = User.objects.create_superuser('repair-operator')
        pending = Restaurant.objects.create(name='Awaiting trial', slug='repair-pending')
        Branch.objects.create(restaurant=pending)
        Subscription.objects.create(restaurant=pending, status='pending')

    def paid_fixture(self):
        order = create_order(self.table, {'idempotency_key': str(uuid4()),
            'lines': [{'item_id': self.item.pk, 'quantity': 1}]})
        return record_manual_payment(order, self.owner)

    def session(self, user):
        client = Client()
        client.force_login(user)
        return client.cookies[settings.SESSION_COOKIE_NAME].value

    def test_pending_staff_request_is_frozen_and_restored_in_both_languages(self):
        session = self.session(self.owner)
        snapshots = Path('tmp/repair-ui')
        snapshots.mkdir(parents=True, exist_ok=True)
        with sync_playwright() as p:
            browser = p.chromium.launch(channel='chrome', headless=True)
            for lang, direction in [('ar', 'rtl'), ('en', 'ltr')]:
                context = browser.new_context(viewport={'width': 390, 'height': 850})
                context.add_cookies([{'name': settings.SESSION_COOKIE_NAME, 'value': session, 'url': self.live_server_url}])
                page = context.new_page()
                page.goto(self.live_server_url + reverse('staff_order', args=[self.venue.slug]) + '?lang=' + lang)
                expect(page.locator('html')).to_have_attribute('dir', direction)
                page.locator('#staffTable').select_option(str(self.table.pk))
                page.locator('.add-item').first.click()
                page.locator('#itemOptions button[type=submit]').click()
                page.locator('#openCart').click()
                sent = []
                def drop_first_response(route):
                    if route.request.method != 'POST':
                        route.continue_()
                        return
                    sent.append(json.loads(route.request.post_data))
                    if len(sent) == 1:
                        self.assertEqual(route.fetch().status, 200)
                        route.abort('failed')
                    else:
                        route.continue_()
                page.route('**/orders/new/', drop_first_response)
                page.locator('.cart-modal button.btn-primary').click()
                expect(page.locator('.cart-modal h2')).to_have_text('بانتظار تأكيد المطعم' if lang == 'ar' else 'Awaiting order confirmation')
                expect(page.locator('#staffTable')).to_be_disabled()
                expect(page.locator('.cart-modal .cart-list')).to_have_count(0)
                page.reload()
                expect(page.locator('.cart-modal h2')).to_have_text('بانتظار تأكيد المطعم' if lang == 'ar' else 'Awaiting order confirmation')
                expect(page.locator('#staffTable')).to_be_disabled()
                page.screenshot(path=str(snapshots / f'pending-{lang}.png'))
                page.get_by_role('button', name='التحقق من الطلب' if lang == 'ar' else 'Check order', exact=True).click()
                page.wait_for_url('**/orders/')
                self.assertEqual(len(sent), 2)
                self.assertEqual(sent[0]['idempotency_key'], sent[1]['idempotency_key'])
                self.assertEqual(sent[0]['lines'], sent[1]['lines'])
                context.close()
            browser.close()
        self.assertEqual(Order.objects.filter(source='staff').count(), 2)
        self.assertEqual(list(Order.objects.filter(source='staff').values_list('total', flat=True)), [10, 10])

    def test_changed_screens_are_responsive_and_bilingual(self):
        owner_session, operator_session = self.session(self.owner), self.session(self.operator)
        root = Path('tmp/repair-ui')
        root.mkdir(parents=True, exist_ok=True)
        owner_routes = [('reports', '?scope=all'), ('resource_archive_list', '?resource=table'),
                        ('resource_archive_list', '?resource=staff'), ('restaurant_settings', '?'),
                        ('manage_orders', '?'), ('staff', '?'), ('menu_import', '?')]
        with sync_playwright() as p:
            browser = p.chromium.launch(channel='chrome', headless=True)
            for width in (360, 1366):
                for lang, direction in [('ar', 'rtl'), ('en', 'ltr')]:
                    context = browser.new_context(viewport={'width': width, 'height': 850})
                    context.add_cookies([{'name': settings.SESSION_COOKIE_NAME, 'value': owner_session, 'url': self.live_server_url}])
                    page = context.new_page()
                    for route, query in owner_routes:
                        url = reverse(route, args=[self.venue.slug]) + query + '&lang=' + lang
                        response = page.goto(self.live_server_url + url)
                        self.assertEqual(response.status, 200)
                        expect(page.locator('html')).to_have_attribute('dir', direction)
                        self.assertLessEqual(page.evaluate('document.documentElement.scrollWidth'), width+1, (route, width, lang))
                        if route in ('reports', 'resource_archive_list'):
                            page.screenshot(path=str(root / f'{route}-{width}-{lang}.png'), full_page=True)
                    context.add_cookies([{'name': settings.SESSION_COOKIE_NAME, 'value': operator_session, 'url': self.live_server_url}])
                    for path in (reverse('platform_dashboard') + '?status=pending', reverse('platform_plan_edit', args=[self.plan.pk]) + '?'):
                        response = page.goto(self.live_server_url + path + '&lang=' + lang)
                        self.assertEqual(response.status, 200)
                        expect(page.locator('html')).to_have_attribute('dir', direction)
                        self.assertLessEqual(page.evaluate('document.documentElement.scrollWidth'), width+1, ('platform', width, lang))
                    public_paths = [reverse('privacy'), reverse('terms'),
                                    reverse('feature_detail', args=['billing']), reverse('feature_detail', args=['analytics'])]
                    for index, path in enumerate(public_paths):
                        response = page.goto(self.live_server_url + path + '?lang=' + lang)
                        self.assertEqual(response.status, 200)
                        expect(page.locator('html')).to_have_attribute('dir', direction)
                        self.assertLessEqual(page.evaluate('document.documentElement.scrollWidth'), width+1, (path, width, lang))
                        page.screenshot(path=str(root / f'public-{index}-{width}-{lang}.png'))
                    context.close()
            browser.close()

    def test_collection_correction_submits_localized_timestamp_in_both_languages(self):
        orders = [self.paid_order, self.paid_fixture()]
        session = self.session(self.owner)
        with sync_playwright() as p:
            browser = p.chromium.launch(channel='chrome', headless=True)
            for lang, order in zip(('ar', 'en'), orders):
                context = browser.new_context(viewport={'width': 390, 'height': 850})
                context.add_cookies([{'name': settings.SESSION_COOKIE_NAME, 'value': session, 'url': self.live_server_url}])
                page = context.new_page()
                page.goto(self.live_server_url + reverse('manage_orders', args=[self.venue.slug]) + '?lang=' + lang)
                form = page.locator(f'form[action$="/orders/{order.pk}/collection-correct/"]')
                form.locator('xpath=..').locator('summary').click()
                form.locator('input[name="reason"]').fill('Wrong operator entry')
                page.once('dialog', lambda dialog: dialog.accept())
                form.locator('button').click()
                expect(page.locator('.toast-stack')).to_contain_text('صُحح قيد التحصيل اليدوي' if lang == 'ar' else 'Manual collection entry corrected')
                context.close()
            browser.close()
        self.assertEqual(CollectionCorrection.objects.count(), 2)
        self.assertFalse(Order.objects.filter(pk__in=[order.pk for order in orders], payment_status='paid').exists())
