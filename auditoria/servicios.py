"""
Servicio central de auditoría.

Todo registro de auditoría pasa por `registrar_evento`, que completa
automáticamente el usuario, la IP, el navegador y la ruta del request en curso,
y la categoría/nivel según la acción. Así las señales, las vistas y los comandos
registran eventos con el mismo formato.
"""
import ipaddress
import logging
from datetime import date, datetime
from decimal import Decimal

from django.conf import settings
from django.db import transaction
from django.utils import timezone
from django.utils.formats import date_format

from .middleware import get_current_request, get_current_user

logger = logging.getLogger('auditoria')

# Nunca se guardan en la auditoría (ni siquiera el hash).
CAMPOS_SENSIBLES = {'password'}

LARGO_TEXTO_VALOR = 300


# --------------------------------------------------------------------- request
def obtener_ip(request):
    """
    IP del cliente. Solo se confía en X-Forwarded-For si el sistema está detrás
    de un proxy propio (settings.AUDITORIA_CONFIAR_PROXY = True); si no, un
    atacante podría falsificar su IP mandando esa cabecera.
    """
    if request is None:
        return None
    candidata = request.META.get('REMOTE_ADDR', '')
    if getattr(settings, 'AUDITORIA_CONFIAR_PROXY', False):
        reenviada = request.META.get('HTTP_X_FORWARDED_FOR', '')
        if reenviada:
            candidata = reenviada.split(',')[0].strip()
    try:
        return str(ipaddress.ip_address(candidata))
    except ValueError:
        return None


def _contexto_request(request):
    if request is None:
        return {'ip': None, 'user_agent': '', 'ruta': ''}
    ruta = f"{request.method} {request.get_full_path()}"
    return {
        'ip': obtener_ip(request),
        'user_agent': request.META.get('HTTP_USER_AGENT', '')[:255],
        'ruta': ruta[:255],
    }


# ------------------------------------------------------------------- registrar
def registrar_evento(
    accion, descripcion, *, modelo='', objeto=None, objeto_id=None, objeto_repr='',
    categoria=None, nivel=None, cambios=None, datos_anteriores=None, datos_nuevos=None,
    usuario=None, usuario_nombre='', request=None,
):
    """
    Crea un registro de auditoría. Nunca lanza excepciones: si la auditoría falla
    se deja constancia en el log, pero la operación del usuario no se interrumpe.
    """
    from .models import Auditoria

    request = request if request is not None else get_current_request()
    if usuario is None:
        usuario = get_current_user()
    if usuario is not None and not getattr(usuario, 'is_authenticated', False):
        usuario = None

    if objeto is not None:
        objeto_id = objeto_id if objeto_id is not None else objeto.pk
        objeto_repr = objeto_repr or representar(objeto)

    registro = Auditoria(
        usuario=usuario,
        usuario_nombre=(usuario.get_username() if usuario else usuario_nombre or '')[:150],
        accion=accion,
        categoria=categoria or Auditoria.CATEGORIA_POR_ACCION.get(accion, 'datos'),
        nivel=nivel or Auditoria.NIVEL_POR_ACCION.get(accion, 'info'),
        modelo=modelo,
        objeto_id=str(objeto_id) if objeto_id is not None else None,
        objeto_repr=(objeto_repr or '')[:200],
        descripcion=descripcion,
        cambios=cambios or None,
        datos_anteriores=datos_anteriores,
        datos_nuevos=datos_nuevos,
        **_contexto_request(request),
    )
    try:
        # Savepoint propio: si falla, no arruina la transacción de la operación.
        with transaction.atomic():
            registro.save()
    except Exception:
        logger.exception('No se pudo guardar el registro de auditoría: %s', descripcion)
        return None

    if registro.categoria != 'datos' or registro.nivel != 'info':
        nivel_log = {'critico': logging.ERROR, 'advertencia': logging.WARNING}.get(registro.nivel, logging.INFO)
        logger.log(nivel_log, '[%s] %s | usuario=%s ip=%s',
                   registro.get_accion_display(), descripcion, registro.actor, registro.ip or '-')
    return registro


