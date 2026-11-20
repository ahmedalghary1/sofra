"""Public sample menu for the Serveza marketing page."""

from decimal import Decimal

from django.core.exceptions import ValidationError
from .menu_photos import menu_photo

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

ENGLISH_SECTIONS = {'mezze': 'Mezze', 'grill': 'From the grill', 'sweets': 'Desserts', 'drinks': 'Drinks'}
ENGLISH_ITEMS = {
    1: ('Lemon hummus', 'Chickpeas, tahini and olive oil with warm baladi bread.'),
    2: ('Chargrilled aubergine', 'Charcoal-roasted aubergine with pomegranate molasses, seeds and parsley.'),
    3: ('Rocket salad', 'Rocket, aged cheese, tomatoes and sumac onions.'),
    4: ('Chargrilled chicken skewers', 'Lemon and yogurt chicken with sumac onions, bread and tahini.'),
    5: ('House kofta', 'Grilled kofta with vermicelli rice and tahini salad.'),
    6: ('Seasonal grilled vegetables', 'Courgette, pepper and aubergine with freekeh and mint yogurt.'),
    7: ('Pistachio Om Ali', 'Warm layers of pastry baked with milk and cream, finished with pistachios.'),
    8: ('Rosewater milk pudding', 'Chilled milk pudding with dried rose and toasted almonds.'),
    9: ('Hibiscus and orange', 'Cold-steeped hibiscus with orange juice over ice.'),
    10: ('Mint lemonade', 'Fresh lemon juice blended with mint and crushed ice.'),
    11: ('Sand-brewed coffee', 'Cardamom-spiced coffee, sweetened to taste.'),
}


def sample_menu(english=False):
    sections = SECTIONS
    if english:
        sections = [
            (key, ENGLISH_SECTIONS[key], [
                (item_id, *ENGLISH_ITEMS[item_id], price)
                for item_id, _, _, price in rows
            ])
            for key, _, rows in SECTIONS
        ]
    items = [dict(ITEMS[item_id], name=title, description=description, category=key,
                  category_name=label, photo=menu_photo(title))
             for key, label, rows in sections for item_id, title, description, _ in rows]
    return sections, items


def demo_order_total(payload, english=False):
    invalid = 'Invalid cart data.' if english else 'توجد بيانات غير صالحة في السلة.'
    if not isinstance(payload, dict) or not isinstance(payload.get('lines'), list):
        raise ValidationError(invalid)
    lines = payload['lines']
    if not lines or len(lines) > len(ITEMS):
        raise ValidationError('Add items from the sample menu.' if english else 'أضف أصنافًا من المنيو التجريبية للسلة.')

    total = Decimal('0.00')
    for line in lines:
        if not isinstance(line, dict):
            raise ValidationError(invalid)
        item_id, quantity = line.get('item_id'), line.get('quantity')
        if type(item_id) is not int or item_id not in ITEMS:
            raise ValidationError('An item is missing from the sample menu.' if english else 'أحد الأصناف غير موجود في المنيو التجريبية.')
        if type(quantity) is not int or not 1 <= quantity <= 30:
            raise ValidationError('Choose a quantity from 1 to 30 for each item.' if english else 'اختر كمية من 1 إلى 30 لكل صنف.')
        total += Decimal(ITEMS[item_id]['price']) * quantity
    return total
