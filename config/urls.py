from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static

urlpatterns = [
    path('admin/', admin.site.urls),
    path('superadmin/', include('accounts.superadmin_urls')),
    path('', include('accounts.urls')),
    path('meals/', include('meals.urls')),
    path('finance/', include('finance.urls')),
    path('reports/', include('reports.urls')),
    path('api/', include('api.urls')),
] + static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
