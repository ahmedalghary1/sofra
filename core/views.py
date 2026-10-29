import csv
import hashlib
import io
import json
import secrets
from datetime import timedelta
from datetime import datetime, time
from decimal import Decimal, InvalidOperation
from PIL import Image, UnidentifiedImageError
from django.contrib import messages
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.views import PasswordResetView
from django.contrib.auth.models import User
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.core.exceptions import PermissionDenied
from django.core.validators import validate_email
from django.db import IntegrityError, transaction
from django.db.models import Avg, Count, F, Q, Sum
from django.core.paginator import Paginator
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST
from .models import Branch, Category, DiningTable, LoginAttempt, Membership, MenuItem, ModifierGroup, ModifierOption, Order, OrderHistory, OrderLine, Plan, Restaurant, ServiceCall, Subscription
from .services import advance_call, advance_order, audit, create_order, create_service_call, record_manual_payment
from .limits import check_activation_limit, check_creation_limit

def error_text(exc): return ' '.join(exc.messages) if hasattr(exc, 'messages') else str(exc)
def health(request): return JsonResponse({'status': 'ok'})

def home(request):
    if request.user.is_authenticated:
        if request.user.is_superuser:
            return redirect('platform_dashboard')
        membership = request.user.memberships.filter(active=True).select_related('restaurant').first()
        if membership: return redirect('dashboard', slug=membership.restaurant.slug)
    return render(request, 'home.html')

def register(request):
    if request.method == 'POST':
        name = request.POST.get('name', '').strip()
        email = request.POST.get('email', '').strip().lower()
        restaurant_name = request.POST.get('restaurant', '').strip()
        password = request.POST.get('password', '')
        if not all([name, email, restaurant_name]):
            messages.error(request, 'أكمل جميع البيانات.')
        elif User.objects.filter(username=email).exists():
            messages.error(request, 'هذا البريد مستخدم بالفعل.')
        else:
            try:
                validate_email(email)
                validate_password(password, user=User(username=email, email=email, first_name=name))
            except ValidationError as exc:
                messages.error(request, error_text(exc))
                return render(request, 'auth.html', {'mode': 'register'})
            with transaction.atomic():
                user = User.objects.create_user(username=email, email=email, password=password, first_name=name)
                restaurant = Restaurant.objects.create(name=restaurant_name, slug='rest-' + secrets.token_hex(5))
                Branch.objects.create(restaurant=restaurant)
                Membership.objects.create(user=user, restaurant=restaurant, role='owner')
                plan, _ = Plan.objects.get_or_create(name='تجربة', defaults={'max_tables': 10, 'max_items': 50, 'max_staff': 5})
                Subscription.objects.create(restaurant=restaurant, plan=plan, ends_at=timezone.now()+timedelta(days=14))
            login(request, user)
            return redirect('dashboard', slug=restaurant.slug)
    return render(request, 'auth.html', {'mode': 'register'})

def login_view(request):
    if request.method == 'POST':
        email = request.POST.get('email', '').strip().lower()
        key = hashlib.sha256(f"{request.META.get('REMOTE_ADDR','')}:{email}".encode()).hexdigest()
        attempts = LoginAttempt.objects.filter(key=key, created_at__gte=timezone.now()-timedelta(minutes=15))
        if attempts.count() >= 8:
            messages.error(request, 'محاولات كثيرة. انتظر 15 دقيقة ثم حاول مرة أخرى.')
            return render(request, 'auth.html', {'mode': 'login'}, status=429)
        user = authenticate(request, username=email, password=request.POST.get('password', ''))
        if user and (user.is_superuser or user.memberships.filter(active=True).exists()):
            LoginAttempt.objects.filter(key=key).delete()
            login(request, user)
            return redirect('home')
        LoginAttempt.objects.create(key=key)
        messages.error(request, 'البريد أو كلمة المرور غير صحيحة.')
    return render(request, 'auth.html', {'mode': 'login'})

class LimitedPasswordResetView(PasswordResetView):
    def form_valid(self, form):
        email = form.cleaned_data['email'].strip().lower()
        key = hashlib.sha256(f"reset:{self.request.META.get('REMOTE_ADDR','')}:{email}".encode()).hexdigest()
        if LoginAttempt.objects.filter(key=key, created_at__gte=timezone.now()-timedelta(hours=1)).count() >= 5:
            return redirect('password_reset_done')
        LoginAttempt.objects.create(key=key)
        return super().form_valid(form)

