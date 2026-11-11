from django.db import models
from usuarios.models import Usuario

class Auditoria(models.Model):
    ACCIONES_CHOICES = [('creacion', 'Creación'), ('modificacion', 'Modificación'), ('eliminacion', 'Eliminación')]
    usuario = models.ForeignKey(Usuario, on_delete=models.SET_NULL, null=True, blank=True)
    accion = models.CharField(max_length=20, choices=ACCIONES_CHOICES)
    modelo = models.CharField(max_length=50) 
    objeto_id = models.IntegerField(null=True, blank=True)
    descripcion = models.TextField()
    datos_anteriores = models.JSONField(null=True, blank=True)
    datos_nuevos = models.JSONField(null=True, blank=True)
    fecha_hora = models.DateTimeField(auto_now_add=True)
    class Meta:
        verbose_name = "Auditoría"
        verbose_name_plural = "Auditorías"
        ordering = ['-fecha_hora']
    def __str__(self): return f"{self.get_accion_display()} - {self.modelo} por {self.usuario.username if self.usuario else 'Sistema'}"