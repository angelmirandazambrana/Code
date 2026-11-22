"""Listas de productos de wikis (Traveller Wiki, Wikipedia): año, mes, editorial y código de los libros viejos.

    python -m biblioteca_rpg.referencias bajar [--solo traveller|wikipedia]   # despacio: unos minutos
    python -m biblioteca_rpg.referencias cruzar [--ensayo]                    # con el catálogo; luego aplica

Cada lista vale para unas carpetas de la biblioteca (las de su juego). Un documento de esas carpetas se
empareja con un producto de la lista por ISBN, por código de producto (TSR9039, PZO1110…) o por título
(casi idéntico, y si hay varios con el mismo título —el Player's Handbook de cada edición— el de la
edición de la carpeta). Lo emparejado va a la tabla extra con la fuente 'ref'.
"""

import argparse
import difflib
import json
import re
import sys
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from html.parser import HTMLParser

from . import config as config_mod
from .db import conectar

AGENTE = "biblioteca-rpg/1.0 (catalogo personal de una biblioteca de rol)"
MESES = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}
UMBRAL = 0.92            # parecido mínimo de títulos
UMBRAL_CON_CODIGO = 0.4  # con el mismo código basta con que el título no sea otra cosa
TOLERANCIA_EDICION = 3   # años antes del de la edición de la carpeta


def _dnd(anio):
    return "TSR" if anio and anio <= 1997 else "Wizards of the Coast"


def _shadowrun(anio):
    if not anio:
        return None
    return "FASA" if anio <= 2000 else "FanPro" if anio <= 2006 else "Catalyst"


# (página, carpetas donde buscar, editorial (texto o función del año), prefijo para códigos solo numéricos)
DND = ("RPG/D&D/",)
WIKIPEDIA = [
    ("List of Dungeons & Dragons rulebooks", DND, _dnd, "TSR"),
    ("List of Dungeons & Dragons modules", DND, _dnd, "TSR"),
    ("List of Dungeons & Dragons adventures", DND, _dnd, None),
    ("List of Dragonlance modules and sourcebooks", DND + ("RPG/Dragonlance - Fifth Age/",), _dnd, "TSR"),
    ("List of Eberron modules and sourcebooks", DND, _dnd, None),
    ("List of Dungeon Crawl Classics modules", ("RPG/Dungeon Crawl Classics/", "RPG/D&D 3rd Party/"),
     "Goodman Games", None),
    ("List of Shadowrun books", ("RPG/Shadowrun/",), _shadowrun, "FASA"),
    ("List of Pathfinder books", ("RPG/Pathfinder/",), "Paizo", None),
]
TRAVELLER = ("RPG/Traveller/", "Magazines/Traveller/", "Magazines/The Journal of the Travellers' Aid Society/",
             "RPG/2300 AD/")


# --- Texto ----------------------------------------------------------------------------

def norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().lower()
    s = s.replace("&", " and ").replace("'", "")
    s = re.sub(r"[^a-z0-9]+", " ", s)
    s = re.sub(r"^(the|a|an) ", "", s.strip())
    return " ".join(s.split())


RE_PREFIJO = re.compile(r"^\s*(?:[A-Za-z]{1,5}\d{0,5}[a-z]?(?:\.\d+)?|\d{3,5})\s*[-–_:]\s+")
# Siglas de juego o edición delante del título, aunque no lleven guion.
RE_SIGLA_JUEGO = re.compile(r"^\s*(?:CT|MT|TNE|T4|GT|T20|MGT2?|T5|TCE|DCC|SR[1-6]?|PF[12]?|AD&?D[12]?|OD&?D|"
                            r"D&D|DnD|BECMI|[1-5]e|3\.5e?)\s+(?=\S)", re.I)
