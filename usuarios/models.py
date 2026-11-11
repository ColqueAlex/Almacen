
from django.db import models
from django.contrib.auth.models import AbstractBaseUser, BaseUserManager, PermissionsMixin

class Perfil(models.Model):
    nombre = models.CharField(max_length=50, unique=True)
    descripcion = models.TextField(blank=True, null=True)
    def __str__(self): return self.nombre

class Permiso(models.Model):
    nombre = models.CharField(max_length=50, unique=True)
    descripcion = models.TextField(blank=True, null=True)
    perfiles = models.ManyToManyField(Perfil, related_name='permisos')
    def __str__(self): return self.nombre

class UsuarioManager(BaseUserManager):
    def create_user(self, dni, apellido, nombre, correo, password=None, id_perfil=None, username=None):
        if not dni: raise ValueError('El DNI es obligatorio')
        apellido = (apellido or '').strip()
        nombre = (nombre or '').strip()
        correo = (correo or '').strip()
        if not apellido or not nombre or not correo: raise ValueError('Apellido, nombre y correo son obligatorios')
        if not username:
            base_apellido = apellido.lower().strip()
            base_nombre = nombre.lower().strip()
            primera_letra = base_nombre[:1] if base_nombre else 'x'
            username = f"{base_apellido}{primera_letra}"
        user = self.model(
            dni=dni, apellido=apellido, nombre=nombre, correo=self.normalize_email(correo),
            username=username, id_perfil=id_perfil, debe_cambiar_clave=True,
        )
        if password is not None: user.set_password(password)
        else: user.set_unusable_password()
        user.save(using=self._db)
        return user

    def create_superuser(self, dni, apellido, nombre, correo, password=None, username=None, **extra_fields):
        user = self.create_user(dni=dni, apellido=apellido, nombre=nombre, correo=correo, password=password, username=username)
        user.is_admin = True
        user.is_superuser = True
        user.is_staff = True
        user.debe_cambiar_clave = False
        user.save(using=self._db)
        return user

class Usuario(AbstractBaseUser, PermissionsMixin):
    dni = models.CharField(max_length=8, primary_key=True)
    apellido = models.CharField(max_length=30)
    nombre = models.CharField(max_length=30)
    correo = models.EmailField(unique=True)
    username = models.CharField(max_length=30, unique=True)
    activo = models.BooleanField(default=True)
    debe_cambiar_clave = models.BooleanField(default=True)
    fecha_ultima_modificacion = models.DateTimeField(auto_now=True)
    fecha_baja = models.DateTimeField(blank=True, null=True)
    id_perfil = models.ForeignKey(Perfil, on_delete=models.SET_NULL, null=True, blank=True)
    is_staff = models.BooleanField(default=False)
    is_admin = models.BooleanField(default=False)
    intentos_fallidos = models.IntegerField(default=0)
    bloqueo_hasta = models.DateTimeField(null=True, blank=True)

    objects = UsuarioManager()
    USERNAME_FIELD = 'username'
    REQUIRED_FIELDS = ['dni', 'apellido', 'nombre', 'correo']

    @property
    def is_active(self): return self.activo
    def __str__(self): return f"{self.username} - {self.nombre} {self.apellido}"
