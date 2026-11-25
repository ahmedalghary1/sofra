"""Operator-only account administration, sales enquiries and activity records."""
from django import forms
from django.contrib import messages
from django.contrib.admin.models import ADDITION, CHANGE, LogEntry
from django.contrib.auth import update_session_auth_hash
from django.contrib.auth.models import User
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db import IntegrityError, transaction
from django.db.models import Count, Q
from django.http import HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from .limits import check_creation_limit
from .models import AuditLog, Branch, DemoRequest, Membership, OwnerEmailVerification, StaffInvitation, SubscriptionPayment
from .services import audit
from .views import platform_scope


def record_change(actor, obj, message, flag=CHANGE):
    LogEntry.objects.log_actions(user_id=actor.pk, queryset=[obj], action_flag=flag, change_message=message)


@require_GET
def records(request, section):
    denied = platform_scope(request)
    if denied:
        return denied
    query = request.GET.get('q', '').strip()[:100]
    state = request.GET.get('filter', '')
    if section == 'accounts':
        rows = User.objects.annotate(seats=Count('memberships')).order_by('username', 'pk')
        rows = rows.filter(Q(username__icontains=query) | Q(email__icontains=query) | Q(first_name__icontains=query))
        if state in ('active', 'paused'):
            rows = rows.filter(is_active=state == 'active')
        elif state == 'operator':
            rows = rows.filter(is_superuser=True)
    elif section == 'requests':
        rows = DemoRequest.objects.filter(Q(venue__icontains=query) | Q(name__icontains=query) | Q(email__icontains=query)).order_by('-created_at', '-pk')
        if state in dict(DemoRequest.STATES):
            rows = rows.filter(status=state)
    elif section == 'receipts':
        rows = SubscriptionPayment.objects.select_related('restaurant', 'recorded_by').filter(
            Q(restaurant__name__icontains=query) | Q(reference__icontains=query))
    elif state == 'global':
        rows = LogEntry.objects.select_related('user').filter(Q(object_repr__icontains=query) |
            Q(user__username__icontains=query) | Q(change_message__icontains=query)).order_by('-action_time', '-pk')
    else:
        rows = AuditLog.objects.select_related('actor', 'restaurant').filter(Q(restaurant__name__icontains=query) |
            Q(actor__username__icontains=query) | Q(detail__icontains=query) | Q(action__icontains=query)).order_by('-created_at', '-pk')
    return render(request, 'platform_records.html', {'section': section, 'rows': Paginator(rows, 25).get_page(request.GET.get('page')),
        'query': query, 'filter': state})


@require_POST
def request_status(request, pk):
    denied = platform_scope(request)
    if denied:
        return denied
    status = request.POST.get('status')
    if status not in dict(DemoRequest.STATES):
        return HttpResponseBadRequest()
    with transaction.atomic():
        lead = get_object_or_404(DemoRequest.objects.select_for_update(), pk=pk)
        if lead.status != status:
            record_change(request.user, lead, f'{lead.status} → {status}')
            lead.status = status
            lead.save(update_fields=['status'])
    return redirect('platform_requests')