@require_POST
def logout_view(request):
    logout(request)
    return redirect('home')

def platform_scope(request):
    if not request.user.is_authenticated:
        return redirect('login')
    if not request.user.is_superuser:
        return render(request, '403.html', status=403)
    return None

def platform_dashboard(request):
    denied = platform_scope(request)
    if denied: return denied
    query = request.GET.get('q','').strip()[:80]
    restaurants = Restaurant.objects.select_related('subscription__plan').annotate(
        branch_count=Count('branches', distinct=True), member_count=Count('memberships', distinct=True)).order_by('-created_at')
    if query:
        restaurants = restaurants.filter(Q(name__icontains=query) | Q(slug__icontains=query))
    page = Paginator(restaurants, 25).get_page(request.GET.get('page'))
    return render(request, 'platform.html', {'restaurants': page, 'plans': Plan.objects.all(), 'query': query,
        'restaurant_total': Restaurant.objects.count(), 'active_total': Restaurant.objects.filter(active=True).count()})

@require_POST
def platform_update_restaurant(request, restaurant_id):
    denied = platform_scope(request)
    if denied: return denied
    restaurant = get_object_or_404(Restaurant, pk=restaurant_id)
    reason = request.POST.get('reason','').strip()[:120]
    if not reason:
        messages.error(request, 'اكتب سبب التغيير قبل الحفظ.')
        return redirect('platform_dashboard')
    action = request.POST.get('action')
    try:
        with transaction.atomic():
            restaurant = Restaurant.objects.select_for_update().get(pk=restaurant.pk)
            if action == 'toggle':
                restaurant.active = not restaurant.active
                restaurant.save(update_fields=['active'])
                audit(restaurant, request.user, 'platform.restaurant.toggle',
                      f'active={restaurant.active}; reason={reason}')
            elif action == 'subscription':
                subscription = Subscription.objects.select_for_update().get(restaurant=restaurant)
                plan = get_object_or_404(Plan, pk=request.POST.get('plan_id'))
                status = request.POST.get('status')
                if status not in ('trial','active','expired'):
                    raise ValidationError('حالة الاشتراك غير صالحة.')
                try:
                    end_date = datetime.fromisoformat(request.POST.get('ends_at','')).date()
                except (ValueError, TypeError):
                    raise ValidationError('تاريخ الانتهاء غير صالح.')
                old = f'{subscription.plan_id}/{subscription.status}/{subscription.ends_at.date()}'
                subscription.plan = plan
                subscription.status = status
                subscription.ends_at = timezone.make_aware(datetime.combine(end_date, time.max))
                subscription.save(update_fields=['plan','status','ends_at'])
                audit(restaurant, request.user, 'platform.subscription.update',
                      f'{old} → {plan.pk}/{status}/{end_date}; reason={reason}')
            else:
                raise ValidationError('إجراء غير معروف.')
        messages.success(request, 'تم حفظ التغيير وتسجيل السبب.')
    except ValidationError as exc:
        messages.error(request, error_text(exc))
    return redirect('platform_dashboard')

def scope(request, slug, roles=None):
    if not request.user.is_authenticated: return None, None, redirect('login')
    restaurant = get_object_or_404(Restaurant, slug=slug)
    membership = Membership.objects.filter(user=request.user, restaurant=restaurant, active=True).first()
    if not membership or (roles and membership.role not in roles):
        return None, None, render(request, '403.html', status=403)
    if not restaurant.active and request.method not in ('GET','HEAD') and request.resolver_match.url_name != 'select_branch':
        return None, None, render(request, '403.html', status=403)
    return restaurant, membership, None

def branch_for(request, restaurant, membership):
    branches = Branch.objects.filter(restaurant=restaurant)
    if membership.role not in ('owner','manager'):
        branches = branches.filter(active=True, staff_memberships=membership)
    selected = request.session.get(f'safra_branch_{restaurant.pk}')
    if selected:
        branch = branches.filter(pk=selected).first()
        if branch and (branch.active or membership.role in ('owner','manager')):
            return branch
    branch = branches.filter(active=True).first()
    if not branch and membership.role in ('owner','manager'):
        branch = branches.first()
    if not branch:
        raise PermissionDenied('لا يوجد فرع متاح لهذا الحساب.')
    return branch

def ensure_active_subscription(restaurant):
    if not restaurant.subscription.valid():
        raise ValidationError('انتهت فترة التجربة أو الاشتراك. البيانات متاحة للعرض، لكن الإضافة متوقفة.')

