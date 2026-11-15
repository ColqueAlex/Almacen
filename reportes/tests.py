from datetime import date, time, timedelta
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse

from caja.models import Caja
from productos.models import LotesProducto, Productos
from reportes import servicios
from usuarios.models import Perfil, Usuario
from ventas.models import Clientes, DetallesVentas, DevolucionVenta, Ventas

D = Decimal


class ReportesTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.perfil_admin, _ = Perfil.objects.get_or_create(nombre='Administrador')
        cls.perfil_vendedor, _ = Perfil.objects.get_or_create(nombre='Vendedor')
        cls.admin = cls._usuario('11111111', 'adminrep', 'a@example.com', cls.perfil_admin)
        cls.vendedor = cls._usuario('22222222', 'venderep', 'v@example.com', cls.perfil_vendedor)
        cls.caja = Caja.objects.create(usuario=cls.admin)

        cls.yerba = Productos.objects.create(nombre='Yerba', categoria='Almacen', precio=D('100'))
        cls.gaseosa = Productos.objects.create(nombre='Gaseosa', categoria='Bebidas', precio=D('50'))
        # Dos lotes de yerba con distinto costo
        cls.yerba_l1 = LotesProducto.objects.create(id_producto=cls.yerba, lote='Y1', stock_actual=10, precio_costo=D('60'))
        cls.yerba_l2 = LotesProducto.objects.create(id_producto=cls.yerba, lote='Y2', stock_actual=10, precio_costo=D('70'))
        cls.gaseosa_l1 = LotesProducto.objects.create(id_producto=cls.gaseosa, lote='G1', stock_actual=4, precio_costo=D('30'))
        # Lote vencido: no debe contar en el stock valorizado
        LotesProducto.objects.create(id_producto=cls.gaseosa, lote='G-VENC', stock_actual=100, precio_costo=D('30'),
                                     fecha_vencimiento=date.today() - timedelta(days=1))

        cls.ana = Clientes.objects.create(nombre='Ana', saldo=D('500'))
        cls.beto = Clientes.objects.create(nombre='Beto', saldo=D('0'))

        hoy = date.today()
        # Venta 1 (hoy, fiada a Ana): 3 yerbas del lote Y1 + 2 del Y2 a $100
        v1 = cls._venta(hoy, D('500'), cls.ana, 'fiado')
        DetallesVentas.objects.create(venta=v1, producto=cls.yerba, lote=cls.yerba_l1, cantidad=3, precio_unitario_venta=D('100'), subtotal=D('300'))
        d_y2 = DetallesVentas.objects.create(venta=v1, producto=cls.yerba, lote=cls.yerba_l2, cantidad=2, precio_unitario_venta=D('100'), subtotal=D('200'))
        # Devolución de 1 yerba del lote Y2
        DevolucionVenta.objects.create(detalle=d_y2, caja=cls.caja, usuario=cls.admin, cantidad=1, monto=D('100'), metodo_reintegro='fiado')

        # Venta 2 (hace 10 días, pagada por Beto): 2 gaseosas
        v2 = cls._venta(hoy - timedelta(days=10), D('100'), cls.beto, 'pagado')
        DetallesVentas.objects.create(venta=v2, producto=cls.gaseosa, lote=cls.gaseosa_l1, cantidad=2, precio_unitario_venta=D('50'), subtotal=D('100'))

        # Venta anulada: no debe contar en ningún reporte
        v3 = cls._venta(hoy, D('1000'), cls.ana, 'anulado')
        DetallesVentas.objects.create(venta=v3, producto=cls.yerba, lote=cls.yerba_l1, cantidad=10, precio_unitario_venta=D('100'), subtotal=D('1000'))

    @classmethod
    def _usuario(cls, dni, username, correo, perfil):
        u = Usuario.objects.create_user(dni=dni, apellido='Test', nombre='Test', correo=correo,
                                        password='x', username=username, id_perfil=perfil)
        u.debe_cambiar_clave = False
        u.save()
        return u

    @classmethod
    def _venta(cls, fecha, total, cliente, estado):
        return Ventas.objects.create(id_caja=cls.caja, total=total, fecha=fecha, hora=time(10, 0),
                                     id_clientes=cliente, estado_de_pago=estado)

    # ---- Servicios -------------------------------------------------------
    def test_rentabilidad_usa_costo_del_lote_y_descuenta_devoluciones(self):
        filas, totales = servicios.rentabilidad_por_producto()
        yerba = next(f for f in filas if f['producto'] == 'Yerba')
        # Vendidas 5 - devuelta 1 = 4; ingreso 500 - 100 = 400; costo 3*60 + 2*70 - 1*70 = 250
        self.assertEqual(yerba['cantidad'], 4)
        self.assertEqual(yerba['ingreso'], D('400'))
        self.assertEqual(yerba['costo'], D('250'))
        self.assertEqual(yerba['ganancia'], D('150'))
        # Totales: + gaseosa (2 u, ingreso 100, costo 60)
        self.assertEqual(totales['ingreso'], D('500'))
        self.assertEqual(totales['costo'], D('310'))
        self.assertEqual(totales['ganancia'], D('190'))

    def test_rentabilidad_filtra_por_fecha_y_categoria(self):
        filas, _ = servicios.rentabilidad_por_producto(desde=date.today() - timedelta(days=2))
        self.assertEqual([f['producto'] for f in filas], ['Yerba'])
        filas, _ = servicios.rentabilidad_por_producto(categoria='Bebidas')
        self.assertEqual([f['producto'] for f in filas], ['Gaseosa'])

    def test_ranking_clientes(self):
        filas, totales = servicios.ranking_clientes()
        self.assertEqual([f['cliente'] for f in filas], ['Ana', 'Beto'])
        self.assertEqual(filas[0]['facturado'], D('400'))  # 500 - devolución 100, sin la anulada
        self.assertEqual(filas[0]['cantidad'], 1)
        self.assertEqual(filas[0]['saldo'], D('500'))
        self.assertEqual(totales['facturado'], D('500'))

        filas, _ = servicios.ranking_clientes(estado_pago='pagado')
        self.assertEqual([f['cliente'] for f in filas], ['Beto'])

    def test_stock_valorizado_ignora_lotes_vencidos(self):
        filas, totales, limite = servicios.stock_valorizado()
        self.assertEqual(limite, 10)
        yerba = next(f for f in filas if f['producto'] == 'Yerba')
        gaseosa = next(f for f in filas if f['producto'] == 'Gaseosa')
        self.assertEqual(yerba['stock'], 20)
        self.assertEqual(yerba['valor'], D('1300'))           # 10*60 + 10*70
        self.assertEqual(yerba['costo_promedio'], D('65'))
        self.assertEqual(gaseosa['stock'], 4)                  # el lote vencido no cuenta
        self.assertTrue(gaseosa['stock_bajo'])
        self.assertEqual(totales['valor'], D('1420'))

        filas, _, _ = servicios.stock_valorizado(solo_stock_bajo=True)
        self.assertEqual([f['producto'] for f in filas], ['Gaseosa'])

    # ---- Vistas ----------------------------------------------------------
    def test_vendedor_no_accede_a_reportes_de_admin(self):
        self.client.force_login(self.vendedor)
        for nombre in ('reporte_rentabilidad', 'reporte_stock_valorizado'):
            self.assertRedirects(self.client.get(reverse(nombre)), reverse('inicio'), fetch_redirect_response=False)
        self.assertEqual(self.client.get(reverse('reporte_ranking_clientes')).status_code, 200)

    def test_vistas_html_y_pdf(self):
        self.client.force_login(self.admin)
        for nombre in ('reporte_rentabilidad', 'reporte_ranking_clientes', 'reporte_stock_valorizado'):
            respuesta = self.client.get(reverse(nombre))
            self.assertEqual(respuesta.status_code, 200, nombre)
            self.assertContains(respuesta, 'TOTALES')
            pdf = self.client.get(reverse(nombre), {'exportar': 'pdf'})
            self.assertEqual(pdf['Content-Type'], 'application/pdf')
            self.assertTrue(pdf.content.startswith(b'%PDF'), nombre)

    def test_filtros_invalidos_no_rompen(self):
        self.client.force_login(self.admin)
        respuesta = self.client.get(reverse('reporte_rentabilidad'), {'desde': 'no-es-fecha', 'categoria': 'Inexistente'})
        self.assertEqual(respuesta.status_code, 200)
        self.assertEqual(respuesta.context['categoria_seleccionada'], '')
        respuesta = self.client.get(reverse('reporte_ranking_clientes'), {'estado': 'anulado'})
        self.assertEqual(respuesta.context['estado_seleccionado'], '')

    def test_querystring_conserva_filtros_en_paginacion(self):
        self.client.force_login(self.admin)
        respuesta = self.client.get(reverse('reporte_stock_valorizado'), {'categoria': 'Almacen', 'stock_bajo': '1', 'page': '1'})
        self.assertEqual(respuesta.context['querystring'], 'categoria=Almacen&stock_bajo=1')
