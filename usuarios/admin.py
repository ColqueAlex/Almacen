from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from .models import Usuario, Perfil, Permiso

class CustomUserAdmin(UserAdmin):
    list_display = ('username', 'nombre', 'apellido', 'dni', 'correo', 'id_perfil', 'activo')
    list_filter = ('activo', 'is_staff', 'is_superuser', 'id_perfil')
    
    fieldsets = (
        (None, {'fields': ('username', 'password')}),
        ('Información Personal', {'fields': ('nombre', 'apellido', 'dni', 'correo')}),
        ('Perfil y Permisos', {'fields': ('id_perfil', 'activo', 'is_staff', 'is_superuser', 'is_admin')}),
        ('Seguridad', {'fields': ('intentos_fallidos', 'bloqueo_hasta', 'debe_cambiar_clave', 'fecha_baja')}),
    )

    add_fieldsets = (
        (None, {
            'classes': ('wide',),
            'fields': ('username', 'dni', 'nombre', 'apellido', 'correo', 'password1', 'password2', 'id_perfil'),
        }),
    )
    search_fields = ('username', 'dni', 'nombre', 'apellido', 'correo')
    ordering = ('username',)

admin.site.register(Usuario, CustomUserAdmin)
admin.site.register(Perfil)
admin.site.register(Permiso)