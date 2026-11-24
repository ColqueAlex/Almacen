from datetime import timedelta

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from .models import LotesProducto, Productos, Proveedor
from usuarios.models import Usuario


class InventarioPaginacionTests(TestCase):
    def setUp(self):
        usuario = Usuario.objects.create_user(
            dni='12345678',
            apellido='Inventario',
            nombre='Admin',
            correo='inventario@example.com',
            password='test-password',
            username='inventarioadmin',
        )
        usuario.is_superuser = True
        usuario.debe_cambiar_clave = False
        usuario.save()
        self.client.force_login(usuario)

    def test_inventario_muestra_siete_productos_y_conserva_filtros_al_paginar(self):
        for numero in range(8):
            Productos.objects.create(
                nombre=f'Producto {numero}',
                categoria='Alimentos',
                precio='10.00',
            )

        response = self.client.get(
            reverse('consultar'),
            {'busqueda': 'Producto', 'categoria': 'Alimentos', 'estado_stock': 'agotado'},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.context['productos']), 7)
        self.assertContains(
            response,
            '?page=2&busqueda=Producto&categoria=Alimentos&estado_stock=agotado',
        )

    def test_proveedores_muestra_siete_registros_y_conserva_busqueda_al_paginar(self):
        for numero in range(8):
            Proveedor.objects.create(
                nombre=f'Proveedor {numero}',
                numero_telefono=f'38750000{numero:02d}',
                tipo_productos='Alimentos',
            )

        response = self.client.get(
            reverse('listar_proveedores'),
            {'busqueda': 'Proveedor'},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.context['proveedores']), 7)
        self.assertContains(response, '?page=2&busqueda=Proveedor')


class StockLotesVigentesTests(TestCase):
    def setUp(self):
        Usuario.objects.create_user(
            dni='12345678',
            apellido='Inventario',
            nombre='Admin',
            correo='stock@example.com',
            password='test-password',
            username='stockadmin',
        )

    def test_stock_total_no_incluye_lotes_vencidos(self):
        producto = Productos.objects.create(
            nombre='Producto con lotes',
            categoria='Alimentos',
            precio='10.00',
        )
        hoy = timezone.localdate()
        LotesProducto.objects.create(
            id_producto=producto,
            lote='L1',
            fecha_vencimiento=hoy - timedelta(days=1),
            stock_actual=20,
            precio_costo='5.00',
        )
        LotesProducto.objects.create(
            id_producto=producto,
            lote='L2',
            fecha_vencimiento=hoy + timedelta(days=10),
            stock_actual=30,
            precio_costo='5.00',
        )

        self.assertEqual(producto.stock, 30)


class ProveedorTelefonoUnicoTests(TestCase):
    def setUp(self):
        usuario = Usuario.objects.create_user(
            dni='55667788', apellido='Prov', nombre='Admin',
            correo='prov@example.com', password='test-password', username='provadmin',
        )
        usuario.is_superuser = True
        usuario.debe_cambiar_clave = False
        usuario.save()
        self.client.force_login(usuario)
        self.norte = Proveedor.objects.create(nombre='Norte', numero_telefono='3876894527', tipo_productos='Bebidas')
        self.sur = Proveedor.objects.create(nombre='Sur', numero_telefono='3875669230', tipo_productos='Lacteos')

    def test_crear_rechaza_telefono_duplicado(self):
        response = self.client.post(reverse('crear_proveedor'), {
            'nombre': 'Este', 'telefono': '3876894527', 'tipo_productos': 'Snacks',
        })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Ya existe un proveedor registrado con el teléfono')
        self.assertFalse(Proveedor.objects.filter(nombre='Este').exists())

    def test_editar_permite_conservar_su_telefono_pero_no_usar_el_de_otro(self):
        response = self.client.post(reverse('editar_proveedor', args=[self.norte.pk]), {
            'nombre': 'Norte SA', 'telefono': '3876894527', 'tipo_productos': 'Bebidas',
        })
        self.assertEqual(response.status_code, 302)
        response = self.client.post(reverse('editar_proveedor', args=[self.norte.pk]), {
            'nombre': 'Norte SA', 'telefono': '3875669230', 'tipo_productos': 'Bebidas',
        })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Ya existe otro proveedor registrado con el teléfono')
        self.norte.refresh_from_db()
        self.assertEqual(self.norte.numero_telefono, '3876894527')


