from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.contrib.auth import update_session_auth_hash, authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.views.decorators.http import require_POST
from django.db.models import Count, Q
from django.utils import timezone
from datetime import timedelta
from django.core.validators import validate_email
from django.core.exceptions import ValidationError
from django_email_validators import validate_email_mx
from .models import Usuario, Perfil
from productos.models import LotesProducto
from .cache import perfiles_disponibles
from .utils import requiere_perfil, _solo_letras_y_espacios
from auditoria.servicios import registrar_evento
from auditoria.notificaciones import verificar_cajas_abiertas, verificar_limpieza_productos

MENSAJE_LOGIN_INCORRECTO = (
    'Usuario o contraseña incorrectos. Tras 5 intentos fallidos la cuenta se bloquea por 1 minuto '
    'y tras 10, de forma permanente.'
)

@login_required
def home(request):
    if not request.user.activo:
        logout(request)
        messages.error(request, 'Tu cuenta ha sido dada de baja.')
        return redirect('login')

    if request.user.debe_cambiar_clave:
        return redirect('cambiar_clave')

    # Avisos al administrador: cajas abiertas demasiado tiempo y productos
    # inactivos sin movimientos que se podrían eliminar.
    verificar_cajas_abiertas()
    verificar_limpieza_productos()

    hoy = timezone.localdate()
    limite = hoy + timedelta(days=30)

    # Lotes que vencen en los próximos 30 días (los ya vencidos no se venden y
    # se ven en el inventario), solo de productos activos.
    alertas = LotesProducto.objects.select_related('id_producto').filter(
        activo=True,
        stock_actual__gt=0,
        id_producto__estado='activo',
        fecha_vencimiento__gte=hoy,
        fecha_vencimiento__lte=limite,
    ).order_by('fecha_vencimiento')[:3]

    return render(request, "principal.html", {
        'alertas_vencimiento': alertas
    })

