"""Validated restaurant menu colors and readable derived surfaces."""
import re

from django.core.exceptions import ValidationError

from .validation import localized


MENU_COLOR_DEFAULTS = {
    'menu_primary_color': '#1d6150',
    'menu_background_color': '#f8f8f4',
    'menu_text_color': '#193b34',
}


def validate_menu_color(value):
    if not isinstance(value, str) or not re.fullmatch(r'#[0-9a-fA-F]{6}', value):
        raise ValidationError(localized('اختر لونًا صالحًا بصيغة #RRGGBB.', 'Choose a valid color in #RRGGBB format.'))


def luminance(color):
    channels = [int(color[i:i + 2], 16) / 255 for i in (1, 3, 5)]
    linear = [value / 12.92 if value <= .04045 else ((value + .055) / 1.055) ** 2.4 for value in channels]
    return sum(value * weight for value, weight in zip(linear, (.2126, .7152, .0722)))


def contrast(first, second):
    light, dark = sorted((luminance(first), luminance(second)), reverse=True)
    return (light + .05) / (dark + .05)


def validate_menu_contrast(text, background):
    if contrast(text, background) < 4.5:
        raise ValidationError(localized(
            'لون النص قريب من الخلفية. اختر لونين أوضح (تباين 4.5:1 على الأقل).',
            'Text and background are too similar. Choose clearer colors (at least 4.5:1 contrast).'))


def mix(first, second, weight):
    # Match Math.floor(x + .5) in the browser preview, including .5 ties.
    values = [int(int(first[i:i + 2], 16) * weight + int(second[i:i + 2], 16) * (1 - weight) + .5)
              for i in (1, 3, 5)]
    return '#' + ''.join(f'{value:02x}' for value in values)


def menu_theme(values):
    """Never interpolate unvalidated database/form values into CSS."""
    try:
        colors = {key: values[key].lower() for key in MENU_COLOR_DEFAULTS}
        for value in colors.values():
            validate_menu_color(value)
        validate_menu_contrast(colors['menu_text_color'], colors['menu_background_color'])
    except (KeyError, AttributeError, TypeError, ValidationError):
        colors = MENU_COLOR_DEFAULTS.copy()
    primary, background, text = (colors[key] for key in MENU_COLOR_DEFAULTS)
    on_primary = '#ffffff' if contrast(primary, '#ffffff') >= contrast(primary, '#000000') else '#000000'
    edge = '#ffffff' if luminance(background) > luminance(text) else '#000000'
    surface = mix(background, edge, .3)
    muted = text
    for weight in (.75, .8, .85, .9, .95):
        candidate = mix(text, background, weight)
        if min(contrast(candidate, background), contrast(candidate, surface)) >= 4.5:
            muted = candidate
            break
    return {'primary': primary, 'background': background, 'text': text, 'on_primary': on_primary,
            'surface': surface, 'muted': muted, 'border': mix(text, background, .25),
            'soft': mix(primary, background, .10)}


def restaurant_menu_theme(restaurant):
    return menu_theme({key: getattr(restaurant, key) for key in MENU_COLOR_DEFAULTS})
