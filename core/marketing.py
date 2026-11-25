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
from .security import consume_limits, limited_response
from .presentation import localized_url, site_metadata
from .seo import AVAILABLE, MARKET, PRODUCT, UNAVAILABLE, WHATSAPP, monthly_price_text, public_metadata
from .showcase_catalog import LIMITS, PRICING_NOTE, public_plans, starter_price
from .showcase_data import SOLUTIONS
from .sitemaps import PublicSitemap

META = {
    "home": (
        "نظام طلبات الطاولات للمطاعم في مصر",
        "Restaurant table ordering in Egypt",
        "نظام طلبات الطاولات للمطاعم والكافيهات في مصر. استقبل طلبات الهاتف على شاشة المطبخ وتابع استدعاءات الويتر والحساب في Serveza.",
        "Run table ordering in your Egyptian café or restaurant. Send mobile orders to the kitchen display and follow floor service and bill requests.",
    ),
    "how_it_works": (
        "كيف تعمل Serveza",
        "How Serveza works",
        "شوف خطوات تشغيل نظام طلبات الطاولات من إعداد المنيو ورمز الطاولة، لحد وصول الطلب للمطبخ وتسجيل التحصيل.",
        "Follow the table service workflow from setting up menus and codes to kitchen preparation, service and manual collection.",
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

def faq_content(en, plans):
    price = starter_price(plans)
    rows = [(entry[en], entry[2 + en]) for entry in FAQ]
    if price:
        rows.insert(0, (("كام سعر منيو QR للمطاعم في مصر؟", "How much does a QR menu cost in Egypt?")[en],
                        monthly_price_text(price, en) + " " +
                        ("تشمل الباقات منيو QR ونظام طلبات الطاولات. قارن حدود الفريق والفروع والمحطات في صفحة الباقات.",
                         "Plans include a QR menu and table ordering. Compare team, branch and station limits on the pricing page.")[en]))
    rows.extend([
        (("إيه الفرق بين منيو QR ونظام طلبات الطاولات؟", "How do QR menus differ from table ordering?")[en],
         ("منيو QR يعرض الأصناف والأسعار. نظام طلبات الطاولات يرسل سلة الضيف للمطعم مع رقم الطاولة وحالة الطلب.",
          "A QR menu displays items and prices. A table ordering system sends the guest’s basket to the venue with the table number and a status to track.")[en]),
    ])
    return rows


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
    if name in ("home", "faq", "about"):
        try:
            plans = public_plans(en)
        except DatabaseError:
            plans = []
        context["monthly_price_text"] = monthly_price_text(starter_price(plans), en)
        if name in ("home", "faq"):
            rows = faq_content(en, plans)
            context["faq_rows"] = rows[:5] if name == "home" else rows
    if name in ("home", "how_it_works"):
        context["flow"] = [(s[0], s[1 + en], s[3 + en]) for s in FLOW]
    if name == "home":
        context["plans"] = plans
        context["starter_price"] = starter_price(plans)
        context["venue_tiles"] = [
            (s.slug, s.title[en], s.headline[en]) for s in SOLUTIONS
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
    return render(request, path, public_metadata(request, context))


@require_safe
def resource_detail(request, slug):
    guide = next((g for g in GUIDES if g.slug == slug), None)
    if guide is None:
        raise Http404
    en = request.LANGUAGE_CODE == "en"
    return render(
        request,
        "marketing/guide.html",
        public_metadata(request, {
            "guide": guide_data(guide, en),
            "page_title": guide.title[en],
            "page_description": guide.lead[en],
        }),
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
    billing = request.GET.get("billing")
    annual = billing == "annual"
    initial = {"key": uuid.uuid4()}
    selected_plan = None
    slug = request.GET.get("plan")
    if slug:
        selected_plan = next((plan for plan in public_plans(en) if plan["slug"] == slug), None)
        if selected_plan:
            name = selected_plan["name"]
            if annual:
                initial["message"] = (
                    f"I’d like to ask about the annual {name} plan."
                    if en else f"أرغب في الاستفسار عن باقة {name} السنوية."
                )
            elif billing == "monthly":
                initial["message"] = (
                    f"I’d like to ask about the monthly {name} plan."
                    if en else f"أرغب في الاستفسار عن باقة {name} الشهرية."
                )
            else:
                initial["message"] = f"I’d like to ask about {name}." if en else f"أرغب في الاستفسار عن باقة {name}."
    form = ContactForm(
        request.POST if request.method == "POST" else None,
        english=en,
        initial=initial,
    )
    status = 200
    if request.method == "POST" and form.is_valid():
        if (not DemoRequest.objects.filter(submission_key=form.cleaned_data['key']).exists()
                and not consume_limits(request, 'contact', limit=10, seconds=3600,
                                       identity=form.cleaned_data['email'])):
            return limited_response(request, retry_after=3600)
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
        public_metadata(request, {
            **page_data(request, "contact"), "form": form, "selected_plan": selected_plan, "annual": annual,
            "pricing_note": PRICING_NOTE[en],
        }),
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
    agents = ["GPTBot", "OAI-SearchBot", "ChatGPT-User", "ClaudeBot", "Claude-SearchBot",
              "PerplexityBot", "Google-Extended", "Applebot-Extended"]
    if settings.SERVEZA_ALLOW_CCBOT:
        agents.append("CCBot")
    private = ("/app/", "/platform/", "/django-admin/", "/t/", "/track/", "/invitation/",
               "/verify/", "/reset/", "/login/", "/register/", "/password-reset/")
    rules = "\n".join(f"Disallow: {route}" for route in private)
    group = "\n".join(f"User-agent: {agent}" for agent in agents)
    content = f"User-agent: *\n{rules}\n\n{group}\n{rules}\nAllow: /\n"
    if not settings.SERVEZA_ALLOW_CCBOT:
        content += "\nUser-agent: CCBot\nDisallow: /\n"
    return HttpResponse(content + f"\nSitemap: {settings.SITE_URL}{reverse('sitemap')}\n",
                        content_type="text/plain; charset=utf-8")


@require_safe
def llms(request):
    plans = public_plans(True)
    origin = settings.SITE_URL
    en = request.LANGUAGE_CODE == "en"
    lines = ["# Serveza", "", "> " + PRODUCT[en], "",
             f"- {('السوق', 'Market')[en]}: {MARKET[en]}",
             f"- WhatsApp: {WHATSAPP}"]
    if settings.SERVEZA_PUBLIC_EMAIL:
        lines.append(f"- {('البريد العام', 'Public email')[en]}: {settings.SERVEZA_PUBLIC_EMAIL}")
    if settings.SERVEZA_LEGAL_NAME:
        lines.append(f"- {('الاسم القانوني', 'Legal name')[en]}: {settings.SERVEZA_LEGAL_NAME}")
    lines.extend(f"- {('الحساب الرسمي', 'Official profile')[en]}: {url}" for url in settings.SERVEZA_SOCIAL_URLS)
    lines.extend(["", ("## الباقات الشهرية", "## Monthly plans")[en]])
    if starting_price := monthly_price_text(starter_price(plans), en):
        lines.extend(["", starting_price, ""])
    for plan in plans:
        currency = "EGP/month" if en else "ج.م/شهر"
        label = "Price" if en else "السعر"
        limits = "; ".join(f"{names[en]}: {plan['limits'][key]}" for key, names in LIMITS.items())
        lines.append(f"- {plan['name']}: {label} {plan['price']} {currency}; {limits}.")
    for heading, features in ((("المتاح", "Available"), AVAILABLE), (("غير متاح حاليًا", "Not currently available"), UNAVAILABLE)):
        lines.extend(["", "## " + heading[en], *(f"- {feature[en]}" for feature in features)])
    paths = (
        ("home", (), "الرئيسية", "Home", "نظام طلبات الطاولات", "Table ordering platform"),
        ("feature_detail", ("digital-menu",), "منيو QR", "QR menu", "المنيو الرقمي للمطاعم", "Digital menus for restaurants"),
        ("feature_detail", ("kitchen-display",), "شاشة المطبخ", "Kitchen display", "تنظيم تذاكر التحضير", "Kitchen tickets and stations"),
        ("pricing", (), "الباقات", "Pricing", "الأسعار الشهرية والحدود", "Monthly pricing and limits"),
        ("resources", (), "أدلة التشغيل", "Field guides", "خطوات الإعداد وإدارة الوردية", "Setup and service guides"),
        ("faq", (), "الأسئلة الشائعة", "FAQ", "إجابات عن التشغيل والدفع", "Service and payment answers"),
        ("about", (), "عن Serveza", "About Serveza", "المنصة والسوق المصري", "Platform and Egyptian market"),
        ("contact", (), "تواصل", "Contact", "التواصل مع فريق Serveza", "Contact the Serveza team"),
    )
    lines.extend(["", ("## صفحات مهمة", "## Key pages")[en]])
    lines.extend(f"- [{(ar, english)[en]}]({origin}{localized_url(name, en, *args)}): {(desc_ar, desc_en)[en]}"
                 for name, args, ar, english, desc_ar, desc_en in paths)
    return HttpResponse("\n".join(lines) + "\n", content_type="text/plain; charset=utf-8")


urlpatterns = [
    path("public-worker.js", public_worker, name="public_worker"),
    path("pricing/summary/", plan_teaser, name="plan_teaser"),
    path("contact/", contact, name="contact"),
    path("resources/<slug:slug>/", resource_detail, name="resource_detail"),
    path("robots.txt", robots, name="robots"),
    path("sitemap.xml", require_safe(sitemap), {"sitemaps": {"public": PublicSitemap},
                                               "template_name": "marketing/sitemap.xml"}, name="sitemap"),
    path("llms.txt", llms, name="llms"),
] + [
    path(name.replace("_", "-") + "/", require_safe(public_page), name=name)
    for name in META
    if name not in ("home", "contact")
]
