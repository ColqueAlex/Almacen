from django.contrib import admin
from .models import Clientes, Ventas, DetallesVentas, Pago

admin.site.register(Clientes)
admin.site.register(Ventas)
admin.site.register(DetallesVentas)
admin.site.register(Pago)