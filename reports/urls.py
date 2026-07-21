from django.urls import path
from . import views

urlpatterns = [
    path('monthly/',         views.report_monthly,  name='report_monthly'),
    path('daily/',           views.report_daily,    name='report_daily'),
    path('analytics/',       views.analytics,       name='analytics'),

    # legacy CSV
    path('export/csv/',      views.export_csv,      name='export_csv'),

    # new exports
    path('export/excel/en/', views.export_excel_en, name='export_excel_en'),
    path('export/excel/bn/', views.export_excel_bn, name='export_excel_bn'),
    path('export/pdf/en/',   views.export_pdf_en,   name='export_pdf_en'),
    path('export/pdf/en/preview/', views.export_pdf_en_preview, name='export_pdf_en_preview'),
    path('export/pdf/bn/',   views.export_pdf_bn,   name='export_pdf_bn'),

    # keep old excel url for backwards compat
    path('export/excel/',    views.export_excel_en, name='export_excel'),
]