def validate_menu_image(image):
    if not image:
        return
    if image.size > 3_000_000 or image.content_type not in ('image/jpeg','image/png','image/webp'):
        raise ValidationError('الصورة يجب أن تكون JPG أو PNG أو WebP وأقل من 3 ميجابايت.')
    try:
        with Image.open(image) as picture:
            if picture.format not in ('JPEG','PNG','WEBP') or picture.width > 5000 or picture.height > 5000:
                raise ValidationError('صيغة الصورة أو أبعادها غير مدعومة.')
            picture.verify()
        image.seek(0)
    except (UnidentifiedImageError, OSError):
        raise ValidationError('ملف الصورة غير صالح.')

def shell(request, restaurant, membership, page, **extra):
    branches = Branch.objects.filter(restaurant=restaurant, active=True)
    if membership.role not in ('owner','manager'):
        branches = branches.filter(staff_memberships=membership)
    ctx = {'restaurant': restaurant, 'membership': membership, 'branch': branch_for(request, restaurant, membership),
           'available_branches': branches, 'page': page}
    ctx.update(extra)
    return render(request, f'{page}.html', ctx)

def dashboard(request, slug):
    restaurant, membership, denied = scope(request, slug)
    if denied: return denied
    branch = branch_for(request, restaurant, membership)
    orders = Order.objects.filter(branch=branch)
    return shell(request, restaurant, membership, 'dashboard',
        recent=orders.select_related('table').prefetch_related('lines')[:6],
        today_orders=orders.filter(created_at__date=timezone.localdate()).count(),
        open_orders=orders.exclude(status__in=['served','cancelled']).count(),
        open_calls=ServiceCall.objects.filter(table__branch=branch, status__in=['open','acknowledged','in_progress']).count(),
        tables_count=branch.tables.count(), items_count=MenuItem.objects.filter(category__branch=branch).count())

