from datetime import date
from urllib.parse import urlsplit

from django.conf import settings
from django.contrib.sitemaps import Sitemap
from django.urls import reverse

from .guidebook import GUIDES
from .showcase_data import SOLUTIONS, TOPICS


class PublicSitemap(Sitemap):
    def get_domain(self, site=None):
        return urlsplit(settings.SITE_URL).netloc

    def get_protocol(self, protocol=None):
        return urlsplit(settings.SITE_URL).scheme

    def items(self):
        names = (
            "home", "features", "solutions", "how_it_works", "pricing", "demo",
            "demo_menu", "resources", "faq", "contact", "about", "privacy", "terms",
        )
        pages = [(reverse(name), None) for name in names]
        # Update this date when the versioned guide, feature or solution content changes.
        content_updated = date(2026, 11, 20)
        for name, entries in (
            ("feature_detail", TOPICS), ("solution_detail", SOLUTIONS), ("resource_detail", GUIDES),
        ):
            pages.extend((reverse(name, args=[entry.slug]), content_updated) for entry in entries)
        return pages

    def location(self, item):
        return item[0]

    def lastmod(self, item):
        return item[1]
