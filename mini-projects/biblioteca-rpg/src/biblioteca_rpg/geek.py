"""RPGGeek / BoardGameGeek: búsqueda por título y emparejado con los documentos del catálogo.

Usa la API XML2 de BGG (la misma para RPGGeek). Desde 2025 exige un token de aplicación:
se registra gratis en https://boardgamegeek.com/applications y se pone en BGG_TOKEN.
Uso no comercial y citando la fuente. Las consultas van despacio a propósito.
"""

import difflib
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

from .texto_pdf import normalizar

API = os.environ.get("BGG_API", "https://boardgamegeek.com/xmlapi2")
PAUSA = float(os.environ.get("BGG_PAUSA", "2.5"))  # segundos entre peticiones
CADUCIDAD_CACHE = 180 * 86400

UMBRAL_AUTO = 0.92
# Si lo único que falta es puntuación (sin empate, sistema, editorial ni nombre dudosos), basta con
# menos: en las revisiones a mano, de 0,85 a 0,92 la primera opción era la buena 43 de 44 veces.
UMBRAL_AUTO_LIMPIO = 0.85
UMBRAL_REVISAR = 0.70
# Si dos candidatos se llaman igual, el primero tiene que sacar esta ventaja para elegirlo solo.
MARGEN_MISMO_NOMBRE = 0.055

TIPO_POR_CATEGORIA = {"Rol": "rpgitem", "Wargames": "boardgame", "Juegos de mesa": "boardgame"}
URL_FICHA = {"rpgitem": "https://rpggeek.com/rpgitem/{}", "boardgame": "https://boardgamegeek.com/boardgame/{}"}


class SinToken(RuntimeError):
    pass


class Cliente:
    def __init__(self, con, token: str | None = None):
        self.con = con
        self.token = token if token is not None else os.environ.get("BGG_TOKEN", "")
        self._ultima = 0.0
        self.peticiones = 0

    def _get(self, ruta: str, params: dict) -> str:
        url = f"{API}/{ruta}?{urllib.parse.urlencode(params)}"
        fila = self.con.execute("SELECT respuesta, creado_en FROM geek_cache WHERE clave=?", (url,)).fetchone()
        if fila and time.time() - fila[1] < CADUCIDAD_CACHE:
            return fila[0]
        if not self.token:
            raise SinToken("Falta BGG_TOKEN (regístrala en https://boardgamegeek.com/applications)")
        espera = 0
        for intento in range(6):
            pausa = max(PAUSA - (time.time() - self._ultima), 0) + espera
            time.sleep(pausa)
            self._ultima = time.time()
            self.peticiones += 1
            req = urllib.request.Request(url, headers={
                "Authorization": f"Bearer {self.token}", "User-Agent": "biblioteca-rpg/1.0",
            })
            try:
                with urllib.request.urlopen(req, timeout=30) as r:
                    if r.status == 202:  # BGG la está preparando: volver a pedir
                        espera = 5
                        continue
                    cuerpo = r.read().decode("utf-8", "replace")
            except urllib.error.HTTPError as ex:
                if ex.code in (401, 403):
                    raise SinToken(f"BGG rechaza el token ({ex.code}). ¿Ha caducado?") from ex
                if ex.code in (429, 500, 502, 503, 504):
                    espera = 30 * (intento + 1)
                    continue
                raise
            except (urllib.error.URLError, TimeoutError):
                espera = 15 * (intento + 1)
                continue
            with self.con:
                self.con.execute("INSERT OR REPLACE INTO geek_cache VALUES (?, ?, ?)",
                                 (url, cuerpo, int(time.time())))
            return cuerpo
        raise RuntimeError(f"BGG no responde: {url}")

    def buscar(self, texto: str, tipo: str) -> list[dict]:
        raiz = ET.fromstring(self._get("search", {"query": texto, "type": tipo}))
        res = []
        for it in raiz.findall("item"):
            nombre = it.find("name")
            anio = it.find("yearpublished")
            res.append({
                "id": int(it.get("id")),
                "nombre": nombre.get("value") if nombre is not None else "",
                "anio": _entero(anio.get("value")) if anio is not None else None,
            })
        return res

    def fichas(self, ids: list[int], tipo: str) -> dict[int, dict]:
        if not ids:
            return {}
        # Con las versiones: ISBN, código, páginas y año de cada edición publicada (misma petición).
        raiz = ET.fromstring(self._get("thing", {"id": ",".join(map(str, ids)), "type": tipo, "versions": "1"}))
        return {int(it.get("id")): leer_ficha(it, tipo) for it in raiz.findall("item")}


def _entero(v) -> int | None:
    try:
        n = int(v)
        return n if n > 0 else None
    except (TypeError, ValueError):
        return None


