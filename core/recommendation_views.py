"""Public cart reads and telemetry share the same verified ordering context."""
import logging

from django.core.exceptions import ValidationError
from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_POST

from .menu_catalog import serialize_menu_item
from .menu_photos import menu_photo
from .models import Branch, DiningTable
from .plan_features import has_feature
from .recommendations import cart_ids, context_key, exposure_for, fail, minimum_price, record_event, select_recommendations
from .security import consume_limits
from .validation import load_json_payload, localized

logger = logging.getLogger(__name__)


@never_cache
@require_POST
def recommendations(request, token=None, slug=None, branch_id=None):
    if not consume_limits(request, 'recommendations', limit=120, seconds=60):
        response = JsonResponse({'ok': False}, status=429)
        response['Retry-After'] = '60'
        return response
    if len(request.body) > 16384:
        return JsonResponse({'ok': False}, status=400)
    try:
        payload = load_json_payload(request.body)
        if not isinstance(payload, dict):
            fail()
        table = None
        channel = 'qr' if token else 'online'
        if token:
            table = get_object_or_404(DiningTable.objects.select_related('branch__restaurant__subscription'), public_token=token)
            branch = table.branch
            if not table.active or table.archived or str(table.session_token) != payload.get('session_token'):
                return JsonResponse({'ok': False}, status=409)
        else:
            branch = get_object_or_404(Branch.objects.select_related('restaurant__subscription'),
                                      pk=branch_id, restaurant__slug=slug)
        restaurant = branch.restaurant
        subscription = getattr(restaurant, 'subscription', None)
        available = (restaurant.active and branch.active and not branch.archived and branch.accepting_orders
                     and subscription is not None and subscription.valid())
        if channel == 'online':
            available = (available and restaurant.online_ordering_enabled and has_feature(subscription, 'online_ordering')
                         and (restaurant.online_pickup_enabled or restaurant.online_delivery_enabled))
        if not available:
            return JsonResponse({'ok': False}, status=409)
        scope_key = context_key(branch, channel, payload.get('context'), table)
        if payload.get('kind') in ('shown', 'added'):
            record_event(branch, scope_key, payload)
            return JsonResponse({'ok': True})
        if payload.get('kind', 'select') != 'select':
            fail()
        ids = cart_ids(branch, payload.get('item_ids'))
        candidates, version = select_recommendations(branch, ids)
        exposure = None
        try:
            exposure = exposure_for(branch, channel, scope_key, ids, candidates, version)
        except Exception:
            logger.warning('Recommendation exposure recording unavailable for branch %s', branch.pk)
        items = []
        for item in candidates:
            data = serialize_menu_item(item)
            price, starting = minimum_price(item)
            data.update(photo=menu_photo(item.name, data['image']), minimum_price=str(price), starting_price=starting)
            items.append(data)
        return JsonResponse({'ok': True, 'items': items, 'exposure': str(exposure.pk) if exposure else None})
    except ValidationError:
        return JsonResponse({'ok': False, 'error': localized('حدّث السلة وحاول مجددًا.', 'Refresh the cart and try again.')}, status=400)
