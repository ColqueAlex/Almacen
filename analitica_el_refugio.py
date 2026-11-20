#!/usr/bin/env python
"""
==============================================================================
 MÓDULO DE ANALÍTICA - ALMACÉN "EL REFUGIO"
==============================================================================
Script independiente (no es una vista de Django) que se conecta a la base de
datos MySQL del proyecto, analiza las ventas con pandas/numpy y genera 5
imágenes PNG con matplotlib en la carpeta estática `static/analitica/`, desde
donde las muestra el Dashboard Ejecutivo de Django (/analitica/):

    grafico_ventas_categoria.png   Barras agrupadas    ¿Cómo evolucionan las ventas de cada categoría?
    grafico_metodos_pago.png       Donut               ¿Cómo cobramos: efectivo o transferencia?
    grafico_evolucion.png          Líneas + tendencia  ¿La facturación diaria crece o cae?
    grafico_heatmap.png            Mapa de calor       ¿Qué días y horas concentran las ventas?
    grafico_dashboard.png          Panel (GridSpec)    Tablero ejecutivo con KPIs y 4 vistas

Criterios de datos (los mismos que los Reportes Consolidados del sistema):
  - Se excluyen las ventas ANULADAS.
  - Se descuentan las DEVOLUCIONES (ventas_devolucionventa), porque al devolver
    un producto el total de la venta original no se modifica.

Uso:
    python analitica_el_refugio.py
    python analitica_el_refugio.py --desde 2026-11-01 --hasta 2026-11-30
    python analitica_el_refugio.py --categoria bebidas --dias 15
    python analitica_el_refugio.py --fecha_inicio 2026-11-01 --fecha_fin 2026-11-30   (alias)

Parámetros (filtros):
    --desde      (alias --fecha_inicio) Fecha inicial YYYY-MM-DD (por defecto: todo el historial).
    --hasta      (alias --fecha_fin)    Fecha final YYYY-MM-DD (por defecto: hoy).
    --categoria  Analiza solo una categoría de producto (sin distinguir mayúsculas).
    --dias       Ventana del gráfico de evolución cuando no se indica --desde (default 30).
    --salida     Carpeta de salida (default: static/analitica/).

Reproducibilidad:
  - Sin aleatoriedad: con la misma base de datos y los mismos parámetros
    (fijando `--hasta`), el script genera siempre los mismos gráficos.
  - Las credenciales se leen de myproject/settings.py (que a su vez usa las
    variables de entorno DB_HOST, DB_PORT, DB_NAME, DB_USER, DB_PASSWORD).
  - Versiones probadas: ver requirements-analitica.txt.
==============================================================================
"""
import argparse
import json
import os
import sys
import warnings
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

import matplotlib

# Backend sin interfaz gráfica: genera los PNG sin abrir ventanas
# (funciona en servidores, Docker o tareas programadas). Debe ir ANTES de pyplot.
matplotlib.use('Agg')

import matplotlib.dates as mdates  # noqa: E402
from matplotlib.patches import FancyBboxPatch  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402
import matplotlib.ticker as mticker  # noqa: E402
import mysql.connector  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

# pandas recomienda SQLAlchemy, pero read_sql funciona con cualquier conexión
# DB-API 2 (como mysql.connector). Silenciamos solo ese aviso para no agregar
# otra dependencia.
warnings.filterwarnings('ignore', message='pandas only supports SQLAlchemy')

BASE_DIR = Path(__file__).resolve().parent

# Carpeta estática donde Django busca los gráficos (BASE_DIR/static está en
# STATICFILES_DIRS de settings.py). Ruta absoluta para que funcione aunque el
# script se ejecute desde otra carpeta.
CARPETA_ESTATICA = os.path.join(BASE_DIR, 'static', 'analitica')

# Nombres fijos: el template auditoria/analitica.html los referencia con {% static %}
ARCHIVOS = {
    'categorias': 'grafico_ventas_categoria.png',
    'metodos_pago': 'grafico_metodos_pago.png',
    'evolucion': 'grafico_evolucion.png',
    'heatmap': 'grafico_heatmap.png',
    'dashboard': 'grafico_dashboard.png',
}
# Filtros de la última ejecución (el panel de Django los muestra y precarga el formulario)
ARCHIVO_METADATOS = 'ultima_generacion.json'

# --- Estilo visual común (colores de la paleta del sistema, base.css) --------
COLOR_PRIMARIO = '#1e40af'
COLOR_SECUNDARIO = '#059669'
COLOR_TERCIARIO = '#d97706'
COLOR_PELIGRO = '#ba1a1a'
COLOR_TEXTO = '#0f172a'
COLOR_MUTED = '#64748b'
PALETA_CATEGORIAS = ['#1e40af', '#059669', '#d97706', '#7c3aed', '#db2777', '#0891b2', '#65a30d', '#ea580c']

