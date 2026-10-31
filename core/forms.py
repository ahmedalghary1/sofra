import re

from django import forms


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
    show_contact_on_menu = forms.BooleanField(label='إظهار العنوان والتواصل وساعات العمل للضيف', required=False)
    accepting_orders = forms.BooleanField(label='استقبال طلبات جديدة من طاولات هذا الفرع', required=False)

    def clean_phone(self):
        value = self.cleaned_data['phone'].strip()
        if value and not re.fullmatch(r'\+?[0-9\s()\-]{7,24}', value):
            raise forms.ValidationError('أدخل رقم تواصل صالحًا بالأرقام.')
        return value
