from functools import wraps
from django.shortcuts import redirect
from django.contrib import messages

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
    return not valor or (valor.isascii() and valor.isdigit())