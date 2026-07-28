from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.http import JsonResponse, Http404
from django.db.models import Sum
from django.utils import timezone
from decimal import Decimal
from accounts.models import Member
from accounts.views import log_action
from accounts.month_helpers import get_active_my, set_active_my, months_list, years_list
from .models import MealMark, MealMarkDraft, MemberInputSettings, DayMealLock, DayConfig, MonthlySettings, Announcement, MealCountSettings
import datetime, calendar, json


def require_perm(perm):
    def decorator(view_func):
        from functools import wraps
        @wraps(view_func)
        def wrapper(request, *args, **kwargs):
            if not request.user.is_authenticated:
                return redirect('login')
            try:
                m = request.user.member
                if not m.has_perm_code(perm):
                    messages.error(request, 'You do not have permission for this action.')
                    return redirect('dashboard')
            except Exception:
                return redirect('login')
            return view_func(request, *args, **kwargs)
        return wrapper
    return decorator


def compute_month_stats(month, year):
    from finance.models import Deposit, Expense
    from accounts.mess_context import get_current_mess
    members   = list(Member.objects.filter(is_active=True))
    ms        = MonthlySettings.objects.filter(month=month, year=year).first()
    cook_cost = ms.cooking_cost if ms else Decimal('500')
    # select_related('member') avoids a query-per-mark to resolve
    # mk.member (needed by effective_count()); list() evaluates the
    # queryset once instead of re-querying it every time it's iterated.
    marks     = list(MealMark.objects.filter(date__month=month, date__year=year)
                      .select_related('member__mess'))

    # Deposits for every active member this month, fetched once and
    # grouped in Python instead of running a filtered aggregate query
    # per member below.
    deposits_by_member = {}
    dep_rows = (Deposit.objects.filter(date__month=month, date__year=year)
                .values('member_id').annotate(t=Sum('amount')))
    for row in dep_rows:
        deposits_by_member[row['member_id']] = row['t'] or Decimal('0')

    # Preload DayConfig + MealCountSettings once for the whole mess
    # (falls back to each mark's own mess if `get_current_mess()` isn't
    # set, e.g. when called outside a request) instead of re-querying
    # them for every single mark — this is what removes the N+1 here.
    mess = get_current_mess()
    if mess is None and marks:
        mess = marks[0].member.mess
    if mess is not None:
        day_config_map, weights = MealMark.preload_calc_context(
            mess, dates=(mk.date for mk in marks)
        )
    else:
        day_config_map, weights = None, None

    marks_by_member = {}
    for mk in marks:
        marks_by_member.setdefault(mk.member_id, []).append(mk)

    total_eff = Decimal('0')
    for mk in marks:
        total_eff += mk.effective_count(day_config_map, weights)

    total_exp = Expense.objects.filter(date__month=month, date__year=year
                ).aggregate(t=Sum('amount'))['t'] or Decimal('0')
    total_dep = Deposit.objects.filter(date__month=month, date__year=year
                ).aggregate(t=Sum('amount'))['t'] or Decimal('0')
    meal_rate = (total_exp / total_eff).quantize(Decimal('0.01')) if total_eff > 0 else Decimal('0')
    fund_bal  = total_dep - total_exp

    per_member = []
    for m in members:
        m_marks = marks_by_member.get(m.id, [])
        m_eff = Decimal('0')
        for mk in m_marks:
            m_eff += mk.effective_count(day_config_map, weights)
        m_raw = sum((mk.count for mk in m_marks), Decimal('0'))
        m_dep = deposits_by_member.get(m.id, Decimal('0'))
        m_cook = cook_cost if m_eff > 0 else Decimal('0')
        m_cost = (m_eff * meal_rate + m_cook).quantize(Decimal('0.01'))
        m_meal_only = (m_eff * meal_rate).quantize(Decimal('0.01'))
        m_bal  = m_dep - m_cost
        per_member.append({
            'member': m, 'raw_meals': m_raw, 'eff_meals': m_eff,
            'deposit': m_dep, 'cost': m_cost, 'balance': m_bal,
            'cook_cost': m_cook, 'meal_only_cost': m_meal_only,
        })
    return {
        'total_eff': total_eff, 'total_exp': total_exp, 'total_dep': total_dep,
        'meal_rate': meal_rate, 'fund_balance': fund_bal, 'cook_cost': cook_cost,
        'per_member': per_member, 'month': month, 'year': year,
        'month_name': calendar.month_name[month],
    }


