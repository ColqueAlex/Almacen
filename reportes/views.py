from datetime import date

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.shortcuts import render
from django.utils import timezone

from usuarios.utils import requiere_perfil
from ventas.models import Ventas
from . import servicios
from .pdf import generar_reporte_pdf

FILAS_POR_PAGINA = 15


# ---------------------------------------------------------------------------
# Helpers comunes
# ---------------------------------------------------------------------------
def _moneda(valor):
    """$ 1.234,56 (formato argentino)."""
    if valor is None:
        return '—'
    texto = f'{valor:,.2f}'.replace(',', 'X').replace('.', ',').replace('X', '.')
    return f'$ {texto}'


def _nombre_producto(fila):
    marca = (fila.get('marca') or '').strip()
    if not marca or marca.lower() == 'sin marca':
        return fila['producto']
    return f"{fila['producto']} ({marca})"


def _porcentaje(valor):
    return '—' if valor is None else f'{valor:.1f} %'.replace('.', ',')


def _leer_rango_fechas(request):
    """Lee ?desde=&hasta= (YYYY-MM-DD). Fechas inválidas se ignoran con aviso."""
    fechas = {}
    for campo in ('desde', 'hasta'):
        valor = request.GET.get(campo, '').strip()
        fechas[campo] = None
        if valor:
            try:
                fechas[campo] = date.fromisoformat(valor)
            except ValueError:
                messages.error(request, f'La fecha "{valor}" no es válida y se ignoró.')
    if fechas['desde'] and fechas['hasta'] and fechas['desde'] > fechas['hasta']:
        messages.error(request, 'La fecha "desde" es posterior a "hasta"; se invirtió el rango.')
        fechas['desde'], fechas['hasta'] = fechas['hasta'], fechas['desde']
    return fechas['desde'], fechas['hasta']


def _texto_rango(desde, hasta):
    if desde and hasta:
        return f'del {desde:%d/%m/%Y} al {hasta:%d/%m/%Y}'
    if desde:
        return f'desde el {desde:%d/%m/%Y}'
    if hasta:
        return f'hasta el {hasta:%d/%m/%Y}'
    return 'todo el historial'


def _querystring_sin(request, *claves):
    """Querystring actual sin las claves indicadas (para paginación y exportación)."""
    params = request.GET.copy()
    for clave in claves:
        params.pop(clave, None)
    return params.urlencode()


def _quiere_pdf(request):
    return request.GET.get('exportar') == 'pdf'


def _contexto_base(request, filas, totales, filtros_texto):
    page_obj = Paginator(filas, FILAS_POR_PAGINA).get_page(request.GET.get('page'))
    return {
        'filas': page_obj,
        'page_obj': page_obj,
        'totales': totales,
        'total_filas': len(filas),
        'filtros_texto': filtros_texto,
        'querystring': _querystring_sin(request, 'page', 'exportar'),
    }


# ---------------------------------------------------------------------------
# Reporte 1: Rentabilidad por producto (solo Administrador)
# ---------------------------------------------------------------------------
@login_required
@requiere_perfil('Administrador')
def reporte_rentabilidad(request):
    desde, hasta = _leer_rango_fechas(request)
    categorias = servicios.categorias_disponibles()
    categoria = request.GET.get('categoria', '').strip()
    if categoria not in categorias:
        categoria = ''

    filas, totales = servicios.rentabilidad_por_producto(desde, hasta, categoria)
    filtros_texto = f'Período: {_texto_rango(desde, hasta)}; Categoría: {categoria or "Todas"}'

    if _quiere_pdf(request):
        return generar_reporte_pdf(
            titulo='Reporte de Rentabilidad por Producto',
            subtitulo=f'Período: {_texto_rango(desde, hasta)} — Ventas no anuladas, netas de devoluciones',
            columnas=['Producto', 'Categoría', 'Cant. vendida', 'Ingreso total', 'Costo total', 'Ganancia', 'Margen'],
            filas=[
                [_nombre_producto(f), f['categoria'], str(f['cantidad']), _moneda(f['ingreso']),
                 _moneda(f['costo']), _moneda(f['ganancia']), _porcentaje(f['margen'])]
                for f in filas
            ],
            fila_totales=['TOTALES', '', str(totales['cantidad']), _moneda(totales['ingreso']),
                          _moneda(totales['costo']), _moneda(totales['ganancia']), _porcentaje(totales['margen'])],
            filtros_texto=filtros_texto,
            usuario=request.user.username,
            nombre_archivo=f'Rentabilidad_{timezone.localdate():%Y%m%d}.pdf',
            anchos=[200, 95, 70, 95, 95, 95, 60],
            horizontal=True,
            alinear_derecha=(2, 3, 4, 5, 6),
        )

    contexto = _contexto_base(request, filas, totales, filtros_texto)
    contexto.update({
        'desde': desde, 'hasta': hasta,
        'categorias': categorias, 'categoria_seleccionada': categoria,
    })
    return render(request, 'reportes/rentabilidad.html', contexto)