def manage_menu(request, slug):
    restaurant, membership, denied = scope(request, slug, ['owner','manager'])
    if denied: return denied
    branch = branch_for(request, restaurant, membership)
    if request.method == 'POST':
        action = request.POST.get('action')
        try:
            ensure_active_subscription(restaurant)
            if action == 'category':
                name = request.POST.get('name','').strip()[:80]
                if not name: raise ValidationError('أدخل اسم التصنيف.')
                Category.objects.create(branch=branch, name=name)
                audit(restaurant, request.user, 'category.create', name)
            elif action == 'category_toggle':
                category = get_object_or_404(Category, pk=request.POST.get('category_id'), branch=branch)
                category.visible = not category.visible
                category.save(update_fields=['visible'])
                audit(restaurant, request.user, 'category.toggle', str(category.pk))
            elif action == 'item':
                category = get_object_or_404(Category, pk=request.POST.get('category'), branch=branch)
                price = Decimal(request.POST.get('price', ''))
                if price < 0 or price > 9999999: raise ValidationError('السعر غير صالح.')
                name = request.POST.get('name','').strip()[:120]
                if not name: raise ValidationError('أدخل اسم الصنف.')
                image = request.FILES.get('image')
                validate_menu_image(image)
                with transaction.atomic():
                    check_creation_limit(restaurant, 'item', branch)
                    item = MenuItem.objects.create(category=category, name=name, description=request.POST.get('description','').strip()[:300], price=price, image=image)
                audit(restaurant, request.user, 'item.create', str(item.pk))
            elif action == 'item_edit':
                item = get_object_or_404(MenuItem, pk=request.POST.get('item_id'), category__branch=branch)
                category = get_object_or_404(Category, pk=request.POST.get('category'), branch=branch)
                name = request.POST.get('name', '').strip()[:120]
                if not name: raise ValidationError('أدخل اسم الصنف.')
                price = Decimal(request.POST.get('price', ''))
                if price < 0 or price > 9999999: raise ValidationError('السعر غير صالح.')
                image = request.FILES.get('image')
                validate_menu_image(image)
                item.name = name
                item.description = request.POST.get('description', '').strip()[:300]
                item.price = price
                item.category = category
                if image: item.image = image
                item.save()
                audit(restaurant, request.user, 'item.edit', str(item.pk))
            elif action in ('toggle','price'):
                item = get_object_or_404(MenuItem, pk=request.POST.get('item_id'), category__branch=branch)
                if action == 'toggle':
                    with transaction.atomic():
                        if not item.available:
                            check_activation_limit(restaurant, 'item', branch)
                        item.available = not item.available
                        item.save(update_fields=['available'])
                else:
                    item.price = Decimal(request.POST.get('price',''))
                    if item.price < 0 or item.price > 9999999: raise ValidationError('السعر غير صالح.')
                    item.save(update_fields=['price'])
                audit(restaurant, request.user, 'item.update', str(item.pk))
            elif action == 'modifier':
                item = get_object_or_404(MenuItem, pk=request.POST.get('item_id'), category__branch=branch)
                group_name = request.POST.get('group','').strip()[:80]
                option_name = request.POST.get('option','').strip()[:80]
                if not group_name or not option_name: raise ValidationError('أدخل المجموعة والإضافة.')
                minimum = int(request.POST.get('min_select','0'))
                maximum = int(request.POST.get('max_select','5'))
                if not 0 <= minimum <= maximum <= 20:
                    raise ValidationError('حدود اختيار الإضافات غير صالحة.')
                group, _ = ModifierGroup.objects.get_or_create(item=item, name=group_name, defaults={'min_select': minimum, 'max_select': maximum})
                if (group.min_select, group.max_select) != (minimum, maximum):
                    group.min_select, group.max_select = minimum, maximum
                    group.save(update_fields=['min_select','max_select'])
                price = Decimal(request.POST.get('price','0'))
                if price < 0 or price > 9999999: raise ValidationError('السعر غير صالح.')
                ModifierOption.objects.create(group=group, name=option_name, price=price)
                audit(restaurant, request.user, 'modifier.create', str(item.pk))
            elif action == 'modifier_toggle':
                option = get_object_or_404(ModifierOption, pk=request.POST.get('option_id'), group__item__category__branch=branch)
                option.available = not option.available
                option.save(update_fields=['available'])
                audit(restaurant, request.user, 'modifier.toggle', str(option.pk))
            else: raise ValidationError('إجراء غير معروف.')
            messages.success(request, 'تم حفظ التغيير بنجاح.')
        except (ValidationError, InvalidOperation, IntegrityError, ValueError) as exc: messages.error(request, error_text(exc))
        return redirect('manage_menu', slug=slug)
    categories = Category.objects.filter(branch=branch).prefetch_related('items__modifier_groups__options')
    return shell(request, restaurant, membership, 'menu_manage', categories=categories,
                 item_count=MenuItem.objects.filter(category__branch=branch).count(), limit=restaurant.subscription.plan.max_items)

def manage_tables(request, slug):
    restaurant, membership, denied = scope(request, slug, ['owner','manager'])
    if denied: return denied
    branch = branch_for(request, restaurant, membership)
    if request.method == 'POST':
        try:
            ensure_active_subscription(restaurant)
            action = request.POST.get('action')
            if action == 'create':
                name = request.POST.get('name','').strip()[:40]
                if not name: raise ValidationError('أدخل اسم الطاولة.')
                with transaction.atomic():
                    check_creation_limit(restaurant, 'table', branch)
                    DiningTable.objects.create(branch=branch, name=name)
                audit(restaurant, request.user, 'table.create', name)
            else:
                table = get_object_or_404(DiningTable, pk=request.POST.get('table_id'), branch=branch)
                if action == 'toggle':
                    with transaction.atomic():
                        if not table.active:
                            check_activation_limit(restaurant, 'table', branch)
                        table.active = not table.active
                        table.save(update_fields=['active'])
                elif action == 'rotate':
                    table.public_token = secrets.token_urlsafe(24)
                    table.save(update_fields=['public_token'])
                else: raise ValidationError('إجراء غير معروف.')
                audit(restaurant, request.user, f'table.{action}', str(table.pk))
            messages.success(request, 'تم حفظ التغيير بنجاح.')
        except (ValidationError, IntegrityError) as exc: messages.error(request, error_text(exc))
        return redirect('manage_tables', slug=slug)
    return shell(request, restaurant, membership, 'tables', tables=branch.tables.all(), limit=restaurant.subscription.plan.max_tables)

