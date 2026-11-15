from django.urls import path
from . import views

urlpatterns = [
    path('reportes/rentabilidad/', views.reporte_rentabilidad, name='reporte_rentabilidad'),
    path('reportes/ranking-clientes/', views.reporte_ranking_clientes, name='reporte_ranking_clientes'),
    path('reportes/stock-valorizado/', views.reporte_stock_valorizado, name='reporte_stock_valorizado'),
]
