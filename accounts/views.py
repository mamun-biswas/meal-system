from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.models import User
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.http import JsonResponse
from django.db import transaction
from django.db.models import Sum, Q, F
from decimal import Decimal
import json, random, datetime, calendar

from .models import Member, SubManagerPermission, ActivityLog, Notification, Mess, AdminMessage

AVATAR_COLORS = [
    '#6366f1','#8b5cf6','#ec4899','#ef4444','#f97316',
    '#eab308','#22c55e','#14b8a6','#3b82f6','#06b6d4',
]

def log_action(member, action, detail='', request=None):
    ip = None
    if request:
        x = request.META.get('HTTP_X_FORWARDED_FOR')
        ip = x.split(',')[0].strip() if x else request.META.get('REMOTE_ADDR')
    ActivityLog.objects.create(mess=member.mess, member=member, action=action, detail=detail, ip=ip)


# ── AUTH ──────────────────────────────────────────────────────────────────────
def login_view(request):
    if request.user.is_authenticated:
        return redirect('dashboard')
    if request.method == 'POST':
        mess_ident = request.POST.get('mess_id', '').strip()
        phone      = request.POST.get('phone', '').strip()
        password   = request.POST.get('password', '').strip()

        mess = Mess.objects.filter(code__iexact=mess_ident).first() \
               or Mess.objects.filter(name__iexact=mess_ident).first()
        if not mess:
            messages.error(request, 'Mess not found. Please check your Mess ID / Name.')
            return render(request, 'accounts/login.html', {'mess_id': mess_ident, 'phone': phone})
        try:
            member = Member.all_objects.get(mess=mess, phone=phone, is_active=True)
            user   = authenticate(request, username=member.user.username, password=password)
            if user:
                login(request, user)
                log_action(member, 'Logged in', request=request)
                return redirect('dashboard')
            messages.error(request, 'Incorrect password. Please try again.')
        except Member.DoesNotExist:
            messages.error(request, 'No member with that phone number was found in this mess.')
        return render(request, 'accounts/login.html', {'mess_id': mess_ident, 'phone': phone})
    return render(request, 'accounts/login.html')


def register_mess(request):
    """Public signup: create a brand-new, independent Mess. The person
    registering becomes that Mess's first member, automatically given
    the Manager role — there is no separate 'admin' concept, the
    Manager IS the first member."""
    if request.user.is_authenticated:
        return redirect('dashboard')
    if request.method == 'POST':
        mess_name = request.POST.get('mess_name', '').strip()
        name      = request.POST.get('name', '').strip()
        phone     = request.POST.get('phone', '').strip()
        pwd       = request.POST.get('password', '').strip()
        confirm   = request.POST.get('confirm_password', '').strip()

        errors = []
        if not mess_name:
            errors.append('Mess name is required.')
        if not name:
            errors.append('Your name is required.')
        if not phone:
            errors.append('Phone number is required.')
        if not pwd:
            errors.append('Password is required.')
        elif len(pwd) < 6:
            errors.append('Password must be at least 6 characters.')
        elif pwd != confirm:
            errors.append('Passwords do not match.')

        if errors:
            for e in errors:
                messages.error(request, e)
            return render(request, 'accounts/register_mess.html', {
                'mess_name': mess_name, 'name': name, 'phone': phone,
            })

        with transaction.atomic():
            mess = Mess.objects.create(name=mess_name, is_approved=False)
            # Username must be globally unique across the whole system (Django
            # auth User isn't mess-scoped), so namespace it by the new mess code.
            username = f'user_{mess.code}_{phone}'
            user  = User.objects.create_user(username=username, password=pwd)
            color = random.choice(AVATAR_COLORS)
            member = Member.objects.create(
                mess=mess, user=user, phone=phone, name=name,
                role=Member.ROLE_MANAGER, avatar_color=color,
            )
            log_action(member, 'Registered new mess', f'Mess:{mess.name} ({mess.code})', request)

        user = authenticate(request, username=username, password=pwd)
        if user:
            login(request, user)
        messages.info(
            request,
            f'🎉 "{mess.name}" has been created — your Mess ID is {mess.code}. '
            f'After Admin Approval You Can Use Your Created Mess.'
        )
        return redirect('dashboard')
    return render(request, 'accounts/register_mess.html')


