from django.db.models import Q

from usuarios.utils import _tiene_perfil
from .almacenamiento import (
    UMBRAL_ALMACENAMIENTO_BD_BYTES, UMBRAL_ALMACENAMIENTO_BD_GB, tamano_base_datos_bytes,
)
from .models import Notificacion


def filtro_notificaciones_usuario(user):
    """Q con las notificaciones visibles para el usuario (su perfil; el superusuario ve también las de Administrador)."""
    filtro = Q(pk__in=[])
    if getattr(user, 'id_perfil_id', None):
        filtro |= Q(destino_perfil_id=user.id_perfil_id)
    if user.is_superuser:
        filtro |= Q(destino_perfil__nombre__iexact='Administrador')
    return filtro


def notificaciones(request):
    user = getattr(request, 'user', None)
    if not user or not user.is_authenticated:
        return {}
    cantidad = Notificacion.objects.filter(filtro_notificaciones_usuario(user), leida=False).count()
    return {'notificaciones_no_leidas': cantidad}


def almacenamiento(request):
    user = getattr(request, 'user', None)
    if not user or not _tiene_perfil(user, 'Administrador'):
        return {}

    tamano_bytes = tamano_base_datos_bytes()
    if tamano_bytes is None:
        return {}
    return {
        'almacenamiento_bd_gb': tamano_bytes / 1000 ** 3,
        'umbral_almacenamiento_bd_gb': UMBRAL_ALMACENAMIENTO_BD_GB,
        'alerta_almacenamiento_bd': tamano_bytes >= UMBRAL_ALMACENAMIENTO_BD_BYTES,
    }
