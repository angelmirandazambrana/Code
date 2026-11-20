"""Reorganización por oleadas de toda la biblioteca al esquema nuevo (docs/esquema_carpetas.md).

    python -m biblioteca_rpg.oleadas ensayo A --csv /datos/oleada_A.csv     # propone (no mueve)
    python -m biblioteca_rpg.oleadas ejecutar A --csv /datos/oleada_A.csv   # mueve según la tabla

Oleada A: los juegos de rol «normales» (RPG/<Juego>), cada uno en su sitio:
    RPG/<Juego>/[<Edición>]/<Type>/[<Serie>]/[<Idioma>]/fichero

- La edición es la carpeta de edición que ya tenga ("5e (Catalyst, 2013)"); en los juegos sin
  ediciones no hay ese nivel. Una carpeta que agrupa una línea del juego antes del tipo
  ("Star Wars/WEG d6/Adventures") ocupa el sitio de la edición.
- El tipo sale de las carpetas o del nombre; si no se sabe: "_Por clasificar" (luego lo tipa
  Claude Haiku por lotes).
- Lo que no es un libro catalogado (jpg, txt, carpetas de escaneos…) va con sus libros.
"""

import argparse
import csv
import re
import sqlite3
import sys
from collections import Counter
from pathlib import PurePosixPath

from . import config as config_mod
from .db import conectar
from .reorganizar import IDIOMAS, MIN_SERIE, POR_CLASIFICAR, ejecutar_tabla, idioma_de
from .rutas import RE_EDICION, RE_EDICION_NUMERADA

CUALQUIERA = "Any Edition"

# Tipos de rol (orden: gana el primero que encaje).
TIPOS_ROL = [(re.compile(p, re.I), t) for p, t in [
    (r"adventure ?league|\bsociety\b|\bliving [a-z]|organi[sz]ed play|\bddal|\bpfs\b|convention created",
     "Organized Play"),
    (r"homebrew|\bfan\b|fan made|fan created|unofficial|netbook", "Fan & Homebrew"),
    (r"novel|novela|fiction|short stor|comic|antholog|relatos", "Fiction"),
    ((r"character sheet|\bsheets?\b|\bscreen\b|pantalla|fichas|\bcards?\b|handouts?|\btokens?\b|\bmaps?\b|mapas"
     r"|record sheets?|play aids?|ayudas|\btiles?\b|worksheet"), "Game Aids"),
    # "DCC Adventures & Settings": sobre todo aventuras.
    (r"adventures? (&|and|y) settings?", "Adventures"),
    (r"campaign setting|settings?\b|ambientaci[oó]n|gazetteer|lost omens", "Settings"),
    ((r"adventures?|aventuras?|\bmodules?\b|m[oó]dulos?|scenarios?|escenarios?|campaigns?|campa[nñ]as?|one[ -]?shots?"
     r"|adventure paths?|\bquests?\b|\badv\b|\(lvl"), "Adventures"),
    ((r"supplements?|suplementos?|sourcebooks?|bestiar|monsters?|creatures|compendium|options|equipment|\bgear\b"
     r"|spells?|magic|\bclasses\b|expansions?|expansi[oó]n|companion|guide to|handbook of|player'?s options"),
     "Supplements"),
    ((r"core|rule ?books?|\brules\b|player'?s? ?hand ?book|game ?master'?s? guide|dungeon master|starter|quick ?start"
     r"|basic set|reglas|reglamento|b[aá]sico|manual b|\bsrd\b"), "Core Rules"),
    (r"background|\blore\b|\bhistory\b|art ?book|\bartbook", "Background"),
    (r"^_?varios$|^_?misc|miscellan|^general|^extras?$|^otros$|^other", "Misc"),
]]
TIPOS = {t for _, t in TIPOS_ROL}

# Carpetas de RPG que NO son un juego de la oleada A (se tratan en otras oleadas).
FUERA_DE_A = {
    "D&D", "D&D Third Party", "Paizo", "OSR(ic)", "DrivethroughRPG", "Necromancer - Frog God Games",
    "Fate", "Powered by the Apocalypse", "World of Darkness", "Anime & Cartoon RPGs", "000000 Archivist Thread",
}


def es_edicion(seg: str) -> bool:
    return seg == CUALQUIERA or bool(RE_EDICION.match(seg) or RE_EDICION_NUMERADA.match(seg))


def tipo_de(texto: str) -> str | None:
    t = texto.replace("_", " ").strip()  # "_Varios" -> "Varios"
    for patron, tipo in TIPOS_ROL:
        if patron.search(t):
            return tipo
    return None


def limpiar(seg: str) -> str:
    """'01) Rulebooks & Missions' -> 'Rulebooks & Missions'; '_Fan y homebrew' -> 'Fan y homebrew'."""
    # Solo números cortos de orden: "1940-1943 North Africa" no pierde el año.
    return re.sub(r"^\s*(\d{1,3}[\).\s-]+|_+)\s*", "", seg).strip() or seg


