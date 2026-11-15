from django.contrib import admin
from .models import Auditoria, Configuracion, Notificacion

admin.site.register(Auditoria)


@admin.register(Configuracion)
class ConfiguracionAdmin(admin.ModelAdmin):
    list_display = ('clave', 'valor', 'descripcion')
    list_editable = ('valor',)


@admin.register(Notificacion)
class NotificacionAdmin(admin.ModelAdmin):
    list_display = ('fecha', 'tipo', 'destino_perfil', 'leida', 'mensaje')
    list_filter = ('tipo', 'leida', 'destino_perfil')
