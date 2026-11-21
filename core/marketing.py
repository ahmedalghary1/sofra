import uuid
from typing import ClassVar
from django import forms
from django.conf import settings
from django.contrib import messages
from django.contrib.sitemaps.views import sitemap
from django.db import DatabaseError
from django.http import Http404, HttpResponse, JsonResponse
from django.shortcuts import redirect, render
from django.template.loader import render_to_string
from django.urls import path, reverse
from django.views.decorators.http import require_http_methods, require_safe

from .guidebook import FAQ, FLOW, GUIDES
from .models import DemoRequest
from .presentation import localized_url, site_metadata
from .showcase_catalog import public_plans, starter_price
from .showcase_data import SOLUTIONS, TOPICS
from .sitemaps import PublicSitemap

META = {
    "home": (
        "خدمة الطاولة من المنيو للمطبخ",
        "Table service from menu to kitchen",
        "منصة طلبات الطاولات للكافيهات والمطاعم في مصر. جرّب المنيو وشاشة المطبخ من متصفحك.",
        "Discover QR ordering, kitchen tickets and floor service for cafés and restaurants in Egypt.",
    ),
    "how_it_works": (
        "كيف تعمل Serveza",
        "How Serveza works",
        "تابع رحلة طاولة كاملة من الإعداد للتحصيل.",
        "Follow one table from venue setup to collection.",
    ),
    "demo": (
        "جرّب وردية تفاعلية",
        "Interactive table demo",
        "جرّب المنيو والمطبخ والصالة في مقهى تجريبي.",
        "Run a sample café table from guest order to service in your browser.",
    ),
    "about": (
        "عن Serveza",
        "About Serveza",
        "أدوات للمطاعم المصرية تربط تعاملات الطاولة بالفريق.",
        "Table-service tools designed for independent Egyptian hospitality teams.",
    ),
    "resources": (
        "دليل تشغيل المطعم",
        "Restaurant field guides",
        "أدلة قصيرة للإعداد والطاولات والمطبخ والتحصيل.",
        "Practical guides for setup, table codes, preparation and collection.",
    ),
    "contact": (
        "احجز عرضًا توضيحيًا",
        "Request a walkthrough",
        "تواصل مع فريق Serveza بخصوص تجربة تناسب مكانك.",
        "Request a product walkthrough for the way your venue works.",
    ),
    "faq": (
        "الأسئلة الشائعة",
        "Frequently asked questions",
        "إجابات عن الاشتراك والطلبات والتشغيل.",
        "Answers on subscriptions, orders and running a service.",
    ),
    "privacy": (
        "مسودة سياسة الخصوصية",
        "Draft privacy notice",
        "ملاحظات عن البيانات التي يعالجها الموقع حاليًا.",
        "A draft description of the information processed by the service.",
    ),
    "terms": (
        "مسودة شروط الاستخدام",
        "Draft service terms",
        "معلومات أولية عن استخدام النظام والباقات.",
        "Draft information on platform use and plans.",
    ),
    "cookies": (
        "ملفات الارتباط",
        "Cookie information",
        "ملفات الجلسة واللغة وحماية النماذج المستخدمة في الموقع.",
        "Session, language and form protection cookies used by the site.",
    ),
    "offline": (
        "تعذر الاتصال",
        "Connection interrupted",
        "راجع الشبكة وحاول فتح الصفحة مجددًا.",
        "Check your connection and retry the page.",
    ),
    "maintenance": (
        "صيانة الخدمة",
        "Service maintenance",
        "العودة إلى الموقع بعد انتهاء الصيانة.",
        "Return to the site after maintenance.",
    ),
}


def page_data(request, name):
    en = request.LANGUAGE_CODE == "en"
    meta = META[name]
    return {"page_title": meta[en], "page_description": meta[2 + en], "kind": name}


def guide_data(guide, en):
    return {
        "slug": guide.slug,
        "title": guide.title[en],
        "category": guide.category[en],
        "lead": guide.lead[en],
        "steps": [s[en] for s in guide.steps],
        "feature": guide.feature,
    }


def public_page(request, name=None):
    name = name or request.resolver_match.url_name
    en = request.LANGUAGE_CODE == "en"
    context = page_data(request, name)
    if name in ("home", "faq"):
        context["faq_rows"] = [(f[en], f[2 + en]) for f in FAQ]
    if name in ("home", "how_it_works"):
        context["flow"] = [(s[0], s[1 + en], s[3 + en]) for s in FLOW]
    if name == "home":
        try:
            plans = public_plans(en)
        except DatabaseError:
            plans = []
        context["plans"] = plans
        context["starter_price"] = starter_price(plans)
        context["venue_tiles"] = [
            (s.slug, s.title[en], s.headline[en]) for s in SOLUTIONS
        ]
        context["home_topics"] = [
            (t.slug, t.title[en])
            for t in TOPICS
            if t.slug
            in (
                "tables",
                "waiter",
                "billing",
                "upselling",
                "analytics",
                "team",
                "promotions",
                "multi-branch",
            )
        ]
    if name == "resources":
        q = request.GET.get("q", "").strip()[:150]
        context["query"] = q
        context["guides"] = [
            guide_data(g, en)
            for g in GUIDES
            if q.casefold() in " ".join((*g.title, *g.category, *g.lead)).casefold()
        ]
    template = {
        "home": "home",
        "demo": "demo",
        "resources": "resources",
        "faq": "faq_page",
        "about": "about",
        "how_it_works": "how",
    }.get(name, "notice")
    path = "home.html" if name == "home" else f"marketing/{template}.html"
    return render(request, path, context)


