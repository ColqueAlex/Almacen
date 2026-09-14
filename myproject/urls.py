from django.contrib import admin
from django.urls import path
from Almacen import views
from django.contrib.auth import views as auth_views

urlpatterns = [
    path('', views.home, name='inicio'),

    path('login/', auth_views.LoginView.as_view(
        template_name='login.html',
        redirect_authenticated_user=True
    ), name='login'),
    path('logout/', auth_views.LogoutView.as_view(next_page='login'), name='logout'),

    path('admin/', admin.site.urls),
    path('productos', views.consultar, name='consultar' ),
    path('productos/guardar', views.guardar, name='guardar'),
    path('productos/estado/<int:id>', views.cambiar_estado, name='cambiar_estado'),
    path('productos/eliminar-seleccionados', views.eliminar_seleccionados, name='eliminar_seleccionados'),
    path('productos/detalle/<int:id>', views.detalle, name='detalle'),
    path('productos/editar', views.editar, name='editar'),
    path('usuarios/', views.listar_usuarios, name='listar_usuarios'),
    path('usuarios/', views.listar_usuarios, name='listar_usuarios'),
    path('usuarios/crear/', views.crear_usuario, name='crear_usuario'),
    path('usuarios/editar/<int:dni>/', views.editar_usuario, name='editar_usuario'),
    path('usuarios/baja/<int:dni>/', views.baja_usuario, name='baja_usuario'),
    path('usuarios/restablecer/<int:dni>/', views.restablecer_clave, name='restablecer_clave'),
    path('cambiar-clave/', views.cambiar_clave, name='cambiar_clave'),
    path('usuarios/alta/<str:dni>/', views.alta_usuario, name='alta_usuario'),
]
