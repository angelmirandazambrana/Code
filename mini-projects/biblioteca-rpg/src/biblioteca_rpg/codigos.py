"""Código de catálogo de la editorial: "MGP40039", "PZO7121", "TSR9123", "MAA190".

Sale del nombre del fichero ("MGT2 - Skandersvik [2020][MGP40039].pdf") o del texto del PDF
(portada, créditos, contraportada). Solo se reconocen los prefijos de la tabla, cada uno con su
número de cifras: así "DCC 2016 Holiday Module" (un año) o "CA 2018" no pasan por códigos.

El código se guarda tal cual (prefijo en mayúsculas + cifras) y aparte una clave para ordenar
con los números alineados ("PZO9" antes que "PZO10").
"""

import re
from collections import Counter
from dataclasses import dataclass

from .texto_pdf import normalizar


@dataclass(frozen=True)
class Serie:
    prefijo: str
    editoriales: tuple[str, ...]   # nombres unificados (editoriales.py) a los que pertenece
    cifras: tuple[int, int]        # mínimo y máximo de cifras del número
    pista: str | None = None       # si el prefijo es ambiguo: texto que tiene que aparecer en la ruta
    sufijo: str = ""               # letras que pueden ir pegadas al final ("PAL519P")


OSPREY = ("Osprey Gaming",)
SERIES = [
    Serie("PZO", ("Paizo",), (4, 5)),
    Serie("MGP", ("Mongoose Publishing",), (3, 5)),
    Serie("TSR", ("TSR", "Wizards of the Coast"), (4, 5)),
    Serie("WTC", ("Wizards of the Coast",), (5, 5)),
    Serie("FASA", ("FASA",), (3, 4)),
    Serie("WW", ("White Wolf", "Onyx Path"), (4, 5)),
    Serie("ICE", ("Iron Crown Enterprises",), (3, 4)),
    Serie("GDW", ("GDW",), (3, 4)),
    Serie("AEG", ("AEG",), (3, 4)),
    Serie("AG", ("Atlas Games",), (4, 4)),
    Serie("PAL", ("Palladium Books",), (3, 4), sufijo="P"),
    Serie("JG", ("Judges Guild",), (2, 4)),
    Serie("LPJ", ("Louis Porter Jr. Design",), (4, 4)),
    Serie("CB7", ("Cubicle 7",), (4, 5)),
    Serie("CAT", ("Catalyst",), (5, 5)),
    Serie("GRR", ("Green Ronin",), (3, 4)),
    Serie("WEG", ("West End Games",), (5, 5)),
    Serie("FGU", ("Fantasy Games Unlimited",), (3, 4)),
    Serie("BTRC", ("BTRC",), (4, 4)),
    Serie("CP", ("R. Talsorian",), (4, 4)),
    Serie("SJG", ("Steve Jackson Games",), (4, 5)),
    Serie("CHA", ("Chaosium",), (4, 5)),
    Serie("GMG", ("Goodman Games",), (4, 5)),
    Serie("DCC", ("Goodman Games",), (1, 3)),
    Serie("MCG", ("Monte Cook Games",), (3, 4)),
    Serie("LEG", ("Leading Edge Games",), (5, 5)),
    # Series de Osprey: prefijos que pueden ser palabras, así que solo con "Osprey" en la ruta.
    *(Serie(p, OSPREY, (1, 3), pista="osprey")
      for p in ("MAA", "CAM", "ELI", "NVG", "WAR", "FOR", "ESS", "OWG", "DUE", "CMD", "RAID", "ACE", "COM",
                "WPN", "OSP")),
]
POR_PREFIJO = {s.prefijo: s for s in SERIES}

# Prefijo + separador opcional + cifras + sufijo opcional, sin letras ni cifras pegadas detrás.
_PREFIJOS = "|".join(sorted((re.escape(s.prefijo) for s in SERIES), key=len, reverse=True))
RE_CODIGO = re.compile(rf"(?<![A-Za-z0-9])({_PREFIJOS})[ _#.-]?(\d{{1,6}})([A-Z]?)(?![A-Za-z0-9])", re.I)
RE_COPYRIGHT = re.compile(r"©|\(c\)|copyright", re.I)
# Tienen forma de código pero son nombres de juego ("Cyberpunk 2020", "Cyberpunk 2013").
NO_SON_CODIGOS = {"CP2013", "CP2020", "CP2077"}
# En las revistas los códigos que salen son de reseñas y anuncios, no de la revista.
RE_REVISTA = re.compile(r"(?:^|/)(?:magazines?|revistas?|fanzines?)(?:/|$)", re.I)


