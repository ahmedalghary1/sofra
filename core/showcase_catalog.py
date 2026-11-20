"""Read-only feature discovery, venue solutions and public plan comparisons."""

from decimal import Decimal

from django.db.models.functions import Lower
from django.http import Http404
from django.shortcuts import render
from django.urls import path
from django.views.decorators.http import require_safe

from .models import Plan
from .showcase_data import FAQS, GROUPS, ROLES, SOLUTIONS, STATES, TOPICS

PUBLIC_TIERS = (
    ('Starter', ('Starter', 'ستارتر', 'البداية'), 600, (1, 20, 150, 5, 1)),
    ('Growth', ('Growth', 'النمو'), 1300, (1, 60, 500, 20, 4)),
    ('Business', ('Business', 'الأعمال'), 2500, (3, 120, 1500, 60, 8)),
)
LIMITS = {
    'max_branches': ('فروع لكل مطعم', 'Branches per venue'),
    'max_tables': ('طاولات لكل فرع', 'Tables per branch'),
    'max_items': ('أصناف لكل فرع', 'Menu items per branch'),
    'max_staff': ('إجمالي حسابات الفريق للمطعم', 'Total team accounts per venue'),
    'max_stations': ('محطات تحضير KDS لكل فرع', 'KDS prep stations per branch'),
}
STAFF_ROLES = {
    'waiter': ('الويتر', 'Waiters'), 'admin': ('الإدارة، شاملة المالك', 'Admins, including the owner'),
    'kitchen': ('المطبخ', 'Kitchen staff'), 'cashier': ('الكاشير', 'Cashiers'),
}
PLAN_FOCUS = {
    'Starter': ('للخدمة اليومية من أول مسح للكود حتى طلب الحساب.',
                'Take a table from its first QR scan to the bill request.'),
    'Growth': ('وزّع التحضير بين المحطات، وتابع جاهزية كل صنف وقت الزحمة.',
               'Split prep between stations and track each item through a busy shift.'),
    'Business': ('كل أدوات التشغيل، مع منيو ومطبخ وتقارير مستقلة لكل فرع.',
                 'All operating tools, with a separate menu, kitchen and reports for each branch.'),
}
HIGHLIGHTS = {
    'Starter': (
        ('digital-menu', 'منيو بالصور والأحجام والإضافات', 'Photo menu with sizes and extras'),
        ('qr-ordering', 'رمز QR لكل طاولة وطلب مباشر من الموبايل', 'Table QR codes and mobile ordering'),
        ('kitchen-display', 'شاشة مطبخ KDS بتذاكر الطلبات', 'Kitchen display (KDS) with order tickets'),
        ('waiter', 'استدعاء الويتر وطلب الحساب من الطاولة', 'Waiter calls and bill requests at the table'),
    ),
    'Growth': (
        ('kitchen-display', 'تذاكر حسب محطة التحضير ومتابعة جاهزية الأصناف', 'Station tickets and item readiness'),
        ('notifications', 'تنبيهات لتأخر التحضير واستجابة الويتر', 'Late prep and service alerts'),
        ('billing', 'طباعة ملخص الطاولة وتسجيل التحصيل', 'Print table summaries and record collections'),
        ('analytics', 'الأصناف الأكثر طلبًا وتقرير طلبات CSV', 'Best-selling items and CSV order reports'),
    ),
    'Business': (
        ('multi-branch', 'منيو وأسعار وطاولات مستقلة لكل فرع', 'Separate menus, prices and tables for each branch'),
        ('team', 'أدوار للموظفين وتحديد الفروع المتاحة لكل حساب', 'Staff roles and branch access per account'),
        ('kitchen-display', 'توزيع التذاكر على محطات المطبخ ومتابعة الجاهزية', 'Route kitchen tickets and track readiness'),
        ('analytics', 'تقارير كل فرع عن الطلبات والتحصيل وزمن الخدمة', 'Branch reports on orders, collections and service times'),
    ),
}
# These topics contain planned modules as well as smaller live functions.
INCLUDED_TITLES = {
    'upselling': ('الأحجام والإضافات المدفوعة', 'Paid sizes and extras'),
    'promotions': ('تحديث أسعار المنيو', 'Menu price updates'),
    'customer-experience': ('مساعدة الضيف من الطاولة', 'Guest assistance at the table'),
    'multi-branch': ('إدارة عدة فروع', 'Multi-branch management'),
}
ROADMAP_PREVIEWS = {'promotions', 'reservations', 'upselling'}
TOPIC_BY_SLUG = {topic.slug: topic for topic in TOPICS}
SOLUTION_BY_SLUG = {solution.slug: solution for solution in SOLUTIONS}


