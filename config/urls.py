from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.urls import path
from django.conf import settings
from django.conf.urls.static import static
from core import views

urlpatterns = [
    path('django-admin/', admin.site.urls),
    path('', views.home, name='home'),
    path('register/', views.register, name='register'),
    path('login/', views.login_view, name='login'),
    path('password-reset/', views.LimitedPasswordResetView.as_view(
        template_name='password_reset.html', email_template_name='password_reset_email.txt',
        subject_template_name='password_reset_subject.txt', success_url='/password-reset/done/'), name='password_reset'),
    path('password-reset/done/', auth_views.PasswordResetDoneView.as_view(template_name='password_reset_done.html'), name='password_reset_done'),
    path('reset/<uidb64>/<token>/', auth_views.PasswordResetConfirmView.as_view(template_name='password_reset_confirm.html', success_url='/reset/complete/'), name='password_reset_confirm'),
    path('reset/complete/', auth_views.PasswordResetCompleteView.as_view(template_name='password_reset_complete.html'), name='password_reset_complete'),
    path('logout/', views.logout_view, name='logout'),
    path('health/', views.health, name='health'),
    path('platform/', views.platform_dashboard, name='platform_dashboard'),
    path('platform/<int:restaurant_id>/update/', views.platform_update_restaurant, name='platform_update_restaurant'),
    path('app/<slug:slug>/', views.dashboard, name='dashboard'),
    path('app/<slug:slug>/menu/', views.manage_menu, name='manage_menu'),
    path('app/<slug:slug>/menu/import/', views.menu_import, name='menu_import'),
    path('app/<slug:slug>/tables/', views.manage_tables, name='manage_tables'),
    path('app/<slug:slug>/orders/', views.manage_orders, name='manage_orders'),
    path('app/<slug:slug>/orders/new/', views.staff_order, name='staff_order'),
    path('app/<slug:slug>/orders/<int:order_id>/edit/', views.order_edit, name='order_edit'),
    path('app/<slug:slug>/kitchen/', views.kitchen, name='kitchen'),
    path('app/<slug:slug>/kitchen/feed/', views.kitchen_feed, name='kitchen_feed'),
    path('app/<slug:slug>/calls/', views.manage_calls, name='manage_calls'),
    path('app/<slug:slug>/reports/', views.reports, name='reports'),
    path('app/<slug:slug>/staff/', views.staff, name='staff'),
    path('app/<slug:slug>/branches/', views.manage_branches, name='manage_branches'),
    path('app/<slug:slug>/branches/select/', views.select_branch, name='select_branch'),
    path('app/<slug:slug>/settings/', views.restaurant_settings, name='restaurant_settings'),
    path('app/<slug:slug>/orders/<int:order_id>/status/', views.order_status_change, name='order_status_change'),
    path('app/<slug:slug>/orders/<int:order_id>/payment/', views.order_payment_change, name='order_payment_change'),
    path('app/<slug:slug>/calls/<int:call_id>/status/', views.call_status_change, name='call_status_change'),
    path('app/<slug:slug>/tables/<int:table_id>/qr/', views.table_qr, name='table_qr'),
    path('t/<str:token>/', views.public_menu, name='public_menu'),
    path('t/<str:token>/order/', views.place_order, name='place_order'),
    path('t/<str:token>/call/', views.create_call, name='create_call'),
    path('track/<str:token>/', views.track_order, name='track_order'),
] + (static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT) if settings.DEBUG else [])