def _valido(prefijo: str, cifras: str, sufijo: str, en_texto: str, mayusculas: str) -> str | None:
    s = POR_PREFIJO.get(prefijo.upper())
    if s is None or not s.cifras[0] <= len(cifras) <= s.cifras[1]:
        return None
    if sufijo and sufijo.upper() not in s.sufijo:
        return None
    if len(s.prefijo) <= 2 and prefijo != prefijo.upper():
        return None  # "ag", "Cp" o "jg" sueltos en minúsculas son palabras, no códigos
    if mayusculas and prefijo != prefijo.upper():
        return None
    if s.pista and s.pista not in normalizar(en_texto):
        return None
    codigo = f"{s.prefijo}{cifras}{sufijo.upper()}"
    return None if codigo in NO_SON_CODIGOS else codigo


def _partir(codigo: str) -> tuple[Serie | None, str, str]:
    """'CB72000' -> (serie CB7, '2000', ''); usa la tabla porque algún prefijo lleva cifras."""
    for pre in sorted(POR_PREFIJO, key=len, reverse=True):
        if codigo.startswith(pre):
            m = re.match(r"(\d+)([A-Z]?)$", codigo[len(pre):])
            if m:
                return POR_PREFIJO[pre], m.group(1), m.group(2)
    return None, "", ""


def orden(codigo: str | None) -> str | None:
    """'PZO9' -> 'PZO 000009': ordena por prefijo y luego por número."""
    if not codigo:
        return None
    serie, cifras, sufijo = _partir(codigo)
    return f"{serie.prefijo} {int(cifras):06d}{sufijo}" if serie else codigo


def del_nombre(nombre: str, ruta: str = "") -> str | None:
    """Código del nombre del fichero; si hay varios distintos, el que va entre corchetes o llaves."""
    base = re.sub(r"\.[A-Za-z0-9]{2,4}$", "", nombre)
    contexto = f"{ruta} {nombre}"
    candidatos = []
    for m in RE_CODIGO.finditer(base):
        c = _valido(m.group(1), m.group(2), m.group(3), contexto, "")
        if c:
            entre_corchetes = base[m.start() - 1:m.start()] in "[{(" if m.start() else False
            candidatos.append((not entre_corchetes, m.start(), c))
    return min(candidatos)[2] if candidatos else None


def de_editorial(codigo: str, editorial: str | None) -> bool:
    """¿Puede ser de esta editorial? (sin editorial conocida, sí)."""
    if not editorial:
        return True
    from .editoriales import clave
    serie = _partir(codigo)[0]
    return serie is None or clave(editorial) in {clave(e) for e in serie.editoriales}


def del_texto(texto: str, editorial: str | None = None, ruta: str = "") -> tuple[str | None, list[str]]:
    """Código del producto en el texto del PDF -> (código o None, candidatos vistos).

    Al final de muchos libros hay anuncios con los códigos de otros productos, así que solo vale
    un código si es el único, o si se repite (portada y contraportada) más que cualquier otro.
    Las líneas de copyright no cuentan ("© TSR 1983" no es un código).
    """
    if RE_REVISTA.search(ruta):
        return None, []
    vistos: Counter = Counter()
    contexto = f"{ruta} {texto[:4000]}"
    for linea in texto.splitlines():
        if RE_COPYRIGHT.search(linea):
            continue
        for m in RE_CODIGO.finditer(linea):
            c = _valido(m.group(1), m.group(2), m.group(3), contexto, "mayusculas")
            if c and de_editorial(c, editorial):
                vistos[c] += 1
    candidatos = [c for c, _ in vistos.most_common()]
    if len(vistos) == 1:
        return candidatos[0], candidatos
    if len(vistos) > 1:
        (primero, n1), (_, n2) = vistos.most_common(2)
        if n1 >= 2 and n1 > n2:
            return primero, candidatos
    return None, candidatos[:10]