_RUIDO = r"v\d+(?:\.\d+)*|hq|ocr|(?:alt )?scan(?:ned)?|retail|bookmarked|optimi[sz]ed|ebook|final|pdf|print(?:er)? friendly"
# Corchetes y llaves fuera siempre; paréntesis solo si son ruido («(OCR)», «(2016)», «(MGP40009)»):
# «(Update 2023)» o «(Revised)» son parte del título.
RE_RUIDO = re.compile(r"\[[^\]]*\]|\{[^}]*\}|\(\s*(?:\d{4}(?:[-/ .]\d{1,2}){0,2}|[A-Z]{2,5}\d{2,6}|"
                      rf"(?:{_RUIDO})(?:[ ,]+(?:{_RUIDO}))*|\d+)\s*\)|\b(?:{_RUIDO})\b", re.I)


def titulo_doc(nombre: str) -> str:
    """Título de un documento a partir de su nombre de fichero: sin extensión, códigos ni ruido."""
    s = re.sub(r"\.[A-Za-z0-9]{2,4}$", "", nombre or "").replace("_", " ")
    s = RE_RUIDO.sub(" ", s)
    for _ in range(3):  # "MGT2 - ", "CT - ", "A1 - ", "TSR 9039 - ", "CT Azhanti…"
        s = re.sub(r"^\s*TSR\s*\d{4,5}\s*[-–_:]?\s*", "", s, flags=re.I)
        s = RE_PREFIJO.sub("", RE_SIGLA_JUEGO.sub("", s))
    return norm(s)


def variantes(titulo: str) -> list[str]:
    """El título y sus partes a los lados de ':' («Rise of the Runelords #1: Burnt Offerings»)."""
    res = [norm(titulo)]
    if ":" in titulo:
        antes, despues = titulo.split(":", 1)
        res.append(norm(despues))
        if len(norm(antes).split()) >= 2:
            res.append(norm(antes))
    return [r for r in dict.fromkeys(res) if len(r) >= 4]


