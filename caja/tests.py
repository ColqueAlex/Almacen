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


# =============================================================================
# Importes fuera de rango (antes provocaban DataError 1264 en MySQL)
# =============================================================================
import json
from decimal import Decimal

from django.contrib.messages import get_messages

from productos.models import Productos
from usuarios.utils import leer_cantidad, leer_monto
from ventas.models import Clientes, Pago


class LeerMontoTests(TestCase):
    def test_valores_validos(self):
        self.assertEqual(leer_monto('1500.5'), (Decimal('1500.50'), None))
        self.assertEqual(leer_monto('1500,50'), (Decimal('1500.50'), None))
        self.assertEqual(leer_monto('0.005')[0], Decimal('0.01'))
        self.assertEqual(leer_monto('99999999.99')[0], Decimal('99999999.99'))

    def test_valores_invalidos(self):
        for valor in ['', None, 'abc', 'NaN', 'Infinity', '-1', '100000000', '1e30', '99999999.999']:
            monto, error = leer_monto(valor)
            self.assertIsNone(monto, valor)
            self.assertTrue(error, valor)
        self.assertIsNotNone(leer_monto('0', permitir_cero=False)[1])

    def test_cantidades(self):
        self.assertEqual(leer_cantidad('3'), (3, None))
        for valor in ['0', '-2', '1.5', 'x', '99999999999']:
            self.assertIsNone(leer_cantidad(valor)[0], valor)


class MontosFueraDeRangoTests(TestCase):
    def setUp(self):
        self.usuario = Usuario.objects.create_user(
            dni='12345670', apellido='Montos', nombre='Admin', correo='montos@example.com',
            password='x', username='montosadmin',
        )
        self.usuario.is_superuser = True
        self.usuario.debe_cambiar_clave = False
        self.usuario.save()
        self.client.force_login(self.usuario)

    def _mensajes(self, respuesta):
        return [str(m) for m in get_messages(respuesta.wsgi_request)]

    def test_caso_reportado_cerrar_caja_con_monto_enorme(self):
        caja = Caja.objects.create(usuario=self.usuario, monto_inicial=Decimal('1000'))
        respuesta = self.client.post(reverse('cerrar_caja'), {'monto_real': '999999999999999999'})
        self.assertRedirects(respuesta, reverse('gestion_caja'), fetch_redirect_response=False)
        self.assertIn('no puede superar $99.999.999,99', ' '.join(self._mensajes(respuesta)))
        caja.refresh_from_db()
        self.assertEqual(caja.estado, 'abierta')

    def test_cerrar_caja_con_monto_valido_sigue_funcionando(self):
        caja = Caja.objects.create(usuario=self.usuario, monto_inicial=Decimal('1000'))
        self.client.post(reverse('cerrar_caja'), {'monto_real': '1000,00'})
        caja.refresh_from_db()
        self.assertEqual(caja.estado, 'cerrada')
        self.assertEqual(caja.diferencia, Decimal('0.00'))

    def test_abrir_caja_con_monto_enorme(self):
        self.client.post(reverse('abrir_caja'), {'monto_inicial': '1e20'})
        self.assertFalse(Caja.objects.exists())

    def test_pago_con_nan_no_rompe(self):
        Caja.objects.create(usuario=self.usuario, monto_inicial=Decimal('0'))
        cliente = Clientes.objects.create(nombre='Juan', saldo=Decimal('500'))
        respuesta = self.client.post(reverse('registrar_pago_cliente', args=[cliente.pk]), {'monto': 'NaN'})
        self.assertEqual(respuesta.status_code, 302)
        self.assertFalse(Pago.objects.exists())

    def test_producto_con_precio_enorme(self):
        self.client.post(reverse('guardar'), {
            'nombre': 'Caro', 'categoria': 'Varios', 'precio': '123456789012', 'estado': 'activo',
        })
        self.assertFalse(Productos.objects.exists())

    def test_compra_con_costo_enorme_muestra_mensaje_claro(self):
        producto = Productos.objects.create(nombre='Yerba', categoria='Almacen', precio=Decimal('100'))
        carrito = [{'id_producto': producto.pk, 'cantidad': 1, 'precio_costo': '999999999999'}]
        respuesta = self.client.post(reverse('registrar_compra'), {'cart_data': json.dumps(carrito)})
        mensajes = ' '.join(self._mensajes(respuesta))
        self.assertIn('El costo de "Yerba" no puede superar', mensajes)
        self.assertNotIn('Out of range', mensajes)


