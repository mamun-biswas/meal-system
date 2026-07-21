from django.contrib import admin
from .models import Deposit, Expense
@admin.register(Deposit)
class DepositAdmin(admin.ModelAdmin):
    list_display = ('member','amount','date','method','added_by')
    list_filter  = ('method','date')
    date_hierarchy= 'date'
    search_fields = ('member__name',)
@admin.register(Expense)
class ExpenseAdmin(admin.ModelAdmin):
    list_display = ('mess','date','amount','category','description','bought_by')
    list_filter  = ('category','date')
    date_hierarchy= 'date'