def leer_ficha(it, tipo: str) -> dict:
    nombres = [n.get("value") for n in it.findall("name")]
    principal = next((n.get("value") for n in it.findall("name") if n.get("type") == "primary"),
                     nombres[0] if nombres else "")
    enlaces: dict[str, list[str]] = {}
    for ln in it.findall("link"):
        enlaces.setdefault(ln.get("type", ""), []).append(ln.get("value", ""))

    def de(*fragmentos):
        return [v for t, vs in enlaces.items() if any(f in t for f in fragmentos) for v in vs]

    anio = it.find("yearpublished")
    miniatura = it.find("thumbnail")
    versiones = []
    bloque = it.find("versions")
    for v in (list(bloque) if bloque is not None else [])[:30]:
        def val(etiqueta, v=v):
            e = v.find(etiqueta)
            return (e.get("value") or "").strip() if e is not None else ""
        versiones.append({
            "anio": _entero(val("yearpublished")), "isbn": [x for x in (val("isbn10"), val("isbn13")) if x],
            "codigo": val("productcode"), "paginas": _entero(val("pagecount")),
            "editoriales": [ln.get("value", "") for ln in v.findall("link") if "publisher" in ln.get("type", "")],
        })
    return {
        "id": int(it.get("id")),
        "tipo": tipo,
        "nombre": principal,
        "alternativos": [n for n in nombres if n != principal][:10],
        "anio": _entero(anio.get("value")) if anio is not None else None,
        "editoriales": de("publisher"),
        "autores": de("designer"),
        "sistema": de("rpgsystem") or [v for v in enlaces.get("rpg", [])],
        "serie": de("series", "family"),
        "genero": de("genre", "category"),
        "versiones": versiones,
        "miniatura": (miniatura.text or "").strip() if miniatura is not None else "",
        "url": URL_FICHA.get(tipo, URL_FICHA["rpgitem"]).format(it.get("id")),
    }


# --- Emparejado ---------------------------------------------------------------

# Carpetas que solo agrupan juegos (no son un juego) en Juegos de mesa y Wargames.
AGRUPAN_JUEGOS = {"Card games", "Board games", "_Sueltos", "Days of Wonder", "Milton Bradley", "Other Publishers"}


def _nombre_de_juego(texto: str) -> str:
    """'Blue Moon City - Cards' -> 'Blue Moon City'; 'Photosynthesis PnP' -> 'Photosynthesis'."""
    t = re.sub(r"\s*[\(\[][^)\]]*[\)\]]", "", texto)            # (Z-Man), [multi], (166115394)
    t = re.split(r"\s+-\s+|:\s", t)[0]                         # lo que va tras " - " es el producto
    t = re.sub(r"\s*(&|and)\s+expansions?$|\s+pnp$|\s+print (and|&) play$", "", t, flags=re.I)
    return t.strip() or texto


# Carpetas de Wargames que no son juegos (libros de historia, tablas) o que no dicen el juego.
NO_SON_JUEGOS = {"_Reference", "Tables & Charts"}
SIN_JUEGO = {"Solo", "Miniaturas", "Osprey Wargaming"}
# Cómo llama BGG a juegos que en la biblioteca van con siglas o nombre corto.
ALIAS_BGG = {
    "asl": "Advanced Squad Leader", "warhammer 40k": "Warhammer 40,000", "wh40k": "Warhammer 40,000",
    "warhammer fantasy": "Warhammer Fantasy Battle", "age of sigmar": "Warhammer Age of Sigmar",
    "kill team": "Warhammer 40,000: Kill Team", "the old world": "Warhammer: The Old World",
}


def _es_carpeta_de_tipo(seg: str) -> bool:
    from .rutas import TIPOS
    return seg.lower() in {"misc", "rules"} or any(rx.search(seg) for rx, _ in TIPOS)


def juego_para_bgg(carpeta: str, titulo: str) -> str:
    """Nombre del juego para buscar en BoardGameGeek, sacado de la ruta o, si no, del título."""
    p = carpeta.split("/")
    juego = None
    if p[0] == "Juegos de mesa":
        juego = next((s for s in p[1:] if s not in AGRUPAN_JUEGOS and not s.startswith("_")), None)
    elif p[0] == "Wargames" and len(p) > 1 and not p[1].startswith("_") and p[1] not in SIN_JUEGO:
        # Wargames/<Editorial>/<Juego>/… (o Wargames/Other Publishers/<Editorial>/<Juego>/…)
        resto = [s for s in p[1:] if s not in AGRUPAN_JUEGOS]
        if (len(resto) > 1 and not resto[1].startswith("_") and resto[1] not in SIN_JUEGO
                and not _es_carpeta_de_tipo(resto[1])):
            juego = resto[1]
    nombre = _nombre_de_juego(juego or titulo)
    if juego:
        nombre = re.split(r"\s+&\s+", nombre)[0]  # "Panzer Leader & Panzer Blitz" -> "Panzer Leader"
    return ALIAS_BGG.get(normalizar(nombre), nombre)


