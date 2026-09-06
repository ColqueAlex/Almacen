from django.shortcuts import render, redirect, get_object_or_404
from Almacen.models import Productos, Proveedor, Usuario, Perfil
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth import update_session_auth_hash
from django.utils import timezone
from django.core.exceptions import ValidationError


@login_required
def home(request):
    if request.user.debe_cambiar_clave:
        return redirect('cambiar_clave')
    return render(request, "principal.html")


@login_required
def consultar(request):
    if request.user.debe_cambiar_clave:
        return redirect('cambiar_clave')
        
    productos = Productos.objects.all()
    stock_filtro = request.GET.get('stock_filtro', 'todos')
    busqueda = request.GET.get('busqueda', '').strip()

    if busqueda:
        productos = productos.filter(nombre__icontains=busqueda)

    if stock_filtro == 'cero':
        productos = productos.filter(stock=0)
    elif stock_filtro == 'hasta_diez':
        productos = productos.filter(stock__gt=0, stock__lte=10)
    elif stock_filtro == 'mayor_diez':
        productos = productos.filter(stock__gt=10)

    return render(request, "productos.html", {
        'productos': productos,
        'stock_filtro': stock_filtro,
        'busqueda': busqueda
    })


@login_required
def guardar(request):
    nombre = request.POST["nombre"]
    categoria = request.POST["categoria"]
    marca = request.POST["marca"]
    precio = request.POST["precio"]
    fecha_vencimiento = request.POST.get("fecha_vencimiento")
    stock = request.POST["stock"]
    descripcion = request.POST.get("descripcion", "")

    if not fecha_vencimiento:
        messages.error(request, 'La fecha de vencimiento es obligatoria')
        return redirect('consultar')

    if Productos.objects.filter(nombre=nombre).exists():
        messages.error(request, 'Ya existe un producto con ese nombre')
        return redirect('consultar')

    p = Productos(
        nombre=nombre,
        categoria=categoria,
        marca=marca,
        precio=precio,
        fecha_vencimiento=fecha_vencimiento,
        stock=stock,
        descripcion=descripcion,
    )
    try:
        p.full_clean()
        p.save()
    except ValidationError:
        messages.error(request, 'El precio y el stock no pueden ser negativos')
        return redirect('consultar')
    messages.success(request, 'Producto Agregado')
    return redirect('consultar')


@login_required
def eliminar(request, id):
    producto = Productos.objects.filter(pk=id)
    producto.delete()
    messages.success(request, 'Producto eliminado')
    return redirect('consultar')


@login_required
def detalle(request, id):
    producto = Productos.objects.get(pk=id)
    return render(request, "productoEditar.html", {
        'producto': producto
    })


@login_required
def editar(request):
    nombre = request.POST["nombre"]
    categoria = request.POST["categoria"]
    marca = request.POST["marca"]
    precio = request.POST["precio"]
    fecha_vencimiento = request.POST.get("fecha_vencimiento") or None
    stock = request.POST["stock"]
    descripcion = request.POST.get("descripcion", "")
    id = request.POST["id"]
    producto = Productos.objects.get(pk=id)
    producto.nombre = nombre
    producto.categoria = categoria
    producto.marca = marca
    producto.precio = precio
    producto.fecha_vencimiento = fecha_vencimiento
    producto.stock = stock
    producto.descripcion = descripcion
    try:
        producto.full_clean()
        producto.save()
    except ValidationError:
        messages.error(request, 'El precio y el stock no pueden ser negativos')
        return redirect('consultar')
    messages.success(request, 'Producto Actualizado')
    return redirect('consultar')


@login_required
def baja_usuario(request, dni):
    usuario = get_object_or_404(Usuario, dni=dni)
    usuario.activo = False
    usuario.fecha_baja = timezone.now()
    usuario.save()
    messages.success(request, f'Usuario {usuario.username} dado de baja.')
    return redirect('listar_usuarios')


@login_required
def editar_usuario(request, dni):
    usuario = get_object_or_404(Usuario, dni=dni)
    if request.method == 'POST':
        usuario.correo = request.POST.get('correo')
        usuario.save()
        messages.success(request, 'Correo actualizado correctamente.')
        return redirect('listar_usuarios')

    return render(request, 'usuario_form.html', {'usuario': usuario, 'es_edicion': True})


@login_required
def listar_usuarios(request):
    usuarios = Usuario.objects.all()
    return render(request, 'usuarios_list.html', {'usuarios': usuarios})


@login_required
def crear_usuario(request):
    if request.method == 'POST':
        dni = request.POST.get('dni')
        apellido = request.POST.get('apellido')
        nombre = request.POST.get('nombre')
        correo = request.POST.get('correo')
        password = request.POST.get('password')
        perfil_id = request.POST.get('perfil')

        perfil = Perfil.objects.filter(id=perfil_id).first() if perfil_id else None

        usuario = Usuario.objects.create_user(
            dni=dni,
            apellido=apellido,
            nombre=nombre,
            correo=correo,
            password=str(dni),
            id_perfil=perfil
        )

        usuario.debe_cambiar_clave = True
        usuario.save()

        messages.success(request, 'Usuario creado exitosamente.')
        return redirect('listar_usuarios')

    perfiles = Perfil.objects.all()
    return render(request, 'usuario_form.html', {'perfiles': perfiles, 'es_edicion': False})


@login_required
def cambiar_clave(request):
    if request.method == 'POST':
        nueva_clave = request.POST.get('nueva_clave')
        confirmar_clave = request.POST.get('confirmar_clave')

        if nueva_clave != confirmar_clave:
            return render(request, 'cambiar_clave.html', {'error': 'Las contraseñas no coinciden'})

        request.user.set_password(nueva_clave)
        request.user.debe_cambiar_clave = False
        request.user.save()
        
        update_session_auth_hash(request, request.user)
        
        messages.success(request, 'Contraseña actualizada con éxito.')
        return redirect('consultar')

    return render(request, 'cambiar_clave.html')


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

@login_required
def alta_usuario(request, dni):
    usuario = get_object_or_404(Usuario, dni=dni)
    usuario.activo = True
    usuario.fecha_baja = None  
    usuario.save() 
    messages.success(request, f'Usuario {usuario.username} reactivado correctamente.')
    return redirect('listar_usuarios')