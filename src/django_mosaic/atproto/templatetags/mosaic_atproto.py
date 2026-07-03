from django import template

from django_mosaic.atproto import lexicons, reactions

register = template.Library()


@register.simple_tag
def atproto_reactions(post):
    """Fetch (cached) ATmosphere reactions for a synced post, or None."""
    return reactions.reactions_for(post)


@register.simple_tag
def atproto_handle():
    """The site owner's configured handle."""
    from django_mosaic.atproto import conf

    return conf.get_setting("HANDLE")


@register.filter
def blob_url(blob):
    """URL for a blob dict from one of the owner's records (images etc.)."""
    return lexicons.blob_url(blob)


@register.filter
def tabbed(value, sep=", "):
    """Join a tab-separated string (e.g. BookHive authors) with `sep`."""
    return sep.join(part for part in str(value).split("\t") if part)


@register.filter
def half_stars(value):
    """BookHive stores stars as half-stars 1-10; render as 0.5-5."""
    try:
        stars = int(value) / 2
    except (TypeError, ValueError):
        return ""
    return f"{stars:g}"


@register.filter
def token_name(value):
    """Trailing name of a lexicon token ('...defs#finished' -> 'finished')."""
    return str(value).rsplit("#", 1)[-1]
