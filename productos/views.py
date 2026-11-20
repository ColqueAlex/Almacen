from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Count, ProtectedError, Q
from django.core.paginator import Paginator
from django.core.exceptions import ValidationError
from .cache import categorias_productos
from .models import Productos, Proveedor
from usuarios.utils import requiere_perfil, _contiene_letras, _es_telefono_numerico, MENSAJE_TELEFONO_INVALIDO

# Un producto con stock entre 1 y este valor se considera "stock bajo".
STOCK_BAJO_MAXIMO = 10

@login_required
@requiere_perfil('Administrador')
def consultar(request):
    if request.user.debe_cambiar_clave:
        return redirect('cambiar_clave')

    busqueda = request.GET.get('busqueda', '').strip()
    cat_filtro = request.GET.get('categoria', '')
    estado_filtro = request.GET.get('estado_stock', '')

    productos_qs = Productos.objects.all()
    if busqueda:
        productos_qs = productos_qs.filter(Q(nombre__icontains=busqueda) | Q(marca__icontains=busqueda))
    if cat_filtro:
        productos_qs = productos_qs.filter(categoria__iexact=cat_filtro)

    # Stock vigente calculado en la base de datos (una sola consulta, sin N+1).
    productos_qs = productos_qs.con_stock()

    kpis = productos_qs.aggregate(
        total_productos=Count('id'),
        stock_bajo=Count('id', filter=Q(stock_vigente__gt=0, stock_vigente__lte=STOCK_BAJO_MAXIMO)),
        sin_stock=Count('id', filter=Q(stock_vigente=0)),
        inactivos=Count('id', filter=Q(estado='inactivo')),
    )

    if estado_filtro == 'bajo':
        productos_qs = productos_qs.filter(stock_vigente__gt=0, stock_vigente__lte=STOCK_BAJO_MAXIMO)
    elif estado_filtro == 'agotado':
        productos_qs = productos_qs.filter(stock_vigente=0)
    elif estado_filtro == 'normal':
        productos_qs = productos_qs.filter(stock_vigente__gt=STOCK_BAJO_MAXIMO)

    productos_qs = productos_qs.prefetch_related('proveedores').order_by('nombre', 'id')
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
        'proveedores': Proveedor.objects.filter(activo=True).order_by('nombre'),
    })

@login_required
def guardar(request):
    if request.method == 'POST':
        nombre = request.POST.get("nombre", "").strip()
        categoria = request.POST.get("categoria", "").strip()
        marca = (request.POST.get("marca") or "").strip() or "Sin marca"
        precio = request.POST.get("precio")
        estado = request.POST.get("estado", "activo")

        if Productos.objects.filter(nombre__iexact=nombre, marca__iexact=marca).exists():
            messages.error(request, f'Ya existe el producto "{nombre}" de marca "{marca}" en el catálogo.')
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
def cambiar_estado(request, id):
    producto = get_object_or_404(Productos, pk=id)
    producto.estado = 'inactivo' if producto.estado == 'activo' else 'activo'
    producto.save()
    messages.success(request, f'Estado del producto actualizado a {producto.get_estado_display()}')
    return redirect('consultar')

@login_required
def detalle(request, id):
    producto = get_object_or_404(Productos, pk=id)
    proveedores = Proveedor.objects.filter(activo=True).order_by('nombre')
    return render(request, "productos/productoEditar.html", {
        'producto': producto,
        'proveedores': proveedores,
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
    if request.method == 'POST':
        id_prod = request.POST.get("id")
        producto = get_object_or_404(Productos, pk=id_prod)

        producto.nombre = request.POST.get("nombre", "").strip()
        producto.categoria = request.POST.get("categoria", "").strip()
        producto.marca = (request.POST.get("marca") or "").strip() or "Sin marca"
        producto.precio = request.POST.get("precio")
        producto.estado = request.POST.get("estado", "activo")

        try:
            producto.full_clean()
            producto.save()
            proveedores_ids = request.POST.getlist('proveedores')
            producto.proveedores.set(proveedores_ids)
            messages.success(request, f'Producto "{producto.nombre}" actualizado correctamente.')
        except ValidationError as e:
            if hasattr(e, 'message_dict') and 'categoria' in e.message_dict:
                messages.error(request, 'La categoría no puede contener números.')
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
        nombre = request.POST.get('nombre', '').strip()
        telefono = request.POST.get('telefono', '').strip()
        tipo_productos = request.POST.get('tipo_productos', '').strip()

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

        if Proveedor.objects.filter(nombre__iexact=nombre).exists():
            error = f'Ya existe un proveedor registrado con el nombre "{nombre}".'
        elif Proveedor.objects.filter(numero_telefono=telefono).exists():
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
        nombre = request.POST.get('nombre', '').strip()
        telefono = request.POST.get('telefono', '').strip()
        tipo_productos = request.POST.get('tipo_productos', '').strip()

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

        # Se excluye al propio proveedor para que pueda guardar sin cambiar su teléfono.
        if Proveedor.objects.filter(numero_telefono=telefono).exclude(pk=proveedor.pk).exists():
            messages.error(request, f'Ya existe otro proveedor registrado con el teléfono {telefono}.')
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