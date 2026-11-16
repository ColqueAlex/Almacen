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

def requiere_perfil(*perfiles_permitidos):
    def decorator(view_func):
        @wraps(view_func)
        def _wrapped_view(request, *args, **kwargs):
            if not request.user.is_authenticated:
                return redirect('login')
            if request.user.is_superuser:
                return view_func(request, *args, **kwargs)

            perfil_obj = getattr(request.user, 'id_perfil', None)
            perfil_nombre = ''
            if perfil_obj:
                perfil_nombre = (getattr(perfil_obj, 'nombre_perfil', '') or getattr(perfil_obj, 'nombre', '')).lower()

            perfiles_lower = [p.lower() for p in perfiles_permitidos]

            if perfil_nombre in perfiles_lower or request.user.is_staff:
                return view_func(request, *args, **kwargs)
            else:
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