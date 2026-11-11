from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.db.models import Sum, Q
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
from .models import Ventas, DetallesVentas, Clientes, Pago
from productos.models import Productos, LotesProducto
from caja.models import Caja
from usuarios.utils import requiere_perfil, _solo_letras_y_espacios, _es_telefono_numerico

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
    
    return render(request, 'ventas/Gstion_Ventas.html', {
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

                    lotes = LotesProducto.objects.filter(
                        id_producto=producto, activo=True, stock_actual__gt=0
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

                if fiado and cliente:
                    cliente.saldo += total_venta
                    cliente.save()

            messages.success(request, f'Venta #{venta.id_ventas} registrada exitosamente.')
            return redirect('ventas')

        except Exception as e:
            messages.error(request, f'Error al registrar la venta: {str(e)}')
            return redirect('registrar_venta')

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
            messages.error(request, 'El teléfono solo puede contener números.')
            return redirect('cuentas_clientes')
        cliente = Clientes.objects.create(
            nombre=nombre,
            apellido=apellido,
            telefono=telefono
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