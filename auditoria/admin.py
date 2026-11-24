from django.contrib import admin
from .models import Auditoria, Configuracion, Notificacion


@admin.register(Auditoria)
class AuditoriaAdmin(admin.ModelAdmin):
    """Solo lectura: un registro de auditoría no debe poder crearse, editarse ni borrarse a mano."""
    list_display = ('fecha_hora', 'usuario_nombre', 'categoria', 'accion', 'nivel', 'modelo', 'objeto_repr', 'ip')
    list_filter = ('categoria', 'accion', 'nivel', 'modelo')
    search_fields = ('usuario_nombre', 'descripcion', 'objeto_repr', 'ip')
    date_hierarchy = 'fecha_hora'

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Configuracion)
class ConfiguracionAdmin(admin.ModelAdmin):
    list_display = ('clave', 'valor', 'descripcion')
    list_editable = ('valor',)


@admin.register(Notificacion)
class NotificacionAdmin(admin.ModelAdmin):
    list_display = ('fecha', 'tipo', 'destino_perfil', 'leida', 'mensaje')
    list_filter = ('tipo', 'leida', 'destino_perfil')
