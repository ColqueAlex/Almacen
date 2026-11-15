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

class Configuracion(models.Model):
    """Parámetros del sistema editables desde el admin (clave/valor)."""
    clave = models.CharField(max_length=50, unique=True)
    valor = models.CharField(max_length=255)
    descripcion = models.TextField(blank=True)

    class Meta:
        verbose_name = "Configuración"
        verbose_name_plural = "Configuraciones"
        ordering = ['clave']

    def __str__(self): return f"{self.clave} = {self.valor}"

    @classmethod
    def obtener(cls, clave, default=None, tipo=str):
        """Devuelve el valor de la clave convertido a `tipo`, o `default` si no existe o es inválido."""
        try:
            return tipo(cls.objects.get(clave=clave).valor)
        except (cls.DoesNotExist, ValueError, TypeError, ArithmeticError):
            return default


class Notificacion(models.Model):
    TIPO_CHOICES = [
        ('stock_bajo', 'Stock bajo'),
        ('limite_credito', 'Límite de crédito'),
        ('vencimiento', 'Vencimiento'),
    ]
    fecha = models.DateTimeField(auto_now_add=True)
    tipo = models.CharField(max_length=30, choices=TIPO_CHOICES)
    mensaje = models.TextField()
    destino_perfil = models.ForeignKey('usuarios.Perfil', on_delete=models.CASCADE, related_name='notificaciones')
    leida = models.BooleanField(default=False)
    fecha_lectura = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = "Notificación"
        verbose_name_plural = "Notificaciones"
        ordering = ['leida', '-fecha']

    def __str__(self): return f"[{self.get_tipo_display()}] {self.mensaje[:50]}"
