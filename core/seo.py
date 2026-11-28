"""Versioned public content and search metadata for Serveza."""

from datetime import date
from urllib.parse import urlsplit

from django.conf import settings
from django.templatetags.static import static
from django.urls import reverse
from django.utils.text import Truncator

BRAND = "Serveza"
PHONE = "+201156972307"
WHATSAPP = "https://wa.me/" + PHONE.lstrip("+")
MARKET = ("مصر", "Egypt")
PRODUCT = (
    "Serveza منصة لطلبات الطاولات بالـ QR وشاشة مطبخ للكافيهات والمطاعم في مصر.",
    "Serveza is a QR table ordering and kitchen display platform for cafés and restaurants in Egypt.",
)
AVAILABLE = (
    ("منيو رقمي وطلبات من متصفح الهاتف", "Digital menus and orders from a mobile browser"),
    ("شاشة مطبخ KDS ومحطات تحضير حسب الباقة", "Kitchen display (KDS) and prep stations by plan"),
    ("استدعاء الويتر وطلب الحساب وتسجيل التحصيل يدويًا", "Waiter calls, bill requests and manual collection"),
)
UNAVAILABLE = (
    ("الدفع الإلكتروني والربط المباشر بنقاط البيع", "Online payments and direct POS integration"),
    ("الحجوزات والعروض وباقات الوجبات الآلية", "Reservations, promotions and automated meal bundles"),
)
FIRST_EDITION = date(2026, 11, 20)
SEO_EDITION = date(2026, 11, 21)
GUIDE_PUBLISHED = FIRST_EDITION
GUIDE_MODIFIED = FIRST_EDITION
PUBLIC_EDITIONS = {
    "home": SEO_EDITION, "features": FIRST_EDITION, "solutions": SEO_EDITION,
    "how_it_works": SEO_EDITION, "pricing": SEO_EDITION, "demo": FIRST_EDITION,
    "demo_menu": SEO_EDITION, "resources": FIRST_EDITION, "faq": SEO_EDITION,
    "contact": SEO_EDITION, "about": SEO_EDITION, "privacy": FIRST_EDITION, "terms": FIRST_EDITION,
}
DETAIL_PARENTS = {
    "feature_detail": ("features", "دليل المميزات", "Feature guide"),
    "solution_detail": ("solutions", "حلول المطاعم والكافيهات", "Hospitality solutions"),
    "resource_detail": ("resources", "أدلة التشغيل", "Field guides"),
    "demo_menu": ("demo", "تجربة الخدمة", "Service demo"),
}
TOPIC_SEARCH = {
    "digital-menu": (
        ("منيو QR للمطاعم والكافيهات في مصر", "QR code menu for Egyptian cafés"),
        (
            "منيو QR من Serveza يتيح للمطعم نشر الأصناف والأسعار والصور والأحجام والإضافات في منيو إلكتروني بالباركود. يفتحه الضيف من متصفح هاتفه بعد مسح رمز الطاولة. يدير المالك الأسعار والتوفر من لوحة الفرع، ويقدر يجرّب المنيو الرقمي للكافيه قبل طباعة الرموز.",
            "Serveza gives café and restaurant operators in Egypt a digital menu with prices, photos, sizes and extras. Guests scan a table QR code and open the menu in their browser. Owners update availability and prices from the branch workspace and can try the sample menu before printing codes.",
        ),
    ),
    "kitchen-display": (
        ("شاشة مطبخ للمطاعم في مصر (KDS)", "Kitchen display system (KDS) for restaurants"),
        (
            "شاشة مطبخ Serveza تعرض الطلب المؤكد مع الطاولة والأصناف والإضافات. يتابع فريق المطبخ التحضير والجاهزية، ويمكنه توزيع التذاكر على محطات مثل المشروبات والشواية حسب سعة الباقة. يقدر المدير مراجعة حالات الطلبات وتأخر التحضير من شاشة الفرع أثناء الوردية.",
            "Serveza’s kitchen display system shows confirmed orders with their table, items and extras. Kitchen staff update preparation and readiness, and can route tickets to stations such as drinks or grill within the plan limit. Managers use the branch display to check progress and spot delayed tickets during service.",
        ),
    ),
    "qr-ordering": (
        ("طلب من الطاولة بالموبايل عبر QR", "Order from the table on a phone"),
        (
            "الطلب من الطاولة في Serveza يبدأ بمسح كود QR وفتح المنيو على الموبايل. يختار الضيف الأصناف والإضافات ويرسل السلة، ثم يصل الطلب المؤكد إلى فريق المطعم مع رقم الطاولة. يتابع الضيف حالة طلبه من رابط المتابعة، ويقدر الفريق إضافة طلب من واجهة الموظف خلال الوردية.",
            "To order from a table, a guest scans its QR code, picks items and extras, and sends the basket from a mobile browser. The confirmed order arrives with its table number. The guest can follow progress through a tracking link while staff can add orders from their own workspace.",
        ),
    ),
    "waiter": (("نظام ويتر وطلب الحساب للمطاعم", "Waiter calls and bill requests"), None),
}


def language_urls(path):
    base = settings.SITE_URL + path
    return {"ar": base, "en": base + "?lang=en", "x-default": base}

def monthly_price_text(price, en):
    return (f"تبدأ باقات Serveza من {price} ج.م/شهر.",
            f"Serveza plans start at EGP {price}/month.")[en] if price else ""


