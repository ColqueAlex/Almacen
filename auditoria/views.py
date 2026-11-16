import json
import os
import subprocess
import sys
from datetime import date, datetime

from django.conf import settings
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Q
from django.utils import timezone
from django.views.decorators.http import require_POST
from .models import Auditoria, Notificacion
from .context_processors import filtro_notificaciones_usuario
from usuarios.utils import requiere_perfil

@login_required
@requiere_perfil('Administrador')
def listar_auditoria(request):
    busqueda = request.GET.get('busqueda', '').strip()
    accion = request.GET.get('accion', '').strip()
    modulo = request.GET.get('modulo', '').strip()

    registros = Auditoria.objects.select_related('usuario').all()

    if busqueda:
        busqueda_filtro = (
            Q(usuario__username__icontains=busqueda)
            | Q(modelo__icontains=busqueda)
            | Q(descripcion__icontains=busqueda)
        )
        if 'sistema' in busqueda.casefold():
            busqueda_filtro |= Q(usuario__isnull=True)
        registros = registros.filter(busqueda_filtro)

    acciones_validas = dict(Auditoria.ACCIONES_CHOICES)
    if accion in acciones_validas:
        registros = registros.filter(accion=accion)

    if modulo:
        registros = registros.filter(modelo=modulo)

    registros = registros.order_by('-fecha_hora')
    page_obj = Paginator(registros, 20).get_page(request.GET.get('page'))

    return render(request, 'auditoria/auditoria.html', {
        'registros': page_obj,
        'page_obj': page_obj,
        'busqueda': busqueda,
        'accion_seleccionada': accion,
        'modulo_seleccionado': modulo,
        'acciones': Auditoria.ACCIONES_CHOICES,
        'modulos': Auditoria.objects.order_by('modelo').values_list('modelo', flat=True).distinct(),
    })


@login_required
def bandeja_notificaciones(request):
    estado = request.GET.get('estado', 'no_leidas')
    tipo = request.GET.get('tipo', '').strip()

    base = Notificacion.objects.filter(filtro_notificaciones_usuario(request.user))
    notificaciones = base
    if estado == 'no_leidas':
        notificaciones = notificaciones.filter(leida=False)
    elif estado == 'leidas':
        notificaciones = notificaciones.filter(leida=True)
    else:
        estado = 'todas'

    tipos_validos = dict(Notificacion.TIPO_CHOICES)
    if tipo in tipos_validos:
        notificaciones = notificaciones.filter(tipo=tipo)
    else:
        tipo = ''

    page_obj = Paginator(notificaciones.order_by('leida', '-fecha'), 20).get_page(request.GET.get('page'))

    return render(request, 'auditoria/notificaciones.html', {
        'notificaciones': page_obj,
        'page_obj': page_obj,
        'estado_seleccionado': estado,
        'tipo_seleccionado': tipo,
        'tipos': Notificacion.TIPO_CHOICES,
        'total_no_leidas': base.filter(leida=False).count(),
    })


def _volver_a_bandeja(request):
    destino = request.POST.get('next', '')
    if destino.startswith('/') and not destino.startswith('//'):
        return redirect(destino)
    return redirect('bandeja_notificaciones')


@login_required
@require_POST
def marcar_notificacion_leida(request, id_notificacion):
    notificacion = get_object_or_404(
        Notificacion.objects.filter(filtro_notificaciones_usuario(request.user)), pk=id_notificacion,
    )
    if not notificacion.leida:
        notificacion.leida = True
        notificacion.fecha_lectura = timezone.now()
        notificacion.save(update_fields=['leida', 'fecha_lectura'])
    return _volver_a_bandeja(request)


@login_required
@require_POST
def marcar_todas_leidas(request):
    cantidad = Notificacion.objects.filter(
        filtro_notificaciones_usuario(request.user), leida=False,
    ).update(leida=True, fecha_lectura=timezone.now())
    messages.success(request, f'Se marcaron {cantidad} notificación(es) como leídas.')
    return _volver_a_bandeja(request)


# --- Dashboard Ejecutivo de Analítica -------------------------------------
# Las imágenes las genera el script independiente analitica_el_refugio.py en
# static/analitica/. La vista solo verifica que existan y las muestra.
GRAFICOS_ANALITICA = [
    ('grafico_dashboard.png', 'Panel integrado de KPIs', 'Facturación, ticket promedio, composición del cobro, top de productos y días más fuertes en una sola vista.'),
    ('grafico_ventas_categoria.png', 'Ventas mensuales por categoría', 'Unidades vendidas por mes, agrupadas por categoría de producto.'),
    ('grafico_metodos_pago.png', 'Métodos de pago', 'Participación de Efectivo y Transferencia en el total cobrado.'),
    ('grafico_evolucion.png', 'Evolución de la facturación', 'Facturación diaria del período (o de los últimos 30 días) con línea de tendencia.'),
    ('grafico_heatmap.png', 'Mapa de calor de ventas', 'Días de la semana y horas con mayor cantidad de ventas.'),
]
SCRIPT_ANALITICA = 'analitica_el_refugio.py'
METADATOS_ANALITICA = 'ultima_generacion.json'   # lo escribe el script con los filtros usados
TIMEOUT_ANALITICA = 120                          # segundos


def _carpeta_analitica():
    return os.path.join(settings.BASE_DIR, 'static', 'analitica')


