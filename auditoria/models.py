from django.db import models
from usuarios.models import Usuario

class Auditoria(models.Model):
    """
    Registro inmutable de actividad del sistema.

    Los registros se crean con `auditoria.servicios.registrar_evento` (nunca a mano)
    para que siempre lleven usuario, IP, navegador, categoría y nivel coherentes.
    """
    ACCIONES_CHOICES = [
        # Operaciones sobre datos
        ('creacion', 'Creación'),
        ('modificacion', 'Modificación'),
        ('eliminacion', 'Eliminación'),
        ('anulacion', 'Anulación'),
        ('cierre_forzado', 'Cierre forzado de caja'),
        # Seguridad
        ('login', 'Inicio de sesión'),
        ('logout', 'Cierre de sesión'),
        ('login_fallido', 'Inicio de sesión fallido'),
        ('bloqueo_cuenta', 'Bloqueo de cuenta'),
        ('cambio_clave', 'Cambio de contraseña'),
        ('restablecer_clave', 'Restablecimiento de contraseña'),
        ('acceso_denegado', 'Acceso denegado'),
        # Sistema
        ('backup', 'Copia de seguridad'),
        ('exportacion', 'Exportación de datos'),
    ]
    CATEGORIA_CHOICES = [('datos', 'Operaciones'), ('seguridad', 'Seguridad'), ('sistema', 'Sistema')]
    NIVEL_CHOICES = [('info', 'Informativo'), ('advertencia', 'Advertencia'), ('critico', 'Crítico')]

    # Valores por defecto según la acción (se pueden sobreescribir al registrar).
    CATEGORIA_POR_ACCION = {
        'login': 'seguridad', 'logout': 'seguridad', 'login_fallido': 'seguridad',
        'bloqueo_cuenta': 'seguridad', 'cambio_clave': 'seguridad',
        'restablecer_clave': 'seguridad', 'acceso_denegado': 'seguridad',
        'backup': 'sistema', 'exportacion': 'sistema',
    }
    NIVEL_POR_ACCION = {
        'eliminacion': 'advertencia', 'anulacion': 'advertencia', 'cierre_forzado': 'advertencia',
        'login_fallido': 'advertencia', 'acceso_denegado': 'advertencia',
        'restablecer_clave': 'advertencia', 'bloqueo_cuenta': 'critico',
    }

    usuario = models.ForeignKey(Usuario, on_delete=models.SET_NULL, null=True, blank=True)
    # Copia del nombre de usuario al momento del evento: se conserva aunque el
    # usuario se borre, y guarda el nombre tipeado en un login fallido.
    usuario_nombre = models.CharField(max_length=150, blank=True, default='', db_index=True)
    accion = models.CharField(max_length=20, choices=ACCIONES_CHOICES, db_index=True)
    categoria = models.CharField(max_length=15, choices=CATEGORIA_CHOICES, default='datos', db_index=True)
    nivel = models.CharField(max_length=15, choices=NIVEL_CHOICES, default='info', db_index=True)
    modelo = models.CharField(max_length=50, db_index=True, blank=True, default='')
    objeto_id = models.CharField(max_length=64, null=True, blank=True)
    objeto_repr = models.CharField('objeto', max_length=200, blank=True, default='')
    descripcion = models.TextField()
    # {campo: {"campo": etiqueta legible, "antes": texto, "despues": texto}}
    cambios = models.JSONField(null=True, blank=True)
    datos_anteriores = models.JSONField(null=True, blank=True)
    datos_nuevos = models.JSONField(null=True, blank=True)
    ip = models.GenericIPAddressField('dirección IP', null=True, blank=True)
    user_agent = models.CharField('navegador', max_length=255, blank=True, default='')
    ruta = models.CharField(max_length=255, blank=True, default='')
    fecha_hora = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        verbose_name = "Auditoría"
        verbose_name_plural = "Auditorías"
        ordering = ['-fecha_hora']
        indexes = [
            models.Index(fields=['categoria', '-fecha_hora'], name='audit_cat_fecha_idx'),
            models.Index(fields=['accion', '-fecha_hora'], name='audit_accion_fecha_idx'),
        ]

    def __str__(self):
        return f"{self.get_accion_display()} - {self.modelo or self.get_categoria_display()} por {self.actor}"

    @property
    def actor(self):
        """Nombre a mostrar de quien hizo la acción."""
        if self.usuario_id and self.usuario:
            return self.usuario.username
        if self.usuario_nombre:
            return self.usuario_nombre
        return 'Sistema'

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
        ('caja_abierta', 'Caja abierta demasiado tiempo'),
        ('limpieza_productos', 'Limpieza de catálogo'),
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
