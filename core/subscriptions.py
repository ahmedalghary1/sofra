"""Capture agreed terms separately from editable public plan definitions."""
from .validation import localized
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from .models import Plan, Subscription, SubscriptionAgreement, SubscriptionPayment
from .validation import TOTAL_MAX, money
from decimal import Decimal, ROUND_HALF_UP
from .plan_features import enabled_features

LIMIT_FIELDS = ('max_branches', 'max_tables', 'max_items', 'max_staff', 'max_stations')


def terms_for_plan(plan):
    return {'plan_id': plan.pk, 'plan_name': plan.name, 'currency': 'EGP',
            'monthly_price': str(plan.price_monthly),
            'limits': {field: getattr(plan, field) for field in LIMIT_FIELDS}, 'features': enabled_features(plan)}


@transaction.atomic
def freeze_plan_subscribers(plan_id, actor=None):
    previous = Plan.objects.get(pk=plan_id)
    terms = terms_for_plan(previous)
    for subscription in Subscription.objects.select_for_update().filter(plan_id=plan_id):
        if subscription.agreed_terms and 'features' in subscription.agreed_terms:
            continue
        subscription.agreed_terms = dict(subscription.agreed_terms or terms, features=terms['features'])
        subscription.save(update_fields=['agreed_terms'])
        SubscriptionAgreement.objects.create(subscription=subscription, terms=subscription.agreed_terms,
            status=subscription.status, starts_on=timezone.localdate(), ends_at=subscription.ends_at,
            actor=actor, reason='Baseline captured from configured terms; not a reconstruction of an earlier contract.')


def record_agreement(subscription, actor, reason, *, payment_reference='', billing='manual', agreed_price=None):
    if billing not in ('manual', 'monthly', 'annual', 'trial'):
        raise ValidationError(localized('فترة الاشتراك غير صالحة.', 'Invalid subscription billing period.'))
    payment = None
    if payment_reference:
        payment = SubscriptionPayment.objects.filter(restaurant=subscription.restaurant,
            reference=payment_reference.strip().upper()).first()
        if not payment or payment.period_end < timezone.localdate() or payment.period_end < subscription.ends_at.date():
            raise ValidationError(localized('مرجع التحصيل غير صالح أو لا يغطي نهاية فترة الاشتراك.', 'The receipt is invalid or does not cover the subscription expiry.'))
    terms = terms_for_plan(subscription.plan)
    terms['billing'] = billing
    price = None
    if agreed_price not in (None, ''):
        price = money(agreed_price, TOTAL_MAX)
    elif billing == 'monthly':
        price = subscription.plan.price_monthly
    elif billing == 'annual':
        from .showcase_catalog import ANNUAL_DISCOUNTS, PUBLIC_TIERS
        tier = next((name for name, aliases in PUBLIC_TIERS if subscription.plan.name.casefold() in
                     {alias.casefold() for alias in aliases}), None)
        discount = ANNUAL_DISCOUNTS.get(tier, 0)
        price = (subscription.plan.price_monthly * 12 * (100-discount) / 100).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    elif billing == 'trial':
        price = Decimal('0.00')
    terms['agreed_price'] = str(money(price, TOTAL_MAX)) if price is not None else None
    # Reconfirming a plan is an explicit agreement; the public plan edit alone is not.
    subscription.agreed_terms = terms
    subscription.save(update_fields=['agreed_terms'])
    return SubscriptionAgreement.objects.create(subscription=subscription, terms=terms,
        status=subscription.status, starts_on=payment.period_start if payment else timezone.localdate(), ends_at=subscription.ends_at,
        payment=payment, actor=actor, reason=reason[:240])
