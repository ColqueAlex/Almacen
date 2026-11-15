"""
Generación de PDF para los reportes consolidados.

Sigue el mismo estilo que ventas.views.generar_comprobante_pdf (ReportLab
platypus, encabezado "ALMACÉN EL REFUGIO", tabla con cabecera azul) y agrega:
  - encabezado institucional con logo,
  - pie de página en todas las hojas con filtros aplicados, usuario y fecha de emisión,
  - numeración de páginas y cabecera de tabla repetida en cada hoja.
"""
from io import BytesIO
from xml.sax.saxutils import escape

from django.conf import settings
from django.http import HttpResponse
from django.utils import timezone
from reportlab.lib import colors
from reportlab.lib.pagesizes import letter, landscape
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.utils import ImageReader
from reportlab.platypus import Image, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

AZUL = colors.HexColor('#0d6efd')
GRIS_CLARO = colors.HexColor('#f8f9fa')
LOGO = settings.BASE_DIR / 'Almacen' / 'static' / 'img' / 'logo.png'


def _logo(alto=40):
    if not LOGO.exists():
        return None
    ancho_img, alto_img = ImageReader(str(LOGO)).getSize()
    return Image(str(LOGO), width=alto * ancho_img / alto_img, height=alto)


def generar_reporte_pdf(*, titulo, subtitulo, columnas, filas, fila_totales, filtros_texto,
                        usuario, nombre_archivo, anchos=None, horizontal=False, alinear_derecha=()):
    """
    columnas: lista de encabezados.
    filas: lista de listas de strings (ya formateadas).
    fila_totales: lista de strings para la última fila (o None).
    alinear_derecha: índices de columnas numéricas.
    """
    tamano = landscape(letter) if horizontal else letter
    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer, pagesize=tamano, rightMargin=30, leftMargin=30, topMargin=30, bottomMargin=60,
        title=f'{titulo} - El Refugio', author=usuario,
    )
    styles = getSampleStyleSheet()
    style_title = ParagraphStyle('Title', parent=styles['Heading1'], fontSize=16, alignment=1, spaceAfter=4)
    style_report = ParagraphStyle('Report', parent=styles['Heading2'], fontSize=13, alignment=1, spaceAfter=2, textColor=AZUL)
    style_sub = ParagraphStyle('Sub', parent=styles['Normal'], fontSize=9, alignment=1, spaceAfter=14, textColor=colors.grey)
    style_footer = ParagraphStyle('Footer', parent=styles['Normal'], fontSize=7.5, leading=9, textColor=colors.HexColor('#475569'))
    style_vacio = ParagraphStyle('Vacio', parent=styles['Normal'], fontSize=10, alignment=1, textColor=colors.grey)

    fecha_emision = timezone.localtime().strftime('%d/%m/%Y %H:%M')
    texto_pie = (
        f'<b>Filtros aplicados:</b> {escape(filtros_texto)} &nbsp;|&nbsp; '
        f'<b>Usuario:</b> {escape(usuario)} &nbsp;|&nbsp; '
        f'<b>Fecha de emisión:</b> {fecha_emision}'
    )

    def dibujar_pie(canvas, documento):
        canvas.saveState()
        ancho_util = documento.width - 60  # espacio para el número de página
        pie = Paragraph(texto_pie, style_footer)
        _, alto = pie.wrap(ancho_util, documento.bottomMargin)
        y_linea = documento.bottomMargin - 10
        canvas.setStrokeColor(colors.lightgrey)
        canvas.setLineWidth(0.5)
        canvas.line(documento.leftMargin, y_linea, documento.leftMargin + documento.width, y_linea)
        pie.drawOn(canvas, documento.leftMargin, y_linea - 4 - alto)
        canvas.setFont('Helvetica', 7.5)
        canvas.setFillColor(colors.grey)
        canvas.drawRightString(documento.leftMargin + documento.width, y_linea - 4 - 9, f'Página {documento.page}')
        canvas.restoreState()

    story = []
    logo = _logo()
    if logo:
        story.append(logo)
        story.append(Spacer(1, 4))
    story.append(Paragraph('<b>ALMACÉN EL REFUGIO</b>', style_title))
    story.append(Paragraph(escape(titulo), style_report))
    story.append(Paragraph(escape(subtitulo), style_sub))

    if not filas:
        story.append(Spacer(1, 20))
        story.append(Paragraph('No hay datos para los filtros seleccionados.', style_vacio))
    else:
        data = [columnas] + filas + ([fila_totales] if fila_totales else [])
        table = Table(data, colWidths=anchos, repeatRows=1)
        estilo = [
            ('BACKGROUND', (0, 0), (-1, 0), AZUL),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, -1), 8.5),
            ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
            ('BOTTOMPADDING', (0, 0), (-1, 0), 6),
            ('GRID', (0, 0), (-1, -1), 0.5, colors.lightgrey),
            ('ROWBACKGROUNDS', (0, 1), (-1, -2 if fila_totales else -1), [colors.white, GRIS_CLARO]),
        ]
        for col in alinear_derecha:
            estilo.append(('ALIGN', (col, 0), (col, -1), 'RIGHT'))
        if fila_totales:
            estilo += [
                ('FONTNAME', (0, -1), (-1, -1), 'Helvetica-Bold'),
                ('BACKGROUND', (0, -1), (-1, -1), colors.HexColor('#e2e8f0')),
                ('LINEABOVE', (0, -1), (-1, -1), 1, colors.HexColor('#0f172a')),
            ]
        table.setStyle(TableStyle(estilo))
        story.append(table)

    doc.build(story, onFirstPage=dibujar_pie, onLaterPages=dibujar_pie)
    buffer.seek(0)

    response = HttpResponse(buffer, content_type='application/pdf')
    response['Content-Disposition'] = f'inline; filename="{nombre_archivo}"'
    return response
