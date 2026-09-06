•  Clonar el repositorio:
git clone https://github.com/ColqueAlex/Almacen.git
•  Entrar a la carpeta del proyecto:
cd Almacen
•  Asegurarse de tener Docker Desktop abierto y ejecutar:
docker compose up -d --build

•  Aplicar las migraciones de la base de datos:
docker compose exec web python manage.py migrate

Entrar a la carpeta del proyecto:
cd Almacen

Asegurarse de tener Docker Desktop abierto y ejecutar:
docker compose up -d --build

Aplicar las migraciones de la base de datos:
docker compose exec web python manage.py migrate

Si no te abre, normalmente es por una de estas 2 cosas
docker compose up -d --build
docker compose ps

http://localhost:8000/