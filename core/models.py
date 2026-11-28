import secrets
import uuid
from typing import ClassVar
from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator, MaxValueValidator
from decimal import Decimal
from django.db import models
from django.utils import timezone
from django.utils.translation import get_language
from .branding import validate_menu_color

def token(): return secrets.token_urlsafe(24)


def default_qr_origin():
    return settings.SITE_URL.rstrip('/')


class DemoRequest(models.Model):
    STATES = [('new', 'جديد'), ('contacted', 'تم التواصل'), ('closed', 'مغلق')]
    status = models.CharField(max_length=12, choices=STATES, default='new')
    submission_key = models.UUIDField(unique=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=100)
    venue = models.CharField(max_length=140)
    email = models.EmailField()
    message = models.TextField(blank=True, max_length=1600)
    language = models.CharField(max_length=2)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering: ClassVar[list] = ['-created_at']

    def __str__(self):
        return self.venue

class Plan(models.Model):
    name = models.CharField(max_length=80, unique=True)
    price_monthly = models.DecimalField(max_digits=9, decimal_places=2, default=0, validators=[MinValueValidator(Decimal('0'))])
    max_tables = models.PositiveIntegerField(default=10)
    max_items = models.PositiveIntegerField(default=50)
    max_staff = models.PositiveIntegerField(default=5)
    max_branches = models.PositiveIntegerField(default=1)
    max_stations = models.PositiveIntegerField(default=1)
    staff_example = models.JSONField(default=dict, blank=True, editable=False)
    enabled_features = models.JSONField(default=None, null=True, blank=True)

    class Meta:
        constraints = [models.CheckConstraint(condition=models.Q(price_monthly__gte=0), name='plan_price_nonnegative')]

    def clean(self):
        super().clean()
        from .plan_features import FEATURES
        if self.enabled_features is not None and (not isinstance(self.enabled_features, list) or
                any(not isinstance(key, str) or key not in FEATURES for key in self.enabled_features)):
            raise ValidationError({'enabled_features': 'Choose valid plan services.' if get_language() == 'en' else 'اختر خدمات باقة صالحة.'})
        if self.max_stations and self.max_staff is not None and self.max_staff < 2:
            raise ValidationError({'max_staff': 'Kitchen stations require seats for the owner and at least one kitchen account.'
                                   if get_language() == 'en' else
                                   'تحتاج محطات المطبخ إلى حساب للمالك وحساب واحد للمطبخ على الأقل.'})

    def __str__(self): return self.name

class Restaurant(models.Model):
    name = models.CharField(max_length=120)
    name_en = models.CharField(max_length=120, blank=True)
    slug = models.SlugField(unique=True)
    description = models.CharField(max_length=240, blank=True)
    logo = models.ImageField(upload_to='restaurant-logos/', blank=True)
    menu_primary_color = models.CharField(max_length=7, default='#1d6150', validators=[validate_menu_color])
    menu_background_color = models.CharField(max_length=7, default='#f8f8f4', validators=[validate_menu_color])
    menu_text_color = models.CharField(max_length=7, default='#193b34', validators=[validate_menu_color])
    qr_color = models.CharField(max_length=7, default='#193b34', validators=[validate_menu_color])
    qr_number_color = models.CharField(max_length=7, default='#193b34', validators=[validate_menu_color])
    qr_public_origin = models.URLField(max_length=255, default=default_qr_origin, editable=False)
    online_ordering_enabled = models.BooleanField(default=False)
    online_pickup_enabled = models.BooleanField(default=True)
    online_delivery_enabled = models.BooleanField(default=False)
    delivery_fee = models.DecimalField(max_digits=9, decimal_places=2, default=0,
                                      validators=[MinValueValidator(Decimal('0'))])
    active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    def __str__(self): return self.name

    def save(self, *args, **kwargs):
        if self.pk and Restaurant.objects.filter(pk=self.pk).exclude(qr_public_origin=self.qr_public_origin).exists():
            raise ValidationError('The printed QR domain is fixed. Keep the existing domain serving its links.' if get_language() == 'en'
                                  else 'دومين QR المطبوع ثابت. حافظ على خدمة الروابط من الدومين الحالي.')
        return super().save(*args, **kwargs)

