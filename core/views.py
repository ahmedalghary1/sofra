import csv
import hashlib
import io
import json
import secrets
import uuid
from smtplib import SMTPException
from urllib.parse import urlencode, urlparse
from datetime import timedelta
from datetime import datetime, time
from decimal import Decimal, InvalidOperation
from PIL import Image, UnidentifiedImageError
from django.contrib import messages
from django.contrib.admin.models import ADDITION, CHANGE, LogEntry
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.views import PasswordResetView
from django.contrib.auth.models import User
from django.core.mail import send_mail
from django.conf import settings
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.core.exceptions import PermissionDenied
from django.core.validators import validate_email
from django.db import DatabaseError, IntegrityError, connection, transaction
from django.db.models.functions import TruncDate
from django.db.models import Avg, Count, Exists, F, OuterRef, Prefetch, Q, Subquery, Sum
from django.core.paginator import Paginator
from django.http import Http404, HttpResponse, JsonResponse, StreamingHttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.template.loader import render_to_string
from django.urls import Resolver404, resolve, reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils.translation import gettext
from django.utils import timezone
from django.utils.dateparse import parse_date
from django.views.decorators.http import require_GET, require_POST, require_http_methods
from django.views.decorators.gzip import gzip_page
from django.views.decorators.cache import never_cache
from .models import Branch, Category, DiningTable, KitchenStation, LoginAttempt, Membership, MenuImportBatch, MenuItem, MenuSize, ModifierGroup, ModifierOption, Order, OrderHistory, OrderLine, OrderLinePrepHistory, OwnerEmailVerification, Plan, Restaurant, ServiceCall, StaffInvitation, Subscription, SubscriptionPayment
from .services import advance_call, advance_order, advance_order_line, audit, correct_order_status, create_order, create_service_call, edit_order, order_edit_allowed, record_manual_payment
from .limits import check_activation_limit, check_creation_limit
from .models import DemoRequest
from .forms import OwnerRegistrationForm, PlatformBranchForm, PlatformPlanForm, PlatformRestaurantForm, RestaurantProfileForm, SubscriptionPaymentForm
from .menu_import import commit_menu_import, parse_menu_csv, parse_menu_xlsx
from .plan_preview import preview_plan_usage
from .showcase_catalog import public_tier_records
from .presentation import localized_url
from .demo_menu import demo_order_total, sample_menu
from .menu_photos import menu_photo
from .invitations import accept_staff_invitation, invitation_for_token, invited_account

def error_text(exc):
    errors = exc.messages if hasattr(exc, 'messages') else [str(exc)]
    return ' '.join(gettext(error) for error in errors)
def health(request):
    try:
        with connection.cursor() as cursor:
            cursor.execute('SELECT 1')
            cursor.fetchone()
    except DatabaseError:
        response = JsonResponse({'status': 'unavailable'}, status=503)
    else:
        response = JsonResponse({'status': 'ok'})
    response['Cache-Control'] = 'no-store'
    return response

@gzip_page
def home(request):
    if request.user.is_authenticated:
        if request.user.is_superuser:
            return redirect('platform_dashboard')
        membership = request.user.memberships.filter(active=True).select_related('restaurant').first()
        if membership: return redirect('dashboard', slug=membership.restaurant.slug)
    from .marketing import public_page
    return public_page(request, 'home')

@require_http_methods(['GET', 'HEAD'])
def demo_menu(request):
    sections, items = sample_menu(english=request.LANGUAGE_CODE == 'en')
    return render(request, 'demo_menu.html', {
        'menu_categories': [(key, name) for key, name, _ in sections], 'items_json': items,
        'demo_menu': True,
    })


@require_POST
def place_demo_order(request):
    english = request.LANGUAGE_CODE == 'en'
    try:
        total = demo_order_total(json.loads(request.body), english=english)
    except ValueError:
        error = 'Invalid cart data.' if english else 'توجد بيانات غير صالحة في السلة.'
        return JsonResponse({'ok': False, 'error': error}, status=400)
    except ValidationError as exc:
        return JsonResponse({'ok': False, 'error': error_text(exc)}, status=400)
    return JsonResponse({'ok': True, 'total': str(total)})


def send_owner_activation(request, user, token, *, resend=False):
    english = request.LANGUAGE_CODE == 'en'
    path = localized_url('verify_owner_email', english, token)
    link = settings.SITE_URL + path
    subject = gettext('رابط تفعيل بريد مالك المطعم في Serveza') if resend else gettext('تفعيل بريد مالك المطعم في Serveza')
    text = gettext('مرحبًا {name}،\nفعّل بريدك خلال 24 ساعة من الرابط التالي:\n{link}').format(name=user.first_name, link=link)
    text += '\n\n' + ('After verifying your email, request contact from customer service to choose a plan and arrange payment.'
                      if english else 'بعد تأكيد بريدك، اطلب التواصل مع خدمة العملاء لاختيار باقتك وترتيب السداد.')
    html = render_to_string('emails/owner_activation.html', {'english': english, 'name': user.first_name, 'link': link})
    if send_mail(subject, text, settings.DEFAULT_FROM_EMAIL, [user.email], html_message=html, fail_silently=False) != 1:
        raise ValidationError('تعذر إرسال رابط التفعيل الآن.')


def register(request):
    form = OwnerRegistrationForm(request.POST if request.method == 'POST' else None)
    if form.is_valid():
        name = form.cleaned_data['name']
        email = form.cleaned_data['email']
        restaurant_name = form.cleaned_data['restaurant']
        password = form.cleaned_data['password']
        try:
            with transaction.atomic():
                user = User.objects.create_user(username=email, email=email, password=password,
                                                first_name=name, is_active=False)
                restaurant = Restaurant.objects.create(name=restaurant_name, slug='rest-' + secrets.token_hex(5))
                Branch.objects.create(restaurant=restaurant)
                Membership.objects.create(user=user, restaurant=restaurant, role='owner')
                Subscription.objects.create(restaurant=restaurant, status='pending')
                raw_token = secrets.token_urlsafe(32)
                OwnerEmailVerification.objects.create(user=user,
                    token_hash=hashlib.sha256(raw_token.encode()).hexdigest(),
                    expires_at=timezone.now()+timedelta(days=1))
                send_owner_activation(request, user, raw_token)
        except IntegrityError:
            form.add_error('email', gettext('تعذر إنشاء الحساب بهذا البريد. حاول تسجيل الدخول أو إعادة المحاولة.'))
        except (SMTPException, OSError):
            form.add_error(None, gettext('تعذر إرسال رابط التفعيل. حاول لاحقًا.'))
        except ValidationError as exc:
            form.add_error(None, error_text(exc))
        else:
            return redirect('verification_pending')
    return render(request, 'auth.html', {'mode': 'register', 'form': form})


@require_GET
def verification_pending(request):
    return render(request, 'email_verification.html')


@require_GET
def verify_owner_email(request, token):
    digest = hashlib.sha256(token.encode()).hexdigest()
    with transaction.atomic():
        verification = OwnerEmailVerification.objects.select_for_update().select_related('user').filter(
            token_hash=digest, verified_at__isnull=True, expires_at__gt=timezone.now()).first()
        if not verification:
            return render(request, 'email_verification.html', {'invalid': True}, status=400)
        verification.verified_at = timezone.now()
        verification.save(update_fields=['verified_at'])
        user = verification.user
        user.is_active = True
        user.save(update_fields=['is_active'])
    login(request, user, backend='django.contrib.auth.backends.ModelBackend')
    messages.success(request, 'تم تفعيل بريدك الإلكتروني.')
    return redirect('home')


@require_POST
def resend_owner_verification(request):
    email = request.POST.get('email', '').strip().lower()
    password = request.POST.get('password', '')
    key = hashlib.sha256(f"verify:{request.META.get('REMOTE_ADDR','')}:{email}".encode()).hexdigest()
    attempts = LoginAttempt.objects.filter(key=key, created_at__gte=timezone.now()-timedelta(hours=1))
    if attempts.count() < 3:
        LoginAttempt.objects.create(key=key)
        user = User.objects.filter(username__iexact=email, is_active=False).first()
        if user and user.check_password(password):
            try:
                with transaction.atomic():
                    verification = OwnerEmailVerification.objects.select_for_update().filter(
                        user=user, verified_at__isnull=True).first()
                    if verification:
                        raw_token = secrets.token_urlsafe(32)
                        verification.token_hash = hashlib.sha256(raw_token.encode()).hexdigest()
                        verification.expires_at = timezone.now()+timedelta(days=1)
                        verification.sent_at = timezone.now()
                        verification.save(update_fields=['token_hash', 'expires_at', 'sent_at'])
                        send_owner_activation(request, user, raw_token, resend=True)
            except (ValidationError, SMTPException, OSError):
                messages.error(request, 'تعذر إرسال البريد الآن. حاول لاحقًا.')
                return redirect('verification_pending')
    messages.success(request, 'إذا كانت البيانات صحيحة، سيصلك رابط تفعيل جديد.')
    return redirect('verification_pending')

