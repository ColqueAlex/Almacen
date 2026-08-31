from django.db import models

# Create your models here.
class Productos(models.Model):

    nombre = models.CharField(max_length=30)
    precio = models.DecimalField(max_digits=10, decimal_places=2)
    stock = models.IntegerField()
    descripcion = models.CharField(max_length=200)

class Proveedor(models.Model):

    nombre = models.CharField(max_length=50)
    numero_telfeono = models.IntegerField()
    tipo_productos = models.CharField(max_length=100)

def __str__(self):
        return self.nombre