def no_es_juego(carpeta: str) -> bool:
    p = (carpeta or "").split("/")
    return p[0] == "Wargames" and len(p) > 1 and p[1] in NO_SON_JUEGOS



RE_RUIDO = re.compile(
    r"\[[^\]]*\]|\([^)]*\)|\{[^}]*\}|\b(?:v|ver|version)\s?\d+(?:\.\d+)*\b|\b\d+(?:st|nd|rd|th) print(?:ing)?\b|"
    r"\b(?:OEF|OCR|hq|hd|bookmarked|errata|final|web|print|ebook|pdf)\b",
    re.I,
)


def limpiar_titulo(titulo: str) -> str:
    t = titulo.replace("_", " ")
    t = RE_RUIDO.sub(" ", t)
    t = re.sub(r"\s+", " ", t).strip(" -–.,")
    return t


# Partes del nombre que solas no dicen qué producto es ("TMB - Rulebook" -> no buscar "Rulebook").
GENERICAS = {"rulebook", "rules", "rule book", "core rules", "core rulebook", "map", "maps", "cards", "counters",
             "errata", "faq", "reference", "player aid", "player aids", "quick reference", "quickstart",
             "character sheet", "character sheets", "scenario", "scenarios", "playbook", "rules reference",
             "living rules", "tokens", "insert", "box", "cover", "manual", "booklet", "charts", "tables"}


# Código de producto al principio ("JG48 Wilderlands…", "TSE2 The Sting…"): BGG no lo tiene en el nombre.
RE_CODIGO_INICIAL = re.compile(r"^[A-Z]{1,4}[ #.-]?\d{1,5}[A-Za-z]?(?:\s*[-–]\s*|\s+)(?=\S)")
# Coletillas que no forman parte del nombre en BGG ("Story Engine Corebook" -> "Story Engine").
RE_COLETILLA = re.compile(r"\s+(?:core ?book|core rule ?book|core rules|rule ?book|quick ?start(?: rules)?|"
                          r"fast[ -]play(?: rules)?|pregen(?:erated)? characters)$", re.I)


def _sin_codigo(t: str, codigo: str | None) -> str:
    if codigo:
        m = re.match(r"([A-Za-z]+)\W*(\d+\w*)", codigo)
        if m:
            t = re.sub(rf"\b{re.escape(m[1])}[\s#_.-]?{re.escape(m[2])}\b", " ", t, flags=re.I)
    t = re.sub(r"\s+", " ", t).strip(" -–.,")
    while RE_CODIGO_INICIAL.match(t):
        t = RE_CODIGO_INICIAL.sub("", t, count=1)
    return t


def _sin_sistema(t: str, sistema: str | None) -> str | None:
    """«Dark Dungeon Samaris» con sistema «Dark Dungeon» -> «Samaris»."""
    if not sistema:
        return None
    ns, palabras = normalizar(sistema), t.split()
    # Palabra a palabra, normalizando lo acumulado («D&D» es «d and d»: una palabra que son tres).
    for i in range(1, len(palabras)):
        hecho = normalizar(" ".join(palabras[:i]))
        if hecho == ns:
            return " ".join(palabras[i:]).strip(" -–:,") or None
        if not ns.startswith(hecho):
            break
    return None


def variantes(titulo: str, sistema: str | None = None, codigo: str | None = None) -> list[str]:
    """Consultas a probar, de la más precisa a la más amplia (BGG pide el nombre casi literal)."""
    t = limpiar_titulo(titulo)
    if " " not in t and t.count("-") >= 2:  # "the-good-the-bad-and-the-troll"
        t = t.replace("-", " ")
    t = RE_COLETILLA.sub("", _sin_codigo(t, codigo))
    partes = [RE_COLETILLA.sub("", _sin_codigo(p.strip(), None))
              for p in re.split(r"\s+[-–]\s+|:\s+", t) if p.strip()]
    out = [t, _sin_sistema(t, sistema)]
    if len(partes) > 1:
        out.append(" ".join(partes[-2:]) if len(partes) > 2 else partes[-1])
        out.append(partes[-1])
    vistos, res = set(), []
    for v in out:
        k = normalizar(v or "")
        if len(k) >= 3 and k not in vistos and k not in GENERICAS:
            vistos.add(k)
            res.append(v)
    # "Savage Worlds - Fort Griffin": la parte que es solo el sistema no se busca… salvo que no quede
    # otra cosa ("Story Engine Corebook" es el básico de Story Engine).
    ns = normalizar(sistema) if sistema else None
    otras = [v for v in res if normalizar(v) != ns]
    return (otras or res)[:4]


