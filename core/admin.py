from django.utils.translation import gettext_lazy as _
from django.contrib import admin
from django import forms
from .subscriptions import freeze_plan_subscribers
from .plan_features import FEATURES, enabled_features
from .models import DemoRequest
from .models import AuditLog, Branch, Category, DiningTable, KitchenStation, Membership, MenuImportBatch, MenuItem, MenuSize, ModifierGroup, ModifierOption, Order, OrderHistory, OrderLine, OrderRevision, Plan, Restaurant, ServiceCall, StaffInvitation, Subscription, SubscriptionPayment

admin.site.site_header = _('Serveza | إدارة المنصة')
admin.site.site_title = 'Serveza'
admin.site.index_title = _('إدارة المنصة')

class ReadOnlyAdmin(admin.ModelAdmin):
    def has_add_permission(self, request): return False
    def has_change_permission(self, request, obj=None): return False
    def has_delete_permission(self, request, obj=None): return False

@admin.register(Restaurant)
class RestaurantAdmin(ReadOnlyAdmin):
    list_display = ('name','slug','active','created_at')
    search_fields = ('name','slug')
    list_filter = ('active',)


@admin.register(DemoRequest)
class DemoRequestAdmin(ReadOnlyAdmin):
    list_display = ('venue', 'name', 'email', 'language', 'created_at')
    search_fields = ('venue', 'email')
    list_filter = ('language', 'created_at')

    def has_view_permission(self, request, obj=None):
        return request.user.is_active and request.user.is_superuser

    def has_module_permission(self, request):
        return self.has_view_permission(request)

@admin.register(Branch)
class BranchAdmin(ReadOnlyAdmin):
    list_display = ('name','restaurant','active','accepting_orders')
    list_filter = ('active','accepting_orders')

@admin.register(Plan)
class PlanAdmin(admin.ModelAdmin):
    list_display = ('name','price_monthly','max_branches','max_tables','max_items','max_staff','max_stations')
    class PlanForm(forms.ModelForm):
        change_reason = forms.CharField(label=_('سبب التعديل'), required=False)
        enabled_features = forms.MultipleChoiceField(required=False, widget=forms.CheckboxSelectMultiple,
            choices=[(key, label[0]) for key, label in FEATURES.items()], label=_('الخدمات الإضافية المشمولة'))
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            from django.utils.translation import get_language
            self.fields['enabled_features'].choices = [(key, label[get_language() == 'en']) for key, label in FEATURES.items()]
            self.initial['enabled_features'] = enabled_features(self.instance) if self.instance.pk else list(FEATURES)
        class Meta:
            model = Plan
            fields = '__all__'
        def clean_change_reason(self):
            reason = self.cleaned_data['change_reason'].strip()
            if self.instance.pk and not reason:
                raise forms.ValidationError(_('اكتب سبب تعديل الباقة.'))
            return reason
    form = PlanForm
    def save_model(self, request, obj, form, change):
        request._plan_change_reason = form.cleaned_data.get('change_reason', '')[:120]
        if change:
            freeze_plan_subscribers(obj.pk, request.user)
        super().save_model(request, obj, form, change)
    def log_change(self, request, obj, message):
        return super().log_change(request, obj, f'{message}; reason={getattr(request, "_plan_change_reason", "")}')

@admin.register(Subscription)
class SubscriptionAdmin(ReadOnlyAdmin):
    list_display = ('restaurant','plan','status','ends_at')
    list_filter = ('status',)

@admin.register(SubscriptionPayment)
class SubscriptionPaymentAdmin(ReadOnlyAdmin):
    list_display = ('id', 'restaurant', 'amount', 'currency', 'period_start', 'period_end', 'method', 'reference', 'recorded_by', 'recorded_at')
    search_fields = ('restaurant__name', 'reference')

@admin.register(Membership)
class MembershipAdmin(ReadOnlyAdmin):
    list_display = ('user','restaurant','role','active')
    list_filter = ('role','active')

@admin.register(Order)
class OrderAdmin(ReadOnlyAdmin):
    list_display = ('id','branch','table','status','payment_status','total','paid_at','created_at')
    list_filter = ('status','payment_status','source')
    search_fields = ('id',)
    readonly_fields = ('public_token','idempotency_key','request_hash','total','paid_at','created_at')

@admin.register(AuditLog)
class AuditAdmin(ReadOnlyAdmin):
    list_display = ('created_at','restaurant','actor','action','detail')
    list_filter = ('action',)
    readonly_fields = ('restaurant','actor','action','detail','created_at')

from .models import CollectionCorrection, SubscriptionAgreement

for model in (Branch,Category,DiningTable,KitchenStation,MenuImportBatch,MenuItem,MenuSize,ModifierGroup,ModifierOption,OrderHistory,OrderLine,OrderRevision,ServiceCall,StaffInvitation,CollectionCorrection,SubscriptionAgreement):
    if not admin.site.is_registered(model):
        admin.site.register(model, ReadOnlyAdmin)
