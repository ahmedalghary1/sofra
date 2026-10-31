from django.contrib import admin
from django import forms
from .models import AuditLog, Branch, Category, DiningTable, Membership, MenuItem, MenuSize, ModifierGroup, ModifierOption, Order, OrderHistory, OrderLine, OrderRevision, Plan, Restaurant, ServiceCall, Subscription

class ReadOnlyAdmin(admin.ModelAdmin):
    def has_add_permission(self, request): return False
    def has_change_permission(self, request, obj=None): return False
    def has_delete_permission(self, request, obj=None): return False

@admin.register(Restaurant)
class RestaurantAdmin(ReadOnlyAdmin):
    list_display = ('name','slug','active','created_at')
    search_fields = ('name','slug')
    list_filter = ('active',)

@admin.register(Branch)
class BranchAdmin(ReadOnlyAdmin):
    list_display = ('name','restaurant','active','accepting_orders')
    list_filter = ('active','accepting_orders')

@admin.register(Plan)
class PlanAdmin(admin.ModelAdmin):
    list_display = ('name','price_monthly','max_branches','max_tables','max_items','max_staff')
    class PlanForm(forms.ModelForm):
        change_reason = forms.CharField(label='سبب التعديل', required=False)
        class Meta:
            model = Plan
            fields = '__all__'
        def clean_change_reason(self):
            reason = self.cleaned_data['change_reason'].strip()
            if self.instance.pk and not reason:
                raise forms.ValidationError('اكتب سبب تعديل الباقة.')
            return reason
    form = PlanForm
    def save_model(self, request, obj, form, change):
        request._plan_change_reason = form.cleaned_data.get('change_reason', '')[:120]
        super().save_model(request, obj, form, change)
    def log_change(self, request, obj, message):
        return super().log_change(request, obj, f'{message}; reason={getattr(request, "_plan_change_reason", "")}')

@admin.register(Subscription)
class SubscriptionAdmin(ReadOnlyAdmin):
    list_display = ('restaurant','plan','status','ends_at')
    list_filter = ('status',)

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

for model in (Branch,Category,DiningTable,MenuItem,MenuSize,ModifierGroup,ModifierOption,OrderHistory,OrderLine,OrderRevision,ServiceCall):
    if not admin.site.is_registered(model):
        admin.site.register(model, ReadOnlyAdmin)
