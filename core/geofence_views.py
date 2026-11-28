from django.contrib import messages
from django.core.exceptions import ValidationError
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods, require_safe

from .geofence_forms import GeofenceForm
from .geofencing import presence_policy, save_geofence
from .models import Branch, DiningTable
from .validation import localized


@never_cache
@require_safe
def table_presence(request, token):
    table = get_object_or_404(DiningTable.objects.select_related('branch__restaurant__subscription'),
        public_token=token, active=True, archived=False, branch__active=True, branch__archived=False,
        branch__accepting_orders=True, branch__restaurant__active=True)
    subscription = getattr(table.branch.restaurant, 'subscription', None)
    if subscription is None or not subscription.valid():
        return JsonResponse({'ok':False, 'error':localized('استقبال الطلبات متوقف. تواصل مع الويتر.',
                                                          'Ordering is paused. Please ask your waiter.')}, status=400)
    return JsonResponse({'ok':True, **presence_policy(table)})


@never_cache
@require_http_methods(['GET', 'POST'])
def platform_geofence(request, restaurant_id, branch_id):
    from .views import error_text, platform_scope
    denied = platform_scope(request)
    if denied:
        return denied
    branch = get_object_or_404(Branch.objects.select_related('restaurant'), pk=branch_id, restaurant_id=restaurant_id)
    form = GeofenceForm(request.POST if request.method=='POST' else None, instance=branch, platform=True, prefix='geofence')
    if request.method=='POST' and form.is_valid():
        try:
            save_geofence(branch, request.user, form, platform=True)
        except ValidationError as error:
            form.add_error(None, error_text(error))
        else:
            messages.success(request, localized('تم حفظ موقع الفرع والمسافة المسموحة.', 'Branch location and allowed distance saved.'))
            return redirect(reverse('platform_geofence', args=[restaurant_id, branch_id]))
    return render(request, 'platform_geofence.html', {'branch':branch, 'restaurant':branch.restaurant,
        'geofence_form':form, 'geofence_platform':True, 'section':'all'})
