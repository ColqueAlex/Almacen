from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.db.models import Count, F, Prefetch, Sum, Q
from django.db.models.functions import Coalesce
from django.core.paginator import Paginator
from django.utils import timezone
from django.http import HttpResponse
from django.views.decorators.http import require_POST
from datetime import timedelta, date, datetime
from decimal import Decimal
import json
from .models import Ventas, DetallesVentas, DevolucionVenta, Clientes, Pago
from productos.cache import categorias_productos
from productos.models import Productos, LotesProducto
from caja.models import Caja
from .ticket import generar_ticket_pdf
from usuarios.utils import (
    requiere_perfil, _solo_letras_y_espacios, _es_telefono_numerico, MENSAJE_TELEFONO_INVALIDO, normalizar_texto,
    MONTO_MAXIMO, MONTO_MAXIMO_TEXTO, leer_cantidad, leer_monto, clave_comparacion,
)

METODOS_PAGO_VALIDOS = ('efectivo', 'transferencia')


def _venta_dentro_del_plazo_de_devolucion(venta, ahora=None):
    ahora = ahora or timezone.now()
    fecha_venta = datetime.combine(venta.fecha, venta.hora)

    if timezone.is_aware(ahora) and timezone.is_naive(fecha_venta):
        fecha_venta = timezone.make_aware(fecha_venta, timezone.get_current_timezone())
    elif timezone.is_naive(ahora) and timezone.is_aware(fecha_venta):
        fecha_venta = timezone.make_naive(fecha_venta, timezone.get_current_timezone())

    antiguedad = ahora - fecha_venta
    return timedelta(0) <= antiguedad <= timedelta(days=1)

