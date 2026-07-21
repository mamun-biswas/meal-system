from django.urls import path
from . import views

urlpatterns = [
    path('deposits/', views.deposit_list, name='deposit_list'),
    path('deposits/add/', views.deposit_add, name='deposit_add'),
    path('deposits/<int:pk>/delete/', views.deposit_delete, name='deposit_delete'),
    path('expenses/', views.expense_list, name='expense_list'),
    path('expenses/add/', views.expense_add, name='expense_add'),
    path('expenses/<int:pk>/edit/', views.expense_edit, name='expense_edit'),
    path('expenses/<int:pk>/delete/', views.expense_delete, name='expense_delete'),
    path('statement/<int:pk>/', views.member_statement, name='member_statement'),
    # Meal Closing
    path('closing/', views.closing_list, name='closing_list'),
    path('closing/create/', views.closing_create, name='closing_create'),
    path('closing/<int:pk>/', views.closing_detail, name='closing_detail'),
    path('closing/<int:pk>/finalise/', views.closing_finalise, name='closing_finalise'),
    path('closing/<int:pk>/reopen/', views.closing_reopen, name='closing_reopen'),
    path('closing/<int:pk>/delete/', views.closing_delete, name='closing_delete'),
    path('closing/verdict/<int:record_pk>/', views.closing_verdict, name='closing_verdict'),
]