def table_qr(request, slug, table_id):
    restaurant, membership, denied = scope(request, slug, ['owner','manager'])
    if denied: return denied
    import qrcode
    branch = branch_for(request, restaurant, membership)
    table = get_object_or_404(DiningTable, pk=table_id, branch=branch)
    buffer = io.BytesIO()
    qrcode.make(request.build_absolute_uri(reverse('public_menu', args=[table.public_token]))).save(buffer, format='PNG')
    response = HttpResponse(buffer.getvalue(), content_type='image/png')
    response['Content-Disposition'] = f'attachment; filename="table-{table.id}-qr.png"'
    return response

def manage_orders(request, slug):
    restaurant, membership, denied = scope(request, slug)
    if denied: return denied
    branch = branch_for(request, restaurant, membership)
    if request.method == 'POST' and membership.role not in ('owner','manager','cashier','waiter'):
        return render(request, '403.html', status=403)
    if request.method == 'POST':
        try:
            table = get_object_or_404(DiningTable, pk=request.POST.get('table_id'), branch=branch)
            order = create_order(table, json.loads(request.POST.get('payload','{}')), request.user)
            messages.success(request, f'تم إنشاء الطلب #{order.pk}.')
        except (ValidationError, json.JSONDecodeError) as exc: messages.error(request, error_text(exc))
        return redirect('manage_orders', slug=slug)
    return shell(request, restaurant, membership, 'orders',
        orders=Order.objects.filter(branch=branch).select_related('table').prefetch_related('lines')[:100],
        tables=branch.tables.filter(active=True), items=MenuItem.objects.filter(category__branch=branch,available=True))

def staff_order(request, slug):
    restaurant, membership, denied = scope(request, slug, ['owner','manager','cashier','waiter'])
    if denied: return denied
    branch = branch_for(request, restaurant, membership)
    if request.method == 'POST':
        try:
            payload = json.loads(request.body)
            if not isinstance(payload, dict):
                raise ValidationError('بيانات الطلب غير صالحة.')
            table = get_object_or_404(DiningTable, pk=payload.get('table_id'), branch=branch)
            order = create_order(table, payload, request.user)
            return JsonResponse({'ok': True, 'order_url': reverse('manage_orders', args=[slug]), 'order_number': order.pk})
        except (json.JSONDecodeError, ValidationError) as exc:
            return JsonResponse({'ok': False, 'error': error_text(exc)}, status=400)
    categories = Category.objects.filter(branch=branch, visible=True).prefetch_related('items__modifier_groups__options')
    return shell(request, restaurant, membership, 'staff_order', categories=categories,
                 items_json=serialize_menu_items(categories), tables=branch.tables.filter(active=True),
                 accepting=restaurant.active and branch.active and branch.accepting_orders and restaurant.subscription.valid())

def kitchen(request, slug):
    restaurant, membership, denied = scope(request, slug, ['owner','manager','kitchen'])
    if denied: return denied
    branch = branch_for(request, restaurant, membership)
    orders = list(Order.objects.filter(branch=branch, status__in=['new','accepted','preparing','ready']).select_related('table').prefetch_related('lines'))
    return shell(request, restaurant, membership, 'kitchen', orders=orders,
                 kitchen_signature=order_feed_signature(orders))

def order_feed_signature(orders):
    payload = '|'.join(f'{order.pk}:{order.status}:{order.updated_at.isoformat()}' for order in orders)
    return hashlib.sha256(payload.encode()).hexdigest()

def kitchen_feed(request, slug):
    restaurant, membership, denied = scope(request, slug, ['owner','manager','kitchen'])
    if denied: return denied
    branch = branch_for(request, restaurant, membership)
    orders = Order.objects.filter(branch=branch, status__in=['new','accepted','preparing','ready']).only('id','status','updated_at')
    response = JsonResponse({'signature': order_feed_signature(orders), 'count': orders.count(),
                             'checked_at': timezone.localtime().isoformat()})
    response['Cache-Control'] = 'no-store'
    return response

@require_POST
def order_status_change(request, slug, order_id):
    restaurant, membership, denied = scope(request, slug)
    if denied: return denied
    branch = branch_for(request, restaurant, membership)
    order = get_object_or_404(Order, pk=order_id, branch=branch)
    new = request.POST.get('status','')
    allowed = membership.role in ('owner','manager') or (membership.role == 'kitchen' and new in ('accepted','preparing','ready')) or (membership.role in ('cashier','waiter') and new == 'served')
    if not allowed: return render(request, '403.html', status=403)
    try:
        advance_order(order, request.user, new, request.POST.get('reason',''))
        messages.success(request, 'تحدثت حالة الطلب.')
    except ValidationError as exc: messages.error(request, error_text(exc))
    return redirect('kitchen' if membership.role == 'kitchen' else 'manage_orders', slug=slug)

