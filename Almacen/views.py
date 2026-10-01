import json
from django.db.models import ProtectedError
from decimal import Decimal
from datetime import timedelta
from django.shortcuts import render, redirect, get_object_or_404
from Almacen.models import Productos, Proveedor, Usuario, Perfil, Ventas, DetallesVentas
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth import update_session_auth_hash
from django.db.models import Max
from django.db import transaction
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

    has_inactive_products = productos.filter(estado='inactivo').exists()

    return render(request, "productos.html", {
        'productos': productos,
        'stock_filtro': stock_filtro,
        'busqueda': busqueda,
        'has_inactive_products': has_inactive_products,
    })


@login_required
def guardar(request):
    nombre = request.POST["nombre"].strip()
    lote = (request.POST.get("lote") or "").strip() or "Sin lote"
    categoria = request.POST["categoria"].strip()
    marca = (request.POST.get("marca") or "").strip() or "Sin marca"
    precio = request.POST["precio"]
    fecha_vencimiento = request.POST.get("fecha_vencimiento")
    stock = request.POST["stock"]
    estado = request.POST.get("estado", "activo")

    if not fecha_vencimiento:
        messages.error(request, 'La fecha de vencimiento es obligatoria')
        return redirect('consultar')

    if Productos.objects.filter(nombre=nombre, lote=lote).exists():
        messages.error(request, 'Ya existe un producto con ese nombre y lote')
        return redirect('consultar')

    p = Productos(
        nombre=nombre,
        lote=lote,
        categoria=categoria,
        marca=marca,
        precio=precio,
        fecha_vencimiento=fecha_vencimiento,
        stock=stock,
        estado=estado,
    )
    try:
        p.full_clean()
        p.save()
    except ValidationError as e:
        if hasattr(e, 'message_dict') and 'categoria' in e.message_dict:
            messages.error(request, 'La categor?a no puede contener n?meros')
        else:
            messages.error(request, 'El precio y el stock no pueden ser negativos')
        return redirect('consultar')
    messages.success(request, 'Producto Agregado')
    return redirect('consultar')

@login_required
def cambiar_estado(request, id):
    producto = get_object_or_404(Productos, pk=id)
    producto.estado = 'inactivo' if producto.estado == 'activo' else 'activo'
    producto.save()
    messages.success(request, f'Estado del producto actualizado a {producto.get_estado_display()}')
    return redirect('consultar')


@login_required
def ventas(request):
    if request.user.debe_cambiar_clave:
        return redirect('cambiar_clave')

    hoy = timezone.now().date()
    periodo = request.GET.get('periodo', 'todos')

    ventas_qs = Ventas.objects.prefetch_related('detalles__producto').order_by('-fecha', '-hora')

    if periodo == 'semana':
        desde = hoy - timezone.timedelta(days=7)
        ventas_qs = ventas_qs.filter(fecha__gte=desde)
    elif periodo == 'mes':
        desde = hoy - timezone.timedelta(days=30)
        ventas_qs = ventas_qs.filter(fecha__gte=desde)
    elif periodo == 'año':
        desde = hoy - timezone.timedelta(days=365)
        ventas_qs = ventas_qs.filter(fecha__gte=desde)

    fecha_limite = hoy.replace(year=hoy.year - 1)
    return render(request, 'Gstion_Ventas.html', {
        'ventas': ventas_qs,
        'fecha_limite': fecha_limite,
        'periodo': periodo,
    })


