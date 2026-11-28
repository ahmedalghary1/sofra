from django.contrib.admin import AdminSite
from django.contrib.admin.forms import AdminAuthenticationForm
from django.core.exceptions import ValidationError
from django.utils.translation import get_language


class PlatformAdminAuthenticationForm(AdminAuthenticationForm):
    def confirm_login_allowed(self, user):
        super().confirm_login_allowed(user)
        if not user.is_superuser:
            raise ValidationError('This account is not authorized for platform administration.' if get_language() == 'en'
                                  else 'هذا الحساب غير مخول بالدخول إلى إدارة المنصة.', code='invalid_login')


class PlatformAdminSite(AdminSite):
    """Django model permissions never grant global access to a venue employee."""
    def has_permission(self, request):
        return super().has_permission(request) and request.user.is_superuser
    login_form = PlatformAdminAuthenticationForm