class AccountForm(forms.Form):
    name = forms.CharField(max_length=150, required=False)
    email = forms.EmailField(max_length=254)
    password = forms.CharField(strip=False, required=False, widget=forms.PasswordInput(attrs={'autocomplete': 'new-password'}))
    access = forms.ChoiceField(choices=[('member', 'عضو مطعم'), ('operator', 'مسؤول المنصة')])
    active = forms.BooleanField(required=False, initial=True)
    branch = forms.ModelChoiceField(queryset=Branch.objects.none(), required=False)
    role = forms.ChoiceField(choices=StaffInvitation.ROLES, initial='waiter', required=False)
    reason = forms.CharField(max_length=120)

    def __init__(self, *args, user, actor, english, **kwargs):
        self.user, self.actor, self.english = user, actor, english
        super().__init__(*args, **kwargs)
        labels = [('الاسم', 'Name'), ('بريد تسجيل الدخول', 'Login email'), ('كلمة مرور جديدة', 'New password'),
                  ('مستوى الوصول', 'Access level'), ('الحساب مفعّل', 'Account enabled'),
                  ('إضافة عضوية في فرع', 'Add membership at branch'), ('دور العضوية الجديدة', 'New membership role'),
                  ('سبب التغيير', 'Reason for change')]
        for field, label in zip(self.fields.values(), labels):
            field.label = label[english]
        self.fields['password'].required = not user.pk
        # Legacy Django accounts can sign in by username and may have no email.
        self.fields['email'].required = not user.pk or bool(user.email)
        if not user.pk or user.username.casefold() == user.email.casefold():
            self.fields['email'].max_length = 150
            self.fields['email'].widget.attrs['maxlength'] = 150
        self.fields['branch'].queryset = Branch.objects.filter(active=True, restaurant__active=True).select_related('restaurant').order_by('restaurant__name', 'name')
        if english:
            self.fields['access'].choices = [('member', 'Restaurant member'), ('operator', 'Platform administrator')]
            self.fields['role'].choices = [(value, value.title()) for value, label in StaffInvitation.ROLES]
        if user.pk:
            self.initial.update(name=user.first_name, email=user.email, active=user.is_active,
                                access='operator' if user.is_superuser else 'member')
        if user.pk == actor.pk:
            self.fields['active'].disabled = self.fields['access'].disabled = True

    def clean(self):
        data = super().clean()
        email = data.get('email', '')
        if self.user.pk and email.casefold() == self.user.email.casefold():
            email = self.user.email
        else:
            email = email.lower()
        data['email'] = email
        existing = User.objects.exclude(pk=self.user.pk)
        if len(email) > self.fields['email'].max_length:
            self.add_error('email', 'Use at most 150 characters.' if self.english else 'الحد الأقصى 150 حرفًا لبريد يُستخدم كاسم للدخول.')
        elif email and email != self.user.email and existing.filter(Q(email__iexact=email) | Q(username__iexact=email)).exists():
            self.add_error('email', 'Email already used.' if self.english else 'هذا البريد مستخدم بالفعل.')
        if self.user.pk and OwnerEmailVerification.objects.filter(user=self.user, verified_at__isnull=True).exists():
            if data.get('active') or email != self.user.email:
                self.add_error(None, 'Email verification is pending.' if self.english else 'هذا الحساب بانتظار تأكيد البريد.')
        if data.get('password'):
            try:
                validate_password(data['password'], User(username=email, email=email, first_name=data.get('name', '')))
            except ValidationError as exc:
                self.add_error('password', exc)
        branch = data.get('branch')
        if branch and not data.get('role'):
            self.add_error('role', 'Select a role for the new membership.' if self.english else 'اختر دور العضوية الجديدة.')
        if data.get('access') == 'member' and not self.user.pk and not branch:
            self.add_error('branch', 'Select a branch.' if self.english else 'اختر فرعًا للعضوية.')
        if branch and (data.get('access') == 'operator' or Membership.objects.filter(user_id=self.user.pk, restaurant=branch.restaurant).exists()):
            self.add_error('branch', 'Choose a new restaurant membership.' if self.english else 'اختر عضوية جديدة لحساب مطعم.')
        if self.user.last_name == 'SOFRA_LOCAL_DEMO' and (branch or data.get('access') == 'operator'):
            self.add_error(None, 'Use a dedicated account for this access.' if self.english else 'استخدم حسابًا مخصصًا لهذه الصلاحيات.')
        return data


@require_http_methods(['GET', 'POST'])
def account(request, pk=None):
    denied = platform_scope(request)
    if denied:
        return denied
    english = request.LANGUAGE_CODE == 'en'
    with transaction.atomic():
        if request.method == 'POST':
            # Serialize operator access changes and recheck the actor after acquiring the locks.
            operators = list(User.objects.select_for_update().filter(is_active=True, is_superuser=True)
                             .order_by('pk').values_list('pk', flat=True))
            if request.user.pk not in operators:
                return render(request, '403.html', status=403)
        user = get_object_or_404(User.objects.select_for_update(), pk=pk) if pk else User()
        form = AccountForm(request.POST if request.method == 'POST' else None, user=user, actor=request.user, english=english)
        if request.method == 'POST' and form.is_valid():
            data = form.cleaned_data
            try:
                with transaction.atomic():
                    branch = data['branch']
                    if branch:
                        if not hasattr(branch.restaurant, 'subscription'):
                            raise ValidationError('Subscription required.' if english else 'يجب ضبط اشتراك المطعم.')
                        check_creation_limit(branch.restaurant, 'staff')
                    if not pk or (data['email'] != user.email and user.username.casefold() == user.email.casefold()):
                        user.username = data['email']
                    user.email, user.first_name, user.is_active = data['email'], data['name'], data['active']
                    if user.is_superuser and data['access'] == 'member':
                        user.is_staff = False
                    user.is_superuser = data['access'] == 'operator'
                    user.is_staff = user.is_superuser
                    if data['password']:
                        user.set_password(data['password'])
                    user.save()
                    if branch:
                        membership = Membership.objects.create(user=user, restaurant=branch.restaurant, role=data['role'])
                        membership.branches.add(branch)
                        audit(branch.restaurant, request.user, 'platform.member.create',
                              f"member={membership.pk}; role={membership.role}; branch={branch.pk}; {data['reason']}")
                    record_change(request.user, user, f"access={data['access']}; active={user.is_active}; {data['reason']}", CHANGE if pk else ADDITION)
            except ValidationError as exc:
                form.add_error(None, exc)
            except IntegrityError:
                form.add_error(None, 'Conflicting account details.' if english else 'تعارض في بيانات الحساب أو العضوية.')
            else:
                if user.pk == request.user.pk and data['password']:
                    update_session_auth_hash(request, user)
                messages.success(request, 'Account saved.' if english else 'تم حفظ الحساب.')
                return redirect('platform_accounts')
    return render(request, 'platform_account.html', {'form': form, 'account': user, 'section': 'accounts'})
