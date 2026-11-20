from django import template
from django.templatetags.static import static
from django.utils.html import format_html

from core.presentation import localized_url

register = template.Library()


@register.simple_tag(takes_context=True)
def bi(context, ar, en):
    return en if context.get("english") else ar


@register.simple_tag(takes_context=True)
def public_url(context, name, *args):
    return localized_url(name, context.get("english"), *args)


# Existing public journey data uses these shorter names.
ICON_ALIASES = {
    "close": "x", "chevron": "chevron-down", "play": "circle-play",
    "scan": "scan-line", "cup": "coffee", "ticket": "receipt-text",
    "book": "book-open", "shield": "shield-check", "bell": "concierge-bell",
}


@register.simple_tag
def symbol(name):
    icon = ICON_ALIASES.get(name, name)
    return format_html(
        '<svg class="ui-icon ui-icon-{}" width="20" height="20" viewBox="0 0 24 24" '
        'fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" '
        'stroke-linejoin="round" aria-hidden="true" focusable="false">'
        '<use href="{}#{}"></use></svg>',
        icon, static("brand/workspace-icons.svg"), icon,
    )


@register.simple_tag
def public_nav_icon(name):
    icons = {
        "home": "panels-top-left", "features": "layers", "solutions": "store",
        "how_it_works": "list-ordered", "pricing": "tag", "demo": "monitor-play",
        "resources": "book-open-text",
    }
    return symbol(icons[name]) if name in icons else ""
