from django.urls import path
from . import views

urlpatterns = [
    path('auditoria/', views.listar_auditoria, name='listar_auditoria'),
    path('auditoria/exportar/', views.exportar_auditoria, name='exportar_auditoria'),
    path('auditoria/antigua/exportar/', views.exportar_auditoria_antigua, name='exportar_auditoria_antigua'),
    path('auditoria/antigua/eliminar/', views.eliminar_auditoria_antigua, name='eliminar_auditoria_antigua'),
    path('notificaciones/', views.bandeja_notificaciones, name='bandeja_notificaciones'),
    path('notificaciones/<int:id_notificacion>/leida/', views.marcar_notificacion_leida, name='marcar_notificacion_leida'),
    path('notificaciones/marcar-todas/', views.marcar_todas_leidas, name='marcar_todas_leidas'),
    path('analitica/', views.panel_analitica, name='panel_analitica'),
]