class Branch(models.Model):
    restaurant = models.ForeignKey(Restaurant, on_delete=models.PROTECT, related_name='branches')
    name = models.CharField(max_length=120, default='الفرع الرئيسي')
    address = models.CharField(max_length=240, blank=True)
    phone = models.CharField(max_length=24, blank=True)
    opening_hours = models.CharField(max_length=160, blank=True)
    waiter_response_minutes = models.PositiveSmallIntegerField(default=5)
    bill_response_minutes = models.PositiveSmallIntegerField(default=5)
    kitchen_delay_minutes = models.PositiveSmallIntegerField(default=20)
    kitchen_opened_at = models.DateTimeField(null=True, blank=True)
    show_contact_on_menu = models.BooleanField(default=False)
    accepting_orders = models.BooleanField(default=True)
    recommendations_enabled = models.BooleanField(default=False)
    recommendations_learning_enabled = models.BooleanField(default=False)
    geofence_enabled = models.BooleanField(default=False)
    latitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True,
                                  validators=[MinValueValidator(-90), MaxValueValidator(90)])
    longitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True,
                                   validators=[MinValueValidator(-180), MaxValueValidator(180)])
    geofence_radius_m = models.PositiveIntegerField(default=50, validators=[MinValueValidator(10), MaxValueValidator(5000)])
    geofence_version = models.UUIDField(default=uuid.uuid4, editable=False)
    active = models.BooleanField(default=True)
    archived = models.BooleanField(default=False)
    class Meta:
        constraints = [
            models.CheckConstraint(condition=models.Q(geofence_radius_m__gte=10, geofence_radius_m__lte=5000), name='branch_geofence_radius_range'),
            models.CheckConstraint(condition=(models.Q(latitude__isnull=True, longitude__isnull=True, geofence_enabled=False) |
                models.Q(latitude__isnull=False, longitude__isnull=False, latitude__gte=-90, latitude__lte=90,
                         longitude__gte=-180, longitude__lte=180)), name='branch_geofence_valid_location'),
        ]
    def __str__(self): return f'{self.restaurant.name} · {self.name}'

class Membership(models.Model):
    ROLES = [('owner','مالك'),('manager','مدير'),('cashier','كاشير'),('kitchen','مطبخ'),('waiter','ويتر')]
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='memberships')
    restaurant = models.ForeignKey(Restaurant, on_delete=models.CASCADE, related_name='memberships')
    role = models.CharField(max_length=12, choices=ROLES)
    active = models.BooleanField(default=True)
    archived = models.BooleanField(default=False)
    branches = models.ManyToManyField(Branch, blank=True, related_name='staff_memberships')
    class Meta:
        constraints = [models.UniqueConstraint(fields=['user','restaurant'], name='unique_restaurant_member')]

class OwnerEmailVerification(models.Model):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='owner_email_verification')
    token_hash = models.CharField(max_length=64, unique=True)
    expires_at = models.DateTimeField()
    sent_at = models.DateTimeField(auto_now_add=True)
    verified_at = models.DateTimeField(null=True, blank=True)

class StaffInvitation(models.Model):
    ROLES = [('manager', 'مدير'), ('cashier', 'كاشير'), ('kitchen', 'مطبخ'), ('waiter', 'ويتر')]
    restaurant = models.ForeignKey(Restaurant, on_delete=models.CASCADE, related_name='invitations')
    branch = models.ForeignKey(Branch, on_delete=models.CASCADE)
    email = models.EmailField()
    role = models.CharField(max_length=12, choices=ROLES)
    token_hash = models.CharField(max_length=64, unique=True)
    invited_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL,
                                   related_name='sent_staff_invitations')
    accepted_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                                    related_name='accepted_staff_invitations')
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    accepted_at = models.DateTimeField(null=True, blank=True)
    revoked_at = models.DateTimeField(null=True, blank=True)
    def pending(self):
        return not self.accepted_at and not self.revoked_at and self.expires_at > timezone.now()

