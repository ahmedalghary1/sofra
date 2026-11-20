"""Bilingual, fictional shift records for the public product tour."""

from django import template

register = template.Library()

# Each panel keeps its explanatory copy alongside the records it illustrates.
PANELS = (
    ('overview', ('نظرة عامة', 'Overview'), ('الوردية في نظرة واحدة', 'Your shift at a glance'),
     ('راجع الطلبات وقيمتها قبل ما تتابع تفاصيل الخدمة.', 'Read order counts and value before diving into service.'),
     ((('الطلبات', 'Orders'), '16'), (('قيمة الطلبات', 'Order value'), '1,280'), (('طاولات نشطة', 'Active tables'), '4'))),
    ('orders', ('الطلبات', 'Live orders'), ('تابع كل طلب على حدة', 'Follow each order'),
     ('كل تذكرة مرتبطة بطاولة وبحالة واضحة للفريق.', 'Each ticket carries its table and service status.'),
     ((('#104 · طاولة 4', '#104 · Table 4'), ('قيد التحضير', 'Preparing')), (('#103 · طاولة 6', '#103 · Table 6'), ('جاهز', 'Ready')), (('#102 · طاولة 2', '#102 · Table 2'), ('تم التقديم', 'Served')))),
    ('menu', ('المنيو', 'Menu'), ('منيو يتغيّر مع الوردية', 'Keep the menu current'),
     ('حدّث سعر المشروب أو أوقف الصنف عند نفاده.', 'Update a drink price or pause an item when it sells out.'),
     ((('فلات وايت', 'Flat white'), ('80 ج.م', '80 EGP')), (('كولد برو', 'Cold brew'), ('95 ج.م', '95 EGP')), (('براوني', 'Brownie'), ('غير متاح', 'Sold out')))),
    ('tables', ('الطاولات وQR', 'Tables & QR'), ('مكان لكل طلب', 'A place for every order'),
     ('اربط المنيو بالطاولة، وتابع الجلسات المفتوحة في الفرع.', 'Link each menu to a table and follow open sessions at the branch.'),
     ((('طاولة 2', 'Table 2'), ('مفتوحة', 'Open')), (('طاولة 4', 'Table 4'), ('مفتوحة', 'Open')), (('طاولة 6', 'Table 6'), ('مغلقة', 'Closed')))),
    ('kitchen', ('المطبخ', 'Kitchen'), ('التفاصيل قدّام المحطة', 'The prep details, in one ticket'),
     ('المحطة تشوف الكمية والإضافات المطلوبة قبل التحضير.', 'Read quantities and selected extras at the preparation station.'),
     ((('2 × فلات وايت', '2 × Flat white'), ('لبن شوفان', 'Oat milk')), (('1 × كولد برو', '1 × Cold brew'), ('بدون سكر', 'Unsweetened')))),
    ('waiter', ('الصالة', 'Waiter'), ('النداء يوصل للطاولة الصح', 'Give every call a table'),
     ('استقبل نداء ضيف أو طلب حساب وتابع استجابة الفريق.', 'Pick up a guest call or bill request and follow its response.'),
     ((('طاولة 4', 'Table 4'), ('نداء ويتر', 'Waiter call')), (('طاولة 2', 'Table 2'), ('طلب حساب', 'Bill request')))),
    ('billing', ('الحساب', 'Billing'), ('سجل تحصيل واضح', 'A clear payment record'),
     ('سجّل تحصيل الطلب يدويًا ثم تابعه في التقارير.', 'Record payment manually and follow the amount in reports.'),
     ((('2 × فلات وايت', '2 × Flat white'), ('160 ج.م', '160 EGP')), (('1 × براوني', '1 × Brownie'), ('70 ج.م', '70 EGP')), (('الإجمالي', 'Total'), ('230 ج.م', '230 EGP')))),
    ('reports', ('التقارير', 'Reports'), ('اعرف إيه بيتطلب أكتر', 'See what guests order most'),
     ('راجع الأصناف الأكثر طلبًا خلال الفترة المحددة.', 'Compare your most ordered items over the selected period.'),
     ((('فلات وايت', 'Flat white'), '8'), (('كولد برو', 'Cold brew'), '5'), (('براوني', 'Brownie'), '3'))),
)


@register.simple_tag
def studio_panels(english=False):
    language = 1 if english else 0

    def localize(value):
        return value[language] if isinstance(value, tuple) else value

    return [
        {
            'slug': slug, 'name': localize(name), 'title': localize(title),
            'description': localize(description),
            'rows': [(localize(label), localize(value)) for label, value in rows],
        }
        for slug, name, title, description, rows in PANELS
    ]
