import csv
import hashlib
import hmac
import json
import os
import subprocess
import sys
from datetime import date, datetime, timedelta

from django.conf import settings
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Count, Max, Q
from django.http import HttpResponse
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST
from .models import Auditoria, Notificacion
from .servicios import registrar_evento
from .context_processors import filtro_notificaciones_usuario
from usuarios.utils import requiere_perfil

# --- Registro de auditoría -------------------------------------------------
REGISTROS_POR_PAGINA = 25
DIAS_RETENCION_AUDITORIA = 730


def _fecha_param(valor):
    try:
        return date.fromisoformat(valor) if valor else None
    except ValueError:
        return None


def _filtrar_auditoria(params):
    """Aplica los filtros del listado. Devuelve (queryset, filtros normalizados)."""
    filtros = {
        campo: params.get(campo, '').strip()
        for campo in ('busqueda', 'categoria', 'accion', 'modulo', 'nivel', 'usuario', 'desde', 'hasta')
    }
    registros = Auditoria.objects.select_related('usuario', 'usuario__id_perfil')

    if filtros['busqueda']:
        texto = filtros['busqueda']
        condicion = (
            Q(usuario_nombre__icontains=texto) | Q(usuario__username__icontains=texto)
            | Q(modelo__icontains=texto) | Q(descripcion__icontains=texto)
            | Q(objeto_repr__icontains=texto) | Q(ip__icontains=texto)
        )
        if 'sistema' in texto.casefold():
            condicion |= Q(usuario__isnull=True, usuario_nombre='')
        registros = registros.filter(condicion)

    for campo, opciones in (
        ('categoria', Auditoria.CATEGORIA_CHOICES),
        ('accion', Auditoria.ACCIONES_CHOICES),
        ('nivel', Auditoria.NIVEL_CHOICES),
    ):
        if filtros[campo] in dict(opciones):
            registros = registros.filter(**{campo: filtros[campo]})
        else:
            filtros[campo] = ''

    if filtros['modulo']:
        registros = registros.filter(modelo=filtros['modulo'])
    if filtros['usuario']:
        registros = registros.filter(usuario_nombre=filtros['usuario'])

    desde, hasta = _fecha_param(filtros['desde']), _fecha_param(filtros['hasta'])
    if desde:
        registros = registros.filter(fecha_hora__date__gte=desde)
    else:
        filtros['desde'] = ''
    if hasta:
        registros = registros.filter(fecha_hora__date__lte=hasta)
    else:
        filtros['hasta'] = ''

    return registros.order_by('-fecha_hora', '-id'), filtros


def _detalle_para_modal(reg):
    if reg.usuario:
        perfil = getattr(reg.usuario.id_perfil, 'nombre', None) or 'Usuario'
    elif reg.usuario_nombre:
        perfil = 'No autenticado'
    else:
        perfil = 'Proceso automático'
    return {
        'id': reg.id,
        'fecha': timezone.localtime(reg.fecha_hora).strftime('%d/%m/%Y %H:%M:%S'),
        'usuario': reg.actor,
        'perfil': perfil,
        'accion': reg.get_accion_display(),
        'categoria': reg.get_categoria_display(),
        'nivel': reg.nivel,
        'nivel_texto': reg.get_nivel_display(),
        'modulo': reg.modelo,
        'objeto': reg.objeto_repr,
        'objeto_id': reg.objeto_id or '',
        'descripcion': reg.descripcion,
        'cambios': reg.cambios or {},
        # Los registros anteriores a esta versión no tienen `cambios`: el modal
        # arma la comparación a partir de los datos crudos.
        'datos_anteriores': reg.datos_anteriores or {},
        'datos_nuevos': reg.datos_nuevos or {},
        'ip': reg.ip or '',
        'user_agent': reg.user_agent,
        'ruta': reg.ruta,
    }