# ---------------------------------------------------------------------------
# Reporte 2: Ranking de clientes por facturación y deuda
# ---------------------------------------------------------------------------
ESTADOS_PAGO_REPORTE = [(valor, etiqueta) for valor, etiqueta in Ventas.ESTADO_PAGO_CHOICES if valor != 'anulado']


@login_required
@requiere_perfil('Administrador', 'Vendedor')
def reporte_ranking_clientes(request):
    desde, hasta = _leer_rango_fechas(request)
    estado = request.GET.get('estado', '').strip()
    estados_validos = dict(ESTADOS_PAGO_REPORTE)
    if estado not in estados_validos:
        estado = ''

    filas, totales = servicios.ranking_clientes(desde, hasta, estado)
    filtros_texto = f'Período: {_texto_rango(desde, hasta)}; Estado de pago: {estados_validos.get(estado, "Todos")}'

    if _quiere_pdf(request):
        return generar_reporte_pdf(
            titulo='Ranking de Clientes por Facturación y Deuda',
            subtitulo=f'Período: {_texto_rango(desde, hasta)} — Saldo deudor al {timezone.localdate():%d/%m/%Y}',
            columnas=['#', 'Cliente', 'Total facturado', 'Cant. ventas', 'Saldo deudor actual'],
            filas=[
                [str(f['posicion']), f['cliente'], _moneda(f['facturado']), str(f['cantidad']), _moneda(f['saldo'])]
                for f in filas
            ],
            fila_totales=['', 'TOTALES', _moneda(totales['facturado']), str(totales['cantidad']), _moneda(totales['saldo'])],
            filtros_texto=filtros_texto,
            usuario=request.user.username,
            nombre_archivo=f'Ranking_Clientes_{timezone.localdate():%Y%m%d}.pdf',
            anchos=[30, 210, 110, 70, 112],
            alinear_derecha=(0, 2, 3, 4),
        )

    contexto = _contexto_base(request, filas, totales, filtros_texto)
    contexto.update({
        'desde': desde, 'hasta': hasta,
        'estados': ESTADOS_PAGO_REPORTE, 'estado_seleccionado': estado,
    })
    return render(request, 'reportes/ranking_clientes.html', contexto)


# ---------------------------------------------------------------------------
# Reporte 3: Stock valorizado al corte (solo Administrador)
# ---------------------------------------------------------------------------
@login_required
@requiere_perfil('Administrador')
def reporte_stock_valorizado(request):
    categorias = servicios.categorias_disponibles()
    categoria = request.GET.get('categoria', '').strip()
    if categoria not in categorias:
        categoria = ''
    solo_stock_bajo = request.GET.get('stock_bajo') == '1'
    # desde = lotes ingresados (comprados) desde; hasta = fecha de corte del stock
    ingreso_desde, fecha_corte = _leer_rango_fechas(request)
    hoy = timezone.localdate()
    if fecha_corte and fecha_corte > hoy:
        messages.error(request, 'La fecha de corte no puede ser futura; se usó la fecha de hoy.')
        fecha_corte = hoy

    filas, totales, limite, corte = servicios.stock_valorizado(categoria, solo_stock_bajo, fecha_corte, ingreso_desde)
    filtros_texto = (
        f'Fecha de corte: {corte:%d/%m/%Y}; '
        f'Lotes ingresados: {_texto_rango(ingreso_desde, corte) if ingreso_desde else "todos"}; '
        f'Categoría: {categoria or "Todas"}; '
        f'Solo stock bajo: {"Sí (< " + str(limite) + " u.)" if solo_stock_bajo else "No"}'
    )

    if _quiere_pdf(request):
        return generar_reporte_pdf(
            titulo='Reporte de Stock Valorizado al Corte',
            subtitulo=f'Corte al {corte:%d/%m/%Y} — Lotes activos y no vencidos a esa fecha, valuados a precio de costo',
            columnas=['Producto', 'Categoría', 'Stock', 'Costo promedio', 'Valor total', 'Estado'],
            filas=[
                [_nombre_producto(f), f['categoria'], str(f['stock']),
                 _moneda(f['costo_promedio']), _moneda(f['valor']), 'Stock bajo' if f['stock_bajo'] else 'OK']
                for f in filas
            ],
            fila_totales=['TOTALES', '', str(totales['stock']), _moneda(totales['costo_promedio']),
                          _moneda(totales['valor']), f"{totales['productos_stock_bajo']} bajo(s)"],
            filtros_texto=filtros_texto,
            usuario=request.user.username,
            nombre_archivo=f'Stock_Valorizado_{corte:%Y%m%d}.pdf',
            anchos=[180, 90, 55, 85, 90, 52],
            alinear_derecha=(2, 3, 4),
        )

    contexto = _contexto_base(request, filas, totales, filtros_texto)
    contexto.update({
        'categorias': categorias, 'categoria_seleccionada': categoria,
        'solo_stock_bajo': solo_stock_bajo, 'limite_stock': limite,
        'fecha_corte': corte, 'ingreso_desde': ingreso_desde, 'hoy': hoy,
    })
    return render(request, 'reportes/stock_valorizado.html', contexto)
