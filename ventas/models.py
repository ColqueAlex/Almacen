from django.db import models
from django.core.validators import MinValueValidator
from productos.models import Productos, LotesProducto
from usuarios.models import Usuario
from caja.models import Caja

class Clientes(models.Model):
    id_clientes = models.AutoField(primary_key=True, db_column='ID_Clientes')
    nombre = models.CharField(max_length=30)
    apellido = models.CharField(max_length=30, blank=True, null=True)
    telefono = models.CharField(max_length=20, blank=True, null=True)
    saldo = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    def __str__(self): return f"{self.nombre} {self.apellido or ''} - Deuda: ${self.saldo}"

class Ventas(models.Model):
    METODO_PAGO_CHOICES = [('efectivo', 'Efectivo'), ('transferencia', 'Transferencia')]
    ESTADO_PAGO_CHOICES = [('pagado', 'Pagado'), ('fiado', 'Fiado'), ('anulado', 'Anulado')]
    id_ventas = models.AutoField(primary_key=True, db_column='ID_ventas')
    id_caja = models.ForeignKey(Caja, on_delete=models.PROTECT, db_column='ID_Caja')
    total = models.DecimalField(max_digits=10, decimal_places=2)
    fecha = models.DateField()
    hora = models.TimeField(db_column='Hora')
    id_clientes = models.ForeignKey(Clientes, on_delete=models.SET_NULL, null=True, blank=True, db_column='ID_Clientes')
    estado_de_pago = models.CharField(max_length=20, choices=ESTADO_PAGO_CHOICES, default='pagado', db_column='Estado_de_pago') 
    metodo_pago = models.CharField(max_length=20, choices=METODO_PAGO_CHOICES, null=True, blank=True)
    def __str__(self): return f"Venta {self.id_ventas} - Total: {self.total}"
    @property
    def cliente_info(self):
        if not self.id_clientes: return "Consumidor Final"
        return f"{self.id_clientes.nombre} {self.id_clientes.apellido or ''}".strip()

class DetallesVentas(models.Model):
    id_detalles_ventas = models.AutoField(primary_key=True, db_column='ID_Detalles_Ventas')
    venta = models.ForeignKey(Ventas, on_delete=models.CASCADE, db_column='ID_ventas', related_name='detalles')
    producto = models.ForeignKey(Productos, on_delete=models.PROTECT, db_column='ID_Productos', related_name='detalles_ventas')
    lote = models.ForeignKey(LotesProducto, on_delete=models.SET_NULL, null=True, blank=True, related_name='detalles_ventas') 
    cantidad = models.IntegerField(db_column='Cantidad')
    precio_unitario_venta = models.DecimalField(max_digits=10, decimal_places=2, db_column='Precio_Unitario_Venta')
    subtotal = models.DecimalField(max_digits=10, decimal_places=2, db_column='Subtotal')
    def __str__(self): return f"Detalle {self.id_detalles_ventas} - Venta: {self.venta_id}"

class Pago(models.Model):
    METODO_PAGO_CHOICES = [('efectivo', 'Efectivo'), ('transferencia', 'Transferencia')]
    cliente = models.ForeignKey(Clientes, on_delete=models.CASCADE, related_name='pagos')
    id_caja = models.ForeignKey(Caja, on_delete=models.PROTECT)
    monto = models.DecimalField(max_digits=10, decimal_places=2, validators=[MinValueValidator(0.01)])
    fecha = models.DateTimeField(auto_now_add=True)
    usuario = models.ForeignKey(Usuario, on_delete=models.PROTECT)
    metodo_pago = models.CharField(max_length=20, choices=METODO_PAGO_CHOICES, default='efectivo')
    def __str__(self): return f"Pago de {self.cliente.nombre} - ${self.monto} - {self.fecha.strftime('%d/%m/%Y')}"