def login_username(identifier):
    username = User.normalize_username(identifier.strip())
    if not username or User.objects.filter(username=username).exists():
        return username
    if '@' not in username:
        matches = list(User.objects.filter(username__iexact=username).values_list('username', flat=True)[:2])
        return matches[0] if len(matches) == 1 else username
    normalized = username.lower()
    if username != normalized and User.objects.filter(username=normalized).exists():
        return normalized
    matches = list(User.objects.filter(email__iexact=username).values_list('username', flat=True)[:2])
    return matches[0] if len(matches) == 1 else username


@never_cache
def login_view(request):
    if request.method == 'GET' and request.user.is_authenticated and request.user.is_superuser:
        return redirect('platform_dashboard')
    login_next = request.POST.get('next') or request.GET.get('next', '')
    identifier = request.POST.get('username') or request.POST.get('email', '')
    context = {'mode': 'login', 'login_next': login_next, 'login_identifier': identifier}
    if request.method == 'POST':
        username = login_username(identifier)
        key = hashlib.sha256(f"{request.META.get('REMOTE_ADDR','')}:{username.lower()}".encode()).hexdigest()
        attempts = LoginAttempt.objects.filter(key=key, created_at__gte=timezone.now()-timedelta(minutes=15))
        if attempts.count() >= 8:
            messages.error(request, 'محاولات كثيرة. انتظر 15 دقيقة ثم حاول مرة أخرى.')
            return render(request, 'auth.html', context, status=429)
        user = authenticate(request, username=username, password=request.POST.get('password', ''))
        target = request.POST.get('next', '')
        safe_target = target and url_has_allowed_host_and_scheme(
            target, allowed_hosts={request.get_host()}, require_https=request.is_secure())
        invitation_login = False
        if user and safe_target:
            try:
                match = resolve(urlparse(target).path)
            except Resolver404:
                match = None
            if match and match.url_name == 'accept_staff_invitation':
                invitation = invitation_for_token(match.kwargs['token'])
                if invitation and invitation.pending():
                    try:
                        invitation_login = invited_account(invitation.email) == user
                    except ValidationError:
                        pass
        if user and (user.is_superuser or user.memberships.filter(active=True).exists() or invitation_login):
            LoginAttempt.objects.filter(key=key).delete()
            login(request, user)
            if user.is_superuser:
                return redirect('platform_dashboard')
            if safe_target:
                return redirect(target)
            return redirect('home')
        if user:
            messages.error(request, 'Your account has no active workspace. Contact your platform administrator.'
                           if request.LANGUAGE_CODE == 'en' else 'لا توجد مساحة عمل مفعّلة لحسابك. تواصل مع مسؤول المنصة.')
        else:
            LoginAttempt.objects.create(key=key)
            messages.error(request, 'Check your credentials. If your email is shared, use your username.' if request.LANGUAGE_CODE == 'en' else 'تحقق من بيانات الدخول. إذا كان بريدك مشتركًا، أدخل اسم المستخدم الخاص بحسابك.')
    return render(request, 'auth.html', context)

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
    restaurants = Restaurant.objects.select_related('subscription__plan').prefetch_related(
        'branches', Prefetch('memberships', queryset=Membership.objects.filter(role='owner').select_related('user'),
                             to_attr='platform_owners')).annotate(
        branch_count=Count('branches', distinct=True), member_count=Count('memberships', distinct=True)).order_by('-created_at')
    section = request.GET.get('status', 'all')
    if section not in ('pending', 'active', 'people', 'plans'):
        section = 'all'
    now = timezone.now()
    active = Q(active=True, subscription__status__in=('active', 'trial'), subscription__ends_at__gt=now)
    pending_only = section == 'pending'
    if pending_only:
        restaurants = restaurants.filter(subscription__status='pending').order_by(
            F('subscription__activation_requested_at').desc(nulls_last=True), '-created_at')
    if query:
        restaurants = restaurants.filter(Q(name__icontains=query) | Q(slug__icontains=query))
    if section == 'active':
        restaurants = restaurants.filter(active)
    members = Membership.objects.select_related('user', 'restaurant').prefetch_related(
        'branches', 'restaurant__branches').order_by('restaurant__name', 'user__username', 'pk')
    if query:
        members = members.filter(Q(user__email__icontains=query) | Q(user__username__icontains=query) |
                                 Q(restaurant__name__icontains=query))
    page = None
    if section != 'plans':
        records = members if section == 'people' else restaurants
        page = Paginator(records, 25).get_page(request.GET.get('page'))
        if section != 'people':
            for venue in page:
                subscription = getattr(venue, 'subscription', None)
                if subscription and subscription.plan_id:
                    venue.branch_limit = subscription.limit('max_branches')
    return render(request, 'platform.html', {'restaurants': page, 'plans': Plan.objects.order_by('price_monthly', 'name'), 'query': query,
        'plan_usage': Restaurant.objects.filter(active).values('subscription__plan__name').annotate(
            venues=Count('pk')).order_by('-venues', 'subscription__plan__name'),
        'new_enquiries': DemoRequest.objects.filter(status='new').count() if section == 'all' else 0,
        'expiring': Restaurant.objects.filter(active, subscription__ends_at__lte=now+timedelta(days=7)).select_related(
            'subscription').order_by('subscription__ends_at', 'pk')[:4],
        'section': section, 'roles': StaffInvitation.ROLES, 'member_total': Membership.objects.count(),
        'activation_plans': public_tier_records().order_by('price_monthly', 'name'),
        'pending_only': pending_only, 'pending_total': Subscription.objects.filter(status='pending').count(),
        'restaurant_total': Restaurant.objects.count(),
        'active_total': Restaurant.objects.filter(active).count()})


@require_POST
def platform_member_update(request, restaurant_id, member_id):
    denied = platform_scope(request)
    if denied:
        return denied
    role = request.POST.get('role')
    state = request.POST.get('state')
    reason = request.POST.get('reason', '').strip()[:120]
    branch_id = request.POST.get('branch')
    try:
        with transaction.atomic():
            member = get_object_or_404(Membership.objects.select_for_update(), pk=member_id, restaurant_id=restaurant_id)
            if member.role == 'owner' or member.user.is_superuser:
                return render(request, '403.html', status=403)
            if role not in dict(StaffInvitation.ROLES) or state not in ('active', 'paused') or not reason:
                raise ValidationError('Review the role, status and reason.' if request.LANGUAGE_CODE == 'en' else 'راجع الدور والحالة وسبب التعديل.')
            enabled = state == 'active'
            if enabled and not member.active:
                ensure_active_subscription(member.restaurant)
                check_activation_limit(member.restaurant, 'staff')
            if branch_id:
                if not branch_id.isascii() or not branch_id.isdigit() or len(branch_id) > 18:
                    raise Http404
                branch = get_object_or_404(Branch, restaurant_id=restaurant_id, pk=branch_id, active=True)
                member.branches.set([branch])
            elif enabled and role != 'manager' and not member.branches.filter(restaurant_id=restaurant_id, active=True).exists():
                raise ValidationError('Choose an active branch.' if request.LANGUAGE_CODE == 'en' else 'اختر فرعًا نشطًا للموظف.')
            member.role, member.active = role, enabled
            member.save(update_fields=['role', 'active'])
            audit(member.restaurant, request.user, 'platform.member.update',
                  f'member={member.pk}; role={role}; active={enabled}; branch={branch_id or "keep"}; reason={reason}')
        messages.success(request, 'Access updated.' if request.LANGUAGE_CODE == 'en' else 'تم تحديث صلاحيات العضو.')
    except ValidationError as exc:
        messages.error(request, error_text(exc))
    return redirect(reverse('platform_dashboard') + '?status=people')


@require_http_methods(['GET', 'POST'])
def platform_plan_form(request, plan_id=None):
    denied = platform_scope(request)
    if denied:
        return denied
    english = request.LANGUAGE_CODE == 'en'
    with transaction.atomic():
        plan = get_object_or_404(Plan.objects.select_for_update(), pk=plan_id) if plan_id else None
        form = PlatformPlanForm(request.POST if request.method == 'POST' else None, instance=plan, english=english)
        if request.method == 'POST' and form.is_valid():
            try:
                with transaction.atomic():
                    plan = form.save()
            except IntegrityError:
                form.add_error('name', 'Plan name already exists.' if english else 'اسم الباقة مستخدم بالفعل.')
            else:
                LogEntry.objects.log_actions(user_id=request.user.pk, queryset=[plan],
                    action_flag=CHANGE if plan_id else ADDITION, change_message=form.cleaned_data['reason'])
                messages.success(request, 'Plan saved.' if english else 'تم حفظ الباقة.')
                return redirect(reverse('platform_dashboard') + '?status=plans')
    return render(request, 'platform_plan_form.html', {'form': form, 'plan': plan, 'section': 'plans'})


