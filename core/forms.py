import re

from django import forms
from django.contrib.auth.models import User
from django.contrib.auth.password_validation import validate_password

from .models import Plan, SubscriptionPayment


class SubscriptionPaymentForm(forms.Form):
    amount = forms.DecimalField(label='المبلغ بالجنيه المصري', max_digits=10, decimal_places=2,
                                min_value=0.01)
    period_start = forms.DateField(label='بداية الفترة', widget=forms.DateInput(attrs={'type': 'date'}))
    period_end = forms.DateField(label='نهاية الفترة', widget=forms.DateInput(attrs={'type': 'date'}))
    method = forms.ChoiceField(label='طريقة التحصيل', choices=SubscriptionPayment.METHODS)
    reference = forms.CharField(label='مرجع التحصيل', min_length=3, max_length=80)

    def clean_reference(self):
        return self.cleaned_data['reference'].strip().upper()

    def clean(self):
        data = super().clean()
        if data.get('period_start') and data.get('period_end') and data['period_end'] < data['period_start']:
            self.add_error('period_end', 'نهاية الفترة يجب ألا تسبق بدايتها.')
        return data


class PlatformRestaurantForm(forms.Form):
    name = forms.CharField(label='اسم المطعم', max_length=120)
    description = forms.CharField(label='وصف المطعم', max_length=240, required=False,
                                  widget=forms.Textarea(attrs={'rows': 2}))
    branch_name = forms.CharField(label='اسم الفرع الأول', max_length=120,
                                  initial='الفرع الرئيسي')
    branch_address = forms.CharField(label='عنوان الفرع', max_length=240, required=False)
    owner_email = forms.EmailField(label='بريد المالك', max_length=254,
                                   widget=forms.EmailInput(attrs={'dir': 'ltr', 'autocomplete': 'email'}))
    owner_name = forms.CharField(label='اسم المالك الجديد', max_length=150, required=False)
    owner_password = forms.CharField(label='كلمة مرور المالك الجديد', required=False,
                                     widget=forms.PasswordInput(attrs={'autocomplete': 'new-password'}))
    plan = forms.ModelChoiceField(label='الباقة', queryset=Plan.objects.all(), required=False,
                                  empty_label='تجربة افتراضية · 14 يومًا')

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['plan'].queryset = Plan.objects.order_by('name')
        self.fields['plan'].label_from_instance = (
            lambda plan: f'{plan.name} · حد الفروع {plan.max_branches}')

    def clean_owner_email(self):
        return self.cleaned_data['owner_email'].strip().lower()

    def clean(self):
        data = super().clean()
        email = data.get('owner_email')
        if not email:
            return data
        existing = User.objects.filter(username__iexact=email).first()
        if existing:
            if not existing.is_active or existing.is_superuser:
                self.add_error('owner_email', 'لا يمكن ربط هذا الحساب كمالك. استخدم حسابًا نشطًا عاديًا.')
            elif existing.last_name == 'SOFRA_LOCAL_DEMO':
                self.add_error('owner_email', 'استخدم بريدًا حقيقيًا أو حسابًا عاديًا بدل حساب العرض المحلي.')
            if data.get('owner_password'):
                self.add_error('owner_password', 'هذا الحساب موجود بالفعل؛ اترك كلمة المرور فارغة.')
        else:
            name, password = data.get('owner_name', ''), data.get('owner_password', '')
            if not name:
                self.add_error('owner_name', 'أدخل اسم المالك لإنشاء حسابه.')
            if not password:
                self.add_error('owner_password', 'أدخل كلمة مرور مؤقتة للمالك الجديد.')
            else:
                try:
                    validate_password(password, user=User(username=email, email=email, first_name=name))
                except forms.ValidationError as exc:
                    self.add_error('owner_password', exc)
        if data.get('plan') and data['plan'].max_branches < 1:
            self.add_error('plan', 'اختر باقة تسمح بفرع واحد على الأقل.')
        return data


class PlatformBranchForm(forms.Form):
    name = forms.CharField(label='اسم الفرع', max_length=120)
    address = forms.CharField(label='عنوان الفرع', max_length=240, required=False)


class RestaurantProfileForm(forms.Form):
    name = forms.CharField(label='اسم المطعم بالعربية', max_length=120)
    name_en = forms.CharField(label='اسم المطعم بالإنجليزية (يظهر للضيف عند إدخاله)', max_length=120, required=False)
    description = forms.CharField(label='وصف يظهر في المنيو', max_length=240, required=False,
                                  widget=forms.Textarea(attrs={'rows': 2}))
    branch_name = forms.CharField(label='اسم الفرع', max_length=120)
    address = forms.CharField(label='عنوان الفرع', max_length=240, required=False)
    phone = forms.CharField(label='رقم التواصل', max_length=24, required=False,
                            widget=forms.TextInput(attrs={'inputmode': 'tel', 'dir': 'ltr'}))
    opening_hours = forms.CharField(label='ساعات العمل', max_length=160, required=False,
                                    widget=forms.TextInput(attrs={'placeholder': 'يوميًا ١٠ صباحًا – ١١ مساءً'}))
    waiter_response_minutes = forms.IntegerField(label='حد استجابة استدعاء الويتر بالدقائق',
                                                  min_value=1, max_value=120, required=False)
    bill_response_minutes = forms.IntegerField(label='حد استجابة طلب الحساب بالدقائق',
                                                min_value=1, max_value=120, required=False)
    kitchen_delay_minutes = forms.IntegerField(label='حد تنبيه تأخر طلب المطبخ بالدقائق',
                                               min_value=1, max_value=240, required=False)
    show_contact_on_menu = forms.BooleanField(label='إظهار العنوان والتواصل وساعات العمل للضيف', required=False)
    accepting_orders = forms.BooleanField(label='استقبال طلبات جديدة من طاولات هذا الفرع', required=False)

    def clean_phone(self):
        value = self.cleaned_data['phone'].strip()
        if value and not re.fullmatch(r'\+?[0-9\s()\-]{7,24}', value):
            raise forms.ValidationError('أدخل رقم تواصل صالحًا بالأرقام.')
        return value
