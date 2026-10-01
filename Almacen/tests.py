import json

from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse

from Almacen.models import Productos, Usuario, Ventas, DetallesVentas


class ProductosEstadoTests(TestCase):
    def test_producto_tiene_estado_por_defecto_activo(self):
        producto = Productos.objects.create(
            nombre='Leche',
            categoria='Lácteos',
            marca='La Vaquita',
            precio=2.50,
            stock=10,
        )

        self.assertEqual(producto.estado, 'activo')

    def test_categoria_no_acepta_numeros(self):
        producto = Productos(
            nombre='Queso',
            categoria='Lacteos123',
            marca='Fresco',
            precio=4.50,
            stock=8,
        )

        with self.assertRaises(ValidationError):
            producto.full_clean()

    def test_cambiar_estado_no_descuenta_stock(self):
        producto = Productos(
            nombre='Frutilla',
            categoria='Frutas',
            marca='Sin marca',
            precio=3.50,
            stock=12,
            estado='activo',
        )

        stock_inicial = producto.stock
        producto.estado = 'inactivo' if producto.estado == 'activo' else 'activo'

        self.assertEqual(producto.stock, stock_inicial)
        self.assertEqual(producto.estado, 'inactivo')

    def test_eliminar_productos_seleccionados(self):
        usuario = Usuario.objects.create_user(
            dni=99999999,
            apellido='Admin',
            nombre='Super',
            correo='admin@example.com',
            password='123456',
        )
        usuario.debe_cambiar_clave = False
        usuario.save()
        self.client.force_login(usuario)

        activo = Productos.objects.create(
            nombre='Pan',
            categoria='Panadería',
            marca='Integral',
            precio=1.50,
            stock=20,
            estado='activo',
        )
        inactivo = Productos.objects.create(
            nombre='Arroz',
            categoria='Cereales',
            marca='Rico',
            precio=3.00,
            stock=15,
            estado='inactivo',
        )
        otro_inactivo = Productos.objects.create(
            nombre='Azúcar',
            categoria='Condimentos',
            marca='Dulce',
            precio=2.00,
            stock=10,
            estado='inactivo',
        )

        response = self.client.post(
            reverse('eliminar_seleccionados'),
            {'selected_ids': f"{inactivo.id},{otro_inactivo.id},{activo.id}"},
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(Productos.objects.filter(pk=inactivo.pk).exists())
        self.assertFalse(Productos.objects.filter(pk=otro_inactivo.pk).exists())
        self.assertTrue(Productos.objects.filter(pk=activo.pk).exists())


class VentasViewTests(TestCase):
    def setUp(self):
        self.usuario = Usuario.objects.create_user(
            dni=12345678,
            apellido='Pérez',
            nombre='Ana',
            correo='ana@example.com',
            password='123456',
        )
        self.usuario.debe_cambiar_clave = False
        self.usuario.save()
        self.client.force_login(self.usuario)

    def test_pagina_de_ventas_muestra_venta_y_detalle(self):
        producto = Productos.objects.create(
            nombre='Manzana',
            categoria='Frutas',
            marca='Natural',
            precio=12.99,
            stock=25,
        )
        venta = Ventas.objects.create(
            id_ventas=1,
            id_caja=1,
            total=129.90,
            fecha='2026-11-19',
            hora='18:10:00',
            id_clientes=42,
            estado_de_pago='pendiente',
        )
        DetallesVentas.objects.create(
            id_detalles_ventas=1,
            venta=venta,
            producto=producto,
            cantidad=10,
            precio_unitario_compra=12.99,
            subtotal=129.90,
        )

        response = self.client.get(reverse('ventas'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '129.90')
        self.assertContains(response, 'Pendiente')

    def test_get_registrar_venta_renderiza_catalogo(self):
        Productos.objects.create(
            nombre='Banana',
            categoria='Frutas',
            marca='Natural',
            precio=5.0,
            stock=20,
        )
        response = self.client.get(reverse('registrar_venta'))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'Registro_ventas.html')
        self.assertContains(response, 'Banana')
        self.assertContains(response, 'Frutas')

    def test_finalizar_guarda_venta_y_detalle_en_base_de_datos(self):
        producto = Productos.objects.create(
            nombre='Yogur',
            categoria='Lácteos',
            marca='Natural',
            precio=15.0,
            stock=10,
        )

        response = self.client.post(
            reverse('registrar_venta'),
            {
                'cart_data': json.dumps([
                    {'id': producto.id, 'cantidad': 2}
                ]),
                'fiado': 'false',
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertRedirects(response, reverse('ventas'))
        self.assertTrue(Ventas.objects.exists())
        venta = Ventas.objects.latest('id_ventas')
        self.assertEqual(float(venta.total), 30.0)
        self.assertEqual(venta.estado_de_pago, 'pagado')
        self.assertEqual(venta.detalles.count(), 1)

        detalle = venta.detalles.first()
        self.assertEqual(detalle.producto, producto)
        self.assertEqual(detalle.cantidad, 2)
        self.assertEqual(float(detalle.precio_unitario_compra), 15.0)
        self.assertEqual(float(detalle.subtotal), 30.0)

        producto.refresh_from_db()
        self.assertEqual(producto.stock, 8)

    def test_registrar_venta_al_fiado(self):
        producto = Productos.objects.create(
            nombre='Galletas',
            categoria='Snacks',
            marca='Oreo',
            precio=10.50,
            stock=15,
        )

        response = self.client.post(
            reverse('registrar_venta'),
            {
                'cart_data': json.dumps([
                    {'id': producto.id, 'cantidad': 3}
                ]),
                'fiado': 'true',
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        venta = Ventas.objects.latest('id_ventas')
        self.assertEqual(float(venta.total), 31.50)
        self.assertEqual(venta.estado_de_pago, 'fiado')

        producto.refresh_from_db()
        self.assertEqual(producto.stock, 12)

    def test_registrar_venta_multiples_productos(self):
        p1 = Productos.objects.create(nombre='Jugo', categoria='Bebidas', precio=8.0, stock=10)
        p2 = Productos.objects.create(nombre='Agua', categoria='Bebidas', precio=4.0, stock=20)

        response = self.client.post(
            reverse('registrar_venta'),
            {
                'cart_data': json.dumps([
                    {'id': p1.id, 'cantidad': 2},
                    {'id': p2.id, 'cantidad': 5},
                ]),
                'fiado': 'false',
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        venta = Ventas.objects.latest('id_ventas')
        # 2*8.0 + 5*4.0 = 16.0 + 20.0 = 36.0
        self.assertEqual(float(venta.total), 36.0)
        self.assertEqual(venta.detalles.count(), 2)

        p1.refresh_from_db()
        p2.refresh_from_db()
        self.assertEqual(p1.stock, 8)
        self.assertEqual(p2.stock, 15)

    def test_registrar_venta_incremento_secuencial_ids(self):
        p = Productos.objects.create(nombre='Pan Integral', categoria='Panadería', precio=2.0, stock=50)

        # Primera venta
        self.client.post(
            reverse('registrar_venta'),
            {'cart_data': json.dumps([{'id': p.id, 'cantidad': 1}]), 'fiado': 'false'},
        )
        venta1 = Ventas.objects.get(id_ventas=1)
        detalle1 = DetallesVentas.objects.get(id_detalles_ventas=1)
        self.assertEqual(detalle1.venta, venta1)

        # Segunda venta
        self.client.post(
            reverse('registrar_venta'),
            {'cart_data': json.dumps([{'id': p.id, 'cantidad': 2}]), 'fiado': 'false'},
        )
        venta2 = Ventas.objects.get(id_ventas=2)
        detalle2 = DetallesVentas.objects.get(id_detalles_ventas=2)
        self.assertEqual(detalle2.venta, venta2)

    def test_registrar_venta_falla_por_stock_insuficiente(self):
        producto = Productos.objects.create(
            nombre='Café',
            categoria='Almacén',
            marca='Dolca',
            precio=25.0,
            stock=3,
        )

        response = self.client.post(
            reverse('registrar_venta'),
            {
                'cart_data': json.dumps([
                    {'id': producto.id, 'cantidad': 10}
                ]),
                'fiado': 'false',
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        # No se debe registrar ninguna venta ni detalle
        self.assertFalse(Ventas.objects.exists())
        self.assertFalse(DetallesVentas.objects.exists())
        # Stock debe mantenerse intacto
        producto.refresh_from_db()
        self.assertEqual(producto.stock, 3)
        self.assertContains(response, f'No hay stock suficiente para {producto.nombre}.')

    def test_registrar_venta_falla_por_carrito_vacio(self):
        response = self.client.post(
            reverse('registrar_venta'),
            {'cart_data': '[]', 'fiado': 'false'},
            follow=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Ventas.objects.exists())
        self.assertContains(response, 'Agrega al menos un producto al carrito.')

    def test_registrar_venta_falla_por_json_invalido(self):
        response = self.client.post(
            reverse('registrar_venta'),
            {'cart_data': '{invalido}', 'fiado': 'false'},
            follow=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Ventas.objects.exists())
        self.assertContains(response, 'No se pudo procesar la venta.')

    def test_registrar_venta_falla_por_cantidades_invalidas(self):
        producto = Productos.objects.create(
            nombre='Té',
            categoria='Almacén',
            precio=5.0,
            stock=20,
        )

        response = self.client.post(
            reverse('registrar_venta'),
            {
                'cart_data': json.dumps([
                    {'id': producto.id, 'cantidad': 0},
                    {'id': producto.id, 'cantidad': -5},
                ]),
                'fiado': 'false',
            },
            follow=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Ventas.objects.exists())
        self.assertContains(response, 'La venta no tiene productos válidos.')
        producto.refresh_from_db()
        self.assertEqual(producto.stock, 20)

    def test_registrar_venta_producto_no_encontrado_retorna_404(self):
        response = self.client.post(
            reverse('registrar_venta'),
            {
                'cart_data': json.dumps([
                    {'id': 999999, 'cantidad': 1}
                ]),
                'fiado': 'false',
            },
        )
        self.assertEqual(response.status_code, 404)
        self.assertFalse(Ventas.objects.exists())

    def test_registrar_venta_requiere_autenticacion(self):
        self.client.logout()
        response = self.client.post(
            reverse('registrar_venta'),
            {'cart_data': '[]', 'fiado': 'false'},
        )
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse('login'), response.url)

    def test_registrar_venta_redirige_si_debe_cambiar_clave(self):
        self.usuario.debe_cambiar_clave = True
        self.usuario.save()

        response = self.client.post(
            reverse('registrar_venta'),
            {'cart_data': '[]', 'fiado': 'false'},
        )
        self.assertEqual(response.status_code, 302)
        self.assertRedirects(response, reverse('cambiar_clave'))

    def test_registrar_venta_consolida_productos_duplicados_en_carrito(self):
        producto = Productos.objects.create(
            nombre='Mantequilla',
            categoria='Lácteos',
            precio=12.0,
            stock=10,
        )

        response = self.client.post(
            reverse('registrar_venta'),
            {
                'cart_data': json.dumps([
                    {'id': producto.id, 'cantidad': 2},
                    {'id': producto.id, 'cantidad': 3},
                ]),
                'fiado': 'false',
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        venta = Ventas.objects.latest('id_ventas')
        # Total: (2+3) * 12 = 60.0
        self.assertEqual(float(venta.total), 60.0)
        self.assertEqual(venta.detalles.count(), 1)
        detalle = venta.detalles.first()
        self.assertEqual(detalle.cantidad, 5)
        self.assertEqual(float(detalle.subtotal), 60.0)

        producto.refresh_from_db()
        self.assertEqual(producto.stock, 5)

    def test_registrar_venta_rechaza_producto_inactivo(self):
        producto = Productos.objects.create(
            nombre='Cereal Viejo',
            categoria='Cereales',
            precio=8.0,
            stock=10,
            estado='inactivo',
        )

        response = self.client.post(
            reverse('registrar_venta'),
            {
                'cart_data': json.dumps([
                    {'id': producto.id, 'cantidad': 2}
                ]),
                'fiado': 'false',
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(Ventas.objects.exists())
        self.assertContains(response, f'El producto {producto.nombre} no está activo para la venta.')
        producto.refresh_from_db()
        self.assertEqual(producto.stock, 10)

    def test_catalogo_no_muestra_productos_inactivos(self):
        Productos.objects.create(
            nombre='Vino Activo',
            categoria='Bebidas',
            precio=30.0,
            stock=5,
            estado='activo',
        )
        Productos.objects.create(
            nombre='Cerveza Inactiva',
            categoria='Bebidas',
            precio=15.0,
            stock=5,
            estado='inactivo',
        )

        response = self.client.get(reverse('registrar_venta'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Vino Activo')
        self.assertNotContains(response, 'Cerveza Inactiva')

    def test_registrar_venta_multiples_lotes_descuenta_fifo(self):
        lote1 = Productos.objects.create(
            nombre='Gaseosa Cola',
            lote='LOTE-001',
            categoria='Bebidas',
            precio=10.0,
            stock=4,
            fecha_vencimiento='2026-10-01',
            estado='activo',
        )
        lote2 = Productos.objects.create(
            nombre='Gaseosa Cola',
            lote='LOTE-002',
            categoria='Bebidas',
            precio=10.0,
            stock=6,
            fecha_vencimiento='2026-11-01',
            estado='activo',
        )

        response = self.client.post(
            reverse('registrar_venta'),
            {
                'cart_data': json.dumps([
                    {'id': lote1.id, 'cantidad': 7}
                ]),
                'fiado': 'false',
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        venta = Ventas.objects.latest('id_ventas')
        self.assertEqual(float(venta.total), 70.0)
        self.assertEqual(venta.detalles.count(), 2)

        lote1.refresh_from_db()
        lote2.refresh_from_db()
        self.assertEqual(lote1.stock, 0)
        self.assertEqual(lote2.stock, 3)




class UsuariosRegresionTests(TestCase):
    def test_cambiar_clave_rechaza_entrada_vacia(self):
        usuario = Usuario.objects.create_user(
            dni=22222222,
            apellido='P?rez',
            nombre='Ana',
            correo='ana.regresion@example.com',
            password='123456',
        )
        usuario.debe_cambiar_clave = True
        usuario.save()
        self.client.force_login(usuario)

        response = self.client.post(
            reverse('cambiar_clave'),
            {'nueva_clave': '', 'confirmar_clave': ''},
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'La contrase?a no puede estar vac?a')
        usuario.refresh_from_db()
        self.assertTrue(usuario.check_password('123456'))

    def test_crear_usuario_con_nombres_con_espacios_no_falla(self):
        usuario = Usuario.objects.create_user(
            dni=33333333,
            apellido='  P?rez  ',
            nombre='  Ana  ',
            correo='ana2@example.com',
            password='123456',
        )

        self.assertEqual(usuario.apellido, 'P?rez')
        self.assertEqual(usuario.nombre, 'Ana')
        self.assertTrue(usuario.username)
        self.assertTrue(usuario.check_password('123456'))