@login_required
def monthly_meal_record(request):
    """Dedicated page — 'Monthly Meal Record' (formerly the 'Monthly
    Matrix' tab inside Meal Mark). Shows the full month's committed
    M/L/D meal counts per member in a grid, with day/member totals.
    Purely a read-only report: it renders committed MealMark data only
    (never drafts), same as the old matrix tab did.

    Visibility mirrors the old tab exactly: any logged-in user can open
    this page. A Manager/Sub-Manager with the meal_mark permission sees
    the whole house roster; a plain Member sees only their own row —
    same scoping meal_mark() has always applied to the roster/grid data
    sent to the front end.
    """
    month, year = get_active_my(request)
    actor       = request.user.member
    all_members = list(Member.objects.filter(is_active=True).order_by('name'))
    dim         = calendar.monthrange(year, month)[1]
    marks       = MealMark.objects.filter(date__month=month, date__year=year).select_related('member__mess')
    day_cfgs    = {dc.date: dc for dc in DayConfig.objects.filter(date__month=month, date__year=year)}
    # Same weights row this mess already loads elsewhere; loaded once
    # here too so effective_count() below doesn't hit the DB per mark.
    weights     = MealCountSettings.load(actor.mess)

    can_edit_all = actor.has_perm_code('meal_mark')
    if can_edit_all:
        visible_members = all_members
    else:
        visible_members = [m for m in all_members if m.id == actor.id]

    grid = {str(m.id): {} for m in all_members}
    for mk in marks:
        key = str(mk.member_id)
        if key in grid:
            grid[key][str(mk.date.day)] = {
                'morning': float(mk.morning),
                'lunch':   float(mk.lunch),
                'dinner':  float(mk.dinner),
                'count':   float(mk.count),
                'eff':     float(mk.effective_count(day_cfgs, weights)),
                'note':    mk.note,
            }

    day_totals = {}
    for d in range(1, dim + 1):
        dt  = datetime.date(year, month, d)
        cfg = day_cfgs.get(dt)
        multi_morning = float(cfg.morning_multiplier) if cfg else 1.0
        multi_lunch   = float(cfg.lunch_multiplier) if cfg else 1.0
        multi_dinner  = float(cfg.dinner_multiplier) if cfg else 1.0
        label = cfg.label if cfg else ''
        day_totals[str(d)] = {
            'multi_morning': multi_morning,
            'multi_lunch':   multi_lunch,
            'multi_dinner':  multi_dinner,
            'multi':   multi_morning if (multi_morning == multi_lunch == multi_dinner) else None,
            'label':   label,
        }

    members_js = [
        {'id': str(m.id), 'name': m.name, 'initials': m.initials(),
         'color': m.avatar_color, 'room': m.room_number}
        for m in visible_members
    ]
    visible_grid = {str(m.id): grid[str(m.id)] for m in visible_members}

    ctx = {
        'members_js':    json.dumps(members_js),
        'grid_js':       json.dumps(visible_grid),
        'day_totals_js': json.dumps(day_totals),
        'days_in_month': dim,
        'month': month, 'year': year,
        'month_name': calendar.month_name[month],
        'months': months_list(), 'years': years_list(year),
    }
    return render(request, 'meals/monthly_meal_record.html', ctx)


