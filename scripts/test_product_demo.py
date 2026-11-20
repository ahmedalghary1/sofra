"""Run against a local Serveza preview using Python Playwright and Chromium.

    python scripts/test_product_demo.py http://127.0.0.1:8000
"""
import argparse
from urllib.parse import urlsplit

from playwright.sync_api import expect, sync_playwright


def check_sandbox(page, language):
    lab = page.locator('[data-sandbox]')

    def act(name):
        return lab.locator(f'[data-act="{name}"]')

    def open_role(role):
        tab = lab.locator(f'[aria-controls="sandbox-{role}"]')
        tab.click()
        expect(tab).to_have_attribute('aria-selected', 'true')

    notice = lab.locator('[data-notice]')
    open_role('guest')
    act('submit').click()
    expect(notice).to_contain_text('empty' if language == 'en' else 'فارغة')
    open_role('menu')
    lab.locator('[data-milk]').select_option('oat')
    act('add').click()
    lab.locator('[data-milk]').select_option('regular')
    act('add').click()
    open_role('guest')
    expect(lab.locator('[data-lines] li')).to_have_count(2)
    expect(lab.locator('[data-total]').first).to_contain_text('175')
    act('remove').click()
    expect(lab.locator('[data-total]').first).to_contain_text('95')
    act('submit').click()
    expect(act('submit')).to_be_disabled()
    act('call').click()
    act('bill').click()
    expect(act('bill')).to_be_disabled()
    open_role('kitchen')
    expect(lab.locator('[data-ticket-lines]')).to_contain_text('Oat' if language == 'en' else 'شوفان')
    expect(act('prepare')).to_be_disabled()
    for action in ('accept', 'prepare', 'ready'):
        act(action).click()
        expect(act(action)).to_be_disabled()
    open_role('waiter')
    for action in ('ackCall', 'ackBill', 'serve'):
        act(action).click()
        expect(act(action)).to_be_disabled()
    open_role('owner')
    expect(lab.locator('[data-confirmed]')).to_have_text('1')
    expect(lab.locator('[data-confirmed-total]')).to_contain_text('95')
    expect(lab.locator('[data-events]')).to_contain_text('Served' if language == 'en' else 'تم التقديم')
    act('reset').click()
    expect(lab.locator('[data-quantity]')).to_have_text('0')
    expect(lab.locator('[aria-controls="sandbox-menu"]')).to_be_focused()
    expect(lab.locator('[data-confirmed]')).to_have_text('0')
    expect(act('accept')).to_be_disabled()


def check_home(page, language):
    tabs = page.locator('#workspace-tour [role=tab]')
    expect(tabs).to_have_count(8)
    for tab in tabs.all():
        tab.click()
        expect(page.locator('#' + tab.get_attribute('aria-controls'))).to_be_visible()
    tabs.first.focus()
    tabs.first.press('Home')
    tabs.first.press('ArrowRight' if language == 'en' else 'ArrowLeft')
    expect(tabs.nth(1)).to_be_focused()

    offer = page.locator('[data-upsell]')
    for choice, total in [('yes', '150'), ('no', '80')]:
        offer.locator('[data-offer=coffee]').click()
        expect(offer.locator('[data-offer-total]')).to_contain_text('80')
        offer.locator(f'[data-offer={choice}]').click()
        expect(offer.locator('[data-offer-total]')).to_contain_text(total)
        offer.locator('[data-offer=reset]').click()
        expect(offer.locator('[data-offer-total]')).to_contain_text('0')

    roi = page.locator('[data-roi]')
    digital = roi.locator('[data-input=digital]')
    digital.fill('101')
    expect(digital).to_have_attribute('aria-invalid', 'true')
    expect(roi.locator('[data-revenue]')).to_have_text('—')
    digital.fill('50')
    roi.locator('[data-input=bills]').fill('100')
    expect(roi.locator('[data-revenue]')).to_contain_text('700')
    expect(roi.locator('[data-contribution]')).to_contain_text('320')
    assert '-' in roi.locator('[data-contribution]').inner_text()


def check_menu_checkout(page, language):
    page.locator('.demo-add-item[data-id="4"]').click()
    page.locator('.demo-add-item[data-id="1"]').click()
    page.locator('#openCart').click()
    dialog = page.get_by_role('dialog')
    expect(dialog).to_contain_text('Chargrilled chicken' if language == 'en' else 'شيش طاووق')
    expect(dialog.locator('.cart-total')).to_contain_text('320')
    with page.expect_response(lambda res: urlsplit(res.url).path == '/demo/menu/order/') as checkout:
        dialog.get_by_role('button', name='Submit demo order' if language == 'en' else 'أرسل الطلب التجريبي', exact=False).click()
    assert checkout.value.json() == {'ok': True, 'total': '320.00'}
    expect(dialog.get_by_role('heading')).to_have_text('Demo order confirmed' if language == 'en' else 'تم تأكيد طلبك التجريبي')
    dialog.get_by_role('button', name='Back to menu' if language == 'en' else 'ارجع للمنيو').click()
    expect(dialog).to_be_hidden()
    expect(page.locator('.demo-add-item').first).to_be_focused()


def run(origin):
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        for language, width in [('ar', 390), ('en', 1440)]:
            page = browser.new_page(viewport={'width': width, 'height': 960}, reduced_motion='reduce')
            errors, failed, requests = [], [], []
            page.on('pageerror', lambda error, log=errors: log.append(str(error)))
            page.on('request', lambda req, log=requests: log.append((req.method, req.url)))
            page.on('response', lambda response, log=failed: log.append(response.url) if response.status >= 400 else None)
            for path, test in [('/demo/', check_sandbox), ('/', check_home)]:
                response = page.goto(origin + path + '?lang=' + language)
                assert response and response.ok, f'{path} failed to load'
                test(page, language)
                for width in (360, 390, 768, 1024, 1440, 1920):
                    page.set_viewport_size({'width': width, 'height': 960})
                    assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth'), (path, width)
                print(f'{language} {path} interactions and six viewport widths passed.')
            assert all(method in ('GET', 'HEAD') for method, _ in requests), requests
            requests.clear()
            page.goto(origin + '/demo/menu/?lang=' + language)
            check_menu_checkout(page, language)
            writes = [urlsplit(url).path for method, url in requests if method not in ('GET', 'HEAD')]
            assert writes == ['/demo/menu/order/'], writes
            print(f'{language} sample restaurant menu and server-confirmed demo checkout passed.')
            assert not failed, failed
            assert not errors, errors
            page.close()
        browser.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('origin', nargs='?', default='http://127.0.0.1:8000')
    origin = parser.parse_args().origin.rstrip('/')
    if urlsplit(origin).hostname not in {'localhost', '127.0.0.1', '::1'}:
        parser.error('Choose a local development server.')
    run(origin)