@require_safe
def resource_detail(request, slug):
    guide = next((g for g in GUIDES if g.slug == slug), None)
    if guide is None:
        raise Http404
    en = request.LANGUAGE_CODE == "en"
    return render(
        request,
        "marketing/guide.html",
        {
            "guide": guide_data(guide, en),
            "page_title": guide.title[en],
            "page_description": guide.lead[en],
        },
    )


class ContactForm(forms.ModelForm):
    key = forms.UUIDField(widget=forms.HiddenInput)

    class Meta:
        model = DemoRequest
        fields = ("name", "venue", "email", "message")
        widgets: ClassVar = {
            "name": forms.TextInput(attrs={"autocomplete": "name"}),
            "venue": forms.TextInput(attrs={"autocomplete": "organization"}),
            "email": forms.EmailInput(attrs={"autocomplete": "email", "dir": "ltr"}),
            "message": forms.Textarea(attrs={"rows": 4}),
        }

    def __init__(self, *args, english, **kwargs):
        super().__init__(*args, **kwargs)
        for name, ar, eng in [
            ("name", "اسمك", "Your name"),
            ("venue", "اسم المكان", "Venue name"),
            ("email", "بريد التواصل", "Contact email"),
            ("message", "رسالتك (اختياري)", "Message (optional)"),
        ]:
            self.fields[name].label = eng if english else ar


@require_http_methods(["GET", "HEAD", "POST"])
def contact(request):
    en = request.LANGUAGE_CODE == "en"
    initial = {"key": uuid.uuid4()}
    selected_plan = None
    slug = request.GET.get("plan")
    if slug:
        selected_plan = next((plan for plan in public_plans(en) if plan["slug"] == slug), None)
        if selected_plan:
            name = selected_plan["name"]
            initial["message"] = f"I’d like to ask about {name}." if en else f"أرغب في الاستفسار عن باقة {name}."
    form = ContactForm(
        request.POST if request.method == "POST" else None,
        english=en,
        initial=initial,
    )
    status = 200
    if request.method == "POST" and form.is_valid():
        data = {field: form.cleaned_data[field] for field in ContactForm.Meta.fields}
        try:
            lead, _ = DemoRequest.objects.get_or_create(
                submission_key=form.cleaned_data["key"],
                defaults={**data, "language": request.LANGUAGE_CODE},
            )
        except DatabaseError:
            form.add_error(
                None,
                "We could not save the request. Please retry."
                if en
                else "تعذّر حفظ الطلب. حاول مجددًا.",
            )
            status = 503
        else:
            if all(getattr(lead, field) == value for field, value in data.items()):
                messages.success(
                    request,
                    "Your request is saved for the Serveza team."
                    if en
                    else "تم حفظ طلبك ليطّلع عليه فريق Serveza.",
                )
                return redirect(localized_url("contact", en))
            form.add_error(
                None,
                "Please reload to start a new request."
                if en
                else "أعد تحميل الصفحة لبدء طلب جديد.",
            )
            status = 409
    return render(
        request,
        "marketing/contact.html",
        {**page_data(request, "contact"), "form": form, "selected_plan": selected_plan},
        status=status,
    )


@require_safe
def plan_teaser(request):
    en = request.LANGUAGE_CODE == "en"
    rows = []
    tiers = []
    for plan in public_plans(en):
        currency = "EGP / month" if en else "ج.م / شهر"
        rows.append(f"{plan['name']} · {plan['price']} {currency}")
        tiers.append({key: plan[key] for key in ("slug", "price", "focus", "limits", "staff", "highlights")})
    return JsonResponse({"rows": rows, "configured": tiers})


@require_safe
def public_worker(request):
    response = render(
        request, "marketing/public-worker.js", content_type="text/javascript"
    )
    response["Cache-Control"] = "no-cache"
    return response


def forbidden_form(request, reason=""):
    return render(request, "403.html", {"form_expired": True}, status=403)


def server_error(request):
    lang = getattr(request, "LANGUAGE_CODE", "ar")
    # Error recovery must still render if session or message storage is unavailable.
    body = render_to_string(
        "500.html",
        {
            **site_metadata(request),
            "lang": lang,
            "english": lang == "en",
            "language_switch_url": "?lang=" + ("ar" if lang == "en" else "en"),
        },
    )
    return HttpResponse(body, status=500)


@require_safe
def robots(request):
    public_map = settings.SITE_URL + reverse("sitemap")
    return HttpResponse(
        "User-agent: *\nDisallow: /app/\nDisallow: /platform/\nDisallow: /django-admin/\nDisallow: /t/\nDisallow: /track/\nDisallow: /invitation/\nDisallow: /verify/\nDisallow: /reset/\nSitemap: "
        + public_map
        + "\n",
        content_type="text/plain",
    )


urlpatterns = [
    path("public-worker.js", public_worker, name="public_worker"),
    path("pricing/summary/", plan_teaser, name="plan_teaser"),
    path("contact/", contact, name="contact"),
    path("resources/<slug:slug>/", resource_detail, name="resource_detail"),
    path("robots.txt", robots, name="robots"),
    path("sitemap.xml", require_safe(sitemap), {"sitemaps": {"public": PublicSitemap}}, name="sitemap"),
] + [
    path(name.replace("_", "-") + "/", require_safe(public_page), name=name)
    for name in META
    if name not in ("home", "contact")
]