def fecha(texto: str) -> tuple[int | None, int | None]:
    """(año, mes) de «1980», «June 2004», «August 13, 2009», «08/01/2000»."""
    t = (texto or "").lower()
    m = re.search(r"\b(0?[1-9]|1[0-2])/\d{1,2}/((?:19|20)\d{2})\b", t)
    if m:
        return int(m.group(2)), int(m.group(1))
    m = re.search(r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s+(?:\d{1,2},?\s+)?((?:19|20)\d{2})\b", t)
    if m:
        return int(m.group(2)), MESES[m.group(1)]
    m = re.search(r"\b((?:19[5-9]|20[0-4])\d)\b", t)
    return (int(m.group(1)), None) if m else (None, None)


def isbn13(s: str) -> str | None:
    d = re.sub(r"[^0-9Xx]", "", s or "").upper()
    if len(d) == 13 and d.isdigit():
        return d
    if len(d) == 10:
        base = "978" + d[:9]
        suma = sum(int(c) * (1 if i % 2 == 0 else 3) for i, c in enumerate(base))
        return base + str((10 - suma % 10) % 10)
    return None


def codigo_norm(s: str, prefijo: str | None = None) -> str | None:
    c = re.sub(r"[^A-Za-z0-9]", "", s or "").upper()
    if not c or len(c) < 3:
        return None
    if c.isdigit():
        return f"{prefijo}{c}" if prefijo and 3 <= len(c) <= 5 else None
    return c if re.fullmatch(r"[A-Z]{2,5}\d{3,6}[A-Z]?", c) else None


# --- Bajar ----------------------------------------------------------------------------

def _get_json(url: str, params: dict, reintentos: int = 4) -> dict:
    u = url + "?" + urllib.parse.urlencode(params)
    for i in range(reintentos):
        try:
            req = urllib.request.Request(u, headers={"User-Agent": AGENTE})
            with urllib.request.urlopen(req, timeout=60) as r:
                return json.load(r)
        except urllib.error.HTTPError as ex:
            if ex.code in (429, 503) and i < reintentos - 1:
                time.sleep(30 * (i + 1))
                continue
            raise
    raise RuntimeError("sin respuesta")


class _Tablas(HTMLParser):
    """Filas de las tablas «wikitable» de una página de Wikipedia, con el último encabezado de sección."""

    def __init__(self):
        super().__init__()
        self.tablas: list[dict] = []
        self.fila = self.celda = None
        self.prof = 0
        self.ignorar = 0
        self.seccion = ""
        self.en_titulo = None

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag in ("style", "sup"):
            self.ignorar += 1
        elif tag in ("h2", "h3", "h4") and not self.prof:
            self.en_titulo = []
        elif tag == "table" and "wikitable" in (a.get("class") or "") and not self.prof:
            self.tablas.append({"seccion": self.seccion, "filas": []})
            self.prof = 1
        elif tag == "table" and self.prof:
            self.prof += 1
        elif self.prof == 1 and tag == "tr":
            self.fila = []
        elif self.prof == 1 and tag in ("td", "th") and self.fila is not None:
            self.celda = []
        elif tag == "br" and self.celda is not None:
            self.celda.append(" ")

    def handle_endtag(self, tag):
        if tag in ("style", "sup"):
            self.ignorar = max(0, self.ignorar - 1)
        elif tag in ("h2", "h3", "h4") and self.en_titulo is not None:
            self.seccion = " ".join("".join(self.en_titulo).split())
            self.en_titulo = None
        elif tag == "table" and self.prof:
            self.prof -= 1
        elif self.prof == 1 and tag in ("td", "th") and self.celda is not None:
            self.fila.append(" ".join("".join(self.celda).split()))
            self.celda = None
        elif self.prof == 1 and tag == "tr" and self.fila is not None:
            self.tablas[-1]["filas"].append(self.fila)
            self.fila = None

    def handle_data(self, d):
        if self.ignorar:
            return
        if self.celda is not None:
            self.celda.append(d)
        elif self.en_titulo is not None:
            self.en_titulo.append(d)


def _columna(cabecera: list[str], *claves: str) -> int | None:
    """La primera columna que tenga la primera clave; si ninguna, la segunda clave…"""
    for k in claves:
        for i, c in enumerate(cabecera):
            if k in c.lower():
                return i
    return None


def filas_wikipedia(html: str, editorial, prefijo: str | None) -> list[dict]:
    """Productos de las tablas de una lista de Wikipedia."""
    p = _Tablas()
    p.feed(html)
    res = []
    for t in p.tablas:
        if not t["filas"]:
            continue
        cab = t["filas"][0]
        c_tit = _columna(cab, "title", "name")
        c_fecha = _columna(cab, "release date", "publication date", "published", "date", "year")
        if c_tit is None or c_fecha is None:
            continue
        c_cod = _columna(cab, "tsr#", "item #", "sku", "code")
        c_mod = _columna(cab, "code") if c_cod is not None and "code" not in cab[c_cod].lower() else None
        c_isbn = _columna(cab, "isbn")
        c_aut = _columna(cab, "author")
        for fila in t["filas"][1:]:
            if len(fila) < len(cab) - 1 or c_tit >= len(fila):
                continue  # encabezados de grupo («Boxed sets»)
            titulo = fila[c_tit].strip(' "“”')
            publicado = fila[c_fecha] if c_fecha < len(fila) else ""
            if not titulo or re.search(r"\b(dungeon|dragon)\s*#?\s*\d", publicado, re.I):
                continue  # aventuras publicadas dentro de las revistas Dungeon o Dragon
            anio, mes = fecha(publicado)
            if not anio:
                continue
            def celda(c, fila=fila):
                return fila[c] if c is not None and c < len(fila) else ""
            ed = editorial(anio) if callable(editorial) else editorial
            res.append({"titulo": titulo, "anio": anio, "mes": mes, "editorial": ed,
                        "codigo": codigo_norm(celda(c_cod), prefijo), "modulo": celda(c_mod) or None,
                        "isbn": isbn13(celda(c_isbn)), "autores": celda(c_aut) or None, "seccion": t["seccion"]})
    return res


def bajar_wikipedia(con) -> int:
    total = 0
    for pagina, carpetas, editorial, prefijo in WIKIPEDIA:
        try:
            d = _get_json("https://en.wikipedia.org/w/api.php",
                          {"action": "parse", "page": pagina, "prop": "text", "format": "json",
                           "formatversion": 2, "redirects": 1})
        except (urllib.error.URLError, TimeoutError, OSError, RuntimeError) as ex:
            print(f"  {pagina}: {type(ex).__name__}: {ex}", file=sys.stderr, flush=True)
            continue
        filas = filas_wikipedia(d["parse"]["text"], editorial, prefijo)
        url = "https://en.wikipedia.org/wiki/" + urllib.parse.quote(pagina.replace(" ", "_"))
        _guardar(con, "wikipedia", pagina, carpetas, url, filas)
        print(f"  {pagina}: {len(filas)} productos", flush=True)
        total += len(filas)
        time.sleep(10)
    return total


def _limpiar_wiki(s: str) -> str:
    s = re.sub(r"\[\[(?:[^|\]]*\|)?([^\]]*)\]\]", r"\1", s or "")
    s = re.sub(r"\{\{[^{}]*\}\}", "", s)
    s = re.sub(r"<[^>]+>|'{2,}", "", s)
    return " ".join(s.split())


def infobox(texto: str) -> dict:
    """Campos de la primera {{Infobox…}} de una página de la Traveller Wiki."""
    m = re.search(r"\{\{\s*Infobox", texto or "")
    if not m:
        return {}
    campos = {}
    for linea in texto[m.start():].splitlines()[1:]:
        if linea.startswith("}}"):
            break
        mm = re.match(r"\s*\|\s*([\w ]+?)\s*=\s*(.*)", linea)
        if mm:
            campos[mm.group(1).lower()] = _limpiar_wiki(mm.group(2))
    return campos


def producto_traveller(titulo_pagina: str, texto: str) -> dict | None:
    c = infobox(texto)
    anio, mes = fecha(c.get("year") or c.get("date") or c.get("published") or "")
    if not anio:
        return None
    cod = re.search(r"(?:product|stock)\s*(?:#|no\.?|number)?\s*:?\s*([A-Z]{0,4}-?\d{2,5})", c.get("footnote", ""), re.I)
    return {"titulo": c.get("name") or titulo_pagina, "anio": anio, "mes": mes,
            "editorial": c.get("publisher") or None, "codigo": cod.group(1) if cod else None, "modulo": None,
            "isbn": isbn13(c.get("isbn", "")), "autores": c.get("author") or None, "seccion": c.get("version")}


def bajar_traveller(con) -> int:
    api = "https://wiki.travellerrpg.com/api.php"
    titulos: list[str] = []
    for plantilla in ("Template:InfoboxBook2", "Template:InfoboxBook"):
        seguir: dict = {}
        while True:
            d = _get_json(api, {"action": "query", "list": "embeddedin", "eititle": plantilla, "einamespace": 0,
                                "eilimit": 500, "format": "json", **seguir})
            titulos += [p["title"] for p in d["query"]["embeddedin"]]
            if "continue" not in d:
                break
            seguir = {"eicontinue": d["continue"]["eicontinue"]}
            time.sleep(1)
    titulos = list(dict.fromkeys(titulos))
    print(f"  Traveller Wiki: {len(titulos)} fichas", flush=True)
    filas = []
    for i in range(0, len(titulos), 50):
        d = _get_json(api, {"action": "query", "prop": "revisions", "rvprop": "content", "rvslots": "main",
                            "titles": "|".join(titulos[i:i + 50]), "format": "json", "formatversion": 2})
        for p in d["query"]["pages"]:
            rev = (p.get("revisions") or [{}])[0]
            texto = ((rev.get("slots") or {}).get("main") or {}).get("content", "")
            prod = producto_traveller(p["title"], texto)
            if prod:
                prod["url"] = "https://wiki.travellerrpg.com/" + urllib.parse.quote(p["title"].replace(" ", "_"))
                filas.append(prod)
        time.sleep(2)
    _guardar(con, "traveller", "Traveller Wiki", TRAVELLER, "https://wiki.travellerrpg.com", filas)
    return len(filas)


def _guardar(con, fuente, pagina, carpetas, url, filas) -> None:
    with con:
        con.execute("DELETE FROM referencias WHERE pagina = ?", (pagina,))
        con.executemany(
            "INSERT INTO referencias (fuente, pagina, carpetas, titulo, anio, mes, editorial, codigo, modulo, isbn, "
            "autores, seccion, url) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            [(fuente, pagina, json.dumps(carpetas), f["titulo"], f["anio"], f["mes"], f["editorial"], f["codigo"],
              f.get("modulo"), f["isbn"], f["autores"], f.get("seccion"), f.get("url") or url) for f in filas])