@require_http_methods(['GET', 'POST'])
def platform_create_restaurant(request):
    denied = platform_scope(request)
    if denied: return denied
    form = PlatformRestaurantForm(request.POST if request.method == 'POST' else None)
    if request.method == 'POST' and form.is_valid():
        data = form.cleaned_data
        try:
            with transaction.atomic():
                owner = User.objects.select_for_update().filter(username__iexact=data['owner_email']).first()
                if owner:
                    if not owner.is_active or owner.is_superuser or owner.last_name == 'SOFRA_LOCAL_DEMO' or data['owner_password']:
                        raise ValidationError('تعذر ربط حساب المالك؛ حدّث الصفحة وراجع البريد وكلمة المرور.')
                else:
                    owner = User.objects.create_user(
                        username=data['owner_email'], email=data['owner_email'],
                        first_name=data['owner_name'], password=data['owner_password'])
                plan = data['plan']
                if plan and plan.max_branches < 1:
                    raise ValidationError('هذه الباقة لا تسمح بإنشاء الفرع الأول.')
                restaurant = Restaurant.objects.create(
                    name=data['name'], description=data['description'],
                    slug='rest-' + secrets.token_hex(6))
                branch = Branch.objects.create(
                    restaurant=restaurant, name=data['branch_name'], address=data['branch_address'])
                Membership.objects.create(user=owner, restaurant=restaurant, role='owner')
                if plan:
                    Subscription.objects.create(restaurant=restaurant, plan=plan, status='active',
                        ends_at=timezone.make_aware(datetime.combine(data['ends_at'], time.max)))
                else:
                    Subscription.objects.create(restaurant=restaurant, status='pending')
                audit(restaurant, request.user, 'platform.restaurant.create',
                      f'owner={owner.pk}; branch={branch.pk}; plan={plan.pk if plan else "pending"}')
            messages.success(request, 'Restaurant created.' if request.LANGUAGE_CODE == 'en' else 'تم إنشاء المطعم وربط حساب المالك.')
            return redirect(f'{reverse("platform_dashboard")}#restaurant-{restaurant.pk}')
        except IntegrityError:
            form.add_error(None, 'تعذر إنشاء المطعم بسبب تعارض في البيانات. حدّث الصفحة ثم حاول مرة أخرى.')
        except ValidationError as exc:
            form.add_error(None, error_text(exc))
    return render(request, 'platform_restaurant_form.html', {'form': form, 'section': 'all'})


@require_POST
def platform_branch_change(request, restaurant_id):
    denied = platform_scope(request)
    if denied: return denied
    restaurant = get_object_or_404(Restaurant, pk=restaurant_id)
    action = request.POST.get('action')
    try:
        with transaction.atomic():
            if action == 'create':
                form = PlatformBranchForm(request.POST)
                if not form.is_valid():
                    raise ValidationError('أدخل اسم فرع صالحًا وعنوانًا لا يتجاوز 240 حرفًا.')
                if not Subscription.objects.filter(restaurant=restaurant).exists():
                    raise ValidationError('اضبط اشتراك المطعم قبل إضافة فرع.')
                check_creation_limit(restaurant, 'branch')
                name = form.cleaned_data['name']
                if Branch.objects.filter(restaurant=restaurant, name__iexact=name).exists():
                    raise ValidationError('يوجد فرع بهذا الاسم في المطعم.')
                branch = Branch.objects.create(restaurant=restaurant, name=name,
                                               address=form.cleaned_data['address'])
                audit(restaurant, request.user, 'platform.branch.create', f'branch={branch.pk}')
            elif action == 'toggle':
                branch = get_object_or_404(Branch.objects.select_for_update(),
                                           pk=request.POST.get('branch_id'), restaurant=restaurant)
                reason = request.POST.get('reason', '').strip()[:120]
                if not reason:
                    raise ValidationError('اكتب سبب تغيير حالة الفرع.')
                if not branch.active:
                    if not Subscription.objects.filter(restaurant=restaurant).exists():
                        raise ValidationError('اضبط اشتراك المطعم قبل تفعيل الفرع.')
                    check_activation_limit(restaurant, 'branch')
                branch.active = not branch.active
                if not branch.active:
                    branch.accepting_orders = False
                branch.save(update_fields=['active', 'accepting_orders'])
                audit(restaurant, request.user, 'platform.branch.toggle',
                      f'branch={branch.pk}; active={branch.active}; reason={reason}')
            else:
                raise ValidationError('إجراء فرع غير معروف.')
        messages.success(request, 'تم حفظ الفرع.')
    except ValidationError as exc:
        messages.error(request, error_text(exc))
    return redirect(f'{reverse("platform_dashboard")}#restaurant-{restaurant.pk}')

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
                subscription = Subscription.objects.select_for_update().filter(restaurant=restaurant).first()
                needs_activation = subscription is None or subscription.status == 'pending'
                plans = public_tier_records() if needs_activation else Plan.objects.all()
                try:
                    plan = plans.get(pk=request.POST.get('plan_id'))
                except (Plan.DoesNotExist, ValueError, OverflowError):
                    raise ValidationError('Choose an available plan.' if request.LANGUAGE_CODE == 'en' else 'اختر باقة متاحة لهذا المطعم.')
                status = request.POST.get('status')
                if status not in ('trial','active','expired'):
                    raise ValidationError('حالة الاشتراك غير صالحة.')
                try:
                    end_date = datetime.fromisoformat(request.POST.get('ends_at','')).date()
                except (ValueError, TypeError):
                    raise ValidationError('تاريخ الانتهاء غير صالح.')
                if needs_activation and status != 'active':
                    raise ValidationError('Choose an active subscription to open the workspace.' if request.LANGUAGE_CODE == 'en' else 'اختر اشتراكًا نشطًا لفتح لوحة المطعم.')
                if status in ('active', 'trial') and end_date < timezone.localdate():
                    raise ValidationError('Choose today or a future expiry date.' if request.LANGUAGE_CODE == 'en' else 'اختر تاريخ انتهاء اليوم أو بعده.')
                old = 'missing'
                if subscription:
                    old = f'{subscription.plan_id}/{subscription.status}/{subscription.ends_at.date() if subscription.ends_at else "-"}'
                else:
                    subscription = Subscription(restaurant=restaurant)
                if subscription.plan_id != plan.pk:
                    subscription.grandfathered_limits = {}
                subscription.plan = plan
                subscription.status = status
                subscription.ends_at = timezone.make_aware(datetime.combine(end_date, time.max))
                subscription.save()
                audit(restaurant, request.user, 'platform.subscription.update',
                      f'{old} → {plan.pk}/{status}/{end_date}; reason={reason}')
            else:
                raise ValidationError('إجراء غير معروف.')
        messages.success(request, 'تم حفظ التغيير وتسجيل السبب.')
    except ValidationError as exc:
        messages.error(request, error_text(exc))
    return redirect('platform_dashboard')

@require_POST
def platform_record_payment(request, restaurant_id):
    denied = platform_scope(request)
    if denied: return denied
    restaurant = get_object_or_404(Restaurant, pk=restaurant_id)
    form = SubscriptionPaymentForm(request.POST)
    if not form.is_valid():
        messages.error(request, 'بيانات التحصيل غير صالحة: ' + ' '.join(
            str(error) for errors in form.errors.values() for error in errors))
        return redirect('platform_dashboard')
    data = form.cleaned_data
    try:
        with transaction.atomic():
            payment = SubscriptionPayment.objects.create(
                restaurant=restaurant, amount=data['amount'], currency='EGP',
                period_start=data['period_start'], period_end=data['period_end'],
                method=data['method'], reference=data['reference'], recorded_by=request.user)
            audit(restaurant, request.user, 'platform.subscription.payment',
                  f'payment={payment.pk}; amount={payment.amount} EGP; reference={payment.reference}')
    except IntegrityError:
        messages.error(request, 'مرجع التحصيل مسجل بالفعل لهذا المطعم.')
        return redirect('platform_dashboard')
    messages.success(request, 'سُجل التحصيل اليدوي. صلاحية الاشتراك لا تتغير تلقائيًا.')
    return redirect('platform_dashboard')

def scope(request, slug, roles=None, *, allow_pending=False):
    if not request.user.is_authenticated: return None, None, redirect('login')
    restaurant = get_object_or_404(Restaurant.objects.select_related('subscription'), slug=slug)
    membership = Membership.objects.filter(user=request.user, restaurant=restaurant, active=True).first()
    if not membership or (roles and membership.role not in roles):
        return None, None, render(request, '403.html', status=403)
    subscription = getattr(restaurant, 'subscription', None)
    if not allow_pending and (subscription is None or subscription.status == 'pending'):
        return None, None, redirect('subscription_activation', slug=slug)
    if not restaurant.active and request.method not in ('GET','HEAD') and request.resolver_match.url_name != 'select_branch':
        return None, None, render(request, '403.html', status=403)
    return restaurant, membership, None


