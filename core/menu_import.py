import csv
import io
from decimal import Decimal, InvalidOperation

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone
from datetime import timedelta

from .limits import check_creation_limit
from .models import Category, MenuImportBatch, MenuItem
from .services import audit


HEADERS = ('category', 'name', 'price', 'description')


def parse_menu_csv(upload):
    if not upload or not upload.name.lower().endswith('.csv'):
        raise ValidationError('اختر ملف CSV.')
    if upload.size > 1_000_000:
        raise ValidationError('الحد الأقصى للملف 1 ميجابايت.')
    try:
        content = upload.read().decode('utf-8-sig')
        reader = csv.DictReader(io.StringIO(content, newline=''))
        if not reader.fieldnames or tuple(name.strip() for name in reader.fieldnames) != HEADERS:
            raise ValidationError('الأعمدة المطلوبة بالترتيب: category,name,price,description.')
        rows, errors, seen = [], [], set()
        for number, source in enumerate(reader, start=2):
            if number > 201:
                errors.append('الحد الأقصى 200 صنف في الدفعة.')
                break
            if None in source or all(not str(source.get(key) or '').strip() for key in HEADERS):
                errors.append(f'السطر {number}: عدد الأعمدة أو البيانات غير صالح.')
                continue
            category = str(source['category'] or '').strip()
            name = str(source['name'] or '').strip()
            description = str(source['description'] or '').strip()
            if not category or len(category) > 80 or not name or len(name) > 120 or len(description) > 300:
                errors.append(f'السطر {number}: الاسم أو التصنيف أو الوصف يتجاوز الحد المسموح.')
                continue
            try:
                price = Decimal(str(source['price'] or '').strip())
                if not price.is_finite() or price < 0 or price > 9999999 or price.as_tuple().exponent < -2:
                    raise InvalidOperation
            except InvalidOperation:
                errors.append(f'السطر {number}: السعر يجب أن يكون رقمًا موجبًا بدقتين عشريتين.')
                continue
            signature = (category.casefold(), name.casefold())
            if signature in seen:
                errors.append(f'السطر {number}: الصنف مكرر داخل الملف.')
                continue
            seen.add(signature)
            rows.append({'category': category, 'name': name, 'description': description,
                         'price': str(price.quantize(Decimal('0.01')))})
        if not rows and not errors:
            errors.append('الملف لا يحتوي أصنافًا.')
        return rows, errors
    except (UnicodeDecodeError, csv.Error):
        raise ValidationError('تعذر قراءة الملف. استخدم CSV بترميز UTF-8.')


@transaction.atomic
def commit_menu_import(batch, actor):
    batch = MenuImportBatch.objects.select_for_update().select_related('branch__restaurant').get(pk=batch.pk)
    if batch.user_id != actor.pk:
        raise ValidationError('دفعة الاستيراد لا تخص هذا الحساب.')
    if batch.committed_at:
        return 0
    if batch.created_at < timezone.now() - timedelta(minutes=15):
        raise ValidationError('انتهت صلاحية المعاينة. ارفع الملف مجددًا.')
    restaurant, branch = batch.branch.restaurant, batch.branch
    if not restaurant.active or not branch.active:
        raise ValidationError('هذا الفرع لا يقبل استيرادًا حاليًا.')
    subscription = check_creation_limit(restaurant, 'item', branch)
    current_count = MenuItem.objects.filter(category__branch=branch).count()
    if current_count + len(batch.rows) > subscription.plan.max_items:
        raise ValidationError(f'الملف يتجاوز حد الأصناف في الباقة ({subscription.plan.max_items}).')
    categories = {category.name: category for category in Category.objects.filter(branch=branch)}
    items = []
    for row in batch.rows:
        category = categories.get(row['category'])
        if category is None:
            category = Category.objects.create(branch=branch, name=row['category'])
            categories[category.name] = category
        items.append(MenuItem(category=category, name=row['name'], price=Decimal(row['price']),
                              description=row['description']))
    MenuItem.objects.bulk_create(items)
    batch.committed_at = timezone.now()
    batch.save(update_fields=['committed_at'])
    audit(restaurant, actor, 'menu.import', f'branch={branch.pk}; items={len(items)}; batch={batch.pk}')
    return len(items)
