from django.db import models
from django.core.validators import MinValueValidator
from django.core.exceptions import ValidationError
from django.db.models import OuterRef, Q, Subquery, Sum
from django.db.models.functions import Coalesce
from django.utils import timezone
from usuarios.utils import validar_telefono

def validar_categoria(value):
    if any(char.isdigit() for char in value):
        raise ValidationError('La categoría no puede contener números.')

class Proveedor(models.Model):
    nombre = models.CharField(max_length=30)
    numero_telefono = models.CharField(max_length=20, unique=True, validators=[validar_telefono])
    tipo_productos = models.CharField(max_length=30)
    activo = models.BooleanField(default=True)
    def __str__(self): return self.nombre

def filtro_lotes_vigentes(prefijo='', hoy=None):
    """Q de lotes activos y no vencidos. `prefijo` permite usarlo desde otra tabla (p. ej. 'lotes__')."""
    hoy = hoy or timezone.localdate()
    return Q(**{f'{prefijo}activo': True}) & (
        Q(**{f'{prefijo}fecha_vencimiento__isnull': True}) | Q(**{f'{prefijo}fecha_vencimiento__gte': hoy})
    )


class ProductosQuerySet(models.QuerySet):
    def con_stock(self):
        """
        Anota `stock_vigente` (suma de los lotes vigentes) calculado en la base de datos.

        Se usa una subconsulta correlacionada en lugar de Sum('lotes__...') con JOIN
        para poder combinarla con otras anotaciones (p. ej. total vendido) sin que
        las sumas se multipliquen entre sí, y para poder filtrar por el resultado.
        """
        stock_lotes = (
            LotesProducto.objects.filter(filtro_lotes_vigentes(), id_producto=OuterRef('pk'))
            .order_by()
            .values('id_producto')
            .annotate(total=Sum('stock_actual'))
            .values('total')
        )
        return self.annotate(
            stock_vigente=Coalesce(Subquery(stock_lotes, output_field=models.IntegerField()), 0)
        )


class Productos(models.Model):
    ESTADO_CHOICES = [('activo', 'Activo'), ('inactivo', 'Inactivo')]
    nombre = models.CharField(max_length=40, db_index=True)
    categoria = models.CharField(max_length=40, validators=[validar_categoria], db_index=True)
    marca = models.CharField(max_length=400, default='Sin marca')
    precio = models.DecimalField(max_digits=10, decimal_places=2, validators=[MinValueValidator(0.01)])
    estado = models.CharField(max_length=20, choices=ESTADO_CHOICES, default='activo', db_index=True)
    proveedores = models.ManyToManyField(Proveedor, blank=True, related_name='productos_que_suministra')

    objects = ProductosQuerySet.as_manager()

    def __str__(self): return f"{self.nombre} ({self.marca}) - ${self.precio}"

    def lotes_vigentes(self):
        return self.lotes.filter(filtro_lotes_vigentes())

    @property
    def stock(self):
        # Si el queryset vino de `con_stock()`, se usa el valor ya calculado y
        # no se hace una consulta por producto.
        if 'stock_vigente' in self.__dict__:
            return self.stock_vigente
        res = self.lotes_vigentes().aggregate(total=Sum('stock_actual'))
        return res['total'] if res['total'] is not None else 0

class LotesProducto(models.Model):
    id_producto = models.ForeignKey(Productos, on_delete=models.CASCADE, related_name='lotes')
    lote = models.CharField(max_length=50)
    fecha_vencimiento = models.DateField(null=True, blank=True, db_index=True)
    stock_actual = models.IntegerField(default=0)
    precio_costo = models.DecimalField(max_digits=10, decimal_places=2)
    activo = models.BooleanField(default=True)
    class Meta: unique_together = ('id_producto', 'lote')
    def __str__(self): return f"{self.id_producto.nombre} - Lote: {self.lote} - Stock: {self.stock_actual}"