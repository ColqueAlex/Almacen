from unittest.mock import patch

from django.test import TestCase
from django.urls import reverse

from auditoria.models import Auditoria
from .models import Perfil, Usuario


class GestionUsuariosPermisosTests(TestCase):
    """Solo un administrador puede dar de baja, reactivar o restablecer la clave de otro usuario."""

    def setUp(self):
        self.perfil_admin, _ = Perfil.objects.get_or_create(nombre='Administrador')
        self.perfil_vendedor, _ = Perfil.objects.get_or_create(nombre='Vendedor')
        self.admin = self._crear('20000001', 'admin', self.perfil_admin)
        self.otro_admin = self._crear('20000002', 'admin2', self.perfil_admin)
        self.vendedor = self._crear('30000001', 'vendedor', self.perfil_vendedor)
        self.inactivo = self._crear('30000002', 'inactivo', self.perfil_vendedor, activo=False)

    def _crear(self, dni, username, perfil, activo=True):
        usuario = Usuario.objects.create_user(
            dni=dni, apellido='Test', nombre=username, correo=f'{username}@example.com',
            password='clave-original', username=username, id_perfil=perfil,
        )
        usuario.debe_cambiar_clave = False
        usuario.activo = activo
        usuario.save()
        return usuario

    def _refrescar(self, *usuarios):
        for usuario in usuarios:
            usuario.refresh_from_db()

    # --- vendedor (sin permisos) -------------------------------------------
    def test_vendedor_no_puede_restablecer_la_clave_de_un_admin(self):
        self.client.force_login(self.vendedor)
        self.client.post(reverse('restablecer_clave', args=[self.admin.dni]))
        self._refrescar(self.admin)
        self.assertTrue(self.admin.check_password('clave-original'))
        self.assertTrue(Auditoria.objects.filter(accion='acceso_denegado', usuario=self.vendedor).exists())

    def test_vendedor_no_puede_reactivar_cuentas(self):
        self.client.force_login(self.vendedor)
        self.client.post(reverse('alta_usuario', args=[self.inactivo.dni]))
        self._refrescar(self.inactivo)
        self.assertFalse(self.inactivo.activo)

    # --- administrador -------------------------------------------------------
    def test_admin_restablece_la_clave_de_un_vendedor(self):
        self.client.force_login(self.admin)
        self.client.post(reverse('restablecer_clave', args=[self.vendedor.dni]))
        self._refrescar(self.vendedor)
        self.assertTrue(self.vendedor.check_password(self.vendedor.dni))
        self.assertTrue(self.vendedor.debe_cambiar_clave)
        evento = Auditoria.objects.get(accion='restablecer_clave')
        self.assertEqual(evento.objeto_id, self.vendedor.dni)
        self.assertNotIn(self.vendedor.dni, evento.descripcion)   # la clave temporal no se guarda

    def test_admin_reactiva_a_un_vendedor(self):
        self.client.force_login(self.admin)
        self.client.post(reverse('alta_usuario', args=[self.inactivo.dni]))
        self._refrescar(self.inactivo)
        self.assertTrue(self.inactivo.activo)

    def test_admin_no_puede_restablecer_la_clave_de_otro_admin(self):
        self.client.force_login(self.admin)
        self.client.post(reverse('restablecer_clave', args=[self.otro_admin.dni]))
        self._refrescar(self.otro_admin)
        self.assertTrue(self.otro_admin.check_password('clave-original'))

    def test_admin_no_puede_restablecer_su_propia_clave_por_esta_via(self):
        self.client.force_login(self.admin)
        self.client.post(reverse('restablecer_clave', args=[self.admin.dni]))
        self._refrescar(self.admin)
        self.assertTrue(self.admin.check_password('clave-original'))

    def test_crear_usuario_no_registra_un_restablecimiento_de_clave(self):
        Usuario.objects.create_user(dni='30000009', apellido='Nuevo', nombre='Usuario',
                                    correo='nuevo@example.com', password='x')
        self.assertFalse(Auditoria.objects.filter(accion='restablecer_clave').exists())

    # --- solo POST -------------------------------------------------------------
    def test_las_acciones_no_aceptan_get(self):
        self.client.force_login(self.admin)
        for nombre, destino in (('baja_usuario', self.vendedor), ('alta_usuario', self.inactivo),
                                ('restablecer_clave', self.vendedor)):
            respuesta = self.client.get(reverse(nombre, args=[destino.dni]))
            self.assertEqual(respuesta.status_code, 405, nombre)
        self._refrescar(self.vendedor, self.inactivo)
        self.assertTrue(self.vendedor.activo)
        self.assertFalse(self.inactivo.activo)


