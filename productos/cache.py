"""
Caché de consultas que se repiten mucho y cambian poco.

Las categorías se muestran en los filtros de inventario, POS y reportes. Se
guardan en caché y se invalidan automáticamente cuando se crea, modifica o
elimina un producto (signals registrados en ProductosConfig.ready).
"""
from django.core.cache import cache
from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

from .models import Productos

CLAVE_CATEGORIAS = 'productos:categorias:{alcance}'
TIEMPO_CACHE = 60 * 60  # 1 hora (igual se invalida ante cualquier cambio)


def categorias_productos(solo_activos=False):
    """Lista ordenada de categorías distintas (opcionalmente solo de productos activos)."""
    clave = CLAVE_CATEGORIAS.format(alcance='activos' if solo_activos else 'todos')
    categorias = cache.get(clave)
    if categorias is None:
        qs = Productos.objects.exclude(categoria='')
        if solo_activos:
            qs = qs.filter(estado='activo')
        categorias = list(qs.order_by('categoria').values_list('categoria', flat=True).distinct())
        cache.set(clave, categorias, TIEMPO_CACHE)
    return categorias


def invalidar_categorias():
    cache.delete_many([CLAVE_CATEGORIAS.format(alcance=a) for a in ('activos', 'todos')])


@receiver([post_save, post_delete], sender=Productos, dispatch_uid='productos_invalidar_categorias')
def _invalidar_categorias(sender, **kwargs):
    invalidar_categorias()
