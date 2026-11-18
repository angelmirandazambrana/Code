# Code

Carpeta base para mini-proyectos y pruebas rápidas.

## Estructura

- `mini-projects/`: proyectos activos y pequeños experimentos
- `templates/`: referencias o plantillas reutilizables
- `archive/`: proyectos viejos o pausados
- `mini-projects/Bookmarks management/`: proyecto actual ya existente

## Flujo recomendado desde VS Code

### Opción 1: abrir la carpeta base

Úsala cuando quieras crear proyectos nuevos o moverte entre varios mini-proyectos.

1. Abre VS Code
2. Ve a `File > Open Folder...`
3. Abre `C:\Users\angel\Documents\Code`
4. Abre un terminal integrado
5. Crea un proyecto nuevo con:

```powershell
.\new_python_project.ps1 nombre-del-proyecto
```

6. Cuando termine, abre la carpeta del proyecto:

```text
C:\Users\angel\Documents\Code\mini-projects\nombre-del-proyecto
```

### Opción 2: abrir directamente un proyecto

Úsala cuando ya vayas a trabajar centrado en un solo mini-proyecto.

1. Abre VS Code
2. Ve a `File > Open Folder...`
3. Abre:

```text
C:\Users\angel\Documents\Code\mini-projects\nombre-del-proyecto
```

VS Code usará el `.venv` local del proyecto.

## Crear un mini-proyecto

Desde `C:\Users\angel\Documents\Code`, ejecuta:

```powershell
.\new_python_project.ps1 nombre-del-proyecto
```

Esto crea:

- `.venv`
- `src/`
- `tests/`
- `requirements.txt`
- `.vscode/settings.json`
- archivos base para arrancar rápido

## Comandos habituales

Entra en la carpeta del proyecto:

```powershell
cd .\mini-projects\nombre-del-proyecto
```

### Activar entorno virtual

```powershell
.\.venv\Scripts\Activate.ps1
```

### Ejecutar el script principal

```powershell
python .\src\main.py
```

### Ejecutar tests

```powershell
$env:PYTHONPATH='src'
python -m pytest -q
```

### Ejecutar Ruff

```powershell
python -m ruff check .
```

### Formatear con Ruff

```powershell
python -m ruff format .
```

## Cuándo abrir `Code` y cuándo abrir un proyecto

Abre `C:\Users\angel\Documents\Code` si:

- vas a crear varios proyectos
- estás organizando carpetas
- quieres navegar entre mini-proyectos

Abre directamente la carpeta de un proyecto si:

- vas a trabajar varias horas en uno solo
- quieres menos ruido visual
- quieres que VS Code se centre en un único `.venv`

## Notas

- Python está instalado en este equipo.
- VS Code ya tiene soporte Python, Pylance y Ruff.
- Cada mini-proyecto usa su propio `.venv` para evitar mezclas.
- Si algo no reconoce `python` en una terminal vieja, abre una terminal nueva dentro de VS Code.