class NombresDuplicadosTests(TestCase):
    """Un nombre con comillas, espacios o mayúsculas distintas no debe colarse como otro producto/proveedor."""

    def setUp(self):
        usuario = Usuario.objects.create_user(
            dni='12345679', apellido='Dup', nombre='Admin', correo='dup@example.com',
            password='x', username='dupadmin',
        )
        usuario.is_superuser = True
        usuario.debe_cambiar_clave = False
        usuario.save()
        self.client.force_login(usuario)

    def _crear_producto(self, nombre, marca=''):
        return self.client.post(reverse('guardar'), {
            'nombre': nombre, 'categoria': 'Golosinas', 'marca': marca, 'precio': '2500', 'estado': 'activo',
        })

    def _editar_producto(self, producto, nombre):
        return self.client.post(reverse('editar'), {
            'id': producto.pk, 'nombre': nombre, 'categoria': producto.categoria,
            'marca': producto.marca, 'precio': '2500', 'estado': 'activo',
        })

    def test_las_comillas_se_quitan_al_guardar(self):
        self._crear_producto('  "Alfajor   block" ')
        self.assertEqual(Productos.objects.get().nombre, 'Alfajor block')

    def test_no_se_puede_crear_el_mismo_producto_con_comillas_o_acentos(self):
        self._crear_producto('Alfajor block')
        for variante in ['"Alfajor block"', 'ALFAJOR  BLOCK', '“Alfajor block”', "'alfajor block'"]:
            self._crear_producto(variante)
        self.assertEqual(Productos.objects.count(), 1)

    def test_caso_reportado_editar_quitando_comillas_no_duplica(self):
        Productos.objects.create(nombre='Alfajor block', categoria='Golosinas', precio=2500)
        # Un registro viejo guardado con comillas (antes de la corrección).
        viejo = Productos.objects.create(nombre='"Alfajor block"', categoria='Golosinas', precio=2500)
        respuesta = self._editar_producto(viejo, 'Alfajor block')
        viejo.refresh_from_db()
        self.assertEqual(viejo.nombre, '"Alfajor block"')   # no se guardó
        self.assertContains(respuesta, 'Ya existe el producto')

    def test_editar_sin_cambiar_el_nombre_sigue_funcionando(self):
        producto = Productos.objects.create(nombre='Alfajor block', categoria='Golosinas', precio=1000)
        self._editar_producto(producto, 'Alfajor block')
        producto.refresh_from_db()
        self.assertEqual(producto.precio, 2500)

    def test_mismo_nombre_con_otra_marca_esta_permitido(self):
        self._crear_producto('Alfajor block', 'Arcor')
        self._crear_producto('Alfajor block', 'Georgalos')
        self.assertEqual(Productos.objects.count(), 2)

    def test_proveedor_no_se_puede_renombrar_igual_a_otro(self):
        Proveedor.objects.create(nombre='Arcnoa', numero_telefono='3811111111', tipo_productos='Golosinas')
        otro = Proveedor.objects.create(nombre='Distribuidora Sur', numero_telefono='3812222222', tipo_productos='Bebidas')
        self.client.post(reverse('editar_proveedor', args=[otro.pk]), {
            'nombre': '"arcnoa"', 'telefono': '3812222222', 'tipo_productos': 'Bebidas',
        })
        otro.refresh_from_db()
        self.assertEqual(otro.nombre, 'Distribuidora Sur')

    def test_proveedor_no_se_duplica_al_crear_con_comillas(self):
        Proveedor.objects.create(nombre='Arcnoa', numero_telefono='3811111111', tipo_productos='Golosinas')
        self.client.post(reverse('crear_proveedor'), {
            'nombre': '"Arcnoa"', 'telefono': '3813333333', 'tipo_productos': 'Golosinas',
        })
        self.assertEqual(Proveedor.objects.count(), 1)


# =============================================================================
# Productos inactivos y limpieza de catálogo
# =============================================================================
from datetime import date, datetime
from decimal import Decimal

from auditoria.models import Auditoria, Notificacion
from auditoria.notificaciones import verificar_limpieza_productos
from caja.models import Caja
from usuarios.models import Perfil
from ventas.models import DetallesVentas, Ventas


