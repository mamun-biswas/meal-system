from django import template
register = template.Library()

@register.filter
def has_perm(member, codename):
    if member is None:
        return False
    try:
        return member.has_perm_code(codename)
    except:
        return False

@register.filter
def is_role(member, role):
    if member is None:
        return False
    return member.role == role

@register.filter
def bn_digit(value):
    """Convert any number/string to Bangla digits."""
    bn_map = str.maketrans('0123456789', '০১২৩৪৫৬৭৮৯')
    return str(value).translate(bn_map)

@register.filter
def trim0(value):
    """Format a Decimal/number as a plain string with trailing zeros
    trimmed — 1.000 -> '1', 0.500 -> '0.5', 2.750 -> '2.75'. Used for
    number-input `value=` attributes so they don't show as 1.000
    (Decimal's default str()) when the field stores extra precision."""
    try:
        s = format(value, 'f')
    except (TypeError, ValueError):
        return value
    if '.' in s:
        s = s.rstrip('0').rstrip('.')
    return s or '0'
