from django.shortcuts import render, redirect, get_object_or_404
from django.db import transaction
from django.urls import reverse
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.views.decorators.http import require_POST
from datetime import timedelta
from django.db.models import Count, F, Prefetch, ProtectedError, Q
from django.core.paginator import Paginator
from django.core.exceptions import ValidationError
from django.utils import timezone
from .cache import categorias_productos
from auditoria.notificaciones import dias_inactivo_para_limpieza
from .models import LotesProducto, Productos, Proveedor, filtro_lotes_vigentes
from usuarios.utils import (
    requiere_perfil, _contiene_letras, _es_telefono_numerico, MENSAJE_TELEFONO_INVALIDO,
    buscar_duplicado, leer_monto, normalizar_texto,
)

# Un producto con stock entre 1 y este valor se considera "stock bajo".
STOCK_BAJO_MAXIMO = 10
# Un lote que vence dentro de esta cantidad de días se marca "Por vencer".
DIAS_POR_VENCER = 30
# Opciones del filtro "Mostrar" del inventario.
VISTAS_INVENTARIO = ('activos', 'inactivos', 'todos', 'limpieza')


def _error_producto_duplicado(nombre, marca, excluir_pk=None):
    """Mensaje de error si ya existe otro producto con el mismo nombre y marca."""
    existente = buscar_duplicado(Productos.objects.all(), excluir_pk=excluir_pk, nombre=nombre, marca=marca)
    if existente:
        return (f'Ya existe el producto "{existente.nombre}" de marca "{existente.marca}" '
                f'(#ART-{existente.pk:05d}) en el catálogo.')
    return None


def _error_proveedor_duplicado(nombre, excluir_pk=None):
    existente = buscar_duplicado(Proveedor.objects.all(), excluir_pk=excluir_pk, nombre=nombre)
    if existente:
        return f'Ya existe un proveedor registrado con el nombre "{existente.nombre}".'
    return None

@login_required
@requiere_perfil('Administrador')
def consultar(request):
    if request.user.debe_cambiar_clave:
        return redirect('cambiar_clave')

    busqueda = request.GET.get('busqueda', '').strip()
    cat_filtro = request.GET.get('categoria', '')
    estado_filtro = request.GET.get('estado_stock', '')
    # Qué productos se listan: por defecto solo los activos.
    ver = request.GET.get('ver', 'activos')
    if ver not in VISTAS_INVENTARIO:
        ver = 'activos'

    productos_qs = Productos.objects.all()
    if busqueda:
        productos_qs = productos_qs.filter(Q(nombre__icontains=busqueda) | Q(marca__icontains=busqueda))
    if cat_filtro:
        productos_qs = productos_qs.filter(categoria__iexact=cat_filtro)

    # Stock vigente calculado en la base de datos (una sola consulta, sin N+1).
    productos_qs = productos_qs.con_stock()

    # Los KPIs de stock miran solo productos activos: que un producto dado de
    # baja esté "sin stock" no es una alerta.
    activo = Q(estado='activo')
    kpis = productos_qs.aggregate(
        total_productos=Count('id', filter=activo),
        stock_bajo=Count('id', filter=activo & Q(stock_vigente__gt=0, stock_vigente__lte=STOCK_BAJO_MAXIMO)),
        sin_stock=Count('id', filter=activo & Q(stock_vigente=0)),
        inactivos=Count('id', filter=Q(estado='inactivo')),
    )

    dias_limpieza = dias_inactivo_para_limpieza()
    if ver == 'activos':
        productos_qs = productos_qs.filter(estado='activo')
    elif ver == 'inactivos':
        productos_qs = productos_qs.filter(estado='inactivo')
    elif ver == 'limpieza':
        productos_qs = productos_qs.candidatos_limpieza(dias_limpieza)

    if estado_filtro == 'bajo':
        productos_qs = productos_qs.filter(stock_vigente__gt=0, stock_vigente__lte=STOCK_BAJO_MAXIMO)
    elif estado_filtro == 'agotado':
        productos_qs = productos_qs.filter(stock_vigente=0)
    elif estado_filtro == 'normal':
        productos_qs = productos_qs.filter(stock_vigente__gt=STOCK_BAJO_MAXIMO)

    # Lotes con existencias: vigentes (se pueden vender) y vencidos (siguen activos
    # con stock pero quedan fuera del stock vendible).
    hoy = timezone.localdate()
    productos_qs = productos_qs.annotate(
        cantidad_lotes=Count(
            'lotes',
            filter=filtro_lotes_vigentes('lotes__', hoy) & Q(lotes__stock_actual__gt=0),
            distinct=True,
        ),
        lotes_vencidos=Count(
            'lotes',
            filter=Q(lotes__activo=True, lotes__stock_actual__gt=0, lotes__fecha_vencimiento__lt=hoy),
            distinct=True,
        ),
    )

    # Los lotes se precargan en una sola consulta para el detalle desplegable,
    # ordenados igual que el FEFO de ventas (primero los que vencen antes).
    lotes_ordenados = LotesProducto.objects.order_by(F('fecha_vencimiento').asc(nulls_last=True), 'id')
    productos_qs = productos_qs.prefetch_related(
        'proveedores', Prefetch('lotes', queryset=lotes_ordenados)
    ).order_by('nombre', 'id')
    page_obj = Paginator(productos_qs, 7).get_page(request.GET.get('page'))

    return render(request, "productos/productos.html", {
        'productos': page_obj,
        'page_obj': page_obj,
        'busqueda': busqueda,
        'categoria_actual': cat_filtro,
        'estado_actual': estado_filtro,
        'categorias': categorias_productos(),
        'total_productos': kpis['total_productos'],
        'stock_bajo': kpis['stock_bajo'],
        'sin_stock': kpis['sin_stock'],
        'has_inactive_products': kpis['inactivos'] > 0,
        'total_inactivos': kpis['inactivos'],
        'ver_actual': ver,
        'dias_limpieza': dias_limpieza,
        # Sugerencia general (sin filtros de búsqueda) para el aviso de limpieza.
        'cantidad_limpieza': Productos.objects.candidatos_limpieza(dias_limpieza).count(),
        'proveedores': Proveedor.objects.filter(activo=True).order_by('nombre'),
        'hoy': hoy,
        'limite_por_vencer': hoy + timedelta(days=DIAS_POR_VENCER),
    })

