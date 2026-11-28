import io
import uuid

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db import transaction
from django.http import FileResponse, HttpResponse
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_safe

from .forms import TableBatchForm, TableQRStyleForm
from .models import DiningTable, Restaurant, TableBatch
from .services import audit
from .table_codes import MAX_BATCH, qr_pdf, qr_png, qr_svg, qr_zip, stable_table_url, table_number
from .validation import localized


def table_batch_context(restaurant, branch, *, batch_form=None):
    current = list(branch.tables.only('name', 'number', 'archived'))
    count = sum(not table.archived for table in current)
    limit = restaurant.subscription.limit('max_tables', branch)
    used = [number for table in current if (number := table_number(table)) is not None]
    return {
        'limit': limit, 'remaining_tables': max(limit-count, 0), 'batch_table_count': count,
        'batch_form': batch_form if batch_form is not None else TableBatchForm(initial={
            'branch_id': branch.pk, 'submission_key': uuid.uuid4(),
            'count': min(50, max(limit-count, 1)), 'start_number': max(used, default=0)+1}),
    }


def table_context(request, restaurant, branch, *, batch_form=None, style_form=None):
    tables=list(branch.tables.filter(archived=False).order_by('pk'))
    batch=None
    if request.GET.get('batch'):
        from .views import valid_pk
        batch=get_object_or_404(TableBatch,pk=valid_pk(request.GET['batch']),branch=branch)
    return {**table_batch_context(restaurant, branch, batch_form=batch_form),
        'tables':tables,'selected_batch':batch,
        'qr_style_form':style_form or TableQRStyleForm(instance=restaurant,initial={'branch_id':branch.pk}),
        'qr_preview_table':tables[0] if tables else None,'qr_origin':restaurant.qr_public_origin}


def save_style(restaurant, actor, form):
    with transaction.atomic():
        locked=Restaurant.objects.select_for_update().get(pk=restaurant.pk)
        before={field:getattr(locked,field) for field in ('qr_color','qr_number_color')}
        for field in before:setattr(locked,field,form.cleaned_data[field].lower())
        locked.save(update_fields=list(before))
        audit(restaurant,actor,'table.qr.style',str(restaurant.pk),changes={
            'before':before,'after':{field:getattr(locked,field) for field in before}})


def file_response(content, kind, filename, *, inline=False):
    response=(FileResponse(io.BytesIO(content),content_type=kind,as_attachment=not inline,filename=filename)
              if kind in ('application/pdf','application/zip') else HttpResponse(content,content_type=kind))
    response['Content-Disposition']=('inline' if inline else 'attachment')+f'; filename="{filename}"'
    response['Cache-Control']='no-store, private'
    response['X-Content-Type-Options']='nosniff'
    return response


@never_cache
@require_safe
def table_qr_file(request, slug, table_id):
    from .views import scope,branch_for,error_text
    restaurant,membership,denied=scope(request,slug,['owner','manager'])
    if denied:return denied
    branch=branch_for(request,restaurant,membership)
    table=get_object_or_404(DiningTable,pk=table_id,branch=branch,archived=False)
    mode=request.GET.get('format','png')
    if mode not in ('png','svg','pdf'):return HttpResponse(status=400)
    try:
        if mode=='png':content,kind=qr_png(table,restaurant),'image/png'
        elif mode=='svg':content,kind=qr_svg(table,restaurant),'image/svg+xml'
        else:content,kind=qr_pdf([table],restaurant,branch),'application/pdf'
    except ValidationError as error:return HttpResponse(error_text(error),status=400)
    return file_response(content,kind,f'table-{table.pk}-qr.{mode}',inline=request.GET.get('preview')=='1')


@never_cache
@require_safe
def table_qr_pack(request,slug,branch_id):
    from .views import scope,branch_for,valid_pk,error_text
    restaurant,membership,denied=scope(request,slug,['owner','manager'])
    if denied:return denied
    branch=branch_for(request,restaurant,membership)
    if branch.pk!=branch_id:
        return HttpResponse(localized('تغيّر الفرع المختار. أعد فتح صفحة طاولاته قبل التنزيل.',
                                      'The selected branch changed. Reopen its tables page before downloading.'),status=409)
    mode=request.GET.get('format','pdf')
    if mode not in ('pdf','zip'):return HttpResponse(status=400)
    tables=branch.tables.filter(archived=False)
    if request.GET.get('batch'):
        batch=get_object_or_404(TableBatch,pk=valid_pk(request.GET['batch']),branch=branch)
        tables=tables.filter(batch=batch)
    tables=list(tables.order_by('number','pk')[:MAX_BATCH+1])
    if not tables or len(tables)>MAX_BATCH:
        return HttpResponse(localized('اختر دفعة بها من 1 إلى 200 طاولة للطباعة.',
                                      'Choose a batch containing 1–200 tables to print.'),status=400)
    tables.sort(key=lambda table:(table_number(table) if table_number(table) is not None else 100000,table.name,table.pk))
    try:content=qr_pdf(tables,restaurant,branch) if mode=='pdf' else qr_zip(tables,restaurant)
    except ValidationError as error:return HttpResponse(error_text(error),status=400)
    return file_response(content,'application/pdf' if mode=='pdf' else 'application/zip',f'branch-{branch.pk}-table-codes.{mode}')
