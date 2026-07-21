from .models import Notification, AdminMessage
from django.db.models import Q
from django.conf import settings
import datetime, calendar

def user_member(request):
    ctx = {'user_member': None, 'notif_count': 0,
           'active_month': datetime.date.today().month,
           'active_year':  datetime.date.today().year,
           'active_month_name': '',
           'mess': None,
           'unread_admin_messages': None,
           'app_name':    getattr(settings, 'APP_NAME', 'Meal Manager'),
           'app_tagline': getattr(settings, 'APP_TAGLINE', 'Meal Management System')}
    if request.user.is_authenticated:
        try:
            member = request.user.member
            notif_count = Notification.objects.filter(is_read=False).filter(
                Q(recipient=member) | Q(broadcast=True)
            ).count()
            unread_admin_messages = AdminMessage.objects.filter(mess=member.mess, is_read=False)

            # Read active month from session (set by Settings page)
            today = datetime.date.today()
            active_month = request.session.get('active_month', today.month)
            active_year  = request.session.get('active_year',  today.year)

            ctx.update({
                'user_member':       member,
                'mess':              member.mess,
                'notif_count':       notif_count,
                'unread_admin_messages': unread_admin_messages,
                'active_month':      active_month,
                'active_year':       active_year,
                'active_month_name': calendar.month_name[active_month],
            })
        except Exception:
            pass
    return ctx
