from django.utils.translation import gettext
import csv
import io
from decimal import Decimal, InvalidOperation

from openpyxl import load_workbook
from openpyxl.utils.exceptions import InvalidFileException
from zipfile import BadZipFile

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone
from datetime import timedelta

from .limits import check_creation_limit
from .plan_features import require_feature
from .models import Category, MenuImportBatch, MenuItem
from .services import audit
from .validation import money


HEADERS = ('category', 'name', 'price', 'description')


def validate_menu_rows(sources):
    rows, errors, seen = [], [], set()
    for number, source in enumerate(sources, start=2):
        if number > 201:
            errors.append('الحد الأقصى 200 صنف في الدفعة.')
            break
        if None in source or all(not str(source.get(key) or '').strip() for key in HEADERS):
            errors.append(gettext('السطر {number}: عدد الأعمدة أو البيانات غير صالح.').format(number=number))
            continue
        category = str(source['category'] or '').strip()
        name = str(source['name'] or '').strip()
        description = str(source['description'] or '').strip()
        if not category or len(category) > 80 or not name or len(name) > 120 or len(description) > 300:
            errors.append(gettext('السطر {number}: الاسم أو التصنيف أو الوصف يتجاوز الحد المسموح.').format(number=number))
            continue
        try:
            price = money(source['price'])
        except ValidationError:
            errors.append(gettext('السطر {number}: السعر يجب أن يكون رقمًا موجبًا بدقتين عشريتين.').format(number=number))
            continue
        signature = (category.casefold(), name.casefold())
        if signature in seen:
            errors.append(gettext('السطر {number}: الصنف مكرر داخل الملف.').format(number=number))
            continue
        seen.add(signature)
        rows.append({'category': category, 'name': name, 'description': description,
                     'price': str(price.quantize(Decimal('0.01')))})
    if not rows and not errors:
        errors.append('الملف لا يحتوي أصنافًا.')
    return rows, errors


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
        reader.fieldnames = list(HEADERS)
        return validate_menu_rows(reader)
    except (UnicodeDecodeError, csv.Error):
        raise ValidationError('تعذر قراءة الملف. استخدم CSV بترميز UTF-8.')


def parse_menu_xlsx(upload):
    if not upload or not upload.name.lower().endswith('.xlsx'):
        raise ValidationError('اختر ملف Excel بصيغة XLSX.')
    if upload.size > 1_000_000:
        raise ValidationError('الحد الأقصى للملف 1 ميجابايت.')
    try:
        workbook = load_workbook(upload, read_only=True, data_only=False)
        try:
            if len(workbook.worksheets) != 1:
                raise ValidationError('استخدم ورقة عمل واحدة للاستيراد.')
            sheet = workbook.active
            header = next(sheet.iter_rows(min_row=1, max_row=1, max_col=5, values_only=True), ())
            if tuple(str(value or '').strip() for value in header[:4]) != HEADERS or len(header) > 4 and header[4] is not None:
                raise ValidationError('الأعمدة المطلوبة بالترتيب: category,name,price,description.')
            def sources():
                for cells in sheet.iter_rows(min_row=2, max_row=202, max_col=5):
                    if any(cell.data_type == 'f' for cell in cells):
                        yield {'category': '', 'name': '', 'price': '', 'description': '', None: 'formula'}
                    else:
                        values = [cell.value for cell in cells]
                        row = dict(zip(HEADERS, values[:4]))
                        if values[4] is not None:
                            row[None] = values[4]
                        yield row
            rows, errors = validate_menu_rows(sources())
            if sheet.max_row and sheet.max_row > 201:
                errors.append('الحد الأقصى 200 صنف في الدفعة.')
            return rows, errors
        finally:
            workbook.close()
    except (BadZipFile, InvalidFileException, OSError, ValueError, TypeError, EOFError):
        raise ValidationError('تعذر قراءة ملف Excel. استخدم ملف XLSX صالحًا.')


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
    require_feature(restaurant, 'menu_import', subscription=subscription)
    current_count = MenuItem.objects.filter(category__branch=branch, archived=False).count()
    limit = subscription.limit('max_items', branch)
    if current_count + len(batch.rows) > limit:
        raise ValidationError(gettext('الملف يتجاوز حد الأصناف في الباقة ({limit}).').format(limit=limit))
    validate_existing_menu(branch, batch.rows)
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


def validate_existing_menu(branch, rows):
    existing = {(category.casefold(), name.casefold()) for category, name in MenuItem.objects.filter(
        category__branch=branch, archived=False).values_list('category__name', 'name')}
    for row in rows:
        if (row['category'].casefold(), row['name'].casefold()) in existing:
            from django.utils.translation import get_language
            raise ValidationError(('An item with this category and name already exists. Review the menu before importing.'
                if get_language() == 'en' else 'يوجد صنف بالاسم والتصنيف نفسيهما بالفعل. راجع المنيو قبل الاستيراد.'))