@require_POST
def order_payment_change(request, slug, order_id):
    restaurant, membership, denied = scope(request, slug, ['owner','manager','cashier'])
    if denied: return denied
    branch = branch_for(request, restaurant, membership)
    order = get_object_or_404(Order, pk=order_id, branch=branch)
    try:
        record_manual_payment(order, request.user)
        messages.success(request, 'تم تسجيل التحصيل اليدوي. هذه ليست عملية دفع إلكتروني.')
    except ValidationError as exc:
        messages.error(request, error_text(exc))
    return redirect('manage_orders', slug=slug)

def manage_calls(request, slug):
    restaurant, membership, denied = scope(request, slug, ['owner','manager','waiter','cashier'])
    if denied: return denied
    branch = branch_for(request, restaurant, membership)
    calls = ServiceCall.objects.filter(table__branch=branch).select_related('table','assigned_to')[:100]
    return shell(request, restaurant, membership, 'calls', calls=calls)

@require_POST
def call_status_change(request, slug, call_id):
    restaurant, membership, denied = scope(request, slug, ['owner','manager','waiter','cashier'])
    if denied: return denied
    branch = branch_for(request, restaurant, membership)
    call = get_object_or_404(ServiceCall, pk=call_id, table__branch=branch)
    try:
        advance_call(call, request.user, request.POST.get('status'))
        messages.success(request, 'تحدثت حالة الاستدعاء.')
    except ValidationError as exc: messages.error(request, error_text(exc))
    return redirect('manage_calls', slug=slug)

def reports(request, slug):
    restaurant, membership, denied = scope(request, slug, ['owner','manager'])
    if denied: return denied
    branch = branch_for(request, restaurant, membership)
    orders = Order.objects.filter(branch=branch)
    if request.GET.get('export') == 'csv':
        response = HttpResponse(content_type='text/csv; charset=utf-8-sig')
        response['Content-Disposition'] = 'attachment; filename="orders.csv"'
        response.write('\ufeff')
        writer = csv.writer(response)
        writer.writerow(['رقم الطلب','الطاولة','الحالة','حالة الدفع','المصدر','إجمالي الطلب','وقت الإنشاء'])
        for order in orders.select_related('table'):
            name = str(order.table.name)
            if name.startswith(('=','+','-','@')): name = "'" + name
            writer.writerow([order.pk,name,order.get_status_display(),order.get_payment_status_display(),order.get_source_display(),order.total,order.created_at.isoformat()])
        return response
    status_names = dict(Order.STATUSES)
    by_status = [{'label': status_names.get(row['status'], row['status']), 'count': row['count']}
                 for row in orders.values('status').annotate(count=Count('id'))]
    accepted_avg = OrderHistory.objects.filter(order__branch=branch, new_status='accepted').aggregate(
        value=Avg(F('created_at') - F('order__created_at')))['value']
    ready_avg = OrderHistory.objects.filter(order__branch=branch, new_status='ready').aggregate(
        value=Avg(F('created_at') - F('order__created_at')))['value']
    call_avg = ServiceCall.objects.filter(table__branch=branch, acknowledged_at__isnull=False).aggregate(
        value=Avg(F('acknowledged_at') - F('created_at')))['value']
    return shell(request, restaurant, membership, 'reports', total=orders.count(),
                 by_status=by_status,
                 by_source=orders.values('source').annotate(count=Count('id')),
                 total_value=orders.exclude(status='cancelled').aggregate(sum=Sum('total'))['sum'] or 0,
                 paid_value=orders.filter(payment_status='paid').aggregate(sum=Sum('total'))['sum'] or 0,
                 top_items=OrderLine.objects.filter(order__branch=branch).exclude(order__status='cancelled').values('item_name').annotate(quantity=Sum('quantity')).order_by('-quantity')[:5],
                 accepted_time=format_duration(accepted_avg), ready_time=format_duration(ready_avg),
                 call_response_time=format_duration(call_avg))

def format_duration(duration):
    if duration is None:
        return 'لا توجد بيانات'
    seconds = max(0, round(duration.total_seconds()))
    return f'{seconds // 60} د {seconds % 60} ث'

