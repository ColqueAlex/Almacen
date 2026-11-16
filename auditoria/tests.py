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


import os
from pathlib import Path
from datetime import date, datetime, timedelta
from decimal import Decimal
from io import StringIO

from django.core import mail
from django.core.management import call_command
from django.db import transaction
from django.test import override_settings

from auditoria.models import Configuracion, Notificacion
from caja.models import Caja
from productos.models import LotesProducto, Productos
from usuarios.models import Perfil
from ventas.models import Clientes, DetallesVentas, Pago, Ventas


@override_settings(
    EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',
    ALERTAS_EMAIL_DESTINATARIO='alertas@example.com',
)
class NotificacionesTests(TestCase):
    def setUp(self):
        self.perfil_admin, _ = Perfil.objects.get_or_create(nombre='Administrador')
        self.perfil_vendedor, _ = Perfil.objects.get_or_create(nombre='Vendedor')
        self.admin = self._crear_usuario('11111111', 'adminuno', 'admin@example.com', self.perfil_admin)
        self.vendedor = self._crear_usuario('22222222', 'vendedos', 'vende@example.com', self.perfil_vendedor)
        self.caja = Caja.objects.create(usuario=self.admin)
        self.producto = Productos.objects.create(nombre='Yerba', categoria='Almacen', precio=Decimal('100'))
        self.lote = LotesProducto.objects.create(id_producto=self.producto, lote='L1', stock_actual=12, precio_costo=Decimal('50'))
        self.cliente = Clientes.objects.create(nombre='Juan', saldo=Decimal('99000'))

    def _crear_usuario(self, dni, username, correo, perfil):
        usuario = Usuario.objects.create_user(
            dni=dni, apellido='Test', nombre='Test', correo=correo,
            password='x', username=username, id_perfil=perfil,
        )
        usuario.debe_cambiar_clave = False
        usuario.save()
        return usuario

    def _vender(self, cantidad, fiado=False):
        """Replica el orden de ventas.views.registrar_venta: venta -> detalles/stock -> saldo."""
        with self.captureOnCommitCallbacks(execute=True):
            with transaction.atomic():
                total = Decimal('100') * cantidad
                venta = Ventas.objects.create(
                    id_caja=self.caja, total=total, fecha=date.today(), hora=datetime.now().time(),
                    id_clientes=self.cliente if fiado else None,
                    estado_de_pago='fiado' if fiado else 'pagado',
                )
                self.lote.stock_actual -= cantidad
                self.lote.save(update_fields=['stock_actual'])
                DetallesVentas.objects.create(
                    venta=venta, producto=self.producto, lote=self.lote, cantidad=cantidad,
                    precio_unitario_venta=Decimal('100'), subtotal=total,
                )
                if fiado:
                    self.cliente.saldo += total
                    self.cliente.save()
        return venta

    def test_configuracion_inicial_cargada(self):
        self.assertEqual(Configuracion.obtener('limite_stock_critico', 0, int), 10)
        self.assertEqual(Configuracion.obtener('limite_deuda_cliente', 0, Decimal), Decimal('100000'))

    def test_stock_bajo_notifica_admin_y_envia_correo(self):
        self._vender(1)  # stock 11 -> sin alerta
        self.assertFalse(Notificacion.objects.exists())
        self._vender(4)  # stock 7 -> alerta
        notif = Notificacion.objects.get(tipo='stock_bajo')
        self.assertEqual(notif.destino_perfil, self.perfil_admin)
        self.assertIn('7 unidad', notif.mensaje)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ['alertas@example.com'])

    def test_stock_bajo_no_duplica_mientras_no_se_lea(self):
        self._vender(5)
        self._vender(2)
        self.assertEqual(Notificacion.objects.filter(tipo='stock_bajo').count(), 1)
        self.assertIn('5 unidad', Notificacion.objects.get().mensaje)
        self.assertEqual(len(mail.outbox), 1)

    def test_limite_stock_configurable(self):
        Configuracion.objects.filter(clave='limite_stock_critico').update(valor='3')
        self._vender(5)
        self.assertFalse(Notificacion.objects.filter(tipo='stock_bajo').exists())

    def test_venta_fiada_supera_limite_credito(self):
        self._vender(1, fiado=True)  # 99100 -> no supera
        self.assertFalse(Notificacion.objects.filter(tipo='limite_credito').exists())
        self._vender(10, fiado=True)  # 100100 -> supera
        self.assertTrue(Notificacion.objects.filter(tipo='limite_credito', mensaje__contains='Juan').exists())

    def test_pago_verifica_limite_credito(self):
        Clientes.objects.filter(pk=self.cliente.pk).update(saldo=Decimal('150000'))
        with self.captureOnCommitCallbacks(execute=True):
            Pago.objects.create(cliente=self.cliente, id_caja=self.caja, monto=Decimal('1000'), usuario=self.admin)
        self.assertTrue(Notificacion.objects.filter(tipo='limite_credito').exists())

    def test_comando_alertas_vencimiento(self):
        hoy = date.today()
        LotesProducto.objects.create(id_producto=self.producto, lote='PRONTO', fecha_vencimiento=hoy + timedelta(days=10), stock_actual=5, precio_costo=1)
        LotesProducto.objects.create(id_producto=self.producto, lote='LEJOS', fecha_vencimiento=hoy + timedelta(days=60), stock_actual=5, precio_costo=1)
        LotesProducto.objects.create(id_producto=self.producto, lote='VACIO', fecha_vencimiento=hoy + timedelta(days=5), stock_actual=0, precio_costo=1)
        call_command('alertas_vencimiento', stdout=StringIO())
        call_command('alertas_vencimiento', stdout=StringIO())  # no duplica
        notifs = Notificacion.objects.filter(tipo='vencimiento')
        self.assertEqual(notifs.count(), 1)
        self.assertIn('PRONTO', notifs.get().mensaje)

    def test_badge_y_bandeja_por_perfil(self):
        notif = Notificacion.objects.create(tipo='stock_bajo', mensaje='Prueba', destino_perfil=self.perfil_admin)

        self.client.force_login(self.vendedor)
        respuesta = self.client.get(reverse('bandeja_notificaciones'))
        self.assertEqual(respuesta.context['notificaciones_no_leidas'], 0)
        self.assertNotContains(respuesta, 'class="notif-bell-badge"')
        self.assertEqual(self.client.post(reverse('marcar_notificacion_leida', args=[notif.id])).status_code, 404)

        self.client.force_login(self.admin)
        respuesta = self.client.get(reverse('bandeja_notificaciones'))
        self.assertEqual(respuesta.context['notificaciones_no_leidas'], 1)
        self.assertContains(respuesta, 'class="notif-bell-badge"')
        self.assertContains(respuesta, 'Prueba')

        self.client.post(reverse('marcar_notificacion_leida', args=[notif.id]))
        notif.refresh_from_db()
        self.assertTrue(notif.leida)
        self.assertIsNotNone(notif.fecha_lectura)
        self.assertEqual(self.client.get(reverse('bandeja_notificaciones')).context['notificaciones_no_leidas'], 0)

    def test_marcar_todas_leidas(self):
        for i in range(3):
            Notificacion.objects.create(tipo='vencimiento', mensaje=f'N{i}', destino_perfil=self.perfil_admin)
        self.client.force_login(self.admin)
        self.client.post(reverse('marcar_todas_leidas'))
        self.assertFalse(Notificacion.objects.filter(leida=False).exists())