class Subscription(models.Model):
    restaurant = models.OneToOneField(Restaurant, on_delete=models.CASCADE, related_name='subscription')
    plan = models.ForeignKey(Plan, on_delete=models.PROTECT, null=True, blank=True)
    status = models.CharField(max_length=12, choices=[('pending','بانتظار التفعيل'),('trial','تجربة'),('active','نشط'),('expired','منتهي')], default='trial')
    ends_at = models.DateTimeField(null=True, blank=True)
    activation_requested_at = models.DateTimeField(null=True, blank=True)
    grandfathered_limits = models.JSONField(default=dict, blank=True, editable=False)
    agreed_terms = models.JSONField(default=dict, blank=True, editable=False)

    class Meta:
        constraints = [models.CheckConstraint(
            condition=(models.Q(status='pending', plan__isnull=True, ends_at__isnull=True) |
                       (~models.Q(status='pending') & models.Q(plan__isnull=False, ends_at__isnull=False))),
            name='subscription_terms_match_pending_status')]

    def valid(self):
        return self.status in ('trial','active') and self.ends_at > timezone.now()

    def limit(self, field, branch=None):
        allowances = self.grandfathered_limits
        if branch is not None:
            allowances = allowances.get('branches', {}).get(str(branch.pk), {})
        return max(self.agreed_terms.get('limits', {}).get(field, getattr(self.plan, field)), allowances.get(field, 0))

class SubscriptionPayment(models.Model):
    METHODS = [('cash', 'نقدًا'), ('bank', 'تحويل بنكي'), ('other', 'وسيلة أخرى')]
    restaurant = models.ForeignKey(Restaurant, on_delete=models.PROTECT, related_name='subscription_payments')
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    currency = models.CharField(max_length=3, default='EGP')
    period_start = models.DateField()
    period_end = models.DateField()
    method = models.CharField(max_length=8, choices=METHODS)
    reference = models.CharField(max_length=80)
    recorded_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL)
    recorded_at = models.DateTimeField(auto_now_add=True)
    class Meta:
        constraints = [models.UniqueConstraint(fields=['restaurant', 'reference'], name='unique_subscription_payment_reference')]
        ordering = ['-recorded_at', '-id']

class Category(models.Model):
    branch = models.ForeignKey(Branch, on_delete=models.CASCADE, related_name='categories')
    name = models.CharField(max_length=80)
    sort_order = models.PositiveIntegerField(default=0)
    visible = models.BooleanField(default=True)
    class Meta:
        ordering = ['sort_order', 'id']
        constraints = [models.UniqueConstraint(fields=['branch','name'], name='unique_branch_category')]
    def __str__(self): return self.name

    def save(self, *args, **kwargs):
        if self.pk and Category.objects.filter(pk=self.pk).exclude(branch_id=self.branch_id).exists():
            if ItemRecommendation.objects.filter(models.Q(source_item__category_id=self.pk) |
                                                 models.Q(target_item__category_id=self.pk)).exists():
                raise ValidationError('Remove recommendation links before moving this category.' if get_language() == 'en'
                                      else 'احذف روابط الاقتراحات قبل نقل التصنيف إلى فرع آخر.')
        return super().save(*args, **kwargs)

class KitchenStation(models.Model):
    branch = models.ForeignKey(Branch, on_delete=models.PROTECT, related_name='stations')
    name = models.CharField(max_length=80)
    active = models.BooleanField(default=True)
    archived = models.BooleanField(default=False)
    class Meta:
        constraints = [models.UniqueConstraint(fields=['branch', 'name'], name='unique_branch_station_name')]
        ordering = ['id']
    def __str__(self): return self.name