def staff(request, slug):
    restaurant, membership, denied = scope(request, slug, ['owner','manager'])
    if denied: return denied
    if request.method == 'POST':
        action = request.POST.get('action', 'create')
        if action in ('role','toggle','branch'):
            target = get_object_or_404(Membership, pk=request.POST.get('membership_id'), restaurant=restaurant)
            if target.role == 'owner' or target.user_id == request.user.pk:
                return render(request, '403.html', status=403)
            if membership.role == 'manager' and target.role == 'manager':
                return render(request, '403.html', status=403)
            if action == 'role':
                if membership.role != 'owner':
                    return render(request, '403.html', status=403)
                role = request.POST.get('role')
                if role not in ('manager','cashier','kitchen','waiter'):
                    messages.error(request, 'الدور غير صالح.')
                else:
                    target.role = role
                    target.save(update_fields=['role'])
                    if role in ('cashier','kitchen','waiter') and not target.branches.exists():
                        target.branches.add(branch_for(request, restaurant, membership))
                    audit(restaurant, request.user, 'staff.role', f'{target.user_id}: {role}')
                    messages.success(request, 'تم تحديث الدور.')
            elif action == 'toggle':
                try:
                    with transaction.atomic():
                        if not target.active:
                            check_activation_limit(restaurant, 'staff')
                        target.active = not target.active
                        target.save(update_fields=['active'])
                        audit(restaurant, request.user, 'staff.toggle', f'{target.user_id}: {target.active}')
                    messages.success(request, 'تم تحديث حالة العضو.')
                except ValidationError as exc:
                    messages.error(request, error_text(exc))
            else:
                branch = get_object_or_404(Branch, pk=request.POST.get('branch_id'), restaurant=restaurant, active=True)
                target.branches.set([branch])
                audit(restaurant, request.user, 'staff.branch', f'{target.user_id}: {branch.pk}')
                messages.success(request, 'تم تحديث فرع العضو.')
            return redirect('staff', slug=slug)
        email = request.POST.get('email','').strip().lower()
        role = request.POST.get('role','')
        password = request.POST.get('password','')
        if not restaurant.subscription.valid():
            messages.error(request, 'انتهت فترة التجربة أو الاشتراك. إضافة الموظفين متوقفة مؤقتًا.')
        elif role not in ('manager','cashier','kitchen','waiter') or not email:
            messages.error(request, 'تحقق من البريد والدور.')
        elif User.objects.filter(username=email).exists():
            messages.error(request, 'البريد مستخدم بالفعل. تواصل مع مدير المنصة لإضافته.')
        else:
            try:
                validate_email(email)
                validate_password(password, user=User(username=email, email=email))
            except ValidationError as exc:
                messages.error(request, error_text(exc))
                return redirect('staff', slug=slug)
            with transaction.atomic():
                try:
                    check_creation_limit(restaurant, 'staff')
                except ValidationError as exc:
                    messages.error(request, error_text(exc))
                    return redirect('staff', slug=slug)
                user = User.objects.create_user(username=email, email=email, password=password)
                new_membership = Membership.objects.create(user=user, restaurant=restaurant, role=role)
                new_membership.branches.add(branch_for(request, restaurant, membership))
                audit(restaurant, request.user, 'staff.create', email)
            messages.success(request, 'تم إنشاء حساب الموظف.')
        return redirect('staff', slug=slug)
    return shell(request, restaurant, membership, 'staff',
        members=restaurant.memberships.select_related('user').prefetch_related('branches'),
        limit=restaurant.subscription.plan.max_staff)

def manage_branches(request, slug):
    restaurant, membership, denied = scope(request, slug, ['owner','manager'])
    if denied: return denied
    if request.method == 'POST':
        action = request.POST.get('action')
        try:
            if action == 'create':
                name = request.POST.get('name', '').strip()[:120]
                if not name:
                    raise ValidationError('أدخل اسم الفرع.')
                with transaction.atomic():
                    check_creation_limit(restaurant, 'branch')
                    branch = Branch.objects.create(restaurant=restaurant, name=name,
                        address=request.POST.get('address', '').strip()[:240])
                    audit(restaurant, request.user, 'branch.create', str(branch.pk))
            elif action == 'toggle':
                branch = get_object_or_404(Branch, pk=request.POST.get('branch_id'), restaurant=restaurant)
                with transaction.atomic():
                    if not branch.active:
                        check_activation_limit(restaurant, 'branch')
                    branch.active = not branch.active
                    if not branch.active:
                        branch.accepting_orders = False
                    branch.save(update_fields=['active','accepting_orders'])
                audit(restaurant, request.user, 'branch.toggle', str(branch.pk))
            else:
                raise ValidationError('إجراء غير معروف.')
            messages.success(request, 'تم حفظ الفرع.')
        except ValidationError as exc:
            messages.error(request, error_text(exc))
        return redirect('manage_branches', slug=slug)
    return shell(request, restaurant, membership, 'branches',
        branches=restaurant.branches.all(), limit=restaurant.subscription.plan.max_branches)