def proponer_rol(juego: str, relativa: str, nombre: str, raiz: str = "RPG", tipo_de=tipo_de) -> tuple[str, str]:
    """(carpeta nueva, regla) para un fichero en <raiz>/<juego>/<relativa>. Con `tipo_de` = tipo_war
    sirve para los wargames."""
    segs = [s for s in relativa.split("/") if s] if relativa else []
    nivel2 = None
    resto = segs
    regla = []
    e = next((i for i, s in enumerate(segs) if es_edicion(s)), None)
    pistas = []
    if e is not None:
        nivel2, pistas, resto = segs[e], segs[:e], segs[e + 1:]
        # "Middle-earth/MERP/1st Edition": la línea antes de la edición se conserva delante.
        linea_previa = [limpiar(x) for x in pistas if not tipo_de(x)]
        if linea_previa:
            nivel2 = "/".join([*linea_previa, nivel2])
        regla.append("edición de la carpeta")
    tipo = serie = None
    # El tipo: primero en las carpetas (las de antes de la edición cuentan: "D&D/Adventures/5e"),
    # luego en el nombre del fichero. "Misc" solo si no aparece nada más concreto.
    texto_nombre = re.sub(r"\.[a-z0-9]{2,4}$", "", nombre, flags=re.I)
    misc = None
    for i, s in enumerate(resto):
        t = tipo_de(s)
        if t == "Misc":
            misc = misc or i
            continue
        if t:
            tipo = t
            linea = [x for x in resto[:i] if tipo_de(x) != "Misc"]
            if linea and nivel2 is None:
                nivel2 = limpiar(linea[0])  # una línea del juego antes del tipo
            serie = next((limpiar(x) for x in resto[i + 1:] if not tipo_de(x)), None)
            if t == "Organized Play":
                # Programa y temporada: "Adventure League/S9 - …", "Society/Season 07", "Living Greyhawk/Modules".
                programa = [] if re.fullmatch(r"organi[sz]ed play", limpiar(s), re.I) else [limpiar(s)]
                serie = "/".join((programa + [limpiar(x) for x in resto[i + 1:]])[:3]) or None
            elif t == "Fan & Homebrew" and resto[i + 1:]:
                serie = limpiar(resto[i + 1])  # "Homebrew/00. Adventures" -> Fan & Homebrew/Adventures
            regla.append(f"tipo por la carpeta «{s}»")
            break
    if not tipo:
        for s in pistas:
            tipo = tipo_de(s)
            if tipo and tipo != "Misc":
                regla.append(f"tipo por la carpeta «{s}»")
                serie = next((limpiar(x) for x in resto if not tipo_de(x)), None)
                break
            tipo = None
    otras = [limpiar(x) for x in resto if not tipo_de(x)]
    if not tipo and otras and nivel2 is None:
        # Sin tipo en las carpetas: la primera carpeta es una línea o ambientación del juego
        # ("Star Wars/SWD6", "Savage Worlds/Sundered Skies") y ocupa el sitio de la edición.
        nivel2, otras = otras[0], otras[1:]
    if not tipo:
        tipo = tipo_de(texto_nombre)
        if tipo:
            regla.append("tipo por el nombre")
            serie = otras[0] if otras else None
    if not tipo and misc is not None:
        tipo, serie = "Misc", (otras[0] if otras else None)
        regla.append("carpeta de varios")
    if not tipo:
        tipo = POR_CLASIFICAR
        serie = otras[0] if otras else None  # se conserva la agrupación que tenía
        regla.append("sin tipo claro")
    partes = [f"{raiz}/{juego}"] + ([nivel2] if nivel2 else []) + [tipo] + ([serie] if serie else [])
    idioma = idioma_de(nombre)
    if idioma and not re.search(r"\[(es|fr|de|it)\]", juego, re.I):  # un juego ya en ese idioma no lleva carpeta
        partes.append(idioma)
        regla.append(idioma)
    return "/".join(partes), ", ".join(regla)


# --- Oleada B: familias -> un juego por línea, con la familia como etiqueta ----------------

FAMILIAS = {"Fate": "Fate", "Powered by the Apocalypse": "PbtA", "OSR(ic)": "OSR",
            "World of Darkness": "World of Darkness", "Anime & Cartoon RPGs": "Anime & Cartoon"}
