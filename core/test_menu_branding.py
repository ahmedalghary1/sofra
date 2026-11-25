"""Restaurant branding, image handling, tenant isolation and historic data."""
import io
import tempfile
from datetime import timedelta
from pathlib import Path
from uuid import uuid4

from PIL import Image
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, SimpleTestCase, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .branding import MENU_COLOR_DEFAULTS, contrast, menu_theme, validate_menu_color
from .models import AuditLog, Branch, Category, DiningTable, Membership, MenuItem, Plan, Restaurant, Subscription
from .services import create_order


def logo_upload(*, size=(1024, 256), image_format='PNG', suffix=b''):
    output = io.BytesIO()
    Image.new('RGBA', size, (70, 30, 150, 128)).save(output, image_format)
    return SimpleUploadedFile('restaurant.html', output.getvalue() + suffix, content_type='image/png')


class MenuThemeTests(SimpleTestCase):
    def test_derived_colors_keep_text_and_button_labels_readable(self):
        for primary in ('#ffffff', '#000000', '#ffcc00', '#b33379'):
            for background, text in (('#ffffff', '#111111'), ('#121212', '#fafafa'), ('#f7e4cf', '#311f13')):
                theme = menu_theme({'menu_primary_color': primary, 'menu_background_color': background, 'menu_text_color': text})
                self.assertEqual(theme['primary'], primary)
                for surface in ('background', 'surface'):
                    self.assertGreaterEqual(contrast(theme['text'], theme[surface]), 4.5)
                    self.assertGreaterEqual(contrast(theme['muted'], theme[surface]), 4.5)
                self.assertGreaterEqual(contrast(theme['primary'], theme['on_primary']), 4.5)

    def test_invalid_persisted_colors_fall_back_without_css_injection(self):
        defaults = menu_theme(MENU_COLOR_DEFAULTS)
        for value in ('red', '#123', '#ffffff;}body{display:none}', None):
            with self.subTest(value=value):
                with self.assertRaises(ValidationError):
                    validate_menu_color(value)
                self.assertEqual(menu_theme({**MENU_COLOR_DEFAULTS, 'menu_text_color': value}), defaults)
        self.assertEqual(menu_theme({**MENU_COLOR_DEFAULTS, 'menu_text_color': '#f8f8f4'}), defaults)