@login_required
def meal_mark(request):
    month, year = get_active_my(request)
    today       = datetime.date.today()
    actor       = request.user.member
    all_members = list(Member.objects.filter(is_active=True).order_by('name'))
    dim         = calendar.monthrange(year, month)[1]
    marks       = MealMark.objects.filter(date__month=month, date__year=year).select_related('member__mess')
    day_cfgs    = {dc.date: dc for dc in DayConfig.objects.filter(date__month=month, date__year=year)}
    day_locks   = {dl.date: dl for dl in DayMealLock.objects.filter(date__month=month, date__year=year)}
    # Loaded once up front (instead of once per mark inside effective_count)
    # and reused for the count_weights_js payload further down too.
    count_weights = MealCountSettings.load(actor.mess)

    # ── Who can edit what ────────────────────────────────────────────
    # Manager / Sub-Manager (with the meal_mark perm) can edit everyone,
    # any slot, any day — unchanged. A plain Member can edit their own
    # row PER MEAL TYPE: each of morning/lunch/dinner has its own
    # independent on/off + daily time window (MemberInputSettings), and
    # a slot becomes permanently uneditable for self-input the moment a
    # Manager/Sub-Manager commits that specific slot for that specific
    # day (DayMealLock) — checked per-day in the template/JS below using
    # the day_totals lock flags already being sent.
    can_edit_all = actor.has_perm_code('meal_mark')
    input_settings = MemberInputSettings.load(actor.mess)
    self_open_slots = input_settings.open_slots_now()  # which slots' time windows are open right now
    can_edit_self = (not can_edit_all) and actor.role == Member.ROLE_MEMBER and bool(self_open_slots)
    can_edit = can_edit_all or can_edit_self

    # A plain Member only ever sees their OWN card on this page — the
    # house-wide roster is a Manager/Sub-Manager view. Day totals below
    # still aggregate over everyone (it's informational, not an edit
    # surface), but the list of member cards rendered is scoped here.
    if can_edit_all:
        visible_members = all_members
    else:
        visible_members = [m for m in all_members if m.id == actor.id]

    # grid[member_id][day] = {morning, lunch, dinner, count, eff, note}
    # Built over the full roster so the day-total aggregates below stay
    # house-wide regardless of who is viewing the page.
    grid = {str(m.id): {} for m in all_members}
    for mk in marks:
        key = str(mk.member_id)
        if key in grid:
            grid[key][str(mk.date.day)] = {
                'morning': float(mk.morning),
                'lunch':   float(mk.lunch),
                'dinner':  float(mk.dinner),
                'count':   float(mk.count),
                'eff':     float(mk.effective_count(day_cfgs, count_weights)),
                'note':    mk.note,
            }

    day_totals = {}
    for d in range(1, dim + 1):
        dt    = datetime.date(year, month, d)
        cfg   = day_cfgs.get(dt)
        lock  = day_locks.get(dt)
        multi_morning = float(cfg.morning_multiplier) if cfg else 1.0
        multi_lunch   = float(cfg.lunch_multiplier) if cfg else 1.0
        multi_dinner  = float(cfg.dinner_multiplier) if cfg else 1.0
        label = cfg.label if cfg else ''
        day_morning = sum(float(grid[str(m.id)].get(str(d), {}).get('morning', 0)) for m in all_members)
        day_lunch   = sum(float(grid[str(m.id)].get(str(d), {}).get('lunch', 0)) for m in all_members)
        day_dinner  = sum(float(grid[str(m.id)].get(str(d), {}).get('dinner', 0)) for m in all_members)
        day_totals[str(d)] = {
            'total':   sum(float(grid[str(m.id)].get(str(d), {}).get('count', 0)) for m in all_members),
            'morning': day_morning,
            'lunch':   day_lunch,
            'dinner':  day_dinner,
            'eff':     sum(float(grid[str(m.id)].get(str(d), {}).get('eff', 0)) for m in all_members),
            'multi_morning': multi_morning,
            'multi_lunch':   multi_lunch,
            'multi_dinner':  multi_dinner,
            # Backward-compat single value: only meaningful when all three slots match.
            'multi':   multi_morning if (multi_morning == multi_lunch == multi_dinner) else None,
            'label':   label,
            'morning_locked': bool(lock and lock.morning_locked),
            'lunch_locked':   bool(lock and lock.lunch_locked),
            'dinner_locked':  bool(lock and lock.dinner_locked),
        }

    # Cards / grid sent to the front end are scoped to visible_members —
    # the full roster for Manager/Sub-Manager, just the actor's own row
    # for a plain Member.
    members_js = [
        {'id': str(m.id), 'name': m.name, 'initials': m.initials(),
         'color': m.avatar_color, 'room': m.room_number}
        for m in visible_members
    ]
    visible_grid = {str(m.id): grid[str(m.id)] for m in visible_members}

    # Date-independent draft values: {member_id: {morning, lunch, dinner}}
    drafts = MealMarkDraft.objects.filter(member__in=visible_members)
    draft_js = {
        str(d.member_id): {
            'morning': float(d.morning),
            'lunch':   float(d.lunch),
            'dinner':  float(d.dinner),
        }
        for d in drafts
    }
    # Date-independent totals across everyone's currently-staged draft —
    # purely informational, shown as a stat card. Not tied to any date
    # since drafts themselves aren't tied to a date.
    draft_totals = {
        'morning': sum(float(d.morning) for d in drafts),
        'lunch':   sum(float(d.lunch) for d in drafts),
        'dinner':  sum(float(d.dinner) for d in drafts),
        'members_with_draft': sum(1 for d in drafts if d.morning or d.lunch or d.dinner),
    }
    draft_totals['total'] = draft_totals['morning'] + draft_totals['lunch'] + draft_totals['dinner']

    if month == today.month and year == today.year:
        default_day = today.day
    else:
        default_day = 1

    input_settings_js = {
        slot: {
            'enabled': getattr(input_settings, f'{slot}_enabled'),
            'start': getattr(input_settings, f'{slot}_start').strftime('%H:%M'),
            'end': getattr(input_settings, f'{slot}_end').strftime('%H:%M'),
            'open_now': input_settings.is_slot_open_now(slot),
        }
        for slot in ('morning', 'lunch', 'dinner')
    }

    # Global per-slot weight from Meal Count Settings (e.g. morning 1
    # mark = 0.5 effective meals). The JS grid needs this directly
    # because it must compute a LIVE effective-meal preview for
    # not-yet-saved draft input (effective_count() only exists on saved
    # MealMark rows) — so the same weight x day-multiplier formula used
    # server-side in MealMark.effective_count() has to be mirrored here.
    count_weights_js = json.dumps({
        'morning': float(count_weights.morning_weight),
        'lunch':   float(count_weights.lunch_weight),
        'dinner':  float(count_weights.dinner_weight),
    })

    ctx = {
        'members_js':    json.dumps(members_js),
        'grid_js':       json.dumps(visible_grid),
        'draft_js':      json.dumps(draft_js),
        'draft_totals':  draft_totals,
        'day_totals_js': json.dumps(day_totals),
        'count_weights_js': count_weights_js,
        'days_in_month': dim,
        'month': month, 'year': year,
        'can_edit':         can_edit,
        'can_edit_all':     can_edit_all,
        'can_edit_self':    can_edit_self,
        'self_open_slots':  self_open_slots,
        'self_open_slots_js': json.dumps(self_open_slots),
        'input_settings':   input_settings,
        'input_settings_js': json.dumps(input_settings_js),
        'own_member_id':    str(actor.id),
        'month_name': calendar.month_name[month],
        'months': months_list(), 'years': years_list(year),
        'default_day': default_day,
        'today': today,
        'members': visible_members,
        'day_range': range(1, dim + 1),
        'grid_raw': visible_grid,
    }
    return render(request, 'meals/meal_mark.html', ctx)



