"""Reorganización al esquema nuevo de carpetas (docs/esquema_carpetas.md), juego a juego.

    python -m biblioteca_rpg.reorganizar ensayo warhammer40k --csv /datos/esquema_40k.csv

Por ahora solo propone: para cada documento del juego, su carpeta nueva y la regla que la decide.
La tabla se revisa a mano antes de mover nada.
"""

import argparse
import csv
import json
import os
import re
import sqlite3
import sys
import time
from collections import Counter
from pathlib import PurePosixPath

from . import config as config_mod
from .db import conectar

MIN_SERIE = 5  # una serie o producto tiene subcarpeta propia solo con 5 ficheros o más
POR_CLASIFICAR = "_Por clasificar"
CUALQUIERA = "Any Edition"

# Idioma de un fichero traducido, por su nombre -> carpeta de idioma junto al original.
IDIOMAS = [
    (re.compile(r"\b(castellano|espa[nñ]ol|edici[oó]n|reglas|b[aá]sicas?|\[es\]|\(sp\)|\(es\))", re.I), "Spanish"),
    (re.compile(r"\b(fran[cç]ais|\[fr\]|\(fr\)|r[eè]gles)\b", re.I), "French"),
    (re.compile(r"\b(deutsch|\[de\]|\(de\)|regelbuch)\b", re.I), "German"),
    (re.compile(r"\b(italiano|\[ita?\]|\(ita?\)|regolamento)\b", re.I), "Italian"),
]

# Tipo por palabras de la carpeta (primero) o del nombre del fichero. Orden: gana la primera.
TIPOS_WARGAMES = [
    (r"homebrew|unoffic|3rd party|custom|fan made|narrative games", "Fan & Homebrew"),
    (r"novel|antholog|short stor|comic|black library", "Fiction"),
    (r"background|lore|collector'?s guide|index astartes|realm of chaos", "Background"),
    (r"faq|errata|actualizaci[oó]n|update", "Errata & FAQ"),
    (r"codex|codices|index|army|armies|legends", "Army Books"),
    (r"campaign|campa[nñ]a|war ?zone|mission|psychic awakening|vigilus|flashpoint|imperium nihilus|crusade - book", "Scenarios & Campaigns"),
    (r"dataslate|supplement|expansion|forge ?world|imperial armour|apocalypse|wargear|vehicle manual|dark millen+ium|boarding actions|freebooterz", "Supplements"),
    (r"tools|extras|cards|templates|tokens|counters", "Game Aids"),
    (r"rule ?book|rules|reglamento|core|battle manual|compendium|points|chapter approved|cap[ií]tulo aprobado|grand tournament|- rogue trader$|basic", "Rules"),
]
TIPOS_WARGAMES = [(re.compile(p, re.I), t) for p, t in TIPOS_WARGAMES]

# --- Juegos ---------------------------------------------------------------------------

JUEGOS = {
    "warhammer40k": {
        "origen": "Wargames/Miniaturas/Games Workshop/Warhammer 40K",
        "destino": "Wargames/Games Workshop/Warhammer 40K",
        "editorial": "Games Workshop",
        "ediciones": {1: 1987, 2: 1993, 3: 1998, 4: 2004, 5: 2008, 6: 2012, 7: 2014, 8: 2017, 9: 2020, 10: 2023},
        "alias_edicion": {"rogue trader": 1},
        # Ejércitos del juego: un libro con su nombre y sin otro tipo es un codex / army book.
        "ejercitos": r"\b(eldar|orks?|imperial guard|astra militarum|tyranids?|ultramarines|chaos|assassins|sisters of battle|"
                     r"adepta sororitas|angels of death|space marines?|adeptus|necrons?|t'?au|dark angels|blood angels|"
                     r"space wolves|grey knights|craftworlds?|drukhari|dark eldar|harlequins|genestealer|imperial knights|"
                     r"death guard|thousand sons|custodes|mechanicus|squats|witch ?hunters|daemon ?hunters|catachan|"
                     r"deathwatch|black templars|inquisition|ynnari|leagues of votann)\b",
        # (patrón sobre la ruta relativa, tipo, serie): reglas que mandan sobre las genéricas.
        "reglas": [
            # Novelas, trasfondo y las series con sus propias ediciones: sin edición del juego.
            (r"^Black Library/Novels/Necromunda", "Fiction", None, "Wargames/Games Workshop/Necromunda", True),
            (r"^Black Library/Novels/(?P<serie>[^/]+)", "Fiction", "{serie}", None, True),
            (r"^Black Library/Comics", "Fiction", "Comics", None, True),
            (r"^Black Library/", "Background", None, None, True),
            (r"^Forge World/Horus Heresy", "Supplements", "Horus Heresy", None, True),
            (r"^(Forge World/)?Imperial Armour", "Supplements", "Imperial Armour", None, True),
            (r"/2\) Campaigns[^/]*/Psychic Awakening", "Scenarios & Campaigns", "Psychic Awakening", None, False),
            (r"/02\) Campaigns[^/]*/Psychic Awakening", "Scenarios & Campaigns", "Psychic Awakening", None, False),
            (r"/Codex & Index/Legends", "Army Books", "Legends", None, False),
        ],
    },
}