def _staff_example(total):
    """Illustrate the shared membership allowance, reserving a floor seat when possible."""
    admins = (total + 9) // 10
    back_of_house = max(0, total - admins - 1)
    kitchen = min((total + 4) // 5, back_of_house)
    cashiers = min((total + 9) // 10, back_of_house - kitchen)
    return {'admin': admins, 'kitchen': kitchen, 'cashier': cashiers,
            'waiter': total - admins - kitchen - cashiers}


def public_plans(english=False, configured=True):
    """Expose only named public tiers. Never publish arbitrary negotiated plan records."""
    names = {alias.casefold() for _, aliases, _, _ in PUBLIC_TIERS for alias in aliases}
    records = {}
    if configured:
        rows = Plan.objects.annotate(public_name=Lower('name')).filter(public_name__in=names).order_by('pk')
        for row in rows:
            records.setdefault(row.name.casefold(), row)
    plans = []
    for name, aliases, proposed_price, proposed_limits in PUBLIC_TIERS:
        row = next((records[alias.casefold()] for alias in aliases if alias.casefold() in records), None)
        price = row.price_monthly if row else Decimal(proposed_price)
        limits = {key: getattr(row, key) for key in LIMITS} if row else dict(zip(LIMITS, proposed_limits))
        # Keep cents when configured. Avoid scientific notation for integer prices.
        price_text = format(price, '.2f').rstrip('0').rstrip('.')
        plan = {
            'name': name, 'slug': name.lower(), 'price': price_text, 'configured': row is not None,
            'proposed_price': proposed_price, 'price_differs': price != proposed_price,
            'limits': limits, 'rows': [(label[english], limits[key]) for key, label in LIMITS.items()],
        }
        focus = PLAN_FOCUS[name]
        if name == 'Business' and limits['max_branches'] <= 1:
            focus = ('تابع الفريق والمطبخ وتقارير التشغيل في مساحة واحدة.', 'Manage staff, kitchen and reporting in one workspace.')
        if name == 'Growth' and limits['max_stations'] <= 1:
            focus = ('تابع طلبات الصالة والتحصيل في الوردية.', 'Track floor orders and collections throughout the shift.')
        plan['focus'] = focus[english]
        plan['staff'] = _staff_example(limits['max_staff'])
        plan['staff_rows'] = [(label[english], plan['staff'][role]) for role, label in STAFF_ROLES.items()]
        plan['highlights'] = [(ar, en)[english] for slug, ar, en in HIGHLIGHTS[name]
                              if _fits_plan(TOPIC_BY_SLUG[slug], plan)]
        plans.append(plan)
    return plans


def _language(request):
    return request.LANGUAGE_CODE == 'en'


def _page(request, template, title, description, **context):
    en = _language(request)
    return render(request, f'marketing/catalog/{template}.html', {
        'lang': 'en' if en else 'ar', 'english': en, 'page_title': title, 'page_description': description,
        **context,
    })


def _topic(topic, en):
    return {
        'slug': topic.slug, 'group': topic.group, 'group_name': GROUPS[topic.group][en],
        'title': topic.title[en], 'headline': topic.headline[en], 'summary': topic.summary[en],
        'steps': [s[en] for s in topic.steps], 'roles': [ROLES[role][en] for role in topic.roles.split()],
        'sample': [(row[en], row[2 + en]) for row in topic.sample], 'preview': topic.preview,
        'concept': topic.slug in ROADMAP_PREVIEWS,
        'items': [{'anchor': key, 'status': state, 'label': STATES[state][en], 'text': (ar, eng)[en]}
                  for key, state, ar, eng in topic.capabilities],
    }


def _solution(solution, en):
    return {
        'slug': solution.slug, 'title': solution.title[en], 'headline': solution.headline[en],
        'story': solution.story[en], 'scenario': [s[en] for s in solution.scenario],
        'topics': [_topic(TOPIC_BY_SLUG[slug], en) for slug in solution.topics],
    }


def _options(options, en):
    return [(key, labels[en]) for key, labels in options.items()]


def _fits_plan(topic, plan):
    # Only the resources defined by Plan are entitlements. No feature flags exist.
    if topic.slug == 'multi-branch':
        return plan['limits']['max_branches'] > 1
    if topic.slug == 'kitchen-display':
        return plan['limits']['max_stations'] > 0
    return True


@require_safe
def features(request):
    en = _language(request)
    query = request.GET.get('q', '').strip()[:150]
    filters = {key: request.GET.get(key, '')[:60] for key in ('category', 'role', 'state', 'plan')}
    plans = [p for p in public_plans(en) if p['configured']]
    selected = next((p for p in plans if p['slug'] == filters['plan']), None)
    entries = []
    for topic in TOPICS:
        if filters['category'] and filters['category'] != topic.slug:
            continue
        if filters['role'] and filters['role'] not in topic.roles.split():
            continue
        if filters['plan'] == 'common' and topic.slug == 'multi-branch':
            continue
        if filters['plan'] not in ('', 'common') and (selected is None or not _fits_plan(topic, selected)):
            continue
        entry = _topic(topic, en)
        topic_text = ' '.join((*topic.title, *topic.headline, *topic.summary, topic.slug)).casefold()
        matches = []
        for item, capability in zip(entry['items'], topic.capabilities):
            if filters['state'] and filters['state'] != item['status']:
                continue
            if filters['plan'] and item['status'] != 'available':
                continue
            if query.casefold() in topic_text or query.casefold() in ' '.join(capability[2:]).casefold():
                matches.append(item)
        if matches:
            entry['items'] = matches
            entries.append(entry)
    sections = [{'id': key, 'name': labels[en], 'topics': [e for e in entries if e['group'] == key]}
                for key, labels in GROUPS.items()]
    return _page(request, 'features', ('دليل المميزات', 'Feature guide')[en],
                 ('ابحث في أدوات خدمة المطاعم وحالة كل ميزة.', 'Explore restaurant tools and the status of each capability.')[en],
                 entries=entries, categories=[(t.slug, t.title[en]) for t in TOPICS],
                 roles=_options(ROLES, en), states=_options(STATES, en), plans=plans,
                 sections=[s for s in sections if s['topics']], query=query, filters=filters,
                 hero_preview=_topic(TOPIC_BY_SLUG['kitchen-display'], en))


@require_safe
def feature_detail(request, slug):
    topic = TOPIC_BY_SLUG.get(slug)
    if topic is None:
        raise Http404
    en = _language(request)
    entry = _topic(topic, en)
    related = sorted((t for t in TOPICS if t != topic), key=lambda t: t.group != topic.group)[:3]
    return _page(request, 'feature', entry['title'], entry['summary'], entry=entry,
                 faq_question=FAQS[slug][0][en], faq_answer=FAQS[slug][1][en],
                 related=[_topic(t, en) for t in related],
                 fit=[p for p in public_plans(en) if p['configured'] and _fits_plan(topic, p)])


@require_safe
def pricing(request):
    en = _language(request)
    plans = public_plans(en)
    rows = [(label[en], [p['limits'][key] for p in plans]) for key, label in LIMITS.items()]
    for plan in plans:
        plan['included'] = []
    tools = []
    for topic in TOPICS:
        items = [(ar, eng)[en] for _, status, ar, eng in topic.capabilities if status == 'available']
        if not items:
            continue
        group = {'slug': topic.slug, 'title': INCLUDED_TITLES.get(topic.slug, topic.title)[en], 'items': items}
        availability = [_fits_plan(topic, plan) for plan in plans]
        for plan, available in zip(plans, availability):
            if available:
                plan['included'].append(group)
        tools.append((group, availability))
    staff_rows = [(label[en], [p['staff'][key] for p in plans]) for key, label in STAFF_ROLES.items()]
    return _page(request, 'pricing', ('الباقات والأسعار', 'Plans and pricing')[en],
                 ('قارن حدود الطاولات والفريق والمحطات لكل باقة.', 'Compare table, team and station capacity across plans.')[en],
                 plans=plans, rows=rows, staff_rows=staff_rows, tools=tools)


@require_safe
def solutions(request):
    en = _language(request)
    return _page(request, 'solutions', ('حلول المطاعم والكافيهات', 'Hospitality solutions')[en],
                 ('استكشف سير العمل حسب المكان والدور.', 'Explore service workflows by venue and role.')[en],
                 solutions=[_solution(s, en) for s in SOLUTIONS])


@require_safe
def solution_detail(request, slug):
    solution = SOLUTION_BY_SLUG.get(slug)
    if solution is None:
        raise Http404
    en = _language(request)
    entry = _solution(solution, en)
    return _page(request, 'solution', entry['title'], entry['story'], solution=entry)


urlpatterns = [
    path('features/', features, name='features'),
    path('features/<slug:slug>/', feature_detail, name='feature_detail'),
    path('pricing/', pricing, name='pricing'),
    path('solutions/', solutions, name='solutions'),
    path('solutions/<slug:slug>/', solution_detail, name='solution_detail'),
]
