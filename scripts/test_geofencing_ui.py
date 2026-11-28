"""Real guest/staff HTTP flows; geolocation is emulated and map tiles stay offline."""
from datetime import timedelta
from pathlib import Path

from django.conf import settings
from django.contrib.auth.models import User
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.test import Client
from django.urls import reverse
from django.utils import timezone
from playwright.sync_api import expect, sync_playwright

from core.models import Branch, Category, DiningTable, Membership, MenuItem, Order, Plan, Restaurant, Subscription
from scripts.test_responsive_ux import LAYOUT_AUDIT

TILE='<svg xmlns="http://www.w3.org/2000/svg" width="256" height="256"><rect width="256" height="256" fill="#e9f1eb"/><path d="M0 80H256M80 0V256M0 180H256M180 0V256" stroke="white" stroke-width="12"/><text x="20" y="140" font-size="14" fill="#52675d">Offline test map</text></svg>'


class GeofencingBrowserTests(StaticLiveServerTestCase):
    def setUp(self):
        self.restaurant=Restaurant.objects.create(name='مطعم الحديقة · Garden restaurant',slug='geofence-ui')
        Subscription.objects.create(restaurant=self.restaurant,plan=Plan.objects.create(name='Geofence UI'),
            status='active',ends_at=timezone.now()+timedelta(days=30))
        self.branch=Branch.objects.create(restaurant=self.restaurant,name='الحديقة · Garden',latitude=30,longitude=31,geofence_enabled=True)
        self.table=DiningTable.objects.create(branch=self.branch,name='12')
        self.item=MenuItem.objects.create(category=Category.objects.create(branch=self.branch,name='وجبات · Meals'),
            name='وجبة الحديقة · Garden meal',price='25.50')
        self.sessions={}
        for role in ('owner','operator'):
            user=User.objects.create_user('geofence-ui-'+role,is_superuser=role=='operator',is_staff=role=='operator')
            if role=='owner':Membership.objects.create(user=user,restaurant=self.restaurant,role='owner')
            client=Client();client.force_login(user)
            self.sessions[role]=client.cookies[settings.SESSION_COOKIE_NAME].value
        self.output=Path('tmp/geofencing-ui');self.output.mkdir(parents=True,exist_ok=True)

    def context(self,browser,*,role=None,width=390,state='inside'):
        context=browser.new_context(viewport={'width':width,'height':900},reduced_motion='reduce',
            permissions=[] if state=='denied' else ['geolocation'],
            geolocation={'latitude':30.001 if state=='outside' else 30,'longitude':31,'accuracy':100 if state=='inaccurate' else 5})
        context.route('https://tile.openstreetmap.org/**',lambda route:route.fulfill(content_type='image/svg+xml',body=TILE))
        if role:context.add_cookies([{'name':settings.SESSION_COOKIE_NAME,'value':self.sessions[role],'url':self.live_server_url}])
        context.add_init_script('''window.__locationCalls=0;const original=navigator.geolocation.getCurrentPosition.bind(navigator.geolocation);
            navigator.geolocation.getCurrentPosition=(...args)=>{window.__locationCalls++;return original(...args);};''')
        if state in ('timeout','unavailable'):
            context.add_init_script('navigator.geolocation.getCurrentPosition=(yes,no)=>{window.__locationCalls++;no({code:'+('3' if state=='timeout' else '2')+'});};')
        return context

    def visit(self,page,url,language):
        response=page.goto(self.live_server_url+url+'?lang='+language)
        self.assertEqual(response.status,200);page.set_default_timeout(15000)
        expect(page.locator('html')).to_have_attribute('dir','rtl' if language=='ar' else 'ltr')

    def checkout(self,page):
        page.locator(f'.menu-add[data-id="{self.item.pk}"]').click()
        page.locator('#itemOptions button[type=submit]').click()
        page.locator('#openCart').click()
        expect(page.locator('.cart-line')).to_have_count(1)

    def layout(self,page):
        layout=page.evaluate(LAYOUT_AUDIT)
        self.assertFalse(layout['overflow'] or layout['clipped'] or layout['unlabeled'] or layout['small'],layout)

    def test_owner_location_map_radius_errors_and_platform_controls_in_both_languages(self):
        errors=[]
        with sync_playwright() as p:
            browser=p.chromium.launch(channel='chrome',headless=True)
            for role in ('owner','operator'):
                for language in ('ar','en'):
                    for width in ((320,390,768,1366) if role=='owner' else (390,1366)):
                        context=self.context(browser,role=role,width=width);page=context.new_page();page.on('pageerror',lambda e:errors.append(str(e)))
                        url=reverse('restaurant_settings',args=[self.restaurant.slug]) if role=='owner' else reverse('platform_geofence',args=[self.restaurant.pk,self.branch.pk])
                        requests=[];page.on('request',lambda request:requests.append(request.url) if 'tile.openstreetmap.org' in request.url else None)
                        self.visit(page,url,language);self.assertEqual(requests,[])
                        panel=page.locator('#branchLocation');panel.scroll_into_view_if_needed()
                        panel.locator('[data-locate-branch]').click()
                        expect(panel.locator('[name$="-latitude"]')).to_have_value('30.000000')
                        panel.locator('[data-open-geofence-map]').click()
                        expect(panel.locator('.leaflet-marker-icon')).to_be_visible()
                        panel.locator('[data-geofence-map]').click(position={'x':100,'y':100})
                        self.assertNotEqual(panel.locator('[name$="-latitude"]').input_value(),'30.000000')
                        panel.locator('[name$="-latitude"]').fill('30.000000');panel.locator('[name$="-longitude"]').fill('31.000000')
                        panel.locator('[name$="-geofence_radius_m"]').fill('120')
                        panel.locator('[name$="-geofence_enabled"]').check()
                        if role=='operator':panel.locator('[name$="-reason"]').fill('Venue size reviewed')
                        self.layout(page);page.screenshot(path=str(self.output/f'location-map-{role}-{language}-{width}.png'))
                        self.assertEqual(panel.locator('.leaflet-tile').first.get_attribute('referrerpolicy'),'strict-origin-when-cross-origin')
                        with page.expect_response(lambda response:response.request.method=='POST' and url in response.url) as saved:
                            panel.locator('button[type=submit]').click()
                        self.assertEqual(saved.value.status,302)
                        page.wait_for_load_state();expect(page.locator('[name$="-geofence_radius_m"]')).to_have_value('120')
                        self.layout(page)
                        data=page.locator('[data-geofence-form]').evaluate('form=>Object.fromEntries(new FormData(form))')
                        data['geofence-geofence_radius_m']='9'
                        response=page.request.post(self.live_server_url+url,form=data)
                        self.assertEqual(response.status,200)
                        page.goto(self.live_server_url+url+'?lang='+language);self.layout(page)
                        context.close()
            browser.close()
        self.assertFalse(errors,errors)
        self.branch.refresh_from_db();self.assertEqual(self.branch.geofence_radius_m,120)

    def test_guest_inside_outside_denied_accuracy_timeout_and_unavailable_keep_cart(self):
        errors=[]
        with sync_playwright() as p:
            browser=p.chromium.launch(channel='chrome',headless=True)
            for language in ('ar','en'):
                for state in ('inside','outside','denied','inaccurate','timeout','unavailable'):
                    context=self.context(browser,state=state);page=context.new_page();page.on('pageerror',lambda e:errors.append(str(e)))
                    self.visit(page,reverse('public_menu',args=[self.table.public_token]),language)
                    self.assertEqual(page.evaluate('window.__locationCalls'),0)
                    self.checkout(page);page.locator('.cart-modal textarea').fill('بدون ملح · No salt')
                    page.locator('.cart-review-footer button').click()
                    if state=='inside':page.wait_for_url('**/track/**')
                    else:
                        expect(page.locator('#modalContent .form-error[role=alert]')).to_be_visible()
                        expect(page.locator('.cart-line')).to_have_count(1)
                        expect(page.locator('.cart-modal textarea')).to_have_value('بدون ملح · No salt')
                        draft=page.evaluate('JSON.stringify(sessionStorage)')
                        self.assertNotIn('latitude',draft);self.assertNotIn('challenge',draft);self.assertNotIn('timestamp',draft)
                        self.layout(page);page.screenshot(path=str(self.output/f'guest-{state}-{language}.png'))
                    context.close()
            browser.close()
        self.assertFalse(errors,errors);self.assertEqual(Order.objects.count(),2)

    def test_saved_order_lost_confirmation_recovers_with_location_permission_revoked(self):
        with sync_playwright() as p:
            browser=p.chromium.launch(channel='chrome',headless=True)
            context=self.context(browser);page=context.new_page()
            self.visit(page,reverse('public_menu',args=[self.table.public_token]),'en');self.checkout(page)
            def lose(route):route.fetch();route.abort()
            page.route('**/order/',lose,times=1)
            page.locator('.cart-review-footer button').click()
            expect(page.locator('#modalContent h2')).to_have_text('Awaiting order confirmation')
            context.clear_permissions();page.reload()
            expect(page.locator('#modalContent h2')).to_have_text('Awaiting order confirmation')
            self.assertEqual(page.evaluate('window.__locationCalls'),0)
            page.get_by_role('button',name='Check order',exact=True).click();page.wait_for_url('**/track/**')
            self.assertEqual(page.evaluate('window.__locationCalls'),0)
            context.close();browser.close()
        self.assertEqual(Order.objects.count(),1);self.assertEqual(str(Order.objects.get().total),'25.50')

    def test_unsaved_network_failure_requires_fresh_location_and_retains_pending_if_denied(self):
        with sync_playwright() as p:
            browser=p.chromium.launch(channel='chrome',headless=True)
            context=self.context(browser);page=context.new_page()
            self.visit(page,reverse('public_menu',args=[self.table.public_token]),'en');self.checkout(page)
            page.route('**/order/',lambda route:route.abort(),times=1)
            page.locator('.cart-review-footer button').click()
            expect(page.locator('#modalContent h2')).to_have_text('Awaiting order confirmation')
            context.clear_permissions();page.reload()
            page.get_by_role('button',name='Check order',exact=True).click()
            expect(page.locator('#modalContent .form-error[role=alert]')).to_contain_text('permission denied')
            expect(page.get_by_role('button',name='Check order',exact=True)).to_be_visible()
            context.grant_permissions(['geolocation']);page.reload()
            page.get_by_role('button',name='Check order',exact=True).click();page.wait_for_url('**/track/**')
            context.close();browser.close()
        self.assertEqual(Order.objects.count(),1)

    def test_disabled_branch_does_not_request_location_and_enabled_old_page_cannot_bypass(self):
        Branch.objects.filter(pk=self.branch.pk).update(geofence_enabled=False)
        with sync_playwright() as p:
            browser=p.chromium.launch(channel='chrome',headless=True)
            context=self.context(browser,state='denied',width=1366);page=context.new_page()
            admin_context=self.context(browser,role='owner');admin_page=admin_context.new_page()
            def configure(enabled):
                self.visit(admin_page,reverse('restaurant_settings',args=[self.restaurant.slug]),'en')
                form=admin_page.locator('[data-geofence-form]')
                form.locator('[name$="-geofence_enabled"]').set_checked(enabled)
                data=form.evaluate('form=>Object.fromEntries(new FormData(form))')
                response=admin_page.request.post(self.live_server_url+reverse('restaurant_settings',args=[self.restaurant.slug]),form=data)
                self.assertEqual(response.status,200)
            self.visit(page,reverse('public_menu',args=[self.table.public_token]),'en');self.checkout(page)
            configure(True)
            page.locator('.cart-review-footer button').click()
            expect(page.locator('#modalContent .form-error[role=alert]')).to_contain_text('permission denied')
            configure(False)
            page.route('**/location/',lambda route:route.fulfill(status=503,content_type='text/html',body='<h1>Unavailable</h1>'),times=1)
            page.locator('.cart-review-footer button').click()
            expect(page.locator('#modalContent .form-error[role=alert]')).to_contain_text('Could not connect')
            expect(page.locator('.cart-line')).to_have_count(1)
            page.locator('.cart-review-footer button').click();page.wait_for_url('**/track/**')
            admin_context.close();context.close();browser.close()
        self.assertEqual(Order.objects.count(),1)
