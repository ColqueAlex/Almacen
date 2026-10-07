from django.contrib import admin
from django.urls import path
from Almacen import views
from django.contrib.auth import views as auth_views

urlpatterns = [
    path('', views.home, name='inicio'),

    path('login/', views.login_usuario, name='login'),
    path('logout/', auth_views.LogoutView.as_view(next_page='login'), name='logout'),

    path('admin/', admin.site.urls),
    path('productos', views.consultar, name='consultar' ),
    path('productos/guardar', views.guardar, name='guardar'),
    path('productos/estado/<int:id>', views.cambiar_estado, name='cambiar_estado'),
    path('productos/eliminar-seleccionados', views.eliminar_seleccionados, name='eliminar_seleccionados'),
    path('productos/detalle/<int:id>', views.detalle, name='detalle'),
    path('productos/editar', views.editar, name='editar'),
    path('ventas/', views.ventas, name='ventas'),
    path('ventas/registrar/', views.registrar_venta, name='registrar_venta'),
    path('ventas/cambiar-estado/<int:id_venta>/', views.cambiar_estado_venta, name='cambiar_estado_venta'),
    path('usuarios/', views.listar_usuarios, name='listar_usuarios'),
    path('usuarios/crear/', views.crear_usuario, name='crear_usuario'),
    path('usuarios/editar/<int:dni>/', views.editar_usuario, name='editar_usuario'),
    path('usuarios/baja/<int:dni>/', views.baja_usuario, name='baja_usuario'),
    path('usuarios/restablecer/<int:dni>/', views.restablecer_clave, name='restablecer_clave'),
    path('cambiar-clave/', views.cambiar_clave, name='cambiar_clave'),
    path('usuarios/alta/<int:dni>/', views.alta_usuario, name='alta_usuario'),
    path('proveedores/', views.listar_proveedores, name='listar_proveedores'),
    path('proveedores/crear/', views.crear_proveedor, name='crear_proveedor'),
    path('proveedores/editar/<int:id>/', views.editar_proveedor, name='editar_proveedor'),
    path('proveedores/baja/<int:id>/', views.baja_proveedor, name='baja_proveedor'),
    path('proveedores/alta/<int:id>/', views.alta_proveedor, name='alta_proveedor'),
    path('compras/', views.listar_compras, name='listar_compras'),
    path('compras/nueva/', views.registro_compras, name='registrar_compra'),
    path('ventas/anular/<int:id_venta>/', views.anular_venta, name='anular_venta'),
    path('ventas/comprobante/<int:id_venta>/', views.generar_comprobante_pdf, name='generar_comprobante_pdf'),
    path('cuentas-clientes/', views.cuentas_clientes, name='cuentas_clientes'),
    path('cuentas-clientes/pago/<int:id_cliente>/', views.registrar_pago_cliente, name='registrar_pago_cliente'),
    path('cuentas-clientes/crear/', views.crear_cliente, name='crear_cliente'),
    path('caja/', views.gestion_caja, name='gestion_caja'),
    path('caja/abrir/', views.abrir_caja, name='abrir_caja'),
    path('caja/cerrar/', views.cerrar_caja, name='cerrar_caja'),
]