import shutil
import subprocess
import tempfile
from unittest.mock import patch

from django.contrib.staticfiles import finders


class PanelAnaliticaTests(TestCase):
    def setUp(self):
        perfil_admin, _ = Perfil.objects.get_or_create(nombre='Administrador')
        perfil_vendedor, _ = Perfil.objects.get_or_create(nombre='Vendedor')
        self.admin = Usuario.objects.create_user(dni='33333333', apellido='A', nombre='A', correo='pa@example.com',
                                                 password='x', username='panadmin', id_perfil=perfil_admin)
        self.vendedor = Usuario.objects.create_user(dni='44444444', apellido='V', nombre='V', correo='pv@example.com',
                                                    password='x', username='panvende', id_perfil=perfil_vendedor)
        for u in (self.admin, self.vendedor):
            u.debe_cambiar_clave = False
            u.save()
        # BASE_DIR temporal para no depender de las imágenes reales
        self.base_tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.base_tmp, ignore_errors=True)

    def test_solo_administrador(self):
        self.client.force_login(self.vendedor)
        self.assertRedirects(self.client.get(reverse('panel_analitica')), reverse('inicio'), fetch_redirect_response=False)

    def test_avisa_si_faltan_graficos_y_muestra_los_existentes(self):
        self.client.force_login(self.admin)
        with override_settings(BASE_DIR=Path(self.base_tmp)):
            respuesta = self.client.get(reverse('panel_analitica'))
            self.assertTrue(respuesta.context['faltan_graficos'])
            self.assertContains(respuesta, 'python analitica_el_refugio.py')

            carpeta = Path(self.base_tmp) / 'static' / 'analitica'
            carpeta.mkdir(parents=True)
            for nombre in ('grafico_dashboard.png', 'grafico_ventas_categoria.png', 'grafico_metodos_pago.png',
                           'grafico_evolucion.png', 'grafico_heatmap.png'):
                (carpeta / nombre).write_bytes(b'png')
            respuesta = self.client.get(reverse('panel_analitica'))
        self.assertFalse(respuesta.context['faltan_graficos'])
        self.assertContains(respuesta, '/static/analitica/grafico_heatmap.png?v=')
        self.assertContains(respuesta, 'grafico-destacado')
        self.assertIsNotNone(respuesta.context['ultima_actualizacion'])

    def test_static_analitica_esta_en_staticfiles_dirs(self):
        # .gitkeep existe en el repo: si Django lo encuentra, la carpeta se sirve como estática
        self.assertIsNotNone(finders.find('analitica/.gitkeep'))

    # ---- Formulario de filtros (POST -> subprocess) ----------------------
    def _post(self, **datos):
        self.client.force_login(self.admin)
        return self.client.post(reverse('panel_analitica'), datos)

    @patch('auditoria.views.subprocess.run')
    def test_post_ejecuta_script_con_las_fechas_y_redirige(self, mock_run):
        mock_run.return_value = subprocess.CompletedProcess(args=[], returncode=0, stdout='Listo.', stderr='')
        ayer = (date.today() - timedelta(days=1)).isoformat()
        respuesta = self._post(fecha_inicio='2026-11-01', fecha_fin=ayer)

        self.assertRedirects(respuesta, reverse('panel_analitica'), fetch_redirect_response=False)
        comando = mock_run.call_args.args[0]
        self.assertTrue(comando[1].endswith('analitica_el_refugio.py'))
        self.assertEqual(comando[2:], ['--fecha_fin', ayer, '--fecha_inicio', '2026-11-01'])
        self.assertNotIn('shell', mock_run.call_args.kwargs)

    @patch('auditoria.views.subprocess.run')
    def test_post_sin_fecha_inicio_usa_todo_el_historial(self, mock_run):
        mock_run.return_value = subprocess.CompletedProcess(args=[], returncode=0, stdout='', stderr='')
        self._post(fecha_inicio='', fecha_fin='')
        self.assertEqual(mock_run.call_args.args[0][2:], ['--fecha_fin', date.today().isoformat()])

    @patch('auditoria.views.subprocess.run')
    def test_error_del_script_se_muestra_y_conserva_las_fechas(self, mock_run):
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=1, stdout='', stderr='Traceback...\nError al conectar a MySQL: Connection refused')
        respuesta = self._post(fecha_inicio='2026-11-01', fecha_fin='2026-11-10')
        self.assertEqual(respuesta.status_code, 200)
        self.assertContains(respuesta, 'Error al conectar a MySQL: Connection refused')
        self.assertContains(respuesta, 'value="2026-11-01"')

    @patch('auditoria.views.subprocess.run')
    def test_libreria_faltante_sugiere_pip_install(self, mock_run):
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=1, stdout='', stderr="ModuleNotFoundError: No module named 'pandas'")
        respuesta = self._post(fecha_fin='2026-11-10')
        self.assertContains(respuesta, 'pip install -r requirements-analitica.txt')

    @patch('auditoria.views.subprocess.run', side_effect=subprocess.TimeoutExpired(cmd='python', timeout=120))
    def test_timeout_no_rompe_la_pagina(self, _mock_run):
        respuesta = self._post(fecha_fin='2026-11-10')
        self.assertEqual(respuesta.status_code, 200)
        self.assertContains(respuesta, 'se canceló')

    @patch('auditoria.views.subprocess.run')
    def test_fechas_invalidas_no_ejecutan_el_script(self, mock_run):
        manana = (date.today() + timedelta(days=1)).isoformat()
        for datos in ({'fecha_inicio': '2026-11-10', 'fecha_fin': '2026-11-01'},
                      {'fecha_fin': manana},
                      {'fecha_inicio': 'no-es-fecha'}):
            respuesta = self._post(**datos)
            self.assertEqual(respuesta.status_code, 200)
        mock_run.assert_not_called()

    @patch('auditoria.views.subprocess.run')
    def test_vendedor_no_puede_ejecutar_el_script(self, mock_run):
        self.client.force_login(self.vendedor)
        self.client.post(reverse('panel_analitica'), {'fecha_fin': '2026-11-10'})
        mock_run.assert_not_called()

    def test_get_precarga_filtros_de_la_ultima_generacion(self):
        self.client.force_login(self.admin)
        with override_settings(BASE_DIR=Path(self.base_tmp)):
            carpeta = Path(self.base_tmp) / 'static' / 'analitica'
            carpeta.mkdir(parents=True)
            (carpeta / 'ultima_generacion.json').write_text(
                '{"fecha_inicio": "2026-11-01", "fecha_fin": "2026-11-12"}', encoding='utf-8')
            respuesta = self.client.get(reverse('panel_analitica'))
        self.assertContains(respuesta, 'value="2026-11-01"')
        self.assertContains(respuesta, 'value="2026-11-12"')
        self.assertContains(respuesta, 'del 01/11/2026')