@require_http_methods(['GET', 'HEAD', 'POST'])
def subscription_activation(request, slug):
    restaurant, membership, denied = scope(request, slug, allow_pending=True)
    if denied:
        return denied
    subscription = getattr(restaurant, 'subscription', None)
    if subscription is not None and subscription.status != 'pending':
        return redirect('dashboard', slug=slug)
    if request.method == 'POST':
        if membership.role != 'owner':
            return render(request, '403.html', status=403)
        if subscription is None:
            return redirect('subscription_activation', slug=slug)
        with transaction.atomic():
            requested = Subscription.objects.filter(pk=subscription.pk, status='pending',
                activation_requested_at__isnull=True).update(activation_requested_at=timezone.now())
            if requested:
                audit(restaurant, request.user, 'subscription.activation.request', 'Customer service callback requested')
        return redirect('subscription_activation', slug=slug)
    other_memberships = Membership.objects.filter(user=request.user, active=True).exclude(
        restaurant=restaurant).select_related('restaurant').order_by('restaurant__name')
    return render(request, 'subscription_activation.html', {'restaurant': restaurant,
        'membership': membership, 'subscription': subscription, 'other_memberships': other_memberships})

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
    subscription = getattr(restaurant, 'subscription', None)
    if not subscription or not subscription.valid():
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
           'available_restaurants': Membership.objects.filter(user=request.user, active=True)
               .select_related('restaurant').order_by('restaurant__name'),
           'available_branches': branches, 'page': page}
    ctx.update(extra)
    return render(request, f'{page}.html', ctx)

def dashboard(request, slug):
    restaurant, membership, denied = scope(request, slug)
    if denied: return denied
    branch = branch_for(request, restaurant, membership)
    orders = Order.objects.filter(branch=branch)
    setup_steps = None
    activity_context = {}
    if membership.role in ('owner', 'manager'):
        period = 14 if request.GET.get('period') == '14' else 7
        start = timezone.localdate() - timedelta(days=period - 1)
        period_orders = orders.filter(created_at__date__range=(start, timezone.localdate()))
        daily_counts = dict(period_orders.annotate(day=TruncDate('created_at')).values('day')
                            .annotate(total_orders=Count('pk')).values_list('day', 'total_orders'))
        peak = max(daily_counts.values(), default=1)
        activity = []
        for offset in range(period):
            day = start + timedelta(days=offset)
            count = daily_counts.get(day, 0)
            activity.append({'date': day, 'count': count, 'height': round(78 * count / peak)})
        amounts = period_orders.exclude(status='cancelled').aggregate(value=Sum('total'), average=Avg('total'))
        activity_context = {'period': period, 'activity': activity, 'activity_value': amounts['value'] or Decimal(0),
                            'activity_average': amounts['average'] or Decimal(0)}
        setup_steps = [
            {'label': 'أكمل وصف المطعم', 'done': bool(restaurant.description.strip()), 'url': 'restaurant_settings',
             'hint': 'عرّف الضيف بمطعمك في صفحة المنيو.'},
            {'label': 'أضف صنفًا متاحًا في المنيو',
             'done': MenuItem.objects.filter(category__branch=branch, category__visible=True, available=True).exists(),
             'url': 'manage_menu', 'hint': 'يمكن للضيف طلب الأصناف المتاحة فقط.'},
            {'label': 'جهّز طاولة نشطة', 'done': branch.tables.filter(active=True).exists(),
             'url': 'manage_tables', 'hint': 'حمّل رمز QR وضعه على الطاولة.'},
            {'label': 'عيّن موظفًا للمطبخ',
             'done': Membership.objects.filter(restaurant=restaurant, role='kitchen', active=True,
                                               branches=branch, user__is_active=True).exists(),
             'url': 'staff', 'hint': 'افتح شاشة المطبخ على جهاز الموظف بعد تعيينه.'},
            {'label': 'افتح شاشة المطبخ', 'done': branch.kitchen_opened_at is not None,
             'url': 'kitchen', 'hint': 'تأكد من ظهور طلبات الفرع على جهاز المطبخ.'},
        ]
    return shell(request, restaurant, membership, 'dashboard',
        recent=orders.select_related('table').prefetch_related('lines')[:6],
        today_orders=orders.filter(created_at__date=timezone.localdate()).count(),
        open_orders=orders.exclude(status__in=['served','cancelled']).count(),
        open_calls=ServiceCall.objects.filter(table__branch=branch, status__in=['open','acknowledged','in_progress']).count(),
        tables_count=branch.tables.count(), items_count=MenuItem.objects.filter(category__branch=branch).count(),
        setup_steps=setup_steps, setup_done=sum(step['done'] for step in setup_steps) if setup_steps else 0,
        setup_total=len(setup_steps) if setup_steps else 0, **activity_context)

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
            elif action == 'station_create':
                name = request.POST.get('name', '').strip()[:80]
                if not name: raise ValidationError('أدخل اسم محطة المطبخ.')
                with transaction.atomic():
                    check_creation_limit(restaurant, 'station', branch)
                    station = KitchenStation.objects.create(branch=branch, name=name)
                audit(restaurant, request.user, 'station.create', str(station.pk))
            elif action == 'station_assign':
                item = get_object_or_404(MenuItem, pk=request.POST.get('item_id'), category__branch=branch)
                station_id = request.POST.get('station_id')
                station = (get_object_or_404(KitchenStation, pk=station_id, branch=branch, active=True)
                           if station_id else None)
                item.station = station
                item.save(update_fields=['station'])
                audit(restaurant, request.user, 'station.assign', f'item={item.pk}; station={station_id or "none"}')
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
                price = Decimal(request.POST.get('price','0'))
                if price < 0 or price > 9999999: raise ValidationError('السعر غير صالح.')
                with transaction.atomic():
                    group, _ = ModifierGroup.objects.get_or_create(
                        item=item, name=group_name,
                        defaults={'min_select': minimum, 'max_select': maximum})
                    if minimum > group.options.filter(available=True).count() + 1:
                        raise ValidationError('الحد الأدنى أكبر من عدد الخيارات المتاحة بعد الإضافة.')
                    if (group.min_select, group.max_select) != (minimum, maximum):
                        group.min_select, group.max_select = minimum, maximum
                        group.save(update_fields=['min_select','max_select'])
                    ModifierOption.objects.create(group=group, name=option_name, price=price)
                audit(restaurant, request.user, 'modifier.create', str(item.pk))
            elif action == 'modifier_toggle':
                option = get_object_or_404(ModifierOption, pk=request.POST.get('option_id'), group__item__category__branch=branch)
                if option.available and option.group.options.filter(available=True).count() <= option.group.min_select:
                    raise ValidationError('لا يمكن إيقاف هذا الخيار قبل تقليل الحد الأدنى للمجموعة.')
                option.available = not option.available
                option.save(update_fields=['available'])
                audit(restaurant, request.user, 'modifier.toggle', str(option.pk))
            elif action == 'modifier_group_edit':
                group = get_object_or_404(ModifierGroup, pk=request.POST.get('group_id'),
                                          item__category__branch=branch)
                name = request.POST.get('name', '').strip()[:80]
                if not name: raise ValidationError('أدخل اسم المجموعة.')
                minimum = int(request.POST.get('min_select', ''))
                maximum = int(request.POST.get('max_select', ''))
                if not 0 <= minimum <= maximum <= 20:
                    raise ValidationError('حدود اختيار الإضافات غير صالحة.')
                if minimum > group.options.filter(available=True).count():
                    raise ValidationError('الحد الأدنى أكبر من عدد الخيارات المتاحة.')
                group.name, group.min_select, group.max_select = name, minimum, maximum
                group.save(update_fields=['name', 'min_select', 'max_select'])
                audit(restaurant, request.user, 'modifier.group.edit', str(group.pk))
            elif action == 'modifier_option_edit':
                option = get_object_or_404(ModifierOption, pk=request.POST.get('option_id'),
                                           group__item__category__branch=branch)
                name = request.POST.get('name', '').strip()[:80]
                if not name: raise ValidationError('أدخل اسم الإضافة.')
                price = Decimal(request.POST.get('price', ''))
                if not 0 <= price <= 9999999: raise ValidationError('السعر غير صالح.')
                sort_order = int(request.POST.get('sort_order', option.sort_order))
                if not 0 <= sort_order <= 999:
                    raise ValidationError('ترتيب الإضافة يجب أن يكون بين 0 و999.')
                option.name, option.price, option.sort_order = name, price, sort_order
                option.save(update_fields=['name', 'price', 'sort_order'])
                audit(restaurant, request.user, 'modifier.option.edit', str(option.pk))
            elif action == 'modifier_option_delete':
                option = get_object_or_404(ModifierOption, pk=request.POST.get('option_id'),
                                           group__item__category__branch=branch)
                if option.available and option.group.options.filter(available=True).count() <= option.group.min_select:
                    raise ValidationError('قلل الحد الأدنى للمجموعة قبل حذف هذا الخيار.')
                option_id = option.pk
                option.delete()
                audit(restaurant, request.user, 'modifier.option.delete', str(option_id))
            elif action == 'size':
                item = get_object_or_404(MenuItem, pk=request.POST.get('item_id'), category__branch=branch)
                name = request.POST.get('name', '').strip()[:80]
                if not name: raise ValidationError('أدخل اسم الحجم.')
                price = Decimal(request.POST.get('price', ''))
                if not 0 <= price <= 9999999: raise ValidationError('السعر غير صالح.')
                if item.sizes.count() >= 12: raise ValidationError('الحد الأقصى 12 حجمًا للصنف.')
                size = MenuSize.objects.create(item=item, name=name, price=price)
                audit(restaurant, request.user, 'size.create', str(size.pk))
            elif action == 'size_edit':
                size = get_object_or_404(MenuSize, pk=request.POST.get('size_id'), item__category__branch=branch)
                name = request.POST.get('name', '').strip()[:80]
                if not name: raise ValidationError('أدخل اسم الحجم.')
                price = Decimal(request.POST.get('price', ''))
                if not 0 <= price <= 9999999: raise ValidationError('السعر غير صالح.')
                size.name, size.price = name, price
                size.save(update_fields=['name', 'price'])
                audit(restaurant, request.user, 'size.edit', str(size.pk))
            elif action == 'size_toggle':
                size = get_object_or_404(MenuSize, pk=request.POST.get('size_id'), item__category__branch=branch)
                size.available = not size.available
                size.save(update_fields=['available'])
                audit(restaurant, request.user, 'size.toggle', str(size.pk))
            else: raise ValidationError('إجراء غير معروف.')
            messages.success(request, 'تم حفظ التغيير بنجاح.')
        except (ValidationError, InvalidOperation, IntegrityError, ValueError) as exc: messages.error(request, error_text(exc))
        return redirect('manage_menu', slug=slug)
    categories = Category.objects.filter(branch=branch).prefetch_related('items__sizes', 'items__modifier_groups__options')
    return shell(request, restaurant, membership, 'menu_manage', categories=categories,
                 stations=KitchenStation.objects.filter(branch=branch, active=True),
                 item_count=MenuItem.objects.filter(category__branch=branch).count(), limit=restaurant.subscription.limit('max_items', branch))

