"""Configuración por variables de entorno (las mismas en Windows y en Docker)."""

import os
from dataclasses import dataclass, field
from pathlib import Path

# Lo que se cataloga (y a donde se puede mover lo que llega). Puede ser una subcarpeta:
# de assets solo entra el papercraft y las miniaturas de papel.
AREAS = [
    "RPG", "Wargames", "Magazines", "Juegos de mesa",
    "assets/Papercraft",
    "assets/By Artist or Source/Stephen Joy/Paper Minis",
    "assets/By Artist or Source/Paizo/Pathfinder/Paper Minis",
    "assets/By Artist or Source/Paizo/Pathfinder/Pawns",
    "assets/By Artist or Source/Paizo/Starfinder/Pawns",
]


def en_areas(areas: list[str], rel: str) -> bool:
    """¿Está la ruta (relativa a la raíz) dentro de lo catalogado?"""
    rel = rel.strip("/")
    return any(rel == a or rel.startswith(a + "/") for a in areas)


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
            os.environ.get("BIBLIOTECA_AREAS", ",".join(AREAS))
        )
    )
    # Ruta de red para mostrar en la ficha ("copiar ruta"), p. ej. \\OPENMEDIAVAULT3\Isengard
    ruta_smb: str = field(default_factory=lambda: os.environ.get("BIBLIOTECA_RUTA_SMB", ""))
    procesos: int = field(default_factory=lambda: int(os.environ.get("BIBLIOTECA_PROCESOS", "2")))
    # Carpeta (relativa a la raíz) donde llegan los PDF nuevos para clasificar.
    entrada: str = field(default_factory=lambda: os.environ.get("BIBLIOTECA_ENTRADA", "_Inbox"))
    # Confianza mínima para mover sin preguntar (0 = siempre se revisa a mano).
    auto: float = field(default_factory=lambda: float(os.environ.get("CLASIFICADOR_AUTO", "0.9")))

    @property
    def db(self) -> Path:
        return self.datos / "catalogo.sqlite"

    @property
    def portadas(self) -> Path:
        return self.datos / "portadas"


def cargar() -> Config:
    return Config()