@login_required
@requiere_perfil('Administrador')
def listar_auditoria(request):
    registros, filtros = _filtrar_auditoria(request.GET)
    depurar_antiguos = request.GET.get('depurar') == 'antiguos'
    fecha_limite = timezone.now() - timedelta(days=DIAS_RETENCION_AUDITORIA)
    registros_antiguos = Auditoria.objects.filter(fecha_hora__lt=fecha_limite)
    cantidad_antigua = registros_antiguos.count()
    if depurar_antiguos:
        registros = registros.filter(fecha_hora__lt=fecha_limite)
    page_obj = Paginator(registros, REGISTROS_POR_PAGINA).get_page(request.GET.get('page'))

    ahora = timezone.now()
    inicio_hoy = timezone.localtime(ahora).replace(hour=0, minute=0, second=0, microsecond=0)
    hace_24h = ahora - timedelta(hours=24)
    kpis = Auditoria.objects.aggregate(
        eventos_hoy=Count('id', filter=Q(fecha_hora__gte=inicio_hoy)),
        logins_hoy=Count('id', filter=Q(accion='login', fecha_hora__gte=inicio_hoy)),
        fallidos_24h=Count('id', filter=Q(accion='login_fallido', fecha_hora__gte=hace_24h)),
        criticos_7d=Count('id', filter=Q(nivel='critico', fecha_hora__gte=ahora - timedelta(days=7))),
    )
    # Cuentas con 3 o más intentos fallidos en las últimas 24 h (posible ataque).
    cuentas_atacadas = (
        Auditoria.objects.filter(accion='login_fallido', fecha_hora__gte=hace_24h)
        .values('usuario_nombre').annotate(intentos=Count('id'), ultimo=Max('fecha_hora'))
        .filter(intentos__gte=3).order_by('-intentos')[:5]
    )

    parametros = request.GET.copy()
    parametros.pop('page', None)

    return render(request, 'auditoria/auditoria.html', {
        'registros': page_obj,
        'page_obj': page_obj,
        'detalles': [_detalle_para_modal(reg) for reg in page_obj],
        'filtros': filtros,
        'hay_filtros': any(filtros.values()),
        'querystring': parametros.urlencode(),
        'kpis': kpis,
        'cuentas_atacadas': cuentas_atacadas,
        'acciones': Auditoria.ACCIONES_CHOICES,
        'categorias': Auditoria.CATEGORIA_CHOICES,
        'niveles': Auditoria.NIVEL_CHOICES,
        'modulos': Auditoria.objects.exclude(modelo='').order_by('modelo')
                   .values_list('modelo', flat=True).distinct(),
        'usuarios': Auditoria.objects.exclude(usuario_nombre='').order_by('usuario_nombre')
                    .values_list('usuario_nombre', flat=True).distinct(),
        'depurar_antiguos': depurar_antiguos,
        'cantidad_antigua': cantidad_antigua,
    })


def _celda_csv(valor):
    # Evita la inyección de fórmulas al abrir el archivo en Excel.
    if isinstance(valor, str) and valor[:1] in ('=', '+', '-', '@'):
        return "'" + valor
    return valor


@login_required
@requiere_perfil('Administrador')
def exportar_auditoria(request):
    """Descarga en CSV (compatible con Excel) los registros con los filtros actuales."""
    registros, _ = _filtrar_auditoria(request.GET)
    respuesta = HttpResponse(content_type='text/csv; charset=utf-8')
    respuesta['Content-Disposition'] = f'attachment; filename="auditoria_{timezone.localdate():%Y%m%d}.csv"'
    respuesta.write('﻿')  # BOM: Excel reconoce los acentos

    escritor = csv.writer(respuesta, delimiter=';')
    escritor.writerow(['Fecha y hora', 'Usuario', 'Categoría', 'Acción', 'Nivel', 'Módulo',
                       'Objeto', 'Descripción', 'Cambios', 'IP', 'Navegador', 'Ruta'])
    cantidad = 0
    for reg in registros.iterator(chunk_size=500):
        cambios = ' | '.join(
            f"{c.get('campo', campo)}: {c.get('antes') or '—'} → {c.get('despues') or '—'}"
            for campo, c in (reg.cambios or {}).items()
        )
        escritor.writerow([_celda_csv(v) for v in (
            timezone.localtime(reg.fecha_hora).strftime('%d/%m/%Y %H:%M:%S'), reg.actor,
            reg.get_categoria_display(), reg.get_accion_display(), reg.get_nivel_display(),
            reg.modelo, reg.objeto_repr, reg.descripcion, cambios, reg.ip or '', reg.user_agent, reg.ruta,
        )])
        cantidad += 1

    registrar_evento('exportacion', f'Exportó {cantidad} registro(s) de auditoría a CSV', modelo='Auditoría', request=request)
    return respuesta


def _ids_seleccionados_auditoria(request):
    try:
        ids = {int(valor) for valor in request.POST.getlist('registro_ids')}
    except (TypeError, ValueError):
        return None
    return sorted(ids) if ids and all(id_registro > 0 for id_registro in ids) else None


def _hash_ids_auditoria(ids):
    contenido = ','.join(str(id_registro) for id_registro in ids)
    return hashlib.sha256(contenido.encode('ascii')).hexdigest()


def _volver_auditoria_antigua():
    return f'{reverse("listar_auditoria")}?depurar=antiguos'


