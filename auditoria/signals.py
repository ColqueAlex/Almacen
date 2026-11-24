import functools
import logging
from decimal import Decimal, InvalidOperation

from django.contrib.auth.signals import user_logged_in, user_logged_out, user_login_failed
from django.db import transaction
from django.db.models.signals import post_save, pre_delete, pre_save
from django.dispatch import receiver

from usuarios.models import Usuario
from productos.models import LotesProducto, Productos, Proveedor
from ventas.models import Ventas, Clientes, DevolucionVenta, Pago
from compras.models import Compras
from caja.models import Caja
from .middleware import get_current_user
from .servicios import calcular_cambios, instantanea, registrar_evento, representar
from . import notificaciones


logger = logging.getLogger('auditoria')


def _sin_romper_la_operacion(receptor):
    """Un error al auditar se registra en el log, pero nunca cancela la venta/compra/etc."""
    @functools.wraps(receptor)
    def envoltura(*args, **kwargs):
        try:
            return receptor(*args, **kwargs)
        except Exception:
            logger.exception('Error en la señal de auditoría %s', receptor.__name__)
    return envoltura



# =============================================================================
# AUDITORÍA DE DATOS
# =============================================================================
# Nombre visible de cada modelo auditado.
MODELOS_AUDITADOS = {
    Productos: 'Producto', LotesProducto: 'Lote', Proveedor: 'Proveedor',
    Ventas: 'Venta', DevolucionVenta: 'Devolución', Pago: 'Pago', Clientes: 'Cliente',
    Compras: 'Compra', Caja: 'Caja', Usuario: 'Usuario',
}

# Modelos de los que solo interesa el alta (son movimientos, no se editan).
SOLO_CREACION = {Pago, DevolucionVenta}

# Campos que cambian solos o como efecto de otra operación ya auditada:
# registrarlos llenaría la auditoría de ruido.
CAMPOS_IGNORADOS_COMUNES = {'last_login', 'fecha_ultima_modificacion'}
CAMPOS_IGNORADOS = {
    # El login, el bloqueo y el cambio de clave tienen sus propios eventos de seguridad.
    Usuario: {'intentos_fallidos', 'bloqueo_hasta', 'debe_cambiar_clave'},
    # El stock de un lote baja con cada venta (ya registrada como venta).
    LotesProducto: {'stock_actual'},
    # El saldo del cliente se mueve con las ventas fiadas y los pagos.
    Clientes: {'saldo'},
}


def _ignorados(sender):
    return CAMPOS_IGNORADOS_COMUNES | CAMPOS_IGNORADOS.get(sender, set())


def _dinero(valor):
    """Formato argentino: $1.234,50. Acepta Decimal, número o texto (valores de formularios)."""
    try:
        numero = Decimal(str(valor)) if valor not in (None, '') else Decimal('0')
    except (InvalidOperation, ValueError):
        return f"${valor}"
    return f"${numero:,.2f}".replace(',', 'X').replace('.', ',').replace('X', '.')


def _descripcion_creacion(sender, obj):
    if sender is Productos:
        return f'Creó el producto "{obj.nombre}" ({obj.marca}) con precio {_dinero(obj.precio)}'
    if sender is LotesProducto:
        # La fecha puede llegar como texto ('2026-12-31') si el objeto no se recargó.
        fecha = LotesProducto._meta.get_field('fecha_vencimiento').to_python(obj.fecha_vencimiento)
        vence = f", vence el {fecha:%d/%m/%Y}" if fecha else ''
        # Las compras crean el lote vacío y después le suman el stock: por eso
        # no se informan unidades aquí (las detalla la compra).
        return f'Registró el lote "{obj.lote}" de "{obj.id_producto.nombre}" (costo {_dinero(obj.precio_costo)}{vence})'
    if sender is Proveedor:
        return f'Registró al proveedor "{obj.nombre}"'
    if sender is Ventas:
        cliente = obj.cliente_info
        forma = 'fiada' if obj.estado_de_pago == 'fiado' else (obj.get_metodo_pago_display() or '').lower()
        return f'Registró la venta #{obj.pk} por {_dinero(obj.total)} a {cliente}' + (f' ({forma})' if forma else '')
    if sender is DevolucionVenta:
        return (f'Registró la devolución de {obj.cantidad} u. de "{obj.detalle.producto.nombre}" '
                f'(venta #{obj.detalle.venta_id}) por {_dinero(obj.monto)}, reintegro: {obj.get_metodo_reintegro_display()}')
    if sender is Pago:
        return (f'Registró un pago de {_dinero(obj.monto)} del cliente "{representar(obj.cliente)}" '
                f'({obj.get_metodo_pago_display()})')
    if sender is Clientes:
        return f'Registró al cliente "{representar(obj)}"'
    if sender is Compras:
        proveedor = f' al proveedor "{obj.id_proveedor.nombre}"' if obj.id_proveedor_id else ''
        return f'Registró la compra #{obj.pk}{proveedor} por {_dinero(obj.total)}'
    if sender is Caja:
        return f'Abrió la caja #{obj.pk} con un monto inicial de {_dinero(obj.monto_inicial)}'
    if sender is Usuario:
        perfil = f' con perfil {obj.id_perfil.nombre}' if obj.id_perfil_id else ''
        return f'Creó el usuario "{obj.username}"{perfil}'
    return f'Creó un registro en {MODELOS_AUDITADOS[sender]}'


