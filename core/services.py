import hashlib
import json
import uuid
from datetime import timedelta
from decimal import Decimal
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone
from .models import AuditLog, MenuItem, ModifierOption, Order, OrderHistory, OrderLine, ServiceCall

ORDER_NEXT = {'new': 'accepted', 'accepted': 'preparing', 'preparing': 'ready', 'ready': 'served'}
CALL_NEXT = {'open': 'acknowledged', 'acknowledged': 'in_progress', 'in_progress': 'done'}

def audit(restaurant, actor, action, detail=''):
    AuditLog.objects.create(restaurant=restaurant, actor=actor, action=action, detail=detail[:240])

def create_order(table, payload, user=None):
    if not table.active or not table.branch.active or not table.branch.accepting_orders or not table.branch.restaurant.active:
        raise ValidationError('الطاولة لا تستقبل طلبات حاليًا.')
    subscription = getattr(table.branch.restaurant, 'subscription', None)
    if subscription and not subscription.valid():
        raise ValidationError('استقبال الطلبات متوقف مؤقتًا. يرجى التواصل مع المطعم.')
    try:
        key = uuid.UUID(str(payload['idempotency_key']))
    except (KeyError, ValueError, TypeError):
        raise ValidationError('تعذر التحقق من الطلب. حدّث الصفحة وحاول مجددًا.')
    raw_lines = payload.get('lines', [])
    if not isinstance(raw_lines, list) or not 1 <= len(raw_lines) <= 40:
        raise ValidationError('أضف صنفًا واحدًا على الأقل للسلة.')
    note = str(payload.get('note', '')).strip()
    if len(note) > 500:
        raise ValidationError('الملاحظة طويلة جدًا.')
    digest = hashlib.sha256(json.dumps({'lines': raw_lines, 'note': note}, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    existing = Order.objects.filter(table=table, idempotency_key=key).first()
    if existing:
        if existing.request_hash != digest:
            raise ValidationError('مفتاح الطلب مستخدم بالفعل لطلب آخر.')
        return existing
    if Order.objects.filter(table=table, created_at__gte=timezone.now()-timedelta(minutes=1)).count() >= 12:
        raise ValidationError('عدد الطلبات مرتفع على هذه الطاولة. انتظر دقيقة وحاول مرة أخرى.')
    normalized, total = [], Decimal('0.00')
    for line in raw_lines:
        try:
            item_id = int(line['item_id'])
            quantity = int(line['quantity'])
            selected = line.get('options', [])
        except (KeyError, ValueError, TypeError, AttributeError):
            raise ValidationError('توجد بيانات غير صالحة في السلة.')
        if not 1 <= quantity <= 30 or not isinstance(selected, list):
            raise ValidationError('كمية أو خيارات غير صالحة.')
        item = MenuItem.objects.select_related('category').prefetch_related('modifier_groups__options').filter(pk=item_id, category__branch=table.branch, category__visible=True, available=True).first()
        if not item:
            raise ValidationError('أحد الأصناف لم يعد متاحًا. حدّث المنيو.')
        try:
            chosen = {int(x) for x in selected}
        except (ValueError, TypeError):
            raise ValidationError('أحد الخيارات غير صالح.')
        if len(chosen) != len(selected):
            raise ValidationError('لا يمكن اختيار الإضافة نفسها مرتين.')
        snapshot, extra = [], Decimal('0.00')
        for group in item.modifier_groups.all():
            group_options = {o.id: o for o in group.options.all() if o.available}
            found = chosen.intersection(group_options)
            if not group.min_select <= len(found) <= group.max_select:
                raise ValidationError(f'اختيارات «{group.name}» غير مكتملة.')
            for option_id in found:
                option = group_options[option_id]
                snapshot.append({'name': option.name, 'price': str(option.price)})
                extra += option.price
            chosen -= found
        if chosen:
            raise ValidationError('أحد الخيارات لا يخص الصنف المحدد.')
        unit = item.price + extra
        line_total = unit * quantity
        normalized.append((item, quantity, snapshot, unit, line_total))
        total += line_total
    if total > Decimal('99999999.99'):
        raise ValidationError('إجمالي الطلب أكبر من الحد المسموح.')
    try:
        with transaction.atomic():
            order = Order.objects.create(branch=table.branch, table=table, idempotency_key=key,
                                         request_hash=digest, source='staff' if user else 'qr',
                                         total=total, note=note, created_by=user)
            OrderLine.objects.bulk_create([OrderLine(order=order, item=item, item_name=item.name,
                unit_price=unit, quantity=quantity, modifiers=mods, line_total=subtotal)
                for item, quantity, mods, unit, subtotal in normalized])
            OrderHistory.objects.create(order=order, old_status='', new_status='new', actor=user)
            return order
    except IntegrityError:
        existing = Order.objects.get(table=table, idempotency_key=key)
        if existing.request_hash != digest:
            raise ValidationError('مفتاح الطلب مستخدم بالفعل لطلب آخر.')
        return existing

@transaction.atomic
def advance_order(order, actor, status, reason=''):
    order = Order.objects.select_for_update().get(pk=order.pk)
    if status == 'cancelled':
        if order.status in ('served', 'cancelled') or not reason.strip():
            raise ValidationError('الإلغاء يتطلب سببًا وطلبًا لم يُقدّم بعد.')
    elif ORDER_NEXT.get(order.status) != status:
        raise ValidationError('الانتقال إلى هذه الحالة غير مسموح.')
    old = order.status
    order.status = status
    order.save(update_fields=['status', 'updated_at'])
    OrderHistory.objects.create(order=order, old_status=old, new_status=status, actor=actor, reason=reason[:240])
    audit(order.branch.restaurant, actor, 'order.status', f'{order.pk}: {old} → {status}')
    return order

@transaction.atomic
def advance_call(call, actor, status):
    call = ServiceCall.objects.select_for_update().get(pk=call.pk)
    if CALL_NEXT.get(call.status) != status:
        raise ValidationError('الانتقال إلى هذه الحالة غير مسموح.')
    call.status = status
    if status == 'acknowledged': call.assigned_to = actor
    call.save(update_fields=['status', 'assigned_to', 'updated_at'])
    audit(call.table.branch.restaurant, actor, 'call.status', f'{call.pk}: {status}')
    return call

def create_service_call(table, kind):
    if kind not in ('waiter', 'bill'):
        raise ValidationError('نوع الاستدعاء غير صالح.')
    if not table.active or not table.branch.active or not table.branch.accepting_orders or not table.branch.restaurant.active:
        raise ValidationError('الطاولة لا تستقبل استدعاءات حاليًا.')
    subscription = getattr(table.branch.restaurant, 'subscription', None)
    if subscription and not subscription.valid():
        raise ValidationError('الخدمة متوقفة مؤقتًا. يرجى التواصل مع المطعم.')
    if ServiceCall.objects.filter(table=table, kind=kind, status__in=['open', 'acknowledged', 'in_progress']).exists():
        raise ValidationError('طلبك السابق ما زال قيد التنفيذ.')
    if ServiceCall.objects.filter(table=table, created_at__gte=timezone.now()-timedelta(minutes=1)).count() >= 3:
        raise ValidationError('انتظر قليلًا قبل إرسال استدعاء جديد.')
    try:
        return ServiceCall.objects.create(table=table, kind=kind)
    except IntegrityError:
        raise ValidationError('طلبك السابق ما زال قيد التنفيذ.')