def login_usuario(request):
    if request.user.is_authenticated:
        return redirect('inicio')

    if request.method == 'POST':
        username = request.POST.get('username', '').strip()
        password = request.POST.get('password', '').strip()

        user_exists = Usuario.objects.filter(username=username).first()

        if user_exists:
            if not user_exists.activo:
                registrar_evento(
                    'login_fallido', f'Intento de inicio de sesión en la cuenta inactiva "{username}"',
                    modelo='Usuario', objeto=user_exists, usuario_nombre=username, request=request,
                )
                messages.error(request, 'Cuenta inactiva o bloqueada permanentemente. Contacte al administrador.')
                return redirect('login')

            if user_exists.bloqueo_hasta and user_exists.bloqueo_hasta > timezone.now():
                registrar_evento(
                    'login_fallido', f'Intento de inicio de sesión en la cuenta bloqueada temporalmente "{username}"',
                    modelo='Usuario', objeto=user_exists, usuario_nombre=username, request=request,
                )
                tiempo_restante = user_exists.bloqueo_hasta - timezone.now()
                minutos = int(tiempo_restante.total_seconds() // 60)
                segundos = int(tiempo_restante.total_seconds() % 60)
                messages.error(request, f'Cuenta bloqueada temporalmente por seguridad. Intente de nuevo en {minutos}m {segundos}s.')
                return redirect('login')

        user = authenticate(request, username=username, password=password)

        if user is not None:
            user.intentos_fallidos = 0
            user.bloqueo_hasta = None
            user.save()
            
            login(request, user)
            if user.debe_cambiar_clave:
                return redirect('cambiar_clave')
            return redirect('inicio')
        else:
            if user_exists:
                user_exists.intentos_fallidos += 1
                
                if user_exists.intentos_fallidos >= 10:
                    user_exists.activo = False
                    registrar_evento(
                        'bloqueo_cuenta',
                        f'Cuenta "{username}" bloqueada PERMANENTEMENTE tras {user_exists.intentos_fallidos} intentos fallidos',
                        modelo='Usuario', objeto=user_exists, usuario_nombre=username, request=request,
                    )
                    messages.error(request, 'Ha excedido el límite máximo de intentos. Su cuenta ha sido bloqueada permanentemente. Contacte al administrador.')
                
                elif user_exists.intentos_fallidos == 5:
                    user_exists.bloqueo_hasta = timezone.now() + timedelta(minutes=1)
                    registrar_evento(
                        'bloqueo_cuenta',
                        f'Cuenta "{username}" bloqueada temporalmente (1 minuto) tras 5 intentos fallidos',
                        modelo='Usuario', objeto=user_exists, usuario_nombre=username, request=request,
                        nivel='advertencia',
                    )
                    messages.error(request, 'Demasiados intentos fallidos. Su cuenta ha sido bloqueada temporalmente por 1 minutos.')
                
                else:
                    # Mismo mensaje que para un usuario inexistente: así no se
                    # puede averiguar qué nombres de usuario existen.
                    messages.error(request, MENSAJE_LOGIN_INCORRECTO)

                user_exists.save()
            else:
                messages.error(request, MENSAJE_LOGIN_INCORRECTO)

            return redirect('login')

    return render(request, 'login.html')

@login_required
def cambiar_clave(request):
    if request.method == 'POST':
        nueva_clave = request.POST.get('nueva_clave', '').strip()
        confirmar_clave = request.POST.get('confirmar_clave', '').strip()

        if not nueva_clave or not confirmar_clave:
            return render(request, 'cambiar_clave.html', {'error': 'La contraseña no puede estar vacía.'})

        if nueva_clave != confirmar_clave:
            return render(request, 'cambiar_clave.html', {'error': 'Las contraseñas no coinciden.'})

        if nueva_clave == str(request.user.dni):
            return render(request, 'cambiar_clave.html', {
                'error': 'Por Seguridad debe ser otra clave.'
            })

        request.user.set_password(nueva_clave)
        request.user.debe_cambiar_clave = False
        request.user.save()

        update_session_auth_hash(request, request.user)
        registrar_evento('cambio_clave', 'Cambió su contraseña', modelo='Usuario', objeto=request.user, request=request)

        messages.success(request, 'Contraseña actualizada con éxito.')
        # Al inicio y no a Productos: un vendedor no tiene acceso al inventario.
        return redirect('inicio')

    return render(request, 'cambiar_clave.html')

@login_required
@requiere_perfil('Administrador')
def listar_usuarios(request):
    usuarios = Usuario.objects.select_related('id_perfil').order_by('nombre')

    totales = Usuario.objects.aggregate(
        total=Count('pk'),
        activos=Count('pk', filter=Q(activo=True)),
        inactivos=Count('pk', filter=Q(activo=False)),
    )
    total_usuarios = totales['total']
    usuarios_activos = totales['activos']
    usuarios_inactivos = totales['inactivos']

    return render(request, 'usuarios/usuarios_list.html', {
        'usuarios': usuarios,
        'total_usuarios': total_usuarios,
        'usuarios_activos': usuarios_activos,
        'usuarios_inactivos': usuarios_inactivos,
    })

@login_required
@requiere_perfil('Administrador')
def crear_usuario(request):
    if request.method == 'POST':
        dni = request.POST.get('dni', '').strip()
        apellido = (request.POST.get('apellido') or '').strip()
        nombre = (request.POST.get('nombre') or '').strip()
        correo = (request.POST.get('correo') or '').strip()
        perfil_id = request.POST.get('perfil')

        error = None
        
        if not dni.isdigit() or len(dni) != 8:
            error = 'El DNI debe contener exactamente 8 números, sin puntos ni espacios.'
        elif not _solo_letras_y_espacios(apellido):
            error = 'El apellido solo puede contener letras y espacios.'
        elif not _solo_letras_y_espacios(nombre):
            error = 'El nombre solo puede contener letras y espacios.'
        elif not correo:
            error = 'El correo electrónico es obligatorio.'
            
        elif not error:
            try:
                validate_email(correo)
                validate_email_mx(correo)
            except ValidationError:
                error = 'El correo electrónico no es válido o el dominio no existe (ej: gmail.com, hotmail.com).'
            except Exception:
                error = 'No se pudo verificar el correo por un problema de conexión. Intentá de nuevo.'

        if error:
            messages.error(request, error)
            return render(request, 'usuarios/usuario_form.html', {
                'perfiles': perfiles_disponibles(),
                'es_edicion': False,
                'dni': dni,
                'apellido': apellido,
                'nombre': nombre,
                'correo': correo,
                'perfil_id': perfil_id,
            })

        duplicado = None
        if Usuario.objects.filter(dni=dni).exists():
            duplicado = f'Ya existe un usuario registrado con el DNI {dni}.'
        elif Usuario.objects.filter(correo__iexact=correo).exists():
            duplicado = f'Ya existe un usuario registrado con el correo {correo}.'
        if duplicado:
            messages.error(request, duplicado)
            return render(request, 'usuarios/usuario_form.html', {
                'perfiles': perfiles_disponibles(),
                'es_edicion': False,
                'dni': dni,
                'apellido': apellido,
                'nombre': nombre,
                'correo': correo,
                'perfil_id': perfil_id,
            })

        password = request.POST.get('password') or str(dni)
        perfil = Perfil.objects.filter(id=perfil_id).first() if perfil_id else None

        usuario = Usuario.objects.create_user(
            dni=dni,
            apellido=apellido,
            nombre=nombre,
            correo=correo,
            password=password,
            id_perfil=perfil,
        )

        usuario.debe_cambiar_clave = True
        usuario.save()

        messages.success(request, f'Usuario {usuario.username} creado exitosamente.')
        return redirect('listar_usuarios')

    perfiles = perfiles_disponibles()
    return render(request, 'usuarios/usuario_form.html', {'perfiles': perfiles, 'es_edicion': False})

@login_required
@requiere_perfil('Administrador')
def editar_usuario(request, dni):
    usuario = get_object_or_404(Usuario, dni=dni)
    if request.method == 'POST':
        correo = (request.POST.get('correo') or '').strip()
        error = None
        try:
            validate_email(correo)
        except ValidationError:
            error = 'El correo electrónico no es válido.'
        if not error and Usuario.objects.filter(correo__iexact=correo).exclude(pk=usuario.pk).exists():
            error = f'Ya existe otro usuario registrado con el correo {correo}.'
        if error:
            messages.error(request, error)
            return render(request, 'usuarios/usuario_form.html', {
                'usuario': usuario,
                'perfiles': perfiles_disponibles(),
                'es_edicion': True,
                'motivo_rol_bloqueado': _error_gestion_usuario(request.user, usuario, 'cambiarle el rol'),
            })

        # Se editan solo el correo y el perfil: DNI, nombre y apellido no se
        # tocan aunque lleguen en el POST.
        campos = ['correo']
        perfil_id = request.POST.get('perfil')
        if perfil_id is not None:
            nuevo_perfil = Perfil.objects.filter(id=perfil_id).first() if perfil_id else None
            if nuevo_perfil != usuario.id_perfil:
                # Cambiar el rol sigue las mismas reglas que dar de baja: no a uno
                # mismo ni a alguien del mismo rango o superior.
                error = _error_gestion_usuario(request.user, usuario, 'cambiarle el rol')
                if error:
                    return _rechazar_gestion(request, usuario, error)
                usuario.id_perfil = nuevo_perfil
                campos.append('id_perfil')

        usuario.correo = correo
        usuario.save(update_fields=campos)
        messages.success(request, 'Usuario actualizado correctamente.')
        return redirect('listar_usuarios')

    perfiles = perfiles_disponibles()
    return render(request, 'usuarios/usuario_form.html', {
        'usuario': usuario,
        'perfiles': perfiles,
        'es_edicion': True,
        # Si no se le puede cambiar el rol, el selector se muestra bloqueado con el motivo.
        'motivo_rol_bloqueado': _error_gestion_usuario(request.user, usuario, 'cambiarle el rol'),
    })

def _error_gestion_usuario(usuario_actual, usuario_destino, accion):
    """
    Reglas comunes para dar de baja, reactivar o restablecer la clave de otro usuario.
    Devuelve el mensaje de error, o None si la operación está permitida.
    """
    if usuario_actual.dni == usuario_destino.dni:
        return f'No puedes {accion} a ti mismo.'
    if usuario_actual.is_superuser:
        return None

    rol_actual = usuario_actual.id_perfil.nombre if usuario_actual.id_perfil else ''
    rol_destino = usuario_destino.id_perfil.nombre if usuario_destino.id_perfil else ''

    if rol_actual not in ['Administrador', 'Propietario']:
        return f'No tienes permisos para {accion} a ningún usuario.'
    if usuario_destino.is_superuser or rol_actual == rol_destino:
        return f'No puedes {accion} a otro usuario con el mismo rango o superior ({rol_destino or "superusuario"}).'
    return None


def _rechazar_gestion(request, usuario_destino, mensaje):
    registrar_evento(
        'acceso_denegado', f'Intento rechazado sobre el usuario "{usuario_destino.username}": {mensaje}',
        modelo='Usuario', objeto=usuario_destino, request=request,
    )
    messages.error(request, mensaje)
    return redirect('listar_usuarios')


@login_required
@requiere_perfil('Administrador')
@require_POST
def baja_usuario(request, dni):
    usuario_destino = get_object_or_404(Usuario, dni=dni)
    error = _error_gestion_usuario(request.user, usuario_destino, 'dar de baja')
    if error:
        return _rechazar_gestion(request, usuario_destino, error)

    usuario_destino.activo = False
    usuario_destino.fecha_baja = timezone.now()
    usuario_destino.save()
    messages.success(request, f'Usuario {usuario_destino.username} dado de baja correctamente.')
    return redirect('listar_usuarios')

@login_required
@requiere_perfil('Administrador')
@require_POST
def alta_usuario(request, dni):
    usuario = get_object_or_404(Usuario, dni=dni)
    error = _error_gestion_usuario(request.user, usuario, 'reactivar')
    if error:
        return _rechazar_gestion(request, usuario, error)

    usuario.activo = True
    usuario.fecha_baja = None
    usuario.intentos_fallidos = 0
    usuario.bloqueo_hasta = None
    usuario.save()
    messages.success(request, f'Usuario {usuario.username} reactivado correctamente.')
    return redirect('listar_usuarios')

@login_required
@requiere_perfil('Administrador')
@require_POST
def restablecer_clave(request, dni):
    usuario = get_object_or_404(Usuario, dni=dni)
    error = _error_gestion_usuario(request.user, usuario, 'restablecer la clave')
    if error:
        return _rechazar_gestion(request, usuario, error)

    clave_temporal = str(usuario.dni)
    usuario.set_password(clave_temporal)
    usuario.debe_cambiar_clave = True
    usuario.save()
    # Nunca se registra la clave temporal, solo que se restableció.
    registrar_evento(
        'restablecer_clave',
        f'Restableció la contraseña del usuario "{usuario.username}" (deberá cambiarla al ingresar)',
        modelo='Usuario', objeto=usuario, request=request,
    )
    messages.success(
        request,
        f'Clave restablecida para {usuario.username}. Nueva clave temporal: {clave_temporal}'
    )
    return redirect('listar_usuarios')