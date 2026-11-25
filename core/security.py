"""Database-backed request limits shared by the public and admin entry points."""
import hashlib
import ipaddress
from datetime import timedelta

from django.contrib import messages
from django.conf import settings
from django.db import transaction
from django.http import HttpResponse
from django.shortcuts import render
from django.utils import timezone

from .models import LoginAttempt, RateLimitBucket


def client_address(request):
    remote = request.META.get('REMOTE_ADDR', '')
    try:
        if not remote and getattr(settings, 'TRUSTED_UNIX_PROXY', False):
            return str(ipaddress.ip_address(request.META.get('HTTP_X_REAL_IP', '')))
        address = ipaddress.ip_address(remote)
        trusted = any(address in ipaddress.ip_network(network) for network in getattr(settings, 'TRUSTED_PROXY_NETWORKS', ()))
        if trusted and request.META.get('HTTP_X_REAL_IP'):
            return str(ipaddress.ip_address(request.META['HTTP_X_REAL_IP']))
        return str(address)
    except ValueError:
        return 'unknown'


def consume_limits(request, action, *, limit, seconds, identity=''):
    now = timezone.now()
    parts = [('source', client_address(request))]
    if identity:
        parts.append(('identity', identity.strip().casefold()))
    # Source always precedes identity, so blocked sources cannot create arbitrary new identity buckets.
    keys = [hashlib.sha256(f'{action}:{kind}:{value}'.encode()).hexdigest() for kind, value in parts]
    with transaction.atomic():
        buckets = []
        for key in keys:
            bucket, _ = RateLimitBucket.objects.get_or_create(key=key, defaults={'started_at': now})
            bucket = RateLimitBucket.objects.select_for_update().get(pk=bucket.pk)
            if bucket.started_at <= now - timedelta(seconds=seconds):
                bucket.started_at, bucket.count = now, 0
            if bucket.count >= limit:
                return False
            buckets.append(bucket)
        for bucket in buckets:
            bucket.count += 1
            bucket.save(update_fields=['started_at', 'count'])
    return True


def limited_response(request, retry_after=900):
    text = ('Too many attempts. Please try again later.' if getattr(request, 'LANGUAGE_CODE', 'ar') == 'en'
            else 'محاولات كثيرة. انتظر قليلًا ثم حاول مجددًا.')
    response = HttpResponse(text, status=429, content_type='text/plain; charset=utf-8')
    response['Retry-After'] = str(retry_after)
    response['Cache-Control'] = 'no-store'
    return response


def authentication_limits_middleware(get_response):
    def middleware(request):
        admin_login = request.path_info == '/django-admin/login/'
        if request.method != 'POST' or request.path_info not in ('/login/', '/django-admin/login/'):
            return get_response(request)
        if not consume_limits(request, 'authentication-source', limit=60, seconds=900):
            return limited_response(request)
        from .views import login_username

        username = login_username(request.POST.get('username') or request.POST.get('email', ''))
        key = hashlib.sha256(f"{client_address(request)}:{username.lower()}".encode()).hexdigest()
        if admin_login and LoginAttempt.objects.filter(key=key, created_at__gte=timezone.now()-timedelta(minutes=15)).count() >= 8:
            return limited_response(request)
        response = get_response(request)
        if admin_login:
            if request.user.is_authenticated and request.user.is_staff and response.status_code in (302, 303):
                LoginAttempt.objects.filter(key=key).delete()
            else:
                LoginAttempt.objects.create(key=key)
        return response
    return middleware