VALID_SLOTS = ('morning', 'lunch', 'dinner')


@login_required
@require_perm('meal_mark')
def meal_save_day(request):
    """Commit on-screen meal counts into MealMark for a specific date.

    Accepts an optional `slots` list — any subset of
    ['morning','lunch','dinner'] — naming exactly which meal type(s) this
    request is committing (the "Save Morning" / "Save Lunch" /
    "Save Dinner" / "Save All Meal" buttons each send a different list).
    If `slots` is omitted, all three are assumed (legacy "Save Day"
    behavior, also used by anything that still commits a full day at
    once).

    Each meal slot for a given date can only ever be committed ONCE —
    DayMealLock tracks that per date. A request naming an already-locked
    slot is rejected outright (no partial commit of the other slots in
    the same request), so Manager/Sub-Manager get a clear "X already
    saved" instead of a silently partial save.
    """
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)
    data   = json.loads(request.body)
    day    = int(data.get('day'))
    month  = int(data.get('month'))
    year   = int(data.get('year'))
    marks  = data.get('marks', {})  # {member_id: {morning, lunch, dinner}}
    raw_slots = data.get('slots')
    if raw_slots is None:
        slots = list(VALID_SLOTS)  # key omitted entirely: legacy "save everything" behavior
    else:
        slots = [s for s in raw_slots if s in VALID_SLOTS]
    if not slots:
        return JsonResponse({'error': 'No valid meal slots specified.'}, status=400)
    date   = datetime.date(year, month, day)
    actor  = request.user.member

    lock, _ = DayMealLock.objects.get_or_create(mess=actor.mess, date=date)
    already_locked = [s for s in slots if getattr(lock, f'{s}_locked')]
    if already_locked:
        names = ' & '.join(s.capitalize() for s in already_locked)
        return JsonResponse({
            'error': f'{names} {"is" if len(already_locked) == 1 else "are"} already saved for this day and cannot be saved again.',
            'already_locked': already_locked,
        }, status=409)

    saved = deleted = 0
    member_ids = [int(mid_str) for mid_str in marks.keys()]
    # Batch-fetch every referenced member and their existing MealMark row
    # for this date in two queries total, instead of a Member lookup
    # PLUS a MealMark lookup per member inside the loop below (previously
    # 2-4 queries per roster member on every single Save action).
    members_map = {m.id: m for m in Member.objects.filter(pk__in=member_ids)}
    if len(members_map) != len(set(member_ids)):
        raise Http404('No Member matches the given query.')
    existing_map = {mk.member_id: mk for mk in
                    MealMark.objects.filter(date=date, member_id__in=member_ids)}

    to_create, to_update, to_delete_ids = [], [], []
    now_dt = timezone.now()
    for mid_str, meal_data in marks.items():
        mid = int(mid_str)
        mem = members_map[mid]
        existing = existing_map.get(mid)

        # Start from whatever already exists on this row (other, previously
        # committed slots must be preserved untouched), then overwrite only
        # the slot(s) this request is responsible for.
        morning = existing.morning if existing else Decimal('0')
        lunch   = existing.lunch if existing else Decimal('0')
        dinner  = existing.dinner if existing else Decimal('0')
        if 'morning' in slots:
            morning = Decimal(str(meal_data.get('morning', 0)))
        if 'lunch' in slots:
            lunch = Decimal(str(meal_data.get('lunch', 0)))
        if 'dinner' in slots:
            dinner = Decimal(str(meal_data.get('dinner', 0)))

        total = morning + lunch + dinner
        if total > 0:
            if existing:
                existing.morning, existing.lunch, existing.dinner = morning, lunch, dinner
                existing.marked_by = actor
                existing.updated_at = now_dt   # bulk_update doesn't auto-populate auto_now fields
                to_update.append(existing)
            else:
                to_create.append(MealMark(member=mem, date=date, morning=morning,
                                           lunch=lunch, dinner=dinner, marked_by=actor))
            saved += 1
        elif existing:
            to_delete_ids.append(existing.pk)
            deleted += 1

    if to_create:
        MealMark.objects.bulk_create(to_create)
    if to_update:
        MealMark.objects.bulk_update(to_update, ['morning', 'lunch', 'dinner', 'marked_by', 'updated_at'])
    if to_delete_ids:
        MealMark.objects.filter(pk__in=to_delete_ids).delete()

    now = timezone.now()
    for s in slots:
        setattr(lock, f'{s}_locked', True)
        setattr(lock, f'{s}_by', actor)
        setattr(lock, f'{s}_at', now)
    lock.save()

    slot_names = ' & '.join(s.capitalize() for s in slots)
    if set(slots) == set(VALID_SLOTS):
        message = 'All Meal Saved'
    else:
        message = ' & '.join(f'{s.capitalize()} Saved' for s in slots)

    log_action(actor, f'Meal mark saved Day {day}/{month}/{year} [{slot_names}]',
               f'{saved} saved, {deleted} removed', request)
    return JsonResponse({
        'ok': True, 'message': message, 'slots_saved': slots,
        'is_fully_locked': lock.is_fully_locked(),
        'locked_slots': lock.locked_slots(),
    })


