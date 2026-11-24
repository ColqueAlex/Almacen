"""
Job diario: genera notificaciones para lotes próximos a vencer.

Uso:
    python manage.py alertas_vencimiento            # usa la configuración (default 30 días)
    python manage.py alertas_vencimiento --dias 15

Para programarlo (simulación de cron/Programador de tareas de Windows), ejecutarlo
una vez por día. Las alertas no leídas de un mismo lote no se duplican.
"""
from django.core.management.base import BaseCommand, CommandError

from auditoria.notificaciones import verificar_cajas_abiertas, verificar_limpieza_productos, verificar_vencimientos


class Command(BaseCommand):
    help = 'Revisa los lotes de productos que vencen pronto y genera notificaciones.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--dias', type=int, default=None,
            help='Días de anticipación (por defecto: configuración "dias_alerta_vencimiento" o 30).',
        )

    def handle(self, *args, **options):
        dias = options['dias']
        if dias is not None and dias < 0:
            raise CommandError('--dias debe ser un número positivo.')
        creadas = verificar_vencimientos(dias)
        self.stdout.write(self.style.SUCCESS(f'Alertas de vencimiento generadas: {creadas}'))
        cajas = verificar_cajas_abiertas()
        self.stdout.write(self.style.SUCCESS(f'Alertas de cajas abiertas demasiado tiempo: {cajas}'))
        candidatos = verificar_limpieza_productos()
        self.stdout.write(self.style.SUCCESS(f'Productos sugeridos para eliminar: {candidatos}'))
