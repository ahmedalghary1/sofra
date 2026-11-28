from .validation import localized
from django.utils.translation import gettext
import hashlib
import json
import uuid
from datetime import timedelta
from decimal import Decimal
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from .models import AuditLog, Branch, CollectionCorrection, Membership, MenuItem, Order, OrderHistory, OrderLine, OrderLinePrepHistory, OrderRevision, Restaurant, ServiceCall
from .validation import TOTAL_MAX, identifier, money

ORDER_NEXT = {'new': 'accepted', 'accepted': 'preparing', 'preparing': 'ready', 'ready': 'served'}
CALL_NEXT = {'open': 'acknowledged', 'acknowledged': 'in_progress', 'in_progress': 'done'}

def audit(restaurant, actor, action, detail='', *, changes=None):
    AuditLog.objects.create(restaurant=restaurant, actor=actor, action=action, detail=detail[:240],
                            changes=changes or {})


def audit_change(restaurant, actor, action, obj, before, fields):
    after = {field: str(getattr(obj, field)) for field in fields}
    audit(restaurant, actor, action, str(obj.pk), changes={'model': obj._meta.label_lower,
          'id': obj.pk, 'before': before, 'after': after})

def order_quantity(value):
    if isinstance(value, str) and len(value) <= 2 and value.isascii() and value.isdecimal():
        value = int(value)
    if type(value) is not int or not 1 <= value <= 30:
        raise ValidationError('كمية أو خيارات غير صالحة.')
    return value


def order_edit_allowed(order, has_started_preparation):
    branch = order.branch
    subscription = getattr(branch.restaurant, 'subscription', None)
    return (order.status in ('new', 'accepted') and order.payment_status == 'unpaid'
            and not has_started_preparation and branch.active and branch.restaurant.active
            and subscription is not None and subscription.valid())

def normalize_order_lines(branch, raw_lines, allow_empty=False):
    if not isinstance(raw_lines, list) or len(raw_lines) > 40 or (not raw_lines and not allow_empty):
        raise ValidationError('أضف صنفًا واحدًا على الأقل للسلة.')
    parsed = []
    for line in raw_lines:
        try:
            item_id = identifier(line['item_id'])
            quantity = order_quantity(line['quantity'])
            selected = line.get('options', [])
            size_id = line.get('size_id')
        except (KeyError, ValueError, TypeError, AttributeError):
            raise ValidationError('توجد بيانات غير صالحة في السلة.')
        if not isinstance(selected, list) or len(selected) > 800:
            raise ValidationError('كمية أو خيارات غير صالحة.')
        chosen = {identifier(option) for option in selected}
        if len(chosen) != len(selected):
            raise ValidationError('لا يمكن اختيار الإضافة نفسها مرتين.')
        parsed.append((item_id, quantity, chosen, identifier(size_id) if size_id not in (None, '') else None))
    items = {item.pk: item for item in MenuItem.objects.select_related('category', 'station')
             .prefetch_related('sizes', 'modifier_groups__options').filter(
                 pk__in={row[0] for row in parsed}, category__branch=branch,
                 category__visible=True, available=True, archived=False)}
    normalized, total = [], Decimal('0.00')
    for item_id, quantity, chosen, size_id in parsed:
        item = items.get(item_id)
        if not item:
            raise ValidationError('أحد الأصناف لم يعد متاحًا. حدّث المنيو.')
        size_name, base_price = '', item.price
        if size_id is not None:
            size = next((s for s in item.sizes.all() if s.pk == size_id and s.available), None)
            if not size:
                raise ValidationError('حجم الصنف لم يعد متاحًا. حدّث المنيو.')
            size_name, base_price = size.name, size.price
        snapshot, extra = [], Decimal('0.00')
        for group in item.modifier_groups.all():
            group_options = {o.id: o for o in group.options.all() if o.available}
            found = chosen.intersection(group_options)
            if not group.min_select <= len(found) <= group.max_select:
                raise ValidationError(gettext('اختيارات «{name}» غير مكتملة.').format(name=group.name))
            for option_id in found:
                option = group_options[option_id]
                snapshot.append({'name': option.name, 'price': str(option.price)})
                extra += option.price
            chosen -= found
        if chosen:
            raise ValidationError('أحد الخيارات لا يخص الصنف المحدد.')
        base_price = money(base_price)
        unit = money(base_price + extra)
        line_total = money(unit * quantity, TOTAL_MAX)
        normalized.append((item, quantity, size_name, base_price, snapshot, unit, line_total))
        total += line_total
    if total > Decimal('99999999.99'):
        raise ValidationError('إجمالي الطلب أكبر من الحد المسموح.')
    return normalized, total

