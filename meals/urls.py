from django.urls import path
from . import views
urlpatterns = [
    path('', views.meal_mark, name='meal_mark'),
    path('monthly-record/', views.monthly_meal_record, name='monthly_meal_record'),
    path('save-day/', views.meal_save_day, name='meal_save_day'),
    path('save-draft/', views.meal_save_member_draft, name='meal_save_member_draft'),
    path('day-config/', views.day_config_list, name='day_config'),
    path('day-config/save/', views.day_config_save, name='day_config_save'),
    path('day-config/<int:pk>/delete/', views.day_config_delete, name='day_config_delete'),
    path('member-input-settings/save/', views.member_input_settings_save, name='member_input_settings_save'),
    path('member-input/', views.member_input_settings_page, name='member_input_settings_page'),
    path('announcements/', views.announcements, name='announcements'),
    path('announcements/save/', views.announcement_save, name='announcement_save'),
    path('announcements/<int:pk>/delete/', views.announcement_delete, name='announcement_delete'),
    path('settings/', views.monthly_settings, name='monthly_settings'),
    path('update-history/', views.update_meal_history, name='update_meal_history'),
]