class ProductosInactivosTests(TestCase):
    def setUp(self):
        Perfil.objects.get_or_create(nombre='Administrador')
        self.vendedor_perfil, _ = Perfil.objects.get_or_create(nombre='Vendedor')
        self.admin = Usuario.objects.create_user(dni='12000001', apellido='Lim', nombre='Pieza', correo='lim@example.com',
                                                 password='x', username='limpieza')
        self.admin.is_superuser = True
        self.admin.debe_cambiar_clave = False
        self.admin.save()
        self.client.force_login(self.admin)

    def _producto(self, nombre, estado='activo', inactivo_hace_dias=None):
        producto = Productos.objects.create(nombre=nombre, categoria='Varios', precio=Decimal('100'), estado=estado)
        if inactivo_hace_dias is not None:
            Productos.objects.filter(pk=producto.pk).update(
                fecha_inactivacion=timezone.now() - timedelta(days=inactivo_hace_dias))
        return producto

    # --- fecha de inactivación -------------------------------------------------
    def test_se_registra_y_se_limpia_la_fecha_de_inactivacion(self):
        producto = self._producto('Yerba')
        self.assertIsNone(producto.fecha_inactivacion)
        self.client.post(reverse('cambiar_estado', args=[producto.pk]))
        producto.refresh_from_db()
        self.assertEqual(producto.estado, 'inactivo')
        self.assertIsNotNone(producto.fecha_inactivacion)
        self.client.post(reverse('cambiar_estado', args=[producto.pk]))
        producto.refresh_from_db()
        self.assertIsNone(producto.fecha_inactivacion)

    def test_save_con_update_fields_tambien_guarda_la_fecha(self):
        producto = self._producto('Yerba')
        producto.estado = 'inactivo'
        producto.save(update_fields=['estado'])
        producto.refresh_from_db()
        self.assertIsNotNone(producto.fecha_inactivacion)

    # --- listado ---------------------------------------------------------------
    def test_por_defecto_solo_se_listan_los_activos(self):
        self._producto('Activo')
        self._producto('Dormido', estado='inactivo')
        nombres = [p.nombre for p in self.client.get(reverse('consultar')).context['productos']]
        self.assertEqual(nombres, ['Activo'])
        nombres = [p.nombre for p in self.client.get(reverse('consultar'), {'ver': 'inactivos'}).context['productos']]
        self.assertEqual(nombres, ['Dormido'])
        self.assertEqual(len(self.client.get(reverse('consultar'), {'ver': 'todos'}).context['productos']), 2)

    def test_kpis_de_stock_no_cuentan_inactivos(self):
        self._producto('Activo')
        self._producto('Dormido', estado='inactivo')
        respuesta = self.client.get(reverse('consultar'))
        self.assertEqual(respuesta.context['total_productos'], 1)
        self.assertEqual(respuesta.context['sin_stock'], 1)
        self.assertEqual(respuesta.context['total_inactivos'], 1)

    # --- candidatos a limpieza ---------------------------------------------------
    def test_candidatos_solo_inactivos_viejos_sin_movimientos(self):
        viejo = self._producto('Viejo sin uso', 'inactivo', inactivo_hace_dias=400)
        self._producto('Reciente', 'inactivo', inactivo_hace_dias=30)
        self._producto('Activo')
        con_lote = self._producto('Viejo con lote', 'inactivo', inactivo_hace_dias=400)
        LotesProducto.objects.create(id_producto=con_lote, lote='L1', stock_actual=0, precio_costo=Decimal('50'))
        con_venta = self._producto('Viejo con venta', 'inactivo', inactivo_hace_dias=400)
        caja = Caja.objects.create(usuario=self.admin, monto_inicial=Decimal('0'))
        venta = Ventas.objects.create(id_caja=caja, total=Decimal('100'), fecha=date.today(), hora=datetime.now().time())
        DetallesVentas.objects.create(venta=venta, producto=con_venta, cantidad=1,
                                      precio_unitario_venta=Decimal('100'), subtotal=Decimal('100'))

        self.assertEqual(list(Productos.objects.candidatos_limpieza(365)), [viejo])
        respuesta = self.client.get(reverse('consultar'))
        self.assertEqual(respuesta.context['cantidad_limpieza'], 1)
        self.assertContains(respuesta, 'sugerido para eliminar')
        respuesta = self.client.get(reverse('consultar'), {'ver': 'limpieza'})
        self.assertEqual([p.nombre for p in respuesta.context['productos']], ['Viejo sin uso'])
        self.assertContains(respuesta, 'class="check-limpieza"')

    def test_notificacion_unica_que_se_actualiza(self):
        self._producto('A', 'inactivo', inactivo_hace_dias=400)
        self.assertEqual(verificar_limpieza_productos(), 1)
        self._producto('B', 'inactivo', inactivo_hace_dias=400)
        self.assertEqual(verificar_limpieza_productos(), 2)
        notificacion = Notificacion.objects.get(tipo='limpieza_productos')
        self.assertIn('2 producto(s)', notificacion.mensaje)

    def test_sin_candidatos_no_notifica(self):
        self._producto('Reciente', 'inactivo', inactivo_hace_dias=10)
        self.assertEqual(verificar_limpieza_productos(), 0)
        self.assertFalse(Notificacion.objects.filter(tipo='limpieza_productos').exists())

    # --- eliminación -------------------------------------------------------------
    def _eliminar(self, *productos):
        return self.client.post(reverse('eliminar_seleccionados'),
                                {'selected_ids': ','.join(str(p.pk) for p in productos), 'ver': 'limpieza'})

    def test_elimina_solo_inactivos_sin_movimientos_y_queda_auditado(self):
        borrable = self._producto('Borrable', 'inactivo', inactivo_hace_dias=400)
        activo = self._producto('Activo')
        con_stock = self._producto('Con stock', 'inactivo')
        LotesProducto.objects.create(id_producto=con_stock, lote='L1', stock_actual=5, precio_costo=Decimal('50'))

        respuesta = self._eliminar(borrable, activo, con_stock)
        self.assertRedirects(respuesta, reverse('consultar') + '?ver=limpieza', fetch_redirect_response=False)
        self.assertFalse(Productos.objects.filter(pk=borrable.pk).exists())
        self.assertTrue(Productos.objects.filter(pk=activo.pk).exists())
        self.assertTrue(LotesProducto.objects.filter(id_producto=con_stock).exists())   # no se perdió stock
        self.assertTrue(Auditoria.objects.filter(accion='eliminacion', objeto_repr='Borrable').exists())

    def test_eliminar_solo_por_post_y_solo_administradores(self):
        producto = self._producto('Borrable', 'inactivo', inactivo_hace_dias=400)
        self.assertEqual(self.client.get(reverse('eliminar_seleccionados')).status_code, 405)
        vendedor = Usuario.objects.create_user(dni='12000002', apellido='V', nombre='V', correo='v2@example.com',
                                               password='x', username='vende', id_perfil=self.vendedor_perfil)
        vendedor.debe_cambiar_clave = False
        vendedor.save()
        self.client.force_login(vendedor)
        self._eliminar(producto)
        self.client.post(reverse('cambiar_estado', args=[producto.pk]))
        producto.refresh_from_db()
        self.assertEqual(producto.estado, 'inactivo')   # tampoco pudo reactivarlo
        self.assertTrue(Productos.objects.filter(pk=producto.pk).exists())