def menu_import(request, slug):
    restaurant, membership, denied = scope(request, slug, ['owner', 'manager'])
    if denied: return denied
    branch = branch_for(request, restaurant, membership)
    if request.method == 'GET' and request.GET.get('template') == '1':
        response = HttpResponse(content_type='text/csv; charset=utf-8')
        response['Content-Disposition'] = 'attachment; filename="serveza-menu-template.csv"'
        response.write('\ufeff')
        writer = csv.writer(response)
        writer.writerow(['category', 'name', 'price', 'description'])
        writer.writerow(['الأطباق الرئيسية', 'طبق تجريبي', '120.00', 'وصف اختياري'])
        return response
    remaining = max(0, restaurant.subscription.limit('max_items', branch) - MenuItem.objects.filter(category__branch=branch).count())
    errors, rows, batch = [], [], None
    if request.method == 'POST':
        try:
            ensure_active_subscription(restaurant)
            if not branch.active:
                raise ValidationError('الفرع متوقف حاليًا.')
            action = request.POST.get('action')
            if action == 'preview':
                if MenuImportBatch.objects.filter(user=request.user,
                        created_at__gte=timezone.now()-timedelta(hours=1)).count() >= 10:
                    raise ValidationError('بلغت حد المعاينات المؤقتة. حاول لاحقًا.')
                upload = request.FILES.get('file')
                if upload and upload.name.lower().endswith('.xlsx'):
                    rows, errors = parse_menu_xlsx(upload)
                else:
                    rows, errors = parse_menu_csv(upload)
                if not errors and len(rows) > remaining:
                    errors.append(gettext('الملف يحتوي {row_count} صنفًا، والمتاح في الباقة {remaining}.').format(row_count=len(rows), remaining=remaining))
                if not errors:
                    batch = MenuImportBatch.objects.create(branch=branch, user=request.user, rows=rows)
            elif action == 'commit':
                try:
                    token = uuid.UUID(request.POST.get('batch', ''))
                except (ValueError, TypeError):
                    raise ValidationError('معاينة الاستيراد غير صالحة.')
                batch = get_object_or_404(MenuImportBatch, token=token, branch=branch, user=request.user)
                rows = batch.rows
                count = commit_menu_import(batch, request.user)
                messages.success(request, gettext('تم استيراد {count} صنف.').format(count=count) if count else 'هذه الدفعة اعتُمدت سابقًا.')
                return redirect('manage_menu', slug=slug)
            else:
                raise ValidationError('إجراء الاستيراد غير معروف.')
        except ValidationError as exc:
            errors = [error_text(exc)]
        except IntegrityError:
            errors = ['تغيرت بيانات المنيو أثناء الاستيراد. راجع الملف وحاول مجددًا.']
    return shell(request, restaurant, membership, 'menu_import', errors=errors, rows=rows,
                 batch=batch, remaining=remaining)

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
                elif action == 'close_session':
                    with transaction.atomic():
                        table = DiningTable.objects.select_for_update().get(pk=table.pk)
                        current = Order.objects.filter(table=table, session_token=table.session_token)
                        if current.exclude(status__in=['served', 'cancelled']).exists() or current.filter(
                                payment_status='unpaid').exclude(status='cancelled').exists():
                            raise ValidationError('أكمل الطلبات الجارية وسجل تحصيل غير الملغى قبل إنهاء الجلسة.')
                        if ServiceCall.objects.filter(table=table, status__in=['open', 'acknowledged', 'in_progress']).exists():
                            raise ValidationError('أغلق استدعاءات الخدمة المفتوحة قبل إنهاء الجلسة.')
                        table.session_token = uuid.uuid4()
                        table.save(update_fields=['session_token'])
                else: raise ValidationError('إجراء غير معروف.')
                audit(restaurant, request.user, f'table.{action}', str(table.pk))
            messages.success(request, 'تم حفظ التغيير بنجاح.')
        except (ValidationError, IntegrityError) as exc: messages.error(request, error_text(exc))
        return redirect('manage_tables', slug=slug)
    return shell(request, restaurant, membership, 'tables', tables=branch.tables.all(), limit=restaurant.subscription.limit('max_tables', branch))

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

@require_http_methods(['GET', 'HEAD'])
def table_summary(request, slug, table_id):
    restaurant, membership, denied = scope(request, slug, ['owner', 'manager', 'cashier'])
    if denied:
        return denied
    branch = branch_for(request, restaurant, membership)
    table = get_object_or_404(DiningTable, pk=table_id, branch=branch)
    session_orders = Order.objects.filter(branch=branch, table=table, session_token=table.session_token)
    orders = list(session_orders.exclude(status='cancelled').prefetch_related('lines').order_by('created_at', 'pk'))
    cancelled_collections = list(session_orders.filter(status='cancelled', payment_status='paid').order_by('pk'))
    total = sum((order.total for order in orders), Decimal('0.00'))
    billable_paid = sum((order.total for order in orders if order.payment_status == 'paid'), Decimal('0.00'))
    cancelled_paid = sum((order.total for order in cancelled_collections), Decimal('0.00'))
    return render(request, 'table_summary.html', {
        'restaurant': restaurant, 'branch': branch, 'table': table, 'orders': orders,
        'total': total, 'paid': billable_paid + cancelled_paid, 'remaining': total - billable_paid,
        'cancelled_collections': cancelled_collections, 'cancelled_paid': cancelled_paid,
    })


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
            messages.success(request, gettext('تم إنشاء الطلب #{number}.').format(number=order.pk))
        except (ValidationError, json.JSONDecodeError) as exc: messages.error(request, error_text(exc))
        return redirect('manage_orders', slug=slug)
    table_key = request.GET.get('table', '')
    if table_key and (len(table_key) > 19 or not table_key.isascii() or not table_key.isdigit()):
        raise Http404
    selected_table = get_object_or_404(DiningTable, pk=table_key, branch=branch) if table_key else None
    orders = Order.objects.filter(branch=branch)
    if selected_table:
        orders = orders.filter(table=selected_table)
    selected_status = request.GET.get('status', '')
    if selected_status not in dict(Order.STATUSES):
        selected_status = ''
    if selected_status:
        orders = orders.filter(status=selected_status)
    preparation = OrderHistory.objects.filter(order_id=OuterRef('pk'), new_status='preparing')
    orders = Paginator(orders.select_related('table').prefetch_related('lines')
                       .annotate(has_started_preparation=Exists(preparation)).order_by('-created_at', '-pk'),
                       25).get_page(request.GET.get('page'))
    for order in orders:
        order.branch = branch
        order.can_edit = membership.role in ('owner', 'manager', 'cashier', 'waiter') and order_edit_allowed(
            order, order.has_started_preparation)
    return shell(request, restaurant, membership, 'orders',
        orders=orders,
        tables=branch.tables.all(), selected_table=selected_table,
        selected_status=selected_status, status_choices=Order.STATUSES,
        items=MenuItem.objects.filter(category__branch=branch,available=True))

def staff_order(request, slug):
    restaurant, membership, denied = scope(request, slug, ['owner','manager','cashier','waiter'])
    if denied: return denied
    branch = branch_for(request, restaurant, membership)
    table_key = request.GET.get('table', '') if request.method == 'GET' else ''
    if table_key and (len(table_key) > 19 or not table_key.isascii() or not table_key.isdigit()):
        raise Http404
    selected_table = (get_object_or_404(DiningTable, pk=table_key, branch=branch, active=True)
                      if table_key else None)
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
    categories = Category.objects.filter(branch=branch, visible=True).prefetch_related('items__sizes', 'items__modifier_groups__options')
    return shell(request, restaurant, membership, 'staff_order', categories=categories,
                 items_json=serialize_menu_items(categories), tables=branch.tables.filter(active=True),
                 selected_table=selected_table,
                 accepting=restaurant.active and branch.active and branch.accepting_orders and restaurant.subscription.valid())

