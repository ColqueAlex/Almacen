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
