import json

from django.test import TestCase
from django.urls import reverse

from productos.models import LotesProducto, Productos
from usuarios.models import Usuario
from .models import Compras, DetallesCompra


class HistorialComprasPaginacionTests(TestCase):
    def setUp(self):
        self.usuario = Usuario.objects.create_user(
            dni='12345678',
            apellido='Compras',
            nombre='Admin',
            correo='compras@example.com',
            password='test-password',
            username='comprasadmin',
        )
        self.usuario.is_superuser = True
        self.usuario.debe_cambiar_clave = False
        self.usuario.save()
        self.client.force_login(self.usuario)

    def test_historial_muestra_siete_compras_y_permite_avanzar(self):
        for numero in range(8):
            Compras.objects.create(
                id_usuario=self.usuario,
                total=numero + 1,
            )

        primera_pagina = self.client.get(reverse('listar_compras'))
        segunda_pagina = self.client.get(reverse('listar_compras'), {'page': 2})

        self.assertEqual(primera_pagina.status_code, 200)
        self.assertEqual(len(primera_pagina.context['compras']), 7)
        self.assertContains(primera_pagina, '?page=2')
        self.assertEqual(len(segunda_pagina.context['compras']), 1)
        self.assertEqual(segunda_pagina.context['page_obj'].number, 2)


class CompraConCostoMayorAlPrecioVentaTests(TestCase):
    def setUp(self):
        self.usuario = Usuario.objects.create_user(
            dni='12345678',
            apellido='Compras',
            nombre='Admin',
            correo='compras-precios@example.com',
            password='test-password',
            username='compraspreciosadmin',
        )
        self.usuario.is_superuser = True
        self.usuario.debe_cambiar_clave = False
        self.usuario.save()
        self.client.force_login(self.usuario)
        self.producto = Productos.objects.create(
            nombre='Producto de prueba',
            categoria='Alimentos',
            precio='10.00',
        )

    def datos_compra(self, **datos_extra):
        datos = {
            'cart_data': json.dumps([{
                'id_producto': self.producto.pk,
                'nombre': self.producto.nombre,
                'lote': 'LOTE-1',
                'fecha_vencimiento': '',
                'cantidad': 2,
                'precio_costo': 15.00,
                'precio_venta': 10.00,
            }]),
            'id_proveedor': '',
        }
        datos.update(datos_extra)
        return datos

    def test_solicita_confirmacion_antes_de_registrar_costo_mayor(self):
        response = self.client.post(
            reverse('registrar_compra'),
            self.datos_compra(),
            follow=True,
        )

        self.assertContains(response, 'Confirma los nuevos precios de venta')
        self.assertFalse(Compras.objects.exists())
        self.producto.refresh_from_db()
        self.assertEqual(self.producto.precio, 10)

    def test_solicita_confirmacion_cuando_costo_igual_precio_venta(self):
        response = self.client.post(
            reverse('registrar_compra'),
            self.datos_compra(cart_data=json.dumps([{
                'id_producto': self.producto.pk,
                'nombre': self.producto.nombre,
                'lote': 'LOTE-1',
                'fecha_vencimiento': '',
                'cantidad': 2,
                'precio_costo': 10.00,
                'precio_venta': 10.00,
            }])),
            follow=True,
        )

        mensajes = [str(message) for message in response.context['messages']]
        self.assertTrue(any('Confirma los nuevos precios de venta' in mensaje for mensaje in mensajes))
        self.assertFalse(Compras.objects.exists())
        self.producto.refresh_from_db()
        self.assertEqual(self.producto.precio, 10)

    def test_confirma_compra_y_actualiza_el_precio_de_venta(self):
        response = self.client.post(
            reverse('registrar_compra'),
            self.datos_compra(
                confirmar_precios='true',
                nuevos_precios=json.dumps({str(self.producto.pk): '18.50'}),
            ),
        )

        self.assertRedirects(response, reverse('listar_compras'))
        self.assertEqual(Compras.objects.count(), 1)
        detalle = DetallesCompra.objects.get()
        self.assertEqual(detalle.precio_unitario_compra, 15)
        self.producto.refresh_from_db()
        self.assertEqual(self.producto.precio, 18.50)
        lote = LotesProducto.objects.get(id_producto=self.producto)
        self.assertEqual(lote.stock_actual, 2)

    def test_pantalla_de_compra_incluye_el_cuadro_de_ajuste(self):
        response = self.client.get(reverse('registrar_compra'))

        self.assertContains(response, 'id="price-warning-modal"')
        self.assertContains(response, 'Costo de compra')
        self.assertContains(response, 'Precio de venta actual')
        self.assertContains(response, 'Nuevo precio')

    def test_bloquea_un_lote_previamente_usado_para_el_mismo_producto(self):
        lote = LotesProducto.objects.create(
            id_producto=self.producto,
            lote='LOTE-1',
            stock_actual=6,
            precio_costo='5.00',
        )

        response = self.client.get(reverse('registrar_compra'))
        self.assertEqual(
            response.context['lotes_usados'][str(self.producto.pk)][0]['stock_actual'],
            6,
        )

        response = self.client.post(
            reverse('registrar_compra'),
            self.datos_compra(),
            follow=True,
        )

        mensajes = [str(message) for message in response.context['messages']]
        self.assertTrue(any('El lote "LOTE-1"' in mensaje for mensaje in mensajes))
        self.assertTrue(any('Stock registrado: 6 unidades.' in mensaje for mensaje in mensajes))
        self.assertFalse(Compras.objects.exists())
        lote.refresh_from_db()
        self.assertEqual(lote.stock_actual, 6)

    def test_lote_igual_se_permite_para_un_producto_distinto(self):
        LotesProducto.objects.create(
            id_producto=self.producto,
            lote='LOTE-1',
            stock_actual=6,
            precio_costo='5.00',
        )
        otro_producto = Productos.objects.create(
            nombre='Otro producto',
            categoria='Alimentos',
            precio='10.00',
        )
        datos = self.datos_compra()
        items = json.loads(datos['cart_data'])
        items[0]['id_producto'] = otro_producto.pk
        items[0]['precio_costo'] = 8.00
        datos['cart_data'] = json.dumps(items)

        response = self.client.post(
            reverse('registrar_compra'),
            datos,
        )

        self.assertRedirects(response, reverse('listar_compras'))
        self.assertEqual(Compras.objects.count(), 1)
        self.assertTrue(
            LotesProducto.objects.filter(id_producto=otro_producto, lote='LOTE-1').exists()
        )


