"""
Caché del listado de perfiles (Administrador, Vendedor, ...).

Se usa en los formularios de alta y edición de usuarios. Se invalida
automáticamente cuando se crea, modifica o elimina un perfil.
"""
from django.core.cache import cache
from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

from .models import Perfil

CLAVE_PERFILES = 'usuarios:perfiles'
TIEMPO_CACHE = 60 * 60  # 1 hora


def perfiles_disponibles():
    perfiles = cache.get(CLAVE_PERFILES)
    if perfiles is None:
        perfiles = list(Perfil.objects.order_by('nombre'))
        cache.set(CLAVE_PERFILES, perfiles, TIEMPO_CACHE)
    return perfiles


@receiver([post_save, post_delete], sender=Perfil, dispatch_uid='usuarios_invalidar_perfiles')
def _invalidar_perfiles(sender, **kwargs):
    cache.delete(CLAVE_PERFILES)
