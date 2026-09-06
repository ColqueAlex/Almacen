from django.db import models
from django.core.validators import MinValueValidator

# Tabla de Producto 
class Productos(models.Model):

    nombre = models.CharField(max_length=100, unique=True)
    categoria = models.CharField(max_length=100)
    marca = models.CharField(max_length=100, default="")
    precio = models.DecimalField(max_digits=10, decimal_places=2, validators=[MinValueValidator(0)])
    fecha_vencimiento = models.DateField(null=True, blank=True)
    stock = models.IntegerField(validators=[MinValueValidator(0)])
    descripcion = models.CharField(max_length=200, blank=True)

class Proveedor(models.Model):

    nombre = models.CharField(max_length=100)
    numero_telfeono = models.IntegerField()
    tipo_productos = models.CharField(max_length=100)

def __str__(self):
        return self.nombre