def _parecido(a: str, b: str, parcial: bool = True) -> float:
    na, nb = normalizar(a), normalizar(b)
    if not na or not nb:
        return 0.0
    na = re.sub(r"^(the|a|an) ", "", na)
    nb = re.sub(r"^(the|a|an) ", "", nb)
    if na == nb:
        return 1.0
    ratio = difflib.SequenceMatcher(None, na, nb).ratio()
    ta, tb = set(na.split()), set(nb.split())
    # Uno contiene todas las palabras del otro ("Tomb of Horrors" ~ "S1 Tomb of Horrors").
    if parcial and ta and tb and (ta <= tb or tb <= ta):
        cubierto = min(len(ta), len(tb)) / max(len(ta), len(tb))
        ratio = max(ratio, 0.75 + 0.2 * cubierto)
    return ratio


def _isbn13(x: str) -> str:
    d = re.sub(r"[^0-9Xx]", "", x or "").upper()
    if len(d) == 10:
        base = "978" + d[:9]
        d = base + str((10 - sum(int(c) * (1 if i % 2 == 0 else 3) for i, c in enumerate(base)) % 10) % 10)
    return d


def version_que_coincide(doc: dict, ficha: dict) -> tuple[dict | None, str | None]:
    """La edición de BGG que es este PDF, por ISBN o por código de producto."""
    isbn = _isbn13(doc.get("isbn") or "")
    codigo = re.sub(r"\D", "", doc.get("codigo") or doc.get("ext_codigo") or "").lstrip("0")
    versiones = ficha.get("versiones") or []
    for v in versiones:
        if len(isbn) == 13 and any(_isbn13(i) == isbn for i in v.get("isbn") or []):
            return v, "isbn"
    for v in versiones:
        if len(codigo) >= 3 and re.sub(r"\D", "", v.get("codigo") or "").lstrip("0") == codigo:
            return v, "codigo"
    return None, None


# Nombres cortos de sistemas tal como van en las carpetas -> como los nombra RPGGeek.
ALIAS_SISTEMA = {"d&d": "dungeons dragons", "ad&d": "advanced dungeons dragons", "coc": "call of cthulhu",
                 "wfrp": "warhammer fantasy roleplay", "swd6": "star wars", "l5r": "legend of the five rings",
                 # en RPGGeek: «Pathfinder 1E Compatible Product», «Alternity», «DramaSystem»
                 "pathfinder 3rd party": "pathfinder", "star drive": "alternity", "hillfolk": "dramasystem"}


def coincide_sistema(sistema: str, ficha: dict) -> bool | None:
    """¿Es del sistema/línea del documento? None si la ficha no dice nada con qué comparar.

    Se mira el sistema y la serie de la ficha, no su nombre: «The Bleeding Edge» (de Candela Obscura)
    no es del juego «Bleeding Edge» por llamarse igual."""
    textos = [*(ficha.get("sistema") or []), *(ficha.get("serie") or [])]
    if not textos:
        return None
    todo = " ".join(normalizar(x).replace("&", " ") for x in textos).split()
    # Con el nombre largo o con el corto: RPGGeek dice tanto «Dungeons & Dragons» como «D&D 5th Edition».
    for forma in {ALIAS_SISTEMA.get(sistema.lower(), sistema), sistema}:
        palabras = {w for w in normalizar(forma).replace("&", " ").split() if w not in {"the", "of", "and", "a"}}
        if palabras and all(w in todo for w in palabras):
            return True
    return False


# Palabras que no distinguen una editorial de otra («Sanguine Games» = «Sanguine Productions»).
EDITORIAL_COMUN = set("""games game publishing publications publication press productions production studio
studios entertainment inc llc ltd co company design designs media books edition editions the and of""".split())  # noqa: SIM905


def misma_editorial(a: str, b: str) -> bool:
    na, nb = normalizar(a), normalizar(b)
    if not na or not nb:
        return False
    if na in nb or nb in na:
        return True
    pa = {w for w in na.split() if w not in EDITORIAL_COMUN and len(w) > 2}
    pb = {w for w in nb.split() if w not in EDITORIAL_COMUN and len(w) > 2}
    return bool(pa & pb)


# Añadidos de RPGGeek que solo dicen el sistema o la edición: «(HERO)», «(D20 3.5)», «(5e)»,
# «(HERO System 5)», «(3rd Edition)». No los de otra cosa: «(Kickstarter Preview Edition)».
RE_ETIQUETA = re.compile(
    r"\s*\((?![^)]*(preview|kickstarter|ashcan|quick ?start|beta|playtest|demo))[^)]*"
    r"(\bsystem\b|edition|\bed\b\.?|\b\d(st|nd|rd|th)?e\b|d20|3\.5|\bhero\b|\bharp\b|rolemaster|gurps|"
    r"savage|pathfinder|\bfate\b|\bosr\b|\bbx\b|revised)[^)]*\)\s*$", re.I)


def _lineas(ficha: dict) -> set[str]:
    return {normalizar(x) for x in [*(ficha.get("sistema") or []), *(ficha.get("serie") or [])] if x}


