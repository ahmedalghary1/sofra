"""Verify manual activation with a real browser and an isolated test database."""
from datetime import timedelta
from pathlib import Path

from django.conf import settings
from django.contrib.auth.models import User
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.test import Client
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone
from playwright.sync_api import expect, sync_playwright

from core.models import Branch, Membership, Plan, Restaurant, Subscription


class SubscriptionActivationUITests(StaticLiveServerTestCase):
    def test_activation_and_changed_screens_in_both_languages_and_sizes(self):
        plan = Plan.objects.get(name='Growth')
        operator = User.objects.create_superuser('activation-operator', 'operator@example.com')
        operator_client = Client()
        operator_client.force_login(operator)
        cases = []
        for width in (360, 1366):
            for language in ('ar', 'en'):
                owner = User.objects.create_user(username=f'owner-{width}-{language}',
                                                 email=f'owner-{width}-{language}@example.com')
                venue = Restaurant.objects.create(name='مطعم الياسمين', slug=f'venue-{width}-{language}')
                branch = Branch.objects.create(restaurant=venue)
                Membership.objects.create(user=owner, restaurant=venue, role='owner')
                teammate = User.objects.create_user(username=f'floor-{width}-{language}')
                member = Membership.objects.create(user=teammate, restaurant=venue, role='waiter')
                member.branches.add(branch)
                Subscription.objects.create(restaurant=venue, status='pending')
                client = Client()
                client.force_login(owner)
                cases.append((width, language, venue, client.cookies[settings.SESSION_COOKIE_NAME].value))
        shots = Path(settings.BASE_DIR) / 'tmp' / 'activation-ui'
        shots.mkdir(parents=True, exist_ok=True)
        end = (timezone.localdate() + timedelta(days=30)).isoformat()
        emails = {lang: render_to_string('emails/owner_activation.html', {
            'english': lang == 'en', 'name': 'سارة',
            'link': 'https://example.com/verify/illustrative-verification-link/',
        }) for lang in ('ar', 'en')}
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(channel='chrome')
            for width, language, venue, cookie in cases:
                context = browser.new_context(viewport={'width': width, 'height': 850})
                page = context.new_page()
                page.set_content(emails[language])
                self.check_layout(page, language, shots / f'email-{width}-{language}.png')
                for name in ('home', 'register', 'verification_pending'):
                    response = page.goto(self.live_server_url + reverse(name) + '?lang=' + language)
                    self.assertEqual(response.status, 200)
                    self.check_layout(page, language, shots / f'{name}-{width}-{language}.png')
                context.add_cookies([{'name': settings.SESSION_COOKIE_NAME, 'url': self.live_server_url,
                                      'value': cookie}])
                page.goto(self.live_server_url + reverse('dashboard', args=[venue.slug]) + '?lang=' + language)
                expect(page.locator('#activation-title')).to_be_visible()
                self.check_layout(page, language, shots / f'waiting-{width}-{language}.png')
                page.get_by_role('button', name='Request contact from customer service' if language == 'en'
                                 else 'اطلب التواصل مع خدمة العملاء').click()
                expect(page.locator('.reset-card [role="status"]')).to_be_visible()
                self.check_layout(page, language, shots / f'requested-{width}-{language}.png')

                admin = browser.new_context(viewport={'width': width, 'height': 850})
                admin.add_cookies([{'name': settings.SESSION_COOKIE_NAME, 'url': self.live_server_url,
                                   'value': operator_client.cookies[settings.SESSION_COOKIE_NAME].value}])
                operator_page = admin.new_page()
                operator_page.goto(self.live_server_url + reverse('platform_create_restaurant') + '?lang=' + language)
                self.check_layout(operator_page, language, shots / f'create-{width}-{language}.png')
                for section in ('all', 'active', 'people', 'plans'):
                    response = operator_page.goto(self.live_server_url + reverse('platform_dashboard') +
                                                  f'?status={section}&lang={language}')
                    self.assertEqual(response.status, 200)
                    if section == 'people':
                        operator_page.locator('form.console-fields').first.locator('..').locator('summary').click()
                        access = operator_page.locator('form.console-fields').first
                        access.locator('[name="role"]').select_option('cashier')
                        access.locator('[name="reason"]').fill('Shift assignment')
                        self.check_layout(operator_page, language, shots / f'access-{width}-{language}.png')
                        access.get_by_role('button').click()
                        expect(operator_page.locator('.toast.success')).to_be_visible()
                    self.check_layout(operator_page, language, shots / f'{section}-{width}-{language}.png')
                for name, args in [('platform_plan_create', []), ('platform_plan_edit', [plan.pk])]:
                    response = operator_page.goto(self.live_server_url + reverse(name, args=args) + '?lang=' + language)
                    self.assertEqual(response.status, 200)
                    self.check_layout(operator_page, language, shots / f'{name}-{width}-{language}.png')
                operator_page.goto(self.live_server_url + reverse('platform_dashboard') +
                                   f'?status=pending&q={venue.slug}&lang={language}')
                self.check_layout(operator_page, language, shots / f'platform-{width}-{language}.png')
                if width == 360:
                    self.assertGreater(operator_page.locator('.platform-card-head>div').bounding_box()['width'], 260)
                    for detail in operator_page.locator('.platform-actions>details').all():
                        self.assertGreater(detail.bounding_box()['width'], 240)
                form = operator_page.locator('.platform-subscription-form')
                form.locator('select[name="plan_id"]').select_option(str(plan.pk))
                form.locator('[name="ends_at"]').fill(end)
                form.locator('[name="reason"]').fill('Confirmed external payment')
                form.get_by_role('button').click()
                expect(operator_page.locator(f'#restaurant-{venue.pk} .platform-details')).to_contain_text('Growth')
                page.get_by_role('link', name='Check subscription status' if language == 'en'
                                 else 'تحقق من حالة الاشتراك').click()
                expect(page).to_have_url(self.live_server_url + reverse('dashboard', args=[venue.slug]))
                admin.close()
                context.close()
            browser.close()

    def check_layout(self, page, language, screenshot):
        page.evaluate('document.fonts.ready')
        self.assertEqual(page.locator('html').get_attribute('dir'), 'rtl' if language == 'ar' else 'ltr')
        self.assertLessEqual(page.evaluate('document.documentElement.scrollWidth'), page.viewport_size['width'])
        page.screenshot(path=str(screenshot), full_page=True)
