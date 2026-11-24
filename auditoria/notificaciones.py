"""
Lógica de reglas de notificación del sistema.

Se mantiene separada de signals.py para que pueda reutilizarse desde el
management command `alertas_vencimiento` sin registrar receivers dos veces.
Solo importa modelos de `usuarios` y de esta app, así que no genera
importaciones circulares con productos/ventas.
"""
import logging
from datetime import timedelta
from decimal import Decimal

from django.conf import settings
from django.core.mail import send_mail
from django.utils import timezone

from usuarios.models import Perfil
from .models import Configuracion, Notificacion

logger = logging.getLogger(__name__)

PERFIL_ADMIN = 'Administrador'

# Claves de configuración y sus valores por defecto
LIMITE_STOCK_CRITICO = ('limite_stock_critico', 10)
LIMITE_DEUDA_CLIENTE = ('limite_deuda_cliente', Decimal('100000'))
DIAS_ALERTA_VENCIMIENTO = ('dias_alerta_vencimiento', 30)
HORAS_ALERTA_CAJA_ABIERTA = ('horas_alerta_caja_abierta', 12)
DIAS_INACTIVO_PARA_LIMPIEZA = ('dias_inactivo_para_limpieza', 365)


def crear_notificacion(tipo, mensaje, perfil_nombre=PERFIL_ADMIN, prefijo_unico=None):
    """
    Crea una notificación para el perfil indicado.

    Si se pasa `prefijo_unico` y ya existe una notificación NO leída del mismo
    tipo cuyo mensaje empiece con ese prefijo, se actualiza su mensaje en lugar
    de duplicarla. Devuelve (notificacion, creada) o (None, False) si el perfil
    no existe.
    """
    perfil = Perfil.objects.filter(nombre__iexact=perfil_nombre).first()
    if perfil is None:
        logger.warning('No existe el perfil "%s"; no se generó la notificación: %s', perfil_nombre, mensaje)
        return None, False

    if prefijo_unico:
        existente = Notificacion.objects.filter(
            tipo=tipo, destino_perfil=perfil, leida=False, mensaje__startswith=prefijo_unico,
        ).first()
        if existente:
            if existente.mensaje != mensaje:
                existente.mensaje = mensaje
                existente.save(update_fields=['mensaje'])
            return existente, False

    return Notificacion.objects.create(tipo=tipo, mensaje=mensaje, destino_perfil=perfil), True


def enviar_correo_alerta(asunto, mensaje):
    """Envía un correo de alerta al destinatario configurado. Nunca lanza excepciones."""
    destinatario = getattr(settings, 'ALERTAS_EMAIL_DESTINATARIO', '')
    if not destinatario:
        logger.info('ALERTAS_EMAIL_DESTINATARIO no configurado; no se envía correo.')
        return False
    destinatarios = [d.strip() for d in destinatario.split(',') if d.strip()]
    try:
        send_mail(
            subject=f'[El Refugio] {asunto}',
            message=mensaje,
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=destinatarios,
            fail_silently=False,
        )
        return True
    except Exception:
        # Un fallo del servidor de correo no debe romper una venta ya registrada.
        logger.exception('No se pudo enviar el correo de alerta "%s".', asunto)
        return False


# ---------------------------------------------------------------------------
# Regla 1: Stock bajo
# ---------------------------------------------------------------------------
def verificar_stock_bajo(productos):
    limite = Configuracion.obtener(*LIMITE_STOCK_CRITICO, tipo=int)
    for producto in productos:
        stock = producto.stock
        if stock >= limite:
            continue
        prefijo = f'Stock bajo: "{producto.nombre}" (ID {producto.pk})'
        mensaje = f'{prefijo} tiene {stock} unidad(es) disponibles. Límite crítico: {limite}.'
        _, creada = crear_notificacion('stock_bajo', mensaje, prefijo_unico=prefijo)
        if creada:
            # Solo se envía correo la primera vez; mientras la alerta siga sin leer
            # se actualiza el mensaje sin volver a mandar mails.
            enviar_correo_alerta(f'Stock bajo de {producto.nombre}', mensaje)


# ---------------------------------------------------------------------------
# Regla 2: Límite de crédito
# ---------------------------------------------------------------------------
def verificar_limite_credito(cliente):
    if cliente is None:
        return
    cliente.refresh_from_db(fields=['saldo'])
    limite = Configuracion.obtener(*LIMITE_DEUDA_CLIENTE, tipo=Decimal)
    if cliente.saldo <= limite:
        return
    nombre = f'{cliente.nombre} {cliente.apellido or ""}'.strip()
    prefijo = f'Límite de crédito: "{nombre}" (ID {cliente.pk})'
    mensaje = f'{prefijo} tiene una deuda de ${cliente.saldo:.2f}, que supera el límite de ${limite:.2f}.'
    crear_notificacion('limite_credito', mensaje, prefijo_unico=prefijo)


