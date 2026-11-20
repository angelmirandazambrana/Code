"""Deduce los metadatos de un fichero a partir de su ruta dentro de la biblioteca.

La estructura de V:\\ (fases 1-6 de la reorganización) ya codifica casi todo:
    RPG/Shadowrun/3e (FASA, 1998)/fichero.pdf
    RPG/D&D/Settings/Dark Sun/fichero.pdf
    RPG/D&D Third Party/3e-3.5e/AEG/fichero.pdf
    Wargames/Tablero/Avalon Hill/Panzer Leader & Panzer Blitz/fichero.pdf
    Magazines/Dragon Magazine/fichero.pdf

Todo es heurístico: lo que no se reconoce queda vacío y la carpeta sigue siendo navegable.
"""

import re
from dataclasses import asdict, dataclass

from . import codigos

CATEGORIAS = {
    "RPG": "Rol",
    "Wargames": "Wargames",
    "Magazines": "Revistas",
    "Juegos de mesa": "Juegos de mesa",
}

# Formatos de Wargames que van antes del juego o la editorial.
FORMATOS_WARGAMES = {"Miniaturas", "Tablero", "Solo"}

# Carpetas que son una editorial, no un juego: el juego va en la carpeta siguiente.
EDITORIALES = {
    "Avalanche Press", "Avalon Hill", "Clash of Arms", "Days of Wonder", "Decision Games",
    "Dream Pod 9", "GDW", "GMT", "Games Workshop", "Mantic Games", "Metagaming", "Milton Bradley",
    "MMP", "One Page Rules", "Osprey Gaming", "SPI", "Task Force Games", "The Gamers",
    "Too Fat Lardies", "Two Hour Wargame", "Victory Games", "WRG",
}

# Carpetas que agrupan por editorial, pero no son un sistema: la carpeta siguiente es la editorial.
AGRUPA_EDITORIALES = {"DrivethroughRPG"}

# Carpeta de sistema -> (sistema, editorial)
SISTEMAS_ALIAS = {
    "Paizo": ("Pathfinder", "Paizo"),
    "D&D Third Party": ("D&D", None),
}

# Segmentos de "tercera parte": la carpeta siguiente es la editorial.
RE_TERCEROS = re.compile(r"^(3rd|third)[ -]party$", re.I)

# Carpetas que no son editoriales aunque cuelguen de un agrupador de editoriales.
RE_NO_EDITORIAL = re.compile(r"^(_|\d+\.|other$|misc|various|varios|homebrew|fan )", re.I)

# Edición: "5e", "3e-3.5e", "5e-5.6e", "Basic", "1st Edition", con " - Nombre" y "(Editorial, año)" opcionales.
RE_EDICION = re.compile(
    r"^(?P<ed>\d+(?:\.\d+)?e(?:-\d+(?:\.\d+)?e)?|Basic|\d+(?:st|nd|rd|th) Edition)"
    r"(?:\s*-\s*(?P<nombre>[^()]+?))?\s*(?:\((?P<paren>[^)]*)\))?\s*$",
    re.I,
)
# Traveller: "08 - Mongoose Traveller (2008)"
RE_EDICION_NUMERADA = re.compile(r"^\d{2} - (?P<nombre>[^()]+?)\s*(?:\((?P<paren>[^)]*)\))?\s*$")
RE_ANIO = re.compile(r"^(1[89]\d\d|20\d\d)(?:-(?:1[89]|20)\d\d)?$")

# Carpeta -> tipo de contenido. Gana la carpeta más profunda que encaje.
TIPOS = [
    (re.compile(r"^(adventures?|aventuras?|modules?|m[oó]dulos|scenarios?|escenarios|campaigns?)$", re.I), "Aventura"),
    (re.compile(r"^(rulebooks?|core( rules| books?)?|reglas|reglamentos?|_reglamentos sueltos)$", re.I), "Reglas"),
    (re.compile(r"^(settings?|ambientaci[oó]n(es)?)$", re.I), "Ambientación"),
    (re.compile(r"^(_?revistas|_?magazines?)$", re.I), "Revista"),
    (re.compile(r"(homebrew|fan created|_fan y homebrew|fan made)", re.I), "Fan y homebrew"),
    (re.compile(r"^(maps?|mapas|_mapas)$", re.I), "Mapas"),
    (re.compile(r"^(bestiar(y|ies|ios?)|monsters?)$", re.I), "Bestiario"),
    (re.compile(r"(character sheets?|hojas de personaje)", re.I), "Hojas de personaje"),
    (re.compile(r"^_?tokens$", re.I), "Tokens"),
    (re.compile(r"^(_pintura y modelismo|painting)$", re.I), "Pintura y modelismo"),
    (re.compile(r"^(_referencia|guides and indexes|_?indexes|00\. indexes)$", re.I), "Referencia"),
    (re.compile(r"^(supplements?|suplementos?)$", re.I), "Suplemento"),
    (re.compile(r"^_gm books$", re.I), "Recursos para el DJ"),
    (re.compile(r"^(warbands?|army ?lists?|codex|codices)$", re.I), "Listas de ejército"),
]
TIPOS_NOMBRE = [
    (re.compile(r"\b(adventure|module|scenario|adv -)\b", re.I), "Aventura"),
    (re.compile(r"\bcharacter sheets?\b", re.I), "Hojas de personaje"),
]

