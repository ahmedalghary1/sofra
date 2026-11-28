import re

from django import forms
from django.contrib.auth.models import User
from django.contrib.auth.password_validation import validate_password
from django.utils import timezone
from django.utils.translation import get_language, gettext_lazy as _

from .models import Plan, SubscriptionPayment
from .accounts import account_for_email, new_login_email
from .branding import MENU_COLOR_DEFAULTS, validate_menu_color, validate_menu_contrast
from .images import prepare_menu_image
from .validation import localized
from .plan_features import FEATURES, enabled_features


class TableBatchForm(forms.Form):
    branch_id = forms.IntegerField(widget=forms.HiddenInput(attrs={'id':'table_batch_branch'}))
    submission_key = forms.UUIDField(widget=forms.HiddenInput)
    count = forms.IntegerField(min_value=1, max_value=200, label=_('عدد الطاولات'))
    start_number = forms.IntegerField(min_value=1, max_value=99999, label=_('ابدأ من رقم'))

    def clean(self):
        values=super().clean()
        if values.get('count') and values.get('start_number') and values['count']+values['start_number']-1>99999:
            raise forms.ValidationError('Table numbering must end at 99999 or below.' if get_language()=='en'
                                        else 'يجب ألا يتجاوز آخر رقم طاولة 99999.')
        return values


class TableQRStyleForm(forms.ModelForm):
    branch_id = forms.IntegerField(widget=forms.HiddenInput(attrs={'id':'table_qr_style_branch'}))

    class Meta:
        from .models import Restaurant
        model = Restaurant
        fields = ('qr_color','qr_number_color')
        widgets = {'qr_color':forms.TextInput(attrs={'type':'color'}),
                   'qr_number_color':forms.TextInput(attrs={'type':'color'})}

    def clean(self):
        from .table_codes import validate_qr_color
        values=super().clean()
        for field in ('qr_color','qr_number_color'):
            if field in values:
                try: validate_qr_color(values[field])
                except forms.ValidationError as error: self.add_error(field,error)
        return values


class ItemRecommendationForm(forms.Form):
    target_item = forms.ModelChoiceField(queryset=None)
    priority = forms.IntegerField(min_value=0, max_value=999, initial=0)
    active = forms.BooleanField(required=False)

    def __init__(self, *args, branch, source, current_target=None, **kwargs):
        from .models import MenuItem
        from django.db.models import Q
        super().__init__(*args, **kwargs)
        self.fields['target_item'].queryset = MenuItem.objects.filter(category__branch=branch).filter(
            Q(archived=False) | Q(pk=current_target)).exclude(pk=source.pk).order_by('name', 'pk')


class PlatformPlanForm(forms.ModelForm):
    reason = forms.CharField(max_length=120)
    enabled_features = forms.MultipleChoiceField(required=False, widget=forms.CheckboxSelectMultiple)

    class Meta:
        model = Plan
        fields = ('name', 'price_monthly', 'max_branches', 'max_tables', 'max_items', 'max_staff', 'max_stations', 'enabled_features')

    def __init__(self, *args, english=False, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['enabled_features'].choices = [(key, label[english]) for key, label in FEATURES.items()]
        self.fields['enabled_features'].label = ('الخدمات الإضافية المشمولة', 'Included additional services')[english]
        self.initial['enabled_features'] = enabled_features(self.instance) if self.instance.pk else list(FEATURES)
        labels = {
            'name': ('اسم الباقة', 'Plan name'), 'price_monthly': ('السعر الشهري بالجنيه', 'Monthly price in EGP'),
            'max_branches': ('الفروع', 'Branches'), 'max_tables': ('الطاولات لكل فرع', 'Tables per branch'),
            'max_items': ('الأصناف لكل فرع', 'Items per branch'),
            'max_staff': ('إجمالي حسابات الفريق، شاملة المالك', 'Total team accounts, including the owner'),
            'max_stations': ('محطات المطبخ', 'Kitchen stations'), 'reason': ('سبب التغيير', 'Reason for change'),
        }
        for key, label in labels.items():
            self.fields[key].label = label[english]
        self.fields['name'].disabled = bool(self.instance.pk)

    def clean_price_monthly(self):
        price = self.cleaned_data['price_monthly']
        if price < 0:
            raise forms.ValidationError(_('يجب ألا يقل السعر عن صفر.') if get_language() != 'en' else 'Enter a non-negative price.')
        return price


class OwnerRegistrationForm(forms.Form):
    name = forms.CharField(label=_('اسمك'), max_length=150,
                           widget=forms.TextInput(attrs={'autocomplete': 'name', 'placeholder': _('الاسم الكامل')}))
    restaurant = forms.CharField(label=_('اسم المطعم'), max_length=120,
                                 widget=forms.TextInput(attrs={'autocomplete': 'organization', 'placeholder': _('مثال: بيت المذاق')}))
    email = forms.EmailField(label=_('البريد الإلكتروني'), max_length=150,
                             widget=forms.EmailInput(attrs={'autocomplete': 'email', 'placeholder': 'name@example.com'}))
    password = forms.CharField(label=_('كلمة المرور'), strip=False,
                               widget=forms.PasswordInput(attrs={'autocomplete': 'new-password', 'minlength': 8,
                                                                 'placeholder': _('8 أحرف على الأقل')}))

    def clean_email(self):
        email = new_login_email(self.cleaned_data['email'])
        if account_for_email(email):
            raise forms.ValidationError(_('هذا البريد مستخدم بالفعل. إذا لم يصلك رابط التفعيل، اطلب إعادة إرساله.'))
        return email

    def clean_password(self):
        email = self.cleaned_data.get('email', '')
        password = self.cleaned_data['password']
        validate_password(password, user=User(username=email, email=email, first_name=self.cleaned_data.get('name', '')))
        return password


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
            self.add_error('period_end', _('نهاية الفترة يجب ألا تسبق بدايتها.'))
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
                                  empty_label='—')
    ends_at = forms.DateField(required=False, widget=forms.DateInput(attrs={'type':'date'}))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['plan'].queryset = Plan.objects.order_by('price_monthly', 'name')
        self.fields['plan'].empty_label = 'Awaiting activation' if get_language() == 'en' else 'بانتظار التفعيل'

    def clean_owner_email(self):
        email = self.cleaned_data['owner_email'].strip().lower()
        existing = account_for_email(email)
        return email if existing else new_login_email(email)

    def clean(self):
        data = super().clean()
        email = data.get('owner_email')
        if data.get('plan') and (not data.get('ends_at') or data['ends_at'] < timezone.localdate()):
            self.add_error('ends_at', 'Choose today or a future expiry date.' if get_language() == 'en'
                           else 'اختر تاريخ انتهاء اليوم أو بعده لتفعيل الباقة.')
        if not email:
            return data
        existing = account_for_email(email)
        if existing:
            if not existing.is_active or existing.is_superuser:
                self.add_error('owner_email', _('لا يمكن ربط هذا الحساب كمالك. استخدم حسابًا نشطًا عاديًا.'))
            elif existing.last_name == 'SOFRA_LOCAL_DEMO':
                self.add_error('owner_email', _('استخدم بريدًا حقيقيًا أو حسابًا عاديًا بدل حساب العرض المحلي.'))
            if data.get('owner_password'):
                self.add_error('owner_password', _('هذا الحساب موجود بالفعل؛ اترك كلمة المرور فارغة.'))
        else:
            name, password = data.get('owner_name', ''), data.get('owner_password', '')
            if not name:
                self.add_error('owner_name', _('أدخل اسم المالك لإنشاء حسابه.'))
            if not password:
                self.add_error('owner_password', _('أدخل كلمة مرور مؤقتة للمالك الجديد.'))
            else:
                try:
                    validate_password(password, user=User(username=email, email=email, first_name=name))
                except forms.ValidationError as exc:
                    self.add_error('owner_password', exc)
        if data.get('plan') and data['plan'].max_branches < 1:
            self.add_error('plan', _('اختر باقة تسمح بفرع واحد على الأقل.'))
        return data