def detail_date(name, slug):
    if name == "resource_detail":
        return GUIDE_MODIFIED
    if name == "feature_detail" and slug in TOPIC_SEARCH:
        return SEO_EDITION
    return FIRST_EDITION


def public_metadata(request, context):
    """Derive visible navigation and schema from the same page context."""
    route = request.resolver_match
    name = route.url_name
    if name not in PUBLIC_EDITIONS and name not in DETAIL_PARENTS:
        return context

    en = request.LANGUAGE_CODE == "en"
    language = "en" if en else "ar-EG"
    path = reverse(name, kwargs=route.kwargs)
    canonical = language_urls(path)["en" if en else "ar"]
    origin = settings.SITE_URL
    company_id, site_id = origin + "/#organization", origin + "/#website"
    plans = context.get("plans", ())
    contact = {
        "@type": "ContactPoint", "contactType": "customer support",
        "telephone": PHONE, "url": WHATSAPP, "areaServed": "EG",
        "availableLanguage": ["ar-EG", "en"],
    }
    organization = {
        "@type": "Organization", "@id": company_id, "name": BRAND, "url": origin + "/",
        "logo": origin + urlsplit(static("brand/serveza-logo.webp")).path,
        "description": PRODUCT[en], "areaServed": {"@type": "Country", "name": "Egypt"},
        "contactPoint": contact,
    }
    if settings.SERVEZA_PUBLIC_EMAIL:
        contact["email"] = settings.SERVEZA_PUBLIC_EMAIL
    if settings.SERVEZA_LEGAL_NAME:
        organization["legalName"] = settings.SERVEZA_LEGAL_NAME
    if settings.SERVEZA_SOCIAL_URLS:
        organization["sameAs"] = settings.SERVEZA_SOCIAL_URLS
    graph = [
        organization,
        {"@type": "WebSite", "@id": site_id, "name": BRAND, "url": origin + "/",
         "inLanguage": ["ar-EG", "en"], "publisher": {"@id": company_id}},
    ]
    if name in ("home", "pricing"):
        offers = [{
            "@type": "Offer", "name": plan["name"], "price": plan["price"], "priceCurrency": "EGP",
            "url": language_urls(reverse("pricing"))["en" if en else "ar"] + "#plan-" + plan["slug"],
            "priceSpecification": {
                "@type": "UnitPriceSpecification", "price": plan["price"], "priceCurrency": "EGP",
                "billingDuration": "P1M", "unitCode": "MON",
            },
        } for plan in plans]
        graph.append({
            "@type": "SoftwareApplication", "@id": origin + "/#software", "name": BRAND,
            "url": language_urls(reverse("home"))["en" if en else "ar"], "description": PRODUCT[en],
            "applicationCategory": "BusinessApplication", "operatingSystem": "Web",
            "inLanguage": ["ar-EG", "en"], "areaServed": "EG", "publisher": {"@id": company_id},
            "offers": offers, "featureList": [feature[en] for feature in AVAILABLE],
        })
    breadcrumbs = []
    if name != "home":
        breadcrumbs.append((("الرئيسية", "Home")[en], reverse("home")))
        if name in DETAIL_PARENTS:
            parent, ar, english = DETAIL_PARENTS[name]
            breadcrumbs.append(((ar, english)[en], reverse(parent)))
        suffix = "?lang=en" if en else ""
        breadcrumbs = [(label, url + suffix) for label, url in breadcrumbs]
        breadcrumbs.append((context.get("breadcrumb_title", context["page_title"]), path + suffix))
        graph.append({"@type": "BreadcrumbList", "itemListElement": [
            {"@type": "ListItem", "position": number, "name": label, "item": origin + url}
            for number, (label, url) in enumerate(breadcrumbs, 1)
        ]})
    if context.get("faq_rows"):
        graph.append({"@type": "FAQPage", "@id": canonical + "#faq", "inLanguage": language,
                      "mainEntity": [{"@type": "Question", "name": question,
                                      "acceptedAnswer": {"@type": "Answer", "text": answer}}
                                     for question, answer in context["faq_rows"]]})
    if name == "resource_detail":
        guide = context["guide"]
        article = {
            "headline": guide["title"], "description": guide["lead"], "mainEntityOfPage": canonical,
            "datePublished": GUIDE_PUBLISHED.isoformat(), "dateModified": GUIDE_MODIFIED.isoformat(),
            "inLanguage": language, "author": {"@id": company_id}, "publisher": {"@id": company_id},
        }
        graph.append({"@type": "TechArticle", **article})
        graph.append({"@type": "HowTo", "name": guide["title"], "description": guide["lead"],
                      "inLanguage": language, "step": [{"@type": "HowToStep", "text": step} for step in guide["steps"]]})
        context["guide_modified"] = GUIDE_MODIFIED
    context.update(
        seo_title=Truncator(context.get("seo_title", context["page_title"])).chars(60 - len(" | " + BRAND)),
        seo_description=Truncator(context["page_description"]).chars(155),
        breadcrumbs=breadcrumbs,
        seo_graph={"@context": "https://schema.org", "@graph": graph},
        product_description=PRODUCT[en],
        public_email=settings.SERVEZA_PUBLIC_EMAIL,
        legal_name=settings.SERVEZA_LEGAL_NAME,
        social_urls=settings.SERVEZA_SOCIAL_URLS,
        whatsapp_url=WHATSAPP,
    )
    return context