def order_edit(request, slug, order_id):
    restaurant, membership, denied = scope(request, slug, ['owner', 'manager', 'cashier', 'waiter'])
    if denied: return denied
    branch = branch_for(request, restaurant, membership)
    order = get_object_or_404(Order.objects.select_related('table').prefetch_related('lines', 'revisions'),
                              pk=order_id, branch=branch)
    if request.method == 'POST':
        try:
            updated = edit_order(order, request.user, json.loads(request.body))
            return JsonResponse({'ok': True, 'order_url': reverse('manage_orders', args=[slug]),
                                 'version': updated.version})
        except (json.JSONDecodeError, ValidationError) as exc:
            return JsonResponse({'ok': False, 'error': error_text(exc)}, status=400)
    existing = [{'id': line.pk, 'item_name': line.item_name, 'size_name': line.size_name,
                 'quantity': line.quantity, 'unit_price': str(line.unit_price),
                 'modifiers': [mod['name'] for mod in line.modifiers]}
                for line in order.lines.all()]
    categories = Category.objects.filter(branch=branch, visible=True).prefetch_related('items__sizes', 'items__modifier_groups__options')
    order.branch = branch
    return shell(request, restaurant, membership, 'order_edit', order=order, existing_json=existing,
                 categories=categories, items_json=serialize_menu_items(categories),
                 can_edit=order_edit_allowed(order, order.history.filter(new_status='preparing').exists()),
                 can_add_order=order.session_token == order.table.session_token and order.table.active
                 and restaurant.active and branch.active and branch.accepting_orders and restaurant.subscription.valid())

def kitchen(request, slug):
    restaurant, membership, denied = scope(request, slug, ['owner','manager','kitchen'])
    if denied: return denied
    branch = branch_for(request, restaurant, membership)
    if branch.kitchen_opened_at is None:
        if Branch.objects.filter(pk=branch.pk, kitchen_opened_at__isnull=True).update(kitchen_opened_at=timezone.now()):
            audit(restaurant, request.user, 'kitchen.opened', f'branch={branch.pk}')
    station_key = request.GET.get('station', '')
    if station_key and station_key != 'unassigned' and not station_key.isdigit():
        raise Http404
    station = (get_object_or_404(KitchenStation, pk=station_key, branch=branch, active=True)
               if station_key and station_key != 'unassigned' else None)
    orders = list(Order.objects.filter(branch=branch, status__in=['new','accepted','preparing','ready']).select_related('table').prefetch_related('lines'))
    if station_key:
        orders = [order for order in orders if any(
            (line.station_id == station.pk if station else line.station_id is None)
            for line in order.lines.all())]
    for order in orders:
        order.station_lines = [line for line in order.lines.all() if not station_key or
                               (line.station_id == station.pk if station else line.station_id is None)]
        order.uses_stations = any(line.station_id for line in order.lines.all())
        order.all_lines_ready = all(line.prep_status == 'ready' for line in order.lines.all())
        order.kitchen_overdue = (order.status != 'ready' and
            order.created_at + timedelta(minutes=branch.kitchen_delay_minutes) < timezone.now())
    return shell(request, restaurant, membership, 'kitchen', orders=orders, tables=branch.tables.all(),
                 stations=KitchenStation.objects.filter(branch=branch, active=True),
                 unassigned_count=OrderLine.objects.filter(order__branch=branch,
                     order__status__in=['new','accepted','preparing','ready'], station__isnull=True).count(),
                 selected_station=station_key, station_view=bool(station_key),
                 kitchen_signature=order_feed_signature(orders, branch.kitchen_delay_minutes))

def order_feed_signature(orders, delay_minutes=0):
    now = timezone.now()
    payload = '|'.join(f'{order.pk}:{order.status}:{order.updated_at.isoformat()}:'
                       f'{int(bool(delay_minutes) and order.status != "ready" and order.created_at + timedelta(minutes=delay_minutes) < now)}'
                       for order in orders)
    return hashlib.sha256(payload.encode()).hexdigest()