# =============================================================================
# Cierre forzado de caja por un administrador
# =============================================================================
from datetime import timedelta

from auditoria.models import Auditoria, Notificacion
from auditoria.notificaciones import verificar_cajas_abiertas
from usuarios.models import Perfil


class CierreForzadoTests(TestCase):
    def setUp(self):
        self.perfil_admin, _ = Perfil.objects.get_or_create(nombre='Administrador')
        self.perfil_vendedor, _ = Perfil.objects.get_or_create(nombre='Vendedor')
        self.admin = self._crear('20100100', 'jefa', self.perfil_admin)
        self.vendedor = self._crear('30100100', 'vende', self.perfil_vendedor)
        self.otro_vendedor = self._crear('30100101', 'vende2', self.perfil_vendedor)
        self.caja = Caja.objects.create(usuario=self.vendedor, monto_inicial=Decimal('1000'))

    def _crear(self, dni, username, perfil):
        usuario = Usuario.objects.create_user(dni=dni, apellido='T', nombre=username, correo=f'{username}@example.com',
                                              password='x', username=username, id_perfil=perfil)
        usuario.debe_cambiar_clave = False
        usuario.save()
        return usuario

    def _forzar(self, monto='1000', motivo='El vendedor se fue sin cerrar el turno', caja=None):
        return self.client.post(reverse('forzar_cierre_caja', args=[(caja or self.caja).pk]),
                                {'monto_real': monto, 'motivo': motivo})

    def test_admin_fuerza_cierre_contando_y_el_faltante_queda_registrado(self):
        self.client.force_login(self.admin)
        self._forzar(monto='800')
        self.caja.refresh_from_db()
        self.assertEqual(self.caja.estado, 'cerrada')
        self.assertTrue(self.caja.cierre_forzado)
        self.assertEqual(self.caja.cerrada_por, self.admin)
        self.assertEqual(self.caja.monto_final_real, Decimal('800'))
        self.assertEqual(self.caja.diferencia, Decimal('-200'))   # antes quedaba en 0
        evento = Auditoria.objects.get(accion='cierre_forzado')
        self.assertEqual(evento.nivel, 'critico')
        self.assertEqual(evento.usuario, self.admin)
        self.assertIn('vende', evento.descripcion)
        self.assertIn('se fue sin cerrar', evento.descripcion)

    def test_sin_faltante_el_evento_es_advertencia(self):
        self.client.force_login(self.admin)
        self._forzar(monto='1000')
        self.assertEqual(Auditoria.objects.get(accion='cierre_forzado').nivel, 'advertencia')

    def test_motivo_obligatorio(self):
        self.client.force_login(self.admin)
        self._forzar(motivo='   corto ')
        self.caja.refresh_from_db()
        self.assertEqual(self.caja.estado, 'abierta')

    def test_monto_invalido(self):
        self.client.force_login(self.admin)
        self._forzar(monto='1e20')
        self.caja.refresh_from_db()
        self.assertEqual(self.caja.estado, 'abierta')

    def test_no_se_puede_forzar_la_caja_propia(self):
        self.caja.delete()
        propia = Caja.objects.create(usuario=self.admin, monto_inicial=Decimal('0'))
        self.client.force_login(self.admin)
        self._forzar(caja=propia)
        propia.refresh_from_db()
        self.assertEqual(propia.estado, 'abierta')

    def test_vendedor_no_puede_forzar(self):
        self.client.force_login(self.otro_vendedor)
        self._forzar()
        self.caja.refresh_from_db()
        self.assertEqual(self.caja.estado, 'abierta')

    def test_solo_post(self):
        self.client.force_login(self.admin)
        respuesta = self.client.get(reverse('forzar_cierre_caja', args=[self.caja.pk]))
        self.assertEqual(respuesta.status_code, 405)

    def test_admin_ve_el_boton_y_el_vendedor_solo_el_aviso(self):
        self.client.force_login(self.admin)
        respuesta = self.client.get(reverse('gestion_caja'))
        self.assertContains(respuesta, 'Cajas abiertas por otros usuarios')
        self.assertContains(respuesta, reverse('forzar_cierre_caja', args=[self.caja.pk]))

        self.client.force_login(self.otro_vendedor)
        respuesta = self.client.get(reverse('gestion_caja'))
        self.assertContains(respuesta, 'Pedile a un administrador')
        self.assertNotContains(respuesta, reverse('forzar_cierre_caja', args=[self.caja.pk]))

    def test_historial_filtra_y_marca_los_cierres_forzados(self):
        self.client.force_login(self.admin)
        self._forzar()
        Caja.objects.create(usuario=self.otro_vendedor, monto_inicial=Decimal('0'), estado='cerrada')
        respuesta = self.client.get(reverse('gestion_caja'), {'forzados': '1'})
        self.assertEqual([c.pk for c in respuesta.context['historial_cajas']], [self.caja.pk])
        self.assertContains(respuesta, 'Forzado por jefa')


