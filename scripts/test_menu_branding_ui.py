"""Exercise saved and unsaved menu branding in Arabic/English on mobile/desktop."""
import io
import tempfile
from datetime import timedelta
from pathlib import Path

from PIL import Image
from django.conf import settings
from django.contrib.auth.models import User
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.test import Client, override_settings
from django.urls import reverse
from django.utils import timezone
from playwright.sync_api import expect, sync_playwright

from core.branding import MENU_COLOR_DEFAULTS, menu_theme
from core.models import Branch, Category, DiningTable, Membership, MenuItem, Plan, Restaurant, Subscription
from scripts.test_responsive_ux import LAYOUT_AUDIT


@override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
class MenuBrandingBrowserTests(StaticLiveServerTestCase):
    @classmethod
    def setUpClass(cls):
        media = tempfile.TemporaryDirectory(prefix='serveza-brand-ui-')
        cls.addClassCleanup(media.cleanup)
        override = override_settings(MEDIA_ROOT=media.name)
        override.enable()
        cls.addClassCleanup(override.disable)
        super().setUpClass()

    def setUp(self):
        self.venue = Restaurant.objects.create(name='قهوة ورد · Ward café', slug='branding-ui',
            description='قهوة طازجة كل يوم · Fresh coffee daily',name_en='Ward café',online_ordering_enabled=True)
        plan, _ = Plan.objects.get_or_create(name='Brand UI', defaults={'max_branches': 2})
        Subscription.objects.create(restaurant=self.venue, plan=plan, status='active', ends_at=timezone.now() + timedelta(days=30))
        self.branch = Branch.objects.create(restaurant=self.venue, name='فرع النيل')
        self.table = DiningTable.objects.create(branch=self.branch, name='T7')
        MenuItem.objects.create(category=Category.objects.create(branch=self.branch, name='قهوة'), name='لاتيه خاص', price='42.75')
        owner = User.objects.create_user('brand-ui-owner')
        Membership.objects.create(user=owner, restaurant=self.venue, role='owner')
        client = Client()
        client.force_login(owner)
        self.session = client.cookies[settings.SESSION_COOKIE_NAME].value
        output = io.BytesIO()
        Image.new('RGBA', (320, 160), (155, 50, 100, 200)).save(output, 'PNG')
        self.logo = output.getvalue()
        self.artifacts = Path('tmp/branding-ui')
        self.artifacts.mkdir(parents=True, exist_ok=True)

    def context(self, browser, width):
        context = browser.new_context(viewport={'width': width, 'height': 900})
        context.add_cookies([{'name': settings.SESSION_COOKIE_NAME, 'value': self.session, 'url': self.live_server_url}])
        return context

    def color(self, page, key, value):
        page.locator(f'[data-menu-color="{key}"]').evaluate(
            '(field, value) => { field.value = value; field.dispatchEvent(new Event("input", {bubbles:true})); }', value)

    def assert_responsive(self, page, width):
        self.assertLessEqual(page.evaluate('document.documentElement.scrollWidth'), width + 1)

    def test_saved_theme_and_cart_match_live_preview_in_both_languages_and_widths(self):
        palettes = [
            {'menu_primary_color': '#eab83b', 'menu_background_color': '#fff4e5', 'menu_text_color': '#332518'},
            {'menu_primary_color': '#b28cff', 'menu_background_color': '#171321', 'menu_text_color': '#f8f2ff'},
        ]
        with sync_playwright() as p:
            browser = p.chromium.launch(channel='chrome', headless=True)
            for width in (320, 390, 768, 1366):
                for index, (lang, direction) in enumerate((('ar', 'rtl'), ('en', 'ltr'))):
                    context = self.context(browser, width)
                    page = context.new_page()
                    errors = []
                    page.on('pageerror', lambda error: errors.append(str(error)))
                    page.goto(self.live_server_url + reverse('restaurant_settings', args=[self.venue.slug]) + '?lang=' + lang)
                    expect(page.locator('html')).to_have_attribute('dir', direction)
                    page.locator('[data-menu-logo]').set_input_files({'name': 'logo.png', 'mimeType': 'image/png', 'buffer': self.logo})
                    palette = palettes[(index + (width == 1366)) % 2]
                    for key, value in palette.items():
                        self.color(page, key, value)
                    theme = menu_theme(palette)
                    computed = page.locator('#menuBrandingPreview').evaluate(
                        '(el) => Object.fromEntries(["primary", "background", "text", "on-primary", "surface", "muted", "border", "soft"].map(key => [key.replace("-", "_"), getComputedStyle(el).getPropertyValue("--menu-" + key).trim()]))')
                    self.assertEqual(computed, theme)
                    expect(page.locator('#brandingPreviewLogo')).to_be_visible()
                    self.assert_responsive(page, width)
                    page.locator('#menuBrandingPreview').screenshot(path=str(self.artifacts / f'preview-{width}-{lang}.png'))
                    page.locator('#menuBrandingForm button[type=submit]').click()
                    page.wait_for_url(self.live_server_url + reverse('restaurant_settings', args=[self.venue.slug]))
                    expect(page.get_by_text('Menu branding saved.' if lang == 'en' else 'تم حفظ هوية المنيو.', exact=True)).to_be_visible()
                    page.locator('#menuBranding').scroll_into_view_if_needed()
                    page.screenshot(path=str(self.artifacts / f'settings-{width}-{lang}.png'))
                    shared = None
                    for mode,menu_path in (('table',reverse('public_menu',args=[self.table.public_token])),
                                           ('online',reverse('online_menu',args=[self.venue.slug]))):
                        page.goto(self.live_server_url + menu_path + '?lang=' + lang)
                        expect(page.locator('html')).to_have_attribute('dir', direction)
                        page.wait_for_function('document.querySelector(".restaurant-logo").naturalWidth > 0')
                        self.assertEqual(page.locator('body').evaluate('(el) => getComputedStyle(el).getPropertyValue("--menu-background").trim()'), theme['background'])
                        self.assertEqual(page.locator('.menu-add').first.evaluate('(el) => getComputedStyle(el).backgroundColor'), self.rgb(theme['primary']))
                        self.assertEqual(page.locator('.menu-product-copy h2').first.evaluate('(el) => getComputedStyle(el).color'), self.rgb(theme['text']))
                        self.assertEqual(page.locator('body').evaluate('(el) => getComputedStyle(el).backgroundColor'), self.rgb(theme['background']))
                        self.assertEqual(page.locator('#menuSearch').evaluate('(el) => getComputedStyle(el, "::placeholder").color'), self.rgb(theme['muted']))
                        self.assert_responsive(page, width)
                        page.screenshot(path=str(self.artifacts / f'{mode}-menu-{width}-{lang}.png'))
                        observed = page.evaluate("""() => Object.fromEntries(['.guest-header','.guest-brand','.restaurant-logo',
                            '.menu-search-field','.menu-filter-bar','.menu-grid','.menu-product','.menu-add'].map(selector=>{
                            const n=document.querySelector(selector),s=getComputedStyle(n),r=n.getBoundingClientRect();
                            return [selector,{background:s.backgroundColor,color:s.color,font:s.fontFamily,
                                radius:s.borderRadius,width:Math.round(r.width),height:Math.round(r.height)}];}))""")
                        if shared is not None:self.assertEqual(observed,shared)
                        shared = observed
                        page.locator('.guest-venue-details summary').click()
                        expect(page.locator('.guest-venue-details')).to_contain_text('Fresh coffee daily')
                        expect(page.locator('.guest-venue-details')).to_contain_text('Ward café')
                        page.locator('.guest-venue-details summary').click()
                        audit=page.evaluate(LAYOUT_AUDIT)
                        self.assertFalse(audit['overflow'] or audit['clipped'] or audit['unlabeled'] or audit['small'],audit)
                        page.locator('.menu-add').first.click()
                        self.assertEqual(page.locator('.guest-modal').evaluate('(el) => getComputedStyle(el).backgroundColor'), self.rgb(theme['surface']))
                        page.locator('#itemOptions button[type=submit]').click()
                        self.assertEqual(page.locator('#openCart').evaluate('(el) => getComputedStyle(el).backgroundColor'), self.rgb(theme['primary']))
                        page.locator('#openCart').click()
                        expect(page.locator('.cart-list')).to_contain_text('لاتيه خاص')
                        expect(page.locator('.cart-total')).to_contain_text('42.75')
                        self.assert_responsive(page, width)
                        page.screenshot(path=str(self.artifacts / f'{mode}-cart-{width}-{lang}.png'))
                        if mode == 'online':
                            self.assertEqual(page.locator('#onlineCheckout [name=customer_name]').evaluate('(n)=>getComputedStyle(n).backgroundColor'),self.rgb(theme['background']))
                            self.assertEqual(page.locator('#onlineCheckout [name=fulfillment]').evaluate('(n)=>getComputedStyle(n).color'),self.rgb(theme['text']))
                    self.assertEqual(errors, [])
                    context.close()
            browser.close()
        self.venue.refresh_from_db()
        self.assertEqual(self.venue.name, 'قهوة ورد · Ward café')

    def rgb(self, color):
        return 'rgb(' + ', '.join(str(int(color[i:i + 2], 16)) for i in (1, 3, 5)) + ')'

    def test_contrast_errors_image_errors_reset_and_removal_in_both_languages(self):
        with sync_playwright() as p:
            browser = p.chromium.launch(channel='chrome', headless=True)
            for lang in ('ar', 'en'):
                context = self.context(browser, 390)
                page = context.new_page()
                path = self.live_server_url + reverse('restaurant_settings', args=[self.venue.slug])
                page.goto(path + '?lang=' + lang)
                page.locator('[data-menu-logo]').set_input_files({'name': 'initial.png', 'mimeType': 'image/png', 'buffer': self.logo})
                page.locator('#menuBrandingForm button[type=submit]').click()
                page.wait_for_url(path)
                self.color(page, 'menu_text_color', MENU_COLOR_DEFAULTS['menu_background_color'])
                self.assertFalse(page.locator('[data-menu-color="menu_text_color"]').evaluate('(el) => el.checkValidity()'))
                expect(page.locator('#menuContrastHint')).to_contain_text('too similar' if lang == 'en' else 'قريب من الخلفية')
                page.locator('#resetMenuColors').click()
                for key, value in MENU_COLOR_DEFAULTS.items():
                    expect(page.locator(f'[data-menu-color="{key}"]')).to_have_value(value)
                page.locator('[data-menu-logo]').set_input_files({'name': 'bad.svg', 'mimeType': 'image/svg+xml', 'buffer': b'<svg/>'})
                page.locator('#menuBrandingForm button[type=submit]').click()
                expect(page.locator('#menuBrandingForm')).to_contain_text('Upload a valid PNG' if lang == 'en' else 'ارفع صورة PNG')
                self.assert_responsive(page, 390)
                page.locator('[data-menu-remove-logo]').check()
                expect(page.locator('#brandingPreviewLogo')).to_be_hidden()
                expect(page.locator('#brandingPreviewFallback')).to_be_visible()
                page.locator('#menuBrandingForm button[type=submit]').click()
                page.wait_for_url(path)
                page.goto(self.live_server_url + reverse('public_menu', args=[self.table.public_token]) + '?lang=' + lang)
                expect(page.locator('.restaurant-logo')).to_have_count(0)
                expect(page.locator('.guest-brand .restaurant-avatar')).to_be_visible()
                context.close()
            browser.close()
        self.venue.refresh_from_db()
        self.assertFalse(self.venue.logo)