def _evento_modificacion(sender, anterior, obj, cambios):
    """Devuelve (accion, descripcion) o (accion, descripcion, nivel) de una modificación."""
    etiqueta = MODELOS_AUDITADOS[sender]
    campos = ', '.join(c['campo'] for c in cambios.values())

    if sender is Compras and 'estado' in cambios and obj.estado == 'Anulado':
        motivo = f' Motivo: {obj.motivo_anulacion}' if obj.motivo_anulacion else ''
        return 'anulacion', f'Anuló la compra #{obj.pk} por {_dinero(obj.total)}.{motivo}'
    if sender is Ventas and 'estado_de_pago' in cambios and obj.estado_de_pago == 'anulado':
        return 'anulacion', f'Anuló la venta #{obj.pk} por {_dinero(obj.total)} ({obj.cliente_info})'
    if sender is Caja and 'estado' in cambios and obj.estado == 'cerrada' and obj.cierre_forzado:
        duenio = obj.usuario.username if obj.usuario_id else '?'
        descripcion = (
            f'Forzó el cierre de la caja #{obj.pk} de "{duenio}". Esperado: {_dinero(obj.monto_final_esperado)}, '
            f'contado: {_dinero(obj.monto_final_real)}, diferencia: {_dinero(obj.diferencia)}. '
            f'Motivo: {obj.motivo_cierre_forzado}'
        )
        # Un faltante en una caja que su dueño no cerró es lo que más hay que revisar.
        faltante = obj.diferencia is not None and Decimal(str(obj.diferencia)) < 0
        return 'cierre_forzado', descripcion, ('critico' if faltante else None)
    if sender is Caja and 'estado' in cambios and obj.estado == 'cerrada':
        return 'modificacion', (
            f'Cerró la caja #{obj.pk}. Esperado: {_dinero(obj.monto_final_esperado)}, '
            f'contado: {_dinero(obj.monto_final_real)}, diferencia: {_dinero(obj.diferencia)}'
        )
    if sender is Usuario and 'activo' in cambios:
        if obj.activo:
            return 'modificacion', f'Reactivó al usuario "{obj.username}"'
        actor = get_current_user()
        if not getattr(actor, 'is_authenticated', False):
            # Sin usuario logueado: lo desactivó el bloqueo por intentos fallidos.
            return 'modificacion', f'La cuenta "{obj.username}" quedó desactivada automáticamente'
        return 'modificacion', f'Dio de baja al usuario "{obj.username}"'
    if sender is Productos and 'estado' in cambios and len(cambios) == 1:
        verbo = 'Activó' if obj.estado == 'activo' else 'Desactivó'
        return 'modificacion', f'{verbo} el producto "{obj.nombre}"'
    if sender is Productos and 'precio' in cambios:
        return 'modificacion', (
            f'Modificó el producto "{obj.nombre}": precio {_dinero(anterior.precio)} → {_dinero(obj.precio)}'
            + (f' (también: {campos})' if len(cambios) > 1 else '')
        )
    return 'modificacion', f'Modificó {etiqueta.lower()} "{representar(obj, etiqueta)}". Campos: {campos}'


@receiver(pre_save)
@_sin_romper_la_operacion
def capturar_estado_anterior(sender, instance, raw=False, update_fields=None, **kwargs):
    """
    Guarda en la propia instancia cómo estaba en la base antes de guardarse.
    (Antes se usaba un diccionario global por modelo, que se pisaba si dos
    usuarios guardaban a la vez el mismo tipo de objeto.)
    """
    if sender not in MODELOS_AUDITADOS or sender in SOLO_CREACION or raw or not instance.pk:
        return
    # save(update_fields=[...]) que solo toca campos ignorados: no hace falta consultar.
    if update_fields and set(update_fields) <= _ignorados(sender):
        instance._auditoria_anterior = None
        return
    instance._auditoria_anterior = sender._default_manager.filter(pk=instance.pk).first()


