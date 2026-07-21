from django.contrib import admin
from .models import Member, SubManagerPermission, ActivityLog, Notification, Mess, AdminMessage

@admin.register(Mess)
class MessAdmin(admin.ModelAdmin):
    list_display = ('name', 'code', 'is_approved', 'is_active', 'created_at')
    list_filter = ('is_approved', 'is_active')
    search_fields = ('name', 'code')

@admin.register(AdminMessage)
class AdminMessageAdmin(admin.ModelAdmin):
    list_display = ('mess', 'message', 'sent_by', 'is_read', 'created_at')
    list_filter = ('is_read',)

@admin.register(Member)
class MemberAdmin(admin.ModelAdmin):
    list_display = ('name','mess','phone','role','room_number','is_active','joined_date')
    list_filter  = ('mess','role','is_active')
    search_fields= ('name','phone')

@admin.register(SubManagerPermission)
class PermAdmin(admin.ModelAdmin):
    list_display = ('member','codename','granted')

@admin.register(ActivityLog)
class LogAdmin(admin.ModelAdmin):
    list_display = ('mess','member','action','timestamp','ip')
    readonly_fields = ('mess','member','action','detail','ip','timestamp')

admin.site.register(Notification)
