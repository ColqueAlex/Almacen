from functools import wraps
from django.shortcuts import redirect
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.core.validators import RegexValidator

# Teléfono: solo dígitos (con un "+" inicial opcional), entre 8 y 15 dígitos.
TELEFONO_REGEX = r'^\+?\d{8,15}$'
MENSAJE_TELEFONO_INVALIDO = (
    'El teléfono solo puede contener números (opcionalmente con "+" al inicio) '
    'y debe tener entre 8 y 15 dígitos.'
)
validar_telefono = RegexValidator(regex=TELEFONO_REGEX, message=MENSAJE_TELEFONO_INVALIDO)

def _tiene_perfil(user, *perfiles_permitidos):
    """True si el usuario es superusuario/staff o su perfil está entre los permitidos."""
    if not getattr(user, 'is_authenticated', False):
        return False
    if user.is_superuser or user.is_staff:
        return True
    perfil_obj = getattr(user, 'id_perfil', None)
    perfil_nombre = ''
    if perfil_obj:
        perfil_nombre = (getattr(perfil_obj, 'nombre_perfil', '') or getattr(perfil_obj, 'nombre', '')).lower()
    return perfil_nombre in [p.lower() for p in perfiles_permitidos]


def requiere_perfil(*perfiles_permitidos):
    def decorator(view_func):
        @wraps(view_func)
        def _wrapped_view(request, *args, **kwargs):
            if not request.user.is_authenticated:
                return redirect('login')
            if _tiene_perfil(request.user, *perfiles_permitidos):
                return view_func(request, *args, **kwargs)
            else:
                # Import diferido: auditoria importa modelos que a su vez usan este módulo.
                from auditoria.servicios import registrar_evento
                registrar_evento(
                    'acceso_denegado',
                    f'Intentó acceder sin permisos a "{request.path}" (requiere: {", ".join(perfiles_permitidos)})',
                    request=request,
                )
                messages.error(request, 'No tiene permisos para acceder a esta sección.')
                return redirect('inicio')
        return _wrapped_view
    return decorator

def _solo_letras_y_espacios(valor):
    return bool(valor) and any(caracter.isalpha() for caracter in valor) and all(caracter.isalpha() or caracter == ' ' for caracter in valor)

def _contiene_letras(valor):
    return any(caracter.isalpha() for caracter in valor)

def _es_telefono_numerico(valor):
    """True si el valor está vacío o cumple el mismo formato que validar_telefono."""
    if not valor:
        return True
    try:
        validar_telefono(valor)
    except ValidationError:
        return False
    return True

# -----------------------------------------------------------------------------
# Normalización de nombres y control de duplicados
# -----------------------------------------------------------------------------
import re as _re
import unicodedata as _unicodedata

# Comillas rectas, tipográficas y angulares.
_COMILLAS = '"\'`´“”‘’«»'
_ESPACIOS = _re.compile(r'\s+')


def normalizar_texto(valor):
    """
    Limpia un nombre antes de guardarlo: quita espacios de más y las comillas
    que lo envuelven. '  "Alfajor   block" ' -> 'Alfajor block'.
    Las comillas internas se respetan: 'Galletitas "Oreo"' queda igual.
    """
    texto = _ESPACIOS.sub(' ', (valor or '')).strip()
    # Comillas que envuelven todo el texto: '"Alfajor block"' (no '"Oreo" y "Toddy"').
    while (len(texto) >= 2 and texto[0] in _COMILLAS and texto[-1] in _COMILLAS
           and not any(c in _COMILLAS for c in texto[1:-1])):
        texto = texto[1:-1].strip()
    # Comilla suelta, sin su pareja, en un extremo: '"Alfajor block'.
    if sum(c in _COMILLAS for c in texto) % 2:
        if texto[:1] in _COMILLAS:
            texto = texto[1:]
        elif texto[-1:] in _COMILLAS:
            texto = texto[:-1]
    return texto.strip()