# --- Cruzar ---------------------------------------------------------------------------

class Indice:
    """Las referencias de unas carpetas, con índices por ISBN, código y palabras del título."""

    def __init__(self, refs: list[dict]):
        self.refs = refs
        self.por_isbn: dict[str, dict] = {}
        self.por_codigo: dict[str, list[dict]] = {}
        self.por_palabra: dict[str, list[int]] = {}
        for i, r in enumerate(refs):
            r.setdefault("_var", variantes(r["titulo"]))
            if r.get("isbn"):
                self.por_isbn.setdefault(r["isbn"], r)
            if r.get("codigo"):
                self.por_codigo.setdefault(r["codigo"].upper(), []).append(r)
            for w in {w for v in r["_var"] for w in v.split() if len(w) >= 4}:
                self.por_palabra.setdefault(w, []).append(i)

    def candidatas(self, tit: str) -> list[dict]:
        idx = {i for w in tit.split() if len(w) >= 4 for i in self.por_palabra.get(w, ())}
        return [self.refs[i] for i in sorted(idx)]


def _numeros(s: str) -> set[str]:
    return {str(int(n)) for n in re.findall(r"\d+", s)}


def _parecido(r: dict, tit: str, numeros: bool = True) -> float:
    mejor = 0.0
    nums = _numeros(tit)
    for v in r["_var"]:
        if v == tit:
            return 1.0
        if numeros and _numeros(v) != nums:
            continue  # «Pirates of Drinax 2» no es el 1, ni «Update 2023» el de 2016
        sm = difflib.SequenceMatcher(None, v, tit)
        if sm.real_quick_ratio() >= UMBRAL_CON_CODIGO and sm.quick_ratio() >= UMBRAL_CON_CODIGO:
            mejor = max(mejor, sm.ratio())
    return mejor


