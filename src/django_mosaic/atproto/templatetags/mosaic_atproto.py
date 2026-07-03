from django import template

from django_mosaic.atproto import reactions

register = template.Library()


@register.simple_tag
def atproto_reactions(post):
    """Fetch (cached) ATmosphere reactions for a synced post, or None."""
    return reactions.reactions_for(post)