def clave_comparacion(valor):
    """
    Forma canónica para detectar duplicados: sin mayúsculas, acentos, comillas
    ni espacios repetidos. 'Azúcar "Ledesma"' y 'azucar ledesma' son iguales.
    """
    texto = _unicodedata.normalize('NFKD', normalizar_texto(valor))
    texto = ''.join(c for c in texto if not _unicodedata.combining(c) and c not in _COMILLAS)
    return _ESPACIOS.sub(' ', texto).strip().casefold()


def buscar_duplicado(queryset, excluir_pk=None, **valores):
    """
    Devuelve el primer objeto del queryset cuyos campos coinciden con `valores`
    según `clave_comparacion`, o None. `excluir_pk` permite editar un registro
    sin que choque consigo mismo.
    Se compara en Python para que también detecte registros viejos guardados
    con comillas o espacios de más (el volumen de un almacén lo permite).
    """
    buscado = tuple(clave_comparacion(v) for v in valores.values())
    candidatos = queryset.exclude(pk=excluir_pk) if excluir_pk is not None else queryset
    for fila in candidatos.values_list('pk', *valores.keys()):
        if tuple(clave_comparacion(v) for v in fila[1:]) == buscado:
            return queryset.model._default_manager.get(pk=fila[0])
    return None


# -----------------------------------------------------------------------------
# Importes y cantidades ingresados por el usuario
# -----------------------------------------------------------------------------
from decimal import ROUND_HALF_UP as _ROUND_HALF_UP, Decimal as _Decimal, InvalidOperation as _InvalidOperation

# Las columnas de dinero son DecimalField(max_digits=10, decimal_places=2).
MONTO_MAXIMO = _Decimal('99999999.99')
MONTO_MAXIMO_TEXTO = '$99.999.999,99'
# Tope razonable de unidades por renglón (la columna admite hasta ~2.100 millones).
CANTIDAD_MAXIMA = 1_000_000


def leer_monto(valor, nombre='El monto', permitir_cero=True, maximo=MONTO_MAXIMO):
    """
    Convierte lo que escribió el usuario en un importe válido para la base.
    Devuelve (Decimal redondeado a 2 decimales, None) o (None, mensaje de error).
    Acepta coma decimal ("1500,50").
    """
    texto = str(valor if valor is not None else '').strip()
    if ',' in texto and '.' not in texto:
        texto = texto.replace(',', '.')
    if not texto:
        return None, f'{nombre} es obligatorio.'
    try:
        monto = _Decimal(texto)
    except (_InvalidOperation, ValueError):
        return None, f'{nombre} debe ser un número válido.'
    if not monto.is_finite():
        return None, f'{nombre} debe ser un número válido.'
    if monto < 0 or (monto == 0 and not permitir_cero):
        return None, f'{nombre} debe ser mayor {"o igual " if permitir_cero else ""}a cero.'
    # Se controla el tope ANTES de redondear: quantize falla con números gigantes (1e30).
    if monto > maximo:
        return None, f'{nombre} no puede superar {MONTO_MAXIMO_TEXTO if maximo == MONTO_MAXIMO else maximo}.'
    monto = monto.quantize(_Decimal('0.01'), rounding=_ROUND_HALF_UP)
    if monto > maximo:   # 99999999.999 redondea a 100000000.00
        return None, f'{nombre} no puede superar {MONTO_MAXIMO_TEXTO if maximo == MONTO_MAXIMO else maximo}.'
    return monto, None


def leer_cantidad(valor, nombre='La cantidad', maximo=CANTIDAD_MAXIMA):
    """Entero positivo dentro de un rango razonable. Devuelve (int, None) o (None, error)."""
    try:
        cantidad = int(str(valor).strip())
    except (TypeError, ValueError):
        return None, f'{nombre} debe ser un número entero.'
    if cantidad <= 0:
        return None, f'{nombre} debe ser mayor a cero.'
    if cantidad > maximo:
        return None, f'{nombre} no puede superar {maximo:,}.'.replace(',', '.')
    return cantidad, None