def create_order(table, payload, user=None, require_session=False, *, recover_only=False):
    if not isinstance(payload, dict):
        raise ValidationError('توجد بيانات غير صالحة في السلة.')
    try:
        key = uuid.UUID(str(payload['idempotency_key']))
    except (KeyError, ValueError, TypeError):
        subscription = getattr(table.branch.restaurant, 'subscription', None)
        if not subscription or not subscription.valid():
            raise ValidationError('استقبال الطلبات متوقف مؤقتًا. يرجى التواصل مع المطعم.')
        raise ValidationError('تعذر التحقق من الطلب. حدّث الصفحة وحاول مجددًا.')
    raw_lines = payload.get('lines', [])
    if not isinstance(raw_lines, list) or not 1 <= len(raw_lines) <= 40:
        raise ValidationError('أضف صنفًا واحدًا على الأقل للسلة.')
    note = str(payload.get('note', '')).strip()
    if len(note) > 500:
        raise ValidationError('الملاحظة طويلة جدًا.')
    try:
        digest = hashlib.sha256(json.dumps({'lines': raw_lines, 'note': note}, sort_keys=True,
                                          ensure_ascii=False, allow_nan=False).encode()).hexdigest()
    except (ValueError, TypeError, RecursionError):
        raise ValidationError('توجد بيانات غير صالحة في السلة.')
    existing = Order.objects.filter(table=table, idempotency_key=key).first()
    if existing:
        if existing.request_hash != digest:
            raise ValidationError('مفتاح الطلب مستخدم بالفعل لطلب آخر.')
        return existing
    if recover_only:
        with transaction.atomic():
            type(table).objects.select_for_update(of=('self',)).get(pk=table.pk)
            saved = Order.objects.filter(table=table, idempotency_key=key).first()
            if saved:
                if saved.request_hash != digest:
                    raise ValidationError('مفتاح الطلب مستخدم بالفعل لطلب آخر.')
                return saved
        raise ValidationError('No matching saved order.', code='order_not_found')
    if not table.active or table.archived or not table.branch.active or table.branch.archived or not table.branch.accepting_orders or not table.branch.restaurant.active:
        raise ValidationError('الطاولة لا تستقبل طلبات حاليًا.')
    subscription = getattr(table.branch.restaurant, 'subscription', None)
    if not subscription or not subscription.valid():
        raise ValidationError('استقبال الطلبات متوقف مؤقتًا. يرجى التواصل مع المطعم.')
    if require_session and str(payload.get('session_token', '')) != str(table.session_token):
        raise ValidationError('انتهت جلسة هذه الطاولة. امسح رمز QR من جديد قبل إرسال الطلب.')
    try:
        with transaction.atomic():
            # Match online/settings lock order before taking a table lock.
            # NO KEY UPDATE permits audit/FK key-share locks during archiving
            # and emergency QR changes, avoiding an inverse lock dependency.
            locked_venue = Restaurant.objects.select_for_update(of=('self',), no_key=True).get(pk=table.branch.restaurant_id)
            locked_branch = Branch.objects.select_for_update(of=('self',), no_key=True).get(pk=table.branch_id)
            locked_branch.restaurant = locked_venue
            locked_table = type(table).objects.select_for_update(of=('self',)).get(pk=table.pk)
            locked_table.branch = locked_branch
            prior = Order.objects.filter(table=locked_table, idempotency_key=key).first()
            if prior:
                if prior.request_hash != digest:
                    raise ValidationError('مفتاح الطلب مستخدم بالفعل لطلب آخر.')
                return prior
            if (not locked_table.active or locked_table.archived or not locked_table.branch.active or locked_table.branch.archived or
                    not locked_table.branch.accepting_orders or not locked_table.branch.restaurant.active):
                raise ValidationError('الطاولة لا تستقبل طلبات حاليًا.')
            current_subscription = getattr(locked_table.branch.restaurant, 'subscription', None)
            if not current_subscription or not current_subscription.valid():
                raise ValidationError('استقبال الطلبات متوقف مؤقتًا. يرجى التواصل مع المطعم.')
            if require_session and str(payload.get('session_token', '')) != str(locked_table.session_token):
                raise ValidationError('انتهت جلسة هذه الطاولة. امسح رمز QR من جديد قبل إرسال الطلب.')
            if user is None:
                from .geofencing import validate_presence
                validate_presence(locked_table, payload)
            if Order.objects.filter(table=locked_table,
                    created_at__gte=timezone.now()-timedelta(minutes=1)).count() >= 12:
                raise ValidationError('عدد الطلبات مرتفع على هذه الطاولة. انتظر دقيقة وحاول مرة أخرى.')
            normalized, total = normalize_order_lines(locked_table.branch, raw_lines)
            if 'reviewed_total' in payload and payload['reviewed_total'] != format(total, '.2f'):
                raise ValidationError('تغيّر إجمالي الطلب. حدّث المنيو وراجع الأسعار قبل الإرسال.')
            order = Order.objects.create(branch=locked_table.branch, table=locked_table, idempotency_key=key,
                                         session_token=locked_table.session_token,
                                         request_hash=digest, source='staff' if user else 'qr',
                                         total=total, note=note, created_by=user)
            OrderLine.objects.bulk_create([OrderLine(order=order, item=item, station=item.station,
                item_name=item.name,
                size_name=size_name, base_price=base_price, unit_price=unit,
                quantity=quantity, modifiers=mods, line_total=subtotal)
                for item, quantity, size_name, base_price, mods, unit, subtotal in normalized])
            OrderHistory.objects.create(order=order, old_status='', new_status='new', actor=user)
            from .recommendations import attribute_order
            attribute_order(order, payload)
            return order
    except IntegrityError:
        existing = Order.objects.get(table=table, idempotency_key=key)
        if existing.request_hash != digest:
            raise ValidationError('مفتاح الطلب مستخدم بالفعل لطلب آخر.')
        return existing

