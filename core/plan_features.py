"""Shared bilingual service catalog and explicit subscription entitlements."""
from django.core.exceptions import ValidationError
from .validation import localized

FEATURES = {
    'online_ordering': ('الطلب أونلاين: استلام من الفرع ودليفري', 'Online ordering: pickup and delivery'),
    'menu_import': ('استيراد المنيو من CSV وExcel', 'CSV and Excel menu import'),
    'advanced_reports': ('الأصناف الأكثر طلبًا وأزمنة التحضير والخدمة', 'Best sellers, preparation and service times'),
    'multi_branch_reports': ('تقارير موحدة ومقارنة بين الفروع', 'Combined reports and branch comparisons'),
}
TIER_FEATURES = {'Starter': (), 'Growth': tuple(FEATURES)[:3], 'Business': tuple(FEATURES)}
TEAM_PRESETS = {
    'Starter': (4, {'admin': 1, 'waiter': 1, 'kitchen': 1, 'cashier': 1}),
    'Growth': (8, {'admin': 1, 'waiter': 3, 'kitchen': 2, 'cashier': 2}),
    'Business': (15, {'admin': 3, 'waiter': 6, 'kitchen': 3, 'cashier': 3}),
}
# All rows describe implemented tools. Roadmap modules are never sold as entitlements.
SERVICES = (
    ('menu', ('المنيو وهوية المطعم', 'Menu and restaurant identity'), (
        ('photo_menu', ('منيو بالصور والتصنيفات والأسعار', 'Photo menu, categories and prices'), None, None),
        ('branding', ('لوجو المطعم وألوانه', 'Restaurant logo and colors'), None, None),
        ('modifiers', ('الأحجام والإضافات المدفوعة', 'Sizes and paid extras'), None, None),
        ('recommendations', ('اقتراحات أصناف اختيارية بتفعيل لكل فرع', 'Optional item suggestions enabled per branch'), None, None),
        ('menu_import', FEATURES['menu_import'], 'menu_import', None),
    )),
    ('orders', ('الطلبات والخدمة', 'Orders and service'), (
        ('qr', ('QR لكل طاولة وطلب للمطبخ مع نطاق موقع اختياري', 'Table QR ordering to the kitchen with optional proximity checks'), None, None),
        ('staff_orders', ('إنشاء الطلب بواسطة الموظف', 'Staff-assisted orders'), None, None),
        ('kitchen', ('شاشة المطبخ ومتابعة حالة الطلب', 'Kitchen display and order status'), None, None),
        ('stations', ('توزيع التحضير على أكثر من محطة', 'Preparation across multiple stations'), None, 'max_stations'),
        ('service', ('استدعاء الويتر وطلب الحساب', 'Waiter calls and bill requests'), None, None),
        ('billing', ('الكاشير وتسجيل التحصيل وطباعة الملخص', 'Cashier, collection records and printed summaries'), None, None),
        ('online_ordering', FEATURES['online_ordering'], 'online_ordering', None),
    )),
    ('management', ('الإدارة والتقارير', 'Management and reports'), (
        ('roles', ('حسابات الفريق وصلاحيات الأدوار', 'Team accounts and role permissions'), None, None),
        ('basic_reports', ('تقارير الطلبات والتحصيل وتصدير البيانات CSV', 'Order and collection reports with CSV export'), None, None),
        ('advanced_reports', FEATURES['advanced_reports'], 'advanced_reports', None),
        ('branches', ('إدارة عدة فروع بمنيو وأسعار مستقلة', 'Multiple branches with separate menus and prices'), None, 'max_branches'),
        ('multi_branch_reports', FEATURES['multi_branch_reports'], 'multi_branch_reports', 'max_branches'),
    )),
)


def enabled_features(plan):
    # NULL preserves the full service access of existing/custom plans.
    return list(FEATURES) if plan.enabled_features is None else list(plan.enabled_features)


def has_feature(subscription, key):
    if key not in FEATURES or not subscription or not subscription.plan_id:
        return False
    saved = subscription.agreed_terms.get('features')
    return key in (enabled_features(subscription.plan) if saved is None else saved)


def require_feature(restaurant, key, *, subscription=None):
    if not has_feature(subscription if subscription is not None else getattr(restaurant, 'subscription', None), key):
        raise ValidationError(localized('هذه الخدمة غير مشمولة في اشتراك المطعم. راجع الباقات من الإعدادات.',
                                        'This service is not included in the venue subscription. Review plans in settings.'))


def service_groups(features, limits, english=False):
    return [{'key': group, 'title': title[english], 'rows': [
        {'key': key, 'title': label[english],
         'included': (feature is None or feature in features) and (limit is None or limits[limit] > 1)}
        for key, label, feature, limit in rows]} for group, title, rows in SERVICES]


def capability_feature(slug, anchor):
    return {('digital-menu', 'import'): 'menu_import', ('integrations', 'import'): 'menu_import', ('qr-ordering', 'takeaway'): 'online_ordering',
            ('analytics', 'times'): 'advanced_reports', ('analytics', 'bestsellers'): 'advanced_reports',
            ('analytics', 'branch-summary'): 'multi_branch_reports'}.get((slug, anchor))