def perfil_vecinos(elegidos: list[tuple[dict, int]]) -> dict:
    """Editoriales y líneas (sistema/serie de RPGGeek) de los emparejados ya confirmados en la misma
    carpeta: (ficha elegida, peso) con peso 2 si lo aceptaste tú y 1 si fue automático. Cuenta lo
    que pesa 2 o más: una confirmación tuya o dos automáticos."""
    eds: dict[str, int] = {}
    lineas: dict[str, int] = {}
    for f, peso in elegidos:
        for e in {normalizar(x) for x in f.get("editoriales") or [] if x}:
            eds[e] = eds.get(e, 0) + peso
        for linea in _lineas(f):
            lineas[linea] = lineas.get(linea, 0) + peso
    return {"editoriales": {e for e, n in eds.items() if n >= 2}, "lineas": {x for x, n in lineas.items() if n >= 2}}


def coincide_vecinos(perfil: dict | None, ficha: dict) -> int:
    """Cuántas cosas comparte con lo ya confirmado en la carpeta: 0, 1 (editorial o línea) o 2 (las dos)."""
    if not perfil:
        return 0
    ed = any(misma_editorial(a, b) for a in ficha.get("editoriales") or [] if a for b in perfil["editoriales"])
    linea = bool(_lineas(ficha) & perfil["lineas"])
    return int(ed) + int(linea)


SIN_EDITORIAL = {"web published", "self published", "unknown", "uncredited"}


def coincide_editorial(doc: dict, ficha: dict) -> bool | None:
    """¿La editorial del documento (de la ruta o del PDF) es una de las de la ficha o sus ediciones?"""
    nuestras = {e for e in (doc.get("editorial_unif"), doc.get("editorial"), doc.get("ext_editorial")) if e}
    # «(Web published)» o «(Self-Published)» no dicen de quién es: ni suma ni resta.
    suyas = [e for e in [*(ficha.get("editoriales") or []),
                         *(e for v in ficha.get("versiones") or [] for e in v.get("editoriales") or [])]
             if e and normalizar(e) not in SIN_EDITORIAL]
    if not nuestras or not suyas:
        return None
    return any(misma_editorial(a, b) for a in nuestras for b in suyas)


def _sin_parentesis(n: str) -> str:
    """«The Echoes of Heaven Bestiary (HERO)» -> «The Echoes of Heaven Bestiary»: la etiqueta de
    sistema que pone RPGGeek para distinguir versiones no hace a una más parecida al título que otra."""
    return RE_ETIQUETA.sub("", n).strip() or n


def titulo_de_ficha(elegido: dict, titulo_archivo: str = "") -> str | None:
    """El nombre del libro según su ficha de RPGGeek, para mostrarlo en vez del del fichero.

    De los nombres de la ficha (el principal y los alternativos), el más parecido al del fichero:
    «Genius Guide to Loot 4 Less 4» -> «The Genius Guide to Loot 4 Less: Volume 4—Fantastic Footwear»
    y no solo «Fantastic Footwear». Sin la etiqueta de sistema («… (Pathfinder)»). Las fichas de
    BoardGameGeek son del juego, no de cada PDF (reglas, mapas…): de ahí no se toma."""
    if elegido.get("tipo") == "boardgame" or not elegido.get("nombre"):
        return None
    nombres = [_sin_parentesis(n) for n in [elegido["nombre"], *(elegido.get("alternativos") or [])] if n]
    base = limpiar_titulo(titulo_archivo)
    if not base:
        return nombres[0]
    t = max(nombres, key=lambda n: _parecido(base, n))  # en empate, el principal (va primero)
    # La serie, si el fichero la nombra y la ficha no: «Monster Classes - Fey» -> «Monster Classes: Fey».
    nb, nt = f" {normalizar(base)} ", f" {normalizar(t)} "
    for serie in elegido.get("serie") or []:
        ns = normalizar(_sin_parentesis(serie))
        if ns and f" {ns} " in nb and f" {ns} " not in nt:
            return f"{_sin_parentesis(serie)}: {t}"
    return t


def es_parte_del_nombre(consulta: str, nombre: str) -> bool:
    """«Thin Blue Line» en «Thin Blue Line: Defenders of the Colonies», «A is for Assassin» en «Strike to
    the Heart: A is for Assassin»: el título es el principio del nombre o una de sus partes. No lo es
    «Behind Lines» en «Behind Enemy Lines»."""
    q = re.sub(r"^(the|a|an) ", "", normalizar(consulta))
    if len(q.split()) < 2:
        return False
    partes = [re.sub(r"^(the|a|an) ", "", normalizar(x)) for x in re.split(r"[:(]", nombre)]
    entero = re.sub(r"^(the|a|an) ", "", normalizar(nombre))
    return q in partes or entero.startswith(q + " ") or entero.endswith(" " + q)


def parecido_nombre(consultas: list[str], ficha: dict) -> float:
    """Parecido del nombre sin la etiqueta de sistema: para ver si dos candidatos «se llaman igual»."""
    nombres = [ficha.get("nombre") or "", *(ficha.get("alternativos") or [])]
    return max((_parecido(q, _sin_parentesis(n)) for q in consultas for n in nombres), default=0.0)


