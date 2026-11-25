"""Shared validation for values crossing an HTTP or import boundary."""
import json
from decimal import Decimal, InvalidOperation

from django.core.exceptions import ValidationError
from django.utils.translation import get_language, gettext

UNIT_MAX = Decimal('9999999.99')
TOTAL_MAX = Decimal('99999999.99')


def localized(arabic, english):
    return english if get_language() == 'en' else arabic


def identifier(value):
    if type(value) is int:
        number = value
    elif isinstance(value, str) and 1 <= len(value) <= 19 and value.isascii() and value.isdecimal():
        number = int(value)
    else:
        raise ValidationError('Invalid identifier.' if get_language() == 'en' else 'معرّف غير صالح.')
    if not 1 <= number <= 9223372036854775807:
        raise ValidationError(localized('معرّف غير صالح.', 'Invalid identifier.'))
    return number


def money(value, maximum=UNIT_MAX):
    try:
        if isinstance(value, bool) or len(str(value)) > 50:
            raise InvalidOperation
        amount = Decimal(str(value).strip())
        if (not amount.is_finite() or amount < 0 or amount > maximum
                or amount.as_tuple().exponent < -2):
            raise InvalidOperation
        return amount.quantize(Decimal('0.01'))
    except (InvalidOperation, ValueError, TypeError):
        raise ValidationError(localized('السعر يجب أن يكون رقمًا غير سالب بدقتين عشريتين وضمن الحد المسموح.',
                                       'Enter a non-negative price with at most two decimal places within the allowed limit.'))


def load_json_payload(value):
    def reject_constant(_):
        raise ValueError('Non-finite JSON number')
    try:
        return json.loads(value, parse_constant=reject_constant)
    except (ValueError, UnicodeDecodeError, TypeError, OverflowError, RecursionError):
        raise ValidationError(gettext('توجد بيانات غير صالحة في السلة.'))