class MenuBrandingTests(TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix='serveza-branding-')
        self.addCleanup(directory.cleanup)
        self.media = Path(directory.name)
        override = override_settings(MEDIA_ROOT=self.media)
        override.enable()
        self.addCleanup(override.disable)
        self.plan = Plan.objects.create(name='Branding plan', max_branches=3, max_staff=10)
        self.venue = Restaurant.objects.create(name='مطعم الألوان', slug='brand-cafe', description='Original description')
        self.other = Restaurant.objects.create(name='Another venue', slug='another-brand')
        for venue in (self.venue, self.other):
            Subscription.objects.create(restaurant=venue, plan=self.plan, status='active', ends_at=timezone.now() + timedelta(days=30))
        self.branch = Branch.objects.create(restaurant=self.venue, name='Original branch')
        self.second_branch = Branch.objects.create(restaurant=self.venue, name='Second branch')
        self.table = DiningTable.objects.create(branch=self.branch, name='A1')
        self.second_table = DiningTable.objects.create(branch=self.second_branch, name='A2')
        self.other_table = DiningTable.objects.create(branch=Branch.objects.create(restaurant=self.other), name='B1')
        self.item = MenuItem.objects.create(category=Category.objects.create(branch=self.branch, name='قهوة'), name='قهوة خاصة', price='42.75')
        self.owner = User.objects.create_user('branding-owner')
        Membership.objects.create(user=self.owner, restaurant=self.venue, role='owner')
        self.client.force_login(self.owner)
        self.endpoint = reverse('restaurant_settings', args=[self.venue.slug])

    def payload(self, **values):
        return {'action': 'branding', **{f'branding-{key}': value for key, value in {**MENU_COLOR_DEFAULTS, **values}.items()}}

    def test_logo_and_colors_publish_to_all_own_branches_with_audit(self):
        marker = b'<script>malicious()</script>'
        response = self.client.post(self.endpoint, self.payload(logo=logo_upload(suffix=marker), menu_primary_color='#B33379'))
        self.assertEqual(response.status_code, 302)
        self.venue.refresh_from_db()
        self.assertEqual(self.venue.menu_primary_color, '#b33379')
        self.assertTrue(self.venue.logo.name.startswith('restaurant-logos/'))
        self.assertTrue(self.venue.logo.name.endswith('.png'))
        self.assertNotIn('restaurant.html', self.venue.logo.name)
        with self.venue.logo.open('rb') as saved:
            self.assertNotIn(marker, saved.read())
        with Image.open(self.venue.logo.path) as saved:
            self.assertEqual(saved.size, (512, 128))
            self.assertEqual(saved.getpixel((0, 0))[3], 128)
        event = AuditLog.objects.get(restaurant=self.venue, action='restaurant.branding')
        self.assertEqual(event.actor, self.owner)
        self.assertEqual(event.changes['before']['logo'], '')
        self.assertEqual(event.changes['after']['logo'], self.venue.logo.name)
        for table in (self.table, self.second_table):
            page = self.client.get(reverse('public_menu', args=[table.public_token]))
            self.assertContains(page, self.venue.logo.url)
            self.assertContains(page, '--menu-primary:#b33379')
        other = self.client.get(reverse('public_menu', args=[self.other_table.public_token]))
        self.assertNotContains(other, self.venue.logo.url)
        self.assertContains(other, '--menu-primary:#1d6150')

    def test_branding_preserves_profile_items_prices_and_order_snapshots(self):
        order = create_order(self.table, {'idempotency_key': str(uuid4()), 'lines': [{'item_id': self.item.pk, 'quantity': 2}]})
        lines = list(order.lines.values())
        history = list(order.history.values())
        self.assertEqual(self.client.post(self.endpoint, self.payload(menu_background_color='#121212', menu_text_color='#fafafa')).status_code, 302)
        self.venue.refresh_from_db(); self.branch.refresh_from_db(); self.item.refresh_from_db(); order.refresh_from_db()
        self.assertEqual((self.venue.name, self.venue.description, self.branch.name), ('مطعم الألوان', 'Original description', 'Original branch'))
        self.assertEqual((self.item.name, str(self.item.price), str(order.total)), ('قهوة خاصة', '42.75', '85.50'))
        self.assertEqual(list(order.lines.values()), lines)
        self.assertEqual(list(order.history.values()), history)

    def test_profile_form_does_not_reset_saved_branding(self):
        self.client.post(self.endpoint, self.payload(logo=logo_upload(), menu_primary_color='#a13372'))
        self.venue.refresh_from_db()
        logo = self.venue.logo.name
        response = self.client.post(self.endpoint, {'name': self.venue.name, 'branch_name': self.branch.name, 'description': 'Updated profile'})
        self.assertEqual(response.status_code, 302)
        self.venue.refresh_from_db()
        self.assertEqual((self.venue.menu_primary_color, self.venue.logo.name), ('#a13372', logo))

    def test_replacement_removal_and_default_colors_preserve_other_resources(self):
        self.client.post(self.endpoint, self.payload(logo=logo_upload()))
        self.venue.refresh_from_db()
        original = self.venue.logo.name
        self.assertEqual(self.client.post(self.endpoint, self.payload(logo=logo_upload())).status_code, 302)
        self.venue.refresh_from_db()
        self.assertNotEqual(self.venue.logo.name, original)
        self.assertEqual(self.client.post(self.endpoint, self.payload(remove_logo='on')).status_code, 302)
        self.venue.refresh_from_db()
        self.assertFalse(self.venue.logo)
        self.assertTrue(self.venue.logo.storage.exists(original))
        self.assertEqual(self.item.name, 'قهوة خاصة')
        page = self.client.get(reverse('public_menu', args=[self.table.public_token]))
        self.assertNotContains(page, 'class="restaurant-logo"')
        self.assertContains(page, 'class="restaurant-avatar"')

    def test_invalid_colors_and_low_contrast_are_rejected_without_writes(self):
        for values in ({'menu_primary_color': '#fff'}, {'menu_background_color': 'red'},
                       {'menu_text_color': '#123456;}</style><script>alert(1)</script>'},
                       {'menu_text_color': '#f8f8f4'}, {'menu_primary_color': ''}):
            with self.subTest(values=values):
                response = self.client.post(self.endpoint, self.payload(**values, logo=logo_upload()))
                self.assertEqual(response.status_code, 200)
                self.assertTrue(response.context['branding_form'].errors)
        self.venue.refresh_from_db()
        self.assertFalse(self.venue.logo)
        self.assertFalse(AuditLog.objects.filter(action='restaurant.branding').exists())
        self.assertEqual(list(self.media.rglob('*.png')), [])

    def test_fake_unsupported_oversized_and_overdimensioned_logos_are_rejected(self):
        uploads = [SimpleUploadedFile('logo.png', b'<svg onload="bad()"></svg>', 'image/png'),
                   logo_upload(size=(2, 2), image_format='GIF'), logo_upload(size=(5001, 1)),
                   logo_upload(suffix=b'x' * 3_000_000)]
        for upload in uploads:
            response = self.client.post(self.endpoint, self.payload(logo=upload))
            self.assertEqual(response.status_code, 200)
            self.assertIn('logo', response.context['branding_form'].errors)
        self.venue.refresh_from_db()
        self.assertFalse(self.venue.logo)

    def test_ambiguous_logo_action_does_not_remove_current_logo(self):
        self.client.post(self.endpoint, self.payload(logo=logo_upload()))
        self.venue.refresh_from_db()
        original = self.venue.logo.name
        response = self.client.post(self.endpoint, self.payload(logo=logo_upload(), remove_logo='on'))
        self.assertEqual(response.status_code, 200)
        self.assertIn('logo', response.context['branding_form'].errors)
        self.venue.refresh_from_db()
        self.assertEqual(self.venue.logo.name, original)

    def test_only_own_owner_and_manager_can_edit(self):
        foreign = reverse('restaurant_settings', args=[self.other.slug])
        self.assertEqual(self.client.post(foreign, self.payload()).status_code, 403)
        worker = User.objects.create_user('branding-worker')
        membership = Membership.objects.create(user=worker, restaurant=self.venue, role='manager')
        self.client.force_login(worker)
        self.assertEqual(self.client.post(self.endpoint, self.payload(menu_primary_color='#223355')).status_code, 302)
        for role in ('cashier', 'waiter', 'kitchen'):
            membership.role = role
            membership.save(update_fields=['role'])
            self.assertEqual(self.client.post(self.endpoint, self.payload()).status_code, 403)
        self.venue.refresh_from_db()
        self.assertEqual(self.venue.menu_primary_color, '#223355')
        self.other.refresh_from_db()
        self.assertEqual(self.other.menu_primary_color, MENU_COLOR_DEFAULTS['menu_primary_color'])

    def test_public_guests_and_csrf_forgery_cannot_change_branding(self):
        self.client.logout()
        self.assertRedirects(self.client.post(self.endpoint, self.payload()), reverse('login'), fetch_redirect_response=False)
        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.force_login(self.owner)
        self.assertEqual(csrf_client.post(self.endpoint, self.payload()).status_code, 403)

    def test_bilingual_form_and_validation_messages(self):
        for lang, direction, label, invalid in (
                ('ar', 'rtl', 'هوية المنيو', 'لون النص قريب من الخلفية'),
                ('en', 'ltr', 'Menu branding', 'Text and background are too similar')):
            page = self.client.get(self.endpoint, {'lang': lang})
            self.assertContains(page, f'dir="{direction}"')
            self.assertContains(page, label)
            page = self.client.post(self.endpoint + '?lang=' + lang, self.payload(menu_text_color='#f8f8f4'))
            self.assertContains(page, invalid)
            page = self.client.post(self.endpoint + '?lang=' + lang, self.payload(logo=SimpleUploadedFile('bad.png', b'bad')))
            self.assertContains(page, 'Upload a valid PNG' if lang == 'en' else 'ارفع صورة PNG')
