from django.db import migrations

CONFIGURACION_INICIAL = [
    ('limite_stock_critico', '10', 'Si el stock de un producto queda por debajo de este valor tras una venta, se notifica al Administrador y se envía un correo.'),
    ('limite_deuda_cliente', '100000', 'Si el saldo (deuda) de un cliente supera este monto tras una venta fiada o un pago, se notifica al Administrador.'),
    ('dias_alerta_vencimiento', '30', 'Días de anticipación con los que el comando alertas_vencimiento avisa sobre lotes por vencer.'),
]


def cargar_configuracion(apps, schema_editor):
    Configuracion = apps.get_model('auditoria', 'Configuracion')
    for clave, valor, descripcion in CONFIGURACION_INICIAL:
        Configuracion.objects.get_or_create(clave=clave, defaults={'valor': valor, 'descripcion': descripcion})


def quitar_configuracion(apps, schema_editor):
    Configuracion = apps.get_model('auditoria', 'Configuracion')
    Configuracion.objects.filter(clave__in=[c[0] for c in CONFIGURACION_INICIAL]).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('auditoria', '0003_configuracion_notificacion'),
    ]

    operations = [
        migrations.RunPython(cargar_configuracion, quitar_configuracion),
    ]
