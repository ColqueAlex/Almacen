from django.test import TestCase
from django.urls import reverse

from Almacen.models import Productos


class ProductosEstadoTests(TestCase):
    def test_producto_tiene_estado_por_defecto_activo(self):
        producto = Productos.objects.create(
            nombre='Leche',
            categoria='Lácteos',
            marca='La Vaquita',
            precio=2.50,
            stock=10,
            descripcion='Leche entera',
        )

        self.assertEqual(producto.estado, 'activo')

    def test_eliminar_productos_seleccionados(self):
        activo = Productos.objects.create(
            nombre='Pan',
            categoria='Panadería',
            marca='Integral',
            precio=1.50,
            stock=20,
            descripcion='Pan integral',
            estado='activo',
        )
        inactivo = Productos.objects.create(
            nombre='Arroz',
            categoria='Cereales',
            marca='Rico',
            precio=3.00,
            stock=15,
            descripcion='Arroz blanco',
            estado='inactivo',
        )
        otro_inactivo = Productos.objects.create(
            nombre='Azúcar',
            categoria='Condimentos',
            marca='Dulce',
            precio=2.00,
            stock=10,
            descripcion='Azúcar blanca',
            estado='inactivo',
        )

        response = self.client.post(
            reverse('eliminar_seleccionados'),
            {'selected_ids': [str(inactivo.id), str(otro_inactivo.id), str(activo.id)]},
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(Productos.objects.filter(pk=inactivo.pk).exists())
        self.assertFalse(Productos.objects.filter(pk=otro_inactivo.pk).exists())
        self.assertTrue(Productos.objects.filter(pk=activo.pk).exists())
