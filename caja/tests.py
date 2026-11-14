from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from caja.models import Caja
from usuarios.models import Usuario


class HistorialCierresPaginacionTests(TestCase):
    def setUp(self):
        self.usuario = Usuario.objects.create_user(
            dni='12345678',
            apellido='Caja',
            nombre='Admin',
            correo='caja@example.com',
            password='test-password',
            username='cajaadmin',
        )
        self.usuario.is_superuser = True
        self.usuario.debe_cambiar_clave = False
        self.usuario.save()
        self.client.force_login(self.usuario)

    def test_historial_muestra_siete_cajas_y_permite_avanzar(self):
        for numero in range(8):
            Caja.objects.create(
                usuario=self.usuario,
                monto_inicial=numero,
                estado='cerrada',
                fecha_cierre=timezone.now(),
            )

        primera_pagina = self.client.get(reverse('gestion_caja'))
        segunda_pagina = self.client.get(reverse('gestion_caja'), {'page': 2})

        self.assertEqual(primera_pagina.status_code, 200)
        self.assertEqual(len(primera_pagina.context['historial_cajas']), 7)
        self.assertContains(primera_pagina, '?page=2')
        self.assertEqual(len(segunda_pagina.context['historial_cajas']), 1)
        self.assertEqual(segunda_pagina.context['page_obj'].number, 2)