plt.rcParams.update({
    'figure.dpi': 110,
    'savefig.dpi': 150,
    'font.size': 10,
    'axes.titlesize': 14,
    'axes.titleweight': 'bold',
    'axes.titlecolor': COLOR_TEXTO,
    'axes.labelcolor': COLOR_MUTED,
    'axes.edgecolor': '#cbd5e1',
    'axes.spines.top': False,
    'axes.spines.right': False,
    'xtick.color': COLOR_MUTED,
    'ytick.color': COLOR_MUTED,
    'axes.grid': True,
    'grid.color': '#e2e8f0',
    'grid.linestyle': '-',
    'grid.linewidth': 0.8,
    'axes.axisbelow': True,
})

MESES_ES = ['Ene', 'Feb', 'Mar', 'Abr', 'May', 'Jun', 'Jul', 'Ago', 'Sep', 'Oct', 'Nov', 'Dic']
DIAS_ES = ['Lunes', 'Martes', 'Miércoles', 'Jueves', 'Viernes', 'Sábado', 'Domingo']


# =============================================================================
# 1. CONEXIÓN A LA BASE DE DATOS
# =============================================================================
def obtener_credenciales():
    """
    Lee las credenciales desde myproject/settings.py para no duplicarlas.
    settings.py solo depende de `pathlib` y `python-decouple` (que lee el .env),
    así que se puede importar sin inicializar Django. Si falla, usa variables de entorno.
    """
    try:
        sys.path.insert(0, str(BASE_DIR))
        from myproject import settings  # noqa: WPS433 (import local intencional)
        db = settings.DATABASES['default']
    except Exception as error:  # pragma: no cover - solo si no existe settings
        print(f'[aviso] No se pudo leer settings.py ({error}); se usan variables de entorno.')
        db = {
            'NAME': os.environ.get('DB_NAME', 'Almacen_ElRefugio'),
            'USER': os.environ.get('DB_USER', ''),
            'PASSWORD': os.environ.get('DB_PASSWORD', ''),
            'HOST': os.environ.get('DB_HOST', '127.0.0.1'),
            'PORT': os.environ.get('DB_PORT', '3307'),
        }
    return {
        'host': db['HOST'] or '127.0.0.1',
        'port': int(db['PORT'] or 3306),
        'database': db['NAME'],
        'user': db['USER'],
        'password': db['PASSWORD'],
    }


def conectar():
    credenciales = obtener_credenciales()
    print(f"Conectando a MySQL {credenciales['user']}@{credenciales['host']}:{credenciales['port']}/{credenciales['database']} ...")
    return mysql.connector.connect(charset='utf8mb4', **credenciales)


# =============================================================================
# 2. FILTROS Y EXTRACCIÓN DE DATOS (pandas.read_sql)
# =============================================================================
@dataclass(frozen=True)
class Filtros:
    """Parámetros de la ejecución (se muestran en el pie de cada gráfico)."""
    hasta: date
    desde: date | None = None
    categoria: str | None = None
    dias: int = 30

    @property
    def inicio_evolucion(self):
        """Con --desde la evolución cubre todo el rango; si no, los últimos --dias días."""
        return self.desde or (self.hasta - timedelta(days=self.dias - 1))

    def texto(self):
        periodo = f"{self.desde:%d/%m/%Y}" if self.desde else 'inicio del historial'
        return (f'Filtros: período {periodo} al {self.hasta:%d/%m/%Y} · '
                f'categoría: {self.categoria or "todas"} · ventas no anuladas, netas de devoluciones.')


# Los nombres de tablas/columnas son los que Django creó (ver db_column en los
# modelos de la app ventas). Las devoluciones se agregan por detalle en una
# subconsulta para no duplicar filas al hacer el JOIN.

SQL_DETALLES = """
    SELECT
        v.ID_ventas                                      AS venta_id,
        v.fecha                                          AS fecha,
        COALESCE(NULLIF(TRIM(p.categoria), ''), 'Sin categoría') AS categoria,
        p.nombre                                         AS producto,
        d.Cantidad - COALESCE(dev.cantidad, 0)           AS cantidad_neta,
        d.Subtotal - COALESCE(dev.monto, 0)              AS subtotal_neto
    FROM ventas_detallesventas d
    JOIN ventas_ventas v        ON v.ID_ventas = d.ID_ventas
    JOIN productos_productos p  ON p.id = d.ID_Productos
    LEFT JOIN (
        SELECT detalle_id, SUM(cantidad) AS cantidad, SUM(monto) AS monto
        FROM ventas_devolucionventa
        GROUP BY detalle_id
    ) dev ON dev.detalle_id = d.ID_Detalles_Ventas
    WHERE v.Estado_de_pago <> %s
      AND v.fecha BETWEEN %s AND %s
"""

