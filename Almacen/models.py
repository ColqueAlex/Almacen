from django.db import models
from django.contrib.auth.models import AbstractBaseUser, BaseUserManager, PermissionsMixin
from django.utils import timezone
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

class Perfil(models.Model):
    nombre = models.CharField(max_length=50, unique=True)
    descripcion = models.TextField(blank=True, null=True)

    def __str__(self):
        return self.nombre

class Permiso(models.Model):
    nombre = models.CharField(max_length=50, unique=True)
    descripcion = models.TextField(blank=True, null=True)
    perfiles = models.ManyToManyField(Perfil, related_name= 'permisos')

    def __str__(self):
        return self.nombre

class UsuarioManager(BaseUserManager):
    def create_user(self, dni, apellido, nombre, correo, password=None, id_perfil=None, username=None):
        if not dni :
            raise ValueError('El DNI es Obligatorio')

        if not username :
            username = f"{apellido.lower().strip()}{nombre.lower().strip()[0]}"

        user = self.model(
            dni = dni,
            apellido = apellido,
            nombre = nombre,
            correo = self.normalize_email(correo),
            username = username,
            id_perfil = id_perfil,
            debe_cambiar_clave = True
        )
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_superuser(self, dni, apellido, nombre, correo, password=None, username=None):
        user = self.create_user(dni, apellido, nombre, correo, password)
        user.is_admin = True
        user.is_superuser = True
        user.is_staff = True
        user.debe_cambiar_clave = False
        user.save(using = self._db)
        return user

class Usuario(AbstractBaseUser, PermissionsMixin):
    dni = models.IntegerField(primary_key=True)
    apellido = models.CharField(max_length=100)
    nombre = models.CharField(max_length=100)
    correo = models.EmailField(unique=True)
    username = models.CharField(max_length=100, unique=True)

    activo = models.BooleanField(default=True)
    debe_cambiar_clave = models.BooleanField(default=True)
    fecha_ultima_modificacion = models.DateTimeField(auto_now=True)
    fecha_baja = models.DateTimeField(blank=True, null=True)

    id_perfil = models.ForeignKey(Perfil, on_delete=models.SET_NULL, null=True, blank=True)

    is_staff = models.BooleanField(default=False)
    is_admin = models.BooleanField(default=False)

    objects = UsuarioManager()

    USERNAME_FIELD = 'username'
    REQUIRED_FIELDS = ['dni', 'apellido', 'nombre', 'correo']

    # Sin el método save() explícito para evitar duplicación y errores de nombre

    def __str__(self):
        return f"{self.username} - {self.nombre} {self.apellido}"