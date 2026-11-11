from django.contrib import admin
from .models import Productos, Proveedor, LotesProducto

admin.site.register(Productos)
admin.site.register(Proveedor)
admin.site.register(LotesProducto)