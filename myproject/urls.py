
from django.contrib import admin
from django.urls import path
from Almacen import views

urlpatterns = [
    path('admin/', admin.site.urls),
    path('', views.home, name='inicio' ),
    path('productos', views.consultar, name='consultar' ),
    path('productos/guardar', views.guardar, name='guardar'),
    path('productos/eliminar/<int:id>', views.eliminar, name='eliminar'),
    path('productos/detalle/<int:id>', views.detalle, name='detalle'),
    path('productos/editar', views.editar, name='editar'),
]