# ------------------------------------------------------ instantáneas y cambios
def representar(instancia, etiqueta=None):
    """Texto corto que identifica al objeto (se guarda tal como era en ese momento)."""
    for atributo in ('username', 'nombre'):
        valor = getattr(instancia, atributo, None)
        if valor:
            apellido = getattr(instancia, 'apellido', None) if atributo == 'nombre' else None
            return f"{valor} {apellido}".strip() if apellido else str(valor)
    if hasattr(instancia, 'lote') and hasattr(instancia, 'id_producto_id'):
        return f"Lote {instancia.lote} de {instancia.id_producto.nombre}"
    etiqueta = etiqueta or str(instancia._meta.verbose_name).capitalize()
    return f"{etiqueta} #{instancia.pk}"


def _campos_auditables(modelo, ignorados=()):
    for campo in modelo._meta.concrete_fields:
        if campo.name in CAMPOS_SENSIBLES or campo.name in ignorados:
            continue
        if campo.primary_key and campo.auto_created:
            continue
        yield campo


def _valor_json(valor):
    if valor is None or isinstance(valor, (bool, int, float, str)):
        return valor
    if isinstance(valor, (list, tuple, set)):
        return [_valor_json(v) for v in valor]
    return str(valor)


def instantanea(instancia, ignorados=()):
    """Datos crudos del objeto (claves foráneas como id) listos para JSON."""
    return {
        campo.attname if campo.is_relation else campo.name: _valor_json(campo.value_from_object(instancia))
        for campo in _campos_auditables(type(instancia), ignorados)
    }


def etiqueta_campo(campo):
    texto = str(campo.verbose_name)
    if texto.lower().startswith('id '):
        texto = texto[3:]
    return texto[:1].upper() + texto[1:]


def _normalizar(campo, valor):
    try:
        return campo.to_python(valor)
    except Exception:
        return valor


def _texto_valor(campo, valor):
    """Valor tal como lo vería un usuario (choices, nombres, fechas, Sí/No)."""
    if valor is None or valor == '':
        return None
    if campo.is_relation:
        relacionado = campo.related_model._default_manager.filter(pk=valor).first()
        texto = representar(relacionado) if relacionado else f"#{valor} (eliminado)"
    elif campo.choices:
        texto = str(dict(campo.flatchoices).get(valor, valor))
    elif isinstance(valor, bool):
        texto = 'Sí' if valor else 'No'
    elif isinstance(valor, datetime):
        texto = date_format(timezone.localtime(valor) if timezone.is_aware(valor) else valor, 'd/m/Y H:i')
    elif isinstance(valor, date):
        texto = date_format(valor, 'd/m/Y')
    elif isinstance(valor, Decimal):
        texto = f"{valor:.2f}"
    else:
        texto = str(valor)
    return texto[:LARGO_TEXTO_VALOR]


def calcular_cambios(anterior, nueva, ignorados=()):
    """
    Diferencias campo a campo entre dos versiones del objeto.
    `anterior` o `nueva` pueden ser None (creación / eliminación).
    """
    modelo = type(nueva if nueva is not None else anterior)
    cambios = {}
    for campo in _campos_auditables(modelo, ignorados):
        antes = _normalizar(campo, campo.value_from_object(anterior)) if anterior is not None else None
        despues = _normalizar(campo, campo.value_from_object(nueva)) if nueva is not None else None
        if antes == despues:
            continue
        texto_antes, texto_despues = _texto_valor(campo, antes), _texto_valor(campo, despues)
        if texto_antes == texto_despues:
            continue
        cambios[campo.name] = {'campo': etiqueta_campo(campo), 'antes': texto_antes, 'despues': texto_despues}
    return cambios
