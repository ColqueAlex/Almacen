"""
Consultas de los reportes consolidados.

Cada función devuelve (filas, totales) ya calculados sobre TODO el conjunto
filtrado (no solo la página visible), para que la tabla HTML y el PDF usen
exactamente los mismos números.

Las devoluciones (DevolucionVenta) se descuentan: Ventas.total no se modifica
al devolver, así que sin restarlas los reportes quedarían inflados.
"""
from decimal import Decimal

from django.db.models import Count, DecimalField, ExpressionWrapper, F, Q, Sum, Value
from django.db.models.functions import Coalesce
from django.utils import timezone

from auditoria.models import Configuracion
from productos.models import Productos
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
def ranking_clientes(desde=None, hasta=None, estado_pago=''):
    ventas = Ventas.objects.exclude(estado_de_pago='anulado').filter(id_clientes__isnull=False)
    if desde:
        ventas = ventas.filter(fecha__gte=desde)
    if hasta:
        ventas = ventas.filter(fecha__lte=hasta)
    if estado_pago:
        ventas = ventas.filter(estado_de_pago=estado_pago)

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
def stock_valorizado(categoria='', solo_stock_bajo=False):
    hoy = timezone.localdate()
    limite = limite_stock_critico()
    # Mismo criterio que Productos.lotes_vigentes(): activos y no vencidos
    lote_vigente = (
        Q(lotes__activo=True, lotes__stock_actual__gt=0)
        & (Q(lotes__fecha_vencimiento__isnull=True) | Q(lotes__fecha_vencimiento__gte=hoy))
    )

    productos = Productos.objects.filter(estado='activo')
    if categoria:
        productos = productos.filter(categoria=categoria)

    productos = productos.annotate(
        stock_total=Coalesce(Sum('lotes__stock_actual', filter=lote_vigente), 0),
        valor_total=Coalesce(
            Sum(_multiplicar('lotes__stock_actual', F('lotes__precio_costo')), filter=lote_vigente),
            Value(CERO), output_field=MONEDA,
        ),
    ).order_by('categoria', 'nombre')

    if solo_stock_bajo:
        productos = productos.filter(stock_total__lt=limite)

    filas = []
    for p in productos:
        filas.append({
            'producto': p.nombre,
            'marca': p.marca,
            'categoria': p.categoria,
            'stock': p.stock_total,
            # Promedio ponderado por unidades de cada lote
            'costo_promedio': (p.valor_total / p.stock_total) if p.stock_total else None,
            'valor': p.valor_total,
            'stock_bajo': p.stock_total < limite,
        })

    totales = {
        'stock': sum(f['stock'] for f in filas),
        'valor': sum((f['valor'] for f in filas), CERO),
        'productos_stock_bajo': sum(1 for f in filas if f['stock_bajo']),
    }
    totales['costo_promedio'] = (totales['valor'] / totales['stock']) if totales['stock'] else None
    return filas, totales, limite
