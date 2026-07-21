from django.contrib import admin
from .models import MealMark, MealMarkDraft, MemberInputSettings, DayMealLock, DayConfig, MonthlySettings, Announcement, MealCountSettings

@admin.register(MealMark)
class MealMarkAdmin(admin.ModelAdmin):
    list_display = ('member','date','count','marked_by')
    list_filter  = ('date',)
    date_hierarchy = 'date'

@admin.register(DayMealLock)
class DayMealLockAdmin(admin.ModelAdmin):
    list_display = ('mess','date','morning_locked','lunch_locked','dinner_locked')
    list_filter   = ('morning_locked','lunch_locked','dinner_locked')
    date_hierarchy = 'date'

@admin.register(MealMarkDraft)
class MealMarkDraftAdmin(admin.ModelAdmin):
    list_display = ('member','morning','lunch','dinner','updated_by','updated_at')

@admin.register(MemberInputSettings)
class MemberInputSettingsAdmin(admin.ModelAdmin):
    list_display = ('mess', 'morning_enabled', 'morning_start', 'morning_end',
                     'lunch_enabled', 'lunch_start', 'lunch_end',
                     'dinner_enabled', 'dinner_start', 'dinner_end',
                     'updated_by', 'updated_at')

@admin.register(DayConfig)
class DayConfigAdmin(admin.ModelAdmin):
    list_display = ('mess','date','morning_multiplier','lunch_multiplier','dinner_multiplier','label')

@admin.register(MealCountSettings)
class MealCountSettingsAdmin(admin.ModelAdmin):
    list_display = ('mess', 'morning_weight', 'lunch_weight', 'dinner_weight', 'updated_by', 'updated_at')

admin.site.register(MonthlySettings)
admin.site.register(Announcement)