@login_required
def meal_save_member_draft(request):
    """Save one member's pending morning/lunch/dinner counts into the
    date-independent draft table. This does NOT touch MealMark and does
    NOT affect any system meal calculation — it only overwrites that
    member's draft row so it shows up pre-filled the next time any date
    is selected on the Meal Mark screen.

    Two ways in:
    - Manager / Sub-Manager with the meal_mark permission can save the
      draft for ANY member, any slot, any day (unchanged).
    - A plain Member can save ONLY their own draft, and PER MEAL TYPE:
      each of morning/lunch/dinner has its own independent time window
      (MemberInputSettings). A slot is also permanently closed for
      self-input the moment a Manager/Sub-Manager commits that specific
      slot for that specific day (DayMealLock) — e.g. if the Manager has
      already saved Morning for June 29, the member can still update
      Lunch and Dinner for June 29 (if their windows are open), but
      never Morning again for that day.

      Slots that aren't currently allowed are simply left untouched
      (preserved from whatever's already in the draft) rather than
      rejecting the whole request — this lets a member submit the
      "save" while only some of the three slots are actually editable,
      matching what the UI shows them. If NONE of the requested slots
      were actually allowed, the request is rejected outright so the
      member gets clear feedback instead of a silent no-op.
    """
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)
    actor = request.user.member
    data  = json.loads(request.body)
    mid   = data.get('member_id')
    mem   = get_object_or_404(Member, pk=int(mid))

    try:
        req_morning = Decimal(str(data.get('morning', 0)))
        req_lunch   = Decimal(str(data.get('lunch', 0)))
        req_dinner  = Decimal(str(data.get('dinner', 0)))
        if req_morning < 0 or req_lunch < 0 or req_dinner < 0:
            raise ValueError
    except Exception:
        return JsonResponse({'error': 'Invalid meal counts'}, status=400)

    is_privileged = actor.has_perm_code('meal_mark')
    if is_privileged:
        # Managers / permitted sub-managers can edit anyone, any slot, any day.
        morning, lunch, dinner = req_morning, req_lunch, req_dinner
        allowed_slots = ['morning', 'lunch', 'dinner']
    elif actor.role == Member.ROLE_MEMBER and actor.id == mem.id:
        sel_day   = data.get('selected_day')
        sel_month = data.get('selected_month')
        sel_year  = data.get('selected_year')
        sel_date = None
        if sel_day and sel_month and sel_year:
            try:
                sel_date = datetime.date(int(sel_year), int(sel_month), int(sel_day))
            except (TypeError, ValueError):
                sel_date = None

        settings_obj = MemberInputSettings.load(actor.mess)
        day_lock = DayMealLock.objects.filter(date=sel_date).first() if sel_date else None

        allowed_slots = []
        for slot in ('morning', 'lunch', 'dinner'):
            window_open = settings_obj.is_slot_open_now(slot)
            already_locked = bool(day_lock and getattr(day_lock, f'{slot}_locked'))
            if window_open and not already_locked:
                allowed_slots.append(slot)

        if not allowed_slots:
            return JsonResponse({
                'error': 'Member meal input is currently closed for this day (either the time window is closed, or it has already been confirmed by the Manager).'
            }, status=403)

        # Start from the existing draft so untouched/disallowed slots are
        # preserved exactly as-is, then overwrite only what's allowed.
        existing_draft = MealMarkDraft.objects.filter(member=mem).first()
        morning = req_morning if 'morning' in allowed_slots else (existing_draft.morning if existing_draft else Decimal('0'))
        lunch   = req_lunch   if 'lunch'   in allowed_slots else (existing_draft.lunch   if existing_draft else Decimal('0'))
        dinner  = req_dinner  if 'dinner'  in allowed_slots else (existing_draft.dinner  if existing_draft else Decimal('0'))
    else:
        return JsonResponse({'error': 'You do not have permission for this action.'}, status=403)

    draft, _ = MealMarkDraft.objects.update_or_create(
        member=mem,
        defaults={'morning': morning, 'lunch': lunch, 'dinner': dinner, 'updated_by': actor}
    )
    log_action(actor, f'Meal draft saved: {mem.name}',
               f'morning={morning} lunch={lunch} dinner={dinner} (slots: {",".join(allowed_slots)})', request)
    return JsonResponse({
        'ok': True, 'member_id': str(mem.id),
        'morning': float(draft.morning), 'lunch': float(draft.lunch), 'dinner': float(draft.dinner),
        'slots_saved': allowed_slots,
        'message': f'Draft saved for {mem.name}',
    })