class PlatformBranchForm(forms.Form):
    name = forms.CharField(label='اسم الفرع', max_length=120)
    address = forms.CharField(label='عنوان الفرع', max_length=240, required=False)


class MenuBrandingForm(forms.Form):
    logo = forms.FileField(required=False, widget=forms.FileInput(attrs={
        'accept': 'image/png,image/jpeg,image/webp', 'data-menu-logo': ''}))
    remove_logo = forms.BooleanField(required=False, widget=forms.CheckboxInput(attrs={'data-menu-remove-logo': ''}))
    menu_primary_color = forms.CharField(validators=[validate_menu_color])
    menu_background_color = forms.CharField(validators=[validate_menu_color])
    menu_text_color = forms.CharField(validators=[validate_menu_color])

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        labels = {
            'logo': ('لوجو المطعم', 'Restaurant logo'),
            'remove_logo': ('إزالة اللوجو الحالي', 'Remove current logo'),
            'menu_primary_color': ('لون الأزرار والتصنيف المختار', 'Buttons and selected category color'),
            'menu_background_color': ('لون خلفية المنيو', 'Menu background color'),
            'menu_text_color': ('لون النص', 'Text color'),
        }
        for key, label in labels.items():
            self.fields[key].label = localized(*label)
        for key in MENU_COLOR_DEFAULTS:
            self.fields[key].widget = forms.TextInput(attrs={
                'type': 'color', 'dir': 'ltr', 'data-menu-color': key,
                'aria-describedby': 'menuContrastHint'})

    def clean_logo(self):
        upload = self.cleaned_data.get('logo')
        try:
            return prepare_menu_image(upload, max_dimension=512)
        except forms.ValidationError:
            raise forms.ValidationError(localized(
                'ارفع صورة PNG أو JPG أو WebP صحيحة، بحد أقصى 3 ميجابايت و5000 × 5000 بكسل.',
                'Upload a valid PNG, JPG or WebP image, up to 3 MB and 5000 × 5000 pixels.'))

    def clean(self):
        data = super().clean()
        if data.get('logo') and data.get('remove_logo'):
            self.add_error('logo', localized('اختر رفع لوجو أو إزالته في المرة الواحدة.', 'Choose either uploading or removing a logo at a time.'))
        for key in MENU_COLOR_DEFAULTS:
            if key in data:
                data[key] = data[key].lower()
        if data.get('menu_text_color') and data.get('menu_background_color'):
            try:
                validate_menu_contrast(data['menu_text_color'], data['menu_background_color'])
            except forms.ValidationError as exc:
                self.add_error('menu_text_color', exc)
        return data


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
                                    widget=forms.TextInput(attrs={'placeholder': _('يوميًا 10 صباحًا – 11 مساءً')}))
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
            raise forms.ValidationError(_('أدخل رقم تواصل صالحًا بالأرقام.'))
        return value
