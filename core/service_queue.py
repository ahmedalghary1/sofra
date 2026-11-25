"""Branch-scoped service queue presentation shared by the page and live feed."""
from datetime import timedelta
import hashlib
import json
from django.core.paginator import Paginator
from django.utils import timezone
from .models import ServiceCall

ACTIVE_CALLS = ('open', 'acknowledged', 'in_progress')


def service_call_page(branch, queue='all', page=None):
    calls = ServiceCall.objects.filter(table__branch=branch).select_related('table', 'assigned_to')
    if queue == 'open':
        calls = calls.filter(status__in=ACTIVE_CALLS)
    elif queue == 'bill':
        calls = calls.filter(kind='bill')
    else:
        queue = 'all'
    calls = Paginator(calls.order_by('-created_at', '-pk'), 30).get_page(page)
    now = timezone.now()
    for call in calls:
        limit = branch.bill_response_minutes if call.kind == 'bill' else branch.waiter_response_minutes
        call.response_overdue = (call.acknowledged_at or now) > call.created_at + timedelta(minutes=limit)
        call.awaiting_overdue = call.status == 'open' and call.response_overdue
    signature = hashlib.sha256(json.dumps([branch.pk, queue, calls.number, calls.paginator.count,
        int(now.timestamp() // 60), [(call.pk, call.status, call.assigned_to_id, call.updated_at.isoformat(),
                                    call.awaiting_overdue, call.response_overdue) for call in calls]]).encode()).hexdigest()
    return {'calls': calls, 'queue': queue, 'overdue_count': sum(call.awaiting_overdue for call in calls),
            'signature': signature}


def public_call_states(table):
    return {call.kind: {'status': call.status} for call in
            ServiceCall.objects.filter(table=table, status__in=ACTIVE_CALLS)}
