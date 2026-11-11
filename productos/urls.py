from django.urls import path
from . import views

urlpatterns = [
    # CRUD Productos
    path('productos', views.consultar, name='consultar'),
    path('productos/guardar', views.guardar, name='guardar'),
    path('productos/estado/<int:id>', views.cambiar_estado, name='cambiar_estado'),
    path('productos/eliminar-seleccionados', views.eliminar_seleccionados, name='eliminar_seleccionados'),
    path('productos/detalle/<int:id>', views.detalle, name='detalle'),
    path('productos/editar', views.editar, name='editar'),
    
    # CRUD Proveedores
    path('proveedores/', views.listar_proveedores, name='listar_proveedores'),
    path('proveedores/crear/', views.crear_proveedor, name='crear_proveedor'),
    path('proveedores/editar/<int:id>/', views.editar_proveedor, name='editar_proveedor'),
    path('proveedores/baja/<int:id>/', views.baja_proveedor, name='baja_proveedor'),
    path('proveedores/alta/<int:id>/', views.alta_proveedor, name='alta_proveedor'),
]