"""Las carpetas de assets (packs de mapas, tokens, música, módulos de Foundry…) para el clasificador.

Lo de assets casi nunca es un PDF: son carpetas con imágenes o audio, así que el catálogo de
documentos no las ve. Aquí se guarda el nombre de cada carpeta de assets para que, cuando llegue
un pack sin PDF ("Heroic Maps - Langstone Castle"), se compare con los que ya hay y se proponga
dejarlo junto a ellos (assets/By Artist or Source/Heroic Maps).
"""

import os
import sqlite3
from pathlib import Path

from .clasificador import VECINOS, _valida, palabras

RAIZ = "assets"
PROFUNDIDAD = 5  # assets/By Artist or Source/<autor>/<pack>/<subcarpeta>

ESQUEMA = """
CREATE TABLE IF NOT EXISTS packs (
    id INTEGER PRIMARY KEY,
    ruta TEXT UNIQUE NOT NULL,   -- assets/By Artist or Source/Heroic Maps/Langstone
    nombre TEXT NOT NULL,        -- Langstone
    padre TEXT NOT NULL          -- assets/By Artist or Source/Heroic Maps
);
CREATE VIRTUAL TABLE IF NOT EXISTS packs_fts USING fts5(nombre, padre, tokenize='unicode61 remove_diacritics 2');
"""


def preparar(con: sqlite3.Connection) -> None:
    con.executescript(ESQUEMA)


def _carpetas(raiz: Path, base: str):
    """Rutas relativas (con '/') de las carpetas bajo `base`, hasta PROFUNDIDAD niveles desde assets."""
    pila = [raiz / base]
    while pila:
        d = pila.pop()
        try:
            with os.scandir(d) as it:
                for e in it:
                    if not e.is_dir(follow_symlinks=False) or e.name.startswith((".", "@", "_")):
                        continue
                    rel = Path(e.path).relative_to(raiz).as_posix()
                    yield rel
                    if rel.count("/") < PROFUNDIDAD:
                        pila.append(Path(e.path))
        except OSError:
            continue


def indexar(con: sqlite3.Connection, cfg, raices: list[str] | None = None) -> int:
    """Rehace el índice de las carpetas bajo `raices` (por defecto, todo assets). Devuelve cuántas hay."""
    preparar(con)
    raices = [r.strip("/") for r in (raices or [RAIZ]) if (r.strip("/") + "/").startswith(RAIZ + "/")]
    n = 0
    for base in raices:
        if not (cfg.raiz / base).is_dir():
            continue
        filas = [(rel, rel.rsplit("/", 1)[1], rel.rsplit("/", 1)[0]) for rel in _carpetas(cfg.raiz, base)
                 if rel.count("/") >= 2 and _valida(rel)]  # assets/<tipo>/<algo> en adelante
        with con:
            viejos = [r[0] for r in con.execute("SELECT id FROM packs WHERE ruta = ? OR ruta LIKE ?",
                                                (base, base + "/%"))]
            con.executemany("DELETE FROM packs_fts WHERE rowid = ?", [(i,) for i in viejos])
            con.execute("DELETE FROM packs WHERE ruta = ? OR ruta LIKE ?", (base, base + "/%"))
            for rel, nombre, padre in filas:
                cur = con.execute("INSERT INTO packs (ruta, nombre, padre) VALUES (?, ?, ?)", (rel, nombre, padre))
                con.execute("INSERT INTO packs_fts (rowid, nombre, padre) VALUES (?, ?, ?)",
                            (cur.lastrowid, nombre, padre))
        n += len(filas)
    return n


def proponer(con: sqlite3.Connection, texto: str) -> dict:
    """Como clasificador.proponer, pero comparando con los packs de assets: vota por la carpeta
    que contiene a los packs parecidos (donde iría este, a su lado)."""
    vacio = {"propuesta": None, "confianza": 0.0, "apoyos": 0, "alternativas": []}
    ps = palabras(texto)
    if not ps:
        return vacio
    try:
        filas = con.execute(
            "SELECT p.padre, bm25(packs_fts, 10.0, 3.0) AS r FROM packs_fts JOIN packs p ON p.id = packs_fts.rowid "
            "WHERE packs_fts MATCH ? ORDER BY r LIMIT ?", (" OR ".join(f'"{p}"' for p in ps), VECINOS)).fetchall()
    except sqlite3.OperationalError:  # aún no hay índice de packs
        return vacio
    if not filas:
        return vacio
    mejor = filas[0][1]
    votos: dict[str, float] = {}
    apoyos: dict[str, int] = {}
    for padre, r in filas:
        peso = (r / mejor) ** 3 if mejor < 0 else 1.0
        votos[padre] = votos.get(padre, 0) + peso
        apoyos[padre] = apoyos.get(padre, 0) + 1
    total = sum(votos.values())
    orden = sorted(votos.items(), key=lambda x: -x[1])
    return {"propuesta": orden[0][0], "confianza": round(orden[0][1] / total, 3), "apoyos": apoyos[orden[0][0]],
            "alternativas": [[c, round(v / total * 100)] for c, v in orden[:5]]}
