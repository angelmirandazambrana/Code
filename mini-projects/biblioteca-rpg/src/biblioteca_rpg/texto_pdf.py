"""Datos que el propio PDF dice de sí mismo: año del copyright, editorial e ISBN.

Se leen las primeras y las últimas páginas, donde suelen estar los créditos y la licencia.
Todo es local: no hace falta internet.
"""

import re
import time
import unicodedata

PAGINAS_INICIO = 4
PAGINAS_FINAL = 2

# Editoriales grandes con las formas en que aparecen en los créditos.
# Las demás salen de las carpetas de la propia biblioteca (ver diccionario_editoriales).
ALIAS_EDITORIALES = {
    "TSR": ["TSR, Inc", "TSR Inc", "TSR Hobbies", "TSR"],
    "Wizards of the Coast": ["Wizards of the Coast"],
    "Games Workshop": ["Games Workshop"],
    "Chaosium": ["Chaosium"],
    "Paizo": ["Paizo Publishing", "Paizo Inc", "Paizo"],
    "FASA": ["FASA Corporation", "FASA"],
    "Catalyst": ["Catalyst Game Labs", "InMediaRes Productions"],
    "White Wolf": ["White Wolf Publishing", "White Wolf"],
    "Onyx Path": ["Onyx Path Publishing", "Onyx Path"],
    "Steve Jackson Games": ["Steve Jackson Games"],
    "Palladium Books": ["Palladium Books"],
    "Mongoose Publishing": ["Mongoose Publishing"],
    "Cubicle 7": ["Cubicle 7"],
    "Pinnacle Entertainment": ["Pinnacle Entertainment"],
    "Fantasy Flight Games": ["Fantasy Flight Games", "Fantasy Flight Publishing"],
    "Modiphius": ["Modiphius Entertainment", "Modiphius"],
    "Free League": ["Fria Ligan", "Free League"],
    "Goodman Games": ["Goodman Games"],
    "Kobold Press": ["Kobold Press", "Open Design"],
    "Green Ronin": ["Green Ronin"],
    "West End Games": ["West End Games"],
    "Iron Crown Enterprises": ["Iron Crown Enterprises"],
    "GDW": ["Game Designers' Workshop", "Game Designers Workshop", "GDW"],
    "Avalon Hill": ["Avalon Hill"],
    "SPI": ["Simulations Publications"],
    "Osprey Gaming": ["Osprey Publishing", "Osprey Games"],
    "R. Talsorian": ["R. Talsorian Games", "R.Talsorian Games", "R. Talsorian"],
    "AEG": ["Alderac Entertainment Group"],
    "Atlas Games": ["Atlas Games"],
    "Troll Lord Games": ["Troll Lord Games"],
    "Frog God Games": ["Frog God Games"],
    "Necromancer Games": ["Necromancer Games"],
    "Monte Cook Games": ["Monte Cook Games"],
    "Evil Hat": ["Evil Hat Productions", "Evil Hat"],
    "Schwalb Entertainment": ["Schwalb Entertainment"],
    "Flying Buffalo": ["Flying Buffalo"],
    "Hero Games": ["Hero Games", "DOJ, Inc"],
    "Columbia Games": ["Columbia Games"],
    "GMT Games": ["GMT Games"],
    "Decision Games": ["Decision Games"],
    "Victory Games": ["Victory Games"],
    "Mayfair Games": ["Mayfair Games"],
    "Judges Guild": ["Judges Guild"],
}

# Carpetas de editorial que no son nombres de editorial.
NO_SON_EDITORIALES = {
    "subclasses", "d20 modern", "unknown publisher", "d&d unleashed", "deck of many",
    "warlock homebrew", "adventure a week", "classic modules today",
}

# Líneas de licencias y créditos de arte: su año vale, su "dueño" no es la editorial del libro.
RE_LICENCIA = re.compile(
    r"system reference document|open game licen[cs]e|\bOGL\b|\bSRD\b|open gaming|creative commons|"
    r"trademark|used under licen[cs]e|used with permission|\bart(?:work)?\b|illustrat|cartograph|"
    r"\bcover\b|\bmaps?\b|photo|\bfonts?\b|\bstock\b|clip ?art|elite design elements",
    re.I,
)
RE_COPYRIGHT = re.compile(
    r"(?:©|\(c\)|copyright)[\s©]*(?:\(c\)\s*)?((?:(?:19[5-9]\d|20[0-4]\d)\s*[,\-–&/y]?\s*)+)([^\n©]{0,90})",
    re.I,
)
# «© Copyright Games Workshop Limited 2017»: el dueño va antes del año.
RE_COPYRIGHT_INV = re.compile(
    r"(?:©|\(c\)|copyright)[\s©]*(?:copyright\s*)?([A-Za-z][^\n\d©]{2,60}?)[,.\s]+(19[5-9]\d|20[0-4]\d)\b",
    re.I,
)
RE_ANIO = re.compile(r"19[5-9]\d|20[0-4]\d")
RE_ISBN = re.compile(r"ISBN(?:-1[03])?[\s:#]*((?:97[89][\s-]?)?(?:\d[\s-]?){9}[\dXx])")
RE_ISBN_SUELTO = re.compile(r"(?<!\d)(97[89]\d{10})(?!\d)")  # p. ej. en el nombre del fichero
RE_SUFIJOS = re.compile(
    r"[,.]?\s*\b(?:inc|llc|ltd|limited|corp|corporation|gmbh|s\.?l|s\.?a|ab|co)\b\.?", re.I
)