# Cabecera de cada venta (sin montos): el monto se calcula en pandas sumando
# sus líneas de detalle (merge), así el filtro por categoría afecta a todos
# los gráficos y no solo al de categorías.
SQL_VENTAS = """
    SELECT
        v.ID_ventas          AS venta_id,
        v.fecha              AS fecha,
        HOUR(v.Hora)         AS hora,
        WEEKDAY(v.fecha)     AS dia_semana,   -- 0 = lunes ... 6 = domingo
        v.Estado_de_pago     AS estado_pago,
        v.metodo_pago        AS metodo_pago
    FROM ventas_ventas v
    WHERE v.Estado_de_pago <> %s
      AND v.fecha BETWEEN %s AND %s
"""


def cargar_datos(conexion, filtros):
    """Trae los datos con pandas.read_sql, aplica los filtros y arma las ventas con merge."""
    params = ('anulado', filtros.desde or date(1900, 1, 1), filtros.hasta)
    detalles = pd.read_sql(SQL_DETALLES, conexion, params=params)
    cabeceras = pd.read_sql(SQL_VENTAS, conexion, params=params)

    # MySQL devuelve DECIMAL como objetos Decimal: los pasamos a float para numpy
    for col in ('cantidad_neta', 'subtotal_neto'):
        detalles[col] = pd.to_numeric(detalles[col], errors='coerce').fillna(0.0).astype(float)
    detalles['fecha'] = pd.to_datetime(detalles['fecha'])
    cabeceras['fecha'] = pd.to_datetime(cabeceras['fecha'])
    cabeceras['hora'] = pd.to_numeric(cabeceras['hora'], errors='coerce')
    cabeceras['dia_semana'] = pd.to_numeric(cabeceras['dia_semana'], errors='coerce')

    # Filtro por categoría (sin distinguir mayúsculas/espacios)
    if filtros.categoria:
        disponibles = sorted(detalles['categoria'].unique())
        coincide = detalles['categoria'].str.strip().str.casefold() == filtros.categoria.strip().casefold()
        if not coincide.any():
            sys.exit(f'La categoría "{filtros.categoria}" no tiene ventas en el período. '
                     f'Categorías disponibles: {", ".join(disponibles) or "(ninguna)"}')
        detalles = detalles[coincide]

    # Monto neto de cada venta = suma de sus líneas (filtradas) -> merge con la cabecera.
    # how='inner' descarta las ventas que no tienen líneas de la categoría elegida.
    montos = (detalles.groupby('venta_id', as_index=False)['subtotal_neto'].sum()
              .rename(columns={'subtotal_neto': 'total_neto'}))
    ventas = cabeceras.merge(montos, on='venta_id', how='inner')

    print(f'Datos cargados: {len(ventas)} ventas, {len(detalles)} líneas de detalle. {filtros.texto()}')
    return detalles, ventas


# =============================================================================
# 3. UTILIDADES DE GRÁFICOS
# =============================================================================
def formato_moneda(valor, _pos=None):
    """$ 12.345 (separador de miles argentino)."""
    return '$ ' + f'{valor:,.0f}'.replace(',', '.')


def guardar(fig, carpeta, nombre):
    ruta = carpeta / nombre
    fig.savefig(ruta, bbox_inches='tight', facecolor='white')
    plt.close(fig)  # liberar memoria (importante si se ejecuta en un job)
    print(f'  [OK] {ruta.relative_to(BASE_DIR) if ruta.is_relative_to(BASE_DIR) else ruta}')
    return ruta


def figura_sin_datos(titulo, mensaje='No hay datos suficientes para este gráfico.'):
    """Gráfico de reemplazo para que el script no falle con la base vacía."""
    fig, ax = plt.subplots(figsize=(10, 5))
    ax.set_title(titulo, loc='left')
    ax.text(0.5, 0.5, mensaje, ha='center', va='center', fontsize=13, color=COLOR_MUTED, transform=ax.transAxes)
    ax.axis('off')
    return fig


def pie_de_figura(fig, texto, filtros):
    """Nota al pie: dato clave del gráfico + filtros con los que se generó."""
    fig.text(0.01, -0.02, f'{texto}\n{filtros.texto()}', fontsize=8, color=COLOR_MUTED, ha='left', va='top',
             linespacing=1.6)


