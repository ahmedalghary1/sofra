"""Public sample menu for the Serveza marketing page."""

from decimal import Decimal

from django.core.exceptions import ValidationError

SECTIONS = (
    ('mezze', 'مقبلات', (
        (1, 'حمص بالليمون', 'حمص ناعم بالطحينة وزيت الزيتون، مع عيش بلدي دافئ.', 75),
        (2, 'باذنجان مشوي', 'باذنجان على الفحم مع دبس الرمان وحب الرمان والبقدونس.', 85),
        (3, 'سلطة الجرجير', 'جرجير طازج مع جبنة قديمة وطماطم وبصل بالسماق.', 95),
    )),
    ('grill', 'من الشواية', (
        (4, 'شيش طاووق على الفحم', 'دجاج بتتبيلة الزبادي والليمون، مع بصل بالسماق وخبز وطحينة.', 245),
        (5, 'كفتة الدار', 'كفتة مشوية مع أرز بالشعيرية وسلطة طحينة.', 265),
        (6, 'خضار الموسم على الفحم', 'كوسة وفلفل وباذنجان مشوي مع فريكة وزبادي بالنعناع.', 175),
    )),
    ('sweets', 'حلويات', (
        (7, 'أم علي بالفستق', 'رقاق باللبن والقشطة، يخرج من الفرن وعليه فستق مجروش.', 110),
        (8, 'مهلبية بماء الورد', 'مهلبية باردة مع ورد مجفف ولوز محمّص.', 80),
    )),
    ('drinks', 'مشروبات', (
        (9, 'كركديه بالبرتقال', 'كركديه منقوع على البارد مع عصير البرتقال والثلج.', 65),
        (10, 'ليمون بالنعناع', 'ليمون معصور مع أوراق نعناع طازجة وثلج مجروش.', 70),
        (11, 'قهوة على الرمل', 'فنجان قهوة محوّجة بالهيل، على درجة السكر المفضلة.', 55),
    )),
)

ITEMS = {
    item_id: {'id': item_id, 'name': title, 'price': price, 'groups': []}
    for _, _, items in SECTIONS
    for item_id, title, _, price in items
}


def demo_order_total(payload):
    if not isinstance(payload, dict) or not isinstance(payload.get('lines'), list):
        raise ValidationError('توجد بيانات غير صالحة في السلة.')
    lines = payload['lines']
    if not lines or len(lines) > len(ITEMS):
        raise ValidationError('أضف أصنافًا من المنيو التجريبية للسلة.')

    total = Decimal('0.00')
    for line in lines:
        if not isinstance(line, dict):
            raise ValidationError('توجد بيانات غير صالحة في السلة.')
        item_id, quantity = line.get('item_id'), line.get('quantity')
        if type(item_id) is not int or item_id not in ITEMS:
            raise ValidationError('أحد الأصناف غير موجود في المنيو التجريبية.')
        if type(quantity) is not int or not 1 <= quantity <= 30:
            raise ValidationError('اختر كمية من ١ إلى ٣٠ لكل صنف.')
        total += Decimal(ITEMS[item_id]['price']) * quantity
    return total
