from django.apps import AppConfig


class ProductosConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'productos'

    def ready(self):
        import productos.cache  # noqa: F401 (registra los signals que invalidan la caché)
