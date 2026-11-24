"""
Backup completo y diario de la base de datos MySQL.

Ejecuta mysqldump, comprime el .sql en un .zip dentro de backups/ y borra los
.zip de más de N días (rotación: por defecto se conserva la última semana).

Uso:
    python manage.py backup_db             # backup + rotación de 7 días
    python manage.py backup_db --dias 14   # conserva 2 semanas

Configuración (.env):
    MYSQLDUMP_PATH   ruta a mysqldump.exe. Si no se define, se busca en el PATH.
    DB_NAME, DB_USER, DB_PASSWORD, DB_HOST, DB_PORT   (los mismos que usa Django)

Restaurar un backup:
    1. Descomprimir el .zip (contiene un único .sql).
    2. mysql -h HOST -P PUERTO -u USUARIO -p NOMBRE_BD < backup_....sql

Resultado: logs/auditoria.log (INFO/ERROR) y logs/django.log (errores).
El comando termina con código de salida 1 si falla, para que el
Programador de tareas de Windows lo marque como error.
"""
import logging
import os
import shutil
import subprocess
import tempfile
import time
import zipfile
from datetime import datetime
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from auditoria.servicios import registrar_evento

logger = logging.getLogger('auditoria.backup')

TIMEOUT_SEGUNDOS = 30 * 60
MARCA_FIN_DUMP = b'-- Dump completed'


class Command(BaseCommand):
    help = 'Genera un backup completo de la base MySQL comprimido en backups/ y borra los de más de 7 días.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--dias', type=int, default=7,
            help='Días de antigüedad a partir de los cuales se borran los .zip de backups/ (default: 7).',
        )

    def handle(self, *args, **options):
        dias = options['dias']
        if dias < 1:
            raise CommandError('--dias debe ser 1 o más.')

        carpeta = Path(settings.BACKUP_DIR)
        carpeta.mkdir(parents=True, exist_ok=True)

        try:
            archivo_zip = self._generar_backup(carpeta)
        except ErrorBackup as error:
            logger.error('Backup de la base de datos FALLIDO: %s', error)
            registrar_evento('backup', f'Falló la copia de seguridad de la base de datos: {error}', nivel='critico')
            raise CommandError(str(error)) from None

        tamano_kb = archivo_zip.stat().st_size / 1024
        logger.info('Backup de la base de datos OK: %s (%.1f KB)', archivo_zip.name, tamano_kb)
        registrar_evento('backup', f'Copia de seguridad generada: {archivo_zip.name} ({tamano_kb:.1f} KB)')

        # La rotación solo corre si el backup de hoy salió bien: si los backups
        # fallaran varios días seguidos, no se borran las últimas copias buenas.
        for borrado in self._rotar(carpeta, dias, conservar=archivo_zip):
            logger.info('Backup antiguo eliminado (más de %s días): %s', dias, borrado)

    # ------------------------------------------------------------------ backup
    def _generar_backup(self, carpeta):
        db = settings.DATABASES['default']
        mysqldump = _ruta_mysqldump()

        marca = datetime.now().strftime('%Y%m%d_%H%M%S')
        nombre = f"backup_{db['NAME']}_{marca}"
        archivo_sql = carpeta / f'{nombre}.sql'
        archivo_zip = carpeta / f'{nombre}.zip'
        zip_parcial = carpeta / f'{nombre}.zip.part'

        # La contraseña va en un archivo de opciones temporal (y no en la línea de
        # comandos, donde la vería cualquier proceso del sistema).
        fd, opciones = tempfile.mkstemp(prefix='mysqldump_', suffix='.cnf')
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as f:
                f.write('[client]\n')
                f.write(f"user=\"{_escapar(db['USER'])}\"\n")
                f.write(f"password=\"{_escapar(db['PASSWORD'])}\"\n")
                f.write(f"host=\"{_escapar(db['HOST'] or '127.0.0.1')}\"\n")
                f.write(f"port={db['PORT'] or 3306}\n")

            comando = [
                mysqldump,
                f'--defaults-extra-file={opciones}',  # debe ser la primera opción
                '--single-transaction',               # copia consistente sin bloquear tablas InnoDB
                '--routines',
                '--triggers',
                '--no-tablespaces',                   # evita requerir el privilegio PROCESS
                '--default-character-set=utf8mb4',
                f'--result-file={archivo_sql}',
                db['NAME'],
            ]
            try:
                resultado = subprocess.run(comando, capture_output=True, timeout=TIMEOUT_SEGUNDOS)
            except FileNotFoundError:
                raise ErrorBackup(f'No se encontró mysqldump.exe en "{mysqldump}". Configurá MYSQLDUMP_PATH en el .env.')
            except subprocess.TimeoutExpired:
                raise ErrorBackup(f'mysqldump superó el tiempo máximo de {TIMEOUT_SEGUNDOS // 60} minutos.')

            if resultado.returncode != 0:
                detalle = resultado.stderr.decode('utf-8', errors='replace').strip() or 'sin detalle'
                raise ErrorBackup(f'mysqldump terminó con código {resultado.returncode}: {detalle}')
            _verificar_dump(archivo_sql)

            with zipfile.ZipFile(zip_parcial, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
                zf.write(archivo_sql, arcname=archivo_sql.name)
            with zipfile.ZipFile(zip_parcial) as zf:
                if zf.testzip() is not None:
                    raise ErrorBackup('El archivo .zip generado está dañado.')
            os.replace(zip_parcial, archivo_zip)  # recién ahora queda visible como backup válido
            return archivo_zip
        except OSError as error:
            raise ErrorBackup(f'Error de archivos al generar el backup: {error}')
        finally:
            for temporal in (Path(opciones), archivo_sql, zip_parcial):
                temporal.unlink(missing_ok=True)

    # ---------------------------------------------------------------- rotación
    def _rotar(self, carpeta, dias, conservar):
        limite = time.time() - dias * 24 * 60 * 60
        borrados = []
        for archivo in carpeta.glob('*.zip'):
            if archivo == conservar or not archivo.is_file():
                continue
            if archivo.stat().st_mtime < limite:
                try:
                    archivo.unlink()
                    borrados.append(archivo.name)
                except OSError as error:
                    logger.error('No se pudo borrar el backup antiguo %s: %s', archivo.name, error)
        return borrados


class ErrorBackup(Exception):
    pass


def _ruta_mysqldump():
    configurada = getattr(settings, 'MYSQLDUMP_PATH', '')
    if configurada:
        if not Path(configurada).is_file():
            raise ErrorBackup(f'No se encontró mysqldump.exe en "{configurada}" (revisá MYSQLDUMP_PATH en el .env).')
        return configurada
    en_path = shutil.which('mysqldump')
    if not en_path:
        raise ErrorBackup('No se encontró mysqldump.exe: definí MYSQLDUMP_PATH en el .env o agregalo al PATH.')
    return en_path


def _escapar(valor):
    return str(valor).replace('\\', '\\\\').replace('"', '\\"')


def _verificar_dump(archivo_sql):
    """mysqldump escribe '-- Dump completed' al final solo si terminó bien."""
    if not archivo_sql.is_file() or archivo_sql.stat().st_size == 0:
        raise ErrorBackup('mysqldump no generó ningún contenido.')
    with open(archivo_sql, 'rb') as f:
        f.seek(max(0, archivo_sql.stat().st_size - 512))
        if MARCA_FIN_DUMP not in f.read():
            raise ErrorBackup('El volcado está incompleto (falta la marca "-- Dump completed").')
