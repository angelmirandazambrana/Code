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
UMBRAL_REVISAR = 0.70

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
    ns, palabras = normalizar(sistema).split(), t.split()
    if ns and [normalizar(w) for w in palabras[:len(ns)]] == ns and len(palabras) > len(ns):
        return " ".join(palabras[len(ns):]).strip(" -–:,")
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
                 "wfrp": "warhammer fantasy roleplay", "swd6": "star wars", "l5r": "legend of the five rings"}


def coincide_sistema(sistema: str, ficha: dict) -> bool | None:
    """¿Es del sistema/línea del documento? None si la ficha no dice nada con qué comparar."""
    textos = [*(ficha.get("sistema") or []), *(ficha.get("serie") or [])]
    if not textos:
        return None
    ns = normalizar(ALIAS_SISTEMA.get(sistema.lower(), sistema)).replace("&", " ")
    palabras = {w for w in ns.split() if w not in {"the", "of", "and", "a"}}
    todo = " ".join(normalizar(x).replace("&", " ")
                    for x in [*textos, ficha.get("nombre") or "", *(ficha.get("alternativos") or [])]).split()
    return bool(palabras) and all(w in todo for w in palabras)


def puntuar(doc: dict, ficha: dict, consultas: list[str]) -> float:
    nombres = [ficha["nombre"], *ficha.get("alternativos", [])]
    es_juego = ficha.get("tipo") == "boardgame"
    # Un juego de mesa tiene que llamarse igual: «Star Wars» no es «Clue: Star Wars»…
    p = max((_parecido(c, n, not es_juego) for c in consultas for n in nombres), default=0.0)
    # …salvo el subtítulo: «Mordheim» es «Mordheim: City of the Damned».
    if es_juego and any(":" in n and normalizar(n.split(":")[0]) == normalizar(c)
                        for c in consultas for n in nombres):
        p = max(p, 0.95)
    # Si solo cuadra una parte del título («… - The Invisible World»), hace falta que lo confirme
    # la editorial, el sistema o la edición; si no, que lo mire una persona.
    p_entero = max((_parecido(consultas[0], n, not es_juego) for n in nombres), default=0.0) if consultas else 0.0
    confirmado = False

    version, por = version_que_coincide(doc, ficha)
    if version:  # el mismo ISBN o código de producto que una edición de esa ficha
        p += 0.3 if por == "isbn" else 0.15
        confirmado = True
    paginas = doc.get("paginas")
    if paginas and any(v.get("paginas") and abs(v["paginas"] - paginas) <= 2 for v in ficha.get("versiones") or []):
        p += 0.03

    anio_doc = doc.get("publicado") or doc.get("anio") or None
    anios = [a for a in [ficha.get("anio"), *(v.get("anio") for v in ficha.get("versiones") or [])] if a]
    if anio_doc and anios:
        dif = min(abs(anio_doc - a) for a in anios)
        # En un wargame o juego de mesa el PDF puede ser un suplemento muy posterior al juego: no resta.
        p += 0.06 if dif <= 1 else (-0.15 if dif > 3 and not es_juego else 0)

    editorial = doc.get("editorial_unif") or doc.get("editorial") or doc.get("ext_editorial")
    editoriales = [*(ficha.get("editoriales") or []),
                   *(e for v in ficha.get("versiones") or [] for e in v.get("editoriales") or [])]
    if editorial and editoriales:
        ne = normalizar(editorial)
        if any(ne in normalizar(e) or normalizar(e) in ne for e in editoriales if e):
            p += 0.06
            confirmado = True

    sistema = doc.get("sistema")
    if sistema and not es_juego:
        coincide = coincide_sistema(sistema, ficha)
        if coincide:
            # Poco: muchas veces el sistema ya va en el título que se ha buscado («Star Trek DS9…»).
            p += 0.04
            confirmado = True
        elif coincide is False:
            p -= 0.06  # RPGGeek dice que es de otro sistema
    if p_entero < 0.9 and not confirmado and len(consultas) > 1:
        p -= 0.1
    return round(min(p, 1.0), 3)


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
        for c in cliente.buscar(q, tipo)[:40]:
            candidatos.setdefault(c["id"], c)
        if candidatos and any(_parecido(q, c["nombre"]) >= 0.9 for c in candidatos.values()):
            break
    if not candidatos:
        return {"estado": "sin_resultado", "confianza": 0.0, "ref": None,
                "datos": {"consultas": consultas, "tipo": tipo}}

    # Preselección por nombre y ficha completa de los 5 mejores (una sola petición).
    pre = sorted(candidatos.values(),
                 key=lambda c: -max(_parecido(q, c["nombre"]) for q in consultas))[:5]
    fichas = cliente.fichas([c["id"] for c in pre], tipo)
    puntuados = sorted(
        ({**fichas.get(c["id"], {**c, "tipo": tipo, "editoriales": [], "autores": [], "sistema": [],
                                 "url": URL_FICHA.get(tipo, "").format(c["id"])}),
          "puntos": puntuar(doc, fichas.get(c["id"], c), consultas)} for c in pre),
        key=lambda f: -f["puntos"],
    )
    mejor = puntuados[0]
    # Dos candidatos casi iguales (reediciones, versiones): mejor que lo mire una persona.
    empate = len(puntuados) > 1 and puntuados[1]["puntos"] >= mejor["puntos"] - 0.02
    if mejor["puntos"] >= UMBRAL_AUTO and not empate:
        estado = "auto"
    elif tipo == "boardgame":
        # Wargames y juegos de mesa: sin revisión a mano; o el juego coincide o no se apunta nada.
        estado = "auto" if mejor["puntos"] >= UMBRAL_AUTO else "sin_resultado"
    elif mejor["puntos"] >= UMBRAL_REVISAR:
        estado = "pendiente"
    else:
        estado = "sin_resultado"
    version, por = version_que_coincide(doc, mejor)
    if version and version.get("anio"):
        # Es esa edición concreta: su año es el de este PDF (el de la ficha es el de la primera).
        mejor = {**mejor, "anio_ficha": mejor.get("anio"), "anio": version["anio"], "version_por": por}
    return {
        "estado": estado, "confianza": mejor["puntos"], "ref": str(mejor["id"]),
        "datos": {"tipo": tipo, "consultas": consultas, "elegido": mejor,
                  "candidatos": [{k: f.get(k) for k in ("id", "nombre", "anio", "editoriales", "url", "puntos")}
                                 for f in puntuados]},
    }


def a_json(d: dict) -> str:
    return json.dumps(d, ensure_ascii=False, separators=(",", ":"))
