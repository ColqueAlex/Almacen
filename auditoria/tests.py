from django.test import TestCase
from django.urls import reverse

from auditoria.models import Auditoria
from usuarios.models import Usuario


class AuditoriaFilterTests(TestCase):
    def setUp(self):
        self.usuario = Usuario.objects.create_user(
            dni='12345678',
            apellido='Admin',
            nombre='Auditoria',
            correo='auditoria@example.com',
            password='test-password',
            username='auditoriaadmin',
        )
        self.usuario.is_superuser = True
        self.usuario.debe_cambiar_clave = False
        self.usuario.save()
        self.client.force_login(self.usuario)

    def test_filtra_por_busqueda_accion_y_modulo(self):
        coincidencia = Auditoria.objects.create(
            usuario=self.usuario,
            accion='creacion',
            modelo='Producto',
            objeto_id=1,
            descripcion='Alta de producto de prueba',
        )
        Auditoria.objects.create(
            usuario=self.usuario,
            accion='modificacion',
            modelo='Producto',
            objeto_id=2,
            descripcion='Cambio de producto',
        )
        Auditoria.objects.create(
            usuario=self.usuario,
            accion='creacion',
            modelo='Caja',
            objeto_id=3,
            descripcion='Alta de caja',
        )

        response = self.client.get(
            reverse('listar_auditoria'),
            {'busqueda': 'auditoriaadmin', 'accion': 'creacion', 'modulo': 'Producto'},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            [registro.id for registro in response.context['registros']],
            [coincidencia.id],
        )
        self.assertContains(response, 'value="auditoriaadmin"')
        self.assertContains(response, 'value="creacion" selected')
        self.assertContains(response, 'value="Producto" selected')