@login_required
@requiere_perfil('Administrador', 'Vendedor')
def ventas(request):
    if request.user.debe_cambiar_clave:
        return redirect('cambiar_clave')

    hoy = timezone.localdate()
    periodo = request.GET.get('filtro', 'todos')

    detalles_qs = DetallesVentas.objects.select_related('producto', 'lote').prefetch_related('devoluciones')
    ventas_qs = Ventas.objects.select_related('id_caja', 'id_clientes').prefetch_related(
        Prefetch('detalles', queryset=detalles_qs)
    ).order_by('-fecha', '-hora')

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
    caja_activa = Caja.objects.filter(estado='abierta', usuario=request.user).last()

    for venta in page_obj:
        detalles = list(venta.detalles.all())
        venta.total_devuelto = sum(
            (devolucion.monto for detalle in detalles for devolucion in detalle.devoluciones.all()),
            Decimal('0.00'),
        )
        venta.total_neto = max(Decimal('0.00'), venta.total - venta.total_devuelto)
        venta.tiene_devoluciones = venta.total_devuelto > 0
        venta.esta_devuelta = bool(detalles) and all(
            detalle.cantidad_disponible_para_devolucion == 0 for detalle in detalles
        )
        venta.dentro_plazo_devolucion = _venta_dentro_del_plazo_de_devolucion(venta)
        venta.puede_devolver = (
            venta.estado_de_pago != 'anulado'
            and venta.dentro_plazo_devolucion
            and caja_activa is not None
            and venta.id_caja.estado == 'abierta'
        )
        venta.puede_anular = (
            venta.puede_devolver
            and not venta.tiene_devoluciones
        )
    
    return render(request, 'ventas/Gstion_Ventas.html', {
        'ventas': page_obj,
        'page_obj': page_obj,
        'fecha_limite': fecha_limite,
        'periodo': periodo,
        'caja_activa': caja_activa,
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
            paga_con = request.POST.get('paga_con', '').strip()
            id_cliente_post = request.POST.get('id_cliente')

            if not fiado and metodo_pago not in METODOS_PAGO_VALIDOS:
                messages.error(request, 'Seleccioná un método de pago válido (efectivo o transferencia).')
                return redirect('registrar_venta')

            if paga_con:
                importe_recibido, error = leer_monto(paga_con, 'El importe con el que paga')
                if error:
                    messages.error(request, error)
                    return redirect('registrar_venta')

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
                ahora = timezone.localtime()
                total_venta = Decimal('0.00')

                for item in items:
                    producto = get_object_or_404(Productos, pk=item['id'])
                    cantidad_necesaria, error = leer_cantidad(item.get('cantidad'), f'La cantidad de "{producto.nombre}"')
                    if error:
                        raise ValueError(error)
                    # El precio sale siempre de la base de datos: el que manda el
                    # navegador se ignora para que no se pueda vender a otro precio.
                    if producto.estado != 'activo':
                        raise ValueError(f'"{producto.nombre}" está inactivo y no se puede vender.')
                    precio_unitario = producto.precio
                    if not precio_unitario or precio_unitario <= 0:
                        raise ValueError(f'"{producto.nombre}" no tiene un precio de venta válido.')
                    item['cantidad'], item['precio'] = cantidad_necesaria, precio_unitario

                    if producto.stock < cantidad_necesaria:
                        raise ValueError(f'Stock insuficiente para "{producto.nombre}". Disponible: {producto.stock}')

                    subtotal = cantidad_necesaria * precio_unitario
                    total_venta += subtotal
                    if total_venta > MONTO_MAXIMO:
                        raise ValueError(f'El total de la venta no puede superar {MONTO_MAXIMO_TEXTO}.')

                venta = Ventas.objects.create(
                    id_caja=caja_activa,
                    total=total_venta,
                    fecha=ahora.date(),
                    hora=ahora.time().replace(tzinfo=None),
                    id_clientes=cliente,
                    estado_de_pago=estado_pago,
                    metodo_pago=metodo_final
                )

                for item in items:
                    producto = get_object_or_404(Productos, pk=item['id'])
                    cantidad_necesaria = int(item['cantidad'])
                    precio_unitario = Decimal(str(item['precio']))
                    subtotal = cantidad_necesaria * precio_unitario

                    lotes = list(
                        producto.lotes_vigentes()
                        .filter(stock_actual__gt=0)
                        .select_for_update()
                        .order_by(F('fecha_vencimiento').asc(nulls_last=True), 'id')
                    )

                    pendiente = cantidad_necesaria

                    for lote in lotes:
                        if pendiente <= 0:
                            break

                        cantidad_del_lote = min(lote.stock_actual, pendiente)
                        lote.stock_actual -= cantidad_del_lote
                        lote.save(update_fields=['stock_actual'])

                        DetallesVentas.objects.create(
                            venta=venta,
                            producto=producto,
                            lote=lote,
                            cantidad=cantidad_del_lote,
                            precio_unitario_venta=precio_unitario,
                            subtotal=cantidad_del_lote * precio_unitario
                        )
                        pendiente -= cantidad_del_lote

                    if pendiente:
                        raise ValueError(f'Stock insuficiente para "{producto.nombre}".')

                if fiado and cliente:
                    cliente.saldo += total_venta
                    cliente.save()

            messages.success(request, f'Venta #{venta.id_ventas} registrada exitosamente.')
            return redirect('ventas')

        except Exception as e:
            messages.error(request, f'Error al registrar la venta: {str(e)}')
            return redirect('registrar_venta')

    productos = Productos.objects.filter(estado='activo').con_stock().annotate(
        total_vendido=Coalesce(
            Sum(
                'detalles_ventas__cantidad',
                filter=~Q(detalles_ventas__venta__estado_de_pago__iexact='anulado'),
            ),
            0,
        )
    ).order_by('-total_vendido', 'nombre')
    categorias = categorias_productos(solo_activos=True)
    clientes = Clientes.objects.all().order_by('nombre')

    return render(request, 'ventas/Registro_ventas.html', {
        'productos': productos,
        'categorias': categorias,
        'clientes': clientes,
        'caja_activa': caja_activa
    })

def _error_saldo_cliente_insuficiente(cliente, monto):
    """
    Al anular o devolver una venta fiada se descuenta su importe del saldo.
    Si el cliente ya pagó parte de esa deuda, el saldo quedaría negativo
    (dinero que el almacén le debe y que la caja no registra): se rechaza.
    """
    if cliente.saldo < monto:
        return (
            f'{cliente.nombre} ya pagó parte de su deuda: su saldo actual es ${cliente.saldo:.2f} y la operación '
            f'descontaría ${monto:.2f}. El saldo quedaría negativo, así que no se puede realizar.'
        )
    return None


@login_required
@requiere_perfil('Administrador', 'Vendedor')
@require_POST
def anular_venta(request, id_venta):
    caja_activa = Caja.objects.filter(estado='abierta', usuario=request.user).last()
    if not caja_activa:
        messages.error(request, 'No se puede anular la venta porque la caja está cerrada. Abra caja para realizar movimientos.')
        return redirect('ventas')

    try:
        with transaction.atomic():
            venta = get_object_or_404(
                Ventas.objects.select_for_update(),
                pk=id_venta,
            )

            if venta.estado_de_pago == 'anulado':
                messages.warning(request, f'La Venta #{venta.id_ventas} ya se encuentra anulada.')
                return redirect('ventas')

            if not _venta_dentro_del_plazo_de_devolucion(venta):
                messages.error(request, 'Solo se puede anular una venta dentro de las 24 horas posteriores a su registro.')
                return redirect('ventas')

            if venta.id_caja.estado == 'cerrada':
                messages.error(
                    request,
                    f'No se puede anular la Venta #{venta.id_ventas} porque pertenece a la Caja #{venta.id_caja.id_caja}, la cual ya fue CERRADA.'
                )
                return redirect('ventas')

            detalles = list(
                DetallesVentas.objects.select_for_update().filter(venta=venta).order_by('pk')
            )
            if any(detalle.devoluciones.exists() for detalle in detalles):
                messages.error(request, 'Esta venta ya tiene devoluciones parciales. Devuelva individualmente las unidades restantes.')
                return redirect('ventas')

            estado_pago_original = venta.estado_de_pago
            if venta.id_clientes_id and estado_pago_original == 'fiado':
                cliente = Clientes.objects.select_for_update().get(pk=venta.id_clientes_id)
                error = _error_saldo_cliente_insuficiente(cliente, venta.total)
                if error:
                    messages.error(request, error)
                    return redirect('ventas')
                cliente.saldo -= venta.total
                cliente.save(update_fields=['saldo'])

            venta.estado_de_pago = 'anulado'
            venta.save()

            for detalle in detalles:
                if detalle.lote_id:
                    lote = LotesProducto.objects.select_for_update().get(pk=detalle.lote_id)
                    lote.stock_actual += detalle.cantidad
                    lote.save(update_fields=['stock_actual'])
                else:
                    lote = LotesProducto.objects.select_for_update().filter(
                        id_producto=detalle.producto,
                    ).order_by('pk').first()
                    if lote:
                        lote.stock_actual += detalle.cantidad
                        lote.save(update_fields=['stock_actual'])
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


@login_required
@requiere_perfil('Administrador', 'Vendedor')
def devolver_detalle_venta(request, id_detalle):
    if request.method != 'POST':
        return redirect('ventas')

    caja_activa = Caja.objects.filter(estado='abierta', usuario=request.user).last()
    if not caja_activa:
        messages.error(request, 'Debe abrir una caja para registrar la devolución y su reintegro.')
        return redirect('ventas')

    try:
        cantidad_a_devolver = int(request.POST.get('cantidad', ''))
    except (TypeError, ValueError):
        messages.error(request, 'Indique una cantidad válida para devolver.')
        return redirect('ventas')

    detalle_venta_id = get_object_or_404(
        DetallesVentas.objects.only('venta_id'),
        pk=id_detalle,
    ).venta_id

    with transaction.atomic():
        venta = get_object_or_404(
            Ventas.objects.select_for_update(),
            pk=detalle_venta_id,
        )
        detalle = get_object_or_404(
            DetallesVentas.objects.select_for_update(),
            pk=id_detalle,
            venta=venta,
        )

        if venta.estado_de_pago == 'anulado':
            messages.error(request, 'No se puede devolver un producto de una venta anulada.')
            return redirect('ventas')

        if not _venta_dentro_del_plazo_de_devolucion(venta):
            messages.error(request, 'Solo se aceptan devoluciones dentro de las 24 horas posteriores a la venta.')
            return redirect('ventas')

        if venta.id_caja.estado == 'cerrada':
            messages.error(request, 'No se puede devolver el producto porque la caja original de la venta está cerrada.')
            return redirect('ventas')

        cantidad_devuelta = detalle.devoluciones.aggregate(total=Sum('cantidad'))['total'] or 0
        cantidad_disponible = detalle.cantidad - cantidad_devuelta
        if cantidad_a_devolver < 1 or cantidad_a_devolver > cantidad_disponible:
            messages.error(request, f'Puede devolver entre 1 y {cantidad_disponible} unidad(es) de este producto.')
            return redirect('ventas')

        if venta.estado_de_pago == 'fiado':
            metodo_reintegro = 'fiado'
            if not venta.id_clientes:
                messages.error(request, 'No se puede ajustar el saldo porque la venta fiada no tiene un cliente asociado.')
                return redirect('ventas')
        else:
            metodo_reintegro = venta.metodo_pago
            if metodo_reintegro not in ('efectivo', 'transferencia'):
                messages.error(request, 'No se pudo determinar el medio de pago original para reintegrar la devolución.')
                return redirect('ventas')

        monto = detalle.precio_unitario_venta * cantidad_a_devolver

        # Se valida antes de tocar el stock: un return dentro del atomic confirma lo ya guardado.
        cliente = None
        if metodo_reintegro == 'fiado':
            cliente = Clientes.objects.select_for_update().get(pk=venta.id_clientes_id)
            error = _error_saldo_cliente_insuficiente(cliente, monto)
            if error:
                messages.error(request, error)
                return redirect('ventas')

        if detalle.lote_id:
            lote = LotesProducto.objects.select_for_update().get(pk=detalle.lote_id)
        else:
            lote, _ = LotesProducto.objects.get_or_create(
                id_producto=detalle.producto,
                lote=f'DEVOLUCION-{detalle.pk}',
                defaults={
                    'precio_costo': detalle.producto.precio,
                    'stock_actual': 0,
                    'activo': True,
                },
            )

        lote.stock_actual += cantidad_a_devolver
        lote.activo = True
        lote.save(update_fields=['stock_actual', 'activo'])

        if cliente is not None:
            cliente.saldo -= monto
            cliente.save(update_fields=['saldo'])

        DevolucionVenta.objects.create(
            detalle=detalle,
            caja=caja_activa,
            usuario=request.user,
            cantidad=cantidad_a_devolver,
            monto=monto,
            metodo_reintegro=metodo_reintegro,
        )

    messages.success(
        request,
        f'Se devolvieron {cantidad_a_devolver} unidad(es) de {detalle.producto.nombre} por ${monto:.2f}.',
    )
    return redirect('ventas')

@login_required
def cuentas_clientes(request):
    clientes_qs = Clientes.objects.all().order_by('-saldo', 'nombre')

    pagos_recientes = Pago.objects.select_related('cliente', 'usuario').order_by('-fecha')[:10]

    totales = Clientes.objects.aggregate(
        deuda_total=Coalesce(Sum('saldo', filter=Q(saldo__gt=0)), Decimal('0.00')),
        con_deuda=Count('pk', filter=Q(saldo__gt=0)),
        al_dia=Count('pk', filter=Q(saldo=0)),
        total_clientes=Count('pk'),
    )
    deuda_total = totales['deuda_total']
    con_deuda = totales['con_deuda']
    al_dia = totales['al_dia']
    total_clientes = totales['total_clientes']

    return render(request, 'ventas/cuentas_clientes.html', {
        'clientes': clientes_qs,
        'pagos_recientes': pagos_recientes,
        'deuda_total': deuda_total,
        'con_deuda': con_deuda,
        'al_dia': al_dia,
        'total_clientes': total_clientes,
    })

@login_required
@requiere_perfil('Administrador', 'Vendedor')
def registrar_pago_cliente(request, id_cliente):
    if request.method == 'POST':
        caja_activa = Caja.objects.filter(estado='abierta', usuario=request.user).last()
        if not caja_activa:
            messages.error(request, 'No se puede recibir un pago porque la caja está cerrada. Debe abrir la caja primero.')
            return redirect('cuentas_clientes')

        get_object_or_404(Clientes, pk=id_cliente)

        metodo_pago = request.POST.get('metodo_pago', 'efectivo')
        if metodo_pago not in METODOS_PAGO_VALIDOS:
            messages.error(request, 'Seleccioná un método de pago válido (efectivo o transferencia).')
            return redirect('cuentas_clientes')
        monto_pago, error = leer_monto(request.POST.get('monto'), 'El monto a abonar', permitir_cero=False)
        if error:
            messages.error(request, error)
            return redirect('cuentas_clientes')

        try:
            with transaction.atomic():
                # Bloquea al cliente: dos cobros simultáneos no pueden pisarse el saldo.
                cliente = Clientes.objects.select_for_update().get(pk=id_cliente)
                if cliente.saldo <= 0:
                    messages.error(request, f'{cliente.nombre} no tiene deuda pendiente; no hay nada que cobrar.')
                    return redirect('cuentas_clientes')
                if monto_pago > cliente.saldo:
                    messages.warning(request, f'El cliente solo debe ${cliente.saldo:.2f}. Se registrará el pago completo y el saldo quedará en 0.')
                    monto_pago = cliente.saldo

                Pago.objects.create(
                    cliente=cliente,
                    id_caja=caja_activa,
                    monto=monto_pago,
                    metodo_pago=metodo_pago,
                    usuario=request.user
                )
                
                cliente.saldo -= monto_pago
                cliente.save(update_fields=['saldo'])

                messages.success(request, f'Se registró el pago de ${monto_pago:.2f} para {cliente.nombre}. Nuevo saldo: ${cliente.saldo:.2f}')

        except Exception as e:
            messages.error(request, f'Error al registrar el pago: {str(e)}')

    return redirect('cuentas_clientes')

def _cliente_con_mismo_nombre(nombre, apellido, excluir_pk=None):
    """
    Cliente cuyo nombre completo coincide sin distinguir mayúsculas, acentos,
    comillas ni espacios de más. Se compara nombre + apellido juntos, así
    "Rodrigo" + "Rojas" choca con un cliente cargado como "Rodrigo Rojas" sin apellido.
    """
    buscado = clave_comparacion(f'{nombre} {apellido or ""}')
    clientes = Clientes.objects.exclude(pk=excluir_pk) if excluir_pk is not None else Clientes.objects.all()
    for pk, nom, ape in clientes.values_list('pk', 'nombre', 'apellido'):
        if clave_comparacion(f'{nom} {ape or ""}') == buscado:
            return Clientes.objects.get(pk=pk)
    return None


@login_required
def crear_cliente(request):
    if request.method == 'POST':
        nombre = normalizar_texto(request.POST.get('nombre'))
        apellido = normalizar_texto(request.POST.get('apellido'))
        telefono = request.POST.get('telefono', '').strip()

        if not nombre:
            messages.error(request, 'El nombre del cliente es obligatorio.')
            return redirect('cuentas_clientes')
        if not _solo_letras_y_espacios(nombre):
            messages.error(request, 'El nombre del cliente solo puede contener letras y espacios.')
            return redirect('cuentas_clientes')
        if apellido and not _solo_letras_y_espacios(apellido):
            messages.error(request, 'El apellido del cliente solo puede contener letras y espacios.')
            return redirect('cuentas_clientes')
        if telefono and not _es_telefono_numerico(telefono):
            messages.error(request, MENSAJE_TELEFONO_INVALIDO)
            return redirect('cuentas_clientes')
        if telefono and Clientes.objects.filter(telefono=telefono).exists():
            messages.error(request, f'Ya existe un cliente registrado con el teléfono {telefono}.')
            return redirect('cuentas_clientes')
        existente = _cliente_con_mismo_nombre(nombre, apellido)
        if existente:
            messages.error(
                request,
                f'Ya existe el cliente "{" ".join(filter(None, [existente.nombre, existente.apellido]))}" '
                f'(Cuenta #{existente.pk:04d}). Usá esa cuenta o agregá el apellido para diferenciarlo.',
            )
            return redirect('cuentas_clientes')
        cliente = Clientes.objects.create(
            nombre=nombre,
            apellido=apellido,
            # Sin teléfono se guarda NULL: la columna es única y varios '' chocarían.
            telefono=telefono or None
        )
        messages.success(request, f'Cliente "{cliente.nombre} {cliente.apellido or ""}" registrado con éxito.')
    
    return redirect('cuentas_clientes')

@login_required
def generar_comprobante_pdf(request, id_venta):
    """Ticket de la venta para impresora térmica (80 mm por defecto, ver ventas/ticket.py)."""
    venta = get_object_or_404(
        Ventas.objects.select_related('id_clientes', 'id_caja__usuario'), pk=id_venta,
    )
    response = HttpResponse(generar_ticket_pdf(venta), content_type='application/pdf')
    response['Content-Disposition'] = f'inline; filename="Ticket_Venta_{venta.id_ventas}.pdf"'
    return response