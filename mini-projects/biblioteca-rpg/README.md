# Biblioteca

Catálogo web de la biblioteca de PDFs de rol, wargames, revistas y juegos de mesa de `V:\`
(`\\OPENMEDIAVAULT3\Isengard`): portadas, búsqueda y filtros por sistema, edición, editorial, tipo…

- **Solo lee la biblioteca.** Escribe únicamente en su carpeta de datos (catálogo SQLite y miniaturas).
- Casi todos los datos se deducen de la ruta, gracias a la estructura de la reorganización
  (`V:\_Reorganizacion`). Del PDF se sacan las páginas, el título y el autor internos, y la portada.

## Piezas

| Fichero | Qué hace |
|---|---|
| `src/biblioteca_rpg/rutas.py` | Deduce categoría, sistema, edición, editorial, año, tipo, idioma… de la ruta |
| `src/biblioteca_rpg/indexador.py` | Recorre la biblioteca, actualiza el catálogo y genera portadas (incremental) |
| `src/biblioteca_rpg/consultas.py` | Búsqueda de texto (FTS5) y recuentos por faceta |
| `src/biblioteca_rpg/web.py` | Web (FastAPI + plantillas Jinja) |
| `docker-compose.yml` | Dos servicios: `web` e `indexador` (cada `INTERVALO_HORAS`) |

## Desarrollo en Windows

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt

# Indexar una muestra (lee de V:\ y escribe en .\datos)
$env:PYTHONPATH = "src"; $env:BIBLIOTECA_DATOS = "datos"
.\.venv\Scripts\python.exe -m biblioteca_rpg.indexador --solo "RPG/Shadowrun" --solo "Magazines/Dragon Magazine"

# Web en http://localhost:8090
.\.venv\Scripts\python.exe dev_server.py

# Tests y estilo
.\.venv\Scripts\python.exe -m pytest
.\.venv\Scripts\python.exe -m ruff check src tests
```

Opciones del indexador: `--sin-portadas`, `--limite N`, `--solo CARPETA` (repetible), `--reintentar-errores`.
Se puede cortar y relanzar: solo procesa lo nuevo o cambiado.

## Despliegue en OMV3

1. Copia esta carpeta al servidor (por ejemplo a `Isengard/_Catalogo/app` desde Windows, o con `git clone`).
2. Por SSH en OMV3, crea el `.env` a partir de `.env.example` y revisa las rutas
   (`ls /srv` para ver la de Isengard) y `PUID`/`PGID` (`id tu_usuario`).
3. Crea la carpeta de datos y arranca:
   ```bash
   mkdir -p "$(grep ^DATOS_HOST .env | cut -d= -f2)"
   docker compose up -d --build
   docker compose logs -f indexador
   ```
4. Abre `http://192.168.1.132:8090`.

La primera pasada genera ~90.000 portadas: cuenta con varias horas. La web se puede usar desde
el principio; las portadas van apareciendo. Las siguientes pasadas solo tocan lo nuevo.

**No publicar en internet sin autenticación.** Si más adelante se expone con Nginx Proxy Manager,
que sea en un subdominio y con acceso protegido.

## Telegram

El servicio `telegram` descarga a `_Inbox` los ficheros nuevos de los canales que elijas, solo si no
los tienes ya (mismo tamaño en la biblioteca, en la entrada o el mismo documento ya descargado).
Después el clasificador los coloca. Ve la biblioteca en solo lectura y solo escribe en `_Inbox`.

1. En <https://my.telegram.org> → *API development tools*, crea una aplicación y copia `api_id` y
   `api_hash` en el `.env` de OMV3 (`TELEGRAM_API_ID`, `TELEGRAM_API_HASH`). No los subas a git.
2. Inicia sesión una vez (pide tu teléfono y el código que te llega por Telegram):
   ```bash
   cd /opt/biblioteca-rpg
   docker compose run --rm telegram python -m biblioteca_rpg.telegram login
   ```
   La sesión queda en `datos/telegram/` con permisos 600: da acceso a tu cuenta, no la copies.
3. Lista tus grupos y canales y copia los ids que quieras en `TELEGRAM_CANALES` (separados por comas):
   ```bash
   docker compose run --rm telegram python -m biblioteca_rpg.telegram canales
   ```
4. `docker compose up -d telegram`. Cada canal se empieza a leer **desde ese momento**. Para traer
   mensajes anteriores:
   ```bash
   docker compose run --rm telegram python -m biblioteca_rpg.telegram historial --canal ID --desde 2026-10-01
   ```

En la web, la página *Telegram* muestra qué se descargó y qué se saltó (y por qué).

## Hoja de ruta

1. ✅ Catálogo, portadas, búsqueda y filtros.
2. ✅ Datos del texto de los PDF (año, editorial, ISBN); RPGGeek/BGG pendiente del token.
3. ✅ Clasificador de `_Inbox`: destino propuesto, *Entrada* y *Novedades*.
4. ✅ Descargador de Telegram conectado al catálogo (no descarga lo que ya existe).