@login_required
@requiere_perfil('Administrador')
def guardar(request):
    if request.method == 'POST':
        nombre = normalizar_texto(request.POST.get("nombre"))
        categoria = normalizar_texto(request.POST.get("categoria"))
        marca = normalizar_texto(request.POST.get("marca")) or "Sin marca"
        precio = request.POST.get("precio")
        estado = request.POST.get("estado", "activo")

        if not nombre:
            messages.error(request, 'El nombre del producto es obligatorio.')
            return redirect('consultar')

        precio, error = leer_monto(precio, 'El precio', permitir_cero=False)
        if error:
            messages.error(request, error)
            return redirect('consultar')

        error = _error_producto_duplicado(nombre, marca)
        if error:
            messages.error(request, error)
            return redirect('consultar')

        p = Productos(
            nombre=nombre,
            categoria=categoria,
            marca=marca,
            precio=precio,
            estado=estado,
        )
        try:
            p.full_clean()
            p.save()
            proveedores_ids = request.POST.getlist('proveedores')
            p.proveedores.set(proveedores_ids)
            messages.success(request, f'Producto "{nombre}" agregado al catálogo.')
            
        except ValidationError as e:
            if hasattr(e, 'message_dict') and 'categoria' in e.message_dict:
                messages.error(request, 'La categoría no puede contener números.')
            else:
                messages.error(request, 'Verifique que el precio sea mayor a 0.')
    
    return redirect('consultar')

@login_required
@requiere_perfil('Administrador')
@require_POST
def cambiar_estado(request, id):
    producto = get_object_or_404(Productos, pk=id)
    producto.estado = 'inactivo' if producto.estado == 'activo' else 'activo'
    producto.save()
    if producto.estado == 'inactivo':
        messages.success(request, f'"{producto.nombre}" quedó inactivo. Lo encontrás en Mostrar → Inactivos.')
    else:
        messages.success(request, f'"{producto.nombre}" volvió a estar activo.')
    return redirect(_url_inventario(request))

@login_required
@requiere_perfil('Administrador')
def detalle(request, id):
    producto = get_object_or_404(Productos, pk=id)
    proveedores = Proveedor.objects.filter(activo=True).order_by('nombre')
    return render(request, "productos/productoEditar.html", {
        'producto': producto,
        'proveedores': proveedores,
    })