NOMBRES = {
    "2. FATE Core (FATE)": "Fate Core", "1. FUDGE": "Fudge", "3. FATE Accelerated (FAE)": "Fate Accelerated",
    "Fate Hacks & Homebrews": "Fate Core", "S+W": "Swords & Wizardry", "S+W White Box": "Swords & Wizardry White Box",
    "[1e Compatible, OSRIC]": "OSRIC", "[Basic Compatible, BX]": "B-X Compatible",
    "[0e, Original Edition Compatible]": "OD&D Compatible", "[OSR Generic]": "OSR Generic",
    "[Adventure Design & Dungeoncraft]": "OSR Generic",
    "VtM (WoD)": "Vampire - The Masquerade", "WtA (WoD)": "Werewolf - The Apocalypse", "MtA (WoD)": "Mage - The Ascension",
    "DAV (WoD)": "Vampire - The Dark Ages", "CtD (WoD)": "Changeling - The Dreaming", "WtO (WoD)": "Wraith - The Oblivion",
    "HtR (WoD)": "Hunter - The Reckoning", "DtF (WoD)": "Demon - The Fallen", "KotE (WoD)": "Kindred of the East",
    "Orpheus (WoD)": "Orpheus", "MtR (WoD)": "Mummy - The Resurrection", "VtR (WoD 2.0)": "Vampire - The Requiem",
    "MtAw (WoD 2.0)": "Mage - The Awakening", "HtV (WoD 2.0)": "Hunter - The Vigil",
    "WtF (WoD 2.0)": "Werewolf - The Forsaken", "CtL (WoD 2.0)": "Changeling - The Lost",
    "PtC (WoD 2.0)": "Promethean - The Created", "DtD (Demon the Descent)": "Demon - The Descent",
}


def _nombre_juego(seg: str) -> str:
    if seg in NOMBRES:
        return NOMBRES[seg]
    t = re.sub(r"^\s*\d+[\).]\s*", "", seg).strip("[] ")
    return t.split(";")[0].strip() or seg


def juego_de_familia(familia: str, partes: list[str]) -> tuple[str, list[str]]:
    """(juego, carpetas por debajo) para un libro en RPG/<familia>/<partes...>."""
    if not partes:
        return familia, []
    if familia == "World of Darkness":
        linea = {"Classic": "Classic", "Chronicles of Darkness": "Chronicles of Darkness"}.get(partes[0])
        if linea and len(partes) > 1 and not partes[1].startswith("_"):
            return _nombre_juego(partes[1]), partes[2:]
        return "World of Darkness", ([linea] if linea else []) + partes[1 if linea else 0:]
    if familia == "Anime & Cartoon RPGs":
        grupo = partes[0]
        if grupo.startswith(("1)", "2)")) and len(partes) > 1:
            return _nombre_juego(partes[1]), partes[2:]
        if grupo.startswith("3)") and len(partes) > 1:
            return "Anime & Cartoon", partes[1:]  # el tema hace de línea
        return "Anime & Cartoon", ["Supplements"] + partes[1:]
    extra = ["Homebrew"] if partes[0] == "Fate Hacks & Homebrews" else []
    extra += ["Adventure Design & Dungeoncraft"] if partes[0] == "[Adventure Design & Dungeoncraft]" else []
    return _nombre_juego(partes[0]), extra + partes[1:]


def ensayo_familias(con, salida: str) -> list[list[str]]:
    existentes = {r[0].split("/")[1].lower(): r[0].split("/")[1]
                  for r in con.execute("SELECT DISTINCT carpeta FROM docs WHERE carpeta LIKE 'RPG/%'")}
    propuestas = []
    familias_de: dict[str, str] = {}
    for carpeta_familia, familia in FAMILIAS.items():
        base = f"RPG/{carpeta_familia}"
        for r in con.execute("SELECT ruta, carpeta, nombre FROM docs WHERE carpeta = ? OR carpeta LIKE ? ORDER BY ruta",
                             (base, base + "/%")):
            partes = [x for x in r["carpeta"][len(base) + 1:].split("/") if x] if r["carpeta"] != base else []
            juego, debajo = juego_de_familia(carpeta_familia, partes)
            juego = existentes.get(juego.lower(), juego)  # "HackMaster" se junta con "Hackmaster"
            nueva, regla = proponer_rol(juego, "/".join(debajo), r["nombre"])
            propuestas.append([r["ruta"], f"{nueva}/{r['nombre']}", nueva, f"familia {familia}: {regla}"])
            familias_de[juego] = familia
    with con:
        con.execute("CREATE TABLE IF NOT EXISTS familias (juego TEXT PRIMARY KEY, familia TEXT NOT NULL)")
        con.executemany("INSERT OR REPLACE INTO familias VALUES (?, ?)", familias_de.items())
    print(f"Familias: {len(familias_de)} juegos -> " + ", ".join(
        f"{f} {sum(1 for x in familias_de.values() if x == f)}" for f in sorted(set(familias_de.values()))))
    return propuestas


# --- Oleada C: D&D, D&D de terceros, Paizo y Frog God -----------------------------------------

DND_3E = "3e-3.5e (Wizards of the Coast, 2000)"
# Subcarpetas de D&D que dicen el tipo mejor que la carpeta de arriba.
SUSTITUIR_DND = {
    "Unearthed Arcana": "Supplements/Unearthed Arcana", "pre-gens": "Game Aids/Pre-gens",
    "Essentials": "Core Rules/Essentials", "Generic": "", "Core Books": "Core Rules",
    "D&D Encounters": "Organized Play/D&D Encounters", "Worldwide Game Days": "Organized Play/Worldwide Game Days",
    "Lair assault": "Organized Play/Lair Assault", "Convention Specials": "Organized Play/Convention Specials",
}
PF = {"1st Edition": "1e (Paizo, 2009)", "2nd Edition": "2e (Paizo, 2019)",
      "2nd Edition Remaster": "2e - Remaster (Paizo, 2023)"}