@login_required
def logout_view(request):
    try:
        log_action(request.user.member, 'Logged out', request=request)
    except Exception:
        pass
    logout(request)
    return redirect('login')


@login_required
def pending_approval(request):
    """Shown to every member of a mess that hasn't been approved by a
    Global Admin yet — MessMiddleware redirects here for any other URL."""
    member = request.user.member
    if member.mess.is_approved:
        return redirect('dashboard')
    admin_messages = AdminMessage.objects.filter(mess=member.mess).order_by('-created_at')
    return render(request, 'accounts/pending_approval.html', {
        'mess': member.mess,
        'admin_messages': admin_messages,
    })


@login_required
def mark_admin_message_read(request, pk):
    if request.method == 'POST':
        msg = get_object_or_404(AdminMessage, pk=pk, mess=request.user.member.mess)
        msg.is_read = True
        msg.save()
    return redirect(request.POST.get('next') or 'dashboard')


# ── DASHBOARD ─────────────────────────────────────────────────────────────────
@login_required
def dashboard(request):
    from meals.models import MealMark, MonthlySettings, Announcement
    from finance.models import Deposit, Expense
    from accounts.month_helpers import get_active_my

    today        = datetime.date.today()
    month, year  = get_active_my(request)          # ← uses session active month
    member       = request.user.member

    # Monthly stats
    ms           = MonthlySettings.objects.filter(month=month, year=year).first()
    cooking_cost = ms.cooking_cost if ms else Decimal('500')

    # select_related + list(): fetch the month's marks once, with their
    # member already attached, instead of a fresh query per mark (to
    # resolve mk.member) and a fresh query per member later on.
    marks        = list(MealMark.objects.filter(date__month=month, date__year=year)
                         .select_related('member__mess'))
    # DayConfig + MealCountSettings loaded once for this mess and reused
    # by every effective_count() call below — the single biggest source
    # of N+1 queries on this page otherwise (2+ queries per mark).
    day_config_map, weights = MealMark.preload_calc_context(member.mess)
    total_eff    = sum((mk.effective_count(day_config_map, weights) for mk in marks), Decimal('0'))

    total_exp = Expense.objects.filter(date__month=month, date__year=year
                ).aggregate(t=Sum('amount'))['t'] or Decimal('0')
    total_dep = Deposit.objects.filter(date__month=month, date__year=year
                ).aggregate(t=Sum('amount'))['t'] or Decimal('0')

    meal_rate = (total_exp / Decimal(str(total_eff))).quantize(Decimal('0.01')) \
                if total_eff > 0 else Decimal('0')
    fund_bal  = total_dep - total_exp

    # Today's real meals — always based on actual calendar today. Reuse
    # `marks` in-memory when today falls inside the viewed month instead
    # of issuing another query for the same rows.
    if today.month == month and today.year == year:
        today_marks = [mk for mk in marks if mk.date == today]
    else:
        today_marks = list(MealMark.objects.filter(date=today).select_related('member__mess'))
    today_meals = sum((mk.effective_count(day_config_map, weights) for mk in today_marks), Decimal('0'))

    # When viewing a past/future month, also compute meals for the
    # equivalent day in that month (same day-of-month, if it exists)
    viewing_current_month = (month == today.month and year == today.year)
    active_month_day_meals = today_meals  # default: same as today
    active_month_day_label = today        # default: today's date
    if not viewing_current_month:
        import calendar as _cal
        dim = _cal.monthrange(year, month)[1]
        target_day = min(today.day, dim)   # clamp to days in that month
        target_date = datetime.date(year, month, target_day)
        active_month_day_marks = [mk for mk in marks if mk.date == target_date]
        active_month_day_meals = sum(
            (mk.effective_count(day_config_map, weights) for mk in active_month_day_marks), Decimal('0'))
        active_month_day_label = target_date

    active_members = Member.objects.filter(is_active=True).count()
    # Recent Activity is management-only — plain Members shouldn't see the
    # house-wide action log, so don't even fetch it for them.
    if member.is_manager() or member.is_sub_manager():
        recent_logs = ActivityLog.objects.select_related('member').all()[:8]
    else:
        recent_logs = ActivityLog.objects.none()
    announcements  = Announcement.objects.filter(is_active=True)[:3]

    # Chart: last 7 days (always real today). Fetched as a single query
    # for the whole week (instead of one query per day) and grouped by
    # date in Python; same for the matching week of expenses.
    week_start = today - datetime.timedelta(days=6)
    week_marks = list(MealMark.objects.filter(date__gte=week_start, date__lte=today)
                       .select_related('member__mess'))
    week_marks_by_date = {}
    for mk in week_marks:
        week_marks_by_date.setdefault(mk.date, []).append(mk)
    week_exp_by_date = {}
    for row in (Expense.objects.filter(date__gte=week_start, date__lte=today)
                .values('date').annotate(t=Sum('amount'))):
        week_exp_by_date[row['date']] = row['t'] or 0

    chart_days, chart_meals_data, chart_exp_data = [], [], []
    for i in range(6, -1, -1):
        d  = today - datetime.timedelta(days=i)
        dm = sum((mk.effective_count(day_config_map, weights) for mk in week_marks_by_date.get(d, [])),
                 Decimal('0'))
        de = week_exp_by_date.get(d, 0)
        chart_days.append(d.strftime('%d %b'))
        chart_meals_data.append(float(dm))
        chart_exp_data.append(float(de))

    # Per-member summary
    all_members    = Member.objects.filter(is_active=True).select_related('user')
    marks_by_member = {}
    for mk in marks:
        marks_by_member.setdefault(mk.member_id, []).append(mk)
    deposits_by_member = {}
    for row in (Deposit.objects.filter(date__month=month, date__year=year)
                .values('member_id').annotate(t=Sum('amount'))):
        deposits_by_member[row['member_id']] = row['t'] or Decimal('0')

    member_summary = []
    for m in all_members:
        m_eff  = sum((mk.effective_count(day_config_map, weights)
                      for mk in marks_by_member.get(m.id, [])), Decimal('0'))
        m_dep  = deposits_by_member.get(m.id, Decimal('0'))
        m_cook = cooking_cost if m_eff > 0 else Decimal('0')
        m_cost = (Decimal(str(m_eff)) * meal_rate + m_cook).quantize(Decimal('0.01'))
        m_bal  = m_dep - m_cost
        member_summary.append({
            'member': m, 'meals': m_eff, 'deposit': m_dep,
            'cost': m_cost, 'balance': m_bal,
        })

    return render(request, 'accounts/dashboard.html', {
        'active_members': active_members,
        'today_meals':    float(today_meals),
        'active_month_day_meals': float(active_month_day_meals),
        'active_month_day_label': active_month_day_label,
        'viewing_current_month':  viewing_current_month,
        'total_meals':    total_eff,
        'total_deposit':  total_dep,
        'total_expense':  total_exp,
        'meal_rate':      meal_rate,
        'fund_balance':   fund_bal,
        'month_name':     calendar.month_name[month],
        'year': year, 'month': month,
        'today': today,
        'is_active_month': viewing_current_month,
        'recent_logs':     recent_logs,
        'announcements':   announcements,
        'member_summary':  member_summary,
        'cooking_cost':    cooking_cost,
        'chart_days':      json.dumps(chart_days),
        'chart_meals':     json.dumps(chart_meals_data),
        'chart_exp':       json.dumps(chart_exp_data),
    })