IDIOMAS = [
    (re.compile(r"\[(fr|french|fran[cç]ais)\]|\bjdr francais\b|\(fr\)", re.I), "Francés"),
    (re.compile(r"\[(es|spanish|espa[nñ]ol)\]|\(es\)|\brevistas es\b|\.en\.es\.", re.I), "Español"),
    (re.compile(r"\[(de|german|deutsch)\]|\(de\)", re.I), "Alemán"),
    (re.compile(r"\[(it|italian|italiano)\]|\(it\)", re.I), "Italiano"),
    (re.compile(r"\[(pl|polish)\]|\(pl\)|\bmagia i miecz\b", re.I), "Polaco"),
]


@dataclass
class Datos:
    carpeta: str
    nombre: str
    titulo: str
    categoria: str | None = None
    formato: str | None = None
    sistema: str | None = None
    edicion: str | None = None
    editorial: str | None = None
    anio: int | None = None
    ambientacion: str | None = None
    tipo: str | None = None
    idioma: str | None = None
    codigo: str | None = None          # código de catálogo de la editorial (codigos.py)
    codigo_orden: str | None = None

    def dict(self) -> dict:
        return asdict(self)


def limpiar(seg: str) -> str:
    return seg.lstrip("_ ").strip()


def titulo_de(nombre: str) -> str:
    base = re.sub(r"\.[A-Za-z0-9]{2,4}$", "", nombre)
    base = base.replace("_", " ")
    return re.sub(r"\s+", " ", base).strip()


def _parentesis(texto: str | None) -> tuple[str | None, int | None]:
    """'(FASA, 1998)' -> ('FASA', 1998); '(2008)' -> (None, 2008)."""
    if not texto:
        return None, None
    partes = [p.strip() for p in texto.split(",") if p.strip()]
    anio = None
    if partes and RE_ANIO.match(partes[-1]):
        anio = int(partes.pop()[:4])
    editorial = ", ".join(partes) or None
    return editorial, anio


def analizar(ruta: str) -> Datos:
    """ruta: relativa a la raíz, con '/' o '\\'."""
    segs = [s for s in re.split(r"[\\/]", ruta) if s]
    nombre = segs[-1]
    carpetas = segs[:-1]
    d = Datos(carpeta="/".join(carpetas), nombre=nombre, titulo=titulo_de(nombre))
    if not carpetas:
        return d

    if carpetas[0] == "assets":
        # Papercraft y miniaturas de papel: assets/Papercraft/<editorial>/... o
        # assets/By Artist or Source/<editorial>/.../Paper Minis
        d.categoria, d.tipo = "Papercraft", "Papercraft"
        if len(carpetas) > 2:
            d.editorial = carpetas[2]
            if carpetas[1] != "Papercraft" and len(carpetas) > 3:
                d.sistema = limpiar(carpetas[3])
        return d
    d.categoria = CATEGORIAS.get(carpetas[0], limpiar(carpetas[0]))
    resto = carpetas[1:]
    if d.categoria == "Revistas":
        d.tipo = "Revista"

    if carpetas[0] == "Wargames" and resto and resto[0] in FORMATOS_WARGAMES:
        d.formato = resto[0]
        resto = resto[1:]

    editorial_tercero = None
    i = 0
    if resto:
        primero = resto[0]
        if primero in AGRUPA_EDITORIALES:
            if len(resto) > 1 and not RE_NO_EDITORIAL.match(resto[1]):
                editorial_tercero = resto[1]
            i = 2
        elif primero in SISTEMAS_ALIAS:
            d.sistema, editorial_alias = SISTEMAS_ALIAS[primero]
            d.editorial = editorial_alias
            i = 1
        elif primero in EDITORIALES:
            d.editorial = primero
            if len(resto) > 1:
                d.sistema = limpiar(resto[1])
                i = 2
            else:
                i = 1
        else:
            d.sistema = limpiar(primero)
            i = 1

    tercero_siguiente = carpetas[0] == "RPG" and resto and resto[0] == "D&D Third Party"
    for j, seg in enumerate(resto[i:], start=i):
        m = RE_EDICION.match(seg) or RE_EDICION_NUMERADA.match(seg)
        if m and d.edicion is None:
            grupos = m.groupdict()
            ed = grupos.get("ed")
            nombre_ed = (grupos.get("nombre") or "").strip()
            d.edicion = f"{ed} - {nombre_ed}" if ed and nombre_ed else (ed or nombre_ed)
            editorial, anio = _parentesis(grupos.get("paren"))
            if editorial and not d.editorial:
                d.editorial = editorial
            if anio:
                d.anio = anio
            if tercero_siguiente:
                # D&D Third Party/<edición>/<editorial>/...
                sig = resto[j + 1] if j + 1 < len(resto) else None
                if sig and not RE_NO_EDITORIAL.match(sig):
                    editorial_tercero = sig
                tercero_siguiente = False
            continue
        if RE_TERCEROS.match(seg):
            sig = resto[j + 1] if j + 1 < len(resto) else None
            if sig and not RE_NO_EDITORIAL.match(sig):
                editorial_tercero = sig
            continue
        if re.match(r"^settings?$", seg, re.I) and j + 1 < len(resto):
            d.ambientacion = limpiar(resto[j + 1])

    if editorial_tercero:
        d.editorial = limpiar(editorial_tercero)

    for seg in carpetas:
        for patron, tipo in TIPOS:
            if patron.search(seg):
                d.tipo = tipo
                break
    if d.tipo is None:
        for patron, tipo in TIPOS_NOMBRE:
            if patron.search(nombre):
                d.tipo = tipo
                break

    texto = " / ".join(segs)
    for patron, idioma in IDIOMAS:
        if patron.search(texto):
            d.idioma = idioma
            break

    d.codigo = codigos.del_nombre(nombre, d.carpeta)
    d.codigo_orden = codigos.orden(d.codigo)
    return d
