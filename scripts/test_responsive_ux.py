"""Site-wide responsive checks use disposable fixtures, never the development database."""
from datetime import timedelta
import hashlib
import json
import os
from pathlib import Path
from uuid import uuid4

from django.conf import settings
from django.contrib.auth.models import User
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.test import Client
from django.urls import reverse
from django.utils import timezone
from playwright.sync_api import expect, sync_playwright

from core.guidebook import GUIDES
from core.marketing import META
from core.models import Branch, Category, DemoRequest, DiningTable, Membership, MenuItem, ModifierGroup, ModifierOption, Plan, Restaurant, StaffInvitation, Subscription
from core.online import create_online_order
from core.services import create_order, create_service_call
from core.showcase_data import SOLUTIONS, TOPICS
from core.plan_features import TIER_FEATURES, TEAM_PRESETS


LAYOUT_AUDIT = """() => {
  const visible = el => {const r=el.getBoundingClientRect(),s=getComputedStyle(el);
    const closed=el.closest('details:not([open])');
    return r.width>0 && r.height>0 && s.visibility!=='hidden' && s.display!=='none' && !el.closest('[hidden],[inert]')
      && (!closed || closed.querySelector('summary')?.contains(el));};
  const nestedScroll = el => {for(let p=el.parentElement;p && p!==document.body;p=p.parentElement)
    if(['auto','scroll'].includes(getComputedStyle(p).overflowX) && p.scrollWidth>p.clientWidth+1) return true;
    return false;};
  const controls = [...document.querySelectorAll('button,input:not([type=hidden]),select,textarea,summary')].filter(visible);
  const clipped = controls.filter(el=>{const r=el.getBoundingClientRect();
    return (r.left < -2 || r.right > innerWidth+2) && !nestedScroll(el);}).map(el=>({
    tag:el.tagName, name:el.name, text:(el.getAttribute('aria-label')||el.textContent||el.placeholder||'').trim().slice(0,90),
    x:Math.round(el.getBoundingClientRect().x),width:Math.round(el.getBoundingClientRect().width)}));
  const unlabeled = controls.filter(el=>el.matches('input:not([type=submit]):not([type=button]),select,textarea') &&
    !el.labels?.length && !el.getAttribute('aria-label') && !el.getAttribute('aria-labelledby')).map(el=>({name:el.name,placeholder:el.placeholder}));
  const small = controls.filter(el=>!el.matches('input[type=checkbox],input[type=radio]') &&
    el.getBoundingClientRect().height<39).map(el=>({tag:el.tagName,name:el.name,text:el.textContent.trim().slice(0,70),height:Math.round(el.getBoundingClientRect().height)}));
  return {overflow:document.documentElement.scrollWidth>innerWidth+1, width:innerWidth,
    contentWidth:document.documentElement.scrollWidth,clipped,unlabeled,small,h1:document.querySelectorAll('h1').length};
}"""


