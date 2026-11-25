"""Read-only feature discovery, venue solutions and public plan comparisons."""

from decimal import Decimal, ROUND_HALF_UP

from django.db.models.functions import Lower
from django.http import Http404
from django.shortcuts import render
from django.urls import path
from django.views.decorators.http import require_safe

from .models import Plan
from .seo import TOPIC_SEARCH, monthly_price_text, public_metadata
from .showcase_data import FAQS, GROUPS, ROLES, SOLUTIONS, STATES, TOPICS
from .plan_features import capability_feature, enabled_features, service_groups

PUBLIC_TIERS = (
    ('Starter', ('Starter', 'ستارتر', 'البداية')),
    ('Growth', ('Growth', 'النمو')),
    ('Business', ('Business', 'الأعمال')),
)
ANNUAL_DISCOUNTS = {'Starter': 10, 'Growth': 15, 'Business': 20}
FREE_TRIAL_DAYS = 14
PRICING_NOTE = (
    'الأسعار استرشادية. أكّد السعر وموعد بدء التجربة مع فريق Serveza.',
    'Prices are illustrative. Confirm your price and trial start date with Serveza.',
)
LIMITS = {
    'max_branches': ('فروع لكل مطعم', 'Branches per venue'),
    'max_tables': ('طاولات لكل فرع', 'Tables per branch'),
    'max_items': ('أصناف لكل فرع', 'Menu items per branch'),
    'max_staff': ('إجمالي حسابات الفريق، شاملة المالك', 'Total team accounts, including the owner'),
    'max_stations': ('محطات تحضير KDS لكل فرع', 'KDS prep stations per branch'),
}
STAFF_ROLES = {
    'waiter': ('الويتر', 'Waiters'), 'admin': ('الإدارة، شاملة المالك', 'Admins, including the owner'),
    'kitchen': ('المطبخ', 'Kitchen staff'), 'cashier': ('الكاشير', 'Cashiers'),
}
PLAN_FOCUS = {
    'Starter': ('لكافيه صغير يخدم حتى {max_tables} طاولة في الفرع. يطلب الضيف من المنيو ويتابع الويتر الطلب حتى الحساب.',
                'For a café serving up to {max_tables} tables per branch. Guests order by QR and the waiter follows each order through to the bill.'),
    'Growth': ('لوردية مزدحمة. افصل تذاكر الشواية عن المشروبات، ووزّع التحضير على ما يصل إلى {max_stations} محطات في الفرع.',
               'For the rush. Give the grill and drinks counter separate tickets, with up to {max_stations} prep stations per branch.'),
    'Business': ('لتشغيل عدة فروع. أدِر حتى {max_branches} فروع و{max_staff} حسابًا للفريق، بمنيو ومطبخ وتقارير مستقلة لكل فرع.',
                 'Run up to {max_branches} branches with {max_staff} team accounts and a separate menu, kitchen and reports for each branch.'),
}
HIGHLIGHT_KEYS = {
    'Starter': ('photo_menu', 'qr', 'kitchen', 'service'),
    'Growth': ('online_ordering', 'menu_import', 'advanced_reports', 'stations'),
    'Business': ('branches', 'multi_branch_reports', 'online_ordering', 'stations'),
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


def _staff_example(plan):
    """Use the saved example while it fits the plan, including after operator edits."""
    example = plan.staff_example
    total = plan.max_staff
    if (example.keys() == STAFF_ROLES.keys()
            and all(type(count) is int and count >= 0 for count in example.values())
            and sum(example.values()) == total
            and (not plan.max_stations or example['kitchen'] >= plan.max_branches)):
        return example
    kitchen = min(max(plan.max_branches, (total + 4) // 5), max(0, total - 1)) if plan.max_stations else 0
    admins = min((total + 9) // 10, total - kitchen)
    back_of_house = max(0, total - admins - kitchen - 1)
    cashiers = min((total + 9) // 10, back_of_house)
    return {'admin': admins, 'kitchen': kitchen, 'cashier': cashiers,
            'waiter': total - admins - kitchen - cashiers}


def public_tier_records():
    names = {alias.casefold() for _, aliases in PUBLIC_TIERS for alias in aliases}
    return Plan.objects.annotate(public_name=Lower('name')).filter(public_name__in=names)


def public_plans(english=False):
    """Expose only named public tiers. Never publish arbitrary negotiated plan records."""
    records = {}
    for row in public_tier_records().order_by('pk'):
        records.setdefault(row.name.casefold(), row)
    plans = []
    for name, aliases in PUBLIC_TIERS:
        row = next((records[alias.casefold()] for alias in aliases if alias.casefold() in records), None)
        if row is None:
            continue
        limits = {key: getattr(row, key) for key in LIMITS}
        discount = ANNUAL_DISCOUNTS[name]
        annual_price = (row.price_monthly * 12 * (100 - discount) / 100).quantize(
            Decimal('0.01'), rounding=ROUND_HALF_UP,
        )
        price_text = format(row.price_monthly, '.2f').rstrip('0').rstrip('.')
        plan = {
            'name': name, 'slug': name.lower(), 'price': price_text, 'limits': limits,
            'features': enabled_features(row),
            'annual_price': format(annual_price, '.2f').rstrip('0').rstrip('.'),
            'annual_discount': discount,
            'trial_duration': f'{FREE_TRIAL_DAYS} days' if english else f'{FREE_TRIAL_DAYS} يومًا',
            'rows': [(key, label[english], limits[key]) for key, label in LIMITS.items()],
        }
        focus = PLAN_FOCUS[name]
        if name == 'Business' and limits['max_branches'] <= 1:
            focus = ('تابع الفريق والمطبخ وتقارير التشغيل في مساحة واحدة.', 'Manage staff, kitchen and reporting in one workspace.')
        if name == 'Growth' and limits['max_stations'] <= 1:
            focus = ('تابع طلبات الصالة والتحصيل في الوردية.', 'Track floor orders and collections throughout the shift.')
        plan['focus'] = focus[english].format(**limits)
        plan['staff'] = _staff_example(row)
        plan['staff_rows'] = [(role, label[english], plan['staff'][role]) for role, label in STAFF_ROLES.items()]
        plan['service_groups'] = service_groups(plan['features'], limits, english)
        plan['highlights'] = [row['title'] for group in plan['service_groups'] for row in group['rows']
                              if row['key'] in HIGHLIGHT_KEYS[name] and row['included']]
        plans.append(plan)
    return plans


def starter_price(plans):
    return next((plan['price'] for plan in plans if plan['slug'] == 'starter'), '')


def _language(request):
    return request.LANGUAGE_CODE == 'en'


def _page(request, template, title, description, **context):
    en = _language(request)
    return render(request, f'marketing/catalog/{template}.html', public_metadata(request, {
        'lang': 'en' if en else 'ar', 'english': en, 'page_title': title, 'page_description': description,
        **context,
    }))


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
    if topic.slug == 'multi-branch':
        return plan['limits']['max_branches'] > 1
    if topic.slug == 'kitchen-display':
        return plan['limits']['max_stations'] > 0
    return True


def _fits_capability(topic, anchor, plan):
    feature = capability_feature(topic.slug, anchor)
    if feature == 'multi_branch_reports' and plan['limits']['max_branches'] <= 1:
        return False
    return _fits_plan(topic, plan) and (feature is None or feature in plan['features'])


@require_safe
def features(request):
    en = _language(request)
    query = request.GET.get('q', '').strip()[:150]
    filters = {key: request.GET.get(key, '')[:60] for key in ('category', 'role', 'state', 'plan')}
    plans = public_plans(en)
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
            if selected and not _fits_capability(topic, item['anchor'], selected):
                continue
            if filters['plan'] == 'common' and plans and not all(_fits_capability(topic, item['anchor'], plan) for plan in plans):
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
    plans = public_plans(en)
    title, answer = TOPIC_SEARCH.get(slug, (topic.title, None))
    if answer:
        entry['direct_answer'] = answer[en]
    for item in entry['items']:
        if item['status'] == 'available':
            item['plans'] = [p['name'] for p in plans if _fits_capability(topic, item['anchor'], p)]
    description = entry['direct_answer'] if answer else entry['summary']
    return _page(request, 'feature', title[en], description, entry=entry, breadcrumb_title=entry['title'],
                 faq_rows=[(FAQS[slug][0][en], FAQS[slug][1][en])],
                 related=[_topic(t, en) for t in related],
                 fit=[p for p in plans if _fits_plan(topic, p)], starter_price=starter_price(plans))


@require_safe
def pricing(request):
    en = _language(request)
    annual = request.GET.get('billing') == 'annual'
    plans = public_plans(en)
    if annual:
        rows = [
            ('price', ('السعر السنوي (ج.م)', 'Annual price (EGP)')[en], [p['annual_price'] for p in plans]),
            ('annual_discount', ('الخصم السنوي', 'Annual discount')[en], [f"{p['annual_discount']}%" for p in plans]),
        ]
    else:
        rows = [('price', ('السعر الشهري (ج.م)', 'Monthly price (EGP)')[en], [p['price'] for p in plans])]
    rows.append(('trial', ('التجربة المجانية', 'Free trial')[en], [p['trial_duration'] for p in plans]))
    rows.extend((key, label[en], [p['limits'][key] for p in plans]) for key, label in LIMITS.items())
    for plan in plans:
        plan['included'] = []
    tools = []
    for topic in TOPICS:
        items = [(ar, eng)[en] for _, status, ar, eng in topic.capabilities if status == 'available']
        if not items:
            continue
        group = {'slug': topic.slug, 'title': INCLUDED_TITLES.get(topic.slug, topic.title)[en], 'items': items}
        availability = []
        for plan in plans:
            included = [(ar, eng)[en] for anchor, state, ar, eng in topic.capabilities
                        if state == 'available' and _fits_capability(topic, anchor, plan)]
            availability.append(bool(included))
            if included:
                plan['included'].append(dict(group, items=included))
        tools.append((group, availability))
    staff_rows = [(label[en], [p['staff'][key] for p in plans]) for key, label in STAFF_ROLES.items()]
    comparison_services = []
    if plans:
        for group_index, group in enumerate(plans[0]['service_groups']):
            for row_index, row in enumerate(group['rows']):
                comparison_services.append((row, [p['service_groups'][group_index]['rows'][row_index]['included'] for p in plans]))
    price = starter_price(plans)
    description = (
        monthly_price_text(price, en) + ' ' +
        ('قارن أسعار نظام طلبات الطاولات وحدود الفروع والطاولات والفريق ومحطات KDS.',
         'Compare table ordering prices and the limits for branches, tables, staff and kitchen stations.')[en]
        if price else ('قارن حدود الطاولات والفريق والمحطات لكل باقة.', 'Compare table, team and station capacity across plans.')[en]
    )
    faq_rows = [
        (('إزاي أعرف حدود اشتراكي الفعلي؟', 'Where can I check my actual limits?')[en],
         ('الخدمات وحدود باقتك واستخدام الفروع موجودة في إعدادات المطعم. يقدر المالك يعاين الخدمات التي ستتوقف عند تغيير الباقة قبل التواصل مع فريق Serveza. العقود القائمة تحتفظ بشروطها حتى تسجيل اتفاق جديد.',
          'Venue settings show included services, plan limits and branch usage. Owners can preview services lost on a plan change. Existing agreements keep their terms until explicitly renewed.')[en]),
        (('هل يمكن الدفع من الصفحة؟', 'Can I pay on this page?')[en],
         ('الدفع الإلكتروني غير متاح حاليًا. يتواصل فريق Serveza معك لاختيار الباقة وتسجيل التحصيل يدويًا.',
          'Online payment is currently unavailable. The Serveza team helps you choose a plan and records collections manually.')[en]),
        (('هل المميزات المخططة ضمن الاشتراك؟', 'Does a subscription include roadmap features?')[en],
         ('تقدر تشغّل الوظائف الموسومة بالمتاحة في دليل المميزات. الوظائف المخططة تنتظر التنفيذ.',
          'You can use capabilities marked available in the feature guide. Planned capabilities are awaiting implementation.')[en]),
    ]
    return _page(request, 'pricing', ('سعر نظام منيو QR وطلبات الطاولات في مصر', 'QR menu and table ordering prices in Egypt')[en],
                 description, plans=plans, rows=rows, staff_rows=staff_rows, tools=tools, faq_rows=faq_rows,
                 starter_price=price, annual=annual, pricing_note=PRICING_NOTE[en], comparison_services=comparison_services)


@require_safe
def solutions(request):
    en = _language(request)
    return _page(request, 'solutions', ('برنامج إدارة خدمة المطاعم والكافيهات', 'Restaurant service management in Egypt')[en],
                 ('نظّم خدمة الصالة والمطبخ بالـ QR في مطعم أو كافيه أو مجموعة فروع. استكشف سيناريو يناسب فريقك وطريقة عملك.',
                  'Manage floor service and kitchen orders in your Egyptian café, restaurant or group. Explore workflows by venue and role.')[en],
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
