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


class CierreCajaTransferenciasTests(TestCase):
    """El monto esperado incluye ventas y cobros por transferencia."""

    def setUp(self):
        from decimal import Decimal
        from ventas.models import Clientes, Pago, Ventas

        self.usuario = Usuario.objects.create_user(
            dni='87654321', apellido='Caja', nombre='Cierre',
            correo='cierre@example.com', password='test-password', username='cierrecaja',
        )
        self.usuario.is_superuser = True
        self.usuario.debe_cambiar_clave = False
        self.usuario.save()
        self.client.force_login(self.usuario)

        self.caja = Caja.objects.create(usuario=self.usuario, monto_inicial=Decimal('100.00'), estado='abierta')
        ahora = timezone.localtime()
        for metodo, total in (('efectivo', '50.00'), ('transferencia', '30.00')):
            Ventas.objects.create(
                id_caja=self.caja, total=total, fecha=ahora.date(),
                hora=ahora.time().replace(tzinfo=None), estado_de_pago='pagado', metodo_pago=metodo,
            )
        # Las fiadas no suman al esperado.
        Ventas.objects.create(
            id_caja=self.caja, total='999.00', fecha=ahora.date(),
            hora=ahora.time().replace(tzinfo=None), estado_de_pago='fiado',
        )
        cliente = Clientes.objects.create(nombre='Cliente')
        Pago.objects.create(cliente=cliente, id_caja=self.caja, monto='20.00', usuario=self.usuario, metodo_pago='efectivo')
        Pago.objects.create(cliente=cliente, id_caja=self.caja, monto='15.00', usuario=self.usuario, metodo_pago='transferencia')
        # 100 + 50 + 20 + 30 + 15
        self.esperado = Decimal('215.00')

    def test_gestion_caja_muestra_esperado_con_transferencias(self):
        response = self.client.get(reverse('gestion_caja'))
        self.assertEqual(response.context['monto_esperado'], self.esperado)
        self.assertEqual(response.context['total_transferencias'], 45)

    def test_cerrar_caja_calcula_diferencia_sobre_total_con_transferencias(self):
        self.client.post(reverse('cerrar_caja'), {'monto_real': '210.00'})
        self.caja.refresh_from_db()
        self.assertEqual(self.caja.estado, 'cerrada')
        self.assertEqual(self.caja.monto_final_esperado, self.esperado)
        self.assertEqual(self.caja.diferencia, -5)
