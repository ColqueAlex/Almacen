"""
Consultas de los reportes consolidados.

Cada función devuelve (filas, totales) ya calculados sobre TODO el conjunto
filtrado (no solo la página visible), para que la tabla HTML y el PDF usen
exactamente los mismos números.

Las devoluciones (DevolucionVenta) se descuentan: Ventas.total no se modifica
al devolver, así que sin restarlas los reportes quedarían inflados.
"""
from datetime import datetime, time, timedelta
from decimal import Decimal

from django.db.models import Count, DecimalField, ExpressionWrapper, F, Q, Sum, Value
from django.db.models.functions import Coalesce
from django.utils import timezone

from auditoria.models import Configuracion
from compras.models import DetallesCompra
from productos.models import LotesProducto, Productos
from ventas.models import Clientes, DetallesVentas, DevolucionVenta, Ventas

CERO = Decimal('0.00')
MONEDA = DecimalField(max_digits=14, decimal_places=2)


def _costo_unitario(ruta_lote):
    """Precio de costo del lote vendido (0 si el lote fue eliminado)."""
    return Coalesce(F(f'{ruta_lote}__precio_costo'), Value(CERO), output_field=MONEDA)


def _multiplicar(cantidad, precio):
    return ExpressionWrapper(F(cantidad) * precio, output_field=MONEDA)


def limite_stock_critico():
    return Configuracion.obtener('limite_stock_critico', 10, tipo=int)


def categorias_disponibles():
    return list(
        Productos.objects.exclude(categoria='').order_by('categoria').values_list('categoria', flat=True).distinct()
    )


# ---------------------------------------------------------------------------
# Reporte 1: Rentabilidad por producto
# ---------------------------------------------------------------------------
def rentabilidad_por_producto(desde=None, hasta=None, categoria=''):
    detalles = DetallesVentas.objects.exclude(venta__estado_de_pago='anulado')
    if desde:
        detalles = detalles.filter(venta__fecha__gte=desde)
    if hasta:
        detalles = detalles.filter(venta__fecha__lte=hasta)
    if categoria:
        detalles = detalles.filter(producto__categoria=categoria)

    vendidos = (
        detalles.order_by()
        .values('producto_id', 'producto__nombre', 'producto__marca', 'producto__categoria')
        .annotate(
            unidades=Sum('cantidad'),
            ingreso=Sum('subtotal'),
            costo=Sum(_multiplicar('cantidad', _costo_unitario('lote'))),
        )
    )

    devueltos = {
        d['detalle__producto_id']: d
        for d in DevolucionVenta.objects.filter(detalle__in=detalles).order_by()
        .values('detalle__producto_id')
        .annotate(
            unidades=Sum('cantidad'),
            monto=Sum('monto'),
            costo=Sum(_multiplicar('cantidad', _costo_unitario('detalle__lote'))),
        )
    }

    filas = []
    for v in vendidos:
        dev = devueltos.get(v['producto_id'], {})
        cantidad = (v['unidades'] or 0) - (dev.get('unidades') or 0)
        if cantidad <= 0:
            continue  # todo lo vendido en el período fue devuelto
        ingreso = (v['ingreso'] or CERO) - (dev.get('monto') or CERO)
        costo = (v['costo'] or CERO) - (dev.get('costo') or CERO)
        ganancia = ingreso - costo
        filas.append({
            'producto': v['producto__nombre'],
            'marca': v['producto__marca'],
            'categoria': v['producto__categoria'],
            'cantidad': cantidad,
            'ingreso': ingreso,
            'costo': costo,
            'ganancia': ganancia,
            'margen': (ganancia / ingreso * 100) if ingreso else None,
        })

    filas.sort(key=lambda f: (-f['ganancia'], f['producto']))

    totales = {
        'cantidad': sum(f['cantidad'] for f in filas),
        'ingreso': sum((f['ingreso'] for f in filas), CERO),
        'costo': sum((f['costo'] for f in filas), CERO),
        'ganancia': sum((f['ganancia'] for f in filas), CERO),
    }
    totales['margen'] = (totales['ganancia'] / totales['ingreso'] * 100) if totales['ingreso'] else None
    return filas, totales


# ---------------------------------------------------------------------------
# Reporte 2: Ranking de clientes por facturación y deuda
# ---------------------------------------------------------------------------
ESTADO_DEUDA_AL_DIA = 'al_dia'
ESTADO_DEUDA_CON_DEUDA = 'con_deuda'