# ── MEMBERS ───────────────────────────────────────────────────────────────────
@login_required
def member_list(request):
    member = request.user.member
    if not (member.is_manager() or member.has_perm_code('manage_members')):
        messages.error(request, 'Access denied.')
        return redirect('dashboard')
    members = Member.objects.filter(is_active=True).select_related('user').order_by('joined_date', 'name', 'id')
    return render(request, 'accounts/members.html', {'members': members})


@login_required
def member_add(request):
    member = request.user.member
    if not member.is_manager():
        messages.error(request, 'Only Manager can add members.')
        return redirect('member_list')
    if request.method == 'POST':
        with transaction.atomic():
            phone = request.POST.get('phone', '').strip()
            name  = request.POST.get('name', '').strip()
            name_bn = request.POST.get('name_bn', '').strip()
            role  = request.POST.get('role', Member.ROLE_MEMBER)
            room  = request.POST.get('room_number', '').strip()
            note  = request.POST.get('note', '').strip()
            pwd   = request.POST.get('password', '').strip()
            if not phone or not name or not pwd:
                messages.error(request, 'Phone, Name and Password are required.')
                return render(request, 'accounts/member_form.html',
                              {'action': 'Add', 'roles': Member.ROLE_CHOICES})
            if Member.objects.filter(phone=phone).exists():
                messages.error(request, f'Phone {phone} is already registered in this mess.')
                return render(request, 'accounts/member_form.html',
                              {'action': 'Add', 'roles': Member.ROLE_CHOICES})
            username = f'user_{member.mess.code}_{phone}'
            user  = User.objects.create_user(username=username, password=pwd)
            color = random.choice(AVATAR_COLORS)
            m     = Member.objects.create(
                mess=member.mess, user=user, phone=phone, name=name, name_bn=name_bn, role=role,
                room_number=room, note=note, avatar_color=color,
            )
            if role == Member.ROLE_SUB_MANAGER:
                for codename, _ in SubManagerPermission.ALL_PERMS:
                    SubManagerPermission.objects.get_or_create(
                        member=m, codename=codename, defaults={'granted': False}
                    )
            log_action(member, f'Added member: {name}', f'Phone:{phone} Role:{role}', request)
            messages.success(request, f'Member "{name}" added!')
            return redirect('member_list')
    return render(request, 'accounts/member_form.html',
                  {'action': 'Add', 'roles': Member.ROLE_CHOICES})


