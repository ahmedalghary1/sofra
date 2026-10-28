import csv
import io
import json
import secrets
from datetime import timedelta
from decimal import Decimal, InvalidOperation
from PIL import Image, UnidentifiedImageError
from django.contrib import messages
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.models import User
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.core.exceptions import PermissionDenied
from django.core.validators import validate_email
from django.db import IntegrityError, transaction
from django.db.models import Count, Sum
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST
from .models import Branch, Category, DiningTable, Membership, MenuItem, ModifierGroup, ModifierOption, Order, Plan, Restaurant, ServiceCall, Subscription
from .services import advance_call, advance_order, audit, create_order, create_service_call
from .limits import check_creation_limit

def error_text(exc): return ' '.join(exc.messages) if hasattr(exc, 'messages') else str(exc)
def health(request): return JsonResponse({'status': 'ok'})

def home(request):
    if request.user.is_authenticated:
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
        user = authenticate(request, username=request.POST.get('email', '').strip().lower(), password=request.POST.get('password', ''))
        if user and (user.is_superuser or user.memberships.filter(active=True).exists()):
            login(request, user)
            return redirect('home')
        messages.error(request, 'البريد أو كلمة المرور غير صحيحة.')
    return render(request, 'auth.html', {'mode': 'login'})

@require_POST
def logout_view(request):
    logout(request)
    return redirect('home')

def scope(request, slug, roles=None):
    if not request.user.is_authenticated: return None, None, redirect('login')
    restaurant = get_object_or_404(Restaurant, slug=slug)
    membership = Membership.objects.filter(user=request.user, restaurant=restaurant, active=True).first()
    if not membership or (roles and membership.role not in roles):
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
            elif action == 'item':
                category = get_object_or_404(Category, pk=request.POST.get('category'), branch=branch)
                price = Decimal(request.POST.get('price', ''))
                if price < 0 or price > 9999999: raise ValidationError('السعر غير صالح.')
                name = request.POST.get('name','').strip()[:120]
                if not name: raise ValidationError('أدخل اسم الصنف.')
                image = request.FILES.get('image')
                if image and (image.size > 3_000_000 or image.content_type not in ('image/jpeg','image/png','image/webp')):
                    raise ValidationError('الصورة يجب أن تكون JPG أو PNG أو WebP وأقل من 3 ميجابايت.')
                if image:
                    try:
                        with Image.open(image) as picture:
                            if picture.format not in ('JPEG','PNG','WEBP') or picture.width > 5000 or picture.height > 5000:
                                raise ValidationError('صيغة الصورة أو أبعادها غير مدعومة.')
                            picture.verify()
                        image.seek(0)
                    except (UnidentifiedImageError, OSError):
                        raise ValidationError('ملف الصورة غير صالح.')
                with transaction.atomic():
                    check_creation_limit(restaurant, 'item', branch)
                    item = MenuItem.objects.create(category=category, name=name, description=request.POST.get('description','').strip()[:300], price=price, image=image)
                audit(restaurant, request.user, 'item.create', str(item.pk))
            elif action in ('toggle','price'):
                item = get_object_or_404(MenuItem, pk=request.POST.get('item_id'), category__branch=branch)
                if action == 'toggle': item.available = not item.available
                else:
                    item.price = Decimal(request.POST.get('price',''))
                    if item.price < 0 or item.price > 9999999: raise ValidationError('السعر غير صالح.')
                item.save()
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
                if action == 'toggle': table.active = not table.active
                elif action == 'rotate': table.public_token = secrets.token_urlsafe(24)
                else: raise ValidationError('إجراء غير معروف.')
                table.save()
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

def kitchen(request, slug):
    restaurant, membership, denied = scope(request, slug, ['owner','manager','kitchen'])
    if denied: return denied
    branch = branch_for(request, restaurant, membership)
    orders = Order.objects.filter(branch=branch, status__in=['new','accepted','preparing','ready']).select_related('table').prefetch_related('lines')
    return shell(request, restaurant, membership, 'kitchen', orders=orders)

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
        writer.writerow(['رقم الطلب','الطاولة','الحالة','المصدر','إجمالي الطلب','وقت الإنشاء'])
        for order in orders.select_related('table'):
            name = str(order.table.name)
            if name.startswith(('=','+','-','@')): name = "'" + name
            writer.writerow([order.pk,name,order.get_status_display(),order.get_source_display(),order.total,order.created_at.isoformat()])
        return response
    status_names = dict(Order.STATUSES)
    by_status = [{'label': status_names.get(row['status'], row['status']), 'count': row['count']}
                 for row in orders.values('status').annotate(count=Count('id'))]
    return shell(request, restaurant, membership, 'reports', total=orders.count(),
                 by_status=by_status,
                 by_source=orders.values('source').annotate(count=Count('id')),
                 total_value=orders.exclude(status='cancelled').aggregate(sum=Sum('total'))['sum'] or 0)

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
                target.active = not target.active
                target.save(update_fields=['active'])
                audit(restaurant, request.user, 'staff.toggle', f'{target.user_id}: {target.active}')
                messages.success(request, 'تم تحديث حالة العضو.')
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
    items = []
    for category in categories:
        for item in category.items.all():
            if item.available:
                items.append({'id': item.pk, 'category': category.pk, 'name': item.name, 'description': item.description,
                              'price': str(item.price), 'image': item.image.url if item.image else '',
                              'groups': [{'name': g.name, 'min': g.min_select, 'max': g.max_select,
                                          'options': [{'id': o.pk, 'name': o.name, 'price': str(o.price)} for o in g.options.all() if o.available]}
                                         for g in item.modifier_groups.all()]})
    subscription = getattr(table.branch.restaurant, 'subscription', None)
    accepting = table.branch.accepting_orders and (subscription is None or subscription.valid())
    return render(request, 'public_menu.html', {'table': table, 'categories': categories, 'items_json': items, 'accepting': accepting})

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
