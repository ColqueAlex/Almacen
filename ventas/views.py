from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.db.models import F, Prefetch, Sum, Q
from django.db.models.functions import Coalesce
from django.core.paginator import Paginator
from django.utils import timezone
from django.http import HttpResponse
from datetime import timedelta, date, datetime
from decimal import Decimal, InvalidOperation
import json
from io import BytesIO
from reportlab.lib.pagesizes import letter
from reportlab.lib import colors
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from .models import Ventas, DetallesVentas, DevolucionVenta, Clientes, Pago
from productos.models import Productos, LotesProducto
from caja.models import Caja
from usuarios.utils import requiere_perfil, _solo_letras_y_espacios, _es_telefono_numerico, MENSAJE_TELEFONO_INVALIDO

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

    hoy = timezone.now().date()
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

            if paga_con:
                try:
                    importe_recibido = Decimal(paga_con)
                except InvalidOperation:
                    messages.error(request, 'El importe de pago no es válido.')
                    return redirect('registrar_venta')
                if not importe_recibido.is_finite() or importe_recibido < 0:
                    messages.error(request, 'El importe de pago no puede ser negativo.')
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
                ahora = datetime.now()
                total_venta = Decimal('0.00')

                for item in items:
                    producto = get_object_or_404(Productos, pk=item['id'])
                    cantidad_necesaria = int(item['cantidad'])
                    precio_unitario = Decimal(str(item['precio']))

                    if producto.stock < cantidad_necesaria:
                        raise ValueError(f'Stock insuficiente para "{producto.nombre}". Disponible: {producto.stock}')

                    subtotal = cantidad_necesaria * precio_unitario
                    total_venta += subtotal

                venta = Ventas.objects.create(
                    id_caja=caja_activa,
                    total=total_venta,
                    fecha=date.today(),
                    hora=ahora.time(),
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

    productos = Productos.objects.filter(estado='activo').annotate(
        total_vendido=Coalesce(
            Sum(
                'detalles_ventas__cantidad',
                filter=~Q(detalles_ventas__venta__estado_de_pago__iexact='anulado'),
            ),
            0,
        )
    ).order_by('-total_vendido', 'nombre')
    categorias = Productos.objects.filter(estado='activo').values_list('categoria', flat=True).distinct()
    clientes = Clientes.objects.all().order_by('nombre')

    return render(request, 'ventas/Registro_ventas.html', {
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
@requiere_perfil('Administrador', 'Vendedor')
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
            if venta.id_clientes and estado_pago_original == 'fiado':
                cliente = venta.id_clientes
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

        if metodo_reintegro == 'fiado':
            cliente = Clientes.objects.select_for_update().get(pk=venta.id_clientes_id)
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
    
    pagos_recientes = Pago.objects.all().order_by('-fecha')[:10]

    deuda_total = sum(c.saldo for c in clientes_qs if c.saldo > 0)
    con_deuda = sum(1 for c in clientes_qs if c.saldo > 0)
    al_dia = sum(1 for c in clientes_qs if c.saldo == 0)
    total_clientes = clientes_qs.count()

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