@login_required
def member_edit(request, pk):
    member = request.user.member
    if not (member.is_manager() or member.has_perm_code('manage_members')):
        messages.error(request, 'Access denied.')
        return redirect('member_list')
    target = get_object_or_404(Member, pk=pk)
    if request.method == 'POST':
        old_role = target.role
        target.name        = request.POST.get('name', target.name).strip()
        target.name_bn     = request.POST.get('name_bn', target.name_bn).strip()
        target.room_number = request.POST.get('room_number', target.room_number).strip()
        target.note        = request.POST.get('note', target.note).strip()
        new_role = request.POST.get('role', target.role)
        role_changed = False
        if member.is_manager() and new_role != target.role:
            target.role = new_role
            role_changed = True
            if new_role == Member.ROLE_SUB_MANAGER:
                for codename, _ in SubManagerPermission.ALL_PERMS:
                    SubManagerPermission.objects.get_or_create(
                        member=target, codename=codename, defaults={'granted': False}
                    )
        pwd = request.POST.get('password', '').strip()
        pwd_changed = False
        if pwd:
            target.user.set_password(pwd)
            target.user.save()
            pwd_changed = True
        target.save()
        detail_parts = [f'Name:{target.name}', f'Room:{target.room_number}']
        if role_changed:
            detail_parts.append(f'Role:{old_role}→{new_role}')
        if pwd_changed:
            detail_parts.append('Password:changed')
        log_action(member, f'Edited member: {target.name}',
                   ' | '.join(detail_parts), request)
        messages.success(request, 'Member updated!')
        return redirect('member_list')
    return render(request, 'accounts/member_form.html',
                  {'action': 'Edit', 'target': target, 'roles': Member.ROLE_CHOICES})


