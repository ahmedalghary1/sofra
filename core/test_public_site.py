import uuid
import json
import re
from datetime import date
from html import unescape
from html.parser import HTMLParser
from unittest.mock import patch
from urllib.parse import urlsplit
from urllib.robotparser import RobotFileParser
from xml.etree import ElementTree

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.db import DatabaseError
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from .guidebook import GUIDES
from .models import DemoRequest, Plan
from .seo import PRODUCT
from .showcase_data import SOLUTIONS, TOPICS


class Links(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links = set()
        self.anchors = set()

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "a" and attrs.get("href"):
            self.links.add(attrs["href"])
        if attrs.get("id"):
            self.anchors.add(attrs["id"])


class PublicSiteTests(TestCase):
    def test_whatsapp_button_on_public_guest_and_admin_shells(self):
        for path in ("/", "/contact/", "/pricing/", "/login/", "/demo/menu/", "/django-admin/login/"):
            for language, label in (("ar", "تواصل مع Serveza على واتساب"),
                                    ("en", "Chat with Serveza on WhatsApp")):
                with self.subTest(path=path, language=language):
                    page = self.client.get(path, {"lang": language}, follow=True)
                    self.assertContains(page, 'class="whatsapp-button" href="https://wa.me/201156972307"', count=1)
                    self.assertContains(page, f'aria-label="{label}"')
                    self.assertContains(page, 'rel="noopener noreferrer"')

    @override_settings(SITE_URL="https://servezaflow.com", CANONICAL_HOST_REDIRECT=False,
                       ALLOWED_HOSTS=["upstream.example"])
    def test_canonical_and_social_urls_use_site_origin_in_both_languages(self):
        client = Client(HTTP_HOST="upstream.example")
        for path in ("/", "/features/", "/features/digital-menu/", "/solutions/", "/pricing/",
                     "/about/", "/privacy/", "/terms/", "/demo/menu/"):
            base = "https://servezaflow.com" + path
            for language, direction in (("ar", "rtl"), ("en", "ltr")):
                with self.subTest(path=path, language=language):
                    page = client.get(path, {"lang": language, "q": "example"})
                    canonical = base + ("?lang=en" if language == "en" else "")
                    locale = "en" if language == "en" else "ar-EG"
                    self.assertContains(page, f'lang="{locale}" dir="{direction}"')
                    self.assertContains(page, f'<link rel="canonical" href="{canonical}">', count=1)
                    self.assertContains(page, f'<meta property="og:url" content="{canonical}">')
                    self.assertContains(page, '<meta property="og:image" content="https://servezaflow.com/static/marketing/social.jpg">')
                    for lang, suffix in (("ar", ""), ("en", "?lang=en"), ("x-default", "")):
                        self.assertContains(page, f'hreflang="{lang}" href="{base}{suffix}"')
                    self.assertNotContains(page, 'name="robots" content="noindex')
        login = client.get("/login/?lang=en")
        self.assertContains(login, '<link rel="canonical" href="https://servezaflow.com/login/">')
        self.assertContains(login, 'name="robots" content="noindex, nofollow"')
        self.assertNotContains(login, 'rel="alternate"')

    @override_settings(SITE_URL="https://servezaflow.com", CANONICAL_HOST_REDIRECT=False,
                       ALLOWED_HOSTS=["upstream.example"])
    def test_sitemap_and_robots_use_canonical_origin_and_public_paths_only(self):
        client = Client(HTTP_HOST="upstream.example")
        robots = client.get("/robots.txt").content.decode().splitlines()
        self.assertEqual(robots[-1], "Sitemap: https://servezaflow.com/sitemap.xml")
        excluded = ("/app/", "/platform/", "/django-admin/", "/t/", "/track/", "/invitation/",
                    "/verify/", "/reset/", "/login/", "/register/", "/password-reset/")
        parser = RobotFileParser()
        parser.parse(robots)
        agents = ("GPTBot", "OAI-SearchBot", "ChatGPT-User", "ClaudeBot", "Claude-SearchBot",
                  "PerplexityBot", "Google-Extended", "Applebot-Extended", "CCBot")
        for agent in (*agents, "UnknownBot"):
            self.assertTrue(parser.can_fetch(agent, "https://servezaflow.com/pricing/"))
            for route in excluded:
                self.assertFalse(parser.can_fetch(agent, "https://servezaflow.com" + route))
        for agent in agents:
            self.assertIn(f"User-agent: {agent}", robots)
        sitemap = client.get("/sitemap.xml")
        self.assertEqual(sitemap.status_code, 200)
        root = ElementTree.fromstring(sitemap.content)
        locations = [node.text for node in root.findall(".//{*}loc")]
        paths = {"/", "/features/", "/solutions/", "/how-it-works/", "/pricing/", "/demo/",
                 "/demo/menu/", "/resources/", "/faq/", "/contact/", "/about/", "/privacy/", "/terms/"}
        for route, entries in (("feature_detail", TOPICS), ("solution_detail", SOLUTIONS), ("resource_detail", GUIDES)):
            paths.update(reverse(route, args=[entry.slug]) for entry in entries)
        expected = ["https://servezaflow.com" + path + suffix for path in paths for suffix in ("", "?lang=en")]
        self.assertCountEqual(locations, expected)
        for node in root.findall(".//{*}url"):
            location = node.findtext("{*}loc")
            base = location.partition("?")[0]
            self.assertFalse(urlsplit(location).path.startswith(excluded))
            self.assertIsInstance(date.fromisoformat(node.findtext("{*}lastmod")), date)
            links = node.findall("{http://www.w3.org/1999/xhtml}link")
            self.assertEqual({link.get("hreflang"): link.get("href") for link in links},
                             {"ar": base, "en": base + "?lang=en", "x-default": base})

    @override_settings(SERVEZA_ALLOW_CCBOT=False)
    def test_ccbot_can_be_blocked_independently(self):
        parser = RobotFileParser()
        parser.parse(self.client.get("/robots.txt").content.decode().splitlines())
        self.assertFalse(parser.can_fetch("CCBot", "/pricing/"))
        self.assertTrue(parser.can_fetch("OAI-SearchBot", "/pricing/"))

    def test_public_pages_have_unique_metadata_and_valid_visible_schema(self):
        root = ElementTree.fromstring(self.client.get("/sitemap.xml").content)
        titles, descriptions = set(), set()
        for node in root.findall(".//{*}loc"):
            url = urlsplit(node.text)
            path = url.path + ("?" + url.query if url.query else "")
            with self.subTest(path=path):
                response = self.client.get(path, {"lang": "ar"} if not url.query else {})
                self.assertEqual(response.status_code, 200)
                source = response.content.decode()
                title = unescape(re.search(r"<title>(.*?)</title>", source).group(1))
                description = unescape(re.search(r'<meta name="description" content="([^"]+)"', source).group(1))
                self.assertNotIn(title, titles)
                self.assertNotIn(description, descriptions)
                titles.add(title)
                descriptions.add(description)
                self.assertLessEqual(len(title), 60)
                self.assertLessEqual(len(description), 155)
                self.assertEqual(len(re.findall(r"<h1(?:\s|>)", source)), 1)
                self.assertIn(f'<link rel="canonical" href="{node.text}">', source)
                schema = json.loads(re.search(r'<script type="application/ld\+json">(.*?)</script>', source).group(1))
                self.assertEqual(schema["@context"], "https://schema.org")
                graph = {item["@type"]: item for item in schema["@graph"]}
                self.assertIn("Organization", graph)
                if url.path != "/":
                    self.assertEqual(graph["BreadcrumbList"]["itemListElement"][-1]["item"], node.text)
                if "FAQPage" in graph:
                    decoded = unescape(source)
                    for question in graph["FAQPage"]["mainEntity"]:
                        self.assertIn("<summary>" + question["name"], decoded)
                        self.assertIn("<p>" + question["acceptedAnswer"]["text"] + "</p>", decoded)
                if "TechArticle" in graph:
                    article = graph["TechArticle"]
                    self.assertIn(f'datetime="{article["dateModified"]}"', source)
                    self.assertTrue(graph["HowTo"]["step"])
                self.assertFalse({"AggregateRating", "Review"} & graph.keys())

    def test_llms_faq_pricing_and_schema_follow_live_public_plan(self):
        Plan.objects.filter(name="Starter").update(price_monthly="645.50", max_tables=23)
        llms = self.client.get("/llms.txt?lang=en")
        self.assertContains(llms, PRODUCT[1])
        self.assertContains(llms, "Starter: Price 645.5 EGP/month")
        self.assertContains(llms, "Tables per branch: 23")
        self.assertContains(llms, "facebook.com/profile.php?id=61596269777742")
        pricing = self.client.get("/pricing/?lang=en").content.decode()
        self.assertIn("start at EGP 645.5/month", pricing)
        self.assertIn('"billingDuration": "P1M"', pricing)
        self.assertIn('"price": "645.5"', pricing)
        self.assertContains(self.client.get("/faq/?lang=en"), "start at EGP 645.5/month")
        for route in ("/?lang=en", "/about/?lang=en"):
            self.assertContains(self.client.get(route), "Serveza plans start at EGP 645.5/month.")

    @override_settings(SITE_URL="https://servezaflow.com", CANONICAL_HOST_REDIRECT=True,
                       ALLOWED_HOSTS=["servezaflow.com", "www.servezaflow.com", "upstream.example"],
                       SECURE_SSL_REDIRECT=True)
    def test_production_redirects_to_canonical_host_and_exempts_health(self):
        for host in ("www.servezaflow.com", "upstream.example", "unlisted.example"):
            with self.subTest(host=host):
                for secure in (False, True):
                    response = self.client.get("/features/?lang=en&q=a%2Bb", HTTP_HOST=host, secure=secure)
                    self.assertEqual(response.status_code, 301)
                    self.assertEqual(response.url, "https://servezaflow.com/features/?lang=en&q=a%2Bb")
                health = self.client.get("/health/", HTTP_HOST=host)
                self.assertEqual(health.status_code, 200)
                self.assertEqual(health.json(), {"status": "ok"})
        path = "/features/"
        response = self.client.get(path, HTTP_HOST="servezaflow.com")
        self.assertEqual(response.status_code, 301)
        self.assertEqual(response.url, settings.SITE_URL + path)
        self.assertEqual(self.client.get(path, HTTP_HOST="servezaflow.com", secure=True).status_code, 200)

    def test_sitemap_and_public_navigation_destinations(self):
        sitemap = self.client.get(reverse("sitemap"))
        urls = ElementTree.fromstring(sitemap.content).findall(".//{*}loc")
        destinations = {urlsplit(loc.text).path for loc in urls}
        destinations.update(
            reverse(name)
            for name in (
                "terms",
                "privacy",
                "cookies",
                "maintenance",
                "offline",
                "demo_menu",
            )
        )
        pages = {}
        internal = set()
        for location in sorted(destinations):
            response = self.client.get(location)
            self.assertEqual(response.status_code, 200, location)
            document = Links()
            document.feed(response.content.decode())
            pages[location] = document
            for href in document.links:
                parsed = urlsplit(href)
                if not parsed.netloc and not parsed.scheme:
                    target = parsed.path or location
                    internal.add((target, parsed.fragment))
        for target, fragment in internal:
            if target not in pages:
                self.assertIn(self.client.get(target).status_code, (200, 302), target)
            elif fragment:
                self.assertIn(fragment, pages[target].anchors, target + "#" + fragment)
        self.assertNotIn("/app/", sitemap.content.decode())
        self.assertContains(self.client.get("/robots.txt"), "Disallow: /track/")

    def test_locale_switch_persists_and_keeps_filters(self):
        page = self.client.get("/resources/", {"lang": "en", "q": "QR"})
        self.assertContains(page, 'lang="en" dir="ltr"')
        self.assertContains(page, "lang=ar&amp;q=QR")
        self.assertEqual(self.client.get("/login/").headers["Content-Language"], "en")
        self.assertEqual(self.client.get("/about/").headers["Content-Language"], "ar")
        self.assertEqual(self.client.get("/about/?lang=en").headers["Content-Language"], "en")
        self.assertContains(self.client.get("/about/?lang=ar"), 'lang="ar-EG" dir="rtl"')
        self.assertEqual(self.client.get("/login/").headers["Content-Language"], "ar")

    def test_guides_and_empty_state(self):
        for guide in GUIDES:
            response = self.client.get(
                reverse("resource_detail", args=[guide.slug]), {"lang": "en"}
            )
            self.assertContains(response, guide.title[1])
        self.assertContains(
            self.client.get("/resources/?q=zzzzzz&lang=en"), "No matching guides"
        )
        self.assertEqual(self.client.get("/resources/unknown/").status_code, 404)

    def test_contact_validation_storage_retry_and_conflict(self):
        payload = {
            "key": str(uuid.uuid4()),
            "name": "Venue owner",
            "venue": "Test café",
            "email": "venue@example.org",
            "message": "A walkthrough, please.",
        }
        invalid = self.client.post("/contact/?lang=en", {**payload, "email": "invalid"})
        self.assertTrue(invalid.context["form"].has_error("email"))
        self.assertEqual(DemoRequest.objects.count(), 0)
        result = self.client.post("/contact/?lang=en", payload, follow=True)
        self.assertContains(result, "Your request is saved")
        self.assertEqual(DemoRequest.objects.get().language, "en")
        self.assertEqual(self.client.post("/contact/", payload).status_code, 302)
        self.assertEqual(DemoRequest.objects.count(), 1)
        self.assertEqual(
            self.client.post(
                "/contact/", {**payload, "message": "Different request"}
            ).status_code,
            409,
        )
        self.assertEqual(DemoRequest.objects.get().message, payload["message"])
        self.assertEqual(
            Client(enforce_csrf_checks=True).post("/contact/", payload).status_code, 403
        )
        with patch.object(
            DemoRequest.objects, "get_or_create", side_effect=DatabaseError
        ):
            error = self.client.post("/contact/?lang=en", payload)
        self.assertContains(error, "We could not save", status_code=503)

    def test_only_platform_superusers_can_view_leads(self):
        User = get_user_model()
        staff = User.objects.create_user(username="floor", is_staff=True)
        staff.user_permissions.add(Permission.objects.get(codename="view_demorequest"))
        link = reverse("admin:core_demorequest_changelist")
        self.client.force_login(staff)
        response = self.client.get(link)
        # The site-wide superuser gate now rejects delegated staff before
        # individual model permissions run, using Django's login redirect.
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.url.startswith(reverse('admin:login')))
        admin = User.objects.create_superuser(username="ops", email="ops@example.org")
        self.client.force_login(admin)
        self.assertEqual(self.client.get(link).status_code, 200)
        self.assertEqual(
            self.client.get(reverse("admin:core_demorequest_add")).status_code, 403
        )

    @override_settings(DEBUG=False)
    def test_recovery_templates_without_debug_output(self):
        self.assertContains(
            self.client.get("/unknown/?lang=en"), "404", status_code=404
        )
        self.assertContains(
            self.client.get("/offline/?lang=en"), "Connection interrupted"
        )
        with patch("core.marketing.public_plans", side_effect=DatabaseError):
            failed = Client(raise_request_exception=False).get(
                "/pricing/summary/?lang=en"
            )
        self.assertContains(
            failed, "We couldn’t complete your request", status_code=500
        )
        self.assertContains(failed, 'lang="en" dir="ltr"', status_code=500)
