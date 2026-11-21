"""Exercise login using real CSRF cookies, including stale forms and network recovery."""
from datetime import timedelta
from pathlib import Path

from django.conf import settings
from django.contrib.auth.models import User
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.urls import reverse
from django.utils import timezone
from playwright.sync_api import expect, sync_playwright

from core.models import Branch, Membership, Plan, Restaurant, Subscription


class LoginUITests(StaticLiveServerTestCase):
    def test_login_recovers_stale_forms_for_owner_and_operator(self):
        password = 'Altitude!73840'
        owner = User.objects.create_user('owner@example.com', 'owner@example.com', password)
        operator = User.objects.create_superuser('SiteAdmin', 'operator@example.com', password)
        venue = Restaurant.objects.create(name='مطعم الياسمين', slug='login-venue')
        Branch.objects.create(restaurant=venue)
        Membership.objects.create(user=owner, restaurant=venue, role='owner')
        Subscription.objects.create(restaurant=venue, plan=Plan.objects.get(name='Starter'),
                                    status='active', ends_at=timezone.now() + timedelta(days=30))
        shots = Path(settings.BASE_DIR) / 'tmp' / 'login-ui'
        shots.mkdir(parents=True, exist_ok=True)
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(channel='chrome')
            for width, language, user, destination in (
                (360, 'ar', owner, reverse('dashboard', args=[venue.slug])),
                (1366, 'en', operator, reverse('platform_dashboard')),
                (360, 'en', operator, reverse('platform_dashboard')),
                (1366, 'ar', owner, reverse('dashboard', args=[venue.slug])),
            ):
                context = browser.new_context(viewport={'width': width, 'height': 850})
                page = context.new_page()
                url = self.live_server_url + reverse('login') + '?lang=' + language
                page.goto(url)
                expect(page.locator('html')).to_have_attribute('dir', 'rtl' if language == 'ar' else 'ltr')
                page.evaluate('document.fonts.ready')
                self.assertLessEqual(page.evaluate('document.documentElement.scrollWidth'), width)
                page.screenshot(path=str(shots / f'login-{width}-{language}.png'), full_page=True)
                identifier = user.username if user == operator else user.email
                page.locator('input[name="email"]').fill(identifier)
                page.locator('input[name="password"]').fill(password)
                # Simulate a cookie rotated by sign-in in another tab.
                context.add_cookies([{'name': settings.CSRF_COOKIE_NAME, 'value': 'a' * 32,
                                      'url': self.live_server_url}])
                page.route('**/login/**', lambda route: route.abort())
                page.locator('[data-login-form] button[type="submit"]').click()
                expect(page.locator('[data-login-error]')).to_be_visible()
                expect(page.locator('input[name="email"]')).to_have_value(identifier)
                expect(page.locator('[data-login-form] button[type="submit"]')).to_be_enabled()
                page.screenshot(path=str(shots / f'retry-{width}-{language}.png'), full_page=True)
                page.unroute('**/login/**')
                # Missing cookies can be restored by the same refresh, too.
                if language == 'en':
                    context.clear_cookies(name=settings.CSRF_COOKIE_NAME)
                page.locator('[data-login-form] button[type="submit"]').click()
                expect(page).to_have_url(self.live_server_url + destination)
                expect(page.locator('h1')).to_be_visible()
                context.close()
            browser.close()
