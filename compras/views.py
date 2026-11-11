from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.utils import timezone
from datetime import date, datetime
from decimal import Decimal
import json
from .models import Compras, DetallesCompra
from productos.models import Productos, Proveedor, LotesProducto
from usuarios.utils import requiere_perfil

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
                total_compra = Decimal('0.00')

                for item in items:
                    cantidad = int(item['cantidad'])
                    precio_costo = Decimal(str(item['precio_costo']))
                    total_compra += (cantidad * precio_costo)

                compra = Compras.objects.create(
                    id_proveedor=proveedor,
                    id_usuario=request.user,
                    total=total_compra
                )

                for item in items:
                    producto = get_object_or_404(Productos, pk=item['id_producto'])
                    cantidad = int(item['cantidad'])
                    precio_costo = Decimal(str(item['precio_costo']))
                    lote_str = item.get('lote', 'Sin lote').strip()
                    
                    fecha_venc_str = item.get('fecha_vencimiento')
                    fecha_venc = None

                    if fecha_venc_str:
                        fecha_venc = datetime.strptime(fecha_venc_str, '%Y-%m-%d').date()
                        if fecha_venc <= hoy:
                            raise ValueError(f'La fecha de vencimiento para "{producto.nombre}" debe ser posterior a hoy.')
                        if fecha_venc > limite_maximo:
                            raise ValueError(f'La fecha de vencimiento para "{producto.nombre}" excede el límite.')

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

                compra.total = total_compra
                compra.save()

            messages.success(request, f'Compra #{compra.id} registrada exitosamente. Stock actualizado.')
            return redirect('listar_compras')

        except Exception as e:
            messages.error(request, f'Error al procesar la compra: {str(e)}')
            return redirect('registrar_compra')

    proveedores = Proveedor.objects.filter(activo=True).order_by('nombre')
    productos = Productos.objects.filter(estado='activo').order_by('nombre')
    
    productos_sin_proveedor = list(Productos.objects.filter(estado='activo', proveedores__isnull=True).values_list('id', flat=True))
    
    mapeo_proveedores = {}
    for prov in proveedores:
        mapeo_proveedores[str(prov.id)] = list(prov.productos_que_suministra.values_list('id', flat=True))

    return render(request, 'compras/registro_compras.html', {
        'proveedores': proveedores,
        'productos': productos,
        'mapeo_proveedores': mapeo_proveedores, 
        'productos_sin_proveedor': productos_sin_proveedor,
    })

@login_required
@requiere_perfil('Administrador')
def listar_compras(request):
    compras = Compras.objects.prefetch_related('detalles__id_producto').order_by('-id')
    return render(request, 'compras/compras_list.html', {'compras': compras})

@login_required
@requiere_perfil('Administrador')
def anular_compra(request, id_compra):
    compra = get_object_or_404(Compras, pk=id_compra)
    
    if compra.estado == 'Anulado':
        messages.warning(request, 'Esta compra ya fue anulada anteriormente.')
        return redirect('listar_compras')
        
    if request.method == 'POST':
        motivo = request.POST.get('motivo', '').strip()
        if not motivo:
            messages.error(request, 'Debe ingresar un motivo para anular la compra.')
            return redirect('listar_compras')

        try:
            with transaction.atomic():
                compra.estado = 'Anulado'
                compra.motivo_anulacion = motivo
                compra.fecha_anulacion = timezone.now()
                compra.usuario_anulacion = request.user
                compra.save()

                detalles = DetallesCompra.objects.filter(id_compra=compra)
                for detalle in detalles:
                    lote = LotesProducto.objects.filter(
                        id_producto=detalle.id_producto,
                        lote=detalle.lote,
                        fecha_vencimiento=detalle.fecha_vencimiento
                    ).first()
                    
                    if lote:
                        lote.stock_actual -= detalle.cantidad
                        if lote.stock_actual < 0:
                            lote.stock_actual = 0
                        lote.save()

            messages.success(request, f'Compra #{compra.id} anulada correctamente. El stock fue revertido.')
        except Exception as e:
            messages.error(request, f'Error al anular la compra: {str(e)}')

    return redirect('listar_compras')