def edicion_de(texto: str, juego: dict) -> int | None:
    """Número de edición en una carpeta o nombre: "8e (2017)", "5th Edition", "(9e)", "6E", "9 Edicion", "Rogue Trader"."""
    t = texto.lower()
    for alias, n in juego.get("alias_edicion", {}).items():
        if alias in t:
            return n
    m = (re.search(r"(?<![\w.])(\d{1,2})\s*(?:st|nd|rd|th)?\s*(?:e|ed|edition|edici[oó]n)(?![a-z])", t)
         or re.search(r"\b(\d{1,2})(?:st|nd|rd|th) edition", t))
    if m and int(m.group(1)) in juego["ediciones"]:
        return int(m.group(1))
    return None


def nombre_edicion(n: int | None, juego: dict) -> str:
    return f"{n}e ({juego['editorial']}, {juego['ediciones'][n]})" if n else CUALQUIERA


def tipo_de(texto: str) -> str | None:
    for patron, tipo in TIPOS_WARGAMES:
        if patron.search(texto):
            return tipo
    return None


def idioma_de(nombre: str) -> str | None:
    for patron, idioma in IDIOMAS:
        if patron.search(nombre):
            return idioma
    return None


def proponer(relativa: str, nombre: str, juego: dict) -> tuple[str, str]:
    """(carpeta nueva, regla) para un fichero en `origen/relativa`."""
    base = juego["destino"]
    tipo = serie = None
    regla = ""
    sin_edicion = False
    texto_nombre = re.sub(r"\.[a-z0-9]{2,4}$", "", nombre.replace("_", " "), flags=re.I)
    for patron, t, s, otro_juego, sin_ed in juego["reglas"]:
        m = re.search(patron, relativa)
        if m:
            tipo, regla, sin_edicion = t, f"regla «{patron}»", sin_ed
            serie = s.format(**m.groupdict()) if s else None
            base = otro_juego or base
            break
    # Edición: la de la carpeta; si no la dice, la del nombre del fichero.
    n = None if sin_edicion else (edicion_de(relativa, juego) or edicion_de(texto_nombre, juego))
    if not tipo:
        tipo = tipo_de(relativa)
        regla = "tipo por la carpeta" if tipo else ""
        # Un libro completo guardado en la carpeta de FAQ (p. ej. un codex en castellano): manda el nombre.
        # Las erratas oficiales se llaman como el codex ("CODEX - DRUKHARI v1-2"): solo los traducidos.
        if tipo == "Errata & FAQ" and idioma_de(nombre) and not re.search(r"faq|errata|actualizaci|update", texto_nombre, re.I):
            tipo_nombre = tipo_de(texto_nombre)
            if tipo_nombre:
                tipo, regla = tipo_nombre, "tipo por el nombre (libro traducido, no es una FAQ)"
    if not tipo:
        tipo = tipo_de(texto_nombre)
        regla = "tipo por el nombre" if tipo else ""
    if not tipo and juego.get("ejercitos") and re.search(juego["ejercitos"], texto_nombre, re.I):
        tipo, regla = "Army Books", "ejército en el nombre"
    regla = regla or "sin tipo claro"
    partes = [base, nombre_edicion(n, juego), tipo or POR_CLASIFICAR]
    if serie:
        partes.append(serie)
    idioma = idioma_de(nombre)
    if idioma:
        partes.append(idioma)
    return "/".join(partes), regla + (f", edición {n}" if n else ", sin edición") + (f", {idioma}" if idioma else "")


