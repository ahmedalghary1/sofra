"""Public navigation and language preferences shared with the restaurant workspace."""

from urllib.parse import urlsplit

from django.conf import settings
from django.core.exceptions import DisallowedHost
from django.http import HttpResponsePermanentRedirect, HttpResponseRedirect
from django.templatetags.static import static
from django.urls import Resolver404, resolve, reverse
from django.utils import translation
from django.utils.cache import patch_vary_headers
from django.utils.encoding import escape_uri_path

from .showcase_data import GROUPS, TOPICS
from .seo import DETAIL_PARENTS, PUBLIC_EDITIONS, language_urls

NAV = (
    ("home", "الرئيسية", "Home"),
    ("features", "المميزات", "Features"),
    ("solutions", "الحلول", "Solutions"),
    ("how_it_works", "كيف تعمل", "How it works"),
    ("pricing", "الباقات", "Pricing"),
    ("demo", "الديمو", "Demo"),
    ("resources", "الأدلة", "Guides"),
)

def canonical_host_middleware(get_response):
    def middleware(request):
        if settings.CANONICAL_HOST_REDIRECT:
            try:
                host = request.get_host().lower()
            except DisallowedHost:
                if request.path_info == '/health/' and request.method in ('GET', 'HEAD'):
                    # Keep host-independent probes available before CommonMiddleware validates Host.
                    from .views import health

                    return health(request)
                host = ''
            if request.path_info != '/health/' and host != urlsplit(settings.SITE_URL).netloc.lower():
                return HttpResponsePermanentRedirect(settings.SITE_URL + request.get_full_path())
        return get_response(request)

    return middleware


def language_middleware(get_response):
    def middleware(request):
        explicit = request.GET.get("lang")
        saved = request.COOKIES.get("serveza_language")
        if explicit not in ("ar", "en") and saved == "en":
            # The Arabic canonical must serve Arabic even after an English visit.
            try:
                route_name = resolve(request.path_info).url_name
            except Resolver404:
                route_name = None
            if route_name in PUBLIC_EDITIONS or route_name in DETAIL_PARENTS:
                saved = None
        language = (
            explicit if explicit in ("ar", "en") else ("en" if saved == "en" else "ar")
        )
        request.LANGUAGE_CODE = language
        with translation.override(language):
            if (request.method in ('GET', 'HEAD') and 'lang' in request.GET
                    and request.path.startswith(reverse('admin:index'))):
                query = request.GET.copy()
                del query['lang']
                target = request.path
                if query:
                    target += '?' + query.urlencode()
                response = HttpResponseRedirect(target)
            else:
                response = get_response(request)
        response.headers.setdefault("Content-Language", language)
        patch_vary_headers(response, ("Cookie",))
        if explicit == language and request.COOKIES.get("serveza_language") != language:
            response.set_cookie(
                "serveza_language",
                language,
                max_age=31536000,
                samesite="Lax",
                httponly=True,
                secure=settings.SESSION_COOKIE_SECURE,
            )
        return response

    return middleware


def localized_url(name, english, *args):
    path = reverse(name, args=args)
    return path + ("?lang=en" if english else "")


def presentation(request):
    lang = getattr(request, "LANGUAGE_CODE", "ar")
    english = lang == "en"
    query = request.GET.copy()
    query["lang"] = "ar" if english else "en"
    context = {
        "lang": lang,
        "english": english,
        "language_switch_url": "?" + query.urlencode(),
    }
    match = request.resolver_match
    if match and match.url_name in PUBLIC_PAGES:
        context.update(public_context(request))
    return context


PUBLIC_PAGES = {name for name, _, _ in NAV} | {
    "feature_detail",
    "solution_detail",
    "about",
    "contact",
    "resource_detail",
    "privacy",
    "terms",
    "cookies",
    "faq",
    "offline",
    "maintenance",
    "demo_menu",
}

def site_metadata(request):
    urls = language_urls(escape_uri_path(request.path))
    match = request.resolver_match
    alternates = bool(match and match.url_name in PUBLIC_PAGES)
    english = getattr(request, "LANGUAGE_CODE", "ar") == "en"
    return {
        "canonical_url": urls["en" if alternates and english else "ar"],
        "og_image": settings.SITE_URL + urlsplit(static("marketing/social.jpg")).path,
        "arabic_url": urls["ar"],
        "english_url": urls["en"],
        "default_url": urls["x-default"],
        "site_has_alternates": alternates,
    }


def public_context(request):
    english = request.LANGUAGE_CODE == "en"
    idx = int(english)
    social_profiles = []
    for url in settings.SERVEZA_SOCIAL_URLS:
        host = (urlsplit(url).hostname or '').lower().removeprefix('www.')
        icon, label = {
            'instagram.com': ('instagram', ('إنستجرام', 'Instagram')[idx]),
            'facebook.com': ('facebook', ('فيسبوك', 'Facebook')[idx]),
        }.get(host, ('globe', ('حساب Serveza', 'Serveza profile')[idx]))
        social_profiles.append({'url': url, 'icon': icon, 'label': label})
    return {
        "social_profiles": social_profiles,
        "site_nav": [(name, en if english else ar) for name, ar, en in NAV],
        "feature_groups": [
            (labels[idx], [(t.slug, t.title[idx]) for t in TOPICS if t.group == key])
            for key, labels in GROUPS.items()
        ],
    }