@receiver(post_save)
@_sin_romper_la_operacion
def registrar_cambios(sender, instance, created, raw=False, **kwargs):
    if sender not in MODELOS_AUDITADOS or raw:
        return
    etiqueta = MODELOS_AUDITADOS[sender]
    ignorados = _ignorados(sender)

    if created:
        registrar_evento(
            'creacion', _descripcion_creacion(sender, instance),
            modelo=etiqueta, objeto=instance, objeto_repr=representar(instance, etiqueta),
            cambios=calcular_cambios(None, instance, ignorados),
            datos_anteriores={}, datos_nuevos=instantanea(instance),
        )
        return

    anterior = getattr(instance, '_auditoria_anterior', None)
    instance._auditoria_anterior = None
    if anterior is None or sender in SOLO_CREACION:
        return

    cambios = calcular_cambios(anterior, instance, ignorados)
    if not cambios:
        return

    # _evento_modificacion devuelve (accion, descripcion) o (accion, descripcion, nivel).
    accion, descripcion, *nivel = _evento_modificacion(sender, anterior, instance, cambios)
    registrar_evento(
        accion, descripcion, nivel=nivel[0] if nivel else None,
        modelo=etiqueta, objeto=instance, objeto_repr=representar(instance, etiqueta),
        cambios=cambios,
        datos_anteriores=instantanea(anterior), datos_nuevos=instantanea(instance),
    )


@receiver(pre_delete)
@_sin_romper_la_operacion
def registrar_eliminacion(sender, instance, **kwargs):
    if sender not in MODELOS_AUDITADOS:
        return
    etiqueta = MODELOS_AUDITADOS[sender]
    nombre = representar(instance, etiqueta)
    registrar_evento(
        'eliminacion', f'Eliminó {etiqueta.lower()} "{nombre}"',
        modelo=etiqueta, objeto=instance, objeto_repr=nombre,
        cambios=calcular_cambios(instance, None, _ignorados(sender)),
        datos_anteriores=instantanea(instance), datos_nuevos={},
    )


# =============================================================================
# AUDITORÍA DE SEGURIDAD (sesiones)
# =============================================================================
@receiver(user_logged_in)
@_sin_romper_la_operacion
def registrar_login(sender, request, user, **kwargs):
    perfil = getattr(getattr(user, 'id_perfil', None), 'nombre', None)
    registrar_evento(
        'login', 'Inició sesión' + (f' como {perfil}' if perfil else ''),
        modelo='Usuario', objeto=user, usuario=user, request=request,
    )


@receiver(user_logged_out)
@_sin_romper_la_operacion
def registrar_logout(sender, request, user, **kwargs):
    if user is None:
        return
    registrar_evento('logout', 'Cerró sesión', modelo='Usuario', objeto=user, usuario=user, request=request)


@receiver(user_login_failed)
@_sin_romper_la_operacion
def registrar_login_fallido(sender, credentials, request=None, **kwargs):
    # Django ya enmascara la contraseña en `credentials`; igual solo se usa el usuario.
    nombre = (credentials or {}).get('username', '') or ''
    cuenta = Usuario.objects.filter(username=nombre).first() if nombre else None
    if cuenta:
        descripcion = f'Contraseña incorrecta para el usuario "{nombre}" (intento {cuenta.intentos_fallidos + 1})'
    else:
        descripcion = f'Intento de inicio de sesión con un usuario inexistente: "{nombre}"'
    registrar_evento(
        'login_fallido', descripcion,
        modelo='Usuario', objeto=cuenta, usuario_nombre=nombre, request=request,
    )


# =============================================================================
# REGLAS DE NOTIFICACIÓN
# =============================================================================
# La venta se crea ANTES que sus detalles y antes de actualizar el saldo del
# cliente (todo dentro de transaction.atomic en ventas.views.registrar_venta).
# Por eso las reglas se evalúan con transaction.on_commit: se ejecutan cuando
# la transacción terminó y los detalles/saldo ya están guardados. Si la venta
# falla y se hace rollback, no se genera ninguna notificación.

@receiver(post_save, sender=Ventas)
def notificar_por_venta(sender, instance, created, **kwargs):
    venta_id = instance.pk

    # Regla 1: stock bajo de los productos vendidos (solo en ventas nuevas)
    if created:
        def _verificar_stock():
            productos = Productos.objects.filter(detalles_ventas__venta_id=venta_id).distinct()
            notificaciones.verificar_stock_bajo(productos)
        transaction.on_commit(_verificar_stock)

    # Regla 2: límite de crédito si la venta es (o pasó a ser) fiada
    if instance.estado_de_pago == 'fiado' and instance.id_clientes_id:
        cliente = instance.id_clientes
        transaction.on_commit(lambda: notificaciones.verificar_limite_credito(cliente))


@receiver(post_save, sender=Pago)
def notificar_por_pago(sender, instance, created, **kwargs):
    # Regla 2: tras registrar un pago, verificar si la deuda sigue superando el límite
    if created:
        cliente = instance.cliente
        transaction.on_commit(lambda: notificaciones.verificar_limite_credito(cliente))