def kitchen_feed(request, slug):
    restaurant, membership, denied = scope(request, slug, ['owner','manager','kitchen'])
    if denied: return denied
    branch = branch_for(request, restaurant, membership)
    station_key = request.GET.get('station', '')
    if station_key and station_key != 'unassigned' and not station_key.isdigit():
        raise Http404
    orders = Order.objects.filter(branch=branch, status__in=['new','accepted','preparing','ready']).only('id','status','updated_at','created_at')
    if station_key == 'unassigned':
        orders = orders.filter(lines__station__isnull=True).distinct()
    elif station_key:
        station = get_object_or_404(KitchenStation, pk=station_key, branch=branch, active=True)
        orders = orders.filter(lines__station=station).distinct()
    response = JsonResponse({'signature': order_feed_signature(orders, branch.kitchen_delay_minutes), 'count': orders.count(),
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
        advance_order(order, request.user, new, request.POST.get('reason',''), english=request.LANGUAGE_CODE == 'en')
        messages.success(request, 'تحدثت حالة الطلب.')
    except ValidationError as exc: messages.error(request, error_text(exc))
    return redirect('kitchen' if membership.role == 'kitchen' else 'manage_orders', slug=slug)


@require_POST
def order_line_status_change(request, slug, order_id, line_id):
    restaurant, membership, denied = scope(request, slug, ['owner', 'manager', 'kitchen'])
    if denied: return denied
    branch = branch_for(request, restaurant, membership)
    order = get_object_or_404(Order, pk=order_id, branch=branch)
    line = get_object_or_404(OrderLine, pk=line_id, order=order)
    station_key = request.POST.get('station', '')
    if station_key == 'unassigned':
        if line.station_id is not None: raise Http404
    elif station_key.isdigit():
        if line.station_id != int(station_key) or not KitchenStation.objects.filter(
                pk=station_key, branch=branch, active=True).exists():
            raise Http404
    else:
        raise Http404
    try:
        advance_order_line(line, request.user, request.POST.get('status', ''))
        messages.success(request, 'تحدثت حالة تحضير الصنف.')
    except ValidationError as exc:
        messages.error(request, error_text(exc))
    return redirect(f'{reverse("kitchen", args=[slug])}?{urlencode({"station": station_key})}')

@require_POST
def order_status_correct(request, slug, order_id):
    restaurant, membership, denied = scope(request, slug, ['owner', 'manager'])
    if denied: return denied
    branch = branch_for(request, restaurant, membership)
    order = get_object_or_404(Order, pk=order_id, branch=branch)
    try:
        correct_order_status(order, request.user, request.POST.get('reason', ''),
                             request.POST.get('expected_status', ''))
        messages.success(request, 'صُححت حالة الطلب وسُجل السبب.')
    except ValidationError as exc:
        messages.error(request, error_text(exc))
    return redirect('manage_orders', slug=slug)

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
    queue = request.GET.get('queue', 'all')
    calls = ServiceCall.objects.filter(table__branch=branch).select_related('table', 'assigned_to')
    if queue == 'open':
        calls = calls.filter(status__in=['open', 'acknowledged', 'in_progress'])
    elif queue == 'bill':
        calls = calls.filter(kind='bill')
    else:
        queue = 'all'
    calls = Paginator(calls.order_by('-created_at', '-pk'), 30).get_page(request.GET.get('page'))
    now = timezone.now()
    for call in calls:
        limit = branch.bill_response_minutes if call.kind == 'bill' else branch.waiter_response_minutes
        response_at = call.acknowledged_at or now
        call.response_overdue = response_at > call.created_at + timedelta(minutes=limit)
        call.awaiting_overdue = call.status == 'open' and call.response_overdue
    return shell(request, restaurant, membership, 'calls', calls=calls, queue=queue,
                 overdue_count=sum(call.awaiting_overdue for call in calls))

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
    station_key = request.GET.get('station', '')
    if station_key and station_key != 'unassigned' and not station_key.isdigit():
        raise Http404
    selected_station = (get_object_or_404(KitchenStation, pk=station_key, branch=branch)
                        if station_key and station_key != 'unassigned' else None)
    start_raw, end_raw = request.GET.get('from', '').strip(), request.GET.get('to', '').strip()
    start_date, end_date, filter_error = None, None, ''
    if start_raw or end_raw:
        try:
            start_date, end_date = parse_date(start_raw), parse_date(end_raw)
        except ValueError:
            start_date = end_date = None
        if not start_date or not end_date:
            filter_error = gettext('اختر تاريخ بداية ونهاية صالحين.')
        elif end_date < start_date or (end_date - start_date).days > 366:
            filter_error = gettext('نطاق التاريخ يجب أن يكون مرتبًا وألا يتجاوز 366 يومًا.')
        else:
            zone = timezone.get_current_timezone()
            start_at = timezone.make_aware(datetime.combine(start_date, time.min), zone)
            end_at = timezone.make_aware(datetime.combine(end_date + timedelta(days=1), time.min), zone)
            orders = orders.filter(created_at__gte=start_at, created_at__lt=end_at)
    if filter_error:
        start_date = end_date = None
    calls = ServiceCall.objects.filter(table__branch=branch)
    if start_date and end_date:
        calls = calls.filter(created_at__gte=start_at, created_at__lt=end_at)
    export_query = f'?export=csv&from={start_date.isoformat()}&to={end_date.isoformat()}' if start_date else '?export=csv'
    if request.GET.get('export') == 'csv':
        if filter_error:
            return HttpResponse(filter_error, status=400)
        headers = (['Order number', 'Table', 'Status', 'Payment status', 'Source', 'Order total', 'Created at']
                   if request.LANGUAGE_CODE == 'en' else
                   ['رقم الطلب', 'الطاولة', 'الحالة', 'حالة الدفع', 'المصدر', 'إجمالي الطلب', 'وقت الإنشاء'])
        labels = {}
        for field in ('status', 'payment_status', 'source'):
            labels[field] = {key: gettext(label) for key, label in Order._meta.get_field(field).choices}

        def csv_rows():
            yield '\ufeff'
            buffer = io.StringIO()
            writer = csv.writer(buffer)
            writer.writerow(headers)
            yield buffer.getvalue()
            for order in orders.select_related('table').order_by('pk').iterator(chunk_size=1000):
                buffer.seek(0)
                buffer.truncate(0)
                name = str(order.table.name)
                if name.lstrip().startswith(('=','+','-','@')):
                    name = "'" + name
                writer.writerow([order.pk, name, labels['status'].get(order.status, order.status),
                                 labels['payment_status'].get(order.payment_status, order.payment_status),
                                 labels['source'].get(order.source, order.source), order.total, order.created_at.isoformat()])
                yield buffer.getvalue()
        response = StreamingHttpResponse(csv_rows(), content_type='text/csv; charset=utf-8')
        response['Content-Disposition'] = 'attachment; filename="orders.csv"'
        response['Cache-Control'] = 'no-store'
        return response
    status_names = dict(Order.STATUSES)
    by_status = [{'label': status_names.get(row['status'], row['status']), 'count': row['count']}
                 for row in orders.values('status').annotate(count=Count('id'))]
    first_transition = OrderHistory.objects.filter(
        order_id=OuterRef('order_id'), new_status=OuterRef('new_status'),
    ).order_by('created_at', 'pk').values('pk')[:1]
    first_events = OrderHistory.objects.filter(order__in=orders, pk=Subquery(first_transition))
    accepted = first_events.filter(new_status='accepted')
    ready = first_events.filter(new_status='ready')
    responded = calls.filter(acknowledged_at__isnull=False)
    accepted_avg = accepted.aggregate(value=Avg(F('created_at') - F('order__created_at')))['value']
    ready_avg = ready.aggregate(value=Avg(F('created_at') - F('order__created_at')))['value']
    call_avg = responded.aggregate(value=Avg(F('acknowledged_at') - F('created_at')))['value']
    line_starts = OrderLinePrepHistory.objects.filter(
        line_id=OuterRef('line_id'), new_status='preparing').order_by('created_at').values('created_at')[:1]
    prepared_lines = OrderLinePrepHistory.objects.filter(
        line__order__in=orders, new_status='ready').annotate(started_at=Subquery(line_starts)).filter(
            started_at__isnull=False)
    if station_key == 'unassigned':
        prepared_lines = prepared_lines.filter(line__station__isnull=True)
    elif selected_station:
        prepared_lines = prepared_lines.filter(line__station=selected_station)
    station_avg = prepared_lines.aggregate(value=Avg(F('created_at') - F('started_at')))['value']
    collections = orders.filter(payment_status='paid')
    cancelled_collections = collections.filter(status='cancelled').aggregate(count=Count('pk'), total=Sum('total'))
    return shell(request, restaurant, membership, 'reports', total=orders.count(),
                 by_status=by_status,
                 by_source=orders.values('source').annotate(count=Count('id')),
                 total_value=orders.exclude(status='cancelled').aggregate(sum=Sum('total'))['sum'] or 0,
                 paid_value=collections.aggregate(sum=Sum('total'))['sum'] or 0,
                 cancelled_collections=cancelled_collections,
                 top_items=OrderLine.objects.filter(order__in=orders).exclude(order__status='cancelled').values('item_name').annotate(quantity=Sum('quantity')).order_by('-quantity')[:5],
                 accepted_time=format_duration(accepted_avg), ready_time=format_duration(ready_avg),
                 call_response_time=format_duration(call_avg), accepted_samples=accepted.count(),
                 ready_samples=ready.count(), call_samples=responded.count(),
                 station_time=format_duration(station_avg), station_samples=prepared_lines.count(),
                 station_key=station_key, stations=KitchenStation.objects.filter(branch=branch),
                 start_date=start_date, end_date=end_date, filter_error=filter_error,
                 start_raw=start_raw, end_raw=end_raw, export_query=export_query)

def format_duration(duration):
    if duration is None:
        return gettext('لا توجد بيانات')
    seconds = max(0, round(duration.total_seconds()))
    return gettext('%(minutes)s د %(seconds)s ث') % {'minutes': seconds // 60, 'seconds': seconds % 60}

def staff(request, slug):
    restaurant, membership, denied = scope(request, slug, ['owner','manager'])
    if denied: return denied
    if request.method == 'POST':
        action = request.POST.get('action', 'create')
        if action == 'invite':
            try:
                email = request.POST.get('email', '').strip().lower()
                role = request.POST.get('role', '')
                validate_email(email)
                if role not in ('manager', 'cashier', 'kitchen', 'waiter') or (
                        role == 'manager' and membership.role != 'owner'):
                    raise ValidationError('الدور المختار غير مسموح.')
                branch = get_object_or_404(Branch, pk=request.POST.get('branch_id'),
                                           restaurant=restaurant, active=True)
                if len(email) > User._meta.get_field('username').max_length:
                    raise ValidationError('Email address is too long.' if request.LANGUAGE_CODE == 'en' else 'عنوان البريد طويل جدًا.')
                existing_user = invited_account(email, english=request.LANGUAGE_CODE == 'en')
                if existing_user and Membership.objects.filter(user=existing_user, restaurant=restaurant).exists():
                    raise ValidationError('هذا البريد عضو في المطعم بالفعل.')
                with transaction.atomic():
                    subscription = check_creation_limit(restaurant, 'staff')
                    pending = StaffInvitation.objects.filter(restaurant=restaurant, accepted_at__isnull=True,
                        revoked_at__isnull=True, expires_at__gt=timezone.now())
                    if pending.filter(email=email).exists():
                        raise ValidationError('توجد دعوة نشطة لهذا البريد بالفعل.')
                    if restaurant.memberships.count() + pending.count() >= subscription.limit('max_staff'):
                        raise ValidationError('الدعوات الحالية تملأ المقاعد المتاحة في الباقة.')
                    if StaffInvitation.objects.filter(restaurant=restaurant,
                            created_at__gte=timezone.now()-timedelta(hours=1)).count() >= 20:
                        raise ValidationError('بلغت حد الدعوات لهذه الساعة. حاول لاحقًا.')
                    raw_token = secrets.token_urlsafe(32)
                    invitation = StaffInvitation.objects.create(restaurant=restaurant, branch=branch,
                        email=email, role=role, token_hash=hashlib.sha256(raw_token.encode()).hexdigest(),
                        invited_by=request.user, expires_at=timezone.now()+timedelta(days=7))
                    link = request.build_absolute_uri(localized_url('accept_staff_invitation', request.LANGUAGE_CODE == 'en', raw_token))
                    delivered = send_mail(gettext('دعوة للانضمام إلى Serveza'),
                        gettext('دعاك {venue} للانضمام إلى فريق {branch}.\nافتح الرابط خلال 7 أيام: {link}').format(venue=restaurant.name, branch=branch.name, link=link),
                        settings.DEFAULT_FROM_EMAIL, [email], fail_silently=False)
                    if delivered != 1:
                        raise ValidationError('تعذر إرسال الدعوة. حاول مجددًا بعد فحص إعدادات البريد.')
                    audit(restaurant, request.user, 'staff.invite', f'{email}: {role}; branch={branch.pk}')
                messages.success(request, 'أُرسلت الدعوة إلى البريد المحدد.')
            except (ValidationError, SMTPException, OSError) as exc:
                messages.error(request, error_text(exc))
            return redirect('staff', slug=slug)
        if action == 'invite_revoke':
            invitation = get_object_or_404(StaffInvitation, pk=request.POST.get('invitation_id'), restaurant=restaurant)
            if membership.role == 'manager' and invitation.role == 'manager':
                return render(request, '403.html', status=403)
            if invitation.pending():
                invitation.revoked_at = timezone.now()
                invitation.save(update_fields=['revoked_at'])
                audit(restaurant, request.user, 'staff.invite.revoke', str(invitation.pk))
                messages.success(request, 'أُلغيت الدعوة.')
            return redirect('staff', slug=slug)
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
        elif role not in ('manager','cashier','kitchen','waiter') or (role == 'manager' and membership.role != 'owner') or not email:
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
        invitations=restaurant.invitations.filter(accepted_at__isnull=True, revoked_at__isnull=True,
            expires_at__gt=timezone.now()).select_related('branch'),
        limit=restaurant.subscription.limit('max_staff'))

def accept_invitation_view(request, token):
    invitation = invitation_for_token(token)
    if not invitation or not invitation.pending():
        return render(request, 'invitation_invalid.html', status=410)
    try:
        existing_user = invited_account(invitation.email, english=request.LANGUAGE_CODE == 'en')
    except ValidationError as exc:
        return render(request, 'invitation_invalid.html', {'invitation_error': error_text(exc)}, status=409)
    if existing_user and not request.user.is_authenticated:
        return redirect(reverse('login') + '?' + urlencode({'next': request.path}))
    if request.user.is_authenticated and (not existing_user or request.user.pk != existing_user.pk):
        return render(request, '403.html', status=403)
    if request.method == 'POST':
        try:
            user, restaurant = accept_staff_invitation(token,
                request.POST.get('name', ''), request.POST.get('password', ''),
                request.user if request.user.is_authenticated else None)
            if not request.user.is_authenticated:
                login(request, user)
            messages.success(request, 'انضممت إلى فريق المطعم بنجاح.')
            return redirect('dashboard', slug=restaurant.slug)
        except ValidationError as exc:
            messages.error(request, error_text(exc))
        except IntegrityError:
            messages.error(request, 'Account details changed. Review the invitation and try again.'
                           if request.LANGUAGE_CODE == 'en' else 'تغيرت بيانات الحساب. راجع الدعوة وحاول مجددًا.')
    response = render(request, 'invitation_accept.html',
                      {'invitation': invitation, 'existing_user': bool(existing_user)})
    response['Cache-Control'] = 'no-store'
    return response

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
        branches=restaurant.branches.all(), limit=restaurant.subscription.limit('max_branches'))

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
    initial = {'name': restaurant.name, 'name_en': restaurant.name_en,
               'description': restaurant.description, 'branch_name': branch.name,
               'address': branch.address, 'phone': branch.phone,
               'opening_hours': branch.opening_hours,
               'waiter_response_minutes': branch.waiter_response_minutes,
               'bill_response_minutes': branch.bill_response_minutes,
               'kitchen_delay_minutes': branch.kitchen_delay_minutes,
               'show_contact_on_menu': branch.show_contact_on_menu,
               'accepting_orders': branch.accepting_orders}
    profile_form = RestaurantProfileForm(request.POST if request.method == 'POST' else None, initial=initial)
    if request.method == 'POST' and profile_form.is_valid():
        data = profile_form.cleaned_data
        with transaction.atomic():
            restaurant.name = data['name']
            restaurant.name_en = data['name_en']
            restaurant.description = data['description']
            restaurant.save(update_fields=['name','name_en','description'])
            branch.name = data['branch_name']
            branch.address = data['address']
            branch.phone = data['phone']
            branch.opening_hours = data['opening_hours']
            branch.waiter_response_minutes = data['waiter_response_minutes'] or branch.waiter_response_minutes
            branch.bill_response_minutes = data['bill_response_minutes'] or branch.bill_response_minutes
            branch.kitchen_delay_minutes = data['kitchen_delay_minutes'] or branch.kitchen_delay_minutes
            branch.show_contact_on_menu = data['show_contact_on_menu']
            branch.accepting_orders = data['accepting_orders']
            branch.save(update_fields=['name','address','phone','opening_hours',
                                       'waiter_response_minutes','bill_response_minutes','kitchen_delay_minutes',
                                       'show_contact_on_menu','accepting_orders'])
            audit(restaurant, request.user, 'restaurant.settings', f'branch={branch.pk}')
        messages.success(request, 'تم حفظ الإعدادات.')
        return redirect('restaurant_settings', slug=slug)
    subscription = restaurant.subscription
    usage = {
        'branches': restaurant.branches.count(),
        'tables': branch.tables.count(),
        'items': MenuItem.objects.filter(category__branch=branch).count(),
        'staff': restaurant.memberships.count(),
        'stations': KitchenStation.objects.filter(branch=branch).count(),
    }
    limits = {
        'branches': subscription.limit('max_branches'),
        'tables': subscription.limit('max_tables', branch),
        'items': subscription.limit('max_items', branch),
        'staff': subscription.limit('max_staff'),
        'stations': subscription.limit('max_stations', branch),
    }
    grandfathered = any(limit > getattr(subscription.plan, f'max_{key}') for key, limit in limits.items())
    over_limit = any(usage[key] > limit for key, limit in limits.items())
    plan_preview = None
    if (membership.role == 'owner' and request.method == 'GET'
            and request.GET.get('preview_plan', '').isascii()
            and request.GET.get('preview_plan', '').isdigit()):
        selected_plan = Plan.objects.filter(pk=request.GET['preview_plan']).first()
        if selected_plan:
            plan_preview = preview_plan_usage(restaurant, selected_plan)
    return shell(request, restaurant, membership, 'settings', profile_form=profile_form, subscription=subscription,
                 preview_table=branch.tables.filter(active=True).first(),
                 subscription_payments=SubscriptionPayment.objects.filter(restaurant=restaurant)[:20],
                 available_plans=Plan.objects.order_by('name') if membership.role == 'owner' else None,
                 plan_preview=plan_preview,
                 usage=usage, limits=limits, grandfathered=grandfathered, over_limit=over_limit)

def public_menu(request, token):
    table = get_object_or_404(DiningTable.objects.select_related('branch__restaurant__subscription'), public_token=token)
    subscription = getattr(table.branch.restaurant, 'subscription', None)
    if subscription is None or subscription.status == 'pending':
        return render(request, 'table_closed.html', status=404)
    if not table.active or not table.branch.active or not table.branch.restaurant.active:
        return render(request, 'table_closed.html', status=404)
    categories = Category.objects.filter(branch=table.branch, visible=True).prefetch_related('items__sizes', 'items__modifier_groups__options')
    items = serialize_menu_items(categories)
    category_names = {category.pk: category.name for category in categories}
    menu_categories = {}
    for item in items:
        category = item['category']
        item['category_name'] = menu_categories[category] = category_names[category]
        item['photo'] = menu_photo(item['name'], item['image'])
    accepting = table.branch.accepting_orders and subscription.valid()
    return render(request, 'public_menu.html', {
        'table': table, 'menu_categories': list(menu_categories.items()), 'items_json': items, 'accepting': accepting,
        'illustrative_photos': any(not item['image'] for item in items),
        'cart_session': hashlib.sha256(table.session_token.bytes).hexdigest(),
    })

def serialize_menu_items(categories):
    items = []
    for category in categories:
        for item in category.items.all():
            if item.available:
                items.append({'id': item.pk, 'category': category.pk, 'name': item.name, 'description': item.description,
                              'price': str(item.price), 'image': item.image.url if item.image else '',
                              'sizes': [{'id': size.pk, 'name': size.name, 'price': str(size.price)}
                                        for size in item.sizes.all() if size.available],
                              'groups': [{'name': g.name, 'min': g.min_select, 'max': g.max_select,
                                          'options': [{'id': o.pk, 'name': o.name, 'price': str(o.price)} for o in g.options.all() if o.available]}
                                         for g in item.modifier_groups.all()]})
    return items

@require_POST
def place_order(request, token):
    table = get_object_or_404(DiningTable, public_token=token)
    try:
        order = create_order(table, json.loads(request.body), require_session=True)
        return JsonResponse({'ok': True, 'order_url': reverse('track_order', args=[order.public_token]), 'order_number': order.pk})
    except (json.JSONDecodeError, ValidationError) as exc:
        return JsonResponse({'ok': False, 'error': error_text(exc)}, status=400)

@require_POST
def create_call(request, token):
    table = get_object_or_404(DiningTable, public_token=token)
    try:
        session_token = request.POST.get('session_token')
        if not session_token:
            raise ValidationError('حدّث صفحة الطاولة قبل إرسال الاستدعاء.')
        create_service_call(table, request.POST.get('kind'), session_token=session_token)
        messages.success(request, 'وصل طلبك لفريق الخدمة.')
    except ValidationError as exc: messages.error(request, error_text(exc))
    return redirect('public_menu', token=token)

def track_order(request, token):
    order = get_object_or_404(Order.objects.select_related('table__branch__restaurant').prefetch_related('lines'), public_token=token)
    table, branch = order.table, order.table.branch
    subscription = getattr(branch.restaurant, 'subscription', None)
    can_add_order = (order.session_token is not None and order.session_token == table.session_token
                     and table.active and branch.active and branch.accepting_orders
                     and branch.restaurant.active and subscription is not None and subscription.valid())
    return render(request, 'track.html', {'order': order, 'can_add_order': can_add_order})
