from django.db.models.signals import post_save, pre_delete, pre_save
from django.dispatch import receiver
from django.forms.models import model_to_dict
from .models import Productos, Ventas, Clientes, Proveedor, Auditoria, Caja, Compras, Usuario
from .middleware import get_current_user

# Diccionario temporal para guardar cómo estaba el objeto antes de modificarse
_estado_anterior = {}

def registrar_auditoria(accion, modelo_nombre, objeto_id, descripcion, datos_ant=None, datos_new=None):
    usuario = get_current_user()
    if usuario and not usuario.is_authenticated:
        usuario = None
        
    Auditoria.objects.create(
        usuario=usuario,
        accion=accion,
        modelo=modelo_nombre,
        objeto_id=objeto_id,
        descripcion=descripcion,
        datos_anteriores=datos_ant,
        datos_nuevos=datos_new
    )

# --- FUNCIÓN PARA CAPTURAR DATOS ANTES DE GUARDAR ---
@receiver(pre_save)
def capturar_estado_anterior(sender, instance, **kwargs):
    modelos_auditados = [Productos, Ventas, Clientes, Proveedor, Caja, Compras, Usuario]
    if sender not in modelos_auditados or not instance.pk:
        return
    
    try:
        obj = sender.objects.get(pk=instance.pk)
        data = model_to_dict(obj)
        for key, value in data.items():
            if hasattr(value, 'pk'):
                data[key] = value.pk 
            elif not isinstance(value, (int, float, str, bool, type(None))):
                data[key] = str(value) 
        _estado_anterior[sender.__name__] = data
    except sender.DoesNotExist:
        pass

# --- FUNCIÓN PARA REGISTRAR EL CAMBIO DESPUÉS DE GUARDAR ---
@receiver(post_save)
def registrar_cambios(sender, instance, created, **kwargs):
    modelos = {
        Productos: 'Producto', Ventas: 'Venta', Clientes: 'Cliente', 
        Proveedor: 'Proveedor', Caja: 'Caja', Compras: 'Compra', Usuario: 'Usuario'
    }
    
    if sender not in modelos:
        return

    modelo_nombre = modelos[sender]
    
    data_nueva = model_to_dict(instance)
    for key, value in data_nueva.items():
        if hasattr(value, 'pk'): 
            data_nueva[key] = value.pk
        elif not isinstance(value, (int, float, str, bool, type(None))): 
            data_nueva[key] = str(value)

    if created:
        desc = f"Creó un nuevo registro en {modelo_nombre}"
        if sender == Productos: 
            desc = f'Creó el producto "{instance.nombre}"'
        elif sender == Compras: 
            desc = f'Registró la compra #{instance.id} por un total de ${instance.total}'
        elif sender == Ventas: 
            desc = f'Registró la venta #{instance.id_ventas} por ${instance.total}'
        elif sender == Clientes: 
            desc = f'Registró al cliente "{instance.nombre}"'
        elif sender == Usuario: 
            desc = f'Creó el usuario "{instance.username}"'
        
        registrar_auditoria('creacion', modelo_nombre, instance.pk, desc, datos_ant={}, datos_new=data_nueva)
    
    else:
        data_vieja = _estado_anterior.get(sender.__name__, {})
        
        # Campos técnicos que ignoramos para no ensuciar la auditoría (ej. last_login)
        campos_ignorados = ['last_login', 'password', 'fecha_ultima_modificacion']
        
        # Buscamos qué campos cambiaron realmente (ignorando los técnicos)
        cambios = {
            k: v for k, v in data_nueva.items() 
            if k in data_vieja and data_vieja[k] != v and k not in campos_ignorados
        }
        
        # Si solo cambió el last_login (inicio de sesión) o la clave, no registramos nada y salimos
        if not cambios and not (sender == Compras and instance.estado == 'Anulado') and not (sender == Ventas and instance.estado_de_pago == 'anulado'):
            if sender.__name__ in _estado_anterior: 
                del _estado_anterior[sender.__name__]
            return
            
        # Si hubo cambios reales o es una anulación, lo registramos
        if cambios or (sender == Compras and instance.estado == 'Anulado') or (sender == Ventas and instance.estado_de_pago == 'anulado'):
            desc = f"Modificó un registro en {modelo_nombre}"
            
            if sender == Compras and instance.estado == 'Anulado':
                desc = f'Anuló la compra #{instance.id}. Motivo: {instance.motivo_anulacion}'
            elif sender == Ventas and instance.estado_de_pago == 'anulado':
                desc = f'Anuló la venta #{instance.id_ventas}'
            elif sender == Productos: 
                desc = f'Modificó el producto "{instance.nombre}". Campos cambiados: {", ".join(cambios.keys())}'
            elif sender == Caja and instance.estado == 'cerrada':
                desc = f'Cerró la caja #{instance.id_caja}. Diferencia: ${instance.diferencia}'
            elif sender == Usuario:
                desc = f'Modificó el usuario "{instance.username}". Campos cambiados: {", ".join(cambios.keys())}'
            elif cambios:
                desc = f"Modificó {modelo_nombre}. Campos cambiados: {', '.join(cambios.keys())}"

            registrar_auditoria('modificacion', modelo_nombre, instance.pk, desc, datos_ant=data_vieja, datos_new=data_nueva)

    # Limpiamos el diccionario temporal
    if sender.__name__ in _estado_anterior:
        del _estado_anterior[sender.__name__]

# --- FUNCIÓN PARA ELIMINACIONES ---
@receiver(pre_delete)
def registrar_eliminacion(sender, instance, **kwargs):
    modelos_auditados = {Productos: 'Producto', Proveedor: 'Proveedor'}
    if sender not in modelos_auditados:
        return
        
    modelo_nombre = modelos_auditados[sender]
    data_vieja = model_to_dict(instance)
    for key, value in data_vieja.items():
        if hasattr(value, 'pk'): 
            data_vieja[key] = value.pk
        elif not isinstance(value, (int, float, str, bool, type(None))): 
            data_vieja[key] = str(value)
        
    desc = f"Eliminó un registro en {modelo_nombre}"
    if sender == Productos: 
        desc = f'Eliminó el producto "{instance.nombre}"'
    elif sender == Proveedor: 
        desc = f'Eliminó el proveedor "{instance.nombre}"'
    
    registrar_auditoria('eliminacion', modelo_nombre, instance.pk, desc, datos_ant=data_vieja, datos_new={})