class MenuItem(models.Model):
    category = models.ForeignKey(Category, on_delete=models.PROTECT, related_name='items')
    station = models.ForeignKey(KitchenStation, null=True, blank=True, on_delete=models.PROTECT)
    name = models.CharField(max_length=120)
    description = models.CharField(max_length=300, blank=True)
    price = models.DecimalField(max_digits=9, decimal_places=2)
    image = models.ImageField(upload_to='menu/', blank=True)
    available = models.BooleanField(default=True)
    archived = models.BooleanField(default=False)
    featured = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    def __str__(self): return self.name

    def save(self, *args, **kwargs):
        if self.pk and self.category_id:
            branch_id = Category.objects.values_list('branch_id', flat=True).get(pk=self.category_id)
            if ItemRecommendation.objects.filter(models.Q(source_item_id=self.pk) |
                    models.Q(target_item_id=self.pk)).exclude(branch_id=branch_id).exists():
                raise ValidationError('Remove recommendation links before moving this item.' if get_language() == 'en'
                                      else 'احذف روابط الاقتراحات قبل نقل الصنف إلى فرع آخر.')
        return super().save(*args, **kwargs)

class MenuSize(models.Model):
    item = models.ForeignKey(MenuItem, on_delete=models.CASCADE, related_name='sizes')
    name = models.CharField(max_length=80)
    price = models.DecimalField(max_digits=9, decimal_places=2)
    available = models.BooleanField(default=True)
    class Meta:
        constraints = [models.UniqueConstraint(fields=['item', 'name'], name='unique_item_size')]
    def __str__(self): return f'{self.item.name} · {self.name}'


class ItemRecommendation(models.Model):
    branch = models.ForeignKey(Branch, on_delete=models.CASCADE)
    source_item = models.ForeignKey(MenuItem, on_delete=models.CASCADE, related_name='recommendations')
    target_item = models.ForeignKey(MenuItem, on_delete=models.CASCADE, related_name='recommended_by')
    priority = models.PositiveSmallIntegerField(default=0)
    active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['priority', 'pk']
        constraints = [
            models.UniqueConstraint(fields=['branch', 'source_item', 'target_item'], name='unique_recommendation_pair'),
            models.CheckConstraint(condition=~models.Q(source_item=models.F('target_item')), name='recommendation_not_self'),
        ]
        indexes = [models.Index(fields=['branch', 'source_item', 'active'])]

    def clean(self):
        super().clean()
        ids = {self.source_item_id, self.target_item_id}
        if (None in ids or len(ids) != 2 or not self.branch_id or
                MenuItem.objects.filter(pk__in=ids, category__branch_id=self.branch_id).count() != 2):
            raise ValidationError('Choose two different items from this branch.' if get_language() == 'en'
                                  else 'اختر صنفين مختلفين من الفرع نفسه.')

    def save(self, *args, **kwargs):
        # Enforce tenant validation on every normal writer, including Django admin.
        self.full_clean()
        return super().save(*args, **kwargs)


class RecommendationExposure(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    branch = models.ForeignKey(Branch, on_delete=models.CASCADE)
    scope_key = models.CharField(max_length=64)
    cart_key = models.CharField(max_length=64)
    channel = models.CharField(max_length=6, choices=[('qr', 'QR'), ('online', 'Online')])
    candidates = models.JSONField(default=list)
    version = models.CharField(max_length=20, default='rules-v1')
    created_at = models.DateTimeField(auto_now_add=True)
    shown_at = models.DateTimeField(null=True)
    expires_at = models.DateTimeField()
    order = models.ForeignKey('Order', null=True, on_delete=models.SET_NULL)

    class Meta:
        indexes = [models.Index(fields=['branch', 'scope_key', 'cart_key']), models.Index(fields=['created_at'])]


class RecommendationEvent(models.Model):
    exposure = models.ForeignKey(RecommendationExposure, on_delete=models.CASCADE, related_name='events')
    item = models.ForeignKey(MenuItem, on_delete=models.CASCADE)
    kind = models.CharField(max_length=5, default='added', choices=[('added', 'Added')])
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['exposure', 'item', 'kind'], name='unique_recommendation_event')]


class RecommendationConversion(models.Model):
    exposure = models.ForeignKey(RecommendationExposure, null=True, on_delete=models.SET_NULL, related_name='conversions')
    impression_id = models.UUIDField()
    shown_at = models.DateTimeField()
    line = models.OneToOneField('OrderLine', on_delete=models.CASCADE, related_name='recommendation_conversion')
    quantity = models.PositiveSmallIntegerField()
    amount = models.DecimalField(max_digits=10, decimal_places=2)