@login_required
@requiere_perfil('Administrador')
@require_POST
def exportar_auditoria_antigua(request):
    """Genera el respaldo CSV de los eventos antiguos seleccionados."""
    ids = _ids_seleccionados_auditoria(request)
    if not ids:
        messages.error(request, 'Selecciona al menos un registro antiguo para exportar.')
        return redirect(_volver_auditoria_antigua())

    fecha_limite = timezone.now() - timedelta(days=DIAS_RETENCION_AUDITORIA)
    registros = Auditoria.objects.filter(
        pk__in=ids, fecha_hora__lt=fecha_limite,
    ).select_related('usuario').order_by('id')
    if registros.count() != len(ids):
        messages.error(request, 'La selección contiene registros que ya no cumplen el plazo de antigüedad.')
        return redirect(_volver_auditoria_antigua())

    respuesta = HttpResponse(content_type='text/csv; charset=utf-8')
    respuesta['Content-Disposition'] = (
        f'attachment; filename="auditoria_mas_de_2_anos_{timezone.localdate():%Y%m%d}.csv"'
    )
    respuesta.write('﻿')
    escritor = csv.writer(respuesta, delimiter=';')
    escritor.writerow([
        'ID', 'Fecha y hora', 'Usuario', 'ID usuario', 'Nombre usuario registrado',
        'Categoría', 'Acción', 'Nivel', 'Módulo', 'ID objeto', 'Objeto', 'Descripción',
        'Cambios', 'Datos anteriores', 'Datos nuevos', 'IP', 'Navegador', 'Ruta',
    ])
    cantidad = 0
    for reg in registros.iterator(chunk_size=500):
        escritor.writerow([_celda_csv(valor) for valor in (
            reg.id, reg.fecha_hora.isoformat(), reg.actor, reg.usuario_id or '',
            reg.usuario_nombre, reg.categoria, reg.accion, reg.nivel, reg.modelo,
            reg.objeto_id or '', reg.objeto_repr, reg.descripcion,
            json.dumps(reg.cambios, ensure_ascii=False, default=str) if reg.cambios is not None else '',
            json.dumps(reg.datos_anteriores, ensure_ascii=False, default=str)
            if reg.datos_anteriores is not None else '',
            json.dumps(reg.datos_nuevos, ensure_ascii=False, default=str)
            if reg.datos_nuevos is not None else '',
            reg.ip or '', reg.user_agent, reg.ruta,
        )])
        cantidad += 1

    request.session['auditoria_limpieza_exportada'] = {
        'hash_ids': _hash_ids_auditoria(ids),
        'emitida_en': timezone.now().timestamp(),
    }
    registrar_evento(
        'exportacion',
        f'Exportó {cantidad} registro(s) antiguos de auditoría a CSV antes de su posible eliminación',
        modelo='Auditoría',
        request=request,
    )
    return respuesta


@login_required
@requiere_perfil('Administrador')
@require_POST
def eliminar_auditoria_antigua(request):
    """Elimina únicamente registros antiguos cuya selección ya se exportó a CSV."""
    ids = _ids_seleccionados_auditoria(request)
    exportacion = request.session.get('auditoria_limpieza_exportada', {})
    exportada_en = exportacion.get('emitida_en') if isinstance(exportacion, dict) else None
    hash_exportado = exportacion.get('hash_ids') if isinstance(exportacion, dict) else None
    exportacion_vigente = (
        isinstance(exportada_en, (int, float))
        and 0 <= timezone.now().timestamp() - exportada_en <= 1800
    )
    if (
        not ids or not exportacion_vigente or not isinstance(hash_exportado, str)
        or not hmac.compare_digest(hash_exportado, _hash_ids_auditoria(ids))
    ):
        request.session.pop('auditoria_limpieza_exportada', None)
        messages.error(request, 'Primero exporta a CSV la misma selección y vuelve a intentar.')
        return redirect(_volver_auditoria_antigua())

    fecha_limite = timezone.now() - timedelta(days=DIAS_RETENCION_AUDITORIA)
    with transaction.atomic():
        registros = Auditoria.objects.select_for_update().filter(
            pk__in=ids, fecha_hora__lt=fecha_limite,
        )
        if registros.count() != len(ids):
            request.session.pop('auditoria_limpieza_exportada', None)
            messages.error(request, 'La selección cambió o ya no cumple el plazo de antigüedad; no se borró nada.')
            return redirect(_volver_auditoria_antigua())

        cantidad, _ = registros.delete()
        registrar_evento(
            'eliminacion',
            f'Eliminó {cantidad} registro(s) de auditoría con más de '
            f'{DIAS_RETENCION_AUDITORIA // 365} años, previamente exportados a CSV',
            modelo='Auditoría',
            request=request,
        )

    request.session.pop('auditoria_limpieza_exportada', None)
    messages.success(request, f'Se eliminaron {cantidad} registro(s) antiguos de auditoría.')
    return redirect(_volver_auditoria_antigua())


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