PF_3P = {"1st Edition": "1e (2009)", "2nd Edition": "2e (2019)", "2nd Edition Remaster": "2e - Remaster (2023)"}
# Carpetas de D&D Third Party que no son una editorial.
RE_SIN_EDITORIAL = re.compile(r"^_sin editorial$|unknown publisher|^xx various", re.I)
# Frog God: sistema de cada carpeta -> (juego, edición)
FROG = {"3E": ("D&D 3rd Party", "3e-3.5e"), "5E": ("D&D 3rd Party", "5e (2014)"),
        "Pathfinder": ("Pathfinder 3rd Party", "1e (2009)"), "Swords & Wizardry": ("Swords & Wizardry", None),
        "OSE": ("Old-School Essentials", None)}
RAPPAN = {"D&D3E": FROG["3E"], "D&D3.5": FROG["3E"],"Sword&Wizardry": FROG["Swords & Wizardry"], "PF1E": FROG["Pathfinder"],
          "5E": FROG["5E"]}


def _sin_editorial(segs: list[str]) -> list[str]:
    """[edición, editorial, …] -> [edición, …]: la editorial la guarda el catálogo, no la ruta."""
    if len(segs) > 1 and (tipo_de(segs[1]) or limpiar(segs[1]) != segs[1] and not RE_SIN_EDITORIAL.match(segs[1])):
        return segs  # "__ Homebrew", "_Character Sheets", "00. Indexes": no son editoriales
    return segs[:1] + segs[2:]


def ruta_c(base: str, segs: list[str]) -> tuple[str, list[str]]:
    """(juego, carpetas por debajo) para un libro en RPG/<base>/<segs...>."""
    s = list(segs)
    if base == "D&D":
        s = [p for x in s for p in (SUSTITUIR_DND.get(x, x).split("/") if x in SUSTITUIR_DND else [x]) if p]
        if s and s[0] == "Settings":
            if len(s) > 1 and s[1] == "Living Greyhawk":
                return "D&D", [DND_3E, "Organized Play", *s[1:]]
            if not any(es_edicion(x) for x in s):
                return "D&D", [CUALQUIERA, "Settings", *[x for x in s[1:] if x != POR_CLASIFICAR]]
        return "D&D", s
    if base == "D&D Third Party":
        return "D&D 3rd Party", _sin_editorial(s)
    if base == "Paizo":
        if not s:
            return "Pathfinder", []
        if s[0] in PF:
            if len(s) > 1 and s[1] == "3rd Party":
                return "Pathfinder 3rd Party", _sin_editorial([PF_3P[s[0]], *s[2:]])
            return "Pathfinder", [PF[s[0]], *s[1:]]
        if s[0] == "3rd Party":
            return "Pathfinder 3rd Party", _sin_editorial(["", *s[1:]])[1:]
        if s[0] == "Wayfinder":
            return "Pathfinder", [PF["1st Edition"], "Fan & Homebrew", *s]
        if s[0] == "Pawns":
            return "Pathfinder", [PF["1st Edition"], "Game Aids", *s]
        return "Pathfinder", s
    # Necromancer - Frog God Games: va al juego de cada sistema; la editorial queda en el catálogo.
    if s and s[0] in FROG:
        juego, ed = FROG[s[0]]
        return juego, ([ed] if ed else []) + s[1:]
    if len(s) > 2 and s[:2] == ["The Lost Lands", "Rappan Athuk"]:
        sistema = s[2].split(" - ")[-1]
        if sistema in RAPPAN:
            juego, ed = RAPPAN[sistema]
            return juego, ([ed] if ed else []) + ["Adventures", "Rappan Athuk", *s[3:]]
    return "D&D 3rd Party", [CUALQUIERA, *[x for x in s if x != "The Lost Lands"]]


# --- Oleada D: wargames -> Wargames/<Editorial>/<Juego>/[<Edición>]/<Type> -----------------------

TIPOS_WAR = [(re.compile(p, re.I), t) for p, t in [
    (r"homebrew|unoffic|fan made|fan created|netbook", "Fan & Homebrew"),
    (r"novel|novela|fiction|antholog|short stor|comic|relatos", "Fiction"),
    (r"background|\blore\b|\bhistory\b|art ?book|trasfondo", "Background"),
    (r"\bfaq|errata|erratas", "Errata & FAQ"),
    (r"codex|army|armies|army lists?|ej[eé]rcitos?|\blists?\b|faction|forces? of|rosters?|battletomes?|warscrolls?|warbands?|fleets?",
     "Army Books"),
    (r"scenarios?|escenarios?|campaigns?|campa[nñ]as?|missions?|misiones|turning points|battle reports?|\bmodules?\b",
     "Scenarios & Campaigns"),
    ((r"supplements?|suplementos?|expansions?|expansi[oó]n|sourcebooks?|technical readout|\btros?\b|xtros?|\brgis?\b"
      r"|touring the stars|companion|\bvehicles?\b|wargear"), "Supplements"),
    ((r"sheets?|\bcards?\b|cartas|counters?|fichas|tokens?|templates?|\bmaps?\b|mapas|hexpacks?|terrain|tools|"
      r"play aids?|reference|charts?|tables|markers"), "Game Aids"),
    (r"rule ?books?|\brules\b|reglas|reglamento|\bcore\b|quick ?start|starter|basic|b[aá]sico|manual", "Rules"),
    (r"^!?_?misc|miscellan|^_?varios$|^others?$|^otros$|^extras?$|^general", "Misc"),
]]
TIPOS |= {t for _, t in TIPOS_WAR}


