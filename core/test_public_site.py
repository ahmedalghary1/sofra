import uuid
from html.parser import HTMLParser
from unittest.mock import patch
from urllib.parse import urlsplit
from xml.etree import ElementTree

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.db import DatabaseError
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from .guidebook import GUIDES
from .models import DemoRequest


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
        self.assertEqual(self.client.get("/about/").headers["Content-Language"], "en")
        self.assertContains(self.client.get("/about/?lang=ar"), 'lang="ar" dir="rtl"')
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
        self.assertEqual(self.client.get(link).status_code, 403)
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
