"""Unifica los nombres de una misma editorial ("Mongoose" / "Mongoose Publishing").

La editorial sale de la ruta (carpetas) o, si la ruta no la dice, del PDF o de RPGGeek, y cada sitio
la escribe a su manera. Aquí se agrupan las formas que son la misma editorial y se elige un nombre:

- se quitan restos de la ruta: "d20 4e Goodman Games", "Columbia Games Inc_", "Rogue Genius Games (RGG)";
- se comparan sin mayúsculas, acentos, puntuación ni sufijos de empresa (Publishing, Games, Inc…);
- el nombre del grupo es el de la tabla de alias si está, y si no, la forma con más documentos.

El resultado va a la tabla `editorial_alias` (forma -> nombre) y a la columna `docs.editorial_unif`,
que unos disparadores mantienen al día cuando cambia la editorial de un documento. Los ficheros no se tocan.

    python -m biblioteca_rpg.editoriales            # recalcula y lista los grupos
"""

import re
import sqlite3
import sys
from collections import defaultdict

from . import config as config_mod
from .db import conectar
from .texto_pdf import ALIAS_EDITORIALES, normalizar

# Formas que la comparación automática no junta (abreviaturas, nombres distintos de la misma casa).
ALIAS_EXTRA = {
    "Kobold Press": ["Kobold Press - Open Design", "Kobold Quarterly"],
    "Catalyst": ["Catalyst Game Labs"],
    "Pinnacle Entertainment": ["Pinnacle Entertainment Group"],
    "Fantasy Flight Games": ["Fantasy Flight Games (Legends & Lairs)"],
    "Louis Porter Jr. Design": ["Louis Porter Jr", "LPJ", "LPJ Design"],
    "Two Hour Wargames": ["Two Hour Wargame"],
    "Evil Hat": ["Evil Hat Productions"],
    "Green Ronin": ["Green Ronin Publishing"],
    "Monte Cook Games": ["Monte Cook"],
    "One Page Rules": ["onepagerules"],
}

# Prefijo de algunas carpetas de terceros: "d20 4e Goodman Games".
RE_PREFIJO = re.compile(r"^d20\s+\d(?:\.\d)?e\s+", re.I)
# Abreviatura entre paréntesis al final: "Rogue Genius Games (RGG)".
RE_SIGLAS = re.compile(r"\s*\(([A-Z]{2,6})\)\s*$")
# Cola societaria (el "_" es un "." que no se podía usar en el nombre de la carpeta).
RE_SOCIEDAD = re.compile(r"[,\s]+(?:inc|ltd|llc|gmbh|s\.?l|corp)[._]*$|_+$", re.I)
SUFIJOS = re.compile(
    r"\b(?:publishing|publications?|publishers?|games?|game ?studios?|studios?|inc|ltd|llc|gmbh|co|corp"
    r"|corporation|company|entertainment|productions?|press|books|design|group|the)\b"
)


def limpiar(nombre: str) -> str:
    """Quita los restos de la ruta, sin cambiar el nombre en sí."""
    t = RE_PREFIJO.sub("", nombre.strip())
    t = RE_SIGLAS.sub("", t) or t
    for _ in range(2):  # "Glory Games, Inc_" -> "Glory Games"
        t = RE_SOCIEDAD.sub("", t).strip(" ,") or t
    return t


def clave(nombre: str) -> str:
    """Lo que tienen en común las formas de una misma editorial."""
    t = normalizar(limpiar(nombre)).replace("&", " ")
    t = re.sub(r"[^a-z0-9 ]+", "", t)  # "E.N." -> "EN", "Skortched Urf'" -> "Skortched Urf"
    sin_sufijos = re.sub(r"\s+", "", SUFIJOS.sub(" ", t))
    return sin_sufijos or re.sub(r"\s+", "", t)


def _canonicos() -> dict[str, str]:
    """clave -> nombre elegido a mano."""
    res = {}
    for tabla in (ALIAS_EDITORIALES, ALIAS_EXTRA):
        for canonico, formas in tabla.items():
            for f in [canonico, *formas]:
                res[clave(f)] = canonico
    return res


def agrupar(cuentas: dict[str, int]) -> dict[str, str]:
    """{forma: nº de documentos} -> {forma: nombre unificado} (solo las formas que cambian)."""
    grupos: dict[str, list[str]] = defaultdict(list)
    for forma in cuentas:
        grupos[clave(forma)].append(forma)
    manuales = _canonicos()
    res = {}
    for k, formas in grupos.items():
        # Formas limpias; las que solo cambian en mayúsculas suman juntas ("Petersen games" + "Petersen Games").
        por_texto: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
        for f in formas:
            por_texto[limpiar(f).lower()][limpiar(f)] += cuentas[f]

        def bien_escrita(variantes: dict[str, int]) -> str:
            # La que tiene más palabras con mayúscula inicial; a igualdad, la más usada.
            return max(variantes, key=lambda x: (sum(w[:1].isupper() for w in x.split()), variantes[x]))

        elegido = manuales.get(k) or bien_escrita(
            max(por_texto.values(), key=lambda v: (sum(v.values()), -len(next(iter(v))))))
        for f in formas:
            if f != elegido:
                res[f] = elegido
    return res


def recalcular(con: sqlite3.Connection) -> int:
    """Rehace `editorial_alias` y `docs.editorial_unif`. Devuelve cuántas formas se unifican."""
    cuentas = {r[0]: r[1] for r in con.execute(
        "SELECT COALESCE(editorial, ext_editorial) AS v, COUNT(*) FROM docs WHERE v IS NOT NULL GROUP BY v")}
    alias = agrupar(cuentas)
    with con:
        con.execute("DELETE FROM editorial_alias")
        con.executemany("INSERT INTO editorial_alias(forma, nombre) VALUES (?, ?)", alias.items())
        con.execute(f"UPDATE docs SET editorial_unif = {EXPR_UNIF} "
                    f"WHERE editorial_unif IS NOT {EXPR_UNIF}")
    return len(alias)


# La editorial del documento, ya unificada (la usan los disparadores de db.py y recalcular).
_BASE = "COALESCE({p}editorial, {p}ext_editorial)"
EXPR_UNIF = (f"COALESCE((SELECT nombre FROM editorial_alias WHERE forma = {_BASE.format(p='')}), "
             f"{_BASE.format(p='')})")


def main() -> None:
    cfg = config_mod.cargar()
    con = conectar(cfg.db)
    n = recalcular(con)
    grupos: dict[str, list[str]] = defaultdict(list)
    for forma, nombre in con.execute("SELECT forma, nombre FROM editorial_alias ORDER BY nombre, forma"):
        grupos[nombre].append(forma)
    for nombre, formas in grupos.items():
        print(f"{nombre}  <-  {' | '.join(formas)}")
    print(f"{n} formas unificadas en {len(grupos)} editoriales", file=sys.stderr)


if __name__ == "__main__":
    main()