def tipo_war(texto: str) -> str | None:
    t = texto.replace("_", " ").strip()
    if limpiar(t) in TIPOS:  # una carpeta que ya se llama como un tipo ("Game Aids", "Army Books")
        return {"Adventures": "Scenarios & Campaigns", "Organized Play": "Scenarios & Campaigns",
                "Core Rules": "Rules", "Settings": "Background"}.get(limpiar(t), limpiar(t))
    for patron, tipo in TIPOS_WAR:
        if patron.search(t):
            return tipo
    return None


FORMATOS = {"Miniaturas", "Tablero"}
# Carpetas que no son un juego ni una editorial: se dejan para el final (como las mezclas de rol).
MIXTAS_WAR = {"_Referencia", "_Pintura y modelismo", "Tables & Charts", "Solo", "Janes", "Misc", "_Reglamentos sueltos"}
# Carpetas que solo agrupan: la siguiente es el juego o la editorial.
GRUPOS_WAR = {"AWG Fantasy", "AWG Sci-Fi", "AWG Homebrews", "Independent games", "scifi games"}
EDITORIALES_WAR = {"Spartan Games", "Warlord Games", "Battlefront", "Privateer Press", "Corvus Belli",
                   "Cheapass Games", "Firelock games", "Baccus games", "Blacklist Games", "Capitan Games",
                   "FASA", "Mantic", "Mayfair Games", "Plastic Soldier", "Steve Jackson Games", "TSR",
                   "West End Games", "WotC", "Alternative Armies", "Ground Zero Games", "Various Publishers"}
ALIAS_EDITORIAL = {"Osprey Gaming": "Osprey Games", "Battlefront": "Battlefront Miniatures", "WotC": "Wizards of the Coast",
                   "Mantic": "Mantic Games", "Plastic Soldier": "Plastic Soldier Company",
                   "Firelock games": "Firelock Games", "Baccus games": "Baccus 6mm", "Various Publishers": "Other Publishers"}
# Editorial de los juegos conocidos (manda sobre la respuesta de Haiku).
EDITORIAL_DE = {
    "BattleTech": "Catalyst Game Labs", "Beyond the Gates of Antares": "Warlord Games", "Impetus": "Dadi & Piombo",
    "Conan (monolith)": "Monolith", "Silent Death": "Iron Crown Enterprises", "Spectre Operations": "Spectre Miniatures",
    "Rangers of Shadow Deep": "Modiphius Entertainment", "Fistful of lead": "Wiley Games",
    "Warlords of Erehwon": "Warlord Games", "7tv": "Crooked Dice", "Chaos Wars": "Ral Partha", "Mercs": "MERCS Miniatures",
    "Speed Freeks (warhammer)": "Games Workshop", "Hordes of the Things": "WRG", "Oathmark": "Osprey Games",
    "All Quiet on the Martian Front": "Alien Dungeon", "Dungeon Degenerates": "Goblinko",
    "Starmada X": "Majestic Twelve Games", "Age of Fantasy": "One Page Rules", "VOR the Maelstrom": "FASA",
    "Warzone": "Target Games", "Chainmail": "TSR", "AT-43": "Rackham",
}
JUEGO_ALIAS = {"Grimdark - Quickstart #1": "Grimdark Future", "Fantasy - Quickstart #1": "Age of Fantasy"}
OTRAS = "Other Publishers"
FICHERO_EDITORIALES = "editoriales_wargames.json"


def juego_war(segs: list[str]) -> tuple[str | None, str | None, list[str]] | None:
    """(editorial, juego, carpetas por debajo) para un libro en Wargames/<segs...>; None = no se toca ahora."""
    from .rutas import EDITORIALES
    s = list(segs)
    if not s or s[0] == "Games Workshop":  # Warhammer 40K y Necromunda ya están en el esquema
        return None
    if s[0] in FORMATOS:
        s = s[1:]
    if s and s[0] in GRUPOS_WAR:
        s = s[1:]
    if not s or s[0] in MIXTAS_WAR or s[0] == "Solo":
        return None
    if s[0] in EDITORIALES | EDITORIALES_WAR:
        return (s[0], s[1], s[2:]) if len(s) > 1 else (s[0], None, [])
    return None, s[0], s[1:]