def anio_de_carpeta(ruta: str) -> int | None:
    """El año entre paréntesis de la carpeta más honda que lo tenga: «JTAS (Mongoose 2019)/x.pdf» -> 2019."""
    for parte in reversed(ruta.split("/")[:-1]):
        m = re.search(r"\([^)]*\b((?:19[5-9]|20[0-4])\d)\)", parte)
        if m:
            return int(m.group(1))
    return None


def emparejar(doc: dict, indice: "Indice | list[dict]") -> tuple[dict, float, str] | None:
    """(referencia, confianza, cómo) para un documento, o None."""
    if not isinstance(indice, Indice):
        indice = Indice(indice)
    isbn = isbn13(doc.get("isbn") or "")
    if isbn and isbn in indice.por_isbn:
        return indice.por_isbn[isbn], 1.0, "isbn"
    tit = titulo_doc(doc["nombre"])
    if len(tit) < 4:
        return None
    codigo = (doc.get("codigo") or doc.get("ext_codigo") or "").upper()
    for r in indice.por_codigo.get(codigo, []) if codigo else []:
        if _parecido(r, tit, numeros=False) >= UMBRAL_CON_CODIGO:
            return r, 0.98, "codigo"
    puntos = []
    for r in indice.candidatas(tit):
        p = _parecido(r, tit)
        if p >= UMBRAL:
            puntos.append((p, r))
    if not puntos:
        return None
    mejor = max(p for p, _ in puntos)
    empatados = [r for p, r in puntos if p >= mejor - 0.02]
    edicion = doc.get("anio") or anio_de_carpeta(doc.get("ruta") or "")  # el año de la edición
    if edicion:
        # Nada de una edición anterior (con margen: lo de Pathfinder empezó en 2007 y la carpeta dice 2009).
        empatados = [r for r in empatados if r["anio"] >= edicion - TOLERANCIA_EDICION]
    if not empatados:
        return None
    anios = {r["anio"] for r in empatados}
    if len(anios) > 1 and not edicion:
        return None  # p. ej. el Player's Handbook sin saber de qué edición
    r = min(empatados, key=lambda x: x["anio"])
    return r, round(mejor, 3), "titulo"


