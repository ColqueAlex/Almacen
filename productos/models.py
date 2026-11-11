from django.db import models
from django.core.validators import MinValueValidator
from django.core.exceptions import ValidationError
from django.db.models import Sum

def validar_categoria(value):
    if any(char.isdigit() for char in value):
        raise ValidationError('La categoría no puede contener números.')

class Proveedor(models.Model):
    nombre = models.CharField(max_length=30)
    numero_telefono = models.CharField(max_length=20)
    tipo_productos = models.CharField(max_length=30)
    activo = models.BooleanField(default=True)
    def __str__(self): return self.nombre

class Productos(models.Model):
    ESTADO_CHOICES = [('activo', 'Activo'), ('inactivo', 'Inactivo')]
    nombre = models.CharField(max_length=40)
    categoria = models.CharField(max_length=40, validators=[validar_categoria])
    marca = models.CharField(max_length=400, default='Sin marca')
    precio = models.DecimalField(max_digits=10, decimal_places=2, validators=[MinValueValidator(0.01)])
    estado = models.CharField(max_length=20, choices=ESTADO_CHOICES, default='activo')
    proveedores = models.ManyToManyField(Proveedor, blank=True, related_name='productos_que_suministra')

    def __str__(self): return f"{self.nombre} ({self.marca}) - ${self.precio}"
    @property
    def stock(self):
        res = self.lotes.filter(activo=True).aggregate(total=Sum('stock_actual'))
        return res['total'] if res['total'] is not None else 0

class LotesProducto(models.Model):
    id_producto = models.ForeignKey(Productos, on_delete=models.CASCADE, related_name='lotes')
    lote = models.CharField(max_length=50)
    fecha_vencimiento = models.DateField(null=True, blank=True)
    stock_actual = models.IntegerField(default=0)
    precio_costo = models.DecimalField(max_digits=10, decimal_places=2)
    activo = models.BooleanField(default=True)
    class Meta: unique_together = ('id_producto', 'lote')
    def __str__(self): return f"{self.id_producto.nombre} - Lote: {self.lote} - Stock: {self.stock_actual}"