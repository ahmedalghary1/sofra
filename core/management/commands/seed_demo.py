"""Create repeatable, local-only demo accounts and restaurant data."""

import hashlib
import ipaddress
import secrets
import uuid
from datetime import timedelta
from decimal import Decimal
from urllib.parse import urlparse

from django.conf import settings
from django.contrib.auth.models import User
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from core.models import (
    AuditLog, Branch, Category, DiningTable, KitchenStation, Membership, MenuItem,
    MenuSize, ModifierGroup, ModifierOption, Order, OrderHistory,
    OrderLinePrepHistory, OwnerEmailVerification, Plan, Restaurant, ServiceCall,
    Subscription, SubscriptionPayment,
)
from core.services import (
    advance_call, advance_order, advance_order_line, create_order,
    create_service_call, record_manual_payment,
)


DEMO_SLUG = 'demo-sofra'
DEMO_MARKER = 'SOFRA_LOCAL_DEMO'
ACCOUNTS = {
    'مشغل المنصة': ('demo-admin@example.com', 'مشغل العرض'),
    'مالك المطعم': ('demo-owner@example.com', 'مالك المطعم'),
    'المطبخ': ('demo-kitchen@example.com', 'موظف المطبخ'),
    'الويتر': ('demo-waiter@example.com', 'موظف الخدمة'),
}