# =============================================================================
# Anulación de compras
# =============================================================================
class AnularCompraTests(TestCase):
    def setUp(self):
        self.usuario = Usuario.objects.create_user(dni='60000001', apellido='C', nombre='A', correo='anula@example.com',
                                                   password='x', username='anula')
        self.usuario.is_superuser = True
        self.usuario.debe_cambiar_clave = False
        self.usuario.save()
        self.client.force_login(self.usuario)
        self.producto = Productos.objects.create(nombre='Arroz', categoria='Almacen', precio=1000)
        self.compra = Compras.objects.create(id_usuario=self.usuario, total=5000)
        DetallesCompra.objects.create(id_compra=self.compra, id_producto=self.producto, lote='A1', cantidad=10,
                                      precio_unitario_compra=500, subtotal=5000)
        self.lote = LotesProducto.objects.create(id_producto=self.producto, lote='A1', stock_actual=10, precio_costo=500)

    def _anular(self):
        return self.client.post(reverse('anular_compra', args=[self.compra.pk]), {'motivo': 'Error de carga'})

    def test_anula_y_descuenta_el_stock(self):
        self._anular()
        self.compra.refresh_from_db()
        self.lote.refresh_from_db()
        self.assertEqual(self.compra.estado, 'Anulado')
        self.assertEqual(self.lote.stock_actual, 0)

    def test_no_anula_si_ya_se_vendieron_unidades(self):
        self.lote.stock_actual = 4
        self.lote.save()
        self._anular()
        self.compra.refresh_from_db()
        self.lote.refresh_from_db()
        self.assertNotEqual(self.compra.estado, 'Anulado')
        self.assertEqual(self.lote.stock_actual, 4)

    def test_anular_dos_veces_no_descuenta_dos_veces(self):
        self.lote.stock_actual = 25
        self.lote.save()
        self._anular()
        self._anular()
        self.lote.refresh_from_db()
        self.assertEqual(self.lote.stock_actual, 15)
