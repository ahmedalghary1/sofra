"""Batch table creation, branded print exports, and protected permanent identities."""
from datetime import timedelta
from pathlib import Path
import io

from django.conf import settings
from django.contrib.auth.models import User
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.test import Client
from django.urls import reverse
from django.utils import timezone
from playwright.sync_api import expect,sync_playwright
from PIL import Image
from pypdf import PdfReader
import zxingcpp

from core.models import Branch,DiningTable,Membership,Plan,Restaurant,Subscription
from core.table_codes import stable_table_url
from scripts.test_responsive_ux import LAYOUT_AUDIT


class TableCodeBrowserTests(StaticLiveServerTestCase):
    def setUp(self):
        self.restaurant=Restaurant.objects.create(name='مطعم الحديقة · Garden restaurant',slug='table-qr-ui')
        Subscription.objects.create(restaurant=self.restaurant,plan=Plan.objects.create(name='Table QR browser',max_tables=60),
            status='active',ends_at=timezone.now()+timedelta(days=30))
        self.branch=Branch.objects.create(restaurant=self.restaurant)
        self.table=DiningTable.objects.create(branch=self.branch,name='ركن النافذة · Window')
        self.pinned=stable_table_url(self.table,self.restaurant)
        self.sessions={}
        for role in ('owner','manager'):
            user=User.objects.create_user('table-qr-'+role)
            Membership.objects.create(user=user,restaurant=self.restaurant,role=role)
            client=Client();client.force_login(user)
            self.sessions[role]=client.cookies[settings.SESSION_COOKIE_NAME].value
        self.output=Path('tmp/table-qr-ui');self.output.mkdir(parents=True,exist_ok=True)

    def context(self,browser,role='owner',width=390):
        context=browser.new_context(viewport={'width':width,'height':900},reduced_motion='reduce',accept_downloads=True)
        context.add_cookies([{'name':settings.SESSION_COOKIE_NAME,'value':self.sessions[role],'url':self.live_server_url}])
        return context

    def visit(self,page,language='ar'):
        response=page.goto(self.live_server_url+reverse('manage_tables',args=[self.restaurant.slug])+'?lang='+language)
        self.assertEqual(response.status,200)
        expect(page.locator('html')).to_have_attribute('dir','rtl' if language=='ar' else 'ltr')
        page.set_default_timeout(15000)

    def assert_layout(self,page):
        layout=page.evaluate(LAYOUT_AUDIT)
        self.assertFalse(layout['overflow'] or layout['clipped'] or layout['unlabeled'] or layout['small'],layout)

    def test_owner_creates_fifty_from_the_ui_and_downloads_the_exact_pdf_batch(self):
        with sync_playwright() as p:
            browser=p.chromium.launch(headless=True)
            context=self.context(browser);page=context.new_page();self.visit(page)
            form=page.locator('[data-table-batch]')
            form.locator('[name=count]').fill('50');form.locator('[name=start_number]').fill('1')
            expect(form.locator('[data-batch-summary]')).to_contain_text('50')
            data=form.evaluate('form=>Object.fromEntries(new FormData(form))')
            form.locator('button').click();page.wait_for_url('**?batch=*#qrDownloads')
            expect(page.locator('.table-card')).to_have_count(51)
            expect(page.locator('.qr-batch-ready')).to_contain_text('50')
            replay=page.request.post(self.live_server_url+reverse('manage_tables',args=[self.restaurant.slug]),form=data)
            self.assertTrue(replay.ok)
            page.reload();expect(page.locator('.table-card')).to_have_count(51)
            seen=[]
            page.on('response',lambda response:seen.append({'status':response.status,'type':response.headers.get('content-type'),
                'disposition':response.headers.get('content-disposition')}) if '/tables/print/' in response.url else None)
            page.on('requestfailed',lambda request:seen.append({'failure':request.failure}) if '/tables/print/' in request.url else None)
            try:
                with page.expect_download() as downloaded:
                    page.locator('.qr-batch-ready a[href*="format=pdf"]').click()
            except Exception:
                print('QR download diagnostics:',seen,'pages',len(context.pages),'current path',page.url.split('?')[0])
                raise
            try:
                downloaded.value.save_as(str(self.output/'fifty-table-batch.pdf'))
            except Exception:
                print('QR download failure:',downloaded.value.failure(),'responses:',seen)
                raise
            self.assertEqual(len(PdfReader(str(self.output/'fifty-table-batch.pdf')).pages),13)
            context.close();browser.close()
        self.assertEqual(self.branch.tables.filter(number__isnull=False).count(),50)

    def test_saved_colors_preview_error_states_and_fixed_links_in_both_languages_and_sizes(self):
        errors=[]
        with sync_playwright() as p:
            browser=p.chromium.launch(channel='chrome',headless=True)
            for language in ('ar','en'):
                for width in (320,390,768,1366):
                    context=self.context(browser,width=width);page=context.new_page();page.on('pageerror',lambda error:errors.append(str(error)))
                    self.visit(page,language)
                    for field,value in [('qr_color','#123456'),('qr_number_color','#38245c')]:
                        page.locator('[name='+field+']').evaluate('(n,v)=>{n.value=v;n.dispatchEvent(new Event("input",{bubbles:true}))}',value)
                    page.locator('#qrDesign form button').click();page.wait_for_url('**#qrDesign')
                    expect(page.locator('#qrDesign img')).to_be_visible()
                    self.assertTrue(page.locator('#qrDesign img').evaluate('(n)=>n.complete&&n.naturalWidth>0'))
                    self.assert_layout(page)
                    page.locator('#qrDesign').scroll_into_view_if_needed();page.screenshot(path=str(self.output/f'colors-{language}-{width}.png'))
                    page.locator('#qrDownloads').scroll_into_view_if_needed();page.screenshot(path=str(self.output/f'downloads-{language}-{width}.png'))
                    png=page.request.get(self.live_server_url+reverse('table_qr',args=[self.restaurant.slug,self.table.pk])).body()
                    codes=zxingcpp.read_barcodes(Image.open(io.BytesIO(png)),formats=zxingcpp.BarcodeFormat.QRCode)
                    self.assertEqual([code.text for code in codes],[self.pinned])
                    page.locator('[name=qr_color]').evaluate('n=>{n.value="#ffffff"}')
                    page.locator('#qrDesign form button').click()
                    expect(page.locator('#qrDesign .form-error')).to_be_visible()
                    self.assert_layout(page)
                    context.close()
            browser.close()
        self.assertFalse(errors,errors)
        self.table.refresh_from_db();self.assertEqual(stable_table_url(self.table,self.restaurant),self.pinned)

    def test_manager_cannot_revoke_and_owner_must_explicitly_confirm_reprinting(self):
        with sync_playwright() as p:
            browser=p.chromium.launch(channel='chrome',headless=True)
            manager=self.context(browser,'manager');page=manager.new_page();self.visit(page,'en')
            expect(page.locator('.qr-emergency')).to_have_count(0);manager.close()
            owner=self.context(browser);page=owner.new_page();self.visit(page,'en')
            emergency=page.locator('.qr-emergency');emergency.locator('summary').press('Enter')
            form=emergency.locator('form')
            form.locator('[name=confirmation_name]').fill(self.table.name)
            form.locator('[name=reason]').fill('QR code was copied outside the restaurant')
            form.locator('[name=confirm_reprint]').check();form.locator('button').click()
            expect(page.locator('.qr-emergency')).to_have_count(1)
            self.assertEqual(page.request.get(self.live_server_url+reverse('public_menu',args=[self.table.public_token])).status,404)
            owner.close();browser.close()
        old=self.table.public_token;self.table.refresh_from_db();self.assertNotEqual(self.table.public_token,old)
