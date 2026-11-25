import json
from datetime import timedelta
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from caja.models import Caja
from productos.models import LotesProducto, Productos
from usuarios.models import Usuario
from .models import Clientes, DevolucionVenta, DetallesVentas, Ventas


class RegistrarVentaLotesTests(TestCase):
    def setUp(self):
        self.usuario = Usuario.objects.create_user(
            dni='12345678',
            apellido='Prueba',
            nombre='Usuario',
            correo='usuario@example.com',
            password='test-password',
        )
        self.usuario.is_superuser = True
        self.usuario.debe_cambiar_clave = False
        self.usuario.save()
        self.client.force_login(self.usuario)
        self.caja = Caja.objects.create(usuario=self.usuario, monto_inicial=0)

    def crear_venta_con_detalle(self, *, estado='pagado', metodo='efectivo', cliente=None, dias_antes=0):
        fecha_hora = timezone.localtime() - timedelta(days=dias_antes)
        producto = Productos.objects.create(
            nombre='Producto devuelto',
            categoria='Alimentos',
            precio='10.00',
        )
        lote = LotesProducto.objects.create(
            id_producto=producto,
            lote=f'LOTE-{dias_antes}-{estado}',
            stock_actual=0,
            precio_costo='5.00',
        )
        venta = Ventas.objects.create(
            id_caja=self.caja,
            total='30.00',
            fecha=fecha_hora.date(),
            hora=fecha_hora.time().replace(tzinfo=None),
            id_clientes=cliente,
            estado_de_pago=estado,
            metodo_pago=metodo if estado == 'pagado' else None,
        )
        detalle = DetallesVentas.objects.create(
            venta=venta,
            producto=producto,
            lote=lote,
            cantidad=3,
            precio_unitario_venta='10.00',
            subtotal='30.00',
        )
        return venta, detalle, lote

    def test_catalogo_ordena_productos_por_unidades_vendidas(self):
        mas_vendido = Productos.objects.create(
            nombre='Mas vendido',
            categoria='Alimentos',
            precio='10.00',
        )
        segundo_mas_vendido = Productos.objects.create(
            nombre='Segundo mas vendido',
            categoria='Alimentos',
            precio='10.00',
        )
        sin_ventas = Productos.objects.create(
            nombre='Sin ventas',
            categoria='Alimentos',
            precio='10.00',
        )

        venta = Ventas.objects.create(
            id_caja=self.caja,
            total='80.00',
            fecha=timezone.now().date(),
            hora=timezone.now().time().replace(tzinfo=None),
            estado_de_pago='pagado',
            metodo_pago='efectivo',
        )
        DetallesVentas.objects.create(
            venta=venta,
            producto=mas_vendido,
            cantidad=8,
            precio_unitario_venta='10.00',
            subtotal='80.00',
        )
        venta_anulada = Ventas.objects.create(
            id_caja=self.caja,
            total='1000.00',
            fecha=timezone.now().date(),
            hora=timezone.now().time().replace(tzinfo=None),
            estado_de_pago='anulado',
        )
        DetallesVentas.objects.create(
            venta=venta_anulada,
            producto=segundo_mas_vendido,
            cantidad=100,
            precio_unitario_venta='10.00',
            subtotal='1000.00',
        )
        venta_segundo_producto = Ventas.objects.create(
            id_caja=self.caja,
            total='40.00',
            fecha=timezone.now().date(),
            hora=timezone.now().time().replace(tzinfo=None),
            estado_de_pago='pagado',
            metodo_pago='efectivo',
        )
        DetallesVentas.objects.create(
            venta=venta_segundo_producto,
            producto=segundo_mas_vendido,
            cantidad=4,
            precio_unitario_venta='10.00',
            subtotal='40.00',
        )

        response = self.client.get(reverse('registrar_venta'))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            list(response.context['productos'].values_list('id', flat=True)),
            [mas_vendido.id, segundo_mas_vendido.id, sin_ventas.id],
        )

    def test_pantalla_venta_muestra_aviso_cuando_caja_esta_cerrada(self):
        self.caja.estado = 'cerrada'
        self.caja.save(update_fields=['estado'])

        response = self.client.get(reverse('registrar_venta'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'La caja está cerrada')
        self.assertContains(response, 'Ir a caja')
        self.assertContains(response, 'class="btn-abrir"')
        self.assertContains(response, 'data-page-size="12"')
        self.assertContains(response, 'id="product-pagination"')
        self.assertContains(response, 'data-mode="top">Más vendidos</button>')
        self.assertContains(response, 'id="top-sellers-count" min="1" step="1" value="12"')

    def test_historial_muestra_siete_ventas_y_conserva_filtro_al_paginar(self):
        for numero in range(8):
            Ventas.objects.create(
                id_caja=self.caja,
                total='10.00',
                fecha=timezone.now().date(),
                hora=timezone.now().time().replace(tzinfo=None),
                estado_de_pago='pagado',
                metodo_pago='efectivo',
            )

        primera_pagina = self.client.get(reverse('ventas'), {'filtro': 'mes'})
        segunda_pagina = self.client.get(
            reverse('ventas'),
            {'filtro': 'mes', 'page': 2},
        )

        self.assertEqual(primera_pagina.status_code, 200)
        self.assertEqual(len(primera_pagina.context['ventas']), 7)
        self.assertContains(primera_pagina, '?filtro=mes&page=2')
        self.assertEqual(len(segunda_pagina.context['ventas']), 1)
        self.assertEqual(segunda_pagina.context['page_obj'].number, 2)

    def test_descuenta_primero_lote_con_vencimiento_mas_cercano_y_registra_cada_lote(self):
        producto = Productos.objects.create(
            nombre='Producto de prueba',
            categoria='Alimentos',
            precio='10.00',
        )
        lote_antiguo = LotesProducto.objects.create(
            id_producto=producto,
            lote='L2',
            fecha_vencimiento='2026-12-01',
            stock_actual=5,
            precio_costo='5.00',
        )
        lote_nuevo = LotesProducto.objects.create(
            id_producto=producto,
            lote='L3',
            fecha_vencimiento='2027-01-01',
            stock_actual=20,
            precio_costo='5.00',
        )

        response = self.client.post(
            reverse('registrar_venta'),
            {
                'cart_data': json.dumps([
                    {'id': producto.id, 'cantidad': 20, 'precio': '10.00'}
                ]),
                'fiado': 'false',
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(Ventas.objects.count(), 1)
        detalles = list(DetallesVentas.objects.order_by('id_detalles_ventas'))
        self.assertEqual(
            [(detalle.lote_id, detalle.cantidad) for detalle in detalles],
            [(lote_antiguo.id, 5), (lote_nuevo.id, 15)],
        )
        lote_antiguo.refresh_from_db()
        lote_nuevo.refresh_from_db()
        self.assertEqual(lote_antiguo.stock_actual, 0)
        self.assertEqual(lote_nuevo.stock_actual, 5)

    def test_venta_ignora_stock_vencido_y_solo_descuenta_lotes_vigentes(self):
        producto = Productos.objects.create(
            nombre='Producto con vencimiento',
            categoria='Alimentos',
            precio='10.00',
        )
        hoy = timezone.localdate()
        lote_vencido = LotesProducto.objects.create(
            id_producto=producto,
            lote='VENCIDO',
            fecha_vencimiento=hoy - timedelta(days=1),
            stock_actual=20,
            precio_costo='5.00',
        )
        lote_vigente = LotesProducto.objects.create(
            id_producto=producto,
            lote='VIGENTE',
            fecha_vencimiento=hoy + timedelta(days=10),
            stock_actual=30,
            precio_costo='5.00',
        )

        self.assertEqual(producto.stock, 30)

        response = self.client.post(
            reverse('registrar_venta'),
            {
                'cart_data': json.dumps([
                    {'id': producto.id, 'cantidad': 31, 'precio': '10.00'}
                ]),
                'fiado': 'false',
            },
            follow=True,
        )
        self.assertContains(response, 'Disponible: 30')
        self.assertFalse(Ventas.objects.exists())

        response = self.client.post(
            reverse('registrar_venta'),
            {
                'cart_data': json.dumps([
                    {'id': producto.id, 'cantidad': 25, 'precio': '10.00'}
                ]),
                'fiado': 'false',
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(Ventas.objects.count(), 1)
        detalle = DetallesVentas.objects.get()
        self.assertEqual(detalle.lote_id, lote_vigente.id)
        self.assertEqual(detalle.cantidad, 25)
        lote_vencido.refresh_from_db()
        lote_vigente.refresh_from_db()
        self.assertEqual(lote_vencido.stock_actual, 20)
        self.assertEqual(lote_vigente.stock_actual, 5)

    def test_devolucion_parcial_repone_stock_y_descuenta_reintegro_de_caja(self):
        venta, detalle, lote = self.crear_venta_con_detalle()

        response = self.client.post(
            reverse('devolver_detalle_venta', args=[detalle.id_detalles_ventas]),
            {'cantidad': '1'},
        )

        self.assertEqual(response.status_code, 302)
        lote.refresh_from_db()
        self.assertEqual(lote.stock_actual, 1)
        devolucion = DevolucionVenta.objects.get(detalle=detalle)
        self.assertEqual(devolucion.cantidad, 1)
        self.assertEqual(devolucion.monto, 10)
        self.assertEqual(devolucion.metodo_reintegro, 'efectivo')
        venta.refresh_from_db()
        self.assertEqual(venta.total, Decimal('30.00'))

        response = self.client.get(reverse('gestion_caja'))
        self.assertEqual(response.context['ventas_efectivo'], 20)
        self.assertEqual(response.context['monto_esperado'], 20)

    def test_devolucion_fiada_reduce_saldo_del_cliente(self):
        cliente = Clientes.objects.create(nombre='Cliente', saldo='30.00')
        _, detalle, _ = self.crear_venta_con_detalle(estado='fiado', cliente=cliente)

        response = self.client.post(
            reverse('devolver_detalle_venta', args=[detalle.id_detalles_ventas]),
            {'cantidad': '1'},
        )

        self.assertEqual(response.status_code, 302)
        cliente.refresh_from_db()
        self.assertEqual(cliente.saldo, 20)
        self.assertEqual(DevolucionVenta.objects.get(detalle=detalle).metodo_reintegro, 'fiado')

    def test_devolucion_rechaza_ventas_con_mas_de_24_horas(self):
        _, detalle, lote = self.crear_venta_con_detalle(dias_antes=2)

        response = self.client.post(
            reverse('devolver_detalle_venta', args=[detalle.id_detalles_ventas]),
            {'cantidad': '1'},
            follow=True,
        )

        self.assertContains(response, 'Solo se aceptan devoluciones dentro de las 24 horas')
        self.assertFalse(DevolucionVenta.objects.filter(detalle=detalle).exists())
        lote.refresh_from_db()
        self.assertEqual(lote.stock_actual, 0)

    def test_devolucion_se_bloquea_si_la_caja_original_esta_cerrada(self):
        _, detalle, lote = self.crear_venta_con_detalle()
        self.caja.estado = 'cerrada'
        self.caja.save(update_fields=['estado'])
        self.caja = Caja.objects.create(usuario=self.usuario, monto_inicial=0)

        response = self.client.post(
            reverse('devolver_detalle_venta', args=[detalle.id_detalles_ventas]),
            {'cantidad': '1'},
            follow=True,
        )

        self.assertContains(response, 'la caja original de la venta está cerrada')
        self.assertFalse(DevolucionVenta.objects.filter(detalle=detalle).exists())
        lote.refresh_from_db()
        self.assertEqual(lote.stock_actual, 0)

    def test_devolucion_requiere_perfil_de_ventas(self):
        _, detalle, lote = self.crear_venta_con_detalle()
        self.usuario.is_superuser = False
        self.usuario.is_staff = False
        self.usuario.id_perfil = None
        self.usuario.save(update_fields=['is_superuser', 'is_staff', 'id_perfil'])

        response = self.client.post(
            reverse('devolver_detalle_venta', args=[detalle.id_detalles_ventas]),
            {'cantidad': '1'},
        )

        self.assertRedirects(response, reverse('inicio'), fetch_redirect_response=False)
        self.assertFalse(DevolucionVenta.objects.filter(detalle=detalle).exists())
        lote.refresh_from_db()
        self.assertEqual(lote.stock_actual, 0)

    def test_anulacion_rechaza_venta_con_devolucion_parcial(self):
        venta, detalle, lote = self.crear_venta_con_detalle()
        self.client.post(
            reverse('devolver_detalle_venta', args=[detalle.id_detalles_ventas]),
            {'cantidad': '1'},
        )

        response = self.client.post(
            reverse('anular_venta', args=[venta.id_ventas]),
            follow=True,
        )

        self.assertContains(response, 'Esta venta ya tiene devoluciones parciales')
        venta.refresh_from_db()
        self.assertEqual(venta.estado_de_pago, 'pagado')
        self.assertEqual(DevolucionVenta.objects.filter(detalle=detalle).count(), 1)
        lote.refresh_from_db()
        self.assertEqual(lote.stock_actual, 1)

    def test_devolucion_rechaza_venta_ya_anulada(self):
        venta, detalle, lote = self.crear_venta_con_detalle()
        self.client.post(reverse('anular_venta', args=[venta.id_ventas]))

        response = self.client.post(
            reverse('devolver_detalle_venta', args=[detalle.id_detalles_ventas]),
            {'cantidad': '1'},
            follow=True,
        )

        self.assertContains(response, 'No se puede devolver un producto de una venta anulada')
        self.assertFalse(DevolucionVenta.objects.filter(detalle=detalle).exists())
        lote.refresh_from_db()
        self.assertEqual(lote.stock_actual, 3)


class ClienteTelefonoUnicoTests(TestCase):
    def setUp(self):
        usuario = Usuario.objects.create_user(
            dni='11223344', apellido='Tel', nombre='Admin',
            correo='tel@example.com', password='test-password', username='teladmin',
        )
        usuario.is_superuser = True
        usuario.debe_cambiar_clave = False
        usuario.save()
        self.client.force_login(usuario)

    def test_rechaza_telefono_duplicado(self):
        Clientes.objects.create(nombre='Ana', telefono='3875541230')
        self.client.post(reverse('crear_cliente'), {'nombre': 'Beto', 'telefono': '3875541230'})
        self.assertEqual(Clientes.objects.filter(telefono='3875541230').count(), 1)
        self.assertFalse(Clientes.objects.filter(nombre='Beto').exists())

    def test_rechaza_telefono_corto_y_acepta_prefijo_mas(self):
        self.client.post(reverse('crear_cliente'), {'nombre': 'Corto', 'telefono': '12345'})
        self.assertFalse(Clientes.objects.filter(nombre='Corto').exists())
        self.client.post(reverse('crear_cliente'), {'nombre': 'Internacional', 'telefono': '+543875541230'})
        self.assertTrue(Clientes.objects.filter(nombre='Internacional').exists())

    def test_varios_clientes_sin_telefono_se_guardan_como_null(self):
        self.client.post(reverse('crear_cliente'), {'nombre': 'Uno', 'telefono': ''})
        self.client.post(reverse('crear_cliente'), {'nombre': 'Dos', 'telefono': ''})
        self.assertEqual(Clientes.objects.filter(telefono__isnull=True).count(), 2)

    def test_rechaza_nombre_duplicado_sin_importar_mayusculas_acentos_ni_espacios(self):
        Clientes.objects.create(nombre='Rodrigo', apellido='Rojas', telefono='3892154589')
        for datos in ({'nombre': 'Rodrigo', 'apellido': 'Rojas'},
                      {'nombre': '  rodrigo ', 'apellido': 'ROJAS'},
                      {'nombre': 'Rodrigo Rojas', 'apellido': ''},
                      {'nombre': 'Rodrigó', 'apellido': '"Rojas"'}):
            respuesta = self.client.post(reverse('crear_cliente'), datos, follow=True)
            self.assertContains(respuesta, 'Ya existe el cliente')
        self.assertEqual(Clientes.objects.count(), 1)

    def test_permite_mismo_nombre_con_otro_apellido(self):
        Clientes.objects.create(nombre='Rodrigo', apellido='Rojas')
        self.client.post(reverse('crear_cliente'), {'nombre': 'Rodrigo', 'apellido': 'Perez'})
        self.assertEqual(Clientes.objects.filter(nombre='Rodrigo').count(), 2)


# =============================================================================
# Ticket de venta (impresora térmica)
# =============================================================================
import re
from datetime import date, datetime

from django.test import override_settings

from .ticket import _renglones, generar_ticket_pdf


def _medidas_pdf(contenido):
    """(cantidad de páginas, ancho mm, alto mm) leyendo el PDF crudo."""
    paginas = len(re.findall(rb'/Type /Page\b', contenido))
    caja = re.search(rb'/MediaBox \[\s*[\d.]+\s+[\d.]+\s+([\d.]+)\s+([\d.]+)', contenido)
    return paginas, round(float(caja.group(1)) / 72 * 25.4, 1), round(float(caja.group(2)) / 72 * 25.4, 1)


class TicketVentaTests(TestCase):
    def setUp(self):
        self.usuario = Usuario.objects.create_user(
            dni='40100100', apellido='Ticket', nombre='Caja', correo='ticket@example.com',
            password='x', username='cajero',
        )
        self.usuario.is_superuser = True
        self.usuario.debe_cambiar_clave = False
        self.usuario.save()
        self.client.force_login(self.usuario)
        self.caja = Caja.objects.create(usuario=self.usuario, monto_inicial=Decimal('0'))

    def _venta(self, productos):
        """productos: lista de (nombre, cantidad, precio)."""
        total = sum(Decimal(precio) * cantidad for _, cantidad, precio in productos)
        venta = Ventas.objects.create(id_caja=self.caja, total=total, fecha=date.today(),
                                      hora=datetime.now().time(), metodo_pago='efectivo')
        for nombre, cantidad, precio in productos:
            producto, _ = Productos.objects.get_or_create(nombre=nombre, defaults={'categoria': 'Varios', 'precio': precio})
            DetallesVentas.objects.create(venta=venta, producto=producto, cantidad=cantidad,
                                          precio_unitario_venta=precio, subtotal=Decimal(precio) * cantidad)
        return venta

    def test_vista_devuelve_un_ticket_de_80mm_en_una_sola_hoja(self):
        venta = self._venta([('Yerba', 2, '1500'), ('Azúcar', 1, '900')])
        respuesta = self.client.get(reverse('generar_comprobante_pdf', args=[venta.pk]))
        self.assertEqual(respuesta['Content-Type'], 'application/pdf')
        paginas, ancho, alto = _medidas_pdf(respuesta.content)
        self.assertEqual((paginas, ancho), (1, 80.0))
        self.assertLess(alto, 150)   # un ticket corto, no una hoja A4 (297 mm)

    def test_muchos_productos_alargan_el_ticket_sin_cortarlo_en_hojas(self):
        venta = self._venta([(f'Producto número {i} con un nombre bastante largo para que ocupe dos líneas', 1, '100')
                             for i in range(60)])
        paginas, _, alto = _medidas_pdf(generar_ticket_pdf(venta))
        self.assertEqual(paginas, 1)
        self.assertGreater(alto, 300)

    @override_settings(TICKET_ANCHO_MM=58)
    def test_ancho_configurable_58mm(self):
        venta = self._venta([('Yerba', 1, '99999.99')])
        paginas, ancho, _ = _medidas_pdf(generar_ticket_pdf(venta))
        self.assertEqual((paginas, ancho), (1, 58.0))

    def test_nombres_con_caracteres_especiales_no_rompen_el_pdf(self):
        venta = self._venta([('Galletitas <Oreo> & "Toddy"', 1, '500')])
        self.assertTrue(generar_ticket_pdf(venta).startswith(b'%PDF'))

    def test_venta_anulada_y_fiada_se_generan(self):
        venta = self._venta([('Yerba', 1, '100')])
        for estado in ('anulado', 'fiado'):
            venta.estado_de_pago = estado
            venta.save()
            self.assertEqual(_medidas_pdf(generar_ticket_pdf(venta))[0], 1)

    def test_un_producto_de_dos_lotes_es_un_solo_renglon(self):
        venta = self._venta([('Yerba', 2, '1500')])
        DetallesVentas.objects.create(venta=venta, producto=Productos.objects.get(nombre='Yerba'), cantidad=3,
                                      precio_unitario_venta=Decimal('1500'), subtotal=Decimal('4500'))
        renglones = _renglones(venta)
        self.assertEqual(len(renglones), 1)
        self.assertEqual(renglones[0]['cantidad'], 5)
        self.assertEqual(renglones[0]['subtotal'], Decimal('7500'))


# =============================================================================
# Correcciones de seguridad e integridad en ventas
# =============================================================================
from caja.models import Caja as _Caja
from usuarios.models import Perfil
from .models import Pago


class VentasIntegridadTests(TestCase):
    def setUp(self):
        perfil, _ = Perfil.objects.get_or_create(nombre='Vendedor')
        self.usuario = Usuario.objects.create_user(dni='30000001', apellido='V', nombre='V', correo='v@example.com',
                                                   password='x', username='vend', id_perfil=perfil)
        self.usuario.debe_cambiar_clave = False
        self.usuario.save()
        self.client.force_login(self.usuario)
        self.caja = _Caja.objects.create(usuario=self.usuario, monto_inicial=Decimal('0'))
        self.producto = Productos.objects.create(nombre='Yerba', categoria='Almacen', precio=Decimal('5000'))
        LotesProducto.objects.create(id_producto=self.producto, lote='L1', stock_actual=10, precio_costo=Decimal('3000'))

    def _vender(self, precio='5000', **extra):
        datos = {'cart_data': json.dumps([{'id': self.producto.pk, 'cantidad': 1, 'precio': precio}]),
                 'metodo_pago': 'efectivo'}
        datos.update(extra)
        return self.client.post(reverse('registrar_venta'), datos)

    def test_el_precio_del_navegador_se_ignora(self):
        self._vender(precio='1')
        venta = Ventas.objects.get()
        self.assertEqual(venta.total, Decimal('5000'))
        self.assertEqual(venta.detalles.get().precio_unitario_venta, Decimal('5000'))

    def test_metodo_de_pago_invalido_se_rechaza(self):
        self._vender(metodo_pago='bitcoin')
        self.assertFalse(Ventas.objects.exists())

    def test_anular_por_get_no_hace_nada(self):
        self._vender()
        venta = Ventas.objects.get()
        respuesta = self.client.get(reverse('anular_venta', args=[venta.pk]))
        self.assertEqual(respuesta.status_code, 405)
        venta.refresh_from_db()
        self.assertEqual(venta.estado_de_pago, 'pagado')

    def test_ya_no_existe_cambiar_estado_de_venta(self):
        from django.urls import NoReverseMatch
        with self.assertRaises(NoReverseMatch):
            reverse('cambiar_estado_venta', args=[1])

    def test_no_se_registra_pago_a_cliente_sin_deuda(self):
        cliente = Clientes.objects.create(nombre='Beto')
        self.client.post(reverse('registrar_pago_cliente', args=[cliente.pk]), {'monto': '100', 'metodo_pago': 'efectivo'})
        self.assertFalse(Pago.objects.exists())

    def test_pago_con_metodo_invalido_se_rechaza(self):
        cliente = Clientes.objects.create(nombre='Beto', saldo=Decimal('500'))
        self.client.post(reverse('registrar_pago_cliente', args=[cliente.pk]), {'monto': '100', 'metodo_pago': 'cheque'})
        self.assertFalse(Pago.objects.exists())
        cliente.refresh_from_db()
        self.assertEqual(cliente.saldo, Decimal('500'))

    def test_anular_fiado_ya_pagado_no_deja_saldo_negativo(self):
        cliente = Clientes.objects.create(nombre='Ana')
        self._vender(fiado='true', id_cliente=cliente.pk)
        self.client.post(reverse('registrar_pago_cliente', args=[cliente.pk]), {'monto': '3000', 'metodo_pago': 'efectivo'})
        venta = Ventas.objects.get()
        self.client.post(reverse('anular_venta', args=[venta.pk]))
        venta.refresh_from_db()
        cliente.refresh_from_db()
        self.assertEqual(venta.estado_de_pago, 'fiado')
        self.assertEqual(cliente.saldo, Decimal('2000'))
        self.assertEqual(self.producto.stock, 9)

    def test_devolucion_fiada_que_dejaria_saldo_negativo_no_toca_el_stock(self):
        cliente = Clientes.objects.create(nombre='Ana')
        self._vender(fiado='true', id_cliente=cliente.pk)
        self.client.post(reverse('registrar_pago_cliente', args=[cliente.pk]), {'monto': '5000', 'metodo_pago': 'efectivo'})
        detalle = DetallesVentas.objects.get()
        self.client.post(reverse('devolver_detalle_venta', args=[detalle.pk]), {'cantidad': '1'})
        self.assertFalse(DevolucionVenta.objects.exists())
        self.assertEqual(self.producto.stock, 9)
        cliente.refresh_from_db()
        self.assertEqual(cliente.saldo, Decimal('0'))
