from django.urls import path
from . import superadmin_views as views

urlpatterns = [
    path('login/', views.superadmin_login, name='superadmin_login'),
    path('logout/', views.superadmin_logout, name='superadmin_logout'),
    path('', views.superadmin_dashboard, name='superadmin_dashboard'),
    path('mess/<int:pk>/approve/', views.superadmin_mess_approve, name='superadmin_mess_approve'),
    path('mess/<int:pk>/revoke/', views.superadmin_mess_revoke, name='superadmin_mess_revoke'),
    path('mess/<int:pk>/delete/', views.superadmin_mess_delete, name='superadmin_mess_delete'),
    path('mess/<int:pk>/message/', views.superadmin_send_message, name='superadmin_send_message'),
]
