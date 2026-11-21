"""Run with Python Playwright against a local Serveza development server."""

import argparse
from urllib.parse import urlsplit

from playwright.sync_api import expect, sync_playwright


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("origin", nargs="?", default="http://127.0.0.1:8000")
    args = parser.parse_args()
    if urlsplit(args.origin).hostname not in {"localhost", "127.0.0.1", "::1"}:
        parser.error("Provide a loopback QA server.")
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel="chrome")
        for english in (False, True):
            language = "en" if english else "ar"
            context = browser.new_context(
                viewport={"width": 1440, "height": 900}, reduced_motion="reduce"
            )
            page = context.new_page()
            errors = []
            page.on(
                "pageerror", lambda error, recorded=errors: recorded.append(str(error))
            )
            page.goto(args.origin + "/?lang=" + language)
            page.locator(".mk-mega summary").focus()
            page.keyboard.press("Enter")
            expect(page.locator(".mk-pop")).to_be_visible()
            page.keyboard.press("Escape")
            expect(page.locator(".mk-mega summary")).to_be_focused()
            page.locator(".mk-mega summary").click()
            panel = page.locator(".mk-pop").bounding_box()
            page.mouse.click(16, panel["y"] + panel["height"] + 12)
            expect(page.locator(".mk-pop")).to_be_hidden()
            page.set_viewport_size({"width": 390, "height": 844})
            page.locator("[data-nav-open]").click()
            expect(page.locator(".mk-drawer")).to_be_visible()
            page.keyboard.press("Escape")
            expect(page.locator("[data-nav-open]")).to_be_focused()
            page.locator("[data-nav-open]").click()
            page.locator('.mk-drawer a[href*="/resources/"]').click()
            page.locator("#guide-search").fill("no-matching-guide")
            page.locator(".mk-search button").click()
            expect(page.locator(".mk-guide-grid > p")).to_be_visible()
            page.locator(".mk-lang").click()
            expect(page.locator("html")).to_have_attribute(
                "lang", "ar-EG" if english else "en"
            )
            expect(page.locator("#guide-search")).to_have_value("no-matching-guide")
            page.evaluate("navigator.serviceWorker.ready")
            page.wait_for_function("Boolean(navigator.serviceWorker.controller)")
            context.set_offline(True)
            offline = page.goto(args.origin + "/about/?lang=" + language)
            assert offline.status == 503
            expect(page.locator("html")).to_have_attribute("lang", language)
            expect(page.locator("h1")).to_have_text(
                "Connection interrupted" if english else "انقطع الاتصال"
            )
            context.set_offline(False)
            page.get_by_role("link").click()
            expect(page.locator(".mk-editorial h1")).to_be_visible()
            assert not errors, errors
            context.close()
        browser.close()


if __name__ == "__main__":
    main()