class UsuariosDuplicadosTests(TestCase):
    def setUp(self):
        self.admin = Usuario.objects.create_user(
            dni='20000009', apellido='Root', nombre='Admin', correo='root@example.com',
            password='x', username='rootadmin',
        )
        self.admin.is_superuser = True
        self.admin.debe_cambiar_clave = False
        self.admin.save()
        self.client.force_login(self.admin)

    def test_username_repetido_se_numera(self):
        primero = Usuario.objects.create_user(dni='30000011', apellido='Perez', nombre='Juan',
                                              correo='juan@example.com', password='x')
        segundo = Usuario.objects.create_user(dni='30000012', apellido='Perez', nombre='Jose',
                                              correo='jose@example.com', password='x')
        self.assertEqual(primero.username, 'perezj')
        self.assertEqual(segundo.username, 'perezj2')

    @patch('usuarios.views.validate_email_mx')
    def test_crear_con_correo_repetido_muestra_error(self, _mx):
        respuesta = self.client.post(reverse('crear_usuario'), {
            'dni': '30000013', 'apellido': 'Gomez', 'nombre': 'Ana', 'correo': 'ROOT@example.com',
        })
        self.assertEqual(respuesta.status_code, 200)
        self.assertContains(respuesta, 'Ya existe un usuario registrado con el correo')
        self.assertFalse(Usuario.objects.filter(dni='30000013').exists())

    def test_editar_con_correo_de_otro_usuario_muestra_error(self):
        otro = Usuario.objects.create_user(dni='30000014', apellido='Diaz', nombre='Luz',
                                           correo='luz@example.com', password='x')
        respuesta = self.client.post(reverse('editar_usuario', args=[otro.dni]), {'correo': 'root@example.com'})
        self.assertEqual(respuesta.status_code, 200)
        otro.refresh_from_db()
        self.assertEqual(otro.correo, 'luz@example.com')

    def test_editar_conservando_su_propio_correo(self):
        otro = Usuario.objects.create_user(dni='30000015', apellido='Diaz', nombre='Mar',
                                           correo='mar@example.com', password='x')
        respuesta = self.client.post(reverse('editar_usuario', args=[otro.dni]), {'correo': 'mar@example.com'})
        self.assertRedirects(respuesta, reverse('listar_usuarios'), fetch_redirect_response=False)


# =============================================================================
# Cambio de clave, cambio de rol y mensajes del login
# =============================================================================
class CorreccionesUsuariosTests(TestCase):
    def setUp(self):
        self.admin_perfil, _ = Perfil.objects.get_or_create(nombre='Administrador')
        self.vendedor_perfil, _ = Perfil.objects.get_or_create(nombre='Vendedor')

    def _usuario(self, dni, username, perfil):
        usuario = Usuario.objects.create_user(dni=dni, apellido='X', nombre='Y', correo=f'{username}@example.com',
                                              password='clave-vieja', username=username, id_perfil=perfil)
        usuario.debe_cambiar_clave = False
        usuario.save()
        return usuario

    def test_vendedor_vuelve_al_inicio_tras_cambiar_la_clave(self):
        vendedor = self._usuario('40000001', 'vende', self.vendedor_perfil)
        self.client.force_login(vendedor)
        respuesta = self.client.post(reverse('cambiar_clave'), {'nueva_clave': 'otra-clave', 'confirmar_clave': 'otra-clave'})
        self.assertRedirects(respuesta, reverse('inicio'), fetch_redirect_response=False)

    def test_admin_no_puede_cambiar_el_rol_de_otro_admin(self):
        admin = self._usuario('40000002', 'admin1', self.admin_perfil)
        otro = self._usuario('40000003', 'admin2', self.admin_perfil)
        self.client.force_login(admin)
        self.client.post(reverse('editar_usuario', args=[otro.dni]),
                         {'correo': otro.correo, 'perfil': self.vendedor_perfil.pk})
        otro.refresh_from_db()
        self.assertEqual(otro.id_perfil, self.admin_perfil)

    def test_admin_no_puede_cambiarse_su_propio_rol(self):
        admin = self._usuario('40000004', 'admin3', self.admin_perfil)
        self.client.force_login(admin)
        self.client.post(reverse('editar_usuario', args=[admin.dni]),
                         {'correo': admin.correo, 'perfil': self.vendedor_perfil.pk})
        admin.refresh_from_db()
        self.assertEqual(admin.id_perfil, self.admin_perfil)

    def test_editar_cambia_correo_y_perfil_pero_no_nombre_ni_dni(self):
        admin = self._usuario('40000005', 'admin4', self.admin_perfil)
        vendedor = self._usuario('40000006', 'vende2', self.vendedor_perfil)
        self.client.force_login(admin)
        self.client.post(reverse('editar_usuario', args=[vendedor.dni]), {
            'correo': 'nuevo@example.com', 'perfil': self.admin_perfil.pk,
            'nombre': 'Otro', 'apellido': 'Distinto', 'dni': '99999999',
        })
        vendedor.refresh_from_db()
        self.assertEqual(vendedor.correo, 'nuevo@example.com')
        self.assertEqual(vendedor.id_perfil, self.admin_perfil)
        self.assertEqual((vendedor.nombre, vendedor.apellido, vendedor.dni), ('Y', 'X', '40000006'))

    def test_formulario_muestra_el_perfil_editable_solo_si_la_jerarquia_lo_permite(self):
        admin = self._usuario('40000008', 'admin5', self.admin_perfil)
        vendedor = self._usuario('40000009', 'vende3', self.vendedor_perfil)
        self.client.force_login(admin)
        de_vendedor = self.client.get(reverse('editar_usuario', args=[vendedor.dni]))
        self.assertContains(de_vendedor, 'name="perfil"')
        propio = self.client.get(reverse('editar_usuario', args=[admin.dni]))
        self.assertNotContains(propio, 'name="perfil"')
        self.assertContains(propio, 'No puedes cambiarle el rol a ti mismo.')
        for respuesta in (de_vendedor, propio):
            self.assertContains(respuesta, 'name="correo"')
            self.assertNotContains(respuesta, 'name="nombre"')
            self.assertNotContains(respuesta, 'name="apellido"')

    def test_login_fallido_no_revela_si_el_usuario_existe(self):
        self._usuario('40000007', 'existe', self.vendedor_perfil)
        existente = self.client.post(reverse('login'), {'username': 'existe', 'password': 'mal'}, follow=True)
        inexistente = self.client.post(reverse('login'), {'username': 'noexiste', 'password': 'mal'}, follow=True)
        mensajes = lambda r: [str(m) for m in r.context['messages']]
        self.assertEqual(mensajes(existente), mensajes(inexistente))
