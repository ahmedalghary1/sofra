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
        Subscription.objects.create(restaurant=venue, plan=Plan.objects.get_or_create(name='Starter')[0],
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
                page.locator('input[name="username"]').fill(identifier)
                page.locator('input[name="password"]').fill(password)
                # Recover a missing cookie, even when the first refresh is interrupted.
                context.clear_cookies(name=settings.CSRF_COOKIE_NAME)
                page.route('**/login/**', lambda route: route.abort())
                page.locator('[data-login-form] button[type="submit"]').click()
                expect(page.locator('[data-login-error]')).to_be_visible()
                expect(page.locator('input[name="username"]')).to_have_value(identifier)
                expect(page.locator('[data-login-form] button[type="submit"]')).to_be_enabled()
                page.screenshot(path=str(shots / f'retry-{width}-{language}.png'), full_page=True)
                page.unroute('**/login/**')
                # A rotated cookie permits native submission without another GET.
                if language == 'ar':
                    context.add_cookies([{'name': settings.CSRF_COOKIE_NAME, 'value': 'a' * 32,
                                          'url': self.live_server_url}])
                page.locator('[data-login-form] button[type="submit"]').click()
                expect(page).to_have_url(self.live_server_url + destination)
                expect(page.locator('h1')).to_be_visible()
                context.close()
            browser.close()

    def test_operator_logs_in_edits_own_account_and_logs_in_again(self):
        for lang in ('ar', 'en'):
            profile_name = f'Operator profile {lang}'
            original = 'OldPhrase!30762'
            updated = 'NewPhrase!50893'
            operator = User.objects.create_superuser(f'Operator-{lang}', f'Ops-{lang}@example.com', original)
            User.objects.create_user(f'Legacy-{lang}', operator.email)
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(channel='chrome')
                page = browser.new_page()
                url = self.live_server_url + reverse('login') + '?lang=' + lang
                page.goto(url)
                # A proxy that blocks background fetch must still accept a normal document POST.
                page.route('**/login/**', lambda route: route.abort() if route.request.resource_type == 'fetch' else route.continue_())
                page.locator('[autocomplete=username]').fill(operator.username)
                page.locator('[autocomplete=current-password]').fill(original)
                page.locator('[data-login-form] button[type=submit]').click()
                expect(page).to_have_url(self.live_server_url + reverse('platform_dashboard'))
                page.get_by_role('link', name='Login accounts' if lang == 'en' else 'حسابات الدخول').click()
                page.locator(f'a[href="{reverse("platform_account_edit", args=[operator.pk])}"]').click()
                page.locator('[name=name]').fill(profile_name)
                page.locator('[name=password]').fill(updated)
                page.locator('[name=reason]').fill('Update profile')
                page.get_by_role('button', name='Save account' if lang == 'en' else 'حفظ الحساب').click()
                expect(page).to_have_url(self.live_server_url + reverse('platform_accounts'))
                expect(page.locator('.desk-row').filter(has_text=profile_name)).to_be_visible()
                page.locator('form[action="/logout/"] button').click()
                page.goto(url)
                page.locator('[autocomplete=username]').fill(operator.username)
                page.locator('[autocomplete=current-password]').fill(updated)
                page.locator('[data-login-form] button[type=submit]').click()
                expect(page).to_have_url(self.live_server_url + reverse('platform_dashboard'))
                browser.close()
            operator.refresh_from_db()
            self.assertEqual(operator.email, f'Ops-{lang}@example.com')
            self.assertEqual(operator.first_name, profile_name)
            self.assertTrue(operator.check_password(updated))