def editoriales_por_haiku(cfg, juegos: dict[str, list[str]]) -> dict[str, str]:
    """Pregunta a Claude Haiku la editorial de cada juego (solo los nombres: céntimos). Se guarda en /datos."""
    import json
    fichero = cfg.datos / FICHERO_EDITORIALES
    sabidas = json.loads(fichero.read_text()) if fichero.exists() else {}
    faltan = sorted(j for j in juegos if j not in sabidas)
    if faltan:
        import anthropic
        lista = "\n".join(f"- {j} (ejemplos: {'; '.join(juegos[j][:2])})" for j in faltan)
        esquema = {"type": "object", "additionalProperties": False, "required": ["juegos"],
                   "properties": {"juegos": {"type": "array", "items": {
                       "type": "object", "additionalProperties": False, "required": ["juego", "editorial", "seguridad"],
                       "properties": {"juego": {"type": "string"}, "editorial": {"type": "string"},
                                      "seguridad": {"type": "string", "enum": ["alta", "media", "baja"]}}}}}}
        msg = anthropic.Anthropic().messages.create(
            model="claude-haiku-5-5", max_tokens=16000,
            system="Eres experto en wargames y juegos de tablero. Para cada juego di su editorial original "
                   "(nombre corto y habitual, p. ej. 'Osprey Games', 'Avalon Hill', 'Catalyst Game Labs'). "
                   "Si no lo sabes con seguridad, seguridad 'baja'.",
            messages=[{"role": "user", "content": f"Juegos (carpeta y ficheros de ejemplo):\n{lista}"}],
            output_config={"effort": "low", "format": {"type": "json_schema", "schema": esquema}})
        resp = json.loads(next(b.text for b in msg.content if b.type == "text"))
        for r in resp["juegos"]:
            if r["juego"] in juegos:
                sabidas[r["juego"]] = r["editorial"].strip() if r["seguridad"] == "alta" else OTRAS
        for j in faltan:
            sabidas.setdefault(j, OTRAS)
        fichero.write_text(json.dumps(sabidas, ensure_ascii=False, indent=1))
        print(f"Haiku: {len(faltan)} juegos, {msg.usage.input_tokens} + {msg.usage.output_tokens} tokens")
    return sabidas


def ensayo_d(con, cfg) -> list[list[str]]:
    filas = []
    sin_editorial: dict[str, list[str]] = {}
    for r in con.execute("SELECT ruta, carpeta, nombre, editorial FROM docs WHERE carpeta LIKE 'Wargames/%' ORDER BY ruta"):
        segs = [x for x in r["carpeta"].split("/")[1:] if x and x != POR_CLASIFICAR]
        dato = juego_war(segs)
        if dato:
            ed, juego, debajo = dato
            juego = JUEGO_ALIAS.get(juego, juego)
            dato = (ALIAS_EDITORIAL.get(ed, ed) if ed else EDITORIAL_DE.get(juego), juego, debajo)
            filas.append((r, *dato))
            if not dato[0]:
                sin_editorial.setdefault(dato[1], []).append(r["nombre"])
    editoriales = editoriales_por_haiku(cfg, sin_editorial) if sin_editorial else {}
    # Una editorial que el catálogo ya conoce de una carpeta vieja gana a la de Haiku solo si es la misma
    # escrita de otra forma: se unifica con las carpetas de editorial que ya hay.
    conocidas = {e.lower(): e for e in {f[1] for f in filas if f[1]} | {"Games Workshop"}}
    propuestas = []
    for r, ed, juego, debajo in filas:
        ed = ed or editoriales.get(juego, OTRAS)
        ed = conocidas.get(ed.lower(), ed)
        if juego is None:
            nueva, regla = f"Wargames/{ed}/{POR_CLASIFICAR}", "suelto en la carpeta de la editorial"
        else:
            nueva, regla = proponer_rol(f"{ed}/{juego}", "/".join(debajo), r["nombre"], raiz="Wargames",
                                        tipo_de=tipo_war)
        propuestas.append([r["ruta"], f"{nueva}/{r['nombre']}", nueva, f"wargames: {regla}"])
    return propuestas


# --- Oleada E1: las carpetas de consulta de Wargames, juntas en _Reference -------------------------

REFERENCIA = "Wargames/_Reference"
# (carpeta vieja, primera subcarpeta o None para la raíz) -> carpeta nueva
MAPA_REFERENCIA = [
    ("Wargames/_Referencia", "Historia militar", "Military History"),
    ("Wargames/_Referencia", "Featherstone", "Wargaming Classics/Featherstone"),
    ("Wargames/_Referencia", "Charles S Grant", "Wargaming Classics/Charles S Grant"),
    ("Wargames/_Referencia", "Catalogos", "Catalogues"),
    ("Wargames/_Referencia", None, "Military History"),
    ("Wargames/_Pintura y modelismo", "How to painting & modelling", "Painting & Modelling"),
    ("Wargames/_Pintura y modelismo", None, "Painting & Modelling"),
    ("Wargames/Janes", None, "Jane's"),
]


