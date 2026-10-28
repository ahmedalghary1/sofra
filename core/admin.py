from django.contrib import admin
from .models import AuditLog, Branch, Category, DiningTable, Membership, MenuItem, ModifierGroup, ModifierOption, Order, OrderHistory, OrderLine, Plan, Restaurant, ServiceCall, Subscription

@admin.register(Restaurant)
class RestaurantAdmin(admin.ModelAdmin):
    list_display = ('name','slug','active','created_at')
    search_fields = ('name','slug')
    list_filter = ('active',)

@admin.register(Branch)
class BranchAdmin(admin.ModelAdmin):
    list_display = ('name','restaurant','active','accepting_orders')
    list_filter = ('active','accepting_orders')

@admin.register(Plan)
class PlanAdmin(admin.ModelAdmin):
    list_display = ('name','price_monthly','max_branches','max_tables','max_items','max_staff')

@admin.register(Subscription)
class SubscriptionAdmin(admin.ModelAdmin):
    list_display = ('restaurant','plan','status','ends_at')
    list_filter = ('status',)

@admin.register(Membership)
class MembershipAdmin(admin.ModelAdmin):
    list_display = ('user','restaurant','role','active')
    list_filter = ('role','active')

@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    list_display = ('id','branch','table','status','payment_status','total','created_at')
    list_filter = ('status','payment_status','source')
    search_fields = ('id',)
    readonly_fields = ('public_token','idempotency_key','request_hash','total','created_at')

@admin.register(AuditLog)
class AuditAdmin(admin.ModelAdmin):
    list_display = ('created_at','restaurant','actor','action','detail')
    list_filter = ('action',)
    readonly_fields = ('restaurant','actor','action','detail','created_at')
    def has_add_permission(self, request): return False
    def has_change_permission(self, request, obj=None): return False
    def has_delete_permission(self, request, obj=None): return False

for model in (Category,DiningTable,MenuItem,ModifierGroup,ModifierOption,OrderHistory,OrderLine,ServiceCall):
    admin.site.register(model)
