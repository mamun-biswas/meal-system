from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from accounts.models import Member
from finance.models import Deposit, Expense
from meals.models import MealMark
from meals.views import compute_month_stats
from django.db.models import Sum
from decimal import Decimal
import datetime

def get_my(request):
    today = datetime.date.today()
    return int(request.query_params.get('month',today.month)), int(request.query_params.get('year',today.year))

@api_view(['GET'])
@permission_classes([IsAuthenticated])
def api_stats(request):
    month, year = get_my(request)
    stats = compute_month_stats(month, year)
    return Response({
        'month': month, 'year': year,
        'total_members': Member.objects.filter(is_active=True).count(),
        'total_meals': float(stats['total_eff']),
        'total_expense': float(stats['total_exp']),
        'total_deposit': float(stats['total_dep']),
        'meal_rate': float(stats['meal_rate']),
        'fund_balance': float(stats['fund_balance']),
    })

@api_view(['GET'])
@permission_classes([IsAuthenticated])
def api_members(request):
    month, year = get_my(request)
    stats = compute_month_stats(month, year)
    data = []
    for s in stats['per_member']:
        m = s['member']
        data.append({'id':m.id,'name':m.name,'phone':m.phone,'room':m.room_number,
                     'role':m.role,'meals':float(s['eff_meals']),'deposit':float(s['deposit']),
                     'cost':float(s['cost']),'balance':float(s['balance'])})
    return Response({'members': data})

@api_view(['GET'])
@permission_classes([IsAuthenticated])
def api_meal_grid(request):
    month, year = get_my(request)
    marks = MealMark.objects.filter(date__month=month, date__year=year).select_related('member__mess')
    day_config_map, weights = MealMark.preload_calc_context(request.user.member.mess)
    data = [{'member':mk.member.name,'date':str(mk.date),
             'morning':float(mk.morning),'lunch':float(mk.lunch),'dinner':float(mk.dinner),
             'count':float(mk.count),'effective':float(mk.effective_count(day_config_map, weights))}
            for mk in marks]
    return Response({'marks': data})

@api_view(['GET'])
@permission_classes([IsAuthenticated])
def api_deposits(request):
    month, year = get_my(request)
    deps = Deposit.objects.filter(date__month=month, date__year=year).select_related('member')
    data = [{'id':d.id,'member':d.member.name,'amount':float(d.amount),'date':str(d.date),'method':d.method,'note':d.note} for d in deps]
    return Response({'deposits': data, 'total': float(deps.aggregate(t=Sum('amount'))['t'] or 0)})

@api_view(['GET'])
@permission_classes([IsAuthenticated])
def api_expenses(request):
    month, year = get_my(request)
    exps = Expense.objects.filter(date__month=month, date__year=year)
    data = [{'id':e.id,'date':str(e.date),'amount':float(e.amount),'category':e.get_category_display(),'description':e.description} for e in exps]
    return Response({'expenses': data, 'total': float(exps.aggregate(t=Sum('amount'))['t'] or 0)})
