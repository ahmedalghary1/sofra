from django import forms
from django.utils.translation import get_language

from .models import Branch


class GeofenceForm(forms.ModelForm):
    branch_id = forms.IntegerField(widget=forms.HiddenInput)
    version = forms.UUIDField(widget=forms.HiddenInput)
    reason = forms.CharField(required=False, max_length=240, min_length=3)

    class Meta:
        model = Branch
        fields = ('geofence_enabled', 'latitude', 'longitude', 'geofence_radius_m')
        widgets = {'latitude': forms.NumberInput(attrs={'step':'0.000001','inputmode':'decimal','min':-90,'max':90}),
                   'longitude': forms.NumberInput(attrs={'step':'0.000001','inputmode':'decimal','min':-180,'max':180}),
                   'geofence_radius_m': forms.NumberInput(attrs={'min':10,'max':5000,'step':1})}

    def __init__(self, *args, platform=False, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['reason'].required = platform
        self.initial.update(branch_id=self.instance.pk, version=self.instance.geofence_version)
        labels = {'geofence_enabled': ('تفعيل التحقق من موقع طلبات الطاولات', 'Require location for table orders'),
            'latitude': ('خط العرض', 'Latitude'), 'longitude': ('خط الطول', 'Longitude'),
            'geofence_radius_m': ('المسافة المسموحة بالمتر (نصف القطر)', 'Allowed distance in metres (radius)'),
            'reason': ('سبب التعديل', 'Reason for change')}
        for key, labels_pair in labels.items():
            self.fields[key].label = labels_pair[get_language() == 'en']

    def clean(self):
        values = super().clean()
        lat, lon = values.get('latitude'), values.get('longitude')
        if (lat is None) != (lon is None) or (values.get('geofence_enabled') and lat is None):
            raise forms.ValidationError('Choose a valid branch location before enabling the check.' if get_language() == 'en'
                                        else 'حدد موقعًا صحيحًا للفرع قبل تفعيل التحقق.')
        return values