def destino_referencia(carpeta: str) -> str | None:
    for vieja, sub, nueva in MAPA_REFERENCIA:
        if sub and (carpeta == f"{vieja}/{sub}" or carpeta.startswith(f"{vieja}/{sub}/")):
            return f"{REFERENCIA}/{nueva}{carpeta[len(vieja) + len(sub) + 1:]}"
        if sub is None and (carpeta == vieja or carpeta.startswith(vieja + "/")):
            return f"{REFERENCIA}/{nueva}{carpeta[len(vieja):]}"
    return None


def referencia(con, cfg, de_verdad: bool) -> None:
    from .reorganizar import ejecutar_plan, planificar
    bases = sorted({v for v, _, _ in MAPA_REFERENCIA})
    plan = []
    for base in bases:
        tabla = [(r["ruta"], f"{destino_referencia(r['carpeta'])}/{r['nombre']}") for r in con.execute(
            "SELECT ruta, carpeta, nombre FROM docs WHERE carpeta = ? OR carpeta LIKE ?", (base, base + "/%"))]
        if tabla:
            plan += planificar(cfg, tabla, base, mezclada=True)
    # Lo que no tiene libros catalogados (los .7z de _Digital): la carpeta entera.
    if (cfg.raiz / "Wargames/_Digital").is_dir():
        plan.append(("carpeta", "Wargames/_Digital", f"{REFERENCIA}/Digital"))
    plan.sort(key=lambda x: {"carpeta": 0, "fichero": 1, "libro": 2}[x[0]])
    ejecutar_plan(con, cfg, "oleada_E_referencia", plan, bases, de_verdad)


EDITORIALES_SUELTAS = {"Warfrog Games", "Wargamer Games Studio", "Game Works", "Just Games", "The Perfect Captain",
                       "Academy Games"}
MISMA_EDITORIAL = {"ICE": "Iron Crown Enterprises", "Dadi&Piombo": "Dadi & Piombo"}


def restos_d(con, cfg, de_verdad: bool) -> None:
    """Carpetas de juego de Wargames sin ningún libro catalogado (solo un .zip o .rar del juego):
    se llevan enteras a su editorial ("Other Publishers" si no se sabe)."""
    import json
    from .reorganizar import _quitar_vacias, ejecutar_plan
    from .rutas import EDITORIALES
    fichero = cfg.datos / FICHERO_EDITORIALES
    haiku = json.loads(fichero.read_text()) if fichero.exists() else {}
    editoriales = {p.name for p in (cfg.raiz / "Wargames").iterdir() if p.is_dir()
                   and con.execute("SELECT 1 FROM docs WHERE ruta LIKE ? LIMIT 1", (f"Wargames/{p.name}/%",)).fetchone()}
    editoriales |= EDITORIALES | EDITORIALES_WAR | set(EDITORIAL_DE.values())
    plan = []
    for padre in ["Wargames", "Wargames/Miniaturas", "Wargames/Tablero", *(f"Wargames/{g}" for g in GRUPOS_WAR)]:
        if not (cfg.raiz / padre).is_dir():
            continue
        for p in sorted((cfg.raiz / padre).iterdir()):
            n = p.name
            if (not p.is_dir() or n in editoriales or n in MIXTAS_WAR | GRUPOS_WAR | FORMATOS | {"Solo"}
                    or n.startswith((".", "_")) or not any(x.is_file() for x in p.rglob("*"))
                    or con.execute("SELECT 1 FROM docs WHERE ruta LIKE ? LIMIT 1", (f"{padre}/{n}/%",)).fetchone()):
                continue
            if n in EDITORIALES_SUELTAS:  # ya es una editorial: a la raíz de Wargames
                if padre != "Wargames":
                    plan.append(("carpeta", f"{padre}/{n}", f"Wargames/{n}"))
                continue
            if n in MISMA_EDITORIAL:  # otra forma de escribir una editorial que ya está
                plan.append(("carpeta", f"{padre}/{n}", f"Wargames/{MISMA_EDITORIAL[n]}/{POR_CLASIFICAR}/{n}"))
                continue
            ed = ALIAS_EDITORIAL.get(n) or EDITORIAL_DE.get(n) or haiku.get(n) or OTRAS
            ed = next((e for e in editoriales if e.lower() == ed.lower()), ed)
            plan.append(("carpeta", f"{padre}/{n}", f"Wargames/{ed}/{n}"))
    for _, o, d in plan:
        print(f"  {o}  ->  {d}")
    ejecutar_plan(con, cfg, "oleada_D_restos", plan, ["Wargames"], de_verdad)
    if not de_verdad:
        print("(las carpetas vacías se quitarán al ejecutar)")


BASES_C = ["D&D", "D&D Third Party", "Paizo", "Necromancer - Frog God Games"]


