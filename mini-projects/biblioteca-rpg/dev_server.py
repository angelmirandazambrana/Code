"""Arranque local para desarrollo en Windows (en el servidor se usa Docker)."""

import os
import sys
from pathlib import Path

AQUI = Path(__file__).resolve().parent
os.environ.setdefault("BIBLIOTECA_RAIZ", "V:/")
os.environ.setdefault("BIBLIOTECA_DATOS", str(AQUI / "datos"))
os.environ.setdefault("BIBLIOTECA_RUTA_SMB", r"\OPENMEDIAVAULT3\Isengard")
sys.path.insert(0, str(AQUI / "src"))

import uvicorn  # noqa: E402

if __name__ == "__main__":
    uvicorn.run("biblioteca_rpg.web:app", host="127.0.0.1", port=int(os.environ.get("PUERTO", "8090")))
