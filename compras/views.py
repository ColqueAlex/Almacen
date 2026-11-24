from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.core.paginator import Paginator
from django.core.exceptions import ValidationError
from django.utils import timezone
from datetime import date, datetime
from decimal import Decimal
import json
from .models import Compras, DetallesCompra
from productos.models import Productos, Proveedor, LotesProducto
from usuarios.utils import MONTO_MAXIMO, MONTO_MAXIMO_TEXTO, leer_cantidad, leer_monto, requiere_perfil

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

            confirmar_precios = request.POST.get('confirmar_precios') == 'true'
            nuevos_precios = {}
            if confirmar_precios:
                try:
                    nuevos_precios = json.loads(request.POST.get('nuevos_precios', '{}'))
                except (TypeError, json.JSONDecodeError):
                    raise ValueError('Los nuevos precios no tienen un formato válido.')
                if not isinstance(nuevos_precios, dict):
                    raise ValueError('Los nuevos precios no tienen un formato válido.')

            proveedor = None
            if proveedor_id:
                proveedor = Proveedor.objects.filter(pk=proveedor_id).first()

            hoy = date.today()
            limite_maximo = hoy.replace(year=hoy.year + 10)

            with transaction.atomic():
                producto_ids = {int(item['id_producto']) for item in items}
                productos = Productos.objects.select_for_update().in_bulk(producto_ids)
                if len(productos) != len(producto_ids):
                    raise ValueError('Uno o más productos ya no están disponibles.')

                total_compra = Decimal('0.00')
                costos_mayores = {}
                lotes_existentes = {}
                for lote_existente in LotesProducto.objects.select_for_update().filter(
                    id_producto_id__in=producto_ids
                ):
                    if lote_existente.lote.strip().casefold() == 'sin lote':
                        continue
                    clave_lote = (
                        lote_existente.id_producto_id,
                        lote_existente.lote.strip().casefold(),
                    )
                    lotes_existentes[clave_lote] = lote_existente

                lotes_en_compra = set()

                for item in items:
                    producto = productos[int(item['id_producto'])]
                    cantidad, error = leer_cantidad(item.get('cantidad'), f'La cantidad de "{producto.nombre}"')
                    if error:
                        raise ValueError(error)
                    precio_costo, error = leer_monto(item.get('precio_costo'), f'El costo de "{producto.nombre}"',
                                                     permitir_cero=False)
                    if error:
                        raise ValueError(error)
                    item['cantidad'], item['precio_costo'] = cantidad, precio_costo
                    lote_str = (item.get('lote') or 'Sin lote').strip() or 'Sin lote'
                    lote_normalizado = lote_str.casefold()
                    if lote_normalizado != 'sin lote':
                        clave_lote = (producto.pk, lote_normalizado)
                        lote_previo = lotes_existentes.get(clave_lote)
                        if lote_previo:
                            raise ValueError(
                                f'El lote "{lote_previo.lote}" ya está registrado para '
                                f'"{producto.nombre}". Stock registrado: {lote_previo.stock_actual} unidades.'
                            )
                        if clave_lote in lotes_en_compra:
                            raise ValueError(
                                f'El lote "{lote_str}" ya se agregó para "{producto.nombre}" '
                                'en esta compra.'
                            )
                        lotes_en_compra.add(clave_lote)

                    if precio_costo >= producto.precio:
                        costos_mayores[producto.pk] = max(
                            costos_mayores.get(producto.pk, Decimal('0.00')),
                            precio_costo,
                        )
                    total_compra += (cantidad * precio_costo)
                    if total_compra > MONTO_MAXIMO:
                        raise ValueError(f'El total de la compra no puede superar {MONTO_MAXIMO_TEXTO}.')

                precios_actualizados = {}
                if costos_mayores:
                    if not confirmar_precios:
                        raise ValueError('Confirma los nuevos precios de venta desde el cuadro de advertencia.')

                    campo_precio = Productos._meta.get_field('precio')
                    for producto_id in costos_mayores:
                        precio_nuevo = nuevos_precios.get(str(producto_id))
                        if precio_nuevo is None:
                            raise ValueError('Indica el nuevo precio de venta de cada producto señalado.')
                        try:
                            precio_nuevo = campo_precio.clean(precio_nuevo, None)
                        except ValidationError as error:
                            raise ValueError(f'El nuevo precio no es válido: {error}')
                        if not precio_nuevo.is_finite():
                            raise ValueError('El nuevo precio debe ser un importe válido.')
                        precios_actualizados[producto_id] = precio_nuevo

                for producto_id, precio_nuevo in precios_actualizados.items():
                    producto = productos[producto_id]
                    producto.precio = precio_nuevo
                    producto.save(update_fields=['precio'])

                compra = Compras.objects.create(
                    id_proveedor=proveedor,
                    id_usuario=request.user,
                    total=total_compra
                )

                for item in items:
                    producto = productos[int(item['id_producto'])]
                    cantidad = int(item['cantidad'])
                    precio_costo = Decimal(str(item['precio_costo']))
                    lote_str = (item.get('lote') or 'Sin lote').strip() or 'Sin lote'
                    
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
    lotes_usados = {}
    lotes_existentes = LotesProducto.objects.filter(
        id_producto__estado='activo'
    ).values('id_producto_id', 'lote', 'fecha_vencimiento', 'stock_actual')
    for lote in lotes_existentes:
        lotes_usados.setdefault(str(lote['id_producto_id']), []).append({
            'lote': lote['lote'],
            'fecha_vencimiento': lote['fecha_vencimiento'],
            'stock_actual': lote['stock_actual'],
        })
    
    mapeo_proveedores = {}
    for prov in proveedores:
        mapeo_proveedores[str(prov.id)] = list(prov.productos_que_suministra.values_list('id', flat=True))

    return render(request, 'compras/registro_compras.html', {
        'proveedores': proveedores,
        'productos': productos,
        'mapeo_proveedores': mapeo_proveedores, 
        'productos_sin_proveedor': productos_sin_proveedor,
        'lotes_usados': lotes_usados,
    })

@login_required
@requiere_perfil('Administrador')
def listar_compras(request):
    compras = Compras.objects.prefetch_related('detalles__id_producto').order_by('-id')
    page_obj = Paginator(compras, 7).get_page(request.GET.get('page'))
    return render(request, 'compras/compras_list.html', {
        'compras': page_obj,
        'page_obj': page_obj,
    })

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