# El Refugio - Sistema de Gestión de Almacén

Sistema web para administrar el día a día de un almacén de barrio: ventas en mostrador,
compras a proveedores, control de stock por lotes, cuentas corrientes de clientes (fiados),
caja, auditoría de operaciones y notificaciones automáticas.

## Funcionalidades

- **Ventas (POS):** registro de ventas con múltiples métodos de pago, devoluciones e historial.
- **Fiados:** cuentas corrientes de clientes, registro de pagos y saldo pendiente.
- **Compras:** carga de compras a proveedores, anulación con motivo y actualización de stock.
- **Productos y lotes:** inventario por categoría y marca, lotes con fecha de vencimiento, proveedores.
- **Caja:** apertura y cierre de caja por usuario, con arqueo (monto esperado vs. real).
- **Usuarios y permisos:** perfiles con permisos por módulo, bloqueo tras intentos fallidos.
- **Auditoría:** registro de quién hizo qué y cuándo, con datos anteriores y nuevos.
- **Notificaciones:** alertas de stock bajo y de productos próximos a vencer (en el sistema y por correo).
- **Reportes y analítica:** reportes en PDF y dashboard con gráficos generados con Pandas y Matplotlib.

## Tecnologías

| Área | Tecnología |
|---|---|
| Backend | Python 3.11+, Django 4.2 |
| Base de datos | MySQL 8 |
| Analítica | Pandas, NumPy, Matplotlib |
| Reportes PDF | ReportLab |
| Contenedores | Docker, Docker Compose (MySQL + phpMyAdmin + Django) |

## Requisitos previos

- [Python 3.11 o superior](https://www.python.org/downloads/)
- [MySQL 8](https://dev.mysql.com/downloads/) (o Docker, que ya lo incluye)
- Git
- *Opcional:* [Docker Desktop](https://www.docker.com/products/docker-desktop/)

## Instalación local

1. **Clonar el repositorio**

   ```bash
   git clone https://github.com/ColqueAlex/Almacen.git
   cd Almacen
   ```

2. **Crear y activar el entorno virtual**

   ```bash
   python -m venv venv

   # Windows
   venv\Scripts\activate
   # Linux / macOS
   source venv/bin/activate
   ```

3. **Instalar las dependencias**

   ```bash
   pip install -r requirements.txt
   ```

4. **Configurar las variables de entorno**

   Copiar el archivo de ejemplo y completar los valores:

   ```bash
   # Windows
   copy .env.example .env
   # Linux / macOS
   cp .env.example .env
   ```

   | Variable | Descripción |
   |---|---|
   | `SECRET_KEY` | Clave secreta de Django (obligatoria) |
   | `DEBUG` | `True` en desarrollo, `False` en producción |
   | `ALLOWED_HOSTS` | Hosts permitidos, separados por coma |
   | `DB_NAME`, `DB_USER`, `DB_PASSWORD`, `DB_HOST`, `DB_PORT` | Conexión a MySQL |
   | `EMAIL_HOST_USER`, `EMAIL_HOST_PASSWORD` | Cuenta SMTP (Gmail: usar contraseña de aplicación) |
   | `ALERTAS_EMAIL_DESTINATARIO` | Correo que recibe las alertas |

   La base de datos indicada en `DB_NAME` debe existir en MySQL:

   ```sql
   CREATE DATABASE Almacen_ElRefugio CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
   ```

5. **Aplicar las migraciones y crear el administrador**

   ```bash
   python manage.py migrate
   python manage.py createsuperuser
   ```

   El comando pide username, DNI, apellido, nombre, correo y contraseña.

6. **Levantar el servidor**

   ```bash
   python manage.py runserver
   ```

   Abrir http://localhost:8000/ (panel de administración en http://localhost:8000/admin/).

## Ejecución con Docker

Con Docker Desktop abierto, desde la carpeta del proyecto:

```bash
docker compose up -d --build
docker compose exec web python manage.py migrate
docker compose exec web python manage.py createsuperuser
```

| Servicio | URL |
|---|---|
| Aplicación Django | http://localhost:8000/ |
| phpMyAdmin | http://localhost:8080/ |
| MySQL (desde el host) | `localhost:3307` |

Las credenciales de MySQL del contenedor están definidas en `docker-compose.yml`
(`myuser` / `mypassword`, base `mydatabase`). Son de la conexión de la aplicación con la base
de datos, no del administrador del sistema.

Si la aplicación no abre, verificar el estado de los contenedores:

```bash
docker compose ps
docker compose logs web
```

Para detenerlo: `docker compose down` (agregar `-v` borra también los datos de la base).

## Tareas complementarias

- **Analítica:** el dashboard regenera los gráficos ejecutando `analitica_el_refugio.py`, que
  también puede correrse a mano: `python analitica_el_refugio.py`. Las imágenes se guardan en
  `static/analitica/` (no se suben a Git).
- **Alertas de vencimiento:** `python manage.py alertas_vencimiento`. En Windows se puede
  programar como tarea diaria con `scripts/programar_alertas_vencimiento.ps1`; la salida queda
  en `logs/alertas_vencimiento.log`.
- **Backup diario de la base de datos:** `python manage.py backup_db` genera un backup completo
  con `mysqldump` (ruta en `MYSQLDUMP_PATH` del `.env`), lo comprime en `backups/` y borra los
  `.zip` de más de 7 días. Se programa a las 3:00 con `scripts/programar_backup_db.ps1`.
  Para restaurar: descomprimir el `.zip` y ejecutar `mysql -h HOST -P PUERTO -u USUARIO -p BASE < archivo.sql`.
  La carpeta `backups/` no se sube a Git.

## Estructura del proyecto

```
├── myproject/               Configuración de Django (settings, urls, wsgi)
├── usuarios/                Usuarios, perfiles, permisos y login
├── productos/               Productos, lotes con vencimiento y proveedores
├── ventas/                  Ventas, clientes, fiados, pagos y devoluciones
├── compras/                 Compras a proveedores
├── caja/                    Apertura y cierre de caja
├── auditoria/               Auditoría, notificaciones, configuración y dashboard de analítica
├── reportes/                Reportes y exportación a PDF
├── templates/               Plantillas base (layout, login, inicio)
├── static/                  CSS, imágenes y gráficos generados
├── scripts/                 Tareas programadas (alertas de vencimiento)
├── analitica_el_refugio.py  Script de analítica (Pandas + Matplotlib)
├── requirements.txt         Dependencias
├── .env.example             Plantilla de variables de entorno
├── Dockerfile
└── docker-compose.yml
```

## Tests

```bash
python manage.py test
```

El usuario de MySQL necesita permiso para crear la base de pruebas (`test_<DB_NAME>`).