@login_required
def day_config_list(request):
    month, year = get_active_my(request)
    member  = request.user.member
    configs = DayConfig.objects.filter(date__month=month, date__year=year).order_by('date')
    return render(request, 'meals/day_config.html', {
        'configs': configs, 'month': month, 'year': year,
        'month_name': calendar.month_name[month],
        'can_edit': member.has_perm_code('day_config'),
        'months': months_list(), 'years': years_list(year),
    })


@login_required
@require_perm('day_config')
def day_config_save(request):
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)
    data = json.loads(request.body)
    try:
        morning_multi = Decimal(str(data.get('morning_multiplier', '1.0')))
        lunch_multi   = Decimal(str(data.get('lunch_multiplier', '1.0')))
        dinner_multi  = Decimal(str(data.get('dinner_multiplier', '1.0')))
    except Exception:
        return JsonResponse({'error': 'Invalid multiplier value'}, status=400)
    label = data.get('label', '').strip()
    note  = data.get('note', '').strip()
    try:
        date = datetime.date.fromisoformat(data.get('date'))
    except Exception:
        return JsonResponse({'error': 'Invalid date'}, status=400)
    if morning_multi <= 0 or lunch_multi <= 0 or dinner_multi <= 0:
        return JsonResponse({'error': 'Multipliers must be > 0'}, status=400)
    dc, created = DayConfig.objects.update_or_create(
        mess=request.user.member.mess, date=date,
        defaults={'morning_multiplier': morning_multi, 'lunch_multiplier': lunch_multi,
                  'dinner_multiplier': dinner_multi, 'label': label,
                  'note': note, 'created_by': request.user.member}
    )
    log_action(request.user.member,
               f'Day config set: {date} M×{morning_multi} L×{lunch_multi} D×{dinner_multi}',
               label, request)
    return JsonResponse({
        'ok': True, 'id': dc.pk,
        'morning_multiplier': float(dc.morning_multiplier),
        'lunch_multiplier': float(dc.lunch_multiplier),
        'dinner_multiplier': float(dc.dinner_multiplier),
        'label': dc.label, 'created': created,
    })


@login_required
@require_perm('day_config')
def day_config_delete(request, pk):
    dc = get_object_or_404(DayConfig, pk=pk)
    dc.delete()
    log_action(request.user.member, f'Day config deleted: {dc.date}', request=request)
    return JsonResponse({'ok': True})


@login_required
@require_perm('member_input_control')
def member_input_settings_save(request):
    """Manager or a permitted Sub-Manager turns member self-input on/off
    and sets the daily time window — INDEPENDENTLY for morning, lunch,
    and dinner. This is a single global setting, not month-scoped.

    Expects: {
      morning: {enabled, start, end}, lunch: {...}, dinner: {...}
    }
    """
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)
    data = json.loads(request.body)

    settings_obj = MemberInputSettings.load(request.user.member.mess)
    parsed = {}
    for slot in ('morning', 'lunch', 'dinner'):
        slot_data = data.get(slot, {})
        try:
            start = datetime.time.fromisoformat(slot_data.get('start', '00:00'))
            end   = datetime.time.fromisoformat(slot_data.get('end', '23:59'))
        except Exception:
            return JsonResponse({'error': f'Invalid time format for {slot}'}, status=400)
        parsed[slot] = {
            'enabled': bool(slot_data.get('enabled', False)),
            'start': start, 'end': end,
        }

    for slot, vals in parsed.items():
        setattr(settings_obj, f'{slot}_enabled', vals['enabled'])
        setattr(settings_obj, f'{slot}_start', vals['start'])
        setattr(settings_obj, f'{slot}_end', vals['end'])
    settings_obj.updated_by = request.user.member
    settings_obj.save()

    summary = ', '.join(
        f"{slot}={'ON' if vals['enabled'] else 'OFF'} ({vals['start']}–{vals['end']})"
        for slot, vals in parsed.items()
    )
    log_action(request.user.member, f'Member input settings updated: {summary}', request=request)

    return JsonResponse({
        'ok': True,
        'morning': {'enabled': settings_obj.morning_enabled,
                    'start': settings_obj.morning_start.strftime('%H:%M'),
                    'end': settings_obj.morning_end.strftime('%H:%M'),
                    'open_now': settings_obj.is_morning_open_now()},
        'lunch': {'enabled': settings_obj.lunch_enabled,
                  'start': settings_obj.lunch_start.strftime('%H:%M'),
                  'end': settings_obj.lunch_end.strftime('%H:%M'),
                  'open_now': settings_obj.is_lunch_open_now()},
        'dinner': {'enabled': settings_obj.dinner_enabled,
                   'start': settings_obj.dinner_start.strftime('%H:%M'),
                   'end': settings_obj.dinner_end.strftime('%H:%M'),
                   'open_now': settings_obj.is_dinner_open_now()},
        'message': 'Member input settings saved.',
    })