def revision_lines(order):
    return [{'id': line.pk, 'item_name': line.item_name, 'size_name': line.size_name,
             'base_price': str(line.base_price) if line.base_price is not None else None,
             'unit_price': str(line.unit_price), 'quantity': line.quantity,
             'modifiers': line.modifiers, 'line_total': str(line.line_total)}
            for line in order.lines.order_by('pk')]

@transaction.atomic
def edit_order(order, actor, payload):
    if not isinstance(payload, dict):
        raise ValidationError('بيانات التعديل غير صالحة.')
    try:
        key = uuid.UUID(str(payload['edit_key']))
    except (KeyError, ValueError, TypeError):
        raise ValidationError('مفتاح التعديل غير صالح.')
    reason = str(payload.get('reason', '')).strip()
    if not 3 <= len(reason) <= 240:
        raise ValidationError('اكتب سبب التعديل بين 3 و240 حرفًا.')
    retained = payload.get('existing')
    added = payload.get('add')
    if not isinstance(retained, list) or not isinstance(added, list) or len(retained) + len(added) > 40:
        raise ValidationError('تفاصيل التعديل غير صالحة.')
    digest = hashlib.sha256(json.dumps({'existing': retained, 'add': added, 'reason': reason,
                                        'expected_version': payload.get('expected_version')},
                                       sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    order = Order.objects.select_for_update(of=('self',)).select_related('branch__restaurant').get(pk=order.pk)
    prior = OrderRevision.objects.filter(order=order, key=key).first()
    if prior:
        if prior.request_hash != digest:
            raise ValidationError('مفتاح التعديل مستخدم لتغيير مختلف.')
        return order
    if not order_edit_allowed(order, order.history.filter(new_status='preparing').exists()):
        raise ValidationError('يمكن تعديل طلب جديد أو مستلم وغير مدفوع فقط. أنشئ طلبًا إضافيًا عند بدء التحضير.')
    if isinstance(payload.get('expected_version'), bool) or str(payload.get('expected_version')) != str(order.version):
        raise ValidationError('تغير الطلب منذ فتح الصفحة. حدّث الصفحة وراجع التعديل مجددًا.')
    current = {line.pk: line for line in order.lines.all()}
    chosen = {}
    for row in retained:
        if not isinstance(row, dict) or isinstance(row.get('id'), bool) or isinstance(row.get('quantity'), bool):
            raise ValidationError('تفاصيل العنصر الموجود غير صالحة.')
        try:
            line_id, quantity = identifier(row['id']), order_quantity(row['quantity'])
        except (KeyError, ValueError, TypeError):
            raise ValidationError('تفاصيل العنصر الموجود غير صالحة.')
        if line_id not in current or line_id in chosen:
            raise ValidationError('عنصر غير صالح أو كمية غير مسموحة.')
        chosen[line_id] = quantity
    if not chosen and not added:
        raise ValidationError('يجب أن يحتوي الطلب على صنف واحد على الأقل.')
    normalized, added_total = normalize_order_lines(order.branch, added, allow_empty=True)
    new_total = order.delivery_fee + added_total + sum((current[line_id].unit_price * qty for line_id, qty in chosen.items()), Decimal('0.00'))
    if new_total > Decimal('99999999.99'):
        raise ValidationError('إجمالي الطلب أكبر من الحد المسموح.')
    before = revision_lines(order)
    old_total = order.total
    order.lines.exclude(pk__in=chosen).delete()
    for line_id, qty in chosen.items():
        line = current[line_id]
        if line.quantity != qty:
            line.quantity = qty
            line.line_total = line.unit_price * qty
            line.save(update_fields=['quantity', 'line_total'])
    OrderLine.objects.bulk_create([OrderLine(order=order, item=item, station=item.station,
        item_name=item.name,
        size_name=size_name, base_price=base_price, unit_price=unit,
        quantity=quantity, modifiers=mods, line_total=subtotal)
        for item, quantity, size_name, base_price, mods, unit, subtotal in normalized])
    order.total = new_total
    order.version += 1
    order.save(update_fields=['total', 'version', 'updated_at'])
    OrderRevision.objects.create(order=order, actor=actor, key=key, request_hash=digest,
        version=order.version, reason=reason, old_total=old_total, new_total=new_total,
        before=before, after=revision_lines(order))
    audit(order.branch.restaurant, actor, 'order.edit', f'{order.pk}: {old_total} → {new_total}; {reason}')
    return order

@transaction.atomic
def advance_order(order, actor, status, reason='', *, english=False):
    order = Order.objects.select_for_update().get(pk=order.pk)
    if status == 'cancelled':
        if order.payment_status == 'paid':
            raise ValidationError('A collected order cannot be cancelled.' if english else 'يتعذر إلغاء طلب بعد تسجيل تحصيله.')
        if order.status in ('served', 'cancelled') or not reason.strip():
            raise ValidationError('الإلغاء يتطلب سببًا وطلبًا لم يُقدّم بعد.')
    elif ORDER_NEXT.get(order.status) != status:
        raise ValidationError('الانتقال إلى هذه الحالة غير مسموح.')
    if status == 'ready' and order.lines.filter(station__isnull=False).exists() and order.lines.exclude(prep_status='ready').exists():
        raise ValidationError('أكمل تحضير جميع أصناف الطلب في محطاتها قبل تحويل الطلب إلى جاهز.')
    old = order.status
    order.status = status
    order.save(update_fields=['status', 'updated_at'])
    OrderHistory.objects.create(order=order, old_status=old, new_status=status, actor=actor, reason=reason[:240])
    audit(order.branch.restaurant, actor, 'order.status', f'{order.pk}: {old} → {status}')
    return order


@transaction.atomic
def advance_order_line(line, actor, status):
    order = Order.objects.select_for_update().select_related('branch__restaurant').get(pk=line.order_id)
    line = OrderLine.objects.select_for_update().get(pk=line.pk, order=order)
    if order.status != 'preparing' or {'new': 'preparing', 'preparing': 'ready'}.get(line.prep_status) != status:
        raise ValidationError('انتقال حالة الصنف غير مسموح. حدّث شاشة المطبخ وراجع حالة الطلب.')
    old = line.prep_status
    line.prep_status = status
    line.save(update_fields=['prep_status'])
    OrderLinePrepHistory.objects.create(line=line, old_status=old, new_status=status, actor=actor)
    order.save(update_fields=['updated_at'])
    audit(order.branch.restaurant, actor, 'order.line.prep', f'{order.pk}/{line.pk}: {old} → {status}')
    return line


@transaction.atomic
def correct_order_status(order, actor, reason, expected_status):
    reason = str(reason).strip()
    if not 3 <= len(reason) <= 240:
        raise ValidationError('اكتب سبب التصحيح بين 3 و240 حرفًا.')
    order = Order.objects.select_for_update().select_related('branch__restaurant').get(pk=order.pk)
    if order.status != expected_status:
        raise ValidationError('تغيرت حالة الطلب منذ فتح الصفحة. حدّث القائمة وراجع الطلب.')
    previous = {'accepted': 'new', 'preparing': 'accepted', 'ready': 'preparing'}
    target = previous.get(order.status)
    if not target or order.payment_status != 'unpaid':
        raise ValidationError('يمكن تصحيح آخر خطوة لطلب جارٍ وغير مدفوع فقط.')
    old = order.status
    order.status = target
    order.save(update_fields=['status', 'updated_at'])
    OrderHistory.objects.create(order=order, old_status=old, new_status=target, actor=actor,
                                reason=reason)
    audit(order.branch.restaurant, actor, 'order.status.correct', f'{order.pk}: {old} → {target}; {reason}')
    return order

@transaction.atomic
def record_manual_payment(order, actor):
    order = Order.objects.select_for_update().get(pk=order.pk)
    if order.status == 'cancelled':
        raise ValidationError('لا يمكن تسجيل تحصيل لطلب ملغي.')
    if order.payment_status == 'paid':
        raise ValidationError('تم تسجيل تحصيل هذا الطلب بالفعل.')
    order.payment_status = 'paid'
    order.paid_at = timezone.now()
    order.save(update_fields=['payment_status','paid_at','updated_at'])
    audit(order.branch.restaurant, actor, 'order.payment.manual', str(order.pk))
    return order


@transaction.atomic
def correct_manual_collection(order, actor, key, reason, *, expected_paid_at):
    """Reverse a mistaken manual record, preserving its original date and amount."""
    try:
        key = uuid.UUID(str(key))
    except (ValueError, TypeError, AttributeError):
        raise ValidationError(localized('مفتاح تصحيح التحصيل غير صالح.', 'Invalid collection correction key.'))
    reason = str(reason).strip()
    if not 3 <= len(reason) <= 240:
        raise ValidationError('اكتب سبب التصحيح بين 3 و240 حرفًا.')
    order = Order.objects.select_for_update().select_related('branch__restaurant').get(pk=order.pk)
    if not Membership.objects.filter(user=actor, restaurant=order.branch.restaurant,
                                     role__in=('owner', 'manager'), active=True, archived=False).exists():
        raise ValidationError(localized('لا تملك صلاحية تصحيح التحصيل.', 'You are not allowed to correct collection entries.'))
    prior = CollectionCorrection.objects.filter(order=order, key=key).first()
    if prior:
        if prior.reason != reason:
            raise ValidationError(localized('مفتاح التصحيح مستخدم لسبب مختلف.', 'This correction key was used with a different reason.'))
        return prior
    if order.payment_status != 'paid' or order.paid_at is None or order.total <= 0:
        raise ValidationError(localized('لا يوجد تحصيل مسجل قابل للتصحيح.', 'No recorded collection can be corrected.'))
    try:
        expected = parse_datetime(str(expected_paid_at)) if len(str(expected_paid_at)) <= 40 else None
    except ValueError:
        expected = None
    if expected is None or timezone.is_naive(expected) or expected != order.paid_at:
        raise ValidationError(localized('تغير قيد التحصيل منذ فتح الصفحة. حدّث السجل وراجعه مجددًا.', 'The collection entry changed. Refresh and review it again.'))
    correction = CollectionCorrection.objects.create(order=order, key=key, reason=reason,
        amount=order.total, original_paid_at=order.paid_at, actor=actor)
    order.payment_status, order.paid_at = 'unpaid', None
    order.save(update_fields=['payment_status', 'paid_at', 'updated_at'])
    audit(order.branch.restaurant, actor, 'order.payment.correct', str(order.pk), changes={
        'order_id': order.pk, 'correction_id': correction.pk, 'amount': str(correction.amount),
        'original_paid_at': correction.original_paid_at.isoformat(), 'reason': reason})
    return correction

@transaction.atomic
def advance_call(call, actor, status):
    call = ServiceCall.objects.select_for_update().get(pk=call.pk)
    if CALL_NEXT.get(call.status) != status:
        raise ValidationError('الانتقال إلى هذه الحالة غير مسموح.')
    call.status = status
    if status == 'acknowledged':
        call.assigned_to = actor
        call.acknowledged_at = timezone.now()
    if status == 'done':
        call.done_at = timezone.now()
    call.save(update_fields=['status', 'assigned_to', 'acknowledged_at', 'done_at', 'updated_at'])
    audit(call.table.branch.restaurant, actor, 'call.status', f'{call.pk}: {status}')
    return call

@transaction.atomic
def create_service_call(table, kind, session_token=None):
    if kind not in ('waiter', 'bill'):
        raise ValidationError(localized('نوع الاستدعاء غير صالح.', 'Invalid service request type.'))
    table = type(table).objects.select_for_update(of=('self',)).get(pk=table.pk)
    table.branch = Branch.objects.select_related('restaurant').get(pk=table.branch_id)
    if session_token is not None and str(session_token) != str(table.session_token):
        raise ValidationError(localized('انتهت جلسة هذه الطاولة. امسح رمز QR من جديد.', 'This table session ended. Scan the QR code again.'))
    if not table.active or table.archived or not table.branch.active or table.branch.archived or not table.branch.accepting_orders or not table.branch.restaurant.active:
        raise ValidationError(localized('الطاولة لا تستقبل استدعاءات حاليًا.', 'This table is not accepting service requests.'))
    subscription = getattr(table.branch.restaurant, 'subscription', None)
    if not subscription or not subscription.valid():
        raise ValidationError(localized('الخدمة متوقفة مؤقتًا. يرجى التواصل مع المطعم.', 'Service is temporarily paused. Please speak to the restaurant team.'))
    if ServiceCall.objects.filter(table=table, kind=kind, status__in=['open', 'acknowledged', 'in_progress']).exists():
        raise ValidationError(localized('طلبك السابق ما زال قيد التنفيذ.', 'Your previous request is still being handled.'))
    if ServiceCall.objects.filter(table=table, created_at__gte=timezone.now()-timedelta(minutes=1)).count() >= 3:
        raise ValidationError(localized('انتظر قليلًا قبل إرسال استدعاء جديد.', 'Please wait before sending another service request.'))
    return ServiceCall.objects.create(table=table, kind=kind)
