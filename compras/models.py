from django.db import models
from django.core.validators import MinValueValidator
from productos.models import Proveedor, Productos
from usuarios.models import Usuario

class Compras(models.Model):
    fecha = models.DateField(auto_now_add=True)
    hora = models.TimeField(auto_now_add=True)
    total = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    estado = models.CharField(max_length=20, default='Confirmado')
    id_proveedor = models.ForeignKey(Proveedor, on_delete=models.PROTECT, null=True, blank=True)
    id_usuario = models.ForeignKey(Usuario, on_delete=models.PROTECT)
    motivo_anulacion = models.TextField(blank=True, null=True)
    fecha_anulacion = models.DateTimeField(blank=True, null=True)
    usuario_anulacion = models.ForeignKey(Usuario, on_delete=models.SET_NULL, null=True, blank=True, related_name='compras_anuladas')
    def __str__(self):
        prov = self.id_proveedor.nombre if self.id_proveedor else "Sin proveedor"
        return f"Compra #{self.id} - Proveedor: {prov} - ${self.total}"

class DetallesCompra(models.Model):
    id_compra = models.ForeignKey(Compras, on_delete=models.CASCADE, related_name='detalles')
    id_producto = models.ForeignKey(Productos, on_delete=models.PROTECT)
    lote = models.CharField(max_length=50)
    fecha_vencimiento = models.DateField(null=True, blank=True)
    cantidad = models.IntegerField(validators=[MinValueValidator(1)])
    precio_unitario_compra = models.DecimalField(max_digits=10, decimal_places=2)
    subtotal = models.DecimalField(max_digits=10, decimal_places=2)
    def __str__(self): return f"Detalle #{self.id} Compra #{self.id_compra.id} - {self.id_producto.nombre}"