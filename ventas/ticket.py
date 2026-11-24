"""
Comprobante de venta en formato ticket para impresora térmica.

- Ancho configurable (settings.TICKET_ANCHO_MM: 80 por defecto, 58 para
  impresoras chicas).
- El largo de la hoja se calcula según el contenido, así la impresora no
  avanza papel en blanco.
"""
from collections import OrderedDict
from decimal import Decimal
from io import BytesIO
from xml.sax.saxutils import escape

from django.conf import settings
from django.utils import timezone
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_RIGHT
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import BaseDocTemplate, Frame, HRFlowable, PageTemplate, Paragraph, Table, TableStyle

NOMBRE_COMERCIO = 'ALMACÉN EL REFUGIO'
MARGEN = 3 * mm
FUENTE = 'Helvetica'
FUENTE_NEGRITA = 'Helvetica-Bold'


def _dinero(valor):
    """$1.234,50 (formato argentino)."""
    return f"${Decimal(valor):,.2f}".replace(',', 'X').replace('.', ',').replace('X', '.')


def _estilos(ancho_mm):
    # En papel de 58 mm todo un punto más chico para que entren los importes.
    base = 8 if ancho_mm >= 70 else 7
    return {
        'comercio': ParagraphStyle('comercio', fontName=FUENTE_NEGRITA, fontSize=base + 4, leading=base + 6, alignment=TA_CENTER),
        'centro': ParagraphStyle('centro', fontName=FUENTE, fontSize=base, leading=base + 2, alignment=TA_CENTER),
        'centro_negrita': ParagraphStyle('centro_negrita', fontName=FUENTE_NEGRITA, fontSize=base, leading=base + 2, alignment=TA_CENTER),
        'normal': ParagraphStyle('normal', fontName=FUENTE, fontSize=base, leading=base + 2),
        'derecha': ParagraphStyle('derecha', fontName=FUENTE, fontSize=base, leading=base + 2, alignment=TA_RIGHT),
        'producto': ParagraphStyle('producto', fontName=FUENTE_NEGRITA, fontSize=base, leading=base + 2),
        'total': ParagraphStyle('total', fontName=FUENTE_NEGRITA, fontSize=base + 5, leading=base + 7),
        'total_monto': ParagraphStyle('total_monto', fontName=FUENTE_NEGRITA, fontSize=base + 5, leading=base + 7, alignment=TA_RIGHT),
        'anulada': ParagraphStyle('anulada', fontName=FUENTE_NEGRITA, fontSize=base + 6, leading=base + 9,
                                  alignment=TA_CENTER, textColor=colors.black, borderWidth=1, borderColor=colors.black,
                                  borderPadding=3, spaceBefore=4, spaceAfter=6),
    }


def _separador():
    return HRFlowable(width='100%', thickness=0.6, dash=(2, 2), color=colors.black, spaceBefore=4, spaceAfter=4)


def _renglones(venta):
    """
    Agrupa los detalles por producto y precio: una venta que descontó stock de
    dos lotes del mismo producto se ve como un solo renglón en el ticket.
    """
    grupos = OrderedDict()
    for detalle in venta.detalles.select_related('producto').prefetch_related('devoluciones').order_by('pk'):
        clave = (detalle.producto_id, detalle.precio_unitario_venta)
        grupo = grupos.setdefault(clave, {
            'nombre': detalle.producto.nombre, 'marca': detalle.producto.marca,
            'precio': detalle.precio_unitario_venta, 'cantidad': 0, 'subtotal': Decimal('0'), 'devuelto': 0,
        })
        grupo['cantidad'] += detalle.cantidad
        grupo['subtotal'] += detalle.subtotal
        grupo['devuelto'] += detalle.cantidad_devuelta
    return list(grupos.values())