def ensayo_c(con) -> list[list[str]]:
    existentes = {r[0].split("/")[1].lower(): r[0].split("/")[1]
                  for r in con.execute("SELECT DISTINCT carpeta FROM docs WHERE carpeta LIKE 'RPG/%'")}
    propuestas = []
    for b in BASES_C:
        base = f"RPG/{b}"
        for r in con.execute("SELECT ruta, carpeta, nombre FROM docs WHERE carpeta = ? OR carpeta LIKE ? ORDER BY ruta",
                             (base, base + "/%")):
            segs = [x for x in r["carpeta"][len(base) + 1:].split("/") if x and x != POR_CLASIFICAR]                 if r["carpeta"] != base else []
            juego, debajo = ruta_c(b, segs)
            juego = existentes.get(juego.lower(), juego)
            nueva, regla = proponer_rol(juego, "/".join(debajo), r["nombre"])
            propuestas.append([r["ruta"], f"{nueva}/{r['nombre']}", nueva, f"{b}: {regla}"])
    return propuestas


def juegos_oleada(con, oleada: str) -> list[str]:
    if oleada != "A":
        raise SystemExit("Por ahora solo está la oleada A")
    nombres = {r[0].split("/")[1] for r in con.execute("SELECT carpeta FROM docs WHERE carpeta LIKE 'RPG/%'")}
    return sorted(n for n in nombres if n not in FUERA_DE_A and not n.startswith("_"))


def ensayo(con: sqlite3.Connection, oleada: str, salida: str, cfg=None) -> None:
    juegos = juegos_oleada(con, oleada) if oleada == "A" else []
    propuestas = {"B": lambda: ensayo_familias(con, salida), "C": lambda: ensayo_c(con),
                  "D": lambda: ensayo_d(con, cfg)}.get(oleada, list)()
    for juego in juegos:
        base = f"RPG/{juego}"
        for r in con.execute("SELECT ruta, carpeta, nombre FROM docs WHERE carpeta = ? OR carpeta LIKE ? ORDER BY ruta",
                             (base, base + "/%")):
            relativa = r["carpeta"][len(base) + 1:] if r["carpeta"] != base else ""
            nueva, regla = proponer_rol(juego, relativa, r["nombre"])
            propuestas.append([r["ruta"], f"{nueva}/{r['nombre']}", nueva, regla])
    _sin_series_pequenas(propuestas)
    with open(salida, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(["Origen", "Destino", "Carpeta nueva", "Regla"])
        w.writerows(propuestas)
    tipos = Counter(next((p for p in PurePosixPath(x[2]).parts[2:] if p in TIPOS | {POR_CLASIFICAR}), "?")
                    for x in propuestas)
    print(f"Oleada {oleada}: {len(juegos)} juegos, {len(propuestas)} libros")
    print("  " + ", ".join(f"{t} {n}" for t, n in tipos.most_common()))
    print(f"  ya en su sitio: {sum(x[0] == x[1] for x in propuestas)}")
    print(f"Tabla: {salida}")


def _sin_series_pequenas(propuestas: list[list[str]]) -> None:
    """Una serie o producto con menos de MIN_SERIE ficheros no tiene subcarpeta propia."""
    idiomas = {i for _, i in IDIOMAS}
    cuenta = Counter(p[2] for p in propuestas)
    for p in propuestas:
        partes = list(PurePosixPath(p[2]).parts)
        idioma = partes.pop() if partes[-1] in idiomas else None
        if partes[-1] in TIPOS | {POR_CLASIFICAR}:
            continue
        carpeta = "/".join(partes)
        total = sum(n for c, n in cuenta.items() if c == carpeta or c.startswith(carpeta + "/"))
        if total < MIN_SERIE and partes[-2] != POR_CLASIFICAR:
            partes.pop()
            p[2] = "/".join(partes + ([idioma] if idioma else []))
            p[1] = f"{p[2]}/{PurePosixPath(p[1]).name}"
            p[3] += ", serie pequeña: sin subcarpeta"


def main(argv=None) -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("orden", choices=["ensayo", "ejecutar", "restos", "referencia"])
    ap.add_argument("oleada")
    ap.add_argument("--csv")
    ap.add_argument("--ejecutar", action="store_true")
    args = ap.parse_args(argv)
    cfg = config_mod.cargar()
    con = conectar(cfg.db)
    if args.orden == "restos":
        restos_d(con, cfg, args.ejecutar)
    elif args.orden == "referencia":
        referencia(con, cfg, args.ejecutar)
    elif args.orden == "ensayo":
        ensayo(con, args.oleada, args.csv, cfg)
    else:
        with open(args.csv, encoding="utf-8-sig", newline="") as f:
            tabla = [(r[0], r[1]) for r in list(csv.reader(f, delimiter=";"))[1:]]
        bases = sorted({"/".join(o.split("/")[:2]) for o, _ in tabla})
        ejecutar_tabla(con, cfg, f"oleada_{args.oleada}", tabla, bases, args.ejecutar,
                       mezclada=args.oleada in ("D", "E"), estricta=args.oleada == "E")
    sys.exit(0)


if __name__ == "__main__":
    main()