# ---------------------------------------------------------------------------
# Regla 3: Vencimientos (usada por el management command)
# ---------------------------------------------------------------------------
def verificar_vencimientos(dias=None):
    """Genera notificaciones para lotes con stock que vencen dentro de `dias`. Devuelve cuántas creó."""
    from productos.models import LotesProducto 

    if dias is None:
        dias = Configuracion.obtener(*DIAS_ALERTA_VENCIMIENTO, tipo=int)
    hoy = timezone.localdate()
    limite = hoy + timedelta(days=dias)

    lotes = (
        LotesProducto.objects
        .select_related('id_producto')
        .filter(activo=True, stock_actual__gt=0, fecha_vencimiento__isnull=False, fecha_vencimiento__lte=limite)
        .order_by('fecha_vencimiento')
    )

    creadas = 0
    for lote in lotes:
        restantes = (lote.fecha_vencimiento - hoy).days
        prefijo = f'Vencimiento: lote "{lote.lote}" de "{lote.id_producto.nombre}" (ID lote {lote.pk})'
        if restantes < 0:
            detalle = f'venció el {lote.fecha_vencimiento:%d/%m/%Y}'
        elif restantes == 0:
            detalle = 'vence HOY'
        else:
            detalle = f'vence el {lote.fecha_vencimiento:%d/%m/%Y} (en {restantes} día(s))'
        mensaje = f'{prefijo} {detalle}. Stock en el lote: {lote.stock_actual}.'
        _, creada = crear_notificacion('vencimiento', mensaje, prefijo_unico=prefijo)
        creadas += int(creada)
    return creadas


# ---------------------------------------------------------------------------
# Regla 4: Caja abierta demasiado tiempo (probable olvido de cierre)
# ---------------------------------------------------------------------------
def horas_alerta_caja_abierta():
    return Configuracion.obtener(*HORAS_ALERTA_CAJA_ABIERTA, tipo=int)


def verificar_cajas_abiertas():
    """
    Avisa al Administrador por cada caja abierta hace más de N horas
    (configuración "horas_alerta_caja_abierta", 12 por defecto).
    Es barata (una consulta), así que se llama al entrar al inicio y a la caja;
    las alertas no leídas de la misma caja no se duplican. Devuelve cuántas creó.
    """
    from caja.models import Caja

    horas = horas_alerta_caja_abierta()
    limite = timezone.now() - timedelta(hours=horas)
    creadas = 0
    for caja in Caja.objects.select_related('usuario').filter(estado='abierta', fecha_apertura__lte=limite):
        abierta_hace = int((timezone.now() - caja.fecha_apertura).total_seconds() // 3600)
        prefijo = f'Caja abierta: caja #{caja.pk} de "{caja.usuario.username}"'
        mensaje = (f'{prefijo} lleva {abierta_hace} hora(s) abierta '
                   f'(desde el {timezone.localtime(caja.fecha_apertura):%d/%m/%Y %H:%M}). '
                   f'Si el vendedor olvidó cerrarla, un administrador puede forzar el cierre desde Gestión de Caja.')
        _, creada = crear_notificacion('caja_abierta', mensaje, prefijo_unico=prefijo)
        if creada:
            enviar_correo_alerta(f'Caja #{caja.pk} abierta hace {abierta_hace} horas', mensaje)
        creadas += int(creada)
    return creadas


# ---------------------------------------------------------------------------
# Regla 5: Limpieza de catálogo (sugerencia, nunca borra nada)
# ---------------------------------------------------------------------------
PREFIJO_LIMPIEZA = 'Limpieza de catálogo:'


def dias_inactivo_para_limpieza():
    return Configuracion.obtener(*DIAS_INACTIVO_PARA_LIMPIEZA, tipo=int)


def verificar_limpieza_productos():
    """
    Avisa al Administrador cuántos productos llevan más de N días inactivos
    (config "dias_inactivo_para_limpieza", 365 por defecto) sin haber tenido
    ventas, compras ni stock. Mantiene una sola notificación sin leer y le
    actualiza el número. Devuelve la cantidad de candidatos.
    """
    from productos.models import Productos

    dias = dias_inactivo_para_limpieza()
    cantidad = Productos.objects.candidatos_limpieza(dias).count()
    if cantidad:
        mensaje = (f'{PREFIJO_LIMPIEZA} {cantidad} producto(s) llevan más de {dias} días inactivos y nunca '
                   f'tuvieron ventas, compras ni stock. Podés revisarlos y eliminarlos desde Inventario → '
                   f'"Sugeridos para eliminar".')
        crear_notificacion('limpieza_productos', mensaje, prefijo_unico=PREFIJO_LIMPIEZA)
    return cantidad
