"""Branch-scoped complementary items. Ordering never depends on telemetry succeeding."""
import hashlib
import json
import logging
import uuid
from collections import Counter, defaultdict
from datetime import timedelta
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from .models import (Branch, ItemRecommendation, MenuItem, Order, OrderLine,
                     RecommendationConversion, RecommendationDailySummary, RecommendationEvent,
                     RecommendationExposure, RecommendationPairStat)
from .validation import localized

logger = logging.getLogger(__name__)
WINDOW = timedelta(minutes=30)


def fail():
    raise ValidationError(localized('بيانات الاقتراحات غير صالحة.', 'Invalid recommendation data.'))


@transaction.atomic
def manage_recommendations(branch, actor, data):
    from django.shortcuts import get_object_or_404
    from .forms import ItemRecommendationForm
    from .services import audit
    Branch.objects.select_for_update().get(pk=branch.pk)
    action = data.get('action')
    if action == 'recommendation_settings':
        branch.recommendations_enabled = data.get('recommendations_enabled') == 'on'
        branch.recommendations_learning_enabled = data.get('recommendations_learning_enabled') == 'on'
        branch.save(update_fields=['recommendations_enabled', 'recommendations_learning_enabled'])
        audit(branch.restaurant, actor, 'recommendation.settings', str(branch.pk), changes={
            'enabled': branch.recommendations_enabled, 'learning': branch.recommendations_learning_enabled})
        return
    try:
        source_id = int(data.get('item_id', ''))
        link_id = int(data['link_id']) if data.get('link_id') else None
    except (ValueError, TypeError):
        fail()
    source = get_object_or_404(MenuItem, pk=source_id, category__branch=branch, archived=False)
    link = get_object_or_404(ItemRecommendation, pk=link_id, branch=branch, source_item=source) if link_id else None
    if action == 'recommendation_delete' and link:
        pk = link.pk
        link.delete()
        audit(branch.restaurant, actor, 'recommendation.delete', str(pk))
        return
    if action != 'recommendation_save':
        fail()
    form = ItemRecommendationForm(data, branch=branch, source=source, current_target=link.target_item_id if link else None)
    if not form.is_valid():
        raise ValidationError(localized('اختر صنفًا مختلفًا من الفرع وترتيبًا من 0 إلى 999.',
                                        'Choose another item from this branch and a priority from 0 to 999.'))
    link = link or ItemRecommendation(branch=branch, source_item=source)
    link.target_item = form.cleaned_data['target_item']
    link.priority = form.cleaned_data['priority']
    link.active = form.cleaned_data['active']
    if ItemRecommendation.objects.filter(branch=branch, source_item=source, target_item=link.target_item).exclude(pk=link.pk).exists():
        raise ValidationError(localized('هذا الصنف مرتبط بالفعل. عدّل الرابط الموجود.', 'This item is already linked. Edit the existing link.'))
    link.save()
    audit(branch.restaurant, actor, 'recommendation.save', str(link.pk), changes={
        'source': source.pk, 'target': link.target_item_id, 'priority': link.priority, 'active': link.active})


def context_key(branch, channel, client_id, table=None):
    try:
        client = str(uuid.UUID(str(client_id)))
    except (ValueError, TypeError, AttributeError):
        fail()
    context = f'{branch.pk}:{channel}:{client}'
    if table is not None:
        context += f':{table.pk}:{table.session_token}'
    return hashlib.sha256(context.encode()).hexdigest()


def cart_ids(branch, raw):
    if not isinstance(raw, list) or len(raw) > 40 or any(type(x) is not int or x <= 0 or x > 2**63-1 for x in raw):
        fail()
    ids = set(raw)
    if MenuItem.objects.filter(pk__in=ids, category__branch=branch, category__visible=True,
                               available=True, archived=False).count() != len(ids):
        fail()
    return ids


def minimum_price(item):
    """The regular size is always selectable; required extras must be priced too."""
    base = min([Decimal(item.price)] + [Decimal(size.price) for size in item.sizes.all() if size.available])
    has_required = False
    for group in item.modifier_groups.all():
        prices = sorted(Decimal(option.price) for option in group.options.all() if option.available)
        if len(prices) < group.min_select or group.min_select > group.max_select:
            return None
        base += sum(prices[:group.min_select], Decimal('0'))
        has_required = has_required or group.min_select > 0
    return base, bool(item.sizes.all()) or has_required


