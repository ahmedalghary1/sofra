from urllib.parse import urlsplit

from django.conf import settings
from django.contrib.sitemaps import Sitemap
from django.urls import reverse

from .guidebook import GUIDES
from .seo import PUBLIC_EDITIONS, detail_date, language_urls
from .showcase_data import SOLUTIONS, TOPICS


class PublicSitemap(Sitemap):
    def get_domain(self, site=None):
        return urlsplit(settings.SITE_URL).netloc

    def get_protocol(self, protocol=None):
        return urlsplit(settings.SITE_URL).scheme

    def items(self):
        pages = [(reverse(name), edition) for name, edition in PUBLIC_EDITIONS.items()]
        for name, entries in (
            ("feature_detail", TOPICS), ("solution_detail", SOLUTIONS), ("resource_detail", GUIDES),
        ):
            pages.extend((reverse(name, args=[entry.slug]), detail_date(name, entry.slug)) for entry in entries)
        return [(path, edition, english) for path, edition in pages for english in (False, True)]

    def location(self, item):
        return item[0] + ("?lang=en" if item[2] else "")

    def lastmod(self, item):
        return item[1]

    def get_urls(self, page=1, site=None, protocol=None):
        urls = super().get_urls(page, site, protocol)
        for entry in urls:
            entry["alternates"] = [
                {"lang_code": language, "location": location}
                for language, location in language_urls(entry["item"][0]).items()
            ]
        return urls