# =============================================================================
# 4. GRÁFICOS
# =============================================================================
def grafico_ventas_mensuales_por_categoria(detalles, carpeta, filtros):
    """Gráfico 1: barras agrupadas -> unidades vendidas por mes y categoría."""
    titulo = 'Ventas mensuales por categoría de producto'
    if detalles.empty:
        return guardar(figura_sin_datos(titulo), carpeta, ARCHIVOS['categorias'])

    # Agrupar por mes (período) y categoría sumando cantidades netas
    datos = detalles.assign(mes=detalles['fecha'].dt.to_period('M'))
    tabla = (
        datos.groupby(['mes', 'categoria'])['cantidad_neta'].sum()
        .unstack(fill_value=0)          # filas = meses, columnas = categorías
        .sort_index()
    )
    tabla = tabla[tabla.sum().sort_values(ascending=False).index]  # categorías de mayor a menor

    meses = tabla.index
    categorias = tabla.columns
    x = np.arange(len(meses))                      # posición de cada grupo (mes)
    # Ancho de cada barra dentro del grupo (máx. 0.22 para que no queden gigantes con pocos datos)
    ancho = min(0.8 / len(categorias), 0.22)

    fig, ax = plt.subplots(figsize=(max(10, 1.6 * len(meses) * max(1, len(categorias) / 2)), 6))
    ax.set_xlim(-0.6, len(meses) - 0.4)
    for i, categoria in enumerate(categorias):
        desplazamiento = (i - (len(categorias) - 1) / 2) * ancho
        barras = ax.bar(x + desplazamiento, tabla[categoria].values, ancho,
                        label=categoria.capitalize(), color=PALETA_CATEGORIAS[i % len(PALETA_CATEGORIAS)])
        ax.bar_label(barras, labels=[f'{v:.0f}' if v else '' for v in tabla[categoria].values],
                     padding=2, fontsize=8, color=COLOR_TEXTO)

    # Promedio mensual de unidades (numpy) como referencia
    promedio_mensual = np.mean(tabla.sum(axis=1).values)
    ax.set_title(titulo, loc='left')
    ax.set_xticks(x, [f'{MESES_ES[p.month - 1]} {p.year}' for p in meses])
    ax.set_ylabel('Unidades vendidas (netas de devoluciones)')
    ax.yaxis.set_major_locator(mticker.MaxNLocator(integer=True))
    ax.grid(axis='x', visible=False)
    ax.legend(title='Categoría', frameon=False, loc='upper left', bbox_to_anchor=(1.0, 1.0))
    pie_de_figura(fig, f'Promedio mensual: {promedio_mensual:.1f} unidades · Total del período: {tabla.values.sum():.0f} unidades.', filtros)
    return guardar(fig, carpeta, ARCHIVOS['categorias'])


def grafico_metodos_de_pago(ventas, carpeta, filtros):
    """Gráfico 2: donut -> participación de Efectivo vs Transferencia en el monto vendido."""
    titulo = 'Participación de métodos de pago'
    # Las ventas fiadas no tienen método de pago hasta que se cancelan con un Pago
    cobradas = ventas[ventas['metodo_pago'].isin(['efectivo', 'transferencia'])]
    if cobradas.empty or cobradas['total_neto'].sum() <= 0:
        return guardar(figura_sin_datos(titulo), carpeta, ARCHIVOS['metodos_pago'])

    montos = cobradas.groupby('metodo_pago')['total_neto'].sum().reindex(['efectivo', 'transferencia'], fill_value=0)
    cantidades = cobradas.groupby('metodo_pago').size().reindex(['efectivo', 'transferencia'], fill_value=0)
    porcentajes = montos.values / montos.values.sum() * 100   # numpy: proporción de cada método

    # Solo se dibujan las porciones con monto > 0 (una porción de $0 no tiene área
    # y su etiqueta se superpone con el título); igual figuran en el pie de figura.
    colores = pd.Series([COLOR_SECUNDARIO, COLOR_PRIMARIO], index=['efectivo', 'transferencia'])
    visibles = montos[montos > 0].index

    fig, ax = plt.subplots(figsize=(8, 7))
    etiquetas = [f'{m.capitalize()}\n{formato_moneda(montos[m])}\n({cantidades[m]} ventas)' for m in visibles]
    _cunas, _textos, autotextos = ax.pie(
        montos[visibles].values, labels=etiquetas, colors=colores[visibles].tolist(), labeldistance=1.12,
        autopct=lambda p: f'{p:.1f}%' if p > 0 else '', pctdistance=0.78, startangle=90, counterclock=False,
        wedgeprops={'width': 0.42, 'edgecolor': 'white', 'linewidth': 2},
        textprops={'fontsize': 10, 'color': COLOR_TEXTO},
    )
    for t in autotextos:
        t.set_color('white')
        t.set_fontweight('bold')
    ax.text(0, 0.08, formato_moneda(montos.sum()), ha='center', va='center', fontsize=17, fontweight='bold', color=COLOR_TEXTO)
    ax.text(0, -0.12, 'total cobrado', ha='center', va='center', fontsize=10, color=COLOR_MUTED)
    ax.set_title(titulo, loc='left', pad=24)
    ax.axis('equal')

    fiado = ventas.loc[ventas['estado_pago'] == 'fiado', 'total_neto']
    pie_de_figura(fig, f'Efectivo {porcentajes[0]:.1f}% · Transferencia {porcentajes[1]:.1f}%. '
                       f'No incluye ventas fiadas sin método de pago ({len(fiado)} ventas, {formato_moneda(fiado.sum())}).', filtros)
    return guardar(fig, carpeta, ARCHIVOS['metodos_pago'])