def select_recommendations(branch, ids):
    if not ids or not branch.recommendations_enabled:
        return [], 'rules-v1'
    links = ItemRecommendation.objects.filter(branch=branch, source_item_id__in=ids, active=True,
        source_item__category__branch=branch, target_item__category__branch=branch,
        target_item__available=True, target_item__archived=False, target_item__category__visible=True,
    ).exclude(target_item_id__in=ids).select_related('target_item__category').prefetch_related(
        'target_item__sizes', 'target_item__modifier_groups__options')
    candidates, priorities, sources = {}, {}, defaultdict(set)
    for link in links:
        target = link.target_item
        if minimum_price(target) is None:
            continue
        candidates[target.pk] = target
        priorities[target.pk] = min(priorities.get(target.pk, link.priority), link.priority)
        sources[target.pk].add(link.source_item_id)
    evidence = {}
    if branch.recommendations_learning_enabled and candidates:
        stats = RecommendationPairStat.objects.filter(branch=branch, source_item_id__in=ids,
            target_item_id__in=candidates, built_at__gte=timezone.now()-timedelta(hours=48),
            eligible_orders__gte=100, source_orders__gte=20, pair_orders__gte=10)
        for stat in stats:
            if stat.source_orders and stat.target_orders and stat.eligible_orders:
                confidence = Decimal(stat.pair_orders) / stat.source_orders
                lift = confidence / (Decimal(stat.target_orders) / stat.eligible_orders)
                if lift >= Decimal('1.2') and stat.source_item_id in sources[stat.target_item_id]:
                    score = (lift, confidence, stat.pair_orders)
                    evidence[stat.target_item_id] = max(evidence.get(stat.target_item_id, score), score)
    def rank(pk):
        score = evidence.get(pk, (Decimal('0'), Decimal('0'), 0))
        return (priorities[pk], *(-x for x in score), -len(sources[pk]), pk)
    return [candidates[pk] for pk in sorted(candidates, key=rank)[:3]], ('pairs-v1' if evidence else 'rules-v1')


def exposure_for(branch, channel, scope_key, ids, candidates, version):
    if not candidates:
        return None
    now = timezone.now()
    fingerprint = [(item.pk, str(item.price), str(minimum_price(item))) for item in candidates]
    cart_key = hashlib.sha256(json.dumps([sorted(ids), fingerprint, version]).encode()).hexdigest()
    # One short branch lock serializes concurrent requests for the same exposure.
    with transaction.atomic():
        Branch.objects.select_for_update().get(pk=branch.pk)
        exposure = RecommendationExposure.objects.filter(branch=branch, scope_key=scope_key,
            channel=channel, cart_key=cart_key, expires_at__gt=now, order__isnull=True).first()
        if not exposure:
            exposure = RecommendationExposure.objects.create(branch=branch, scope_key=scope_key,
                channel=channel, cart_key=cart_key, candidates=[item.pk for item in candidates],
                version=version, expires_at=now+WINDOW)
        return exposure


def record_event(branch, scope_key, payload):
    try:
        exposure_id = uuid.UUID(str(payload.get('exposure')))
    except (ValueError, AttributeError, TypeError):
        fail()
    with transaction.atomic():
        exposure = RecommendationExposure.objects.select_for_update().filter(pk=exposure_id,
            branch=branch, scope_key=scope_key, expires_at__gt=timezone.now(), order__isnull=True).first()
        if exposure is None:
            fail()
        if payload.get('kind') == 'shown':
            if exposure.shown_at is None:
                exposure.shown_at = timezone.now()
                exposure.save(update_fields=['shown_at'])
        elif payload.get('kind') == 'added':
            item_id = payload.get('item_id')
            if type(item_id) is not int or item_id not in exposure.candidates or exposure.shown_at is None:
                fail()
            RecommendationEvent.objects.get_or_create(exposure=exposure, item_id=item_id, kind='added')
        else:
            fail()


def attribute_order(order, payload):
    """Called only on first creation, in a savepoint so telemetry cannot roll back an order."""
    if order.source not in ('qr', 'online') or not payload.get('recommendations'):
        return
    try:
        with transaction.atomic():
            scope_key = context_key(order.branch, order.source, payload.get('recommendation_context'), order.table)
            provenance = payload['recommendations']
            if not isinstance(provenance, list) or len(provenance) > 40:
                return
            lines = list(order.lines.order_by('pk'))
            for entry in provenance:
                if not isinstance(entry, dict):
                    continue
                index, quantity = entry.get('line_index'), entry.get('quantity')
                if type(index) is not int or not 0 <= index < len(lines) or type(quantity) is not int or quantity < 1:
                    continue
                try:
                    exposure_id = uuid.UUID(str(entry.get('exposure')))
                except (ValueError, TypeError, AttributeError):
                    continue
                line = lines[index]
                exposure = RecommendationExposure.objects.select_for_update().filter(pk=exposure_id,
                    branch=order.branch, channel=order.source, scope_key=scope_key,
                    shown_at__isnull=False, expires_at__gt=order.created_at).first()
                if (not exposure or exposure.order_id not in (None, order.pk) or line.item_id not in exposure.candidates
                        or not exposure.events.filter(item_id=line.item_id, kind='added').exists()):
                    continue
                exposure.order = order
                exposure.save(update_fields=['order'])
                amount_quantity = min(quantity, line.quantity)
                RecommendationConversion.objects.get_or_create(line=line, defaults={'exposure': exposure,
                    'impression_id': exposure.pk, 'shown_at': exposure.shown_at,
                    'quantity': amount_quantity, 'amount': line.unit_price * amount_quantity})
    except Exception:
        # No customer fields, payloads, public tokens or exception text in telemetry logs.
        logger.warning('Recommendation attribution unavailable for order %s', order.pk)


