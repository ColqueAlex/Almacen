from io import BytesIO
from django.http import HttpResponse
from reportlab.lib.pagesizes import letter
from reportlab.lib import colors
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
import json
from django.db.models import ProtectedError
from decimal import Decimal
from datetime import timedelta, date, datetime
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth import update_session_auth_hash, logout, authenticate, login
from django.db.models import Max, Sum, Q
from django.db import transaction
from django.core.paginator import Paginator
from django.utils import timezone
from django.core.exceptions import ValidationError
from functools import wraps

from .models import (
    Productos, Proveedor, Usuario, Perfil, Ventas, DetallesVentas,
    Compras, DetallesCompra, LotesProducto, Clientes, Caja, Pago
)

# =======================================================
# DECORADORES PERSONALIZADOS
# =======================================================

def requiere_perfil(*perfiles_permitidos):
    """
    Decorador para restringir vistas según el perfil del usuario.
    Si es superusuario (admin de Django), siempre tiene acceso.
    """
    def decorator(view_func):
        @wraps(view_func)
        def _wrapped_view(request, *args, **kwargs):
            if not request.user.is_authenticated:
                return redirect('login')
            
            # Superusuario de Django siempre pasa
            if request.user.is_superuser:
                return view_func(request, *args, **kwargs)

            # Obtener el nombre del perfil de forma segura
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


# =======================================================
#  VISTAS GENERALES Y HOME
# =======================================================

@login_required
def home(request):
    if not request.user.activo:
        logout(request)
        messages.error(request, 'Tu cuenta ha sido dada de baja.')
        return redirect('login')

    if request.user.debe_cambiar_clave:
        return redirect('cambiar_clave')
        
    return render(request, "principal.html")


# =======================================================
# GESTIÓN DE PRODUCTOS
# =======================================================

