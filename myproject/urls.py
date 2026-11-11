from django.contrib import admin
from django.urls import path, include

urlpatterns = [
    path('admin/', admin.site.urls),
    
    path('', include('usuarios.urls')),
    path('', include('productos.urls')),
    path('', include('ventas.urls')),
    path('', include('compras.urls')),
    path('', include('caja.urls')),
    path('', include('auditoria.urls')),
]