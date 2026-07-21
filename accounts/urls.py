from django.urls import path
from . import views
urlpatterns = [
    path('', views.dashboard, name='dashboard'),
    path('login/', views.login_view, name='login'),
    path('register-mess/', views.register_mess, name='register_mess'),
    path('pending-approval/', views.pending_approval, name='pending_approval'),
    path('admin-message/<int:pk>/read/', views.mark_admin_message_read, name='mark_admin_message_read'),
    path('logout/', views.logout_view, name='logout'),
    path('members/', views.member_list, name='member_list'),
    path('members/add/', views.member_add, name='member_add'),
    path('members/<int:pk>/edit/', views.member_edit, name='member_edit'),
    path('members/<int:pk>/deactivate/', views.member_deactivate, name='member_deactivate'),
    path('members/<int:pk>/permissions/', views.member_permissions, name='member_permissions'),
    path('profile/', views.profile_view, name='profile'),
    path('my-statement/', views.my_statement, name='my_statement'),
    path('notifications/', views.notifications_view, name='notifications'),
    path('activity-log/', views.activity_log_view, name='activity_log'),
]
