"""Real guest-to-waiter service requests and an isolated browser-only menu demo."""
from datetime import timedelta
from pathlib import Path

from django.conf import settings
from django.contrib.auth.models import User
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.test import Client
from django.urls import reverse
from django.utils import timezone
from playwright.sync_api import expect, sync_playwright

from core.models import Branch, Category, DiningTable, Membership, MenuItem, Order, Plan, Restaurant, ServiceCall, Subscription


class LiveServiceUITests(StaticLiveServerTestCase):
    def setUp(self):
        self.venue=Restaurant.objects.create(name='مقهى الحديقة · Garden cafe',slug='service-ui')
        Subscription.objects.create(restaurant=self.venue,plan=Plan.objects.create(name='Service UI',max_staff=8,max_branches=2),
            status='active',ends_at=timezone.now()+timedelta(days=30))
        self.branch=Branch.objects.create(restaurant=self.venue,name='الفرع الرئيسي · Main branch')
        self.second=Branch.objects.create(restaurant=self.venue,name='Second branch')
        MenuItem.objects.create(category=Category.objects.create(branch=self.branch,name='المشروبات · Drinks'),name='شاي · Tea',price='25.75')
        self.tables=[DiningTable.objects.create(branch=self.branch,name='طاولة الحديقة · Garden table '+str(i)) for i in range(4)]
        self.sessions={}
        for key,branch in [('first',self.branch),('second',self.branch),('other-branch',self.second)]:
            user=User.objects.create_user('service-ui-'+key,first_name='ويتر · Waiter '+key)
            member=Membership.objects.create(restaurant=self.venue,user=user,role='waiter');member.branches.add(branch)
            client=Client();client.force_login(user)
            self.sessions[key]=client.cookies[settings.SESSION_COOKIE_NAME].value
        self.output=Path('tmp/service-calls-ui');self.output.mkdir(parents=True,exist_ok=True)

    def context(self,browser,role=None,width=360):
        context=browser.new_context(viewport={'width':width,'height':900},reduced_motion='reduce',service_workers='block')
        if role:context.add_cookies([{'name':settings.SESSION_COOKIE_NAME,'value':self.sessions[role],'url':self.live_server_url}])
        return context

    def visit(self,page,name,args=(),language='ar',query=''):
        page.set_default_timeout(10000)
        return page.goto(self.live_server_url+reverse(name,args=args)+'?lang='+language+query)

    def test_guest_requests_reach_both_waiters_live_without_losing_the_cart(self):
        errors=[]
        with sync_playwright() as p:
            browser=p.chromium.launch(channel='chrome',headless=True)
            for index,(language,width) in enumerate((('ar',360),('en',360),('ar',1366),('en',1366))):
                contexts=[self.context(browser,role,width) for role in (None,'first','second','other-branch')]
                guest,first,second,other=[context.new_page() for context in contexts]
                for page in (guest,first,second,other):page.on('pageerror',lambda error:errors.append(str(error)))
                table=self.tables[index]
                self.visit(first,'dashboard',[self.venue.slug],language)
                self.visit(second,'manage_calls',[self.venue.slug],language,'&queue=open')
                self.visit(other,'manage_calls',[self.venue.slug],language,'&queue=open')
                expect(first.locator('[data-service-count]')).to_have_text('0')
                expect(other.locator('[data-service-count]')).to_have_text('0')
                second.evaluate('window.servicePageKept=true')
                self.visit(guest,'public_menu',[table.public_token],language)
                guest.locator('.menu-add').first.click()
                guest.locator('#itemOptions button[type=submit]').click()
                expect(guest.locator('#cartCount')).to_have_text('1')
                original_url=guest.url
                waiter=guest.locator('[data-service-kind=waiter]')
                waiter.locator('button').click()
                expect(waiter.locator('[data-service-state]')).to_contain_text('تم إرسال الطلب' if language=='ar' else 'Request sent')
                expect(guest.locator('#cartCount')).to_have_text('1')
                self.assertEqual(guest.url,original_url)
                expect(first.locator('[data-service-count]')).to_have_text('1')
                expect(first.locator('[data-service-summary]')).to_contain_text(table.name)
                expect(second.locator('[data-call]')).to_have_count(1)
                self.assertTrue(second.evaluate('window.servicePageKept'))
                expect(other.locator('[data-call]')).to_have_count(0)
                first.locator('[data-service-inbox] a').click()
                call_id=first.locator('[data-call]').get_attribute('data-call')
                first.locator('[data-call] button').click()
                expect(second.locator('[data-call]')).to_contain_text('Waiter first')
                expect(waiter.locator('[data-service-state]')).to_contain_text('استلم فريق الخدمة' if language=='ar' else 'accepted your request')
                bill=guest.locator('[data-service-kind=bill]')
                bill.locator('button').click()
                expect(first.locator('[data-service-count]')).to_have_text('2')
                expect(second.locator('[data-call]')).to_have_count(2)
                guest.screenshot(path=str(self.output/f'guest-{language}-{width}.png'))
                second.screenshot(path=str(self.output/f'waiter-{language}-{width}.png'))
                first.locator('[data-call="'+call_id+'"] button').click()
                first.locator('[data-call="'+call_id+'"] button').click()
                expect(second.locator('[data-call]')).to_have_count(1)
                bill_id=second.locator('[data-call]').get_attribute('data-call')
                for _ in range(3):second.locator('[data-call="'+bill_id+'"] button').click()
                expect(first.locator('[data-service-count]')).to_have_text('0')
                expect(second.locator('[data-call]')).to_have_count(0)
                expect(waiter.locator('button')).to_be_enabled()
                expect(bill.locator('button')).to_be_enabled()
                expect(other.locator('[data-service-count]')).to_have_text('0')
                expect(guest.locator('#cartCount')).to_have_text('1')
                self.assertFalse(guest.evaluate('document.documentElement.scrollWidth>innerWidth'))
                for context in contexts:context.close()
            browser.close()
        self.assertFalse(errors,errors)
        self.assertEqual(ServiceCall.objects.count(),8)
        self.assertEqual(ServiceCall.objects.filter(status='done').count(),8)
        self.assertFalse(Order.objects.exists())

    def test_demo_service_preview_is_interactive_and_never_calls_a_real_waiter(self):
        requests=[]
        with sync_playwright() as p:
            browser=p.chromium.launch(channel='chrome',headless=True)
            for language in ('ar','en'):
                for width in (320,1366):
                    context=self.context(browser,width=width);page=context.new_page()
                    page.on('request',lambda request:requests.append(request.url) if '/t/' in request.url or '/app/' in request.url else None)
                    self.visit(page,'demo_menu',language=language)
                    for kind in ('waiter','bill'):
                        page.locator('[data-service-kind='+kind+'] button').click()
                        expect(page.locator('[data-demo-call='+kind+']')).to_be_visible()
                    page.locator('[data-service-panel]').screenshot(path=str(self.output/f'demo-{language}-{width}.png'))
                    self.assertFalse(page.evaluate('document.documentElement.scrollWidth>innerWidth'))
                    for kind in ('waiter','bill'):
                        for _ in range(3):page.locator('[data-demo-call='+kind+'] button').click()
                        expect(page.locator('[data-service-kind='+kind+'] button')).to_be_enabled()
                    context.close()
            browser.close()
        self.assertFalse(requests,requests)
        self.assertFalse(ServiceCall.objects.exists())

    def test_lost_confirmation_and_staff_connection_recover_without_duplicate_requests(self):
        with sync_playwright() as p:
            browser=p.chromium.launch(channel='chrome',headless=True)
            guest_context=self.context(browser);staff_context=self.context(browser,'first')
            guest=guest_context.new_page();staff=staff_context.new_page()
            self.visit(staff,'manage_calls',[self.venue.slug],'en','&queue=open')
            expect(staff.locator('[data-service-count]')).to_have_text('0')
            def unavailable(route):route.abort()
            staff.route('**/calls/feed/**',unavailable)
            expect(staff.locator('[data-service-inbox]')).to_have_class('service-inbox service-offline')
            self.visit(guest,'public_menu',[self.tables[0].public_token],'en')
            def lost(route):route.fetch();route.abort()
            guest.route('**/call/**',lost)
            guest.locator('[data-service-kind=waiter] button').click()
            expect(guest.locator('[data-service-kind=waiter] [data-service-state]')).to_contain_text('Request sent')
            expect(guest.locator('[data-service-kind=waiter] button')).to_be_disabled()
            staff.unroute('**/calls/feed/**',unavailable)
            expect(staff.locator('[data-call]')).to_have_count(1)
            expect(staff.locator('[data-service-inbox]')).not_to_have_class('service-inbox service-offline')
            guest_context.close();staff_context.close();browser.close()
        self.assertEqual(ServiceCall.objects.count(),1)
