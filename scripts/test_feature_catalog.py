"""Exercise catalog discovery, detail pages and plan comparison on a local QA server."""
import argparse
from urllib.parse import urlsplit

from playwright.sync_api import expect, sync_playwright


def exercise_filters(page, origin, language):
    page.goto(f'{origin}/features/?lang={language}')
    form = page.locator('#catalog-filters')
    form.locator('[name=q]').fill('Excel')
    form.locator('[name=role]').select_option('owner')
    form.locator('[name=category]').select_option('digital-menu')
    form.locator('[name=state]').select_option('available')
    form.locator('[name=plan]').select_option('common')
    form.locator('button').click()
    expect(page.locator('.cat-counter b')).to_have_text('1')
    page.locator('.cat-matches a').first.click()
    assert urlsplit(page.url).fragment == 'publish'
    page.locator('.cat-faq summary').first.focus()
    page.keyboard.press('Enter')
    expect(page.locator('.cat-faq details[open]')).to_have_count(1)
    page.goto(f'{origin}/features/?lang={language}&q=absent-query-no-match')
    expect(page.locator('.cat-empty')).to_be_visible()
    page.locator('.cat-empty a').click()
    expect(page.locator('.cat-counter b')).to_have_text('18')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('origin', nargs='?', default='http://127.0.0.1:8000')
    parser.add_argument('--all-widths', action='store_true')
    args = parser.parse_args()
    origin = args.origin.rstrip('/')
    if urlsplit(origin).hostname not in {'localhost', '127.0.0.1', '::1'}:
        parser.error('Use a local Serveza QA server.')
    widths = (360, 390, 768, 1024, 1440, 1920) if args.all_widths else (390, 1440)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel='chrome')
        checks = 0
        problems = []
        for language in ('ar', 'en'):
            context = browser.new_context(reduced_motion='reduce')
            page = context.new_page()
            page.on('pageerror', lambda error: problems.append(str(error)))
            page.on('response', lambda response: problems.append(f'{response.status} {response.url}')
                    if response.status >= 400 else None)
            exercise_filters(page, origin, language)
            feature_urls = page.locator('.cat-result h3 a').evaluate_all('(links) => links.map(a => a.href)')
            page.goto(f'{origin}/solutions/?lang={language}')
            solution_urls = page.locator('.cat-solution-card h2 a').evaluate_all('(links) => links.map(a => a.href)')
            assert len(set(feature_urls)) == 18 and len(set(solution_urls)) == 6
            pages = [f'{origin}/{section}/?lang={language}' for section in ('features', 'solutions', 'pricing')]
            for width in widths:
                page.set_viewport_size({'width': width, 'height': 900})
                for url in pages + feature_urls + solution_urls:
                    response = page.goto(url)
                    expect(page.locator('html')).to_have_attribute('lang', 'ar-EG' if language == 'ar' else 'en')
                    assert response.ok, url
                    page.evaluate('document.fonts.ready')
                    assert page.locator('main h1').count() == 1, url
                    assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth'), (url, width)
                    checks += 1
            context.close()
        browser.close()
        assert not problems, problems
        print(f'{checks} catalog and pricing viewport checks passed, including bilingual filters and keyboard FAQ operation.')


if __name__ == '__main__':
    main()