def cruzar(con, ensayo: bool = False) -> dict:
    refs_por_carpeta: dict[str, list[dict]] = {}
    for f in con.execute("SELECT * FROM referencias"):
        r = dict(f)
        r["_var"] = variantes(r["titulo"])
        for c in json.loads(r["carpetas"]):
            refs_por_carpeta.setdefault(c, []).append(r)
    indices = {c: Indice(refs) for c, refs in refs_por_carpeta.items()}
    cuenta = {"isbn": 0, "codigo": 0, "titulo": 0, "docs": 0}
    nuevos = []
    ahora = int(time.time())
    for carpeta, indice in indices.items():
        for f in con.execute("SELECT id, nombre, ruta, anio, isbn, codigo, ext_codigo FROM docs WHERE ruta LIKE ?",
                             (carpeta + "%",)):
            cuenta["docs"] += 1
            res = emparejar(dict(f), indice)
            if not res:
                continue
            r, conf, como = res
            cuenta[como] += 1
            datos = {"publicado": r["anio"], "mes": r["mes"], "editorial": r["editorial"], "titulo": r["titulo"],
                     "codigo": r["codigo"], "url": r["url"], "por": como}
            nuevos.append((f["id"], conf, r["pagina"], json.dumps(datos, ensure_ascii=False), ahora))
            if ensayo and len(nuevos) <= 40:
                print(f"  {f['ruta'][-70:]:70} -> {r['titulo'][:40]} ({r['anio']}, {como} {conf})")
    if not ensayo:
        from .enriquecer import aplicar
        with con:
            con.execute("DELETE FROM extra WHERE fuente = 'ref'")
            con.executemany("INSERT OR REPLACE INTO extra (doc_id, fuente, estado, confianza, ref, datos, creado_en) "
                            "VALUES (?, 'ref', 'auto', ?, ?, ?, ?)", nuevos)
            cuenta["cambiados"] = aplicar(con)
    cuenta["emparejados"] = len(nuevos)
    return cuenta


def main(argv=None) -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("orden", choices=["bajar", "cruzar"])
    ap.add_argument("--solo", choices=["traveller", "wikipedia"])
    ap.add_argument("--ensayo", action="store_true")
    args = ap.parse_args(argv)
    con = conectar(config_mod.cargar().db)
    if args.orden == "bajar":
        if args.solo != "wikipedia":
            print("Traveller Wiki:", bajar_traveller(con), "productos con año")
        if args.solo != "traveller":
            print("Wikipedia:", bajar_wikipedia(con), "productos con año")
    else:
        print(cruzar(con, args.ensayo))


if __name__ == "__main__":
    main()
