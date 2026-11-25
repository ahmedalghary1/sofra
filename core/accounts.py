"""Keep legacy usernames, but resolve new accounts by one unambiguous email."""
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.db.models import Q
from django.utils.translation import gettext
from .validation import localized


def account_for_email(email):
    email = email.strip().lower()
    validate_email(email)
    matches = list(User.objects.filter(Q(username__iexact=email) | Q(email__iexact=email)).order_by('pk')[:2])
    if len(matches) > 1 or (matches and matches[0].email and matches[0].email.casefold() != email.casefold()):
        raise ValidationError(localized('يتعارض هذا البريد مع حسابات دخول قائمة. تواصل مع مسؤول المنصة.',
                                       'This email conflicts with existing login accounts. Contact the platform administrator.'))
    return matches[0] if matches else None


def new_login_email(email):
    email = email.strip().lower()
    validate_email(email)
    if len(email) > User._meta.get_field('username').max_length:
        raise ValidationError(localized('الحد الأقصى 150 حرفًا لبريد يُستخدم كاسم للدخول.',
                                       'Use at most 150 characters for an email used as a login name.'))
    return email
