from django.db import connection


UMBRAL_ALMACENAMIENTO_BD_GB = 10
UMBRAL_ALMACENAMIENTO_BD_BYTES = UMBRAL_ALMACENAMIENTO_BD_GB * 1000 ** 3


def tamano_base_datos_bytes():
    if connection.vendor != 'mysql':
        return None

    with connection.cursor() as cursor:
        cursor.execute(
            'SELECT COALESCE(SUM(data_length + index_length), 0) '
            'FROM information_schema.TABLES WHERE table_schema = DATABASE()'
        )
        return int(cursor.fetchone()[0] or 0)
