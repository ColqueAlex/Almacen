from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.contrib.auth import update_session_auth_hash, authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.utils import timezone
from datetime import timedelta
from django.core.validators import validate_email
from django.core.exceptions import ValidationError
from django_email_validators import validate_email_mx
from .models import Usuario, Perfil
from productos.models import LotesProducto
from .utils import requiere_perfil, _solo_letras_y_espacios

@login_required
def home(request):
    if not request.user.activo:
        logout(request)
        messages.error(request, 'Tu cuenta ha sido dada de baja.')
        return redirect('login')

    if request.user.debe_cambiar_clave:
        return redirect('cambiar_clave')
        
    hoy = timezone.now().date()
    limite = hoy + timedelta(days=30)
    
    alertas = LotesProducto.objects.filter(
        activo=True, 
        stock_actual__gt=0, 
        fecha_vencimiento__isnull=False,
        fecha_vencimiento__lte=limite
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
                messages.error(request, 'Cuenta inactiva o bloqueada permanentemente. Contacte al administrador.')
                return redirect('login')

            if user_exists.bloqueo_hasta and user_exists.bloqueo_hasta > timezone.now():
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
                    messages.error(request, 'Ha excedido el límite máximo de intentos. Su cuenta ha sido bloqueada permanentemente. Contacte al administrador.')
                
                elif user_exists.intentos_fallidos == 5:
                    user_exists.bloqueo_hasta = timezone.now() + timedelta(minutes=1)
                    messages.error(request, 'Demasiados intentos fallidos. Su cuenta ha sido bloqueada temporalmente por 1 minutos.')
                
                elif user_exists.intentos_fallidos > 5:
                    intentos_restantes = 10 - user_exists.intentos_fallidos
                    messages.error(request, f'Usuario o contraseña incorrectos. ATENCIÓN: Le quedan {intentos_restantes} intentos antes del bloqueo PERMANENTE de la cuenta.')
                
                else:
                    intentos_restantes = 5 - user_exists.intentos_fallidos
                    messages.error(request, f'Usuario o contraseña incorrectos. Le quedan {intentos_restantes} intentos antes del bloqueo temporal.')
                
                user_exists.save()
            else:
                messages.error(request, 'Usuario o contraseña incorrectos.')

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

        messages.success(request, 'Contraseña actualizada con éxito.')
        return redirect('consultar')

    return render(request, 'cambiar_clave.html')

@login_required
@requiere_perfil('Administrador')
def listar_usuarios(request):
    usuarios = Usuario.objects.all().order_by('nombre')
    
    total_usuarios = usuarios.count()
    usuarios_activos = usuarios.filter(activo=True).count()
    usuarios_inactivos = usuarios.filter(activo=False).count()

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
                'perfiles': Perfil.objects.all(),
                'es_edicion': False,
                'dni': dni,
                'apellido': apellido,
                'nombre': nombre,
                'correo': correo,
                'perfil_id': perfil_id,
            })

        if Usuario.objects.filter(dni=dni).exists():
            messages.error(request, f'Ya existe un usuario registrado con el DNI {dni}.')
            return render(request, 'usuarios/usuario_form.html', {
                'perfiles': Perfil.objects.all(),
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

    perfiles = Perfil.objects.all()
    return render(request, 'usuarios/usuario_form.html', {'perfiles': perfiles, 'es_edicion': False})

@login_required
@requiere_perfil('Administrador')
def editar_usuario(request, dni):
    usuario = get_object_or_404(Usuario, dni=dni)
    if request.method == 'POST':
        usuario.correo = request.POST.get('correo')
        perfil_id = request.POST.get('perfil')
        usuario.id_perfil = Perfil.objects.filter(id=perfil_id).first() if perfil_id else None
        usuario.save()
        messages.success(request, 'Usuario actualizado correctamente.')
        return redirect('listar_usuarios')

    perfiles = Perfil.objects.all()
    return render(request, 'usuarios/usuario_form.html', {
        'usuario': usuario,
        'perfiles': perfiles,
        'es_edicion': True,
    })

@login_required
@requiere_perfil('Administrador')
def baja_usuario(request, dni):
    usuario_destino = get_object_or_404(Usuario, dni=dni)
    usuario_actual = request.user

    if usuario_actual.dni == usuario_destino.dni:
        messages.error(request, 'No puedes darte de baja a ti mismo.')
        return redirect('listar_usuarios')

    rol_actual = usuario_actual.id_perfil.nombre if usuario_actual.id_perfil else ''
    rol_destino = usuario_destino.id_perfil.nombre if usuario_destino.id_perfil else ''

    if rol_actual not in ['Administrador', 'Propietario']:
        messages.error(request, 'No tienes permisos para dar de baja a ningún usuario.')
        return redirect('listar_usuarios')

    if rol_actual == rol_destino:
        messages.error(request, f'No puedes dar de baja a otro usuario con el mismo rango ({rol_actual}).')
        return redirect('listar_usuarios')

    usuario_destino.activo = False
    usuario_destino.fecha_baja = timezone.now()
    usuario_destino.save()
    messages.success(request, f'Usuario {usuario_destino.username} dado de baja correctamente.')
    return redirect('listar_usuarios')

@login_required
def alta_usuario(request, dni):
    usuario = get_object_or_404(Usuario, dni=dni)
    usuario.activo = True
    usuario.fecha_baja = None
    usuario.intentos_fallidos = 0
    usuario.bloqueo_hasta = None
    usuario.save()
    messages.success(request, f'Usuario {usuario.username} reactivado correctamente.')
    return redirect('listar_usuarios')

@login_required
def restablecer_clave(request, dni):
    if request.method == 'POST':
        usuario = get_object_or_404(Usuario, dni=dni)
        clave_temporal = str(usuario.dni)
        usuario.set_password(str(usuario.dni))
        usuario.debe_cambiar_clave = True
        usuario.save()
        messages.success(
            request, 
            f'Clave restablecida para {usuario.username}. Nueva clave temporal: {clave_temporal}'
        )
    return redirect('listar_usuarios')