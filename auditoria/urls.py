from django.urls import path
from . import views

urlpatterns = [
    path('auditoria/', views.listar_auditoria, name='listar_auditoria'),
    path('notificaciones/', views.bandeja_notificaciones, name='bandeja_notificaciones'),
    path('notificaciones/<int:id_notificacion>/leida/', views.marcar_notificacion_leida, name='marcar_notificacion_leida'),
    path('notificaciones/marcar-todas/', views.marcar_todas_leidas, name='marcar_todas_leidas'),
]
