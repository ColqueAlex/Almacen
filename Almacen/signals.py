# Almacen/signals.py
from django.db.models.signals import post_save, pre_delete
from django.dispatch import receiver
from .models import Productos, Ventas, Clientes, Proveedor, Auditoria, Caja
from .middleware import get_current_user

def registrar_auditoria(accion, modelo_nombre, objeto_id, descripcion):
    usuario = get_current_user()
    # Si el usuario no existe (ej. script automático), guardamos None
    if usuario and not usuario.is_authenticated:
        usuario = None
        
    Auditoria.objects.create(
        usuario=usuario,
        accion=accion,
        modelo=modelo_nombre,
        objeto_id=objeto_id,
        descripcion=descripcion
    )

# --- PRODUCTOS ---
@receiver(post_save, sender=Productos)
def auditar_producto(sender, instance, created, **kwargs):
    if created:
        registrar_auditoria('creacion', 'Producto', instance.id, f'Creó el producto "{instance.nombre}"')
    else:
        registrar_auditoria('modificacion', 'Producto', instance.id, f'Modificó el producto "{instance.nombre}"')

@receiver(pre_delete, sender=Productos)
def auditar_eliminar_producto(sender, instance, **kwargs):
    registrar_auditoria('eliminacion', 'Producto', instance.id, f'Eliminó el producto "{instance.nombre}"')

# --- CLIENTES ---
@receiver(post_save, sender=Clientes)
def auditar_cliente(sender, instance, created, **kwargs):
    if created:
        registrar_auditoria('creacion', 'Cliente', instance.id_clientes, f'Registró al cliente "{instance.nombre}"')
    else:
        registrar_auditoria('modificacion', 'Cliente', instance.id_clientes, f'Modificó datos del cliente "{instance.nombre}"')

# --- PROVEEDORES ---
@receiver(post_save, sender=Proveedor)
def auditar_proveedor(sender, instance, created, **kwargs):
    if created:
        registrar_auditoria('creacion', 'Proveedor', instance.id, f'Registró al proveedor "{instance.nombre}"')
    else:
        registrar_auditoria('modificacion', 'Proveedor', instance.id, f'Modificó datos del proveedor "{instance.nombre}"')

# --- VENTAS ---
@receiver(post_save, sender=Ventas)
def auditar_venta(sender, instance, created, **kwargs):
    if created:
        registrar_auditoria('creacion', 'Venta', instance.id_ventas, f'Registró la venta #{instance.id_ventas} por ${instance.total}')
    else:
        if instance.estado_de_pago == 'anulado':
            registrar_auditoria('modificacion', 'Venta', instance.id_ventas, f'Anuló la venta #{instance.id_ventas}')

# --- CAJAS ---
@receiver(post_save, sender=Caja)
def auditar_caja(sender, instance, created, **kwargs):
    if created:
        registrar_auditoria('creacion', 'Caja', instance.id_caja, f'Abrió la caja #{instance.id_caja} con un monto inicial de ${instance.monto_inicial}')
    else:
        if instance.estado == 'cerrada':
            registrar_auditoria('modificacion', 'Caja', instance.id_caja, f'Cerró la caja #{instance.id_caja} con un monto real de ${instance.monto_final_real} (Diferencia: ${instance.diferencia})')