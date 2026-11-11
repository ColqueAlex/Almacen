from django.urls import path
from . import views

urlpatterns = [
    # Ventas
    path('ventas/', views.ventas, name='ventas'),
    path('ventas/registrar/', views.registrar_venta, name='registrar_venta'),
    path('ventas/cambiar-estado/<int:id_venta>/', views.cambiar_estado_venta, name='cambiar_estado_venta'),
    path('ventas/anular/<int:id_venta>/', views.anular_venta, name='anular_venta'),
    path('ventas/comprobante/<int:id_venta>/', views.generar_comprobante_pdf, name='generar_comprobante_pdf'),
    
    # Cuentas Corrientes
    path('cuentas-clientes/', views.cuentas_clientes, name='cuentas_clientes'),
    path('cuentas-clientes/pago/<int:id_cliente>/', views.registrar_pago_cliente, name='registrar_pago_cliente'),
    path('cuentas-clientes/crear/', views.crear_cliente, name='crear_cliente'),
]