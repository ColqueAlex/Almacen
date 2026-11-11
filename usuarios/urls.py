from django.urls import path
from django.contrib.auth import views as auth_views
from . import views

urlpatterns = [
    path('', views.home, name='inicio'),
    path('login/', views.login_usuario, name='login'),
    path('logout/', auth_views.LogoutView.as_view(next_page='login'), name='logout'),
    path('cambiar-clave/', views.cambiar_clave, name='cambiar_clave'),
    
    # CRUD Usuarios
    path('usuarios/', views.listar_usuarios, name='listar_usuarios'),
    path('usuarios/crear/', views.crear_usuario, name='crear_usuario'),
    path('usuarios/editar/<str:dni>/', views.editar_usuario, name='editar_usuario'),
    path('usuarios/baja/<str:dni>/', views.baja_usuario, name='baja_usuario'),
    path('usuarios/restablecer/<str:dni>/', views.restablecer_clave, name='restablecer_clave'),
    path('usuarios/alta/<str:dni>/', views.alta_usuario, name='alta_usuario'),
]