@login_required
def member_deactivate(request, pk):
    member = request.user.member
    if not member.is_manager():
        return JsonResponse({'error': 'Access denied'}, status=403)
    target = get_object_or_404(Member, pk=pk)
    if target.pk == member.pk:
        return JsonResponse({'error': 'You cannot deactivate yourself'}, status=400)
    target.is_active = False
    target.save()
    log_action(member, f'Deactivated member: {target.name}', request=request)
    return JsonResponse({'ok': True, 'message': f'"{target.name}" deactivated.'})


@login_required
def member_permissions(request, pk):
    member = request.user.member
    if not member.is_manager():
        messages.error(request, 'Only Manager can set permissions.')
        return redirect('member_list')
    target    = get_object_or_404(Member, pk=pk, role=Member.ROLE_SUB_MANAGER)
    all_perms = SubManagerPermission.ALL_PERMS
    if request.method == 'POST':
        granted_set = set(request.POST.getlist('permissions'))
        # Fetch every existing permission row for this member in one
        # query instead of a get_or_create() + save() pair per
        # permission (previously up to ~3 queries x 11 permissions).
        existing_perms = {p.codename: p for p in SubManagerPermission.objects.filter(member=target)}
        to_create, to_update = [], []
        for codename, _ in all_perms:
            granted = codename in granted_set
            perm = existing_perms.get(codename)
            if perm is None:
                to_create.append(SubManagerPermission(member=target, codename=codename, granted=granted))
            elif perm.granted != granted:
                perm.granted = granted
                to_update.append(perm)
        if to_create:
            SubManagerPermission.objects.bulk_create(to_create)
        if to_update:
            SubManagerPermission.objects.bulk_update(to_update, ['granted'])
        log_action(member, f'Updated permissions for {target.name}', str(granted_set), request)
        Notification.objects.create(
            mess=target.mess,
            recipient=target,
            title='Your permissions were updated',
            message='Manager has updated your access permissions.',
            ntype=Notification.TYPE_INFO,
        )
        messages.success(request, 'Permissions updated!')
        return redirect('member_list')
    existing  = {p.codename: p.granted for p in SubManagerPermission.objects.filter(member=target)}
    perms_ctx = [(code, label, existing.get(code, False)) for code, label in all_perms]
    return render(request, 'accounts/permissions.html', {'target': target, 'perms': perms_ctx})


# ── MY STATEMENT ─────────────────────────────────────────────────────────────
@login_required
def my_statement(request):
    """The logged-in member's own personal financial statement."""
    from finance.models import Deposit, Expense, MealClosing, ClosingRecord
    from meals.models import MealMark
    from meals.views import compute_month_stats
    from accounts.month_helpers import get_active_my, months_list, years_list

    member      = request.user.member
    month, year = get_active_my(request)

    stats    = compute_month_stats(month, year)
    mem_stat = next((s for s in stats['per_member'] if s['member'].id == member.id), None)
    deposits = Deposit.objects.filter(
        member=member, date__month=month, date__year=year
    ).order_by('date')
    # See finance.views.member_statement for why this is materialized to
    # a list with precomputed .eff/.special attributes rather than left
    # as a bare QuerySet: my_statement.html displays {{ mk.eff }} /
    # {{ mk.special }}, and Django templates call a method with zero
    # arguments, which would otherwise re-trigger the per-row DayConfig +
    # MealCountSettings queries regardless of compute_month_stats() above
    # already being N+1-free.
    meal_marks = list(MealMark.objects.filter(
        member=member, date__month=month, date__year=year
    ).select_related('member__mess').order_by('date'))
    day_config_map, weights = MealMark.preload_calc_context(
        member.mess, dates=(mk.date for mk in meal_marks))
    for mk in meal_marks:
        mk.eff = mk.effective_count(day_config_map, weights)
        mk.special = mk.is_special_day(day_config_map)

    # Fetch finalised closing record for this member+month if it exists
    closing_record = None
    try:
        closing = MealClosing.objects.get(month=month, year=year,
                                          status=MealClosing.STATUS_CLOSED)
        closing_record = ClosingRecord.objects.select_related('verdict_by').get(
            closing=closing, member=member
        )
    except (MealClosing.DoesNotExist, ClosingRecord.DoesNotExist):
        pass

    return render(request, 'accounts/my_statement.html', {
        'target':         member,
        'mem_stat':       mem_stat,
        'stats':          stats,
        'deposits':       deposits,
        'meal_marks':     meal_marks,
        'month':          month,
        'year':           year,
        'month_name':     calendar.month_name[month],
        'months':         months_list(),
        'years':          years_list(year),
        'closing_record': closing_record,
    })