def ranking_clientes(desde=None, hasta=None, estado_deuda=''):
    """
    Ranking de clientes por facturación del período.

    `estado_deuda` filtra por el SALDO ACTUAL del cliente, no por el estado de
    cada venta: cuando un cliente paga su fiado la venta original sigue como
    'fiado' y lo que cambia es Clientes.saldo. Se cuentan siempre TODAS las
    ventas no anuladas (pagadas y fiadas), así un cliente al día que compró
    en efectivo no desaparece del ranking.
      - 'al_dia'    -> saldo <= 0
      - 'con_deuda' -> saldo > 0
      - ''          -> sin filtrar
    """
    ventas = Ventas.objects.exclude(estado_de_pago='anulado').filter(id_clientes__isnull=False)
    if desde:
        ventas = ventas.filter(fecha__gte=desde)
    if hasta:
        ventas = ventas.filter(fecha__lte=hasta)
    if estado_deuda == ESTADO_DEUDA_AL_DIA:
        ventas = ventas.filter(id_clientes__saldo__lte=0)
    elif estado_deuda == ESTADO_DEUDA_CON_DEUDA:
        ventas = ventas.filter(id_clientes__saldo__gt=0)

    por_cliente = (
        ventas.order_by().values('id_clientes')
        .annotate(facturado=Sum('total'), cantidad=Count('id_ventas'))
    )
    devuelto = {
        d['detalle__venta__id_clientes']: d['monto']
        for d in DevolucionVenta.objects.filter(detalle__venta__in=ventas).order_by()
        .values('detalle__venta__id_clientes').annotate(monto=Sum('monto'))
    }
    clientes = Clientes.objects.in_bulk([c['id_clientes'] for c in por_cliente])

    filas = []
    for c in por_cliente:
        cliente = clientes[c['id_clientes']]
        filas.append({
            'cliente': f'{cliente.nombre} {cliente.apellido or ""}'.strip(),
            'telefono': cliente.telefono or '',
            'facturado': (c['facturado'] or CERO) - (devuelto.get(c['id_clientes']) or CERO),
            'cantidad': c['cantidad'],
            'saldo': cliente.saldo,
        })

    filas.sort(key=lambda f: (-f['facturado'], f['cliente']))
    for posicion, fila in enumerate(filas, start=1):
        fila['posicion'] = posicion

    totales = {
        'facturado': sum((f['facturado'] for f in filas), CERO),
        'cantidad': sum(f['cantidad'] for f in filas),
        'saldo': sum((f['saldo'] for f in filas), CERO),
    }
    return filas, totales


# ---------------------------------------------------------------------------
# Reporte 3: Stock valorizado al corte
# ---------------------------------------------------------------------------
def _clave_lote(producto_id, codigo_lote, vencimiento):
    """Identifica un lote igual que compras.views: producto + código de lote + vencimiento."""
    return (producto_id, (codigo_lote or '').strip().casefold(), vencimiento)


def _inicio_del_dia_siguiente(fecha):
    return timezone.make_aware(datetime.combine(fecha + timedelta(days=1), time.min))