class AlertaCajaAbiertaTests(TestCase):
    def setUp(self):
        Perfil.objects.get_or_create(nombre='Administrador')
        vendedor = Usuario.objects.create_user(dni='30200200', apellido='T', nombre='V', correo='v@example.com',
                                               password='x', username='olvidadizo')
        self.caja = Caja.objects.create(usuario=vendedor, monto_inicial=Decimal('0'))

    def _abierta_hace(self, horas):
        Caja.objects.filter(pk=self.caja.pk).update(fecha_apertura=timezone.now() - timedelta(hours=horas))

    def test_caja_reciente_no_genera_alerta(self):
        self._abierta_hace(2)
        self.assertEqual(verificar_cajas_abiertas(), 0)

    def test_caja_olvidada_genera_una_sola_alerta(self):
        self._abierta_hace(13)
        self.assertEqual(verificar_cajas_abiertas(), 1)
        self.assertEqual(verificar_cajas_abiertas(), 0)   # no se duplica mientras no se lea
        notificacion = Notificacion.objects.get(tipo='caja_abierta')
        self.assertIn('olvidadizo', notificacion.mensaje)
        self.assertEqual(notificacion.destino_perfil.nombre, 'Administrador')


# =============================================================================
# Apertura simultánea de cajas
# =============================================================================
class AperturaSimultaneaTests(TestCase):
    def test_si_ya_hay_otra_caja_abierta_la_nueva_se_descarta(self):
        from unittest.mock import patch
        perfil, _ = Perfil.objects.get_or_create(nombre='Vendedor')
        uno = Usuario.objects.create_user(dni='50000001', apellido='A', nombre='A', correo='a1@example.com',
                                          password='x', username='uno', id_perfil=perfil)
        dos = Usuario.objects.create_user(dni='50000002', apellido='B', nombre='B', correo='b2@example.com',
                                          password='x', username='dos', id_perfil=perfil)
        for usuario in (uno, dos):
            usuario.debe_cambiar_clave = False
            usuario.save()
        Caja.objects.create(usuario=uno, monto_inicial=Decimal('0'))
        self.client.force_login(dos)
        # Simula la carrera: el control previo todavía no ve la caja de "uno".
        with patch('caja.views._hay_caja_abierta', return_value=False):
            self.client.post(reverse('abrir_caja'), {'monto_inicial': '100'})
        self.assertEqual(Caja.objects.filter(estado='abierta').count(), 1)
        self.assertEqual(Caja.objects.get(estado='abierta').usuario, uno)
