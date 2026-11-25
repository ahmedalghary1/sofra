"""Financial activity uses collection timestamps, independently of order creation."""
from decimal import Decimal
import csv
import io

from django.db.models import Sum

from .models import CollectionCorrection
from django.http import StreamingHttpResponse


def collection_activity(orders, start=None, end=None):
    current = orders.filter(payment_status='paid', paid_at__isnull=False)
    originals = CollectionCorrection.objects.filter(order__in=orders)
    corrections = CollectionCorrection.objects.filter(order__in=orders)
    if start is not None:
        current = current.filter(paid_at__gte=start, paid_at__lt=end)
        originals = originals.filter(original_paid_at__gte=start, original_paid_at__lt=end)
        corrections = corrections.filter(created_at__gte=start, created_at__lt=end)
    received = (current.aggregate(value=Sum('total'))['value'] or Decimal('0'))
    received += (originals.aggregate(value=Sum('amount'))['value'] or Decimal('0'))
    corrected = corrections.aggregate(value=Sum('amount'))['value'] or Decimal('0')
    by_branch = {}
    for rows, key, field, sign in ((current, 'branch_id', 'total', 1),
                                 (originals, 'order__branch_id', 'amount', 1),
                                 (corrections, 'order__branch_id', 'amount', -1)):
        for row in rows.order_by().values(key).annotate(value=Sum(field)):
            branch_id = row[key]
            by_branch[branch_id] = by_branch.get(branch_id, Decimal('0')) + sign * row['value']
    return {'received': received, 'corrected': corrected, 'net': received-corrected,
            'by_branch': by_branch, 'current': current, 'originals': originals, 'corrections': corrections}


def csv_safe(value):
    value = str(value)
    return "'" + value if value.lstrip().startswith(('=', '+', '-', '@')) else value


def collection_csv(activity, english=False):
    headers = (['Order', 'Branch', 'Table', 'Entry', 'Amount EGP', 'Recorded at', 'Reason'] if english else
               ['الطلب', 'الفرع', 'الطاولة', 'القيد', 'المبلغ بالجنيه', 'وقت التسجيل', 'السبب'])
    def rows():
        yield '\ufeff'
        buffer = io.StringIO()
        writer = csv.writer(buffer)
        writer.writerow(headers)
        yield buffer.getvalue()
        streams = ((activity['current'].select_related('branch', 'table').order_by('pk').iterator(), 'current'),
                   (activity['originals'].select_related('order__branch', 'order__table').order_by('pk').iterator(), 'original'),
                   (activity['corrections'].select_related('order__branch', 'order__table').order_by('pk').iterator(), 'correction'))
        for stream, kind in streams:
            for record in stream:
                order = record if kind == 'current' else record.order
                amount = order.total if kind == 'current' else record.amount
                timestamp = order.paid_at if kind == 'current' else (record.original_paid_at if kind == 'original' else record.created_at)
                label = ('تصحيح قيد' if not english else 'Entry correction') if kind == 'correction' else ('تحصيل يدوي' if not english else 'Manual collection')
                buffer.seek(0)
                buffer.truncate(0)
                writer.writerow([order.pk, csv_safe(order.branch.name), csv_safe(order.table.name), label,
                    -amount if kind == 'correction' else amount, timestamp.isoformat(),
                    csv_safe(record.reason) if kind == 'correction' else ''])
                yield buffer.getvalue()
    response = StreamingHttpResponse(rows(), content_type='text/csv; charset=utf-8')
    response['Content-Disposition'] = 'attachment; filename="collections.csv"'
    response['Cache-Control'] = 'no-store'
    return response
