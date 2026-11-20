import re
import unicodedata

from django.templatetags.static import static


def normalize_name(name):
    name = unicodedata.normalize('NFKD', name.casefold())
    return ' '.join(''.join(c for c in name if not unicodedata.combining(c)).replace('ـ', '').split())


# Match dish names, never descriptions (a side or garnish may be mentioned there).
DISH_NAMES = {
    'hummus': ('حمص', 'hummus'),
    'aubergine': ('باذنجان مشوي', 'chargrilled aubergine'),
    'rocket-salad': ('سلطة الجرجير', 'rocket salad', 'arugula salad'),
    'tawook': ('شيش طاووق', 'شيش طاوق', 'chicken skewers', 'shish tawook'),
    'kofta': ('كفتة', 'kofta'),
    'grilled-vegetables': ('خضار الموسم على الفحم', 'خضار مشوي', 'grilled vegetables'),
    'om-ali': ('أم علي', 'om ali', 'umm ali'),
    'milk-pudding': ('مهلبية', 'milk pudding', 'mahalabia'),
    'hibiscus': ('كركديه', 'hibiscus'),
    'mint-lemonade': ('ليمون بالنعناع', 'mint lemonade'),
    'latte': ('لاتيه', 'لاتية', 'latte'),
    'coffee': ('قهوة على الرمل', 'قهوة سريعة', 'قهوة تركي', 'sand-brewed coffee', 'turkish coffee'),
    'burger': ('برجر', 'برغر', 'burger'),
    'alfredo': ('ألفريدو', 'alfredo'),
    'fries': ('بطاطس مقرمشة', 'بطاطس مقلية', 'fries'),
    'mango': ('عصير مانجو', 'مانجو فريش', 'mango juice'),
}
DISH_PATTERNS = [
    (slug, re.compile(r'(?<!\w)(?:' + '|'.join(re.escape(normalize_name(n)) for n in names) + r')(?!\w)'))
    for slug, names in DISH_NAMES.items()
]


def menu_photo(name, uploaded_url=''):
    if uploaded_url:
        return uploaded_url
    title = normalize_name(name)
    slug = next((slug for slug, pattern in DISH_PATTERNS if pattern.search(title)), 'place-setting')
    return static('demo/tawook-menu.webp' if slug == 'tawook' else f'menu-photos/{slug}.webp')