# ── PROFILE ───────────────────────────────────────────────────────────────────
@login_required
def profile_view(request):
    from meals.models import MealMark
    from finance.models import Deposit
    from accounts.month_helpers import get_active_my
    member = request.user.member

    if request.method == 'POST':
        action = request.POST.get('action', 'update_profile')

        if action == 'change_password':
            current_pwd  = request.POST.get('current_password', '').strip()
            new_pwd      = request.POST.get('new_password', '').strip()
            confirm_pwd  = request.POST.get('confirm_password', '').strip()

            if not member.user.check_password(current_pwd):
                messages.error(request, '❌ Current password is incorrect.')
            elif not new_pwd:
                messages.error(request, '❌ New password cannot be empty.')
            elif new_pwd != confirm_pwd:
                messages.error(request, '❌ New passwords do not match.')
            elif len(new_pwd) < 6:
                messages.error(request, '❌ Password must be at least 6 characters.')
            else:
                member.user.set_password(new_pwd)
                member.user.save()
                from django.contrib.auth import update_session_auth_hash
                update_session_auth_hash(request, member.user)
                log_action(member, 'Changed password', request=request)
                messages.success(request, '✅ Password changed successfully!')
            return redirect('profile')

        else:  # update_profile
            member.name        = request.POST.get('name', member.name).strip()
            member.name_bn     = request.POST.get('name_bn', member.name_bn).strip()
            member.room_number = request.POST.get('room_number', member.room_number).strip()
            member.note        = request.POST.get('note', member.note).strip()
            member.save()
            log_action(member, 'Updated profile', request=request)
            messages.success(request, '✅ Profile updated!')
            return redirect('profile')

    # Stats for profile page
    month, year = get_active_my(request)
    import calendar as _cal
    # DayConfig/MealCountSettings preloaded once for the whole mess (not
    # date-filtered, so it's valid for both the "this month" figures
    # below and the last-10 "recent meals" list, whatever dates those
    # happen to span) — reused everywhere effective_count()/
    # is_special_day() is needed on this page so neither ever falls back
    # to its slow, query-per-row default path.
    day_config_map, weights = MealMark.preload_calc_context(member.mess)
    recent_meals = list(MealMark.objects.filter(member=member).select_related('member__mess').order_by('-date')[:10])
    for mk in recent_meals:
        mk.eff = mk.effective_count(day_config_map, weights)
        mk.special = mk.is_special_day(day_config_map)
    recent_deposits = Deposit.objects.filter(member=member).order_by('-date')[:5]
    this_month_marks = MealMark.objects.filter(
        member=member, date__month=month, date__year=year
    )
    this_month_meals = this_month_marks.aggregate(
        t=Sum(F('morning') + F('lunch') + F('dinner'))
    )['t'] or Decimal('0')
    # Effective meals (what the meal rate / cost calc actually uses) —
    # reflects both Special Meal Entry (per-day) and Meal Count Settings
    # (global) weights. Computed separately from the raw sum above
    # because effective_count() isn't a plain DB column, it's derived
    # per-mark from MealCountSettings x DayConfig.
    #
    # this_month_marks is materialized once here (it's a QuerySet, so
    # iterating it twice would otherwise re-run the query).
    this_month_marks_list = list(this_month_marks.select_related('member__mess'))
    this_month_effective = sum(
        (mk.effective_count(day_config_map, weights) for mk in this_month_marks_list), Decimal('0'))
    this_month_has_special = any(mk.is_special_day(day_config_map) for mk in this_month_marks_list)
    this_month_deposit = Deposit.objects.filter(
        member=member, date__month=month, date__year=year
    ).aggregate(t=Sum('amount'))['t'] or Decimal('0')
    recent_activity = ActivityLog.objects.filter(member=member).order_by('-timestamp')[:8]

    return render(request, 'accounts/profile.html', {
        'target': member,
        'recent_meals': recent_meals,
        'recent_deposits': recent_deposits,
        'this_month_meals': this_month_meals,
        'this_month_effective': this_month_effective,
        'this_month_has_special': this_month_has_special,
        'this_month_deposit': this_month_deposit,
        'month_name': _cal.month_name[month],
        'year': year,
        'recent_activity': recent_activity,
    })