RE_ACCESORIO = re.compile(r"\b(errata|erratas|faq|corrections?|update|actualizaci[oó]n|web enhancement|"
                          r"character sheets?|record sheets?|preview|teaser|sampler)\b", re.I)


def puntuar(doc: dict, ficha: dict, consultas: list[str]) -> float:
    """Parecido del documento con una ficha. Puede pasar de 1: así se ordenan los que empatarían
    a 1 (al guardarlo se recorta)."""
    nombres = [ficha["nombre"], *ficha.get("alternativos", [])]
    es_juego = ficha.get("tipo") == "boardgame"
    sistema = doc.get("sistema")
    coincide = coincide_sistema(sistema, ficha) if sistema and not es_juego else None
    # Un juego de mesa tiene que llamarse igual: «Star Wars» no es «Clue: Star Wars»…
    p = max((_parecido(c, n, not es_juego) for c in consultas for n in nombres), default=0.0)
    # …salvo el subtítulo: «Mordheim» es «Mordheim: City of the Damned».
    # En rol también: «Bleeding Edge» es «Bleeding Edge: High-Tech Low-Life Role-Play».
    if any(":" in n and normalizar(n.split(":")[0]) == normalizar(c) for c in consultas[:1] for n in nombres):
        p = max(p, 0.95)
    # Si solo cuadra una parte del título («… - The Invisible World»), hace falta que lo confirme
    # la editorial, el sistema o la edición; si no, que lo mire una persona.
    # El título entero es la primera consulta… o esa sin el sistema delante («Star Drive Arms &
    # Equipment Guide» es «Arms & Equipment Guide» de la línea Star Drive).
    enteras = consultas[:1]
    ns = normalizar(doc.get("sistema") or "")
    if consultas and ns and coincide is not False:  # y si RPGGeek no dice que es de otro sistema
        enteras += [q for q in consultas[1:] if normalizar(f"{ns} {q}") == normalizar(consultas[0])]
    p_entero = max((_parecido(q, n, not es_juego) for q in enteras for n in nombres), default=0.0)
    confirmado = False

    version, por = version_que_coincide(doc, ficha)
    if version:  # el mismo ISBN o código de producto que una edición de esa ficha
        p += 0.3 if por == "isbn" else 0.15
        confirmado = True
    # Páginas (las del PDF, que no vienen de RPGGeek): las mismas casi lo confirman.
    paginas = doc.get("paginas")
    pags = [v["paginas"] for v in ficha.get("versiones") or [] if v.get("paginas")]
    # Con pocas páginas coincide cualquiera (hay cientos de PDF de 3 páginas).
    if paginas and paginas >= 24 and paginas in pags:
        p += 0.06
    elif paginas and paginas >= 8 and any(abs(x - paginas) <= 2 for x in pags):
        p += 0.03

    anio_doc = doc.get("publicado") or doc.get("anio") or None
    anios = [a for a in [ficha.get("anio"), *(v.get("anio") for v in ficha.get("versiones") or [])] if a]
    if anio_doc and anios:
        dif = min(abs(anio_doc - a) for a in anios)
        # En un wargame o juego de mesa el PDF puede ser un suplemento muy posterior al juego: no resta.
        p += 0.06 if dif <= 1 else (-0.15 if dif > 3 and not es_juego else 0)

    ed = coincide_editorial(doc, ficha)
    if ed:
        p += 0.06
        confirmado = True
    elif ed is False:
        p -= 0.06  # de otra editorial (o publicado por un aficionado en la web)

    if sistema and not es_juego:
        if coincide:
            # Poco: muchas veces el sistema ya va en el título que se ha buscado («Star Trek DS9…»).
            p += 0.04
            confirmado = True
        elif coincide is False:
            p -= 0.06  # RPGGeek dice que es de otro sistema
    # Lo que ya has confirmado en la misma carpeta (misma editorial y misma línea de RPGGeek).
    vecinos = coincide_vecinos(doc.get("vecinos"), ficha)
    if vecinos:  # suma, pero no basta para dar por bueno un título que solo cuadra en parte
        p += 0.08 if vecinos == 2 else 0.03
    if p_entero < 0.9 and not confirmado and len(consultas) > 1:
        p -= 0.1
    # Un nombre de una palabra («Bard», «Threads», «Polaris») lo llevan muchas cosas: sin nada que lo
    # confirme no basta para darlo por bueno.
    if not confirmado and not es_juego and max(len(normalizar(n).split()) for n in nombres if n) <= 1:
        p -= 0.05
    return round(p, 3)


