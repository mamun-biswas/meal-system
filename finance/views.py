from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.http import JsonResponse
from django.db.models import Sum
from django.core.paginator import Paginator
from accounts.models import Member
from accounts.views import log_action
from accounts.month_helpers import get_active_my, months_list, years_list
from .models import Deposit, Expense
from decimal import Decimal
import datetime, calendar, json


def perm_required(perm):
    def dec(fn):
        from functools import wraps
        @wraps(fn)
        def wrapper(req, *a, **kw):
            if not req.user.is_authenticated:
                return redirect('login')
            try:
                m = req.user.member
                if not m.has_perm_code(perm):
                    messages.error(req, 'Access denied.')
                    return redirect('dashboard')
            except Exception:
                return redirect('login')
            return fn(req, *a, **kw)
        return wrapper
    return dec


@login_required
def deposit_list(request):
    month, year = get_active_my(request)
    member  = request.user.member
    can_add = member.has_perm_code('deposit_entry')
    can_del = member.has_perm_code('delete_deposit')
    members = Member.objects.filter(is_active=True).order_by('name')
    deps    = Deposit.objects.filter(date__month=month, date__year=year
              ).select_related('member', 'added_by').order_by('-date', '-created_at')
    total   = deps.aggregate(t=Sum('amount'))['t'] or Decimal('0')
    page    = Paginator(deps, 30).get_page(request.GET.get('page', 1))
    return render(request, 'finance/deposits.html', {
        'page': page, 'total': total, 'members': members,
        'month': month, 'year': year, 'month_name': calendar.month_name[month],
        'can_add': can_add, 'can_del': can_del,
        'months': months_list(), 'years': years_list(year),
    })


@login_required
@perm_required('deposit_entry')
def deposit_add(request):
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)
    data   = json.loads(request.body)
    mid    = data.get('member_id')
    amount = Decimal(str(data.get('amount', 0)))
    if not mid or amount <= 0:
        return JsonResponse({'error': 'member_id and positive amount required'}, status=400)
    try:
        date = datetime.date.fromisoformat(data.get('date', str(datetime.date.today())))
    except Exception:
        return JsonResponse({'error': 'Invalid date'}, status=400)
    mem = get_object_or_404(Member, pk=int(mid))
    dep = Deposit.objects.create(
        member=mem, amount=amount, date=date,
        method=data.get('method', 'cash'),
        note=data.get('note', '').strip(),
        added_by=request.user.member,
    )
    log_action(request.user.member, f'Deposit added: {mem.name} ৳{amount}',
               f'Date:{date} Method:{data.get("method","cash")}', request)
    return JsonResponse({
        'ok': True, 'id': dep.pk, 'date': str(dep.date),
        'amount': float(dep.amount), 'method': dep.method,
        'note': dep.note, 'member': mem.name,
        'message': f'৳{amount} deposited for {mem.name}',
    })


@login_required
@perm_required('delete_deposit')
def deposit_delete(request, pk):
    dep  = get_object_or_404(Deposit, pk=pk)
    name, amt = dep.member.name, dep.amount
    dep.delete()
    log_action(request.user.member, f'Deposit deleted: {name} ৳{amt}', request=request)
    return JsonResponse({'ok': True, 'message': f'Deposit ৳{amt} removed for {name}'})


@login_required
def expense_list(request):
    month, year  = get_active_my(request)
    member       = request.user.member
    can_edit     = member.has_perm_code('bazar_entry')
    members      = Member.objects.filter(is_active=True).order_by('name')
    search       = request.GET.get('q', '').strip()
    cat_filter   = request.GET.get('cat', '')
    exps = Expense.objects.filter(date__month=month, date__year=year
           ).select_related('bought_by', 'added_by')
    if search:
        exps = exps.filter(description__icontains=search)
    if cat_filter:
        exps = exps.filter(category=cat_filter)
    exps       = exps.order_by('-date', '-created_at')
    total      = exps.aggregate(t=Sum('amount'))['t'] or Decimal('0')
    cat_totals = exps.values('category').annotate(t=Sum('amount')).order_by('-t')
    page       = Paginator(exps, 30).get_page(request.GET.get('page', 1))
    return render(request, 'finance/expenses.html', {
        'page': page, 'total': total, 'cat_totals': cat_totals,
        'members': members, 'categories': Expense.CATEGORY,
        'month': month, 'year': year, 'month_name': calendar.month_name[month],
        'can_edit': can_edit, 'months': months_list(), 'years': years_list(year),
        'search': search, 'cat_filter': cat_filter,
    })


