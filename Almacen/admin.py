from django.contrib import admin
from Almacen.models import Permiso, Perfil, Productos, Proveedor, Usuario

# Register your models here.
admin.site.register(Productos)
admin.site.register(Proveedor)
admin.site.register(Perfil)
admin.site.register(Permiso)
admin.site.register(Usuario)