@login_required
def registrar_venta(request):
    if request.user.debe_cambiar_clave:
        return redirect('cambiar_clave')

    if request.method == 'POST':
        cart_data = request.POST.get('cart_data', '[]')
        fiado = request.POST.get('fiado', 'false') == 'true'

        try:
            cart_items = json.loads(cart_data)
        except json.JSONDecodeError:
            messages.error(request, 'No se pudo procesar la venta.')
            return redirect('registrar_venta')

        if not cart_items:
            messages.error(request, 'Agrega al menos un producto al carrito.')
            return redirect('registrar_venta')

        consolidated = {}
        for item in cart_items:
            try:
                p_id = int(item.get('id'))
                qty = int(item.get('cantidad', 0) or 0)
            except (ValueError, TypeError):
                continue
            if p_id and qty > 0:
                consolidated[p_id] = consolidated.get(p_id, 0) + qty

        if not consolidated:
            messages.error(request, 'La venta no tiene productos válidos.')
            return redirect('registrar_venta')

        venta_total = Decimal('0.00')
        items_para_guardar = []

        for producto_id, cantidad in consolidated.items():
            producto = get_object_or_404(Productos, pk=producto_id)
            if producto.estado != 'activo':
                messages.error(request, f'El producto {producto.nombre} no está activo para la venta.')
                return redirect('registrar_venta')

            nombre_limpio = producto.nombre.strip()
            lotes = list(Productos.objects.filter(
                nombre__iexact=nombre_limpio,
                estado='activo',
                stock__gt=0
            ).order_by('fecha_vencimiento', 'id'))

            # Fallback: si iexact no encuentra nada por espacios en DB, buscar con TRIM
            if not lotes:
                from django.db.models.functions import Trim
                lotes = list(Productos.objects.annotate(
                    nombre_trim=Trim('nombre')
                ).filter(
                    nombre_trim__iexact=nombre_limpio,
                    estado='activo',
                    stock__gt=0
                ).order_by('fecha_vencimiento', 'id'))

            stock_total = sum(int(l.stock) for l in lotes)
            if cantidad > stock_total:
                messages.error(request, f'No hay stock suficiente para {producto.nombre}.')
                return redirect('registrar_venta')

            cantidad_restante = cantidad
            for lote in lotes:
                if cantidad_restante <= 0:
                    break
                deducir = min(int(lote.stock), cantidad_restante)
                subtotal = Decimal(str(lote.precio)) * deducir
                venta_total += subtotal
                items_para_guardar.append((lote, deducir, subtotal))
                cantidad_restante -= deducir

        with transaction.atomic():
            ultima_venta = Ventas.objects.aggregate(max_id=Max('id_ventas'))['max_id'] or 0
            ultima_detalle = DetallesVentas.objects.aggregate(max_id=Max('id_detalles_ventas'))['max_id'] or 0

            venta = Ventas.objects.create(
                id_ventas=ultima_venta + 1,
                id_caja=1,
                total=venta_total,
                fecha=timezone.now().date(),
                hora=timezone.now().time(),
                id_clientes=0,
                estado_de_pago='fiado' if fiado else 'pagado',
            )

            for producto, cantidad, subtotal in items_para_guardar:
                ultima_detalle += 1
                DetallesVentas.objects.create(
                    id_detalles_ventas=ultima_detalle,
                    venta=venta,
                    producto=producto,
                    cantidad=cantidad,
                    precio_unitario_compra=Decimal(str(producto.precio)),
                    subtotal=subtotal,
                )
                producto.stock -= cantidad
                producto.save()

        messages.success(request, 'Venta registrada con éxito.')
        return redirect('ventas')

    productos = Productos.objects.filter(estado='activo').order_by('nombre')
    categorias = sorted({producto.categoria.strip() for producto in productos if producto.categoria and producto.categoria.strip()})
    agrupados = {}

    for producto in productos:
        clave = producto.nombre.strip().lower()
        if clave not in agrupados:
            agrupados[clave] = {
                'id': producto.id,
                'nombre': producto.nombre,
                'categoria': producto.categoria,
                'precio': producto.precio,
                'stock': int(producto.stock),
            }
        else:
            agrupados[clave]['stock'] += int(producto.stock)

    productos_agrupados = sorted(
        agrupados.values(),
        key=lambda item: item['nombre'].lower()
    )

    return render(request, 'Registro_ventas.html', {
        'productos': productos_agrupados,
        'categorias': categorias,
    })


@login_required
def eliminar_venta(request, id_venta):
    if request.method != 'POST':
        return redirect('ventas')

    venta = get_object_or_404(Ventas, pk=id_venta)
    hace_un_año = timezone.now().date().replace(year=timezone.now().date().year - 1)

    if venta.fecha > hace_un_año:
        messages.error(request, f'La venta #{id_venta} no puede eliminarse: tiene menos de 1 año de antigüedad.')
        return redirect('ventas')

    venta.detalles.all().delete()
    venta.delete()
    messages.success(request, f'Venta #{id_venta} y sus detalles eliminados correctamente.')
    return redirect('ventas')


@login_required
def cambiar_estado_venta(request, id_venta):
    if request.method != 'POST':
        return redirect('ventas')

    venta = get_object_or_404(Ventas, pk=id_venta)
    nuevo_estado = request.POST.get('estado_de_pago', '').strip()
    estados_validos = ['pagado', 'fiado']

    if nuevo_estado not in estados_validos:
        messages.error(request, 'Estado de pago no válido.')
        return redirect('ventas')

    venta.estado_de_pago = nuevo_estado
    venta.save()
    messages.success(request, f'Venta #{id_venta} actualizada a "{nuevo_estado.capitalize()}".')
    return redirect('ventas')


@login_required
def detalle(request, id):
    producto = Productos.objects.get(pk=id)
    return render(request, "productoEditar.html", {
        'producto': producto
    })


