from django.urls import path
from . import views
urlpatterns = [
    path('stats/', views.api_stats, name='api_stats'),
    path('members/', views.api_members, name='api_members'),
    path('meal-grid/', views.api_meal_grid, name='api_meal_grid'),
    path('deposits/', views.api_deposits, name='api_deposits'),
    path('expenses/', views.api_expenses, name='api_expenses'),
]