@login_required
@requiere_perfil('Administrador')
@require_POST
def eliminar_seleccionados(request):
    """
    Borra productos elegidos a mano. Solo se permiten los inactivos que nunca
    tuvieron ventas, compras ni stock: así no se pierde historial (tickets,
    reportes, auditoría) ni lotes. Cada borrado queda en la auditoría.
    """
    ids = [item.strip() for item in request.POST.get('selected_ids', '').split(',') if item.strip().isdigit()]
    if not ids:
        messages.warning(request, 'No hay productos marcados para eliminar.')
        return redirect(_url_inventario(request))

    seleccionados = Productos.objects.filter(id__in=ids)
    borrables = seleccionados.filter(estado='inactivo').sin_movimientos()
    rechazados = [p.nombre for p in seleccionados.exclude(pk__in=borrables.values('pk'))]

    eliminados = 0
    with transaction.atomic():
        for producto in borrables:
            try:
                producto.delete()
                eliminados += 1
            except ProtectedError:   # por si se registró un movimiento justo ahora
                rechazados.append(producto.nombre)

    if eliminados:
        messages.success(request, f'Se eliminaron {eliminados} producto(s) correctamente.')
    if rechazados:
        messages.error(
            request,
            f'No se eliminó: {", ".join(rechazados)}. Solo se pueden eliminar productos inactivos que nunca '
            f'tuvieron ventas, compras ni stock; los demás se conservan inactivos para no perder el historial.'
        )
    return redirect(_url_inventario(request))


def _url_inventario(request):
    """Vuelve al inventario conservando la vista elegida (activos, inactivos, sugeridos...)."""
    ver = request.POST.get('ver', '')
    return f"{reverse('consultar')}?ver={ver}" if ver in VISTAS_INVENTARIO else reverse('consultar')

@login_required
@requiere_perfil('Administrador')
def editar(request):
    if request.method == 'POST':
        id_prod = request.POST.get("id")
        producto = get_object_or_404(Productos, pk=id_prod)

        producto.nombre = normalizar_texto(request.POST.get("nombre"))
        producto.categoria = normalizar_texto(request.POST.get("categoria"))
        producto.marca = normalizar_texto(request.POST.get("marca")) or "Sin marca"
        producto.precio = request.POST.get("precio")
        producto.estado = request.POST.get("estado", "activo")

        try:
            if not producto.nombre:
                raise ValidationError({'nombre': 'El nombre del producto es obligatorio.'})
            precio, error = leer_monto(producto.precio, 'El precio', permitir_cero=False)
            if error:
                raise ValidationError({'precio': error})
            producto.precio = precio
            # Al editar también se controla que no quede igual a otro producto.
            error = _error_producto_duplicado(producto.nombre, producto.marca, excluir_pk=producto.pk)
            if error:
                raise ValidationError({'nombre': error})
            producto.full_clean()
            producto.save()
            proveedores_ids = request.POST.getlist('proveedores')
            producto.proveedores.set(proveedores_ids)
            messages.success(request, f'Producto "{producto.nombre}" actualizado correctamente.')
        except ValidationError as e:
            errores = getattr(e, 'message_dict', {})
            if 'nombre' in errores:
                messages.error(request, errores['nombre'][0])
            elif 'categoria' in errores:
                messages.error(request, 'La categoría no puede contener números.')
            elif 'precio' in errores:
                messages.error(request, errores['precio'][0])
            else:
                messages.error(request, 'Error al actualizar: Verifique que el precio sea válido.')
            return render(request, 'productos/productoEditar.html', {
                'producto': producto,
                'proveedores': Proveedor.objects.filter(activo=True).order_by('nombre'),
                'datos_enviados': True,
                'nombre': request.POST.get('nombre', ''),
                'categoria': request.POST.get('categoria', ''),
                'marca': request.POST.get('marca', ''),
                'precio': request.POST.get('precio', ''),
            })

    return redirect('consultar')

@login_required
@requiere_perfil('Administrador')
def listar_proveedores(request):
    busqueda = request.GET.get('busqueda', '').strip()
    proveedores_qs = Proveedor.objects.all().order_by('nombre')

    if busqueda:
        proveedores_qs = proveedores_qs.filter(nombre__icontains=busqueda)

    total_proveedores = Proveedor.objects.count()
    activos = Proveedor.objects.filter(activo=True).count()
    inactivos = Proveedor.objects.filter(activo=False).count()

    paginator = Paginator(proveedores_qs, 7)
    page_obj = paginator.get_page(request.GET.get('page'))

    return render(request, 'productos/proveedores_list.html', {
        'proveedores': page_obj,
        'page_obj': page_obj,
        'busqueda': busqueda,
        'total_proveedores': total_proveedores,
        'activos': activos,
        'inactivos': inactivos,
    })