@login_required
@require_perm('member_input_control')
def member_input_settings_page(request):
    """Dedicated settings page — 'Allow Member Meal Input' — where a
    Manager or permitted Sub-Manager turns member self-input on/off and
    sets the daily time window, independently per meal type. Moved out
    of the Meal Mark page into its own section so Meal Mark stays
    focused on marking meals. Actual saving is still handled by the
    existing member_input_settings_save endpoint (unchanged).
    """
    input_settings = MemberInputSettings.load(request.user.member.mess)
    input_settings_js = {
        slot: {
            'enabled': getattr(input_settings, f'{slot}_enabled'),
            'start': getattr(input_settings, f'{slot}_start').strftime('%H:%M'),
            'end': getattr(input_settings, f'{slot}_end').strftime('%H:%M'),
            'open_now': input_settings.is_slot_open_now(slot),
        }
        for slot in ('morning', 'lunch', 'dinner')
    }
    return render(request, 'meals/member_input_settings.html', {
        'input_settings': input_settings,
        'input_settings_js': json.dumps(input_settings_js),
    })


@login_required
def announcements(request):
    member   = request.user.member
    can_post = member.has_perm_code('announcements')
    items    = Announcement.objects.filter(is_active=True)
    return render(request, 'meals/announcements.html',
                  {'announcements': items, 'can_post': can_post})


@login_required
@require_perm('announcements')
def announcement_save(request):
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)
    data = json.loads(request.body)
    a = Announcement.objects.create(
        mess=request.user.member.mess,
        title=data.get('title', '').strip(),
        body=data.get('body', '').strip(),
        priority=data.get('priority', 'normal'),
        posted_by=request.user.member,
    )
    from accounts.models import Notification
    Notification.objects.create(
        mess=request.user.member.mess, broadcast=True, title=a.title, message=a.body,
        ntype='info', announcement=a,
    )
    log_action(request.user.member, f'Posted announcement: {a.title}', request=request)
    return JsonResponse({'ok': True, 'id': a.pk})


@login_required
@require_perm('announcements')
def announcement_delete(request, pk):
    a = get_object_or_404(Announcement, pk=pk)
    title = a.title
    a.is_active = False
    a.save()
    from accounts.models import Notification
    Notification.objects.filter(announcement=a).delete()
    log_action(request.user.member, f'Deleted announcement: {title}', request=request)
    return JsonResponse({'ok': True})


@login_required
@require_perm('meal_mark')
def update_meal_history(request):
    """Allow manager/sub-manager to look up a specific member + date and update their meal count."""
    members = Member.objects.filter(is_active=True).order_by('name')
    today   = datetime.date.today()
    actor   = request.user.member

    selected_member_id = request.GET.get('member_id') or request.POST.get('member_id')
    selected_date_str  = request.GET.get('date') or request.POST.get('date')

    selected_member = None
    selected_date   = None
    current_mark    = None

    if selected_member_id:
        selected_member = get_object_or_404(Member, pk=selected_member_id, is_active=True)

    if selected_date_str:
        try:
            selected_date = datetime.date.fromisoformat(selected_date_str)
        except ValueError:
            messages.error(request, 'Invalid date format.')

    if selected_member and selected_date:
        current_mark = MealMark.objects.select_related('marked_by').filter(
            member=selected_member, date=selected_date
        ).first()

    if request.method == 'POST' and 'save_meal' in request.POST:
        try:
            morning = Decimal(request.POST.get('morning', '0').strip() or '0')
            lunch   = Decimal(request.POST.get('lunch', '0').strip() or '0')
            dinner  = Decimal(request.POST.get('dinner', '0').strip() or '0')
            if morning < 0 or lunch < 0 or dinner < 0:
                raise ValueError
        except Exception:
            messages.error(request, 'Invalid meal counts.')
            return redirect(f'/meals/update-history/?member_id={selected_member_id}&date={selected_date_str}')

        total = morning + lunch + dinner
        if total > 0:
            obj, created = MealMark.objects.update_or_create(
                member=selected_member, date=selected_date,
                defaults={'morning': morning, 'lunch': lunch, 'dinner': dinner, 'marked_by': actor}
            )
            action = 'created' if created else 'updated'
            log_action(actor,
                f'Meal history {action}: {selected_member.name} on {selected_date}',
                f'morning={morning} lunch={lunch} dinner={dinner}', request)
            messages.success(
                request,
                f'✅ Meal for {selected_member.name} on {selected_date} set to {total} (M:{morning} L:{lunch} D:{dinner}).'
            )
        else:
            deleted, _ = MealMark.objects.filter(
                member=selected_member, date=selected_date
            ).delete()
            if deleted:
                log_action(actor,
                    f'Meal history deleted: {selected_member.name} on {selected_date}',
                    request=request)
                messages.success(
                    request,
                    f'✅ Meal record for {selected_member.name} on {selected_date} removed.'
                )
            else:
                messages.info(request, 'No existing record to remove.')
        return redirect(f'/meals/update-history/?member_id={selected_member_id}&date={selected_date_str}')

    # Build recent history for selected member (last 14 days with data)
    recent_history = []
    if selected_member:
        # Materialized + precomputed for the same reason as
        # finance.views.member_statement / accounts.views.my_statement:
        # the template displays {{ mk.eff }} rather than calling
        # {{ mk.effective_count }} directly, since Django templates call
        # methods with zero arguments and would otherwise re-trigger a
        # DayConfig + MealCountSettings query per row.
        recent_history = list(MealMark.objects.filter(
            member=selected_member
        ).select_related('marked_by', 'member__mess').order_by('-date')[:14])
        day_config_map, weights = MealMark.preload_calc_context(
            selected_member.mess, dates=(mk.date for mk in recent_history))
        for mk in recent_history:
            mk.eff = mk.effective_count(day_config_map, weights)

    return render(request, 'meals/update_meal_history.html', {
        'members': members,
        'today': today,
        'selected_member': selected_member,
        'selected_date': selected_date,
        'current_mark': current_mark,
        'recent_history': recent_history,
    })


