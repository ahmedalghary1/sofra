"""Arabic/English checks for delegated admin rejection and archived capacity."""
from datetime import timedelta
from pathlib import Path

from django.conf import settings
from django.contrib.auth.models import Permission, User
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.test import Client, override_settings
from django.urls import reverse
from django.utils import timezone
from playwright.sync_api import expect, sync_playwright

from core.models import Branch, Category, Membership, MenuItem, Plan, Restaurant, Subscription
from scripts.test_responsive_ux import LAYOUT_AUDIT


@override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
class AccessAuditBrowserTests(StaticLiveServerTestCase):
    def setUp(self):
        self.venue=Restaurant.objects.create(name='مطعم المراجعة · Audit restaurant',slug='access-audit-ui')
        Subscription.objects.create(restaurant=self.venue,plan=Plan.objects.create(name='Access UI',max_staff=2,max_items=2),
            status='active',ends_at=timezone.now()+timedelta(days=7))
        self.branch=Branch.objects.create(restaurant=self.venue)
        category=Category.objects.create(branch=self.branch,name='Meals')
        MenuItem.objects.create(category=category,name='Active item',price=12)
        MenuItem.objects.create(category=category,name='Archived item',price=17,available=False,archived=True)
        self.owner=User.objects.create_user('access-ui-owner')
        Membership.objects.create(user=self.owner,restaurant=self.venue,role='owner')
        archived=User.objects.create_user('access-ui-archived')
        Membership.objects.create(user=archived,restaurant=self.venue,role='waiter',active=False,archived=True)
        client=Client();client.force_login(self.owner)
        self.cookie=client.cookies[settings.SESSION_COOKIE_NAME].value
        self.output=Path('tmp/access-audit-ui');self.output.mkdir(parents=True,exist_ok=True)

    def layout(self,page):
        audit=page.evaluate(LAYOUT_AUDIT)
        self.assertFalse(audit['overflow'] or audit['clipped'] or audit['unlabeled'] or audit['small'],audit)

    def test_archived_capacity_is_available_in_import_and_staff_invitation_in_both_languages(self):
        errors=[]
        with sync_playwright() as p:
            browser=p.chromium.launch(channel='chrome',headless=True)
            for language in ('ar','en'):
                for width in (320,1366):
                    context=browser.new_context(viewport={'width':width,'height':900},reduced_motion='reduce')
                    context.add_cookies([{'name':settings.SESSION_COOKIE_NAME,'value':self.cookie,'url':self.live_server_url}])
                    page=context.new_page();page.on('pageerror',lambda error:errors.append(str(error)))
                    page.goto(self.live_server_url+reverse('menu_import',args=[self.venue.slug])+'?lang='+language)
                    expect(page.locator('html')).to_have_attribute('dir','rtl' if language=='ar' else 'ltr')
                    expect(page.locator('.import-file .panel-head p')).to_contain_text('1')
                    self.layout(page);page.screenshot(path=str(self.output/f'import-{language}-{width}.png'))
                    page.goto(self.live_server_url+reverse('staff',args=[self.venue.slug]))
                    form=page.locator('.staff-invite-form')
                    form.locator('[name=email]').fill(f'replacement-{language}-{width}@example.test')
                    form.locator('[name=role]').select_option('waiter')
                    with page.expect_response(lambda response:response.request.method=='POST' and '/staff/' in response.url) as saved:
                        form.locator('button').click()
                    self.assertEqual(saved.value.status,302)
                    expect(page.locator('.invitation-row')).to_have_count(1)
                    self.layout(page);page.locator('.invitation-row').scroll_into_view_if_needed()
                    page.screenshot(path=str(self.output/f'invitation-{language}-{width}.png'))
                    page.on('dialog',lambda dialog:dialog.accept())
                    page.locator('.invitation-row button').click()
                    expect(page.locator('.invitation-row')).to_have_count(0)
                    context.close()
            browser.close()
        self.assertFalse(errors,errors)

    def test_delegated_django_staff_login_shows_clear_rejection_in_arabic_and_english(self):
        user=User.objects.create_user('delegated-admin',password='UnusualPassword!147',is_staff=True)
        user.user_permissions.set(Permission.objects.all())
        with sync_playwright() as p:
            browser=p.chromium.launch(channel='chrome',headless=True)
            for language in ('ar','en'):
                context=browser.new_context(viewport={'width':390,'height':900})
                page=context.new_page();page.goto(self.live_server_url+reverse('admin:login')+'?lang='+language)
                page.locator('#id_username').fill('delegated-admin');page.locator('#id_password').fill('UnusualPassword!147')
                page.locator('input[type=submit]').click()
                expect(page.locator('.errornote')).to_contain_text('غير مخول' if language=='ar' else 'not authorized')
                expect(page.locator('html')).to_have_attribute('dir','rtl' if language=='ar' else 'ltr')
                page.screenshot(path=str(self.output/f'admin-rejected-{language}.png'))
                self.assertLessEqual(page.evaluate('document.documentElement.scrollWidth'),390)
                context.close()
            browser.close()