class RecommendationDailySummary(models.Model):
    branch = models.ForeignKey(Branch, on_delete=models.CASCADE)
    day = models.DateField()
    shown = models.PositiveIntegerField(default=0)
    added = models.PositiveIntegerField(default=0)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['branch', 'day'], name='unique_recommendation_daily_summary')]


class RecommendationPairStat(models.Model):
    branch = models.ForeignKey(Branch, on_delete=models.CASCADE)
    source_item = models.ForeignKey(MenuItem, on_delete=models.CASCADE, related_name='+')
    target_item = models.ForeignKey(MenuItem, on_delete=models.CASCADE, related_name='+')
    eligible_orders = models.PositiveIntegerField()
    source_orders = models.PositiveIntegerField()
    target_orders = models.PositiveIntegerField()
    pair_orders = models.PositiveIntegerField()
    period_start = models.DateTimeField()
    built_at = models.DateTimeField()

    class Meta:
        constraints = [models.UniqueConstraint(fields=['branch', 'source_item', 'target_item'], name='unique_recommendation_stat')]

class MenuImportBatch(models.Model):
    token = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    branch = models.ForeignKey(Branch, on_delete=models.CASCADE, related_name='menu_imports')
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    rows = models.JSONField()
    created_at = models.DateTimeField(auto_now_add=True)
    committed_at = models.DateTimeField(null=True, blank=True)

class ModifierGroup(models.Model):
    item = models.ForeignKey(MenuItem, on_delete=models.CASCADE, related_name='modifier_groups')
    name = models.CharField(max_length=80)
    min_select = models.PositiveIntegerField(default=0)
    max_select = models.PositiveIntegerField(default=1)

class ModifierOption(models.Model):
    group = models.ForeignKey(ModifierGroup, on_delete=models.CASCADE, related_name='options')
    name = models.CharField(max_length=80)
    price = models.DecimalField(max_digits=9, decimal_places=2, default=0)
    available = models.BooleanField(default=True)
    sort_order = models.PositiveSmallIntegerField(default=0)
    class Meta:
        ordering = ['sort_order', 'id']

class TableBatch(models.Model):
    branch = models.ForeignKey(Branch, on_delete=models.PROTECT, related_name='table_batches')
    submission_key = models.UUIDField()
    start_number = models.PositiveIntegerField()
    count = models.PositiveSmallIntegerField()
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['branch', 'submission_key'], name='unique_table_batch_request')]


class DiningTable(models.Model):
    branch = models.ForeignKey(Branch, on_delete=models.CASCADE, related_name='tables')
    name = models.CharField(max_length=40)
    number = models.PositiveIntegerField(null=True, blank=True)
    batch = models.ForeignKey(TableBatch, null=True, blank=True, on_delete=models.SET_NULL, related_name='tables')
    public_token = models.CharField(max_length=48, unique=True, default=token)
    session_token = models.UUIDField(default=uuid.uuid4)
    active = models.BooleanField(default=True)
    archived = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    class Meta:
        constraints = [models.UniqueConstraint(fields=['branch','name'], name='unique_branch_table'),
                       models.UniqueConstraint(fields=['branch','number'], condition=models.Q(number__isnull=False), name='unique_branch_table_number')]
    def __str__(self): return self.name

    def save(self, *args, **kwargs):
        if self.pk and DiningTable.objects.filter(pk=self.pk).exclude(public_token=self.public_token, branch_id=self.branch_id).exists():
            raise ValidationError('Use the owner emergency revocation flow to replace a QR code. Tables cannot move between branches.'
                                  if get_language() == 'en' else 'تغيير QR متاح عبر إجراء الطوارئ للمالك فقط. لا يمكن نقل الطاولة بين الفروع.')
        if self.batch_id and not TableBatch.objects.filter(pk=self.batch_id, branch_id=self.branch_id).exists():
            raise ValidationError('Invalid table batch.' if get_language() == 'en' else 'دفعة الطاولات غير صالحة.')
        return super().save(*args, **kwargs)