# =============================================================================
# Importes en formularios y JavaScript: siempre con punto decimal.
# Con LANGUAGE_CODE='es-ar' Django escribe "1499,50"; un <input type="number">
# lo descarta y parseFloat("1499,50") devuelve 1499 (se pierden los centavos).
# =============================================================================
class DecimalesSinLocalizarTests(TestCase):
    def setUp(self):
        self.admin = Usuario.objects.create_user(dni='12000009', apellido='Dec', nombre='Imal', correo='dec@example.com',
                                                 password='x', username='decimal')
        self.admin.is_superuser = True
        self.admin.debe_cambiar_clave = False
        self.admin.save()
        self.client.force_login(self.admin)
        self.producto = Productos.objects.create(nombre='Queso', categoria='Lacteos', precio=Decimal('1499.50'))

    def test_editar_producto_precarga_el_precio(self):
        respuesta = self.client.get(reverse('detalle', args=[self.producto.pk]))
        self.assertContains(respuesta, 'value="1499.50"')
        self.assertNotContains(respuesta, 'value="1499,50"')

    def test_registrar_venta_envia_el_precio_con_centavos(self):
        Caja.objects.create(usuario=self.admin, monto_inicial=Decimal('0'))
        LotesProducto.objects.create(id_producto=self.producto, lote='L1', stock_actual=3, precio_costo=Decimal('900'))
        respuesta = self.client.get(reverse('registrar_venta'))
        self.assertContains(respuesta, 'data-precio="1499.50"')

    def test_cerrar_caja_precarga_el_monto_esperado(self):
        Caja.objects.create(usuario=self.admin, monto_inicial=Decimal('1234.56'))
        respuesta = self.client.get(reverse('gestion_caja'))
        self.assertContains(respuesta, 'value="1234.56"')
        self.assertContains(respuesta, 'parseFloat("1234.56")')