def _contenido(venta, ancho_util, ancho_mm):
    e = _estilos(ancho_mm)
    historia = [
        Paragraph(escape(NOMBRE_COMERCIO), e['comercio']),
        Paragraph('Comprobante de venta', e['centro']),
        Paragraph('<b>DOCUMENTO NO VÁLIDO COMO FACTURA</b>', e['centro']),
        _separador(),
    ]

    if venta.estado_de_pago == 'anulado':
        historia.append(Paragraph('VENTA ANULADA', e['anulada']))

    atendio = venta.id_caja.usuario.username if venta.id_caja_id and venta.id_caja.usuario_id else '-'
    for etiqueta, valor in (
        ('Ticket N°', f'{venta.id_ventas:08d}'),
        ('Fecha', f'{venta.fecha:%d/%m/%Y} {venta.hora:%H:%M}'),
        ('Caja', f'#{venta.id_caja_id} · {atendio}'),
        ('Cliente', venta.cliente_info),
    ):
        historia.append(Paragraph(f'<b>{etiqueta}:</b> {escape(str(valor))}', e['normal']))
    historia.append(_separador())

    # Renglones: nombre en una línea y "cant x precio ..... subtotal" debajo.
    filas, estilo_tabla = [], [
        ('LEFTPADDING', (0, 0), (-1, -1), 0), ('RIGHTPADDING', (0, 0), (-1, -1), 0),
        ('TOPPADDING', (0, 0), (-1, -1), 0), ('BOTTOMPADDING', (0, 0), (-1, -1), 1),
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
    ]
    for renglon in _renglones(venta):
        nombre = renglon['nombre']
        if renglon['marca'] and renglon['marca'] != 'Sin marca':
            nombre = f"{nombre} - {renglon['marca']}"
        fila_nombre = len(filas)
        filas.append([Paragraph(escape(nombre), e['producto']), ''])
        estilo_tabla.append(('SPAN', (0, fila_nombre), (1, fila_nombre)))
        filas.append([
            Paragraph(f"{renglon['cantidad']} x {_dinero(renglon['precio'])}", e['normal']),
            Paragraph(_dinero(renglon['subtotal']), e['derecha']),
        ])
        if renglon['devuelto']:
            filas.append([Paragraph(f"  Devuelto: {renglon['devuelto']} u.", e['normal']), ''])
        estilo_tabla.append(('BOTTOMPADDING', (0, len(filas) - 1), (-1, len(filas) - 1), 4))

    if filas:
        tabla = Table(filas, colWidths=[ancho_util * 0.58, ancho_util * 0.42])
        tabla.setStyle(TableStyle(estilo_tabla))
        historia.append(tabla)
    historia.append(_separador())

    unidades = sum(r['cantidad'] for r in _renglones(venta))
    historia.append(Paragraph(f'Cantidad de artículos: {unidades}', e['normal']))
    total = Table(
        [[Paragraph('TOTAL', e['total']), Paragraph(_dinero(venta.total), e['total_monto'])]],
        colWidths=[ancho_util * 0.4, ancho_util * 0.6],
    )
    total.setStyle(TableStyle([
        ('LEFTPADDING', (0, 0), (-1, -1), 0), ('RIGHTPADDING', (0, 0), (-1, -1), 0),
        ('TOPPADDING', (0, 0), (-1, -1), 3), ('BOTTOMPADDING', (0, 0), (-1, -1), 3),
    ]))
    historia.append(total)

    if venta.estado_de_pago == 'fiado':
        forma_pago = 'Cuenta corriente (fiado)'
    else:
        forma_pago = venta.get_metodo_pago_display() or venta.get_estado_de_pago_display()
    historia.append(Paragraph(f'<b>Forma de pago:</b> {escape(forma_pago)}', e['normal']))
    historia.append(_separador())
    historia.append(Paragraph('¡Gracias por su compra!', e['centro_negrita']))
    historia.append(Paragraph(f'Impreso: {timezone.localtime():%d/%m/%Y %H:%M}', e['centro']))
    return historia


def generar_ticket_pdf(venta):
    """Devuelve los bytes del PDF del ticket de la venta."""
    ancho_mm = getattr(settings, 'TICKET_ANCHO_MM', 80)
    ancho = ancho_mm * mm
    ancho_util = ancho - 2 * MARGEN

    # El largo se mide con el contenido real para que el ticket no tenga papel de más.
    alto = 2 * MARGEN + 6 * mm   # pequeño margen extra para el corte
    for flowable in _contenido(venta, ancho_util, ancho_mm):
        _, h = flowable.wrap(ancho_util, 100000)
        alto += h + flowable.getSpaceBefore() + flowable.getSpaceAfter()

    buffer = BytesIO()
    doc = BaseDocTemplate(
        buffer, pagesize=(ancho, alto),
        leftMargin=MARGEN, rightMargin=MARGEN, topMargin=MARGEN, bottomMargin=MARGEN,
        title=f'Ticket venta #{venta.id_ventas}', author=NOMBRE_COMERCIO,
    )
    # Frame sin padding interno (el de SimpleDocTemplate suma 6 pt por lado):
    # así el ancho medido arriba es exactamente el ancho en el que se imprime.
    marco = Frame(MARGEN, MARGEN, ancho_util, alto - 2 * MARGEN,
                  leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0, id='ticket')
    doc.addPageTemplates([PageTemplate(id='ticket', frames=[marco])])
    doc.build(_contenido(venta, ancho_util, ancho_mm))
    return buffer.getvalue()