class Order(models.Model):
    STATUSES = [('new','جديد'),('accepted','تم الاستلام'),('preparing','قيد التحضير'),('ready','جاهز'),('served','تم التقديم'),('cancelled','ملغي')]
    branch = models.ForeignKey(Branch, on_delete=models.PROTECT, related_name='orders')
    table = models.ForeignKey(DiningTable, on_delete=models.PROTECT, related_name='orders', null=True, blank=True)
    public_token = models.CharField(max_length=48, unique=True, default=token)
    idempotency_key = models.UUIDField()
    session_token = models.UUIDField(null=True, blank=True)
    request_hash = models.CharField(max_length=64)
    source = models.CharField(max_length=8, choices=[('qr','QR'),('staff','موظف'),('online','أونلاين')], default='qr')
    fulfillment = models.CharField(max_length=10, choices=[('dine_in','داخل المطعم'),
                                   ('pickup','استلام من الفرع'),('delivery','توصيل')], default='dine_in')
    customer_name = models.CharField(max_length=100, blank=True)
    customer_phone = models.CharField(max_length=24, blank=True)
    delivery_address = models.CharField(max_length=500, blank=True)
    delivery_fee = models.DecimalField(max_digits=9, decimal_places=2, default=0,
                                      validators=[MinValueValidator(Decimal('0'))])
    status = models.CharField(max_length=12, choices=STATUSES, default='new')
    payment_status = models.CharField(max_length=10, choices=[('unpaid','غير مدفوع'),('paid','مدفوع')], default='unpaid')
    paid_at = models.DateTimeField(null=True, blank=True)
    total = models.DecimalField(max_digits=10, decimal_places=2)
    note = models.CharField(max_length=500, blank=True)
    version = models.PositiveIntegerField(default=0)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    class Meta:
        ordering = ['-created_at']
        constraints = [models.UniqueConstraint(fields=['table','idempotency_key'], name='unique_table_request'),
            models.UniqueConstraint(fields=['branch', 'idempotency_key'], condition=models.Q(source='online'),
                                    name='unique_online_branch_request'),
            models.CheckConstraint(condition=(models.Q(source='online', table__isnull=True,
                session_token__isnull=True, fulfillment__in=['pickup', 'delivery']) |
                models.Q(source__in=['qr', 'staff'], table__isnull=False, fulfillment='dine_in', delivery_fee=0)),
                name='order_fulfillment_context'),
            models.CheckConstraint(condition=models.Q(delivery_fee__gte=0, delivery_fee__lte=models.F('total')),
                                   name='order_delivery_fee_valid')]
        indexes = [models.Index(fields=['branch','status','created_at'])]

    @property
    def items_subtotal(self):
        return self.total - self.delivery_fee

    @property
    def fulfillment_label(self):
        labels = {'pickup': ('استلام من الفرع', 'Branch pickup'), 'delivery': ('توصيل', 'Delivery')}
        pair = labels.get(self.fulfillment, ('داخل المطعم', 'Dine in'))
        return pair[get_language() == 'en']

    @property
    def service_status(self):
        if self.source == 'online':
            labels = {'ready': ('جاهز للاستلام' if self.fulfillment == 'pickup' else 'جاهز للتوصيل',
                                'Ready for pickup' if self.fulfillment == 'pickup' else 'Ready for delivery'),
                      'served': ('تم التسليم', 'Delivered')}
            if self.status in labels:
                return labels[self.status][get_language() == 'en']
        from django.utils.translation import gettext
        return gettext(self.get_status_display())

class OrderLine(models.Model):
    PREP_STATUSES = [('new', 'لم يبدأ'), ('preparing', 'قيد التحضير'), ('ready', 'جاهز')]
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name='lines')
    item = models.ForeignKey(MenuItem, null=True, on_delete=models.SET_NULL)
    station = models.ForeignKey(KitchenStation, null=True, blank=True, on_delete=models.PROTECT)
    prep_status = models.CharField(max_length=12, choices=PREP_STATUSES, default='new')
    item_name = models.CharField(max_length=120)
    size_name = models.CharField(max_length=80, blank=True)
    base_price = models.DecimalField(max_digits=9, decimal_places=2, null=True, blank=True)
    unit_price = models.DecimalField(max_digits=9, decimal_places=2)
    quantity = models.PositiveSmallIntegerField()
    modifiers = models.JSONField(default=list)
    line_total = models.DecimalField(max_digits=10, decimal_places=2)

