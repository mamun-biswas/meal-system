from functools import wraps
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth import authenticate, login, logout
from django.contrib import messages
from django.utils import timezone
from django.db.models import Count

from .models import Mess, Member, AdminMessage


def superadmin_required(view_func):
    """Only Django superusers (created via `createsuperuser`) may access
    the Global Admin panel — regular mess Members, even Managers, are
    never allowed in here."""
    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        if not (request.user.is_authenticated and request.user.is_superuser):
            messages.error(request, 'Admin access required.')
            return redirect('superadmin_login')
        return view_func(request, *args, **kwargs)
    return wrapper


def superadmin_login(request):
    if request.user.is_authenticated and request.user.is_superuser:
        return redirect('superadmin_dashboard')
    if request.method == 'POST':
        username = request.POST.get('username', '').strip()
        password = request.POST.get('password', '').strip()
        user = authenticate(request, username=username, password=password)
        if user is not None and user.is_superuser:
            login(request, user)
            return redirect('superadmin_dashboard')
        messages.error(request, 'Invalid admin credentials.')
        return render(request, 'superadmin/login.html', {'username': username})
    return render(request, 'superadmin/login.html')


def superadmin_logout(request):
    logout(request)
    return redirect('superadmin_login')


@superadmin_required
def superadmin_dashboard(request):
    q = request.GET.get('q', '').strip()
    messes = Mess.objects.all().order_by('-created_at')
    if q:
        messes = (messes.filter(name__icontains=q) | messes.filter(code__icontains=q)).distinct()
    messes = list(messes)
    mess_ids = [m.id for m in messes]

    # One query for every mess's manager (instead of one query per mess).
    # Member's default ordering (joined_date, name) is applied globally by
    # this queryset, so the first member encountered per mess_id below is
    # the same one `.first()` would have returned for that mess alone.
    managers_by_mess = {}
    for mem in Member.all_objects.filter(mess_id__in=mess_ids, role=Member.ROLE_MANAGER):
        managers_by_mess.setdefault(mem.mess_id, mem)

    # One query for every mess's member count (instead of one COUNT
    # query per mess).
    counts_by_mess = {
        row['mess_id']: row['n']
        for row in Member.all_objects.filter(mess_id__in=mess_ids)
                    .values('mess_id').annotate(n=Count('id'))
    }

    rows = []
    for mess in messes:
        manager = managers_by_mess.get(mess.id)
        rows.append({
            'mess': mess,
            'manager_name': manager.name if manager else '—',
            'manager_phone': manager.phone if manager else '—',
            'member_count': counts_by_mess.get(mess.id, 0),
        })

    pending_count = Mess.objects.filter(is_approved=False).count()
    total_count = Mess.objects.count()
    approved_count = total_count - pending_count

    return render(request, 'superadmin/dashboard.html', {
        'rows': rows,
        'q': q,
        'pending_count': pending_count,
        'total_count': total_count,
        'approved_count': approved_count,
    })


@superadmin_required
def superadmin_mess_approve(request, pk):
    mess = get_object_or_404(Mess, pk=pk)
    if request.method == 'POST':
        mess.is_approved = True
        mess.approved_at = timezone.now()
        mess.save()
        messages.success(request, f'"{mess.name}" has been approved.')
    return redirect('superadmin_dashboard')


@superadmin_required
def superadmin_mess_revoke(request, pk):
    """Undo an approval (e.g. approved by mistake)."""
    mess = get_object_or_404(Mess, pk=pk)
    if request.method == 'POST':
        mess.is_approved = False
        mess.approved_at = None
        mess.save()
        messages.success(request, f'Approval for "{mess.name}" has been revoked.')
    return redirect('superadmin_dashboard')


@superadmin_required
def superadmin_mess_delete(request, pk):
    mess = get_object_or_404(Mess, pk=pk)
    if request.method == 'POST':
        confirm_text = request.POST.get('confirm_name', '').strip()
        if confirm_text != mess.name:
            messages.error(request, 'Mess name did not match — deletion cancelled.')
            return redirect('superadmin_dashboard')
        name = mess.name
        mess.delete()
        messages.success(request, f'"{name}" and all of its data has been permanently deleted.')
    return redirect('superadmin_dashboard')


@superadmin_required
def superadmin_send_message(request, pk):
    mess = get_object_or_404(Mess, pk=pk)
    if request.method == 'POST':
        text = request.POST.get('message', '').strip()
        if text:
            AdminMessage.objects.create(mess=mess, message=text, sent_by=request.user.username)
            messages.success(request, f'Message sent to {mess.name}.')
        else:
            messages.error(request, 'Message cannot be empty.')
    return redirect('superadmin_dashboard')