@login_required
def monthly_settings(request):
    member = request.user.member
    if not member.is_manager():
        messages.error(request, 'Only Manager can change settings.')
        return redirect('dashboard')

    today = datetime.date.today()
    month, year = get_active_my(request)

    ms, _ = MonthlySettings.objects.get_or_create(
        mess=member.mess, month=month, year=year, defaults={'cooking_cost': 500}
    )

    if request.method == 'POST':
        action = request.POST.get('action', 'save')

        if action == 'set_active':
            new_month = int(request.POST.get('new_month', today.month))
            new_year  = int(request.POST.get('new_year',  today.year))
            set_active_my(request, new_month, new_year)
            log_action(member,
                       f'Active month changed to {calendar.month_name[new_month]} {new_year}',
                       request=request)
            messages.success(request,
                f'✅ Active month set to {calendar.month_name[new_month]} {new_year}. '
                f'All pages now show data for this month.')
            return redirect(f'/meals/settings/?month={new_month}&year={new_year}')

        elif action == 'save_count_settings':
            mcs = MealCountSettings.load(member.mess)
            try:
                mcs.morning_weight = Decimal(request.POST.get('morning_weight', '1.0'))
                mcs.lunch_weight   = Decimal(request.POST.get('lunch_weight', '1.0'))
                mcs.dinner_weight  = Decimal(request.POST.get('dinner_weight', '1.0'))
            except Exception:
                messages.error(request, 'Meal count weights must be valid numbers.')
                return redirect(f'/meals/settings/?month={month}&year={year}')

            if mcs.morning_weight < 0 or mcs.lunch_weight < 0 or mcs.dinner_weight < 0:
                messages.error(request, 'Meal count weights cannot be negative.')
                return redirect(f'/meals/settings/?month={month}&year={year}')

            mcs.updated_by = member
            mcs.save()
            log_action(member, 'Meal Count Settings saved',
                       f'morning={mcs.morning_weight} lunch={mcs.lunch_weight} dinner={mcs.dinner_weight}',
                       request)
            messages.success(request,
                f'✅ Meal count settings saved — Morning×{mcs.morning_weight}, '
                f'Lunch×{mcs.lunch_weight}, Dinner×{mcs.dinner_weight}. '
                f'This affects all past and future meal calculations immediately.')
            return redirect(f'/meals/settings/?month={month}&year={year}')

        else:
            ms.cooking_cost = Decimal(request.POST.get('cooking_cost', '500'))
            ms.notes        = request.POST.get('notes', '').strip()
            ms.save()
            log_action(member, f'Monthly settings saved {month}/{year}',
                       f'cooking_cost={ms.cooking_cost}', request)
            messages.success(request, f'Settings saved for {calendar.month_name[month]} {year}.')
            return redirect(f'/meals/settings/?month={month}&year={year}')

    existing = list(MonthlySettings.objects.values_list('year', flat=True).distinct())
    year_range = sorted(set(existing + [today.year - 1, today.year, today.year + 1]))

    active_month = request.session.get('active_month', today.month)
    active_year  = request.session.get('active_year',  today.year)
    mcs = MealCountSettings.load(member.mess)

    return render(request, 'meals/monthly_settings.html', {
        'ms': ms, 'month': month, 'year': year,
        'month_name': calendar.month_name[month],
        'months': months_list(),
        'years': year_range,
        'active_month': active_month,
        'active_year':  active_year,
        'active_month_name': calendar.month_name[active_month],
        'is_current_active': (month == active_month and year == active_year),
        'today': today,
        'mcs': mcs,
    })