class ResponsiveUXTests(StaticLiveServerTestCase):
    def setUp(self):
        # Live-server classes flush migrated rows. Every test owns its public plan fixtures.
        for name,price,branches,tables,items,staff,stations in (
            ('Starter',600,1,20,100,4,1),('Growth',1300,1,60,300,8,3),('Business',2500,3,120,800,15,6)):
            Plan.objects.update_or_create(name=name,defaults={'price_monthly':price,'max_branches':branches,
                'max_tables':tables,'max_items':items,'max_staff':staff,'max_stations':stations,
                'enabled_features':list(TIER_FEATURES[name]),'staff_example':TEAM_PRESETS[name][1]})
        self.venue = Restaurant.objects.create(name='مطعم الورد للمأكولات الشرقية · Ward neighborhood kitchen', slug='ux-restaurant',
            online_ordering_enabled=True, online_delivery_enabled=True, delivery_fee='17.50')
        plan = Plan.objects.create(name='UX test', max_branches=6, max_staff=12)
        self.plan = plan
        Subscription.objects.create(restaurant=self.venue, plan=plan, status='active', ends_at=timezone.now()+timedelta(days=30))
        self.branch = Branch.objects.create(restaurant=self.venue, name='فرع وسط المدينة · Downtown branch', address='١٢ شارع النيل، بجوار الحديقة · 12 Nile Street')
        Branch.objects.create(restaurant=self.venue, name='الفرع الثاني · Second branch')
        self.table = DiningTable.objects.create(branch=self.branch, name='طاولة الحديقة الخارجية · Garden table')
        closed_table = DiningTable.objects.create(branch=self.branch, name='Closed', active=False)
        category = Category.objects.create(branch=self.branch, name='الأطباق الرئيسية والمشويات · Main dishes')
        item = MenuItem.objects.create(category=category, name='وجبة البيت مع الأرز والسلطة · House meal with rice and salad',
            description='وجبة طازجة بتتبيلة المطعم الخاصة · Freshly prepared with our house seasoning.', price='125.75')
        self.qr_order = create_order(self.table, {'idempotency_key':str(uuid4()),'lines':[{'item_id':item.pk,'quantity':2}]})
        self.online_order = create_online_order(self.branch, {'idempotency_key':str(uuid4()),
            'lines':[{'item_id':item.pk,'quantity':1}], 'fulfillment':'delivery', 'customer_name':'عميل تجريبي · Test customer',
            'customer_phone':'01012345678', 'delivery_address':'١٢ شارع النيل، عمارة ٥، الدور الثالث · 12 Nile Street',
            'reviewed_total':'143.25','reviewed_delivery_fee':'17.50'})
        create_service_call(self.table, 'waiter')
        owner = User.objects.create_user('ux-owner@example.org', first_name='مدير المطعم · Venue manager')
        Membership.objects.create(user=owner, restaurant=self.venue, role='owner')
        self.sessions = {}
        for role in ('owner','cashier','kitchen','waiter','operator'):
            user = owner if role=='owner' else (User.objects.create_superuser('ux-operator@example.org') if role=='operator'
                                                else User.objects.create_user('ux-'+role+'@example.org'))
            if role not in ('owner','operator'):
                member = Membership.objects.create(user=user, restaurant=self.venue, role=role)
                member.branches.add(self.branch)
            client = Client()
            client.force_login(user)
            self.sessions[role] = client.cookies[settings.SESSION_COOKIE_NAME].value
        self.owner = owner
        invite_token = 'ux-new-team-member'
        StaffInvitation.objects.create(restaurant=self.venue, branch=self.branch, email='invite@example.org',role='cashier',
            token_hash=hashlib.sha256(invite_token.encode()).hexdigest(), invited_by=owner, expires_at=timezone.now()+timedelta(days=7))
        pending = Restaurant.objects.create(name='مطعم بانتظار التفعيل',slug='ux-pending')
        Subscription.objects.create(restaurant=pending,status='pending')
        Branch.objects.create(restaurant=pending)
        Membership.objects.create(user=owner, restaurant=pending, role='owner')
        DemoRequest.objects.create(name='أحمد · Ahmed',venue='مطعم الواجهة · Waterfront', email='demo@example.org',language='ar',message='طلب عرض وتشغيل فرعين · Request a two-branch demo.')
        self.extra_public = [
            ('qr-menu', reverse('public_menu',args=[self.table.public_token]),200),
            ('online-menu',reverse('online_menu',args=[self.venue.slug]),200),
            ('qr-track',reverse('track_order',args=[self.qr_order.public_token]),200),
            ('online-track',reverse('track_order',args=[self.online_order.public_token]),200),
            ('table-closed',reverse('public_menu',args=[closed_table.public_token]),404),
            ('invitation',reverse('accept_staff_invitation',args=[invite_token]),200),
            ('invitation-invalid',reverse('accept_staff_invitation',args=['unknown']),410),
            ('verification-invalid',reverse('verify_owner_email',args=['unknown']),400),
            ('reset-invalid',reverse('password_reset_confirm',kwargs={'uidb64':'MA','token':'invalid'}),200),
            ('not-found','/not-a-real-page/',404),
        ]

    def routes(self):
        public = [(name,reverse(name),200) for name in [*META, 'features','solutions','pricing','demo_menu',
            'login','register','password_reset','password_reset_done','password_reset_complete','verification_pending']]
        public += [('feature-'+topic.slug,reverse('feature_detail',args=[topic.slug]),200) for topic in TOPICS]
        public += [('solution-'+solution.slug,reverse('solution_detail',args=[solution.slug]),200) for solution in SOLUTIONS]
        public += [('guide-'+guide.slug,reverse('resource_detail',args=[guide.slug]),200) for guide in GUIDES]
        public += self.extra_public
        owner = [(name,reverse(name,args=[self.venue.slug]),200) for name in (
            'dashboard','manage_menu','menu_import','manage_tables','manage_orders','staff_order','kitchen','manage_calls','reports','staff','manage_branches','restaurant_settings','resource_archive_list')]
        owner += [('order-edit',reverse('order_edit',args=[self.venue.slug,self.qr_order.pk]),200),
                  ('online-edit',reverse('order_edit',args=[self.venue.slug,self.online_order.pk]),200),
                  ('receipt',reverse('table_summary',args=[self.venue.slug,self.table.pk]),200),
                  ('activation',reverse('subscription_activation',args=['ux-pending']),200)]
        operator = [(name,reverse(name),200) for name in ('platform_dashboard','platform_accounts','platform_requests','platform_receipts','platform_activity','platform_account_create','platform_create_restaurant','platform_plan_create')]
        operator += [('platform-plans',reverse('platform_dashboard')+'?status=plans',200),
                     ('platform-people',reverse('platform_dashboard')+'?status=people',200),
                     ('platform-plan-edit',reverse('platform_plan_edit',args=[self.plan.pk]),200),
                     ('platform-account-edit',reverse('platform_account_edit',args=[self.owner.pk]),200),
                     ('admin-index',reverse('admin:index'),200),
                     ('admin-restaurants',reverse('admin:core_restaurant_changelist'),200),
                     ('admin-orders',reverse('admin:core_order_changelist'),200),
                     ('admin-users',reverse('admin:auth_user_changelist'),200),
                     ('admin-plan',reverse('admin:core_plan_change',args=[self.plan.pk]),200),
                     ('platform-restaurant-edit',reverse('platform_update_restaurant',args=[self.venue.pk]),405)]
        return [(None,public),('owner',owner),('operator',operator),
                *[(role,[('dashboard-'+role,reverse('dashboard',args=[self.venue.slug]),200)]) for role in ('cashier','kitchen','waiter')]]

    def test_page_inventory_at_phone_tablet_and_desktop_sizes(self):
        phase = os.getenv('UX_AUDIT_PHASE','after')
        output = Path('tmp/responsive-ux')/phase
        output.mkdir(parents=True,exist_ok=True)
        results,issues,errors = [],[],[]
        widths = (320,360,390,768,1024,1366,1920)
        with sync_playwright() as p:
            browser=p.chromium.launch(channel='chrome',headless=True)
            for role,routes in self.routes():
                for language in ('ar','en'):
                    context=browser.new_context(viewport={'width':1366,'height':900},reduced_motion='reduce',service_workers='block')
                    if role:
                        context.add_cookies([{'name':settings.SESSION_COOKIE_NAME,'value':self.sessions[role],'url':self.live_server_url}])
                    page=context.new_page()
                    page.on('pageerror',lambda error:errors.append(str(error)))
                    for name,path,status in routes:
                        if status==405:
                            continue
                        url=self.live_server_url+path+('&' if '?' in path else '?')+'lang='+language
                        response=page.goto(url)
                        if response.status!=status:
                            issues.append({'page':name,'lang':language,'status':response.status,'expected':status})
                            continue
                        page.evaluate('document.fonts.ready')
                        page.locator('details:not([name])').evaluate_all('nodes=>nodes.forEach(node=>{if(node.querySelector("form") && !node.closest("nav,aside,header"))node.open=true;})')
                        for width in widths:
                            page.set_viewport_size({'width':width,'height':900})
                            page.evaluate('()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)))')
                            result=page.evaluate(LAYOUT_AUDIT)
                            result.update(page=name,lang=language,role=role)
                            results.append(result)
                            if result['overflow'] or result['clipped']:
                                issues.append(result)
                            if width in (360,768,1366) and not name.startswith(('feature-','solution-','guide-')):
                                page.screenshot(path=str(output/f'{name}-{language}-{width}.png'))
                    context.close()
            browser.close()
        report={'cases':len(results),'issues':issues,'js_errors':errors,'results':results}
        (output/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps({'phase':phase,'cases':len(results),'layout_issues':len(issues),'js_errors':len(errors)}))
        if phase!='before':
            self.assertFalse(issues, json.dumps(issues[:5],ensure_ascii=False))
            self.assertFalse(errors,errors[:5])
            self.assertFalse([(row['page'],row['width'],row['unlabeled']) for row in results if row['unlabeled']])
            self.assertFalse([(row['page'],row['width'],row['small']) for row in results if row['small']])
            self.assertFalse([(row['page'],row['h1']) for row in results if row['h1']!=1])

    def context(self,browser,role=None,width=360,height=800):
        context=browser.new_context(viewport={'width':width,'height':height},reduced_motion='reduce',service_workers='block')
        if role:
            context.add_cookies([{'name':settings.SESSION_COOKIE_NAME,'value':self.sessions[role],'url':self.live_server_url}])
        return context

    def test_navigation_validation_and_settings_sections_remain_usable_after_resize(self):
        output=Path('tmp/responsive-ux/interactions')
        output.mkdir(parents=True,exist_ok=True)
        with sync_playwright() as p:
            browser=p.chromium.launch(channel='chrome',headless=True)
            for language in ('ar','en'):
                context=self.context(browser,'owner',width=900)
                page=context.new_page()
                page.goto(self.live_server_url+reverse('dashboard',args=[self.venue.slug])+'?lang='+language)
                page.locator('#menuToggle').click()
                expect(page.locator('#sidebar')).to_have_attribute('aria-modal','true')
                page.keyboard.press('Escape')
                expect(page.locator('#menuToggle')).to_be_focused()
                page.locator('#menuToggle').click()
                page.set_viewport_size({'width':1366,'height':800})
                expect(page.locator('#sidebar')).not_to_have_attribute('aria-modal','true')
                self.assertFalse(page.locator('.main-wrap').evaluate('el=>el.inert'))
                page.set_viewport_size({'width':360,'height':800})
                page.goto(self.live_server_url+reverse('manage_branches',args=[self.venue.slug])+'?lang='+language)
                name=page.get_by_label('اسم الفرع' if language=='ar' else 'Branch name',exact=True)
                page.locator('.staff-order-form button').click()
                expect(name).to_have_attribute('aria-invalid','true')
                expect(page.locator('.ux-field-error')).to_contain_text('هذا الحقل مطلوب' if language=='ar' else 'This field is required')
                name.fill('فرع جديد · New branch '+language)
                expect(name).not_to_have_attribute('aria-invalid','true')
                page.locator('.staff-order-form button').click()
                expect(page.locator('.table-card-grid')).to_contain_text('New branch '+language)
                page.goto(self.live_server_url+reverse('restaurant_settings',args=[self.venue.slug])+'?lang='+language)
                for target in ('branchProfile','menuBranding','onlineOrdering','subscriptionPlan'):
                    page.locator('.section-tabs a[href="#'+target+'"]').click()
                    expect(page.locator('.section-tabs a[href="#'+target+'"]').first).to_have_attribute('aria-current','location')
                    expect(page.locator('#'+target)).to_be_in_viewport()
                page.screenshot(path=str(output/f'settings-sections-{language}.png'))
                context.close()
                context=self.context(browser)
                page=context.new_page()
                page.goto(self.live_server_url+reverse('home')+'?lang='+language)
                page.locator('[data-nav-open]').click()
                expect(page.locator('.mk-drawer')).to_be_visible()
                page.set_viewport_size({'width':1440,'height':900})
                expect(page.locator('.mk-drawer')).not_to_be_visible()
                self.assertFalse(page.locator('body').evaluate('el=>el.classList.contains("mk-lock")'))
                page.locator('.mk-mega summary').click()
                page.keyboard.press('Escape')
                expect(page.locator('.mk-mega summary')).to_be_focused()
                context.close()
                context=self.context(browser,'operator',width=768,height=900)
                page=context.new_page()
                page.goto(self.live_server_url+reverse('platform_dashboard')+'?lang='+language)
                card=page.locator('#restaurant-'+str(self.venue.pk))
                card.locator('details').evaluate_all('nodes=>nodes.forEach(node=>node.open=true)')
                card.screenshot(path=str(output/f'platform-forms-{language}.png'))
                layout=page.evaluate(LAYOUT_AUDIT)
                self.assertFalse(layout['clipped'])
                context.close()
            browser.close()

    def test_cart_keeps_total_submit_and_close_accessible_and_explains_missing_fields(self):
        output=Path('tmp/responsive-ux/interactions')
        output.mkdir(parents=True,exist_ok=True)
        with sync_playwright() as p:
            browser=p.chromium.launch(channel='chrome',headless=True)
            for language,width,height in (('ar',360,640),('en',360,640),('ar',740,360),('en',740,360)):
                context=self.context(browser,width=width,height=height)
                page=context.new_page()
                page.goto(self.live_server_url+reverse('online_menu',args=[self.venue.slug])+'?lang='+language)
                page.locator('.menu-add').first.click()
                page.locator('#itemOptions button[type=submit]').click()
                page.locator('#openCart').click()
                form=page.locator('#onlineCheckout')
                form.locator('[name=fulfillment]').select_option('delivery')
                if height>500:
                    expect(page.locator('.cart-review-footer')).to_be_in_viewport(ratio=.8)
                page.locator('.guest-modal').evaluate('el=>el.scrollTop=el.scrollHeight')
                expect(page.locator('#closeModal')).to_be_in_viewport()
                send=page.get_by_role('button',name='Confirm and place order' if language=='en' else 'تأكيد وإرسال الطلب',exact=False)
                send.click()
                expect(form.locator('[name=customer_name]')).to_have_attribute('aria-invalid','true')
                form.locator('[name=customer_name]').fill('Test guest '+language)
                form.locator('[name=customer_phone]').fill('01012345678')
                form.locator('[name=delivery_address]').fill('12 Nile Street')
                page.locator('.cart-total').scroll_into_view_if_needed()
                dimensions=page.evaluate('''() => Object.fromEntries(['.guest-modal','.cart-review-footer','.cart-review-footer>.btn'].map(selector=>{
                    const el=document.querySelector(selector),r=el.getBoundingClientRect();
                    return [selector,{top:r.top,bottom:r.bottom,height:r.height,scrollTop:el.scrollTop,position:getComputedStyle(el).position}];
                }))''')
                (output/f'checkout-{language}-{width}-{height}.json').write_text(json.dumps(dimensions,indent=2))
                if height>500:
                    expect(send).to_be_in_viewport(ratio=.99)
                page.locator('.guest-modal').screenshot(path=str(output/f'checkout-{language}-{width}-{height}.png'))
                send.click()
                page.wait_for_url('**/track/**')
                expect(page.locator('.track-total')).to_contain_text('143,25' if language=='ar' else '143.25')
                context.close()
            browser.close()

    def test_public_catalog_search_pricing_and_billing_remain_discoverable(self):
        from scripts.test_feature_catalog import exercise_filters, exercise_pricing, exercise_billing_period

        with sync_playwright() as p:
            browser=p.chromium.launch(channel='chrome',headless=True)
            for language in ('ar','en'):
                context=self.context(browser,width=390,height=850)
                page=context.new_page()
                exercise_filters(page,self.live_server_url,language)
                exercise_pricing(page,self.live_server_url,language)
                for width in (360,1440):
                    exercise_billing_period(page,self.live_server_url,language,width)
                context.close()
            browser.close()

    def test_item_errors_and_cart_removal_keep_keyboard_focus_in_the_dialog(self):
        item=MenuItem.objects.get(category__branch=self.branch)
        group=ModifierGroup.objects.create(item=item,name='الصلصة · Sauce',min_select=1,max_select=1)
        ModifierOption.objects.create(group=group,name='ليمون · Lemon',price='2.50')
        ModifierOption.objects.create(group=group,name='حار · Hot',price='0.00')
        output=Path('tmp/responsive-ux/interactions')
        output.mkdir(parents=True,exist_ok=True)
        with sync_playwright() as p:
            browser=p.chromium.launch(channel='chrome',headless=True)
            for language in ('ar','en'):
                for width in (360,1366):
                    for role in (None,'owner'):
                        context=self.context(browser,role,width=width,height=800)
                        page=context.new_page()
                        path=reverse('staff_order',args=[self.venue.slug]) if role else reverse('public_menu',args=[self.table.public_token])
                        page.goto(self.live_server_url+path+'?lang='+language)
                        page.evaluate('''() => {
                            const button=document.createElement('button');button.id='ux-preexisting-inert';
                            button.inert=true;document.body.append(button);
                        }''')
                        add=page.locator('.menu-add,.add-item').first
                        outside=page.locator('#staffTable' if role else '#menuSearch')
                        for attempt in range(2):
                            add.click()
                            heading=page.locator('#guestModalTitle')
                            expect(heading).to_be_focused()
                            outside.evaluate('el=>el.focus()')
                            expect(heading).to_be_focused()
                            self.assertTrue(outside.evaluate('el=>Boolean(el.closest("[inert]"))'))
                            submit=page.locator('#itemOptions button[type=submit]')
                            choices=page.locator('#itemOptions input[type=checkbox]')
                            if attempt==0:
                                submit.click()
                                expect(choices.first).to_be_focused()
                                expect(page.locator('#option-error-0')).to_contain_text('اختر من 1 إلى 1' if language=='ar' else 'Choose 1 to 1')
                                choices.first.check()
                                expect(page.locator('#option-error-0')).not_to_be_visible()
                                choices.last.check()
                                submit.click()
                                expect(choices.first).to_be_focused()
                                expect(page.locator('#option-error-0')).to_be_visible()
                                self.assertFalse(page.evaluate(LAYOUT_AUDIT)['clipped'])
                                page.locator('.guest-modal').screenshot(path=str(output/f'option-error-{role or "guest"}-{language}-{width}.png'))
                                choices.last.uncheck()
                                expect(page.locator('#option-error-0')).not_to_be_visible()
                                if role and width==1366:
                                    page.set_viewport_size({'width':900,'height':800})
                                    self.assertTrue(page.locator('#sidebar').evaluate('el=>el.inert'))
                            else:
                                choices.first.check()
                            submit.click()
                            expect(page.locator('#modalBackdrop')).not_to_be_visible()
                            expect(add).to_be_focused()
                            self.assertFalse(outside.evaluate('el=>Boolean(el.closest("[inert]"))'))
                            self.assertTrue(page.locator('#ux-preexisting-inert').evaluate('el=>el.inert'))
                            if role:
                                if width==1366 and attempt==0:
                                    self.assertTrue(page.locator('#sidebar').evaluate('el=>el.inert'))
                                page.set_viewport_size({'width':width,'height':800})
                        page.locator('#openCart').click()
                        page.locator('.cart-list .remove-item').first.click()
                        expect(page.locator('.cart-list .remove-item')).to_be_focused()
                        expect(page.locator('.cart-line')).to_have_count(1)
                        page.keyboard.press('Shift+Tab')
                        self.assertTrue(page.evaluate('Boolean(document.activeElement.closest(".guest-modal"))'))
                        page.locator('.cart-list .remove-item').click()
                        expect(page.locator('#guestModalTitle')).to_be_focused()
                        page.keyboard.press('Escape')
                        expect(page.locator('#modalBackdrop')).not_to_be_visible()
                        expect(outside if not role else add).to_be_focused()
                        self.assertFalse(outside.evaluate('el=>Boolean(el.closest("[inert]"))'))
                        if role and width==360:
                            page.locator('#menuToggle').click()
                            expect(page.locator('#sidebar')).to_have_attribute('aria-modal','true')
                            page.keyboard.press('Escape')
                        context.close()
            browser.close()

    def test_order_edit_quantity_keeps_focus_and_does_not_save_until_confirmed(self):
        with sync_playwright() as p:
            browser=p.chromium.launch(channel='chrome',headless=True)
            for language in ('ar','en'):
                context=self.context(browser,'owner',width=360,height=800)
                page=context.new_page()
                page.goto(self.live_server_url+reverse('order_edit',args=[self.venue.slug,self.qr_order.pk])+'?lang='+language)
                page.locator('#openCart').click()
                quantity=page.locator('.edit-line-controls input')
                quantity.fill('3')
                page.keyboard.press('Tab')
                expect(page.locator('.cart-list .remove-item')).to_be_focused()
                expect(quantity).to_have_value('3')
                expect(page.locator('.cart-total')).to_contain_text('377.25')
                quantity.press('ArrowUp')
                expect(quantity).to_be_focused()
                expect(quantity).to_have_value('4')
                page.keyboard.press('Escape')
                expect(page.locator('#openCart')).to_be_focused()
                context.close()
            browser.close()
        self.assertEqual(self.qr_order.lines.get().quantity,2)

    def test_field_errors_follow_the_current_problem_and_skip_links_move_focus(self):
        with sync_playwright() as p:
            browser=p.chromium.launch(channel='chrome',headless=True)
            for language in ('ar','en'):
                context=self.context(browser,width=360,height=800)
                page=context.new_page()
                page.goto(self.live_server_url+reverse('contact')+'?lang='+language)
                email=page.locator('input[type=email]')
                email.evaluate('el=>el.reportValidity()')
                error=page.locator('#'+email.get_attribute('data-ux-error'))
                expect(error).to_contain_text('هذا الحقل مطلوب' if language=='ar' else 'This field is required')
                email.fill('invalid')
                expect(error).to_contain_text('بريدًا إلكترونيًا صالحًا' if language=='ar' else 'valid email address')
                email.fill('guest@example.org')
                expect(error).to_have_count(0)
                expect(email).not_to_have_attribute('aria-invalid','true')
                for name,path,target in (
                    ('online',reverse('online_menu',args=[self.venue.slug]),'#onlineContent'),
                    ('qr',reverse('public_menu',args=[self.table.public_token]),'#guest-menu'),
                    ('demo',reverse('demo_menu'),'#menu')):
                    page.goto(self.live_server_url+path+'?lang='+language)
                    page.keyboard.press('Tab')
                    expect(page.locator('.ops-skip')).to_be_focused()
                    page.keyboard.press('Enter')
                    expect(page.locator(target)).to_be_focused()
                context.close()
                context=self.context(browser,'operator',width=1366,height=900)
                page=context.new_page()
                page.goto(self.live_server_url+reverse('platform_dashboard')+'?lang='+language)
                page.keyboard.press('Tab')
                expect(page.locator('.ops-skip')).to_be_focused()
                page.keyboard.press('Enter')
                expect(page.locator('#platformMain')).to_be_focused()
                context.close()
            browser.close()

    def test_plan_service_comparison_and_operator_service_selection(self):
        output=Path('tmp/plan-services-ui')
        output.mkdir(parents=True,exist_ok=True)
        starter=Restaurant.objects.create(name='Starter service test',slug='starter-services')
        Subscription.objects.create(restaurant=starter,plan=Plan.objects.get(name='Starter'),status='active',ends_at=timezone.now()+timedelta(days=30))
        Branch.objects.create(restaurant=starter)
        Membership.objects.create(restaurant=starter,user=self.owner,role='owner')
        with sync_playwright() as p:
            browser=p.chromium.launch(channel='chrome',headless=True)
            for language in ('ar','en'):
                context=self.context(browser,width=360,height=900)
                page=context.new_page()
                for width in (320,390,768,1366,1920):
                    page.set_viewport_size({'width':width,'height':900})
                    page.goto(self.live_server_url+reverse('pricing')+'?lang='+language)
                    for tier in ('starter','growth','business'):
                        card=page.locator('#plan-'+tier)
                        expect(card.locator('[data-service]')).to_have_count(17)
                        expect(card.locator('[data-service=recommendations]')).to_have_attribute('data-included','true')
                        online=card.locator('[data-service=online_ordering]')
                        expect(online).to_have_attribute('data-included','false' if tier=='starter' else 'true')
                        if tier=='starter':expect(online.locator('s')).to_be_visible()
                        else:expect(online.locator('s')).to_have_count(0)
                        expect(card.locator('[data-service=multi_branch_reports]')).to_have_attribute('data-included','true' if tier=='business' else 'false')
                        if width in (390,1366):
                            card.screenshot(path=str(output/f'{tier}-{language}-{width}.png'),
                                style='.mk-header,.mk-skip,.whatsapp-button{display:none!important}')
                    layout=page.evaluate(LAYOUT_AUDIT)
                    self.assertFalse(layout['overflow'] or layout['clipped'])
                page.set_viewport_size({'width':360,'height':900})
                page.goto(self.live_server_url+reverse('home')+'?lang='+language)
                expect(page.locator('[data-tier=starter] [data-service=online_ordering]')).to_have_attribute('data-included','false')
                page.goto(self.live_server_url+reverse('contact')+'?plan=growth&lang='+language)
                expect(page.locator('.mk-note [data-service=online_ordering]')).to_have_attribute('data-included','true')
                context.close()
                context=self.context(browser,'owner',width=360,height=900)
                page=context.new_page()
                response=page.goto(self.live_server_url+reverse('menu_import',args=[starter.slug])+'?lang='+language)
                self.assertEqual(response.status,403)
                expect(page.locator('h1')).to_contain_text('هذه الخدمة تحتاج باقة أخرى' if language=='ar' else 'This service needs another plan')
                page.screenshot(path=str(output/f'locked-service-{language}.png'))
                page.goto(self.live_server_url+reverse('restaurant_settings',args=[starter.slug])+'?lang='+language)
                expect(page.locator('#onlineOrdering form')).to_have_count(0)
                expect(page.locator('#subscriptionPlan [data-service=online_ordering]')).to_have_attribute('data-included','false')
                self.assertFalse(page.evaluate(LAYOUT_AUDIT)['clipped'])
                context.close()
                context=self.context(browser,'operator',width=360,height=900)
                page=context.new_page()
                page.goto(self.live_server_url+reverse('platform_plan_create')+'?lang='+language)
                for name,value in {'name':'Service UI '+language,'price_monthly':'799','max_staff':'6',
                    'max_branches':'1','max_tables':'20','max_items':'100','max_stations':'2','reason':'Reviewed service selection'}.items():
                    page.locator('[name='+name+']').fill(value)
                for checkbox in page.locator('[name=enabled_features]').all():
                    checkbox.uncheck()
                for feature in ('online_ordering','advanced_reports'):
                    page.locator('[name=enabled_features][value='+feature+']').check()
                self.assertFalse(page.evaluate(LAYOUT_AUDIT)['clipped'])
                page.screenshot(path=str(output/f'operator-services-{language}.png'),full_page=True)
                page.get_by_role('button',name='حفظ الباقة' if language=='ar' else 'Save plan',exact=True).click()
                page.wait_for_url('**/platform/?status=plans')
                context.close()
            browser.close()
        for language in ('ar','en'):
            self.assertEqual(Plan.objects.get(name='Service UI '+language).enabled_features,['online_ordering','advanced_reports'])
