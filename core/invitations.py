import hashlib

from django.contrib.auth.models import User
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from .limits import check_creation_limit
from .models import Membership, StaffInvitation
from .services import audit


def invitation_for_token(raw_token):
    if not raw_token or len(raw_token) > 128:
        return None
    digest = hashlib.sha256(raw_token.encode()).hexdigest()
    return StaffInvitation.objects.select_related('restaurant', 'branch').filter(token_hash=digest).first()


@transaction.atomic
def accept_staff_invitation(raw_token, name='', password='', authenticated_user=None):
    invitation = invitation_for_token(raw_token)
    if not invitation:
        raise ValidationError('رابط الدعوة غير صالح.')
    invitation = StaffInvitation.objects.select_for_update().select_related('restaurant', 'branch').get(pk=invitation.pk)
    if not invitation.pending():
        raise ValidationError('انتهت صلاحية الدعوة أو استُخدمت سابقًا.')
    if not invitation.restaurant.active or not invitation.branch.active:
        raise ValidationError('المطعم أو الفرع متوقف حاليًا.')
    inviter_membership = Membership.objects.filter(user=invitation.invited_by, restaurant=invitation.restaurant,
                                                  active=True).first()
    if not inviter_membership or inviter_membership.role not in ('owner', 'manager') or (
            invitation.role == 'manager' and inviter_membership.role != 'owner'):
        raise ValidationError('لم تعد صلاحية الداعي كافية لإكمال هذه الدعوة.')
    existing_user = User.objects.filter(username__iexact=invitation.email).first()
    if existing_user:
        if not authenticated_user or authenticated_user.pk != existing_user.pk or not existing_user.is_active:
            raise ValidationError('سجّل الدخول بالبريد المدعو لقبول الدعوة.')
        user = existing_user
    else:
        if authenticated_user and authenticated_user.is_authenticated:
            raise ValidationError('سجّل الخروج لإنشاء حساب البريد المدعو.')
        name = name.strip()
        if not name:
            raise ValidationError('أدخل اسمك.')
        user = User(username=invitation.email, email=invitation.email, first_name=name[:150])
        validate_password(password, user=user)
    if existing_user and Membership.objects.filter(user=user, restaurant=invitation.restaurant).exists():
        raise ValidationError('هذا الحساب عضو في المطعم بالفعل.')
    check_creation_limit(invitation.restaurant, 'staff')
    if not existing_user:
        user.set_password(password)
        user.save()
    membership = Membership.objects.create(user=user, restaurant=invitation.restaurant, role=invitation.role)
    membership.branches.add(invitation.branch)
    invitation.accepted_by = user
    invitation.accepted_at = timezone.now()
    invitation.save(update_fields=['accepted_by', 'accepted_at'])
    audit(invitation.restaurant, user, 'staff.invite.accept', f'{user.pk}: {invitation.role}; branch={invitation.branch_id}')
    return user, invitation.restaurant