class Command(BaseCommand):
    help = 'Create an isolated local demo; passwords are generated and printed once per run.'

    def add_arguments(self, parser):
        parser.add_argument('--base-url', default='http://127.0.0.1:8000',
                            help='URL used only for the printed demo links.')

    def handle(self, *args, **options):
        if not settings.DEBUG:
            raise CommandError('بيانات العرض مسموحة في وضع التطوير فقط (DJANGO_DEBUG=1).')
        base_url = options['base_url'].rstrip('/')
        parsed = urlparse(base_url)
        if parsed.scheme != 'http' or not parsed.hostname or parsed.path or parsed.query or parsed.fragment:
            raise CommandError('استخدم رابطًا محليًا مثل http://127.0.0.1:8000 أو عنوان الشبكة الداخلية.')
        private_networks = tuple(ipaddress.ip_network(network) for network in (
            '10.0.0.0/8', '172.16.0.0/12', '192.168.0.0/16'))
        try:
            local_host = parsed.hostname == 'localhost' or (
                ipaddress.ip_address(parsed.hostname) == ipaddress.ip_address('127.0.0.1') or
                any(ipaddress.ip_address(parsed.hostname) in network for network in private_networks))
        except ValueError:
            local_host = parsed.hostname == 'localhost'
        if not local_host:
            raise CommandError('رابط العرض يجب أن يكون محليًا أو من الشبكة الداخلية.')

        passwords = {label: secrets.token_hex(9) for label in ACCOUNTS}
        with transaction.atomic():
            restaurant = Restaurant.objects.filter(slug=DEMO_SLUG).first()
            if restaurant and not AuditLog.objects.filter(restaurant=restaurant, action='demo.seed').exists():
                raise CommandError('المعرّف demo-sofra مستخدم لمطعم غير موسوم كبيانات عرض.')
            if restaurant is None:
                restaurant = Restaurant.objects.create(
                    slug=DEMO_SLUG, name='مطعم سفرة التجريبي',
                    description='وجبات ومشروبات تجريبية لتوضيح رحلة الطلب من الطاولة إلى المطبخ.')
                AuditLog.objects.create(restaurant=restaurant, action='demo.seed', detail='local demo v1')
            restaurant.active = True
            restaurant.save(update_fields=['active'])

            users = {}
            for label, (username, first_name) in ACCOUNTS.items():
                user = User.objects.filter(username=username).first()
                if user and (user.last_name != DEMO_MARKER or
                             user.memberships.exclude(restaurant=restaurant).exists()):
                    raise CommandError(f'الحساب {username} موجود خارج بيانات العرض. لم تُغيَّر كلمة مروره.')
                if user is None:
                    user = User(username=username, email=username, first_name=first_name,
                                last_name=DEMO_MARKER)
                user.is_active = True
                user.is_staff = label == 'مشغل المنصة'
                user.is_superuser = label == 'مشغل المنصة'
                user.set_password(passwords[label])
                user.save()
                users[label] = user

            plan, _ = Plan.objects.get_or_create(name='باقة العرض المحلي', defaults={
                'price_monthly': Decimal('500.00'), 'max_branches': 2, 'max_tables': 10,
                'max_items': 50, 'max_staff': 6, 'max_stations': 2,
            })
            if plan.max_branches < 2 or plan.max_stations < 2 or plan.max_staff < 3:
                raise CommandError('باقة العرض المحلي موجودة بحدود أقل من بيانات العرض المطلوبة.')
            subscription, _ = Subscription.objects.get_or_create(restaurant=restaurant, defaults={
                'plan': plan, 'status': 'active', 'ends_at': timezone.now()+timedelta(days=30),
            })
            subscription.plan = plan
            subscription.status = 'active'
            subscription.ends_at = timezone.now()+timedelta(days=30)
            subscription.save(update_fields=['plan', 'status', 'ends_at'])

            main, _ = Branch.objects.get_or_create(restaurant=restaurant, name='الفرع الرئيسي', defaults={
                'address': 'شارع التجربة، القاهرة', 'phone': '01000000000',
                'opening_hours': 'يوميًا 10:00 ص – 11:00 م', 'show_contact_on_menu': True,
            })
            second, _ = Branch.objects.get_or_create(restaurant=restaurant, name='الفرع الثاني')
            for branch in (main, second):
                branch.active = True
                branch.accepting_orders = True
                branch.save(update_fields=['active', 'accepting_orders'])

            for label, role in (('مالك المطعم', 'owner'), ('المطبخ', 'kitchen'), ('الويتر', 'waiter')):
                membership, _ = Membership.objects.get_or_create(
                    user=users[label], restaurant=restaurant, defaults={'role': role})
                membership.role = role
                membership.active = True
                membership.save(update_fields=['role', 'active'])
                if role != 'owner':
                    membership.branches.set([main])
            OwnerEmailVerification.objects.get_or_create(user=users['مالك المطعم'], defaults={
                'token_hash': hashlib.sha256(secrets.token_bytes(32)).hexdigest(),
                'expires_at': timezone.now()+timedelta(days=1), 'verified_at': timezone.now(),
            })

            kitchen, _ = KitchenStation.objects.get_or_create(branch=main, name='المطبخ')
            drinks, _ = KitchenStation.objects.get_or_create(branch=main, name='المشروبات')
            for station in (kitchen, drinks):
                if not station.active:
                    station.active = True
                    station.save(update_fields=['active'])
            meals, _ = Category.objects.get_or_create(branch=main, name='الوجبات', defaults={'sort_order': 1})
            beverages, _ = Category.objects.get_or_create(branch=main, name='المشروبات', defaults={'sort_order': 2})
            for category in (meals, beverages):
                if not category.visible:
                    category.visible = True
                    category.save(update_fields=['visible'])

            items = {}
            for name, category, station, price, description in (
                ('برجر سفرة', meals, kitchen, '175.00', 'برجر مشوي مع خس وصوص البيت.'),
                ('مكرونة ألفريدو', meals, kitchen, '145.00', 'مكرونة بصلصة كريمية وفطر.'),
                ('بطاطس مقرمشة', meals, kitchen, '55.00', 'بطاطس ذهبية مع توابل خفيفة.'),
                ('عصير مانجو', beverages, drinks, '65.00', 'مانجو طازجة تقدم باردة.'),
                ('قهوة لاتيه', beverages, drinks, '70.00', 'إسبريسو بالحليب المبخر.'),
            ):
                item, _ = MenuItem.objects.get_or_create(category=category, name=name, defaults={
                    'station': station, 'price': Decimal(price), 'description': description,
                })
                item.station = station
                item.available = True
                item.save(update_fields=['station', 'available'])
                items[name] = item
            for name, price in (('عادي', '175.00'), ('كبير', '220.00')):
                MenuSize.objects.get_or_create(item=items['برجر سفرة'], name=name,
                                               defaults={'price': Decimal(price)})
            for name, price in (('عادي', '65.00'), ('كبير', '85.00')):
                MenuSize.objects.get_or_create(item=items['عصير مانجو'], name=name,
                                               defaults={'price': Decimal(price)})
            extras, _ = ModifierGroup.objects.get_or_create(
                item=items['برجر سفرة'], name='إضافات البرجر',
                defaults={'min_select': 0, 'max_select': 2})
            cheese, _ = ModifierOption.objects.get_or_create(
                group=extras, name='جبنة إضافية', defaults={'price': Decimal('20.00'), 'sort_order': 1})
            ModifierOption.objects.get_or_create(
                group=extras, name='صوص إضافي', defaults={'price': Decimal('10.00'), 'sort_order': 2})
            sugar, _ = ModifierGroup.objects.get_or_create(
                item=items['قهوة لاتيه'], name='السكر',
                defaults={'min_select': 0, 'max_select': 1})
            ModifierOption.objects.get_or_create(
                group=sugar, name='بدون سكر', defaults={'price': Decimal('0.00')})

            tables = [DiningTable.objects.get_or_create(branch=main, name=f'طاولة {index:02d}')[0]
                      for index in range(1, 4)]
            for table in tables:
                if not table.active:
                    table.active = True
                    table.save(update_fields=['active'])
            second_category, _ = Category.objects.get_or_create(branch=second, name='مشروبات الفرع الثاني')
            MenuItem.objects.get_or_create(category=second_category, name='قهوة سريعة', defaults={
                'price': Decimal('50.00'), 'description': 'قهوة تجريبية خاصة بالفرع الثاني.'})
            DiningTable.objects.get_or_create(branch=second, name='طاولة 01')

            examples = (
                (tables[0], [
                    {'item_id': items['برجر سفرة'].pk, 'quantity': 1, 'options': [cheese.pk]},
                    {'item_id': items['عصير مانجو'].pk, 'quantity': 1,
                     'size_id': MenuSize.objects.get(item=items['عصير مانجو'], name='كبير').pk, 'options': []},
                ]),
                (tables[1], [
                    {'item_id': items['مكرونة ألفريدو'].pk, 'quantity': 1, 'options': []},
                    {'item_id': items['قهوة لاتيه'].pk, 'quantity': 2, 'options': []},
                ]),
                (tables[2], [
                    {'item_id': items['برجر سفرة'].pk, 'quantity': 2, 'options': []},
                    {'item_id': items['بطاطس مقرمشة'].pk, 'quantity': 1, 'options': []},
                ]),
            )
            for index, (table, lines) in enumerate(examples, start=1):
                key = uuid.uuid5(uuid.NAMESPACE_DNS, f'sofra-local-demo-order-{index}')
                if Order.objects.filter(table=table, idempotency_key=key).exists():
                    continue
                order = create_order(table, {'idempotency_key': str(key), 'lines': lines,
                                             'note': 'طلب تجريبي مكتمل'}, users['مالك المطعم'])
                advance_order(order, users['المطبخ'], 'accepted')
                advance_order(order, users['المطبخ'], 'preparing')
                for line in order.lines.all():
                    advance_order_line(line, users['المطبخ'], 'preparing')
                    advance_order_line(line, users['المطبخ'], 'ready')
                advance_order(order, users['المطبخ'], 'ready')
                advance_order(order, users['الويتر'], 'served')
                record_manual_payment(order, users['مالك المطعم'])
                self._backdate_order(order, timezone.now()-timedelta(hours=4-index))

            if not ServiceCall.objects.filter(table=tables[2], kind='waiter').exists():
                call = create_service_call(tables[2], 'waiter', session_token=tables[2].session_token)
                for status in ('acknowledged', 'in_progress', 'done'):
                    advance_call(call, users['الويتر'], status)
                base_time = timezone.now()-timedelta(hours=1)
                ServiceCall.objects.filter(pk=call.pk).update(
                    created_at=base_time, acknowledged_at=base_time+timedelta(minutes=2),
                    done_at=base_time+timedelta(minutes=5))

            SubscriptionPayment.objects.get_or_create(
                restaurant=restaurant, reference='DEMO-LOCAL-001', defaults={
                    'amount': Decimal('500.00'), 'currency': 'EGP',
                    'period_start': timezone.localdate(),
                    'period_end': timezone.localdate()+timedelta(days=30),
                    'method': 'cash', 'recorded_by': users['مشغل المنصة'],
                })

        self.stdout.write(self.style.SUCCESS('تم تجهيز بيانات العرض المحلية.'))
        self.stdout.write('كلمات المرور الجديدة لهذا التشغيل (احفظها خارج المستودع):')
        for label, (username, _) in ACCOUNTS.items():
            self.stdout.write(f'{label}: {username} | كلمة المرور: {passwords[label]}')
        self.stdout.write(f'لوحة المنصة: {base_url}/platform/')
        self.stdout.write(f'دخول المطعم والمطبخ: {base_url}/login/')
        self.stdout.write(f'مطعم العرض: {base_url}/app/{DEMO_SLUG}/')
        self.stdout.write(f'شاشة المطبخ: {base_url}/app/{DEMO_SLUG}/kitchen/')
        self.stdout.write(f'شاشة الخدمة: {base_url}/app/{DEMO_SLUG}/calls/')
        self.stdout.write(f'منيو الضيف (بلا حساب): {base_url}/t/{tables[0].public_token}/')
        self.stdout.write('شاشة المطبخ: افتحها بحساب المطبخ في متصفح مستقل؛ الطلبات التاريخية مكتملة.')
        self.stdout.write('عند إعادة تشغيل الملف تتغير كلمات مرور حسابات العرض فقط، وتبقى الطلبات التي أنشأتها.')

    @staticmethod
    def _backdate_order(order, created_at):
        Order.objects.filter(pk=order.pk).update(
            created_at=created_at, updated_at=created_at+timedelta(minutes=12),
            paid_at=created_at+timedelta(minutes=15))
        for status, minutes in (('new', 0), ('accepted', 1), ('preparing', 2),
                                ('ready', 9), ('served', 12)):
            OrderHistory.objects.filter(order=order, new_status=status).update(
                created_at=created_at+timedelta(minutes=minutes))
        OrderLinePrepHistory.objects.filter(line__order=order, new_status='preparing').update(
            created_at=created_at+timedelta(minutes=3))
        OrderLinePrepHistory.objects.filter(line__order=order, new_status='ready').update(
            created_at=created_at+timedelta(minutes=8))