@login_required
@perm_required('bazar_entry')
def expense_add(request):
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)
    data   = json.loads(request.body)
    amount = Decimal(str(data.get('amount', 0)))
    if amount <= 0:
        return JsonResponse({'error': 'Positive amount required'}, status=400)
    try:
        date = datetime.date.fromisoformat(data.get('date', str(datetime.date.today())))
    except Exception:
        return JsonResponse({'error': 'Invalid date'}, status=400)
    mid    = data.get('bought_by')
    bought = Member.objects.filter(pk=int(mid)).first() if mid else None
    exp    = Expense.objects.create(
        mess=request.user.member.mess,
        date=date, amount=amount,
        category=data.get('category', 'other'),
        description=data.get('description', '').strip(),
        bought_by=bought, added_by=request.user.member,
    )
    log_action(request.user.member,
               f'Expense added: ৳{amount} ({exp.get_category_display()})',
               f'Date:{date}', request)
    return JsonResponse({
        'ok': True, 'id': exp.pk, 'date': str(exp.date),
        'amount': float(exp.amount),
        'category': exp.get_category_display(),
        'description': exp.description,
        'bought_by': bought.name if bought else '',
        'message': f'Expense ৳{amount} added',
    })


@login_required
@perm_required('bazar_entry')
def expense_edit(request, pk):
    exp = get_object_or_404(Expense, pk=pk)
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)
    data = json.loads(request.body)
    if 'amount'      in data: exp.amount      = Decimal(str(data['amount']))
    if 'category'    in data: exp.category    = data['category']
    if 'description' in data: exp.description = data['description']
    if 'date'        in data:
        try:   exp.date = datetime.date.fromisoformat(data['date'])
        except Exception: return JsonResponse({'error': 'Invalid date'}, status=400)
    exp.save()
    log_action(request.user.member,
               f'Expense edited: ৳{exp.amount} ({exp.get_category_display()})',
               f'Date:{exp.date} Desc:{exp.description[:40]}', request)
    return JsonResponse({'ok': True, 'message': 'Updated'})


@login_required
@perm_required('bazar_entry')
def expense_delete(request, pk):
    exp = get_object_or_404(Expense, pk=pk)
    amt, cat, date = exp.amount, exp.get_category_display(), exp.date
    exp.delete()
    log_action(request.user.member,
               f'Expense deleted: ৳{amt} ({cat})',
               f'Date:{date}', request)
    return JsonResponse({'ok': True, 'message': f'Expense ৳{amt} deleted'})


@login_required
def member_statement(request, pk):
    target = get_object_or_404(Member, pk=pk)
    month, year = get_active_my(request)
    from meals.models import MealMark
    from meals.views import compute_month_stats
    from .models import MealClosing, ClosingRecord
    stats    = compute_month_stats(month, year)
    mem_stat = next((s for s in stats['per_member'] if s['member'].id == target.id), None)
    deps     = Deposit.objects.filter(member=target, date__month=month,
                                      date__year=year).order_by('date')
    marks    = MealMark.objects.filter(member=target, date__month=month,
                                       date__year=year).order_by('date')
    # Fetch finalised closing record for this member+month if it exists
    closing_record = None
    try:
        closing = MealClosing.objects.get(month=month, year=year, status=MealClosing.STATUS_CLOSED)
        closing_record = ClosingRecord.objects.select_related('verdict_by').get(
            closing=closing, member=target
        )
    except (MealClosing.DoesNotExist, ClosingRecord.DoesNotExist):
        pass
    return render(request, 'finance/statement.html', {
        'target': target, 'mem_stat': mem_stat, 'stats': stats,
        'deposits': deps, 'meal_marks': marks,
        'month': month, 'year': year, 'month_name': calendar.month_name[month],
        'months': months_list(), 'years': years_list(year),
        'closing_record': closing_record,
    })