def _leer_metadatos_analitica():
    """Filtros con los que se generaron las imágenes actuales ({} si no hay)."""
    try:
        with open(os.path.join(_carpeta_analitica(), METADATOS_ANALITICA), encoding='utf-8') as archivo:
            return json.load(archivo)
    except (OSError, ValueError):
        return {}


def _fecha_iso(valor):
    try:
        return date.fromisoformat(valor) if valor else None
    except (TypeError, ValueError):
        return None


def _ejecutar_script_analitica(fecha_inicio, fecha_fin):
    """
    Ejecuta analitica_el_refugio.py como proceso aparte y devuelve (ok, detalle).
    - Usa el mismo intérprete que Django (sys.executable), salvo que se configure
      settings.ANALITICA_PYTHON con otra ruta.
    - Los argumentos van como lista (sin shell=True): no hay inyección de comandos.
    """
    python = getattr(settings, 'ANALITICA_PYTHON', None) or sys.executable
    comando = [python, os.path.join(settings.BASE_DIR, SCRIPT_ANALITICA), '--fecha_fin', fecha_fin.isoformat()]
    if fecha_inicio:
        comando += ['--fecha_inicio', fecha_inicio.isoformat()]

    try:
        resultado = subprocess.run(
            comando, cwd=settings.BASE_DIR, capture_output=True, text=True,
            encoding='utf-8', errors='replace', timeout=TIMEOUT_ANALITICA,
            env={**os.environ, 'PYTHONIOENCODING': 'utf-8'},
        )
    except subprocess.TimeoutExpired:
        return False, f'El script tardó más de {TIMEOUT_ANALITICA} segundos y se canceló.'
    except OSError as error:
        return False, f'No se pudo ejecutar Python ({python}): {error}'

    if resultado.returncode != 0:
        salida = (resultado.stderr or resultado.stdout or '').strip()
        if 'ModuleNotFoundError' in salida:
            return False, ('faltan librerías de analítica en el Python del servidor. '
                           'Ejecutá: pip install -r requirements-analitica.txt')
        ultima_linea = salida.splitlines()[-1] if salida else 'sin detalle'
        return False, ultima_linea
    return True, None


@login_required
@requiere_perfil('Administrador')
def panel_analitica(request):
    hoy = timezone.localdate()
    metadatos = _leer_metadatos_analitica()

    if request.method == 'POST':
        texto_inicio = request.POST.get('fecha_inicio', '').strip()
        texto_fin = request.POST.get('fecha_fin', '').strip()
        fecha_inicio = _fecha_iso(texto_inicio)
        fecha_fin = _fecha_iso(texto_fin) or hoy

        errores = []
        if texto_inicio and not fecha_inicio:
            errores.append('La fecha de inicio no es válida.')
        if texto_fin and not _fecha_iso(texto_fin):
            errores.append('La fecha de fin no es válida.')
        if fecha_fin > hoy:
            errores.append('La fecha de fin no puede ser futura.')
        if fecha_inicio and fecha_inicio > fecha_fin:
            errores.append('La fecha de inicio no puede ser posterior a la fecha de fin.')

        if not errores:
            try:
                ok, detalle = _ejecutar_script_analitica(fecha_inicio, fecha_fin)
            except Exception as error:  # cualquier fallo inesperado no debe romper la página
                ok, detalle = False, str(error)
            if ok:
                rango = f'del {fecha_inicio:%d/%m/%Y}' if fecha_inicio else 'desde el inicio del historial'
                messages.success(request, f'Analítica generada {rango} al {fecha_fin:%d/%m/%Y}.')
                return redirect('panel_analitica')   # POST -> redirect: recargar no vuelve a ejecutar
            errores.append(f'No se pudo generar la analítica: {detalle}')

        for error in errores:
            messages.error(request, error)
        # Con error se vuelve a mostrar el formulario con lo que el usuario ingresó
        valor_inicio, valor_fin = texto_inicio, texto_fin
    else:
        # Precargar el formulario con los filtros de la última generación
        valor_inicio = metadatos.get('fecha_inicio') or ''
        valor_fin = metadatos.get('fecha_fin') or hoy.isoformat()

    carpeta = _carpeta_analitica()
    graficos = []
    for archivo, titulo, descripcion in GRAFICOS_ANALITICA:
        ruta = os.path.join(carpeta, archivo)
        existe = os.path.isfile(ruta)
        modificado = os.path.getmtime(ruta) if existe else None
        graficos.append({
            'archivo': archivo,
            'titulo': titulo,
            'descripcion': descripcion,
            'existe': existe,
            # ?v=<timestamp> evita que el navegador muestre una versión cacheada vieja
            'version': int(modificado) if modificado else 0,
            'destacado': archivo == 'grafico_dashboard.png',   # ocupa el ancho completo
        })

    fechas = [os.path.getmtime(os.path.join(carpeta, g['archivo'])) for g in graficos if g['existe']]
    ultima_actualizacion = (
        timezone.make_aware(datetime.fromtimestamp(max(fechas))) if fechas else None
    )
    return render(request, 'auditoria/analitica.html', {
        'graficos': graficos,
        'ultima_actualizacion': ultima_actualizacion,
        'faltan_graficos': not all(g['existe'] for g in graficos),
        'fecha_inicio': valor_inicio,
        'fecha_fin': valor_fin,
        'hoy': hoy,
        'generado_desde': _fecha_iso(metadatos.get('fecha_inicio')),
        'generado_hasta': _fecha_iso(metadatos.get('fecha_fin')),
        'generado_con_metadatos': bool(metadatos),
    })