class OrderLinePrepHistory(models.Model):
    line = models.ForeignKey(OrderLine, on_delete=models.CASCADE, related_name='prep_history')
    old_status = models.CharField(max_length=12)
    new_status = models.CharField(max_length=12)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL)
    created_at = models.DateTimeField(auto_now_add=True)

class OrderHistory(models.Model):
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name='history')
    old_status = models.CharField(max_length=12)
    new_status = models.CharField(max_length=12)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL)
    reason = models.CharField(max_length=240, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

class OrderRevision(models.Model):
    order = models.ForeignKey(Order, on_delete=models.PROTECT, related_name='revisions')
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL)
    key = models.UUIDField()
    request_hash = models.CharField(max_length=64)
    version = models.PositiveIntegerField()
    reason = models.CharField(max_length=240)
    old_total = models.DecimalField(max_digits=10, decimal_places=2)
    new_total = models.DecimalField(max_digits=10, decimal_places=2)
    before = models.JSONField()
    after = models.JSONField()
    created_at = models.DateTimeField(auto_now_add=True)
    class Meta:
        constraints = [models.UniqueConstraint(fields=['order', 'key'], name='unique_order_revision_key')]

class ServiceCall(models.Model):
    table = models.ForeignKey(DiningTable, on_delete=models.PROTECT, related_name='calls')
    kind = models.CharField(max_length=8, choices=[('waiter','ويتر'),('bill','الحساب')])
    status = models.CharField(max_length=14, choices=[('open','جديد'),('acknowledged','تم الاستلام'),('in_progress','قيد التنفيذ'),('done','مكتمل'),('cancelled','ملغي')], default='open')
    assigned_to = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL)
    created_at = models.DateTimeField(auto_now_add=True)
    acknowledged_at = models.DateTimeField(null=True, blank=True)
    done_at = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)
    class Meta:
        constraints = [models.UniqueConstraint(fields=['table','kind'], condition=models.Q(status__in=['open','acknowledged','in_progress']), name='unique_active_service_call')]

class AuditLog(models.Model):
    restaurant = models.ForeignKey(Restaurant, on_delete=models.PROTECT)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL)
    action = models.CharField(max_length=80)
    detail = models.CharField(max_length=240, blank=True)
    changes = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

class LoginAttempt(models.Model):
    key = models.CharField(max_length=64)
    created_at = models.DateTimeField(auto_now_add=True)
    class Meta:
        indexes = [models.Index(fields=['key','created_at'])]


class RateLimitBucket(models.Model):
    key = models.CharField(max_length=64, primary_key=True)
    started_at = models.DateTimeField()
    count = models.PositiveIntegerField(default=0)


class CollectionCorrection(models.Model):
    """An immutable reversal of a mistaken manual entry, never a payment gateway refund."""
    order = models.ForeignKey(Order, on_delete=models.PROTECT, related_name='collection_corrections')
    key = models.UUIDField()
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    original_paid_at = models.DateTimeField()
    reason = models.CharField(max_length=240)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['order', 'key'], name='unique_collection_correction'),
                       models.CheckConstraint(condition=models.Q(amount__gt=0), name='correction_amount_positive')]


class SubscriptionAgreement(models.Model):
    subscription = models.ForeignKey(Subscription, on_delete=models.PROTECT, related_name='agreements')
    terms = models.JSONField()
    status = models.CharField(max_length=12)
    starts_on = models.DateField()
    ends_at = models.DateTimeField()
    payment = models.ForeignKey(SubscriptionPayment, null=True, blank=True, on_delete=models.PROTECT)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL)
    reason = models.CharField(max_length=240)
    created_at = models.DateTimeField(auto_now_add=True)