def ensayo(con: sqlite3.Connection, clave: str, salida: str) -> None:
    juego = JUEGOS[clave]
    origen = juego["origen"]
    filas = con.execute("SELECT ruta, carpeta, nombre FROM docs WHERE carpeta = ? OR carpeta LIKE ? ORDER BY ruta",
                        (origen, origen + "/%")).fetchall()
    propuestas = []
    for r in filas:
        relativa = r["carpeta"][len(origen) + 1:] if r["carpeta"] != origen else ""
        nueva, regla = proponer(relativa, r["nombre"], juego)
        propuestas.append([r["ruta"], f"{nueva}/{r['nombre']}", nueva, regla])
    # Series con pocos ficheros: sin subcarpeta propia (van directamente a su tipo).
    cuenta = Counter(p[2] for p in propuestas)
    for p in propuestas:
        partes = PurePosixPath(p[2]).parts
        idioma = partes[-1] if partes[-1] in {i for _, i in IDIOMAS} else None
        sin_idioma = "/".join(partes[:-1]) if idioma else p[2]
        ultimo = sin_idioma.rsplit("/", 1)[-1]
        es_serie = ultimo not in {t for _, t in TIPOS_WARGAMES} | {POR_CLASIFICAR}
        total_serie = sum(n for c, n in cuenta.items() if c == sin_idioma or c.startswith(sin_idioma + "/"))
        if es_serie and total_serie < MIN_SERIE:
            sin_idioma = sin_idioma.rsplit("/", 1)[0]
            p[2] = sin_idioma + (f"/{idioma}" if idioma else "")
            p[1] = f"{p[2]}/{PurePosixPath(p[1]).name}"
            p[3] += ", serie pequeña: sin subcarpeta"
    with open(salida, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(["Origen", "Destino", "Carpeta nueva", "Regla"])
        w.writerows(propuestas)
    final = Counter(p[2][len(juego["destino"]) + 1:] if p[2].startswith(juego["destino"]) else p[2] for p in propuestas)
    print(f"{len(propuestas)} ficheros de {len({r['carpeta'] for r in filas})} carpetas -> {len(final)} carpetas")
    for c, n in sorted(final.items()):
        print(f"  {n:5}  {c}")
    print(f"Sin tipo claro (a {POR_CLASIFICAR}): {sum(POR_CLASIFICAR in p[2] for p in propuestas)}")
    print(f"Tabla: {salida}")


# --- Tipo con Claude (Haiku) para lo que no lo tiene claro ---------------------------------

def tipos_con_claude(con, cfg, tabla_csv: str, tipos: list[str]) -> None:
    """Para las filas que van a _Por clasificar, Claude Haiku mira la portada y las primeras páginas
    y elige el tipo de la lista; solo se cambia si lo tiene claro. La tabla se reescribe."""
    import anthropic

    from .indexador import ruta_portada
    from .llm import PRECIOS, peticion, texto_inicio
    with open(tabla_csv, encoding="utf-8-sig", newline="") as f:
        filas = list(csv.reader(f, delimiter=";"))
    cliente = anthropic.Anthropic()
    modelo = "claude-haiku-5-5"
    esquema = {"type": "object", "additionalProperties": False, "required": ["tipo", "seguridad", "motivo"],
               "properties": {"tipo": {"type": "string", "enum": tipos},
                              "seguridad": {"type": "string", "enum": ["alta", "media", "baja"]},
                              "motivo": {"type": "string"}}}
    sistema = ("Clasificas libros de Warhammer 40K de una biblioteca. Mira la portada y las primeras páginas y di "
               "qué tipo de libro es: " + ", ".join(tipos) + ". 'Army Books' son codex y listas de ejército; "
               "'Supplements' expansiones de reglas; 'Scenarios & Campaigns' campañas y misiones. "
               "Seguridad 'alta' solo si lo tienes claro.")
    gasto = cambios = 0
    for fila in filas[1:]:
        if POR_CLASIFICAR not in fila[2]:
            continue
        d = con.execute("SELECT id, nombre FROM docs WHERE ruta=?", (fila[0],)).fetchone()
        if not d:
            continue
        contenido = peticion(con, d["nombre"], ruta_portada(cfg.portadas, d["id"]), texto_inicio(cfg.raiz / fila[0]),
                             [], None)
        r = cliente.messages.create(model=modelo, max_tokens=2000, system=sistema,
                                    messages=[{"role": "user", "content": contenido}],
                                    output_config={"effort": "low", "format": {"type": "json_schema", "schema": esquema}})
        pe, ps = PRECIOS[modelo]
        gasto += r.usage.input_tokens / 1e6 * pe + r.usage.output_tokens / 1e6 * ps
        texto = next((b.text for b in r.content if b.type == "text"), "{}")
        try:
            resp = json.loads(texto)
        except ValueError:
            continue
        marca = "  "
        if resp.get("seguridad") == "alta" and resp.get("tipo") in tipos:
            fila[2] = fila[2].replace(POR_CLASIFICAR, resp["tipo"])
            fila[1] = f"{fila[2]}/{d['nombre']}"
            fila[3] += f", tipo por Claude Haiku ({resp.get('motivo', '')[:80]})"
            cambios += 1
            marca = "✔ "
        print(f"{marca}{d['nombre'][:60]}  ->  {resp.get('tipo')} ({resp.get('seguridad')})", flush=True)
    with open(tabla_csv, "w", encoding="utf-8-sig", newline="") as f:
        csv.writer(f, delimiter=";").writerows(filas)
    print(f"Claude Haiku: {cambios} con tipo nuevo, {gasto:.3f} $")


# --- Ejecutar y deshacer ------------------------------------------------------------------
#
# Lo ejecuta el clasificador (el único con escritura en la biblioteca). Nunca sobrescribe: si el
# destino existe, añade " (2)". Las fichas del catálogo se conservan (misma id, portada y datos):
# solo cambian la ruta y lo que se deduce de ella. Cada movimiento queda en un CSV que sirve
# para deshacerlo todo.

def _leer_tabla(ruta: str) -> list[tuple[str, str]]:
    with open(ruta, encoding="utf-8-sig", newline="") as f:
        filas = list(csv.reader(f, delimiter=";"))[1:]
    return [(fila[0], fila[1]) for fila in filas]


def planificar(cfg, tabla: list[tuple[str, str]], origen_base: str,
               mezclada: bool = False, catalogados: set[str] | None = None) -> list[tuple[str, str, str]]:
    """[(clase, origen, destino)] con los libros de la tabla y todo lo demás que hay en sus carpetas.

    - Ficheros sin ficha (jpg, txt, mp3…) junto a libros: van a donde van la mayoría de esos libros.
    - Carpetas sin ningún libro catalogado (un libro escaneado en jpg): se mueven enteras, con su
      nombre, a donde va la mayoría de los libros de la carpeta que las contiene.
    - `mezclada` (una base con muchos juegos, como Wargames/Miniaturas): lo que no tiene libros de la
      tabla en su carpeta ni por encima se queda donde está, en vez de ir al destino más común.
    - `catalogados` (modo estricto, para carpetas donde cada libro es de un juego distinto): todos los
      libros del catálogo que hay en la base. Un fichero sin ficha solo acompaña a los libros de SU
      carpeta, si todos se mueven y al mismo sitio; las carpetas sin libros se quedan.
    """
    plan = [("libro", o, d) for o, d in tabla]
    votos: dict[str, Counter] = {}
    for o, d in tabla:
        votos.setdefault(o.rsplit("/", 1)[0], Counter())[d.rsplit("/", 1)[0]] += 1
    destino_de = {carpeta: c.most_common(1)[0][0] for carpeta, c in votos.items()}
    con_libros = {origen_base}
    for carpeta in votos:  # carpetas con algún libro debajo (en ellas o en subcarpetas)
        partes = carpeta.split("/")
        for i in range(len(partes)):
            con_libros.add("/".join(partes[: i + 1]))
    libros = {o for o, _ in tabla}

    estricta = catalogados is not None
    if estricta:
        en_carpeta = Counter(r.rsplit("/", 1)[0] for r in catalogados)
        destino_de = {c: v.most_common(1)[0][0] for c, v in votos.items()
                      if len(v) == 1 and sum(v.values()) == en_carpeta[c]}

    def destino_cercano(carpeta: str) -> str | None:
        if estricta:
            return destino_de.get(carpeta)
        while carpeta not in destino_de and "/" in carpeta and carpeta != origen_base:
            carpeta = carpeta.rsplit("/", 1)[0]
        if mezclada:
            return destino_de.get(carpeta)
        return destino_de.get(carpeta) or Counter(destino_de.values()).most_common(1)[0][0]

    for d in sorted((cfg.raiz / origen_base).rglob("*")):
        rel = d.relative_to(cfg.raiz).as_posix()
        padre = rel.rsplit("/", 1)[0]
        if padre not in con_libros:
            continue  # va dentro de una carpeta que se mueve entera
        destino = destino_cercano(padre)
        if destino is None:
            continue  # base mezclada: no hay a dónde llevarlo, se queda
        if d.is_dir() and rel not in con_libros:
            if estricta:
                continue
            plan.append(("carpeta", rel, f"{destino}/{d.name}"))
        elif d.is_file() and rel not in libros:
            plan.append(("fichero", rel, f"{destino}/{d.name}"))
    return plan


def _mover(cfg, con, clase: str, origen: str, destino: str) -> str:
    from .db import CAMPOS_RUTA
    from .entrada import _libre, _renombrar
    from .rutas import analizar
    final = _libre(cfg.raiz / destino)
    # Tampoco un nombre que ya tenga otra ficha del catálogo (aunque su fichero no esté).
    n = 2
    while con.execute("SELECT 1 FROM docs WHERE ruta=?", (final.relative_to(cfg.raiz).as_posix(),)).fetchone()             and clase == "libro":
        base = cfg.raiz / destino
        final = _libre(base.with_name(f"{base.stem} ({n}){base.suffix}"))
        n += 1
    _renombrar(cfg.raiz / origen, final)
    nuevo = final.relative_to(cfg.raiz).as_posix()
    with con:
        if clase == "libro":
            datos = analizar(nuevo)
            # La editorial que salía de una carpeta que ya no está (D&D 3rd Party/5e/Kobold Press)
            # se conserva: el catálogo la sigue teniendo aunque la ruta ya no la diga.
            asignar = ", ".join("editorial=COALESCE(?, editorial)" if c == "editorial" else f"{c}=?"
                                for c in CAMPOS_RUTA)
            con.execute(f"UPDATE docs SET ruta=?, {asignar} WHERE ruta=?",
                        (nuevo, *[getattr(datos, c) for c in CAMPOS_RUTA], origen))
        # Lo que pasó por la Entrada sigue sabiendo dónde está (para «Corregir»).
        con.execute("UPDATE entrada SET ruta_actual=? WHERE ruta_actual=?", (nuevo, origen))
        if clase == "carpeta":  # lo que llegó dentro de esa carpeta también cambia de sitio
            con.execute("UPDATE entrada SET ruta_actual = ? || substr(ruta_actual, ?) "
                        "WHERE substr(ruta_actual, 1, ?) = ?", (nuevo, len(origen) + 1, len(origen) + 1, origen + "/"))
    return nuevo


def ejecutar(con, cfg, clave: str, tabla_csv: str, de_verdad: bool) -> str | None:
    juego = JUEGOS[clave]
    return ejecutar_tabla(con, cfg, clave, _leer_tabla(tabla_csv), [juego["origen"]], de_verdad)


def ejecutar_tabla(con, cfg, etiqueta: str, tabla: list[tuple[str, str]], bases: list[str],
                   de_verdad: bool, mezclada: bool = False, estricta: bool = False) -> str | None:
    """Mueve los libros de la tabla y lo que los acompaña en cada una de las carpetas `bases`."""
    plan = []
    for base in bases:
        propia = [(o, d) for o, d in tabla if o.startswith(base + "/")]
        if propia:
            catalogados = {r[0] for r in con.execute("SELECT ruta FROM docs WHERE ruta LIKE ?", (base + "/%",))}                 if estricta else None
            plan += planificar(cfg, propia, base, mezclada, catalogados)
    return ejecutar_plan(con, cfg, etiqueta, plan, bases, de_verdad)


def ejecutar_plan(con, cfg, etiqueta: str, plan: list[tuple[str, str, str]], bases: list[str],
                  de_verdad: bool) -> str | None:
    """Ejecuta [(clase, origen, destino)], con registro para deshacer, y quita lo vacío en `bases`."""
    plan = [x for x in plan if x[1] != x[2]]  # lo que ya está en su sitio no se toca
    # Primero las carpetas enteras y los sueltos, después los libros: si un libro entrara antes en
    # una carpeta nueva que se llama como una vieja que luego se mueve entera, se iría con ella.
    plan.sort(key=lambda x: {"carpeta": 0, "fichero": 1, "libro": 2}[x[0]])
    cuenta = Counter(c for c, _, _ in plan)
    print(f"Plan: {cuenta['libro']} libros, {cuenta['fichero']} ficheros sin ficha, "
          f"{cuenta['carpeta']} carpetas enteras")
    if not de_verdad:
        for _, o, d in [x for x in plan if x[0] == "carpeta"][:20]:
            print(f"  carpeta: {o}  ->  {d}")
        print("Ensayo: no se ha movido nada (añade --ejecutar).")
        return None
    registro = cfg.raiz / "_Reorganizacion" / f"esquema_{etiqueta}_{time.strftime('%Y-%m-%d_%H%M')}.csv"
    registro.parent.mkdir(parents=True, exist_ok=True)
    errores = 0
    with open(registro, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(["Fecha", "Accion", "Origen", "Destino", "Nota"])
        for i, (clase, origen, destino) in enumerate(plan, 1):
            ahora = time.strftime("%Y-%m-%d %H:%M:%S")
            try:
                if not (cfg.raiz / origen).exists():
                    w.writerow([ahora, f"Mover {clase}", origen, "", "ya no existe"])
                    continue
                if (destino + "/").startswith(origen + "/"):
                    raise OSError("el destino está dentro del origen")
                nuevo = _mover(cfg, con, clase, origen, destino)
                w.writerow([ahora, f"Mover {clase}", origen, nuevo, "renombrado: ya existía" if nuevo != destino else ""])
            except OSError as ex:
                errores += 1
                w.writerow([ahora, f"Error {clase}", origen, destino, str(ex)[:200]])
            if i % 500 == 0:
                f.flush()
                print(f"  {i}/{len(plan)}", flush=True)
    vacias = sum(_quitar_vacias(cfg.raiz / b) for b in bases)
    print(f"Hecho: {len(plan) - errores} movimientos, {errores} errores, {vacias} carpetas vacías quitadas.")
    print(f"Registro (también para deshacer): {registro}")
    return str(registro)


def _quitar_vacias(raiz) -> int:
    """Quita las carpetas que han quedado vacías (solo vacías: rmdir no borra nada con contenido)."""
    n = 0
    if not raiz.is_dir():
        return 0
    for d in [*sorted((p for p in raiz.rglob("*") if p.is_dir()), key=lambda p: -len(p.parts)), raiz]:
        try:
            os.rmdir(d)
            n += 1
        except OSError:
            pass
    return n


def deshacer(con, cfg, registro: str) -> None:
    """Vuelve a poner cada cosa donde estaba, en orden inverso, según el CSV de una ejecución."""
    with open(registro, encoding="utf-8-sig", newline="") as f:
        filas = list(csv.reader(f, delimiter=";"))[1:]
    hechos = [(r[1].split(" ", 1)[1], r[2], r[3]) for r in filas if r[1].startswith("Mover ") and r[3]]
    errores = 0
    for clase, origen, destino in reversed(hechos):
        try:
            if (cfg.raiz / origen).exists():
                raise FileExistsError(origen)
            _mover(cfg, con, clase, destino, origen)
        except OSError as ex:
            errores += 1
            print(f"  no se pudo deshacer {destino}: {ex}", file=sys.stderr)
    print(f"Deshecho: {len(hechos) - errores} movimientos, {errores} errores")


def main(argv=None) -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("orden", choices=["ensayo", "tipos-claude", "ejecutar", "deshacer"])
    ap.add_argument("juego", choices=list(JUEGOS))
    ap.add_argument("--csv", required=True, help="ensayo: tabla que se escribe; ejecutar: tabla revisada; "
                                                 "deshacer: registro de la ejecución")
    ap.add_argument("--ejecutar", action="store_true", help="ejecutar: mover de verdad (si no, solo el plan)")
    args = ap.parse_args(argv)
    cfg = config_mod.cargar()
    con = conectar(cfg.db)
    if args.orden == "ensayo":
        ensayo(con, args.juego, args.csv)
    elif args.orden == "tipos-claude":
        tipos_con_claude(con, cfg, args.csv, sorted({t for _, t in TIPOS_WARGAMES}))
    elif args.orden == "ejecutar":
        ejecutar(con, cfg, args.juego, args.csv, args.ejecutar)
    else:
        deshacer(con, cfg, args.csv)
    sys.exit(0)


if __name__ == "__main__":
    main()