# ===========================================================================
# MEAL CLOSING
# ===========================================================================

@login_required
def closing_list(request):
    """List all month closings. Managers/sub-managers with monthly_close perm."""
    member = request.user.member
    if not (member.is_manager() or member.has_perm_code('monthly_close')):
        messages.error(request, 'Access denied.')
        return redirect('dashboard')
    from .models import MealClosing
    closings = MealClosing.objects.all().select_related('closed_by')
    return render(request, 'finance/closing_list.html', {'closings': closings})


@login_required
def closing_detail(request, pk):
    """View a specific closing with per-member records and verdict controls."""
    member = request.user.member
    if not (member.is_manager() or member.has_perm_code('monthly_close')):
        messages.error(request, 'Access denied.')
        return redirect('dashboard')
    from .models import MealClosing, ClosingRecord
    closing = get_object_or_404(MealClosing, pk=pk)
    records = closing.records.select_related('member', 'verdict_by').order_by('member__name')
    return render(request, 'finance/closing_detail.html', {
        'closing': closing,
        'records': records,
    })


@login_required
def closing_create(request):
    """Snapshot the current active month into a new MealClosing."""
    member = request.user.member
    if not (member.is_manager() or member.has_perm_code('monthly_close')):
        messages.error(request, 'Access denied.')
        return redirect('dashboard')
    if request.method != 'POST':
        return redirect('closing_list')

    from .models import MealClosing, ClosingRecord
    from meals.views import compute_month_stats
    from accounts.month_helpers import get_active_my
    import django.utils.timezone as tz

    month, year = get_active_my(request)

    # Prevent duplicate closing for same month/year
    if MealClosing.objects.filter(month=month, year=year).exists():
        messages.warning(request, f'A closing for {calendar.month_name[month]} {year} already exists.')
        existing = MealClosing.objects.get(month=month, year=year)
        return redirect('closing_detail', pk=existing.pk)

    stats = compute_month_stats(month, year)

    closing = MealClosing.objects.create(
        mess=member.mess,
        month=month, year=year,
        total_exp=stats['total_exp'],
        total_dep=stats['total_dep'],
        total_eff=stats['total_eff'],
        meal_rate=stats['meal_rate'],
        cook_cost=stats['cook_cost'],
        fund_balance=stats['fund_balance'],
        status=MealClosing.STATUS_OPEN,
    )

    ClosingRecord.objects.bulk_create([
        ClosingRecord(
            closing=closing,
            member=s['member'],
            eff_meals=s['eff_meals'],
            meal_only_cost=s['meal_only_cost'],
            cook_cost=s['cook_cost'],
            total_cost=s['cost'],
            deposit=s['deposit'],
            balance=s['balance'],
            verdict=ClosingRecord.VERDICT_PENDING,
        )
        for s in stats['per_member']
    ])

    log_action(member,
               f'Meal closing created: {calendar.month_name[month]} {year}',
               f'Rate:৳{stats["meal_rate"]} Exp:৳{stats["total_exp"]} Members:{len(stats["per_member"])}',
               request)
    messages.success(request, f'Closing for {calendar.month_name[month]} {year} created.')
    return redirect('closing_detail', pk=closing.pk)