@login_required
def eliminar_seleccionados(request):
    if request.method != 'POST':
        return redirect('consultar')

    ids_raw = request.POST.get('selected_ids', '')
    ids = [item.strip() for item in ids_raw.split(',') if item.strip()]

    if not ids:
        messages.warning(request, 'No hay productos marcados para eliminar.')
        return redirect('consultar')

    productos = Productos.objects.filter(id__in=ids, estado='inactivo')

    if not productos.exists():
        messages.warning(request, 'Solo se pueden eliminar productos inactivos.')
        return redirect('consultar')

    eliminados = 0
    bloqueados = []

    for producto in productos:
        try:
            producto.delete()
            eliminados += 1
        except ProtectedError:
            bloqueados.append(producto.nombre)

    if eliminados:
        messages.success(request, f'Se eliminaron {eliminados} producto(s) correctamente.')

    if bloqueados:
        nombres = ', '.join(bloqueados)
        messages.error(
            request,
            f'No se pudo eliminar: {nombres}. '
            f'{"Este producto tiene" if len(bloqueados) == 1 else "Estos productos tienen"} '
            f'ventas registradas asociadas y no pueden borrarse.'
        )

    return redirect('consultar')


@login_required
def editar(request):
    nombre = request.POST["nombre"].strip()
    lote = (request.POST.get("lote") or "").strip() or "Sin lote"
    categoria = request.POST["categoria"].strip()
    marca = (request.POST.get("marca") or "").strip() or "Sin marca"
    precio = request.POST["precio"]
    fecha_vencimiento = request.POST.get("fecha_vencimiento") or None
    stock = request.POST["stock"]
    estado = request.POST.get("estado", "activo")
    id = request.POST["id"]
    producto = Productos.objects.get(pk=id)
    producto.nombre = nombre
    producto.lote = lote
    producto.categoria = categoria
    producto.marca = marca
    producto.precio = precio
    producto.fecha_vencimiento = fecha_vencimiento
    producto.stock = stock
    producto.estado = estado
    try:
        producto.full_clean()
        producto.save()
    except ValidationError as e:
        if hasattr(e, 'message_dict') and 'categoria' in e.message_dict:
            messages.error(request, 'La categor?a no puede contener n?meros')
        else:
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
        perfil_id = request.POST.get('perfil')
        usuario.id_perfil = Perfil.objects.filter(id=perfil_id).first() if perfil_id else None
        usuario.save()
        messages.success(request, 'Usuario actualizado correctamente.')
        return redirect('listar_usuarios')

    perfiles = Perfil.objects.all()
    return render(request, 'usuario_form.html', {
        'usuario': usuario,
        'perfiles': perfiles,
        'es_edicion': True,
    })


@login_required
def listar_usuarios(request):
    usuarios = Usuario.objects.all()
    return render(request, 'usuarios_list.html', {'usuarios': usuarios})


@login_required
def crear_usuario(request):
    if request.method == 'POST':
        dni = request.POST.get('dni')
        apellido = (request.POST.get('apellido') or '').strip()
        nombre = (request.POST.get('nombre') or '').strip()
        correo = (request.POST.get('correo') or '').strip()
        password = request.POST.get('password')
        perfil_id = request.POST.get('perfil')

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

        messages.success(request, 'Usuario creado exitosamente.')
        return redirect('listar_usuarios')

    perfiles = Perfil.objects.all()
    return render(request, 'usuario_form.html', {'perfiles': perfiles, 'es_edicion': False})

@login_required
def cambiar_clave(request):
    if request.method == 'POST':
        nueva_clave = request.POST.get('nueva_clave')
        confirmar_clave = request.POST.get('confirmar_clave')

        if not nueva_clave or not confirmar_clave:
            return render(request, 'cambiar_clave.html', {'error': 'La contrase?a no puede estar vac?a'})

        if nueva_clave != confirmar_clave:
            return render(request, 'cambiar_clave.html', {'error': 'Las contrase?as no coinciden'})

        request.user.set_password(nueva_clave)
        request.user.debe_cambiar_clave = False
        request.user.save()

        update_session_auth_hash(request, request.user)

        messages.success(request, 'Contrase?a actualizada con ?xito.')
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

def lista_ventas(request):
    filtro = request.GET.get('filtro', 'todos')
    ventas = Ventas.objects.all().order_by('-fecha', '-hora')
    hoy = timezone.now().date()

    if filtro == 'semana':
        hace_una_semana = hoy - timedelta(days=7)
        ventas = ventas.filter(fecha__gte=hace_una_semana)

    elif filtro == 'mes':
        hace_un_mes = hoy - timedelta(days=30)
        ventas = ventas.filter(fecha__gte=hace_un_mes)

    elif filtro == 'anio':
        ventas = ventas.filter(fecha__year=hoy.year)

    context = {'ventas': ventas, 'filtro_activo': filtro}
    return render(request, 'tu_app/ventas.html', context)