def recommendation_report(branches, start=None, end=None):
    exposures = RecommendationExposure.objects.filter(branch__in=branches, shown_at__isnull=False)
    summaries = RecommendationDailySummary.objects.filter(branch__in=branches)
    conversions = RecommendationConversion.objects.filter(line__order__branch__in=branches).exclude(line__order__status='cancelled')
    if start:
        exposures = exposures.filter(shown_at__gte=start, shown_at__lt=end)
        summaries = summaries.filter(day__gte=timezone.localtime(start).date(), day__lt=timezone.localtime(end).date())
        conversions = conversions.filter(shown_at__gte=start, shown_at__lt=end)
    added = RecommendationEvent.objects.filter(exposure__in=exposures).values('exposure_id').distinct().count()
    historic = summaries.aggregate(shown=Sum('shown'), added=Sum('added'))
    added += historic['added'] or 0
    shown = exposures.count() + (historic['shown'] or 0)
    accepted = conversions.values('impression_id').distinct().count()
    return {'shown': shown, 'added': added, 'orders': conversions.values('line__order_id').distinct().count(),
        'amount': conversions.aggregate(total=Sum('amount'))['total'] or Decimal('0.00'),
        'acceptance': (Decimal(accepted) * 100 / shown).quantize(Decimal('.1')) if shown else Decimal('0.0')}


def prune_recommendation_events(*, now=None):
    """Retain daily anonymous counts and order attribution; discard detailed cart scopes."""
    now = now or timezone.now()
    cutoff_date = timezone.localtime(now-timedelta(days=90)).date()
    from datetime import datetime, time
    cutoff = timezone.make_aware(datetime.combine(cutoff_date, time.min))
    deleted = 0
    for branch_id in RecommendationExposure.objects.filter(created_at__lt=cutoff).values_list('branch_id', flat=True).distinct():
        with transaction.atomic():
            Branch.objects.select_for_update().get(pk=branch_id)
            exposures = RecommendationExposure.objects.filter(branch_id=branch_id, created_at__lt=cutoff)
            days = defaultdict(list)
            for pk, shown_at in exposures.values_list('pk', 'shown_at'):
                if shown_at:
                    days[timezone.localtime(shown_at).date()].append(pk)
            for day, ids in days.items():
                added = RecommendationEvent.objects.filter(exposure_id__in=ids).values('exposure_id').distinct().count()
                summary, _ = RecommendationDailySummary.objects.get_or_create(branch_id=branch_id, day=day)
                summary.shown += len(ids)
                summary.added += added
                summary.save(update_fields=['shown', 'added'])
            deleted += exposures.count()
            exposures.delete()
    return deleted


def rebuild_stats(branch, *, now=None):
    now = now or timezone.now()
    start = now-timedelta(days=90)
    eligible = Order.objects.filter(branch=branch, status='served', created_at__gte=start, created_at__lte=now)
    # Count each item once per order, streaming baskets instead of keeping every order in memory.
    approved = list(ItemRecommendation.objects.filter(branch=branch, active=True,
        source_item__category__branch=branch, target_item__category__branch=branch).values_list('source_item_id', 'target_item_id'))
    relevant = {pk for pair in approved for pk in pair}
    from itertools import groupby
    neighbors = defaultdict(set)
    for source, target in approved:
        neighbors[source].add(target)
    counts, pairs, total = Counter(), Counter(), 0
    # A single left-join query keeps the denominator and item baskets on the same DB snapshot.
    lines = eligible.order_by('pk').values_list('pk', 'lines__item_id',
        'lines__item__category__branch_id').iterator(chunk_size=2000)
    for _, group in groupby(lines, key=lambda row: row[0]):
        total += 1
        basket = {row[1] for row in group if row[1] in relevant and row[2] == branch.pk}
        counts.update(basket)
        for source in basket:
            pairs.update((source, target) for target in neighbors[source] if target in basket)
    rows = [RecommendationPairStat(branch=branch, source_item_id=source, target_item_id=target,
        eligible_orders=total, source_orders=counts[source], target_orders=counts[target],
        pair_orders=pairs[source, target],
        period_start=start, built_at=now) for source, target in approved]
    with transaction.atomic():
        Branch.objects.select_for_update().get(pk=branch.pk)
        RecommendationPairStat.objects.filter(branch=branch).delete()
        RecommendationPairStat.objects.bulk_create(rows)
    return len(rows)
