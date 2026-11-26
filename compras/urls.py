from django.urls import path
from . import views

urlpatterns = [
    path('compras/', views.listar_compras, name='listar_compras'),
    path('compras/nueva/', views.registro_compras, name='registrar_compra'),
    path('compras/eliminar-antiguas/', views.eliminar_compras_antiguas, name='eliminar_compras_antiguas'),
    path('compras/anular/<int:id_compra>/', views.anular_compra, name='anular_compra'),
]