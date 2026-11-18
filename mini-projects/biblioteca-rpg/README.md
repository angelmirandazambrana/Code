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

## Hoja de ruta

1. ✅ Catálogo, portadas, búsqueda y filtros.
2. Clasificador: carpeta `_Entrada` → destino propuesto, vistas *Novedades* y *Revisar*.
3. Descargador de Telegram conectado al catálogo (no descargar lo que ya existe).
4. Datos de RPGGeek / BoardGameGeek (temas, portadas, años) con revisión manual.
