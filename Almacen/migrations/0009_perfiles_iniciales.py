from django.db import migrations


def crear_perfiles(apps, schema_editor):
    Perfil = apps.get_model('Almacen', 'Perfil')
    perfiles = [
        ('Administrador', 'Acceso completo al sistema.'),
        ('Encargado de almacén', 'Gestiona productos y usuarios.'),
        ('Vendedor', 'Consulta y gestiona productos.'),
    ]
    for nombre, descripcion in perfiles:
        Perfil.objects.get_or_create(
            nombre=nombre,
            defaults={'descripcion': descripcion},
        )


def eliminar_perfiles(apps, schema_editor):
    Perfil = apps.get_model('Almacen', 'Perfil')
    Perfil.objects.filter(
        nombre__in=['Administrador', 'Encargado de almacén', 'Vendedor']
    ).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('Almacen', '0008_alter_productos_nombre'),
    ]

    operations = [
        migrations.RunPython(crear_perfiles, eliminar_perfiles),
    ]