def grafico_facturacion_diaria(ventas, carpeta, filtros):
    """Gráfico 3: facturación diaria (últimos N días o el rango --desde/--hasta) con tendencia lineal."""
    desde, hasta = filtros.inicio_evolucion, filtros.hasta
    dias = (hasta - desde).days + 1
    titulo = (f'Facturación diaria - del {desde:%d/%m/%Y} al {hasta:%d/%m/%Y}' if filtros.desde
              else f'Facturación diaria - últimos {dias} días')
    if dias < 2:
        return guardar(figura_sin_datos(titulo, 'El rango debe tener al menos 2 días para calcular una tendencia.'),
                       carpeta, ARCHIVOS['evolucion'])

    # Serie diaria completa: los días sin ventas quedan en 0 (reindex)
    rango = pd.date_range(desde, hasta, freq='D')
    diaria = (
        ventas[ventas['fecha'] >= pd.Timestamp(desde)]
        .groupby('fecha')['total_neto'].sum()
        .reindex(rango, fill_value=0.0)
    )
    if diaria.sum() <= 0:
        return guardar(figura_sin_datos(titulo, f'No hay ventas entre el {desde:%d/%m/%Y} y el {hasta:%d/%m/%Y}.'),
                       carpeta, ARCHIVOS['evolucion'])

    # --- Cálculos con numpy ---
    y = diaria.values
    x = np.arange(len(y))
    promedio = np.mean(y)
    desvio = np.std(y, ddof=1) if len(y) > 1 else 0.0       # desvío estándar muestral
    pendiente, ordenada = np.polyfit(x, y, deg=1)           # regresión lineal (mínimos cuadrados)
    tendencia = pendiente * x + ordenada
    media_movil = diaria.rolling(window=7, min_periods=1).mean()
    dia_max = diaria.idxmax()

    fig, ax = plt.subplots(figsize=(13, 6))
    ax.fill_between(rango, max(promedio - desvio, 0), promedio + desvio, color=COLOR_PRIMARIO, alpha=0.07,
                    label=f'Promedio ± 1 desvío ({formato_moneda(desvio)})')
    ax.plot(rango, y, marker='o', markersize=4, linewidth=2, color=COLOR_PRIMARIO, label='Facturación diaria')
    ax.plot(rango, media_movil, linewidth=1.5, color=COLOR_SECUNDARIO, alpha=0.9, label='Media móvil 7 días')
    ax.plot(rango, tendencia, linestyle='--', linewidth=2, color=COLOR_PELIGRO if pendiente < 0 else COLOR_TERCIARIO,
            label=f'Tendencia lineal ({"+" if pendiente >= 0 else "−"}{formato_moneda(abs(pendiente))}/día)')
    ax.axhline(promedio, linestyle=':', linewidth=1.2, color=COLOR_MUTED, label=f'Promedio ({formato_moneda(promedio)})')
    ax.annotate(f'Máximo\n{formato_moneda(diaria.max())}', xy=(dia_max, diaria.max()), xytext=(0, 18),
                textcoords='offset points', ha='center', fontsize=8, color=COLOR_TEXTO,
                arrowprops={'arrowstyle': '-', 'color': COLOR_MUTED})

    ax.set_title(titulo, loc='left')
    ax.set_ylabel('Facturación neta')
    ax.yaxis.set_major_formatter(mticker.FuncFormatter(formato_moneda))
    ax.set_ylim(bottom=0)
    ax.set_xlim(rango[0] - pd.Timedelta(hours=12), rango[-1] + pd.Timedelta(hours=12))
    ax.xaxis.set_major_locator(mdates.DayLocator(interval=max(1, dias // 15)))
    ax.xaxis.set_major_formatter(mdates.DateFormatter('%d/%m'))
    ax.legend(frameon=False, fontsize=9, loc='upper left', ncol=2)
    pie_de_figura(fig, f'Período {desde:%d/%m/%Y} - {hasta:%d/%m/%Y} · Total {formato_moneda(y.sum())} · '
                       f'Días con ventas: {int(np.count_nonzero(y))}/{len(y)} · Incluye ventas pagadas y fiadas.', filtros)
    return guardar(fig, carpeta, ARCHIVOS['evolucion'])


def grafico_heatmap_dia_hora(ventas, carpeta, filtros):
    """Gráfico 4: mapa de calor -> cantidad de ventas por día de la semana y hora."""
    titulo = 'Cantidad de ventas por día de la semana y hora'
    datos = ventas.dropna(subset=['hora', 'dia_semana'])
    if datos.empty:
        return guardar(figura_sin_datos(titulo), carpeta, ARCHIVOS['heatmap'])

    # Matriz 7 x 24 (días x horas) con todas las celdas, aunque no tengan ventas
    matriz = (
        datos.pivot_table(index='dia_semana', columns='hora', values='venta_id', aggfunc='count', fill_value=0)
        .reindex(index=range(7), columns=range(24), fill_value=0)
    )
    valores = matriz.values.astype(int)

    fig, ax = plt.subplots(figsize=(15, 5.2))
    imagen = ax.imshow(valores, cmap='YlOrRd', aspect='auto', vmin=0, vmax=max(1, valores.max()))
    ax.grid(False)
    ax.set_xticks(np.arange(24), [f'{h:02d}' for h in range(24)])
    ax.set_yticks(np.arange(7), DIAS_ES)
    ax.set_xlabel('Hora del día')
    ax.set_title(titulo, loc='left')
    # Bordes blancos entre celdas
    ax.set_xticks(np.arange(-0.5, 24, 1), minor=True)
    ax.set_yticks(np.arange(-0.5, 7, 1), minor=True)
    ax.grid(which='minor', color='white', linewidth=1.5)
    ax.tick_params(which='minor', length=0)
    for side in ('top', 'right', 'left', 'bottom'):
        ax.spines[side].set_visible(False)

    # Número dentro de cada celda con ventas (texto blanco en celdas oscuras)
    umbral = valores.max() * 0.6
    for (fila, col), valor in np.ndenumerate(valores):
        if valor:
            ax.text(col, fila, str(valor), ha='center', va='center', fontsize=8,
                    color='white' if valor > umbral else COLOR_TEXTO, fontweight='bold')

    barra = fig.colorbar(imagen, ax=ax, fraction=0.025, pad=0.01)
    barra.set_label('Ventas')
    barra.locator = mticker.MaxNLocator(integer=True)
    barra.update_ticks()

    fila_max, col_max = np.unravel_index(np.argmax(valores), valores.shape)   # numpy: celda pico
    por_dia = valores.sum(axis=1)
    pie_de_figura(fig, f'Pico: {DIAS_ES[fila_max]} a las {col_max:02d} hs ({valores[fila_max, col_max]} ventas) · '
                       f'Día más activo: {DIAS_ES[int(np.argmax(por_dia))]} · Total: {valores.sum()} ventas.', filtros)
    return guardar(fig, carpeta, ARCHIVOS['heatmap'])


def _tarjeta_kpi(ax, etiqueta, valor, detalle, color):
    """Dibuja una tarjeta de KPI (estilo de las tarjetas del sistema) en un eje vacío."""
    ax.axis('off')
    ax.add_patch(FancyBboxPatch((0, 0), 1, 1, boxstyle='round,pad=0,rounding_size=0.06', transform=ax.transAxes,
                                facecolor='#f8fafc', edgecolor='#e2e8f0', linewidth=1, clip_on=False))
    ax.add_patch(FancyBboxPatch((0, 0), 0.025, 1, boxstyle='square,pad=0', transform=ax.transAxes,
                                facecolor=color, edgecolor='none', clip_on=False))
    ax.text(0.08, 0.74, etiqueta.upper(), transform=ax.transAxes, fontsize=8.5, fontweight='bold', color=COLOR_MUTED)
    ax.text(0.08, 0.40, valor, transform=ax.transAxes, fontsize=19, fontweight='bold', color=COLOR_TEXTO)
    ax.text(0.08, 0.13, detalle, transform=ax.transAxes, fontsize=8.5, color=COLOR_MUTED)


def grafico_dashboard(detalles, ventas, carpeta, filtros):
    """Gráfico 5 (panel integrado): KPIs + 4 vistas en una sola figura con matplotlib.gridspec."""
    titulo = 'Dashboard Ejecutivo'
    if ventas.empty:
        return guardar(figura_sin_datos(titulo), carpeta, ARCHIVOS['dashboard'])

    # --- KPIs con numpy ---
    montos = ventas['total_neto'].to_numpy()
    total = np.sum(montos)
    ticket = np.mean(montos)
    desvio = np.std(montos, ddof=1) if montos.size > 1 else 0.0
    fiado = np.sum(ventas.loc[ventas['estado_pago'] == 'fiado', 'total_neto'].to_numpy())
    unidades = np.sum(detalles['cantidad_neta'].to_numpy())

    fig = plt.figure(figsize=(16, 11))
    gs = fig.add_gridspec(3, 4, height_ratios=[0.42, 1.35, 1.35], hspace=0.42, wspace=0.32, top=0.89, bottom=0.07)
    fig.suptitle('Dashboard Ejecutivo · Almacén El Refugio', x=0.01, y=0.985, ha='left',
                 fontsize=19, fontweight='bold', color=COLOR_TEXTO)
    periodo = f"{filtros.desde:%d/%m/%Y}" if filtros.desde else f"{ventas['fecha'].min():%d/%m/%Y}"
    fig.text(0.01, 0.945, f'Período {periodo} - {filtros.hasta:%d/%m/%Y}'
             f'{" · Categoría: " + filtros.categoria if filtros.categoria else ""}', fontsize=11, color=COLOR_MUTED)

    kpis = [
        ('Facturación neta', formato_moneda(total), f'{montos.size} ventas', COLOR_PRIMARIO),
        ('Ticket promedio', formato_moneda(ticket), f'Desvío estándar {formato_moneda(desvio)}', COLOR_SECUNDARIO),
        ('Unidades vendidas', f'{unidades:,.0f}'.replace(',', '.'),
         f'{detalles["producto"].nunique()} productos distintos', COLOR_TERCIARIO),
        ('Ventas fiadas', f'{(fiado / total * 100) if total else 0:.1f} %', f'{formato_moneda(fiado)} a cobrar', COLOR_PELIGRO),
    ]
    for i, kpi in enumerate(kpis):
        _tarjeta_kpi(fig.add_subplot(gs[0, i]), *kpi)

    # --- Facturación acumulada (serie diaria completa + cumsum) ---
    ax = fig.add_subplot(gs[1, :3])
    inicio = filtros.desde or ventas['fecha'].min().date()
    rango = pd.date_range(inicio, filtros.hasta, freq='D')
    acumulada = ventas.groupby('fecha')['total_neto'].sum().reindex(rango, fill_value=0.0).cumsum()
    ax.fill_between(rango, acumulada.values, color=COLOR_PRIMARIO, alpha=0.15)
    ax.plot(rango, acumulada.values, color=COLOR_PRIMARIO, linewidth=2.2)
    ax.set_title('Facturación acumulada', loc='left', fontsize=12)
    ax.yaxis.set_major_formatter(mticker.FuncFormatter(formato_moneda))
    ax.xaxis.set_major_locator(mdates.DayLocator(interval=max(1, len(rango) // 10)))   # 1 marca por día como mínimo
    ax.xaxis.set_major_formatter(mdates.DateFormatter('%d/%m'))
    ax.set_ylim(bottom=0)
    ax.annotate(formato_moneda(acumulada.iloc[-1]), xy=(rango[-1], acumulada.iloc[-1]), xytext=(-6, 6),
                textcoords='offset points', ha='right', fontsize=9, fontweight='bold', color=COLOR_PRIMARIO)

    # --- Composición del cobro (incluye fiado) ---
    ax = fig.add_subplot(gs[1, 3])
    forma = np.where(ventas['estado_pago'] == 'fiado', 'Fiado', ventas['metodo_pago'].fillna('otro').str.capitalize())
    composicion = ventas.assign(forma=forma).groupby('forma')['total_neto'].sum()
    composicion = composicion[composicion > 0].reindex(['Efectivo', 'Transferencia', 'Fiado']).dropna()
    colores = {'Efectivo': COLOR_SECUNDARIO, 'Transferencia': COLOR_PRIMARIO, 'Fiado': COLOR_TERCIARIO}
    ax.pie(composicion.values, colors=[colores[k] for k in composicion.index], startangle=90, counterclock=False,
           autopct=lambda p: f'{p:.0f}%' if p >= 4 else '', pctdistance=0.77,
           wedgeprops={'width': 0.42, 'edgecolor': 'white', 'linewidth': 2},
           textprops={'fontsize': 9, 'color': 'white', 'fontweight': 'bold'})
    ax.set_title('Composición del cobro', loc='left', fontsize=12)
    ax.legend([f'{k} ({formato_moneda(v)})' for k, v in composicion.items()], loc='upper center',
              bbox_to_anchor=(0.5, 0.0), frameon=False, fontsize=8.5)
    ax.axis('equal')

    # --- Top 5 productos por facturación ---
    ax = fig.add_subplot(gs[2, :2])
    top = detalles.groupby('producto')['subtotal_neto'].sum().nlargest(5).sort_values()
    barras = ax.barh(top.index, top.values,
                     color=[COLOR_PRIMARIO if v == top.max() else '#93c5fd' for v in top.values])
    ax.bar_label(barras, labels=[formato_moneda(v) for v in top.values], padding=4, fontsize=8.5, color=COLOR_TEXTO)
    ax.set_title('Top 5 productos por facturación', loc='left', fontsize=12)
    ax.xaxis.set_major_formatter(mticker.FuncFormatter(formato_moneda))
    ax.set_xlim(0, top.max() * 1.25 if len(top) else 1)
    ax.grid(axis='y', visible=False)

    # --- Facturación por día de la semana ---
    ax = fig.add_subplot(gs[2, 2:])
    por_dia = ventas.groupby('dia_semana')['total_neto'].sum().reindex(range(7), fill_value=0.0)
    promedio_dia = np.mean(por_dia.values)
    ax.bar([d[:3] for d in DIAS_ES], por_dia.values,
           color=[COLOR_SECUNDARIO if v == por_dia.max() and v > 0 else '#a7f3d0' for v in por_dia.values])
    ax.axhline(promedio_dia, linestyle='--', linewidth=1.2, color=COLOR_MUTED)
    ax.text(-0.45, promedio_dia, f'prom. {formato_moneda(promedio_dia)}', va='bottom', ha='left',
            fontsize=8, color=COLOR_MUTED)
    ax.set_title('Facturación por día de la semana', loc='left', fontsize=12)
    ax.yaxis.set_major_formatter(mticker.FuncFormatter(formato_moneda))
    ax.grid(axis='x', visible=False)

    pie_de_figura(fig, 'Panel integrado generado con matplotlib.gridspec.', filtros)
    return guardar(fig, carpeta, ARCHIVOS['dashboard'])


# =============================================================================
# 5. RESUMEN EN CONSOLA (estadística descriptiva con numpy)
# =============================================================================
def imprimir_resumen(ventas):
    if ventas.empty:
        print('\nNo hay ventas registradas.')
        return
    tickets = ventas['total_neto'].to_numpy()
    print('\nResumen estadístico de ventas (no anuladas, netas de devoluciones)')
    print(f'  Cantidad de ventas : {tickets.size}')
    print(f'  Facturación total  : {formato_moneda(np.sum(tickets))}')
    print(f'  Ticket promedio    : {formato_moneda(np.mean(tickets))}')
    print(f'  Ticket mediano     : {formato_moneda(np.median(tickets))}')
    print(f'  Desvío estándar    : {formato_moneda(np.std(tickets, ddof=1) if tickets.size > 1 else 0)}')
    print(f'  Percentil 90       : {formato_moneda(np.percentile(tickets, 90))}')


def guardar_metadatos(carpeta, filtros, ventas):
    """Guarda con qué filtros se generaron las imágenes (lo lee el panel de Django)."""
    datos = {
        'fecha_inicio': filtros.desde.isoformat() if filtros.desde else None,
        'fecha_fin': filtros.hasta.isoformat(),
        'categoria': filtros.categoria,
        'generado': datetime.now().isoformat(timespec='seconds'),
        'cantidad_ventas': int(len(ventas)),
        'facturacion': float(np.sum(ventas['total_neto'].to_numpy())) if len(ventas) else 0.0,
    }
    with open(carpeta / ARCHIVO_METADATOS, 'w', encoding='utf-8') as archivo:
        json.dump(datos, archivo, ensure_ascii=False, indent=2)


# =============================================================================
# 6. PROGRAMA PRINCIPAL
# =============================================================================
def parsear_argumentos():
    def fecha(texto):
        try:
            return datetime.strptime(texto, '%Y-%m-%d').date()
        except ValueError:
            raise argparse.ArgumentTypeError(f'"{texto}" no es una fecha válida (formato YYYY-MM-DD).')

    parser = argparse.ArgumentParser(description='Genera los gráficos de analítica de ventas de El Refugio.')
    # --fecha_inicio / --fecha_fin son alias de --desde / --hasta (los usa el panel de Django)
    parser.add_argument('--desde', '--fecha_inicio', dest='desde', type=fecha, default=None,
                        help='Fecha inicial YYYY-MM-DD (por defecto: todo el historial).')
    parser.add_argument('--hasta', '--fecha_fin', dest='hasta', type=fecha, default=date.today(),
                        help='Fecha final YYYY-MM-DD (por defecto: hoy). Fijarla hace el resultado reproducible.')
    parser.add_argument('--categoria', default=None, help='Analizar solo esta categoría de producto (ej: bebidas).')
    parser.add_argument('--dias', type=int, default=30,
                        help='Días del gráfico de evolución cuando no se indica --desde (default 30).')
    parser.add_argument('--salida', default=CARPETA_ESTATICA, help='Carpeta de salida (default: static/analitica/).')
    args = parser.parse_args()
    if args.dias < 2:
        parser.error('--dias debe ser al menos 2 para calcular una tendencia.')
    if args.desde and args.desde > args.hasta:
        parser.error('--desde no puede ser posterior a --hasta.')
    return args


def main():
    args = parsear_argumentos()
    carpeta = Path(args.salida)
    if not carpeta.is_absolute():
        carpeta = BASE_DIR / carpeta
    # Crear la carpeta de salida (static/analitica/ por defecto) si no existe
    os.makedirs(carpeta, exist_ok=True)

    try:
        conexion = conectar()
    except mysql.connector.Error as error:
        sys.exit(f'Error al conectar a MySQL: {error}\n¿Está levantado el contenedor? (docker compose up -d db)')

    filtros = Filtros(hasta=args.hasta, desde=args.desde, categoria=args.categoria, dias=args.dias)
    try:
        detalles, ventas = cargar_datos(conexion, filtros)
    finally:
        conexion.close()

    print(f'\nGenerando gráficos en {carpeta}:')
    grafico_ventas_mensuales_por_categoria(detalles, carpeta, filtros)
    grafico_metodos_de_pago(ventas, carpeta, filtros)
    grafico_facturacion_diaria(ventas, carpeta, filtros)
    grafico_heatmap_dia_hora(ventas, carpeta, filtros)
    grafico_dashboard(detalles, ventas, carpeta, filtros)
    guardar_metadatos(carpeta, filtros, ventas)
    imprimir_resumen(ventas)
    print('\nListo.')


if __name__ == '__main__':
    main()
