"""
Shared active-month helpers.
All views call get_active_my(request) instead of reading ?month=&year= directly.

Priority:
  1. Explicit URL param (?month=X&year=Y)  — lets month-selector overrides work per page
  2. Session value                          — set by Settings page "Set as Active Month"
  3. Today's month/year                    — safe fallback
"""
import datetime, calendar


def get_active_my(request):
    """Return (month, year) for the current request."""
    today = datetime.date.today()
    # URL param takes highest priority (allows per-page selectors to still work)
    try:
        m = int(request.GET.get('month') or request.session.get('active_month') or today.month)
        y = int(request.GET.get('year')  or request.session.get('active_year')  or today.year)
        # Clamp to valid range
        m = max(1, min(12, m))
        if y < 2000 or y > 2099:
            y = today.year
        return m, y
    except (TypeError, ValueError):
        return today.month, today.year


def set_active_my(request, month, year):
    """Persist the active month to session."""
    request.session['active_month'] = int(month)
    request.session['active_year']  = int(year)
    request.session.modified = True


def months_list():
    return [(i, calendar.month_name[i]) for i in range(1, 13)]


def years_list(year):
    return [year - 1, year, year + 1]
