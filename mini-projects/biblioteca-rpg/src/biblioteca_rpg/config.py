"""Configuración por variables de entorno (las mismas en Windows y en Docker)."""

import os
from dataclasses import dataclass, field
from pathlib import Path


def _lista(valor: str) -> list[str]:
    return [x.strip() for x in valor.split(",") if x.strip()]


@dataclass(frozen=True)
class Config:
    # Raíz de la biblioteca: V:\ en Windows, /biblioteca dentro del contenedor.
    raiz: Path = field(default_factory=lambda: Path(os.environ.get("BIBLIOTECA_RAIZ", "V:/")))
    # Base de datos y miniaturas. Nunca dentro de la biblioteca.
    datos: Path = field(default_factory=lambda: Path(os.environ.get("BIBLIOTECA_DATOS", "datos")))
    areas: list[str] = field(
        default_factory=lambda: _lista(
            os.environ.get("BIBLIOTECA_AREAS", "RPG,Wargames,Magazines,Juegos de mesa")
        )
    )
    # Ruta de red para mostrar en la ficha ("copiar ruta"), p. ej. \\OPENMEDIAVAULT3\Isengard
    ruta_smb: str = field(default_factory=lambda: os.environ.get("BIBLIOTECA_RUTA_SMB", ""))
    procesos: int = field(default_factory=lambda: int(os.environ.get("BIBLIOTECA_PROCESOS", "2")))

    @property
    def db(self) -> Path:
        return self.datos / "catalogo.sqlite"

    @property
    def portadas(self) -> Path:
        return self.datos / "portadas"


def cargar() -> Config:
    return Config()
