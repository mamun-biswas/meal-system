from django.shortcuts import redirect
from .mess_context import set_current_mess, clear_current_mess

# Paths that must always remain reachable even when a member's mess is
# still pending admin approval — otherwise they'd have no way to log out
# or even see *why* they're blocked.
_APPROVAL_GATE_EXEMPT_PREFIXES = (
    '/pending-approval/',
    '/logout/',
    '/static/',
    '/media/',
    '/superadmin/',
)


class MessMiddleware:
    """Sets the logged-in user's mess as the 'current mess' for the
    duration of the request, so that MessScopedManager / MemberManager /
    MemberMessScopedManager automatically restrict every default query to
    that mess. Cleared at the end of every request (success or error) so
    threads never leak a mess between unrelated requests.

    Also gates access: a mess created via public registration starts
    unapproved, and every member of it gets redirected to a "pending
    approval" page for any URL until a Global Admin approves it."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        mess = None
        user = getattr(request, 'user', None)
        if user is not None and getattr(user, 'is_authenticated', False):
            member = getattr(user, 'member', None)
            if member is not None:
                mess = member.mess
        set_current_mess(mess)
        try:
            if mess is not None and not mess.is_approved:
                exempt = any(request.path.startswith(p) for p in _APPROVAL_GATE_EXEMPT_PREFIXES)
                if not exempt:
                    return redirect('pending_approval')
            response = self.get_response(request)
        finally:
            clear_current_mess()
        return response
