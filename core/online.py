"""Restaurant storefront orders use the same menu pricing and immutable line snapshots."""
import hashlib
import json
import uuid
from decimal import Decimal

from django import forms
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction

from .models import Branch, Order, OrderHistory, OrderLine, Restaurant
from .services import normalize_order_lines
from .validation import TOTAL_MAX, localized, money
from .plan_features import require_feature


class OnlineOrderingForm(forms.Form):
    online_ordering_enabled = forms.BooleanField(required=False)
    online_pickup_enabled = forms.BooleanField(required=False)
    online_delivery_enabled = forms.BooleanField(required=False)
    delivery_fee = forms.DecimalField(min_value=0, max_digits=9, decimal_places=2)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        labels = {
            'online_ordering_enabled': ('تفعيل رابط الطلب أونلاين', 'Enable the online ordering link'),
            'online_pickup_enabled': ('السماح بالاستلام من الفرع', 'Allow branch pickup'),
            'online_delivery_enabled': ('السماح بالتوصيل', 'Allow delivery'),
            'delivery_fee': ('رسوم التوصيل الثابتة (ج.م)', 'Flat delivery fee (EGP)'),
        }
        for key, label in labels.items():
            self.fields[key].label = localized(*label)
        self.fields['delivery_fee'].widget.attrs.update({'step': '0.01', 'min': '0', 'inputmode': 'decimal'})

    def clean(self):
        data = super().clean()
        if data.get('online_ordering_enabled') and not (data.get('online_pickup_enabled') or data.get('online_delivery_enabled')):
            raise forms.ValidationError(localized('فعّل الاستلام من الفرع أو التوصيل على الأقل.',
                                                  'Enable pickup or delivery to accept online orders.'))
        return data


class OnlineCustomerForm(forms.Form):
    fulfillment = forms.ChoiceField(choices=[('pickup', 'Pickup'), ('delivery', 'Delivery')])
    customer_name = forms.CharField(max_length=100)
    customer_phone = forms.CharField(max_length=24)
    delivery_address = forms.CharField(max_length=500, required=False)

    def clean_customer_phone(self):
        phone = self.cleaned_data['customer_phone']
        if any(not (character.isdecimal() or character in '+-() ') for character in phone):
            raise forms.ValidationError(localized('اكتب رقم هاتف صالحًا للتواصل.', 'Enter a valid contact phone number.'))
        if not 7 <= sum(character.isdecimal() for character in phone) <= 15:
            raise forms.ValidationError(localized('اكتب رقم هاتف صالحًا للتواصل.', 'Enter a valid contact phone number.'))
        return phone

    def clean(self):
        data = super().clean()
        if data.get('fulfillment') == 'delivery' and not data.get('delivery_address'):
            self.add_error('delivery_address', localized('اكتب عنوان التوصيل بالتفصيل.', 'Enter the full delivery address.'))
        if data.get('fulfillment') == 'pickup':
            data['delivery_address'] = ''
        return data


def create_online_order(branch, payload):
    if not isinstance(payload, dict):
        raise ValidationError(localized('بيانات الطلب غير صالحة.', 'Invalid order data.'))
    try:
        key = uuid.UUID(str(payload['idempotency_key']))
    except (KeyError, ValueError, TypeError, AttributeError):
        raise ValidationError(localized('حدّث الصفحة قبل إرسال الطلب.', 'Refresh the page before sending your order.'))
    for field in ('fulfillment', 'customer_name', 'customer_phone', 'delivery_address', 'note'):
        if field in payload and not isinstance(payload[field], str):
            raise ValidationError(localized('بيانات الطلب غير صالحة.', 'Invalid order data.'))
    form = OnlineCustomerForm(payload)
    if not form.is_valid():
        raise ValidationError([str(error) for errors in form.errors.values() for error in errors])
    data = form.cleaned_data
    note = payload.get('note', '').strip()
    if len(note) > 500:
        raise ValidationError(localized('الملاحظة طويلة جدًا.', 'The order note is too long.'))
    raw_lines = payload.get('lines')
    try:
        digest = hashlib.sha256(json.dumps(dict(data, lines=raw_lines, note=note), sort_keys=True,
                                          ensure_ascii=False, allow_nan=False).encode()).hexdigest()
    except (TypeError, ValueError, RecursionError):
        raise ValidationError(localized('بيانات الطلب غير صالحة.', 'Invalid order data.'))

    def prior_order():
        prior = Order.objects.filter(branch=branch, source='online', idempotency_key=key).first()
        if prior and prior.request_hash != digest:
            raise ValidationError(localized('مفتاح الطلب مستخدم بالفعل لطلب آخر.', 'This request key belongs to another order.'))
        return prior

    prior = prior_order()
    if prior:
        return prior
    try:
        with transaction.atomic():
            # Settings saves lock the same restaurant; every branch keeps its own menu.
            restaurant = Restaurant.objects.select_for_update().get(pk=branch.restaurant_id)
            branch = Branch.objects.select_for_update().get(pk=branch.pk, restaurant=restaurant)
            prior = prior_order()
            if prior:
                return prior
            subscription = getattr(restaurant, 'subscription', None)
            require_feature(restaurant, 'online_ordering')
            if (not restaurant.active or not restaurant.online_ordering_enabled or not branch.active
                    or branch.archived or not branch.accepting_orders or not subscription or not subscription.valid()):
                raise ValidationError(localized('استقبال الطلبات أونلاين متوقف حاليًا. حدّث الصفحة أو تواصل مع المطعم.',
                                                'Online ordering is paused. Refresh the page or contact the restaurant.'))
            if ((data['fulfillment'] == 'pickup' and not restaurant.online_pickup_enabled)
                    or (data['fulfillment'] == 'delivery' and not restaurant.online_delivery_enabled)):
                raise ValidationError(localized('طريقة الاستلام لم تعد متاحة. حدّث الصفحة.',
                                                'This fulfillment method is no longer available. Refresh the page.'))
            normalized, subtotal = normalize_order_lines(branch, raw_lines)
            fee = money(restaurant.delivery_fee) if data['fulfillment'] == 'delivery' else Decimal('0.00')
            total = money(subtotal + fee, TOTAL_MAX)
            if payload.get('reviewed_total') != format(total, '.2f') or payload.get('reviewed_delivery_fee') != format(fee, '.2f'):
                raise ValidationError(localized('تغيّرت الأسعار أو رسوم التوصيل. حدّث المنيو وراجع الإجمالي قبل الإرسال.',
                                                'Prices or delivery fees changed. Refresh the menu and review the total before sending.'))
            order = Order.objects.create(branch=branch, table=None, source='online', fulfillment=data['fulfillment'],
                customer_name=data['customer_name'], customer_phone=data['customer_phone'],
                delivery_address=data['delivery_address'], delivery_fee=fee, total=total, note=note,
                idempotency_key=key, request_hash=digest)
            OrderLine.objects.bulk_create([OrderLine(order=order, item=item, station=item.station, item_name=item.name,
                size_name=size_name, base_price=base_price, unit_price=unit, quantity=quantity,
                modifiers=mods, line_total=line_total)
                for item, quantity, size_name, base_price, mods, unit, line_total in normalized])
            OrderHistory.objects.create(order=order, old_status='', new_status='new')
            from .recommendations import attribute_order
            attribute_order(order, payload)
            return order
    except IntegrityError:
        prior = prior_order()
        if prior:
            return prior
        raise