def stock_valorizado(categoria='', solo_stock_bajo=False, fecha_corte=None, ingreso_desde=None):
    """
    Stock valorizado a una fecha de corte (por defecto, hoy).

    El stock histórico se reconstruye HACIA ATRÁS desde el stock actual
    (LotesProducto.stock_actual), deshaciendo los movimientos posteriores al corte:
      - compra confirmada posterior al corte .................. resta
      - venta (no anulada) posterior al corte ................. suma
      - devolución posterior al corte ......................... resta
      - compra anterior al corte pero anulada después de él ... suma
    Así el resultado es correcto aunque existan lotes cargados sin compra.
    Limitación: las ventas anuladas se ignoran (el sistema solo permite anular
    dentro de las 24 h y no guarda la fecha de anulación).

    `ingreso_desde` filtra los lotes cuya primera compra está en
    [ingreso_desde, fecha_corte] (antigüedad de la mercadería).
    """
    hoy = timezone.localdate()
    corte = min(fecha_corte or hoy, hoy)
    limite = limite_stock_critico()

    productos = Productos.objects.filter(estado='activo')
    if categoria:
        productos = productos.filter(categoria=categoria)
    productos = list(productos.order_by('categoria', 'nombre'))
    ids_productos = [p.pk for p in productos]

    lotes = list(LotesProducto.objects.filter(id_producto_id__in=ids_productos, activo=True))
    stock = {lote.pk: lote.stock_actual for lote in lotes}
    lote_por_clave = {_clave_lote(l.id_producto_id, l.lote, l.fecha_vencimiento): l.pk for l in lotes}

    # --- Compras: deshacer las posteriores al corte y registrar fecha de ingreso ---
    fecha_ingreso = {}
    compras = DetallesCompra.objects.filter(id_producto_id__in=ids_productos).values(
        'id_producto_id', 'lote', 'fecha_vencimiento', 'cantidad',
        'id_compra__fecha', 'id_compra__estado', 'id_compra__fecha_anulacion',
    )
    for d in compras:
        pk = lote_por_clave.get(_clave_lote(d['id_producto_id'], d['lote'], d['fecha_vencimiento']))
        if pk is None:
            continue
        fecha_compra = d['id_compra__fecha']
        anulada = (d['id_compra__estado'] or '').casefold() == 'anulado'
        if not anulada:
            fecha_ingreso[pk] = min(fecha_ingreso.get(pk, fecha_compra), fecha_compra)
            if fecha_compra > corte:
                stock[pk] -= d['cantidad']
        elif fecha_compra <= corte and d['id_compra__fecha_anulacion'] \
                and timezone.localdate(d['id_compra__fecha_anulacion']) > corte:
            stock[pk] += d['cantidad']   # al corte esa compra todavía estaba vigente

    if corte < hoy:
        # --- Ventas posteriores al corte: esas unidades todavía estaban en stock ---
        ventas = (
            DetallesVentas.objects.filter(lote_id__in=stock.keys(), venta__fecha__gt=corte)
            .exclude(venta__estado_de_pago='anulado')
            .order_by().values('lote_id').annotate(unidades=Sum('cantidad'))
        )
        for v in ventas:
            stock[v['lote_id']] += v['unidades']

        # --- Devoluciones posteriores al corte: esas unidades todavía no habían vuelto ---
        devoluciones = DevolucionVenta.objects.filter(
            fecha_hora__gte=_inicio_del_dia_siguiente(corte), detalle__producto_id__in=ids_productos,
        ).values('cantidad', 'detalle_id', 'detalle__lote_id', 'detalle__producto_id')
        for d in devoluciones:
            pk = d['detalle__lote_id'] or lote_por_clave.get(
                _clave_lote(d['detalle__producto_id'], f"DEVOLUCION-{d['detalle_id']}", None)
            )
            if pk in stock:
                stock[pk] -= d['cantidad']

    # --- Agregar por producto (solo lotes no vencidos a la fecha de corte) ---
    por_producto = {pk: {'stock': 0, 'valor': CERO} for pk in ids_productos}
    for lote in lotes:
        unidades = max(stock[lote.pk], 0)
        if not unidades:
            continue
        if lote.fecha_vencimiento and lote.fecha_vencimiento < corte:
            continue
        if ingreso_desde:
            ingreso = fecha_ingreso.get(lote.pk)
            if ingreso is None or not (ingreso_desde <= ingreso <= corte):
                continue
        acumulado = por_producto[lote.id_producto_id]
        acumulado['stock'] += unidades
        acumulado['valor'] += unidades * lote.precio_costo

    filas = []
    for p in productos:
        unidades = por_producto[p.pk]['stock']
        valor = por_producto[p.pk]['valor']
        if solo_stock_bajo and unidades >= limite:
            continue
        if ingreso_desde and not unidades:
            continue  # con filtro de ingreso, solo productos con lotes en ese rango
        filas.append({
            'producto': p.nombre,
            'marca': p.marca,
            'categoria': p.categoria,
            'stock': unidades,
            # Promedio ponderado por unidades de cada lote
            'costo_promedio': (valor / unidades) if unidades else None,
            'valor': valor,
            'stock_bajo': unidades < limite,
        })

    totales = {
        'stock': sum(f['stock'] for f in filas),
        'valor': sum((f['valor'] for f in filas), CERO),
        'productos_stock_bajo': sum(1 for f in filas if f['stock_bajo']),
    }
    totales['costo_promedio'] = (totales['valor'] / totales['stock']) if totales['stock'] else None
    return filas, totales, limite, corte