@require_POST
def select_branch(request, slug):
    restaurant, membership, denied = scope(request, slug)
    if denied: return denied
    branches = Branch.objects.filter(restaurant=restaurant, active=True)
    if membership.role not in ('owner','manager'):
        branches = branches.filter(staff_memberships=membership)
    branch = get_object_or_404(branches, pk=request.POST.get('branch_id'))
    request.session[f'safra_branch_{restaurant.pk}'] = branch.pk
    return redirect('dashboard', slug=slug)

def restaurant_settings(request, slug):
    restaurant, membership, denied = scope(request, slug, ['owner','manager'])
    if denied: return denied
    branch = branch_for(request, restaurant, membership)
    if request.method == 'POST':
        restaurant.description = request.POST.get('description','').strip()[:240]
        branch.accepting_orders = request.POST.get('accepting_orders') == 'on'
        restaurant.save(update_fields=['description'])
        branch.save(update_fields=['accepting_orders'])
        audit(restaurant, request.user, 'restaurant.settings')
        messages.success(request, 'تم حفظ الإعدادات.')
        return redirect('restaurant_settings', slug=slug)
    subscription = restaurant.subscription
    usage = {
        'branches': restaurant.branches.count(),
        'tables': branch.tables.count(),
        'items': MenuItem.objects.filter(category__branch=branch).count(),
        'staff': restaurant.memberships.count(),
    }
    over_limit = (usage['branches'] > subscription.plan.max_branches or
                  usage['tables'] > subscription.plan.max_tables or
                  usage['items'] > subscription.plan.max_items or
                  usage['staff'] > subscription.plan.max_staff)
    return shell(request, restaurant, membership, 'settings', subscription=subscription,
                 usage=usage, over_limit=over_limit)

def public_menu(request, token):
    table = get_object_or_404(DiningTable.objects.select_related('branch__restaurant'), public_token=token)
    if not table.active or not table.branch.active or not table.branch.restaurant.active:
        return render(request, 'table_closed.html', status=404)
    categories = Category.objects.filter(branch=table.branch, visible=True).prefetch_related('items__modifier_groups__options')
    items = serialize_menu_items(categories)
    subscription = getattr(table.branch.restaurant, 'subscription', None)
    accepting = table.branch.accepting_orders and (subscription is None or subscription.valid())
    return render(request, 'public_menu.html', {'table': table, 'categories': categories, 'items_json': items, 'accepting': accepting})

def serialize_menu_items(categories):
    items = []
    for category in categories:
        for item in category.items.all():
            if item.available:
                items.append({'id': item.pk, 'category': category.pk, 'name': item.name, 'description': item.description,
                              'price': str(item.price), 'image': item.image.url if item.image else '',
                              'groups': [{'name': g.name, 'min': g.min_select, 'max': g.max_select,
                                          'options': [{'id': o.pk, 'name': o.name, 'price': str(o.price)} for o in g.options.all() if o.available]}
                                         for g in item.modifier_groups.all()]})
    return items

@require_POST
def place_order(request, token):
    table = get_object_or_404(DiningTable, public_token=token)
    try:
        order = create_order(table, json.loads(request.body))
        return JsonResponse({'ok': True, 'order_url': reverse('track_order', args=[order.public_token]), 'order_number': order.pk})
    except (json.JSONDecodeError, ValidationError) as exc:
        return JsonResponse({'ok': False, 'error': error_text(exc)}, status=400)

@require_POST
def create_call(request, token):
    table = get_object_or_404(DiningTable, public_token=token)
    try:
        create_service_call(table, request.POST.get('kind'))
        messages.success(request, 'وصل طلبك لفريق الخدمة.')
    except ValidationError as exc: messages.error(request, error_text(exc))
    return redirect('public_menu', token=token)

def track_order(request, token):
    order = get_object_or_404(Order.objects.select_related('table__branch__restaurant').prefetch_related('lines'), public_token=token)
    return render(request, 'track.html', {'order': order})