def normalizar(s: str) -> str:
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower()
    s = s.replace("&", " and ")
    s = re.sub(r"[^a-z0-9]+", " ", s)
    return " ".join(s.split())


def isbn_valido(digitos: str) -> bool:
    d = digitos.upper()
    if len(d) == 10 and re.fullmatch(r"\d{9}[\dX]", d):
        suma = sum((10 - i) * (10 if c == "X" else int(c)) for i, c in enumerate(d))
        return suma % 11 == 0
    if len(d) == 13 and d.isdigit():
        suma = sum(int(c) * (1 if i % 2 == 0 else 3) for i, c in enumerate(d))
        return suma % 10 == 0
    return False


def isbns(texto: str, nombre: str = "") -> list[str]:
    vistos: list[str] = []
    candidatos = [m.group(1) for m in RE_ISBN.finditer(texto)]
    candidatos += [m.group(1) for m in RE_ISBN_SUELTO.finditer(nombre)]
    for c in candidatos:
        d = re.sub(r"[\s-]", "", c).upper()
        if isbn_valido(d) and d not in vistos:
            vistos.append(d)
    return vistos[:3]


def diccionario_editoriales(conocidas: list[str]) -> list[tuple[str, str]]:
    """[(forma normalizada, nombre canónico)], de la más larga a la más corta."""
    pares: dict[str, str] = {}
    for nombre in conocidas:
        n = normalizar(RE_SUFIJOS.sub("", nombre))
        if len(n) >= 4 and n not in NO_SON_EDITORIALES:
            pares.setdefault(n, nombre)
    for canonico, formas in ALIAS_EDITORIALES.items():
        for f in formas:
            pares[normalizar(RE_SUFIJOS.sub("", f)) or normalizar(f)] = canonico
    return sorted(pares.items(), key=lambda x: -len(x[0]))


def _buscar_editorial(texto: str, dicc: list[tuple[str, str]]) -> str | None:
    t = f" {normalizar(texto)} "
    for forma, canonico in dicc:
        if f" {forma} " in t:
            return canonico
    return None


# Las líneas que publica Wizards of the Coast. Fuera de ellas, su copyright es el de la OGL, el SRD o
# el pie fijo de la DMs Guild («©2016 Wizards of the Coast LLC, PO Box 707…»): ni su año ni su nombre
# son los del libro.
LINEAS_WOTC = re.compile(r"^(RPG/(D&D|d20 Modern|Star Wars[^/]*|Gamma World|Alternity|L5R|Legend of the Five Rings)|"
                         r"Magazines/(Dragon|Dungeon|Polyhedron)[^/]*)(/|$)")
# También partido entre líneas: «Copyright 2000 Wizards of» / «Copyright 2000, Wiz-».
RE_WOTC = re.compile(r"wizards\W+of\b|\bwiz-\s*$|\bcoast,?\s+(inc|llc)\b|\bwotc\b", re.I | re.M)
# La OGL y el SRD (aunque «Wizards» caiga en la línea siguiente): su año es el de la licencia.
RE_OGL = re.compile(r"open game licen[cs]e|system reference document|\bSRD\b", re.I)


def es_de_wotc(ruta: str | None) -> bool:
    """¿Puede ser de Wizards of the Coast? (Sin ruta, no se sabe: sí.)"""
    return not ruta or bool(LINEAS_WOTC.match(ruta))


def extraer(texto: str, nombre: str, dicc: list[tuple[str, str]], anio_max: int | None = None,
            ruta: str | None = None) -> dict:
    """Devuelve {'publicado', 'editorial', 'isbn'} con lo que se pueda deducir (o vacío)."""
    anio_max = anio_max or time.localtime().tm_year
    wotc = es_de_wotc(ruta)
    declaraciones = []  # (año más reciente, texto que sigue, es_licencia)

    def anotar(anio, resto, inicio_match, fin_match):
        inicio = texto.rfind("\n", 0, inicio_match) + 1
        linea = texto[inicio:fin_match]
        if not wotc and (RE_WOTC.search(linea) or RE_OGL.search(linea)):
            return
        declaraciones.append((anio, resto, bool(RE_LICENCIA.search(linea))))

    for m in RE_COPYRIGHT.finditer(texto):
        anios = [int(a) for a in RE_ANIO.findall(m.group(1)) if int(a) <= anio_max]
        if anios:
            anotar(max(anios), m.group(2), m.start(), m.end())
    for m in RE_COPYRIGHT_INV.finditer(texto):
        anio = int(m.group(2))
        if anio <= anio_max:
            anotar(anio, m.group(1), m.start(), m.end())

    res: dict = {}
    if declaraciones:
        res["publicado"] = max(a for a, _, _ in declaraciones)
        # La editorial: la del copyright más reciente que no sea de licencia ni de arte.
        for anio, resto, licencia in sorted(declaraciones, key=lambda x: -x[0]):
            if licencia:
                continue
            ed = _buscar_editorial(resto, dicc)
            if ed:
                res["editorial"] = ed
                break
    lista = isbns(texto, nombre)
    if lista:
        res["isbn"] = lista
    return res


def leer_texto(doc) -> str:
    """Texto de las primeras y últimas páginas de un documento PyMuPDF ya abierto."""
    n = doc.page_count
    paginas = list(range(min(PAGINAS_INICIO, n)))
    paginas += [i for i in range(max(n - PAGINAS_FINAL, 0), n) if i not in paginas]
    return "\n".join(doc[i].get_text() for i in paginas)