def emparejar(cliente: Cliente, doc: dict) -> dict:
    """Busca el documento y devuelve {'estado', 'confianza', 'ref', 'datos'} para la tabla extra."""
    tipo = TIPO_POR_CATEGORIA.get(doc.get("categoria") or "", "rpgitem")
    # Lo que ya vino de un emparejado anterior con BGG no sirve para confirmarlo (sería circular).
    origen = json.loads(doc.get("origen") or "{}")
    doc = {**doc, **({"publicado": None} if origen.get("publicado") == "geek" else {}),
           **({"ext_editorial": None, "editorial_unif": doc.get("editorial")}
              if origen.get("editorial") == "geek" else {})}
    if tipo == "boardgame":
        # BGG tiene una ficha por juego, no por PDF: se busca el juego de la carpeta.
        consultas = [juego_para_bgg(doc.get("carpeta") or "", doc.get("titulo") or "")]
        if normalizar(consultas[0]) in GENERICAS or no_es_juego(doc.get("carpeta") or ""):
            consultas = []
    else:
        consultas = variantes(doc["titulo"], doc.get("sistema"), doc.get("codigo") or doc.get("ext_codigo"))
    candidatos: dict[int, dict] = {}
    for q in consultas:
        # BGG los da por orden alfabético: con títulos comunes el bueno puede estar más allá del 40.
        # Solo con el título entero: con las consultas cortas de recurso («2nd Edition») se colarían
        # cientos de «X (2nd Edition)».
        encontrados = cliente.buscar(q, tipo)
        if len(encontrados) > 40 and q == consultas[0]:
            encontrados = sorted(encontrados, key=lambda c: -max(
                _parecido(q, n) for n in {c["nombre"], _sin_parentesis(c["nombre"])}))
        for c in encontrados[:40]:
            candidatos.setdefault(c["id"], c)
        if candidatos and any(_parecido(q, c["nombre"]) >= 0.9 for c in candidatos.values()):
            break
    if not candidatos:
        return {"estado": "sin_resultado", "confianza": 0.0, "ref": None,
                "datos": {"consultas": consultas, "tipo": tipo}}

    # Preselección por nombre y ficha completa de los 5 mejores (una sola petición). La etiqueta de
    # versión que añade RPGGeek («Dungeon Master's Guide (D&D 5e)») no cuenta, y entre nombres igual de
    # parecidos van primero los del año más cercano al del documento.
    anio = doc.get("publicado") or doc.get("anio") or None

    def orden(c: dict) -> tuple[float, float, int]:
        entero = max(_parecido(q, c["nombre"]) for q in consultas)
        nombre = max(entero, *(_parecido(q, _sin_parentesis(c["nombre"])) for q in consultas))
        lejos = abs(c["anio"] - anio) if anio and c.get("anio") else 0
        # con la etiqueta incluida («Mutant Chronicles (2nd Edition)») desempata antes que el año
        return -round(nombre, 2), -round(entero, 2), lejos

    pre = sorted(candidatos.values(), key=orden)[:5]
    fichas = cliente.fichas([c["id"] for c in pre], tipo)
    puntuados = sorted(
        ({**fichas.get(c["id"], {**c, "tipo": tipo, "editoriales": [], "autores": [], "sistema": [],
                                 "url": URL_FICHA.get(tipo, "").format(c["id"])}),
          "puntos": puntuar(doc, fichas.get(c["id"], c), consultas)} for c in pre),
        key=lambda f: -f["puntos"],
    )
    mejor = puntuados[0]
    # Dos candidatos casi iguales (reediciones, versiones): mejor que lo mire una persona. Si se
    # llaman igual («Bard», «Threads»), hace falta que los datos del PDF marquen bien la diferencia.
    empate = False
    if len(puntuados) > 1:
        otro = puntuados[1]
        mismo_nombre = parecido_nombre(consultas, otro) >= parecido_nombre(consultas, mejor) - 0.01
        ventaja = mejor["puntos"] - otro["puntos"]
        empate = (ventaja < (MARGEN_MISMO_NOMBRE if mismo_nombre else 0.02)
                  # los dos casi perfectos: hace falta una ventaja clara («Ghostwalk» 1,18 frente a
                  # «Ghostwalk Web Enhancement» 1,00 sí la tiene)
                  or (min(otro["puntos"], 1.0) >= min(mejor["puntos"], 1.0) - 0.02 and ventaja < 0.1))
    # Solo se ha podido buscar el sistema («All Flesh Must Be Eaten» para «… Errata»): será otra cosa
    # de esa línea, no el básico; que lo mire una persona.
    solo_sistema = (bool(consultas) and normalizar(consultas[0]) == normalizar(doc.get("sistema") or "")
                    and len(normalizar(RE_COLETILLA.sub("", limpiar_titulo(doc.get("titulo") or ""))).split())
                    > len(normalizar(consultas[0]).split()))
    # Sabemos la editorial y no es la de la ficha: que lo mire una persona.
    # Salvo que lo confirmen el sistema o lo ya emparejado en la carpeta: la carpeta puede decir la
    # dueña del juego (R. Talsorian) y el módulo ser de otra (Atlas Games).
    otra_editorial = (tipo != "boardgame" and coincide_editorial(doc, mejor) is False
                      and not coincide_vecinos(doc.get("vecinos"), mejor)
                      and not (doc.get("sistema") and coincide_sistema(doc["sistema"], mejor)))
    # Una errata, una actualización o unas hojas sueltas no son el libro («Oriental Adventures Errata»);
    # y con el nombre a medias («Behind Lines» / «Behind Enemy Lines») no basta lo de alrededor
    # (editorial, año, carpeta): solo el ISBN o el código de producto. En wargames y juegos de mesa sí
    # vale: la ficha de BGG es la del juego y la errata o el suplemento van con él.
    dudas: list[str] = []
    if tipo != "boardgame":
        nombres_mejor = [mejor.get("nombre") or "", *(mejor.get("alternativos") or [])]
        if RE_ACCESORIO.search(doc.get("titulo") or "") and not any(RE_ACCESORIO.search(n) for n in nombres_mejor):
            dudas.append("accesorio")
        # Un nombre de una palabra («Bard», «Polaris», «Nemesis») lo llevan muchas cosas: el sistema no
        # basta, hace falta la editorial, el ISBN o el código.
        una_palabra = max(len(re.sub(r"^(the|a|an) ", "", normalizar(n)).split()) for n in nombres_mejor if n) <= 1
        if (una_palabra and not coincide_editorial(doc, mejor) and not version_que_coincide(doc, mejor)[0]
                and coincide_vecinos(doc.get("vecinos"), mejor) < 2):
            dudas.append("una_palabra")
        # El PDF es de un sistema y la ficha de otro («robots» de Exodus / «Robots» de Steampower), y ni la
        # editorial ni lo ya emparejado en la carpeta dicen otra cosa: un nombre corriente, no este libro.
        if (doc.get("sistema") and _lineas(mejor) and not coincide_sistema(doc["sistema"], mejor)
                and not coincide_editorial(doc, mejor) and not coincide_vecinos(doc.get("vecinos"), mejor)):
            dudas.append("otro_sistema")
        if (parecido_nombre(consultas, mejor) < 0.9 and not version_que_coincide(doc, mejor)[0]
                and not any(es_parte_del_nombre(q, n) for q in consultas for n in nombres_mejor)):
            dudas.append("nombre_a_medias")
    # Por qué no se elige solo (se guarda para poder afinar las reglas con lo que decides a mano).
    otros = empate or solo_sistema or otra_editorial or dudas
    # y algo de fuera del nombre lo confirma (editorial, sistema, la carpeta, la edición o el año)
    anio_doc = doc.get("publicado") or doc.get("anio")
    confirmado = (coincide_editorial(doc, mejor) or (doc.get("sistema") and coincide_sistema(doc["sistema"], mejor))
                  or coincide_vecinos(doc.get("vecinos"), mejor) or version_que_coincide(doc, mejor)[0]
                  or (anio_doc and mejor.get("anio") and abs(anio_doc - mejor["anio"]) <= 1))
    umbral = UMBRAL_AUTO if otros or tipo == "boardgame" or not confirmado else UMBRAL_AUTO_LIMPIO
    motivos = [m for m, si in (("puntos", mejor["puntos"] < umbral), ("empate", empate),
                               ("solo_sistema", solo_sistema), ("otra_editorial", otra_editorial)) if si] + dudas
    if not motivos:
        estado = "auto"
    elif tipo == "boardgame":
        # Wargames y juegos de mesa: sin revisión a mano; o el juego coincide o no se apunta nada.
        estado = "auto" if mejor["puntos"] >= UMBRAL_AUTO else "sin_resultado"
    elif mejor["puntos"] >= UMBRAL_REVISAR:
        estado = "pendiente"
    else:
        estado = "sin_resultado"
    for f in puntuados:  # lo que se guarda y se enseña, de 0 a 1
        f["raw"], f["puntos"] = f["puntos"], min(f["puntos"], 1.0)
    version, por = version_que_coincide(doc, mejor)
    if version and version.get("anio"):
        # Es esa edición concreta: su año es el de este PDF (el de la ficha es el de la primera).
        mejor = {**mejor, "anio_ficha": mejor.get("anio"), "anio": version["anio"], "version_por": por}
    return {
        "estado": estado, "confianza": mejor["puntos"], "ref": str(mejor["id"]),
        "datos": {"tipo": tipo, "consultas": consultas, "elegido": mejor,
                  **({"motivos": motivos} if estado != "auto" else {}),
                  "candidatos": [{k: f.get(k) for k in ("id", "nombre", "anio", "editoriales", "url", "puntos")}
                                 for f in puntuados]},
    }


def a_json(d: dict) -> str:
    return json.dumps(d, ensure_ascii=False, separators=(",", ":"))