@login_required
@requiere_perfil('Administrador')
def crear_proveedor(request):
    if request.method == 'POST':
        nombre = normalizar_texto(request.POST.get('nombre'))
        telefono = request.POST.get('telefono', '').strip()
        tipo_productos = normalizar_texto(request.POST.get('tipo_productos'))

        error = None
        if not _contiene_letras(nombre):
            error = 'El nombre del proveedor debe contener letras y no puede ser solo números.'
        elif not telefono or not _es_telefono_numerico(telefono):
            error = MENSAJE_TELEFONO_INVALIDO
        elif not _contiene_letras(tipo_productos):
            error = 'El rubro debe contener letras y no puede ser solo números.'

        if error:
            messages.error(request, error)
            return render(request, 'productos/proveedor_form.html', {
                'es_edicion': False,
                'datos_enviados': True,
                'nombre': nombre,
                'telefono': telefono,
                'tipo_productos': tipo_productos,
            })

        error = _error_proveedor_duplicado(nombre)
        if not error and Proveedor.objects.filter(numero_telefono=telefono).exists():
            error = f'Ya existe un proveedor registrado con el teléfono {telefono}.'

        if error:
            messages.error(request, error)
            return render(request, 'productos/proveedor_form.html', {
                'es_edicion': False,
                'datos_enviados': True,
                'nombre': nombre,
                'telefono': telefono,
                'tipo_productos': tipo_productos,
            })

        Proveedor.objects.create(
            nombre=nombre,
            numero_telefono=telefono,
            tipo_productos=tipo_productos,
            activo=True
        )
        messages.success(request, f'Proveedor "{nombre}" registrado correctamente.')
        return redirect('listar_proveedores')

    return render(request, 'productos/proveedor_form.html', {'es_edicion': False})

@login_required
@requiere_perfil('Administrador')
def editar_proveedor(request, id):
    proveedor = get_object_or_404(Proveedor, pk=id)
    if request.method == 'POST':
        nombre = normalizar_texto(request.POST.get('nombre'))
        telefono = request.POST.get('telefono', '').strip()
        tipo_productos = normalizar_texto(request.POST.get('tipo_productos'))

        error = None
        if not _contiene_letras(nombre):
            error = 'El nombre del proveedor debe contener letras y no puede ser solo números.'
        elif not telefono or not _es_telefono_numerico(telefono):
            error = MENSAJE_TELEFONO_INVALIDO
        elif not _contiene_letras(tipo_productos):
            error = 'El rubro debe contener letras y no puede ser solo números.'

        if error:
            messages.error(request, error)
            return render(request, 'productos/proveedor_form.html', {
                'proveedor': proveedor,
                'es_edicion': True,
                'datos_enviados': True,
                'nombre': nombre,
                'telefono': telefono,
                'tipo_productos': tipo_productos,
            })

        # Se excluye al propio proveedor para que pueda guardar sin cambiar su nombre o teléfono.
        error = _error_proveedor_duplicado(nombre, excluir_pk=proveedor.pk)
        if not error and Proveedor.objects.filter(numero_telefono=telefono).exclude(pk=proveedor.pk).exists():
            error = f'Ya existe otro proveedor registrado con el teléfono {telefono}.'
        if error:
            messages.error(request, error)
            return render(request, 'productos/proveedor_form.html', {
                'proveedor': proveedor,
                'es_edicion': True,
                'datos_enviados': True,
                'nombre': nombre,
                'telefono': telefono,
                'tipo_productos': tipo_productos,
            })

        proveedor.nombre = nombre
        proveedor.numero_telefono = telefono
        proveedor.tipo_productos = tipo_productos
        proveedor.save()
        messages.success(request, f'Proveedor "{proveedor.nombre}" actualizado correctamente.')
        return redirect('listar_proveedores')

    return render(request, 'productos/proveedor_form.html', {'proveedor': proveedor, 'es_edicion': True})

@login_required
@requiere_perfil('Administrador')
def baja_proveedor(request, id):
    if request.method == 'POST':
        proveedor = get_object_or_404(Proveedor, pk=id)
        proveedor.activo = False
        proveedor.save()
        messages.success(request, f'Proveedor "{proveedor.nombre}" dado de baja.')
    return redirect('listar_proveedores')


@login_required
@requiere_perfil('Administrador')
def alta_proveedor(request, id):
    if request.method == 'POST':
        proveedor = get_object_or_404(Proveedor, pk=id)
        proveedor.activo = True
        proveedor.save()
        messages.success(request, f'Proveedor "{proveedor.nombre}" reactivado correctamente.')
    return redirect('listar_proveedores')