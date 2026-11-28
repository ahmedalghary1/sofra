"""Branch-specific proximity checks. Device coordinates are assertions, not attestation."""
import hashlib
import math
import uuid

from django.core import signing
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from .models import Branch, Restaurant
from .services import audit
from .validation import localized

CHALLENGE_SECONDS = 120
POSITION_SECONDS = 60
SALT = 'serveza.table-presence.v1'
FIELDS = ('geofence_enabled', 'latitude', 'longitude', 'geofence_radius_m')


class PresenceError(ValidationError):
    pass


def binding(table):
    return hashlib.sha256(f'{table.public_token}:{table.session_token}:{table.branch.geofence_version}'.encode()).hexdigest()


def presence_policy(table):
    branch = table.branch
    result = {'required': branch.geofence_enabled}
    if branch.geofence_enabled:
        result.update(challenge=signing.dumps({'binding': binding(table)}, salt=SALT),
                      radius_m=branch.geofence_radius_m, max_accuracy_m=min(50, branch.geofence_radius_m))
    return result


def number(value, minimum, maximum):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError
    value = float(value)
    if not math.isfinite(value) or not minimum <= value <= maximum:
        raise ValueError
    return value


def distance_m(latitude, longitude, target_latitude, target_longitude):
    lat1, lat2 = math.radians(latitude), math.radians(float(target_latitude))
    delta_lat = lat2 - lat1
    delta_lon = math.radians(float(target_longitude) - longitude)
    a = math.sin(delta_lat/2)**2 + math.cos(lat1)*math.cos(lat2)*math.sin(delta_lon/2)**2
    return 6371008.8 * 2 * math.atan2(math.sqrt(min(1, max(0, a))), math.sqrt(max(0, 1-a)))


def validate_presence(table, payload):
    branch = table.branch
    if not branch.geofence_enabled:
        return
    if branch.latitude is None or branch.longitude is None:
        raise PresenceError(localized('تعذر التحقق من موقع الفرع. تواصل مع الويتر.',
                                     'The branch location is unavailable. Please ask your waiter.'), code='presence_configuration')
    proof = payload.get('presence')
    if not isinstance(proof, dict):
        raise PresenceError(localized('اسمح بتحديد موقعك قبل إرسال طلب الطاولة.',
                                     'Allow location access before placing a table order.'), code='presence_required')
    try:
        challenge = proof.get('challenge')
        if not isinstance(challenge, str) or len(challenge) > 512:
            raise ValueError
        claims = signing.loads(challenge, salt=SALT, max_age=CHALLENGE_SECONDS)
        if not isinstance(claims, dict) or claims.get('binding') != binding(table):
            raise ValueError
    except (signing.BadSignature, ValueError, TypeError):
        raise PresenceError(localized('انتهى تحقق الموقع أو تغير إعداد الفرع. أعد المحاولة.',
                                     'The location check expired or branch settings changed. Please retry.'), code='presence_expired')
    try:
        latitude = number(proof.get('latitude'), -90, 90)
        longitude = number(proof.get('longitude'), -180, 180)
        accuracy = number(proof.get('accuracy'), 0, 1000000)
        timestamp = number(proof.get('timestamp'), 1, 1000000000000000)
    except (ValueError, TypeError, OverflowError):
        raise PresenceError(localized('قراءة الموقع غير صالحة. أعد تحديد موقعك.',
                                     'The location reading is invalid. Please try again.'), code='presence_invalid')
    age = timezone.now().timestamp()*1000 - timestamp
    if age < -15000 or age > POSITION_SECONDS*1000:
        raise PresenceError(localized('قراءة الموقع قديمة أو ساعة جهازك غير مضبوطة. أعد المحاولة.',
                                     'The location reading is stale or your device clock is incorrect. Please retry.'), code='presence_stale')
    if accuracy > min(50, branch.geofence_radius_m):
        raise PresenceError(localized('دقة الموقع غير كافية. فعّل الموقع الدقيق وأعد المحاولة أو اطلب مساعدة الويتر.',
                                     'Location accuracy is too low. Enable precise location and retry, or ask your waiter.'), code='presence_accuracy')
    if distance_m(latitude, longitude, branch.latitude, branch.longitude) > branch.geofence_radius_m:
        raise PresenceError(localized(f'طلب الطاولة متاح داخل نطاق {branch.geofence_radius_m} مترًا من الفرع فقط.',
                                     f'Table ordering is available only within {branch.geofence_radius_m} metres of this branch.'), code='presence_outside')


@transaction.atomic
def save_geofence(branch, actor, form, *, platform=False):
    # Match online-settings lock order without blocking audit/FK key-share locks.
    Restaurant.objects.select_for_update(of=('self',), no_key=True).get(pk=branch.restaurant_id)
    locked = Branch.objects.select_for_update().get(pk=branch.pk, restaurant_id=branch.restaurant_id)
    if form.cleaned_data['branch_id'] != locked.pk or form.cleaned_data['version'] != locked.geofence_version:
        raise ValidationError(localized('تغير الفرع أو إعداد موقعه. أعد فتح الصفحة قبل الحفظ.',
                                        'The branch or its location settings changed. Reopen this page before saving.'))
    if locked.archived:
        raise ValidationError(localized('استعد الفرع من الأرشيف قبل تعديل موقعه.', 'Restore the branch before changing its location.'))
    before = {field: str(getattr(locked, field)) for field in FIELDS}
    for field in FIELDS:
        setattr(locked, field, form.cleaned_data[field])
    locked.geofence_version = uuid.uuid4()
    locked.full_clean()
    locked.save(update_fields=[*FIELDS, 'geofence_version'])
    audit(locked.restaurant, actor, 'branch.geofence', str(locked.pk), changes={
        'before': before, 'after': {field: str(getattr(locked, field)) for field in FIELDS},
        'platform': platform, 'reason': form.cleaned_data.get('reason', '')})
    return locked