@login_required
@requiere_perfil('Administrador')
def consultar(request):
    if request.user.debe_cambiar_clave:
        return redirect('cambiar_clave')
        
    productos = Productos.objects.all().order_by('nombre')
    busqueda = request.GET.get('busqueda', '').strip()

    if busqueda:
        productos = productos.filter(nombre__icontains=busqueda)

    has_inactive_products = productos.filter(estado='inactivo').exists()
    page_obj = Paginator(productos, 7).get_page(request.GET.get('page'))

    return render(request, "productos.html", {
        'productos': page_obj,
        'page_obj': page_obj,
        'busqueda': busqueda,
        'has_inactive_products': has_inactive_products,
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
            messages.success(request, f'Producto "{producto.nombre}" actualizado correctamente.')
        except ValidationError as e:
            if hasattr(e, 'message_dict') and 'categoria' in e.message_dict:
                messages.error(request, 'La categoría no puede contener números.')
            else:
                messages.error(request, 'Error al actualizar: Verifique que el precio sea válido.')
            return redirect('detalle', id=id_prod)

    return redirect('consultar')


# =======================================================
# GESTIÓN DE VENTAS
# =======================================================

@login_required
@requiere_perfil('Administrador', 'Vendedor')
def ventas(request):
    if request.user.debe_cambiar_clave:
        return redirect('cambiar_clave')

    hoy = timezone.now().date()
    periodo = request.GET.get('filtro', 'todos')

    ventas_qs = Ventas.objects.prefetch_related('detalles__producto').order_by('-fecha', '-hora')

    if periodo == 'semana':
        desde = hoy - timedelta(days=7)
        ventas_qs = ventas_qs.filter(fecha__gte=desde)
    elif periodo == 'mes':
        desde = hoy - timedelta(days=30)
        ventas_qs = ventas_qs.filter(fecha__gte=desde)
    elif periodo == 'año':
        desde = hoy - timedelta(days=365)
        ventas_qs = ventas_qs.filter(fecha__gte=desde)

    page_obj = Paginator(ventas_qs, 7).get_page(request.GET.get('page'))
    fecha_limite = hoy.replace(year=hoy.year - 1)
    return render(request, 'Gstion_Ventas.html', {
        'ventas': page_obj,
        'page_obj': page_obj,
        'fecha_limite': fecha_limite,
        'periodo': periodo,
    })


@login_required
@requiere_perfil('Administrador', 'Vendedor')
def registrar_venta(request):
    caja_activa = Caja.objects.filter(estado='abierta', usuario=request.user).last()

    if request.method == 'POST':
        if not caja_activa:
            messages.error(request, 'No hay ninguna caja abierta. Debe abrir caja antes de registrar ventas.')
            return redirect('gestion_caja')

        try:
            cart_data = request.POST.get('cart_data', '[]')
            fiado = request.POST.get('fiado') == 'true'
            metodo_pago = request.POST.get('metodo_pago', 'efectivo')
            id_cliente_post = request.POST.get('id_cliente')
            items = json.loads(cart_data)

            if not items:
                messages.error(request, 'El carrito está vacío.')
                return redirect('registrar_venta')

            cliente = None
            if fiado:
                if not id_cliente_post:
                    messages.error(request, 'Debe seleccionar un cliente para fiar.')
                    return redirect('registrar_venta')
                cliente = get_object_or_404(Clientes, pk=id_cliente_post)

            with transaction.atomic():
                estado_pago = 'fiado' if fiado else 'pagado'
                metodo_final = None if fiado else metodo_pago
                ahora = datetime.now()

                venta = Ventas.objects.create(
                    id_caja=caja_activa,
                    total=Decimal('0.00'),
                    fecha=date.today(),
                    hora=ahora.time(),
                    id_clientes=cliente,
                    estado_de_pago=estado_pago,
                    metodo_pago=metodo_final
                )

                total_venta = Decimal('0.00')

                for item in items:
                    producto = get_object_or_404(Productos, pk=item['id'])
                    cantidad_necesaria = int(item['cantidad'])
                    precio_unitario = Decimal(str(item['precio']))

                    if producto.stock < cantidad_necesaria:
                        raise ValueError(f'Stock insuficiente para "{producto.nombre}". Disponible: {producto.stock}')

                    subtotal = cantidad_necesaria * precio_unitario

                    lotes = LotesProducto.objects.filter(
                        id_producto=producto,
                        activo=True,
                        stock_actual__gt=0
                    ).order_by('fecha_vencimiento', 'id')

                    pendiente = cantidad_necesaria
                    lote_principal = None

                    for lote in lotes:
                        if pendiente <= 0:
                            break
                        
                        if not lote_principal:
                            lote_principal = lote

                        if lote.stock_actual >= pendiente:
                            lote.stock_actual -= pendiente
                            pendiente = 0
                        else:
                            pendiente -= lote.stock_actual
                            lote.stock_actual = 0
                        
                        lote.save()

                    DetallesVentas.objects.create(
                        venta=venta,
                        producto=producto,
                        lote=lote_principal,
                        cantidad=cantidad_necesaria,
                        precio_unitario_venta=precio_unitario,
                        subtotal=subtotal
                    )

                    total_venta += subtotal

                venta.total = total_venta
                venta.save()

                if fiado and cliente:
                    cliente.saldo += total_venta
                    cliente.save()

            messages.success(request, f'Venta #{venta.id_ventas} registrada exitosamente.')
            return redirect('ventas')

        except Exception as e:
            messages.error(request, f'Error al registrar la venta: {str(e)}')
            return redirect('registrar_venta')

    # GET
    if not caja_activa:
        messages.warning(request, 'Atención: Debe abrir la caja para poder cobrar ventas.')

    productos = Productos.objects.filter(estado='activo').annotate(
        total_vendido=Sum(
            'detalles_ventas__cantidad',
            filter=~Q(detalles_ventas__venta__estado_de_pago__iexact='anulado'),
        )
    )
    categorias = Productos.objects.filter(estado='activo').values_list('categoria', flat=True).distinct()
    clientes = Clientes.objects.all().order_by('nombre')

    return render(request, 'Registro_ventas.html', {
        'productos': productos,
        'categorias': categorias,
        'clientes': clientes,
        'caja_activa': caja_activa
    })


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
def anular_venta(request, id_venta):
    caja_activa = Caja.objects.filter(estado='abierta').last()
    if not caja_activa:
        messages.error(request, 'No se puede anular la venta porque la caja está cerrada. Abra caja para realizar movimientos.')
        return redirect('ventas')

    venta = get_object_or_404(Ventas, pk=id_venta)

    if venta.estado_de_pago == 'anulado':
        messages.warning(request, f'La Venta #{venta.id_ventas} ya se encuentra anulada.')
        return redirect('ventas')

    if venta.id_caja.estado == 'cerrada':
        messages.error(
            request, 
            f'No se puede anular la Venta #{venta.id_ventas} porque pertenece a la Caja #{venta.id_caja.id_caja}, la cual ya fue CERRADA.'
        )
        return redirect('ventas')

    try:
        with transaction.atomic():
            venta.estado_de_pago = 'anulado'
            venta.save()

            if venta.id_clientes and venta.estado_de_pago == 'fiado':
                cliente = venta.id_clientes
                cliente.saldo -= venta.total
                cliente.save()

            detalles = DetallesVentas.objects.filter(venta=venta)

            for detalle in detalles:
                if detalle.lote:
                    detalle.lote.stock_actual += detalle.cantidad
                    detalle.lote.save()
                else:
                    lote = LotesProducto.objects.filter(id_producto=detalle.producto).first()
                    if lote:
                        lote.stock_actual += detalle.cantidad
                        lote.save()
                    else:
                        LotesProducto.objects.create(
                            id_producto=detalle.producto,
                            lote='LOTE-DEVOLUCION',
                            precio_costo=detalle.producto.precio,
                            stock_actual=detalle.cantidad,
                            activo=True
                        )

        messages.success(request, f'Venta #{venta.id_ventas} anulada exitosamente y stock reincorporado al almacén.')

    except Exception as e:
        messages.error(request, f'Error al anular la venta: {str(e)}')

    return redirect('ventas')


# =======================================================
# CUENTAS CORRIENTES (FIADOS) Y PAGOS
# =======================================================

@login_required
def cuentas_clientes(request):

    clientes = Clientes.objects.all().order_by('-saldo', 'nombre')
    
    pagos_recientes = Pago.objects.all().order_by('-fecha')[:20]

    return render(request, 'cuentas_clientes.html', {
        'clientes': clientes,
        'pagos_recientes': pagos_recientes
    })


@login_required
@requiere_perfil('Administrador', 'Vendedor')
def registrar_pago_cliente(request, id_cliente):
    if request.method == 'POST':
        caja_activa = Caja.objects.filter(estado='abierta', usuario=request.user).last()
        if not caja_activa:
            messages.error(request, 'No se puede recibir un pago porque la caja está cerrada. Debe abrir la caja primero.')
            return redirect('cuentas_clientes')

        cliente = get_object_or_404(Clientes, pk=id_cliente)
        
        try:
            monto_pago = Decimal(request.POST.get('monto', '0.00'))
            metodo_pago = request.POST.get('metodo_pago', 'efectivo')
        except Exception:
            monto_pago = Decimal('0.00')

        if monto_pago <= Decimal('0.00'):
            messages.error(request, 'El monto a abonar debe ser mayor a 0.')
            return redirect('cuentas_clientes')
            
        if monto_pago > cliente.saldo:
            messages.warning(request, f'El cliente solo debe ${cliente.saldo:.2f}. Se registrará el pago completo y el saldo quedará en 0.')
            monto_pago = cliente.saldo

        try:
            with transaction.atomic():
                Pago.objects.create(
                    cliente=cliente,
                    id_caja=caja_activa,
                    monto=monto_pago,
                    metodo_pago=metodo_pago,
                    usuario=request.user
                )
                
                cliente.saldo -= monto_pago
                cliente.save()

                messages.success(request, f'Se registró el pago de ${monto_pago:.2f} para {cliente.nombre}. Nuevo saldo: ${cliente.saldo:.2f}')

        except Exception as e:
            messages.error(request, f'Error al registrar el pago: {str(e)}')

    return redirect('cuentas_clientes')


@login_required
def crear_cliente(request):
    if request.method == 'POST':
        nombre = request.POST.get('nombre', '').strip()
        apellido = request.POST.get('apellido', '').strip()
        telefono = request.POST.get('telefono', '').strip()

        if not nombre:
            messages.error(request, 'El nombre del cliente es obligatorio.')
            return redirect('cuentas_clientes')

        cliente = Clientes.objects.create(
            nombre=nombre,
            apellido=apellido,
            telefono=telefono
        )
        messages.success(request, f'Cliente "{cliente.nombre} {cliente.apellido or ""}" registrado con éxito.')
    
    return redirect('cuentas_clientes')


# =======================================================
#  GESTIÓN DE CAJA
# =======================================================

@login_required
def gestion_caja(request):
    caja_activa = Caja.objects.filter(estado='abierta', usuario=request.user).last()
    historial_cajas = Caja.objects.all().order_by('-fecha_apertura')[:10]

    ventas_efectivo = Decimal('0.00')
    ventas_transferencia = Decimal('0.00')
    ventas_fiado = Decimal('0.00')
    cobros_efectivo = Decimal('0.00')
    cobros_transferencia = Decimal('0.00')
    monto_esperado = Decimal('0.00')

    if caja_activa:
        # Ventas en Efectivo
        v_efectivo = Ventas.objects.filter(id_caja=caja_activa, estado_de_pago='pagado', metodo_pago='efectivo').aggregate(total=Sum('total'))['total'] or Decimal('0.00')
        
        v_transf = Ventas.objects.filter(id_caja=caja_activa, estado_de_pago='pagado', metodo_pago='transferencia').aggregate(total=Sum('total'))['total'] or Decimal('0.00') 
        v_fiado = Ventas.objects.filter(id_caja=caja_activa, estado_de_pago='fiado').aggregate(total=Sum('total'))['total'] or Decimal('0.00')
        c_efectivo = Pago.objects.filter(id_caja=caja_activa, metodo_pago='efectivo').aggregate(total=Sum('monto'))['total'] or Decimal('0.00')
        c_transf = Pago.objects.filter(id_caja=caja_activa, metodo_pago='transferencia').aggregate(total=Sum('monto'))['total'] or Decimal('0.00')

        # Convertimos a Decimal
        ventas_efectivo = Decimal(str(v_efectivo))
        ventas_transferencia = Decimal(str(v_transf))
        ventas_fiado = Decimal(str(v_fiado))
        cobros_efectivo = Decimal(str(c_efectivo))
        cobros_transferencia = Decimal(str(c_transf))

        monto_esperado = caja_activa.monto_inicial + ventas_efectivo + cobros_efectivo

    return render(request, 'gestion_caja.html', {
        'caja_activa': caja_activa,
        'ventas_efectivo': ventas_efectivo,
        'ventas_transferencia': ventas_transferencia,
        'ventas_fiado': ventas_fiado,
        'cobros_efectivo': cobros_efectivo,
        'cobros_transferencia': cobros_transferencia,
        'monto_esperado': monto_esperado,
        'historial_cajas': historial_cajas,
    })


@login_required
@requiere_perfil('Administrador', 'Vendedor')
def abrir_caja(request):
    if request.method == 'POST':
        if Caja.objects.filter(estado='abierta').exists():
            messages.warning(request, 'Ya existe una caja abierta en el sistema.')
            return redirect('gestion_caja')

        monto_inicial = Decimal(request.POST.get('monto_inicial', '0.00'))

        Caja.objects.create(
            usuario=request.user,
            monto_inicial=monto_inicial,
            estado='abierta'
        )

        messages.success(request, f'Caja abierta con un monto inicial de ${monto_inicial:.2f}')

    return redirect('gestion_caja')


@login_required
@requiere_perfil('Administrador', 'Vendedor')
def cerrar_caja(request):
    if request.method == 'POST':
        caja_activa = Caja.objects.filter(estado='abierta', usuario=request.user).last()

        if not caja_activa:
            messages.error(request, 'No hay ninguna caja abierta para cerrar.')
            return redirect('gestion_caja')

        monto_real = Decimal(request.POST.get('monto_real', '0.00'))
        observaciones = request.POST.get('observaciones', '').strip()

        v_efectivo = Ventas.objects.filter(id_caja=caja_activa, estado_de_pago='pagado', metodo_pago='efectivo').aggregate(total=Sum('total'))['total'] or Decimal('0.00')
        c_efectivo = Pago.objects.filter(id_caja=caja_activa, metodo_pago='efectivo').aggregate(total=Sum('monto'))['total'] or Decimal('0.00')
        
        monto_esperado = caja_activa.monto_inicial + Decimal(str(v_efectivo)) + Decimal(str(c_efectivo))
        diferencia = monto_real - monto_esperado

        caja_activa.monto_final_esperado = monto_esperado
        caja_activa.monto_final_real = monto_real
        caja_activa.diferencia = diferencia
        caja_activa.fecha_cierre = timezone.now()
        caja_activa.estado = 'cerrada'
        caja_activa.observaciones = observaciones
        caja_activa.save()

        if diferencia == Decimal('0.00'):
            messages.success(request, 'Caja cerrada correctamente sin diferencias.')
        elif diferencia > Decimal('0.00'):
            messages.info(request, f'Caja cerrada con Sobrante de ${diferencia:.2f}.')
        else:
            messages.error(request, f'Caja cerrada con Faltante de ${abs(diferencia):.2f}.')

    return redirect('gestion_caja')

@login_required
@requiere_perfil('Administrador')
def forzar_cierre_caja(request, id_caja):
    if request.method == 'POST':
        caja = get_object_or_404(Caja, pk=id_caja, estado='abierta')
        
        ventas_efectivo = Ventas.objects.filter(id_caja=caja, estado_de_pago='pagado').aggregate(total=Sum('total'))['total'] or Decimal('0.00')
        cobros = Pago.objects.filter(id_caja=caja).aggregate(total=Sum('monto'))['total'] or Decimal('0.00')
        
        monto_esperado = caja.monto_inicial + Decimal(str(ventas_efectivo)) + Decimal(str(cobros))
        
        caja.monto_final_esperado = monto_esperado
        caja.monto_final_real = monto_esperado 
        caja.diferencia = Decimal('0.00')
        caja.fecha_cierre = timezone.now()
        caja.estado = 'cerrada'
        caja.observaciones = f"Cierre forzado por el Administrador {request.user.username}. (Caja original de {caja.usuario.username})"
        caja.save()
        
        messages.warning(request, f'Se forzó el cierre de la caja #{caja.id_caja} que pertenecía a {caja.usuario.username}.')
    return redirect('gestion_caja')


# =======================================================
#  GESTIÓN DE COMPRAS
# =======================================================

@login_required
@requiere_perfil('Administrador')
def registro_compras(request):
    if request.method == 'POST':
        try:
            cart_data = request.POST.get('cart_data', '[]')
            proveedor_id = request.POST.get('id_proveedor')
            items = json.loads(cart_data)

            if not items:
                messages.error(request, 'Debe agregar al menos un producto a la compra.')
                return redirect('registrar_compra')

            proveedor = None
            if proveedor_id:
                proveedor = Proveedor.objects.filter(pk=proveedor_id).first()

            hoy = date.today()
            limite_maximo = hoy.replace(year=hoy.year + 10)

            with transaction.atomic():
                compra = Compras.objects.create(
                    id_proveedor=proveedor,
                    id_usuario=request.user,
                    total=Decimal('0.00')
                )

                total_compra = Decimal('0.00')

                for item in items:
                    producto = get_object_or_404(Productos, pk=item['id_producto'])
                    cantidad = int(item['cantidad'])
                    precio_costo = Decimal(str(item['precio_costo']))
                    lote_str = item.get('lote', 'Sin lote').strip()
                    
                    fecha_venc_str = item.get('fecha_vencimiento')
                    fecha_venc = None

                    if fecha_venc_str:
                        fecha_venc = datetime.strptime(fecha_venc_str, '%Y-%m-%d').date()
                        
                        if fecha_venc < hoy:
                            raise ValueError(f'La fecha de vencimiento para "{producto.nombre}" no puede ser anterior a hoy.')
                        if fecha_venc > limite_maximo:
                            raise ValueError(f'La fecha de vencimiento para "{producto.nombre}" excede el límite máximo permitido (hasta {limite_maximo.year}).')

                    subtotal = cantidad * precio_costo

                    DetallesCompra.objects.create(
                        id_compra=compra,
                        id_producto=producto,
                        lote=lote_str,
                        fecha_vencimiento=fecha_venc,
                        cantidad=cantidad,
                        precio_unitario_compra=precio_costo,
                        subtotal=subtotal
                    )

                    lote_obj, created = LotesProducto.objects.get_or_create(
                        id_producto=producto,
                        lote=lote_str,
                        fecha_vencimiento=fecha_venc,
                        defaults={'precio_costo': precio_costo, 'stock_actual': 0}
                    )
                    lote_obj.stock_actual += cantidad
                    lote_obj.save()

                    total_compra += subtotal

                compra.total = total_compra
                compra.save()

            messages.success(request, f'Compra #{compra.id} registrada exitosamente. Stock actualizado.')
            return redirect('listar_compras')

        except Exception as e:
            messages.error(request, f'Error al procesar la compra: {str(e)}')
            return redirect('registrar_compra')

    proveedores = Proveedor.objects.filter(activo=True).order_by('nombre')
    productos = Productos.objects.filter(estado='activo').order_by('nombre')
    return render(request, 'registro_compras.html', {
        'proveedores': proveedores,
        'productos': productos
    })


@login_required
@requiere_perfil('Administrador')
def listar_compras(request):
    compras = Compras.objects.all().order_by('-id')
    return render(request, 'compras_list.html', {'compras': compras})


# =======================================================
#  GESTIÓN DE PROVEEDORES
# =======================================================

@login_required
@requiere_perfil('Administrador')
def listar_proveedores(request):
    proveedores = Proveedor.objects.all().order_by('nombre')
    return render(request, 'proveedores_list.html', {'proveedores': proveedores})


@login_required
@requiere_perfil('Administrador')
def crear_proveedor(request):
    if request.method == 'POST':
        nombre = request.POST.get('nombre', '').strip()
        telefono = request.POST.get('telefono', '').strip()
        tipo_productos = request.POST.get('tipo_productos', '').strip()

        if Proveedor.objects.filter(nombre__iexact=nombre).exists():
            messages.error(request, f'Ya existe un proveedor registrado con el nombre "{nombre}".')
            return render(request, 'proveedor_form.html', {'es_edicion': False})

        Proveedor.objects.create(
            nombre=nombre,
            numero_telefono=telefono,
            tipo_productos=tipo_productos,
            activo=True
        )
        messages.success(request, f'Proveedor "{nombre}" registrado correctamente.')
        return redirect('listar_proveedores')

    return render(request, 'proveedor_form.html', {'es_edicion': False})


@login_required
@requiere_perfil('Administrador')
def editar_proveedor(request, id):
    proveedor = get_object_or_404(Proveedor, pk=id)
    if request.method == 'POST':
        proveedor.nombre = request.POST.get('nombre', '').strip()
        proveedor.numero_telefono = request.POST.get('telefono', '').strip()
        proveedor.tipo_productos = request.POST.get('tipo_productos', '').strip()
        proveedor.save()
        messages.success(request, f'Proveedor "{proveedor.nombre}" actualizado correctamente.')
        return redirect('listar_proveedores')

    return render(request, 'proveedor_form.html', {'proveedor': proveedor, 'es_edicion': True})


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


# =======================================================
# GESTIÓN DE USUARIOS Y AUTENTICACIÓN
# =======================================================

def login_usuario(request):
    if request.user.is_authenticated:
        return redirect('inicio')

    if request.method == 'POST':
        username = request.POST.get('username', '').strip()
        password = request.POST.get('password', '').strip()

        user_exists = Usuario.objects.filter(username=username).first()

        if user_exists and not user_exists.activo:
            messages.error(request, 'Cuenta inactiva.')
            return render(request, 'login.html')

        user = authenticate(request, username=username, password=password)

        if user is not None:
            login(request, user)
            if user.debe_cambiar_clave:
                return redirect('cambiar_clave')
            return redirect('inicio')
        else:
            messages.error(request, 'Usuario o contraseña incorrectos.')
            return render(request, 'login.html')

    return render(request, 'login.html')


@login_required
@requiere_perfil('Administrador')
def listar_usuarios(request):
    usuarios = Usuario.objects.all()
    return render(request, 'usuarios_list.html', {'usuarios': usuarios})


@login_required
@requiere_perfil('Administrador')
def crear_usuario(request):
    if request.method == 'POST':
        dni = request.POST.get('dni')
        apellido = (request.POST.get('apellido') or '').strip()
        nombre = (request.POST.get('nombre') or '').strip()
        correo = (request.POST.get('correo') or '').strip()
        perfil_id = request.POST.get('perfil')

        if Usuario.objects.filter(dni=dni).exists():
            messages.error(request, f'Ya existe un usuario registrado con el DNI {dni}.')
            perfiles = Perfil.objects.all()
            return render(request, 'usuario_form.html', {
                'perfiles': perfiles,
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
    return render(request, 'usuario_form.html', {'perfiles': perfiles, 'es_edicion': False})


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
    return render(request, 'usuario_form.html', {
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
    usuario.save()
    messages.success(request, f'Usuario {usuario.username} reactivado correctamente.')
    return redirect('listar_usuarios')


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


# =======================================================
# REPORTES Y PDF
# =======================================================

@login_required
def generar_comprobante_pdf(request, id_venta):
    venta = get_object_or_404(Ventas, pk=id_venta)
    detalles = DetallesVentas.objects.filter(venta=venta)

    buffer = BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=letter, rightMargin=30, leftMargin=30, topMargin=30, bottomMargin=30)
    story = []
    styles = getSampleStyleSheet()

    style_title = ParagraphStyle('Title', parent=styles['Heading1'], fontSize=16, alignment=1, spaceAfter=10)
    style_sub = ParagraphStyle('Sub', parent=styles['Normal'], fontSize=10, alignment=1, spaceAfter=20)
    style_body = ParagraphStyle('Body', parent=styles['Normal'], fontSize=10, spaceAfter=5)

    story.append(Paragraph("<b>ALMACÉN EL REFUGIO</b>", style_title))
    story.append(Paragraph(f"Comprobante de Venta #{venta.id_ventas}<br/>Fecha: {venta.fecha.strftime('%d/%m/%Y')} - Hora: {venta.hora.strftime('%H:%M')}", style_sub))
    
    cliente_nombre = venta.cliente_info
    story.append(Paragraph(f"<b>Cliente:</b> {cliente_nombre}", style_body))
    story.append(Paragraph(f"<b>Estado de Pago:</b> {venta.get_estado_de_pago_display()}", style_body))
    if venta.metodo_pago:
        story.append(Paragraph(f"<b>Método de Pago:</b> {venta.get_metodo_pago_display()}", style_body))
    story.append(Spacer(1, 10))

    data = [["Producto", "Cant.", "P. Unitario", "Subtotal"]]
    for detalle in detalles:
        data.append([
            detalle.producto.nombre,
            str(detalle.cantidad),
            f"${detalle.precio_unitario_venta:.2f}",
            f"${detalle.subtotal:.2f}"
        ])

    data.append(["", "", "TOTAL:", f"${venta.total:.2f}"])

    table = Table(data, colWidths=[250, 60, 100, 100])
    table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#0d6efd')),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('ALIGN', (0, 1), (0, -1), 'LEFT'),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('BOTTOMPADDING', (0, 0), (-1, 0), 6),
        ('GRID', (0, 0), (-1, -2), 0.5, colors.lightgrey),
        ('FONTNAME', (2, -1), (-1, -1), 'Helvetica-Bold'),
        ('BACKGROUND', (2, -1), (-1, -1), colors.HexColor('#f8f9fa')),
    ]))

    story.append(table)
    story.append(Spacer(1, 20))
    story.append(Paragraph("¡Gracias por su compra!", style_sub))

    doc.build(story)
    buffer.seek(0)

    response = HttpResponse(buffer, content_type='application/pdf')
    response['Content-Disposition'] = f'inline; filename="Comprobante_Venta_{venta.id_ventas}.pdf"'
    return response