from django.urls import path
from . import views

urlpatterns = [
    path('caja/', views.gestion_caja, name='gestion_caja'),
    path('caja/abrir/', views.abrir_caja, name='abrir_caja'),
    path('caja/cerrar/', views.cerrar_caja, name='cerrar_caja'),
    path('caja/forzar-cierre/<int:id_caja>/', views.forzar_cierre_caja, name='forzar_cierre_caja'),
]