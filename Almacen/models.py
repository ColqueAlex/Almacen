from django.db import models
from django.contrib.auth.models import AbstractBaseUser, BaseUserManager, PermissionsMixin
from django.utils import timezone
from django.core.validators import MinValueValidator
from django.core.exceptions import ValidationError
from django.db.models import Sum


def validar_categoria(value):
    if any(char.isdigit() for char in value):
        raise ValidationError('La categoría no puede contener números.')


class Productos(models.Model):
    ESTADO_CHOICES = [
        ('activo', 'Activo'),
        ('inactivo', 'Inactivo'),
    ]

    nombre = models.CharField(max_length=100)
    categoria = models.CharField(max_length=100, validators=[validar_categoria])
    marca = models.CharField(max_length=100, default='Sin marca')
    precio = models.DecimalField(
    max_digits=10, 
    decimal_places=2, 
    validators=[MinValueValidator(0.01)]
    )
    estado = models.CharField(max_length=20, choices=ESTADO_CHOICES, default='activo')

    def __str__(self):
        return f"{self.nombre} ({self.marca}) - ${self.precio}"
    
    @property
    def stock(self):
        res = self.lotes.filter(activo=True).aggregate(total=Sum('stock_actual'))
        return res['total'] if res['total'] is not None else 0

class Ventas(models.Model):
    id_ventas = models.AutoField(primary_key=True, db_column='ID_ventas')
    id_caja = models.IntegerField(db_column='ID_Caja')
    total = models.DecimalField(max_digits=65, decimal_places=2)
    fecha = models.DateField()
    hora = models.TimeField(db_column='Hora')
    id_clientes = models.IntegerField(db_column='ID_Clientes')
    estado_de_pago = models.CharField(max_length=10, db_column='Estado_de_pago')

    def __str__(self):
        return f"Venta {self.id_ventas} - Total: {self.total}"

    @property
    def cliente_info(self):
        if not self.id_clientes or self.id_clientes == 0:
            return "Sin fiar"
        
        from .models import Clientes
        try:
            cliente = Clientes.objects.get(pk=self.id_clientes)
            return f"{cliente.nombre} {cliente.apellido or ''}".strip()
        except Clientes.DoesNotExist:
            return f"Cliente #{self.id_clientes}"

class DetallesVentas(models.Model):
    id_detalles_ventas = models.IntegerField(primary_key=True, db_column='ID_Detalles_Ventas')
    venta = models.ForeignKey('Ventas', on_delete=models.CASCADE, db_column='ID_ventas', related_name='detalles')
    producto = models.ForeignKey('Productos', on_delete=models.PROTECT, db_column='ID_Productos', related_name='detalles_ventas')
    cantidad = models.IntegerField(db_column='Cantidad')
    precio_unitario_compra = models.DecimalField(max_digits=65, decimal_places=2, db_column='Precio_Unitario_Compra')
    subtotal = models.DecimalField(max_digits=65, decimal_places=2, db_column='Subtotal')

    def __str__(self):
        return f"Detalle {self.id_detalles_ventas} - Venta: {self.venta_id}"


    
class Proveedor(models.Model):
    nombre = models.CharField(max_length=100)
    numero_telefono = models.CharField(max_length=20)
    tipo_productos = models.CharField(max_length=100)
    activo = models.BooleanField(default=True)

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
        if not dni:
            raise ValueError('El DNI es obligatorio')

        apellido = (apellido or '').strip()
        nombre = (nombre or '').strip()
        correo = (correo or '').strip()

        if not apellido or not nombre or not correo:
            raise ValueError('Apellido, nombre y correo son obligatorios')

        if not username:
            base_apellido = apellido.lower().strip()
            base_nombre = nombre.lower().strip()
            primera_letra = base_nombre[:1] if base_nombre else 'x'
            username = f"{base_apellido}{primera_letra}"

        user = self.model(
            dni=dni,
            apellido=apellido,
            nombre=nombre,
            correo=self.normalize_email(correo),
            username=username,
            id_perfil=id_perfil,
            debe_cambiar_clave=True,
        )
        if password is not None:
            user.set_password(password)
        else:
            user.set_unusable_password()
        user.save(using=self._db)
        return user

    def create_superuser(self, dni, apellido, nombre, correo, password=None, username=None, **extra_fields):
        user = self.create_user(
            dni=dni,
            apellido=apellido,
            nombre=nombre,
            correo=correo,
            password=password,
            username=username,
        )
        user.is_admin = True
        user.is_superuser = True
        user.is_staff = True
        user.debe_cambiar_clave = False
        user.save(using=self._db)
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

    @property
    def is_active(self):
        return self.activo

    def __str__(self):
        return f"{self.username} - {self.nombre} {self.apellido}"

class Compras(models.Model):
    fecha = models.DateField(auto_now_add=True)
    hora = models.TimeField(auto_now_add=True)
    total = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    estado = models.CharField(max_length=20, default='Confirmado')
    id_proveedor = models.ForeignKey(Proveedor, on_delete=models.PROTECT, null=True, blank=True)
    id_usuario = models.ForeignKey(Usuario, on_delete=models.PROTECT)

    def __str__(self):
        return f"Compra #{self.id} - Proveedor: {self.id_proveedor.nombre} - ${self.total}"


class DetallesCompra(models.Model):
    id_compra = models.ForeignKey(Compras, on_delete=models.CASCADE, related_name='detalles')
    id_producto = models.ForeignKey(Productos, on_delete=models.PROTECT)
    lote = models.CharField(max_length=50)
    fecha_vencimiento = models.DateField(null=True, blank=True)
    cantidad = models.IntegerField(validators=[MinValueValidator(1)])
    precio_unitario_compra = models.DecimalField(max_digits=10, decimal_places=2)
    subtotal = models.DecimalField(max_digits=10, decimal_places=2)

    def __str__(self):
        return f"Detalle #{self.id} Compra #{self.id_compra.id} - {self.id_producto.nombre}"

class LotesProducto(models.Model):
    id_producto = models.ForeignKey(Productos, on_delete=models.CASCADE, related_name='lotes')
    lote = models.CharField(max_length=50)
    fecha_vencimiento = models.DateField(null=True, blank=True)
    stock_actual = models.IntegerField(default=0)
    precio_costo = models.DecimalField(max_digits=10, decimal_places=2)
    activo = models.BooleanField(default=True)

    def __str__(self):
        return f"{self.id_producto.nombre} - Lote: {self.lote} - Stock: {self.stock_actual}"

class Clientes(models.Model):
    id_clientes = models.AutoField(primary_key=True, db_column='ID_Clientes')
    nombre = models.CharField(max_length=100)
    apellido = models.CharField(max_length=100, blank=True, null=True)
    telefono = models.CharField(max_length=20, blank=True, null=True)

    def __str__(self):
        return f"{self.nombre} {self.apellido or ''}"

