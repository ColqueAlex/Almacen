from django.db import models
from usuarios.models import Usuario

class Caja(models.Model):
    ESTADO_CHOICES = [('abierta', 'Abierta'), ('cerrada', 'Cerrada')]
    id_caja = models.AutoField(primary_key=True, db_column='ID_Caja')
    usuario = models.ForeignKey(Usuario, on_delete=models.PROTECT, related_name='cajas')
    monto_inicial = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    monto_final_esperado = models.DecimalField(max_digits=10, decimal_places=2, default=0.00, null=True, blank=True)
    monto_final_real = models.DecimalField(max_digits=10, decimal_places=2, default=0.00, null=True, blank=True)
    diferencia = models.DecimalField(max_digits=10, decimal_places=2, default=0.00, null=True, blank=True)
    fecha_apertura = models.DateTimeField(auto_now_add=True)
    fecha_cierre = models.DateTimeField(null=True, blank=True)
    estado = models.CharField(max_length=10, choices=ESTADO_CHOICES, default='abierta')
    observaciones = models.TextField(blank=True, null=True)
    def __str__(self): return f"Caja #{self.id_caja} ({self.get_estado_display()}) - {self.usuario.username}"