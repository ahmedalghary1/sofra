import secrets
import uuid
from django.conf import settings
from django.db import models
from django.utils import timezone

def token(): return secrets.token_urlsafe(24)

class Plan(models.Model):
    name = models.CharField(max_length=80, unique=True)
    price_monthly = models.DecimalField(max_digits=9, decimal_places=2, default=0)
    max_tables = models.PositiveIntegerField(default=10)
    max_items = models.PositiveIntegerField(default=50)
    max_staff = models.PositiveIntegerField(default=5)
    max_branches = models.PositiveIntegerField(default=1)
    def __str__(self): return self.name

class Restaurant(models.Model):
    name = models.CharField(max_length=120)
    name_en = models.CharField(max_length=120, blank=True)
    slug = models.SlugField(unique=True)
    description = models.CharField(max_length=240, blank=True)
    active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    def __str__(self): return self.name

class Branch(models.Model):
    restaurant = models.ForeignKey(Restaurant, on_delete=models.PROTECT, related_name='branches')
    name = models.CharField(max_length=120, default='الفرع الرئيسي')
    address = models.CharField(max_length=240, blank=True)
    phone = models.CharField(max_length=24, blank=True)
    opening_hours = models.CharField(max_length=160, blank=True)
    show_contact_on_menu = models.BooleanField(default=False)
    accepting_orders = models.BooleanField(default=True)
    active = models.BooleanField(default=True)
    def __str__(self): return f'{self.restaurant.name} · {self.name}'

class Membership(models.Model):
    ROLES = [('owner','مالك'),('manager','مدير'),('cashier','كاشير'),('kitchen','مطبخ'),('waiter','ويتر')]
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='memberships')
    restaurant = models.ForeignKey(Restaurant, on_delete=models.CASCADE, related_name='memberships')
    role = models.CharField(max_length=12, choices=ROLES)
    active = models.BooleanField(default=True)
    branches = models.ManyToManyField(Branch, blank=True, related_name='staff_memberships')
    class Meta:
        constraints = [models.UniqueConstraint(fields=['user','restaurant'], name='unique_restaurant_member')]

class Subscription(models.Model):
    restaurant = models.OneToOneField(Restaurant, on_delete=models.CASCADE, related_name='subscription')
    plan = models.ForeignKey(Plan, on_delete=models.PROTECT)
    status = models.CharField(max_length=12, choices=[('trial','تجربة'),('active','نشط'),('expired','منتهي')], default='trial')
    ends_at = models.DateTimeField()
    def valid(self): return self.status in ('trial','active') and self.ends_at > timezone.now()

class Category(models.Model):
    branch = models.ForeignKey(Branch, on_delete=models.CASCADE, related_name='categories')
    name = models.CharField(max_length=80)
    sort_order = models.PositiveIntegerField(default=0)
    visible = models.BooleanField(default=True)
    class Meta:
        ordering = ['sort_order', 'id']
        constraints = [models.UniqueConstraint(fields=['branch','name'], name='unique_branch_category')]
    def __str__(self): return self.name

class MenuItem(models.Model):
    category = models.ForeignKey(Category, on_delete=models.PROTECT, related_name='items')
    name = models.CharField(max_length=120)
    description = models.CharField(max_length=300, blank=True)
    price = models.DecimalField(max_digits=9, decimal_places=2)
    image = models.ImageField(upload_to='menu/', blank=True)
    available = models.BooleanField(default=True)
    featured = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    def __str__(self): return self.name

class MenuSize(models.Model):
    item = models.ForeignKey(MenuItem, on_delete=models.CASCADE, related_name='sizes')
    name = models.CharField(max_length=80)
    price = models.DecimalField(max_digits=9, decimal_places=2)
    available = models.BooleanField(default=True)
    class Meta:
        constraints = [models.UniqueConstraint(fields=['item', 'name'], name='unique_item_size')]
    def __str__(self): return f'{self.item.name} · {self.name}'

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

class DiningTable(models.Model):
    branch = models.ForeignKey(Branch, on_delete=models.CASCADE, related_name='tables')
    name = models.CharField(max_length=40)
    public_token = models.CharField(max_length=48, unique=True, default=token)
    active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    class Meta:
        constraints = [models.UniqueConstraint(fields=['branch','name'], name='unique_branch_table')]
    def __str__(self): return self.name

class Order(models.Model):
    STATUSES = [('new','جديد'),('accepted','تم الاستلام'),('preparing','قيد التحضير'),('ready','جاهز'),('served','تم التقديم'),('cancelled','ملغي')]
    branch = models.ForeignKey(Branch, on_delete=models.PROTECT, related_name='orders')
    table = models.ForeignKey(DiningTable, on_delete=models.PROTECT, related_name='orders')
    public_token = models.CharField(max_length=48, unique=True, default=token)
    idempotency_key = models.UUIDField()
    request_hash = models.CharField(max_length=64)
    source = models.CharField(max_length=8, choices=[('qr','QR'),('staff','موظف')], default='qr')
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
        constraints = [models.UniqueConstraint(fields=['table','idempotency_key'], name='unique_table_request')]
        indexes = [models.Index(fields=['branch','status','created_at'])]

class OrderLine(models.Model):
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name='lines')
    item = models.ForeignKey(MenuItem, null=True, on_delete=models.SET_NULL)
    item_name = models.CharField(max_length=120)
    size_name = models.CharField(max_length=80, blank=True)
    base_price = models.DecimalField(max_digits=9, decimal_places=2, null=True, blank=True)
    unit_price = models.DecimalField(max_digits=9, decimal_places=2)
    quantity = models.PositiveSmallIntegerField()
    modifiers = models.JSONField(default=list)
    line_total = models.DecimalField(max_digits=10, decimal_places=2)

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
    created_at = models.DateTimeField(auto_now_add=True)

class LoginAttempt(models.Model):
    key = models.CharField(max_length=64)
    created_at = models.DateTimeField(auto_now_add=True)
    class Meta:
        indexes = [models.Index(fields=['key','created_at'])]