# ── NOTIFICATIONS ─────────────────────────────────────────────────────────────
@login_required
def notifications_view(request):
    member = request.user.member
    notifs = Notification.objects.filter(
        Q(recipient=member) | Q(broadcast=True)
    ).order_by('-created_at')[:50]
    Notification.objects.filter(recipient=member, is_read=False).update(is_read=True)
    return render(request, 'accounts/notifications.html', {'notifications': notifs})


# ── ACTIVITY LOG ──────────────────────────────────────────────────────────────
@login_required
def activity_log_view(request):
    member = request.user.member
    if not (member.is_manager() or member.is_sub_manager()):
        messages.error(request, 'Access denied.')
        return redirect('dashboard')

    from django.core.paginator import Paginator

    qs = ActivityLog.objects.select_related('member').all()

    # ── Filters ──────────────────────────────────────────────────────────────
    q_search    = request.GET.get('q', '').strip()
    q_member_id = request.GET.get('member_id', '').strip()
    q_date_from = request.GET.get('date_from', '').strip()
    q_date_to   = request.GET.get('date_to', '').strip()
    q_action    = request.GET.get('action_type', '').strip()
    per_page    = int(request.GET.get('per_page', 50))

    if q_search:
        qs = qs.filter(
            Q(action__icontains=q_search) |
            Q(detail__icontains=q_search) |
            Q(ip__icontains=q_search) |
            Q(member__name__icontains=q_search)
        )
    if q_member_id:
        qs = qs.filter(member__id=q_member_id)
    if q_date_from:
        try:
            qs = qs.filter(timestamp__date__gte=datetime.date.fromisoformat(q_date_from))
        except ValueError:
            pass
    if q_date_to:
        try:
            qs = qs.filter(timestamp__date__lte=datetime.date.fromisoformat(q_date_to))
        except ValueError:
            pass
    if q_action:
        qs = qs.filter(action__icontains=q_action)

    total_count = qs.count()
    paginator   = Paginator(qs, per_page)
    page_num    = int(request.GET.get('page', 1))
    page_obj    = paginator.get_page(page_num)

    # Distinct action prefixes for the action-type quick filter
    action_types = ['Logged in', 'Logged out', 'Meal mark', 'Meal history',
                    'Added member', 'Edited member', 'Added deposit', 'Added expense',
                    'Updated profile', 'Changed password', 'Day config', 'Monthly settings',
                    'Posted announcement', 'Active month changed']

    all_members = Member.objects.filter(is_active=True).order_by('name')

    # Build query string without page for pagination links
    get_params = request.GET.copy()
    get_params.pop('page', None)
    filter_qs = get_params.urlencode()

    any_filter = any([q_search, q_member_id, q_date_from, q_date_to, q_action])

    return render(request, 'accounts/activity_log.html', {
        'page_obj':    page_obj,
        'total_count': total_count,
        'all_members': all_members,
        'action_types': action_types,
        'filter_qs':   filter_qs,
        'any_filter':  any_filter,
        'per_page':    per_page,
        # current filter values
        'q_search':    q_search,
        'q_member_id': q_member_id,
        'q_date_from': q_date_from,
        'q_date_to':   q_date_to,
        'q_action':    q_action,
    })