@login_required
def closing_verdict(request, record_pk):
    """AJAX: set verdict on a single ClosingRecord."""
    member = request.user.member
    if not (member.is_manager() or member.has_perm_code('monthly_close')):
        return JsonResponse({'ok': False, 'error': 'Access denied'}, status=403)
    if request.method != 'POST':
        return JsonResponse({'ok': False, 'error': 'POST required'}, status=405)

    from .models import ClosingRecord
    import django.utils.timezone as tz
    import json as _json

    data    = _json.loads(request.body)
    verdict = data.get('verdict', '').strip()
    note    = data.get('note', '').strip()

    valid = [v for v, _ in ClosingRecord.VERDICT_CHOICES]
    if verdict not in valid:
        return JsonResponse({'ok': False, 'error': 'Invalid verdict'}, status=400)

    rec = get_object_or_404(ClosingRecord, pk=record_pk)
    if rec.closing.is_closed():
        return JsonResponse({'ok': False, 'error': 'Closing is already finalised'}, status=400)

    rec.verdict    = verdict
    rec.verdict_note = note
    rec.verdict_by = member
    rec.verdict_at = tz.now()
    rec.save()

    log_action(member,
               f'Verdict set: {rec.member.name} → {verdict.upper()}',
               f'Closing:{rec.closing} Balance:৳{rec.balance} Note:{note[:60]}',
               request)
    return JsonResponse({'ok': True, 'verdict': verdict})


@login_required
def closing_finalise(request, pk):
    """Mark the entire closing as CLOSED (no more verdict changes)."""
    member = request.user.member
    if not member.is_manager():
        messages.error(request, 'Only the Manager can finalise a closing.')
        return redirect('closing_detail', pk=pk)
    if request.method != 'POST':
        return redirect('closing_detail', pk=pk)

    from .models import MealClosing
    import django.utils.timezone as tz

    closing = get_object_or_404(MealClosing, pk=pk)
    if closing.is_closed():
        messages.info(request, 'Already closed.')
        return redirect('closing_detail', pk=pk)

    closing.status    = MealClosing.STATUS_CLOSED
    closing.closed_by = member
    closing.closed_at = tz.now()
    closing.note      = request.POST.get('note', '').strip()
    closing.save()

    log_action(member,
               f'Meal closing FINALISED: {calendar.month_name[closing.month]} {closing.year}',
               request=request)
    messages.success(request, f'{calendar.month_name[closing.month]} {closing.year} closing is now finalised.')
    return redirect('closing_detail', pk=pk)


@login_required
def closing_reopen(request, pk):
    """Reopen a closed closing (Manager only)."""
    member = request.user.member
    if not member.is_manager():
        messages.error(request, 'Only the Manager can reopen a closing.')
        return redirect('closing_detail', pk=pk)
    if request.method != 'POST':
        return redirect('closing_detail', pk=pk)

    from .models import MealClosing
    closing = get_object_or_404(MealClosing, pk=pk)
    closing.status   = MealClosing.STATUS_OPEN
    closing.closed_by = None
    closing.closed_at = None
    closing.save()

    log_action(member,
               f'Meal closing REOPENED: {calendar.month_name[closing.month]} {closing.year}',
               request=request)
    messages.success(request, 'Closing reopened.')
    return redirect('closing_detail', pk=pk)


@login_required
def closing_delete(request, pk):
    """Permanently delete a closing (and its per-member records). Manager only."""
    member = request.user.member
    if not member.is_manager():
        messages.error(request, 'Only the Manager can delete a closing.')
        return redirect('closing_detail', pk=pk)
    if request.method != 'POST':
        return redirect('closing_detail', pk=pk)

    from .models import MealClosing

    closing = get_object_or_404(MealClosing, pk=pk)
    label = f'{calendar.month_name[closing.month]} {closing.year}'
    was_closed = closing.is_closed()
    closing.delete()  # cascades to ClosingRecord rows

    log_action(member,
               f'Meal closing DELETED: {label}',
               f'Status was: {"closed" if was_closed else "open"}',
               request)
    messages.success(request, f'Closing for {label} has been deleted.')
    return redirect('closing_list')
