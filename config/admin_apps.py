from django.contrib.admin.apps import AdminConfig


class PlatformAdminConfig(AdminConfig):
    default_site = 'config.admin_site.PlatformAdminSite'
