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
        raiz = ET.fromstring(self._get("thing", {"id": ",".join(map(str, ids)), "type": tipo}))
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


def juego_para_bgg(carpeta: str, titulo: str) -> str:
    """Nombre del juego para buscar en BoardGameGeek, sacado de la ruta o, si no, del título."""
    p = carpeta.split("/")
    if p[0] == "Juegos de mesa":
        juego = next((s for s in p[1:] if s not in AGRUPAN_JUEGOS and not s.startswith("_")), None)
    elif p[0] == "Wargames" and len(p) > 2 and not p[1].startswith("_"):
        juego = p[2] if p[2] != "_Por clasificar" else None   # Wargames/<Editorial>/<Juego>/…
    else:
        juego = None
    return _nombre_de_juego(juego or titulo)



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


def variantes(titulo: str, sistema: str | None = None) -> list[str]:
    """Consultas a probar, de la más precisa a la más amplia."""
    t = limpiar_titulo(titulo)
    partes = [p.strip() for p in re.split(r"\s+[-–]\s+|:\s+", t) if p.strip()]
    out = [t]
    if len(partes) > 1:
        out.append(" ".join(partes[-2:]) if len(partes) > 2 else partes[-1])
        out.append(partes[-1])
    if sistema:
        # "Savage Worlds - Fort Griffin" -> "Fort Griffin" ya está; quitar también el sistema al principio.
        ns = normalizar(sistema)
        out = [v for v in out if normalizar(v) != ns]
    vistos, res = set(), []
    for v in out:
        k = normalizar(v)
        if len(k) >= 3 and k not in vistos and k not in GENERICAS:
            vistos.add(k)
            res.append(v)
    return res[:3]


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


def puntuar(doc: dict, ficha: dict, consultas: list[str]) -> float:
    nombres = [ficha["nombre"], *ficha.get("alternativos", [])]
    # Un juego de mesa tiene que llamarse igual: «Star Wars» no es «Clue: Star Wars».
    parcial = ficha.get("tipo") != "boardgame"
    p = max((_parecido(c, n, parcial) for c in consultas for n in nombres), default=0.0)
    # Si solo cuadra una parte del título («… - The Invisible World»), hace falta que lo confirme
    # la editorial o el sistema; si no, que lo mire una persona.
    p_entero = max((_parecido(consultas[0], n, parcial) for n in nombres), default=0.0) if consultas else 0.0
    confirmado = False
    anio_doc = doc.get("publicado") or None
    if anio_doc and ficha.get("anio"):
        dif = abs(anio_doc - ficha["anio"])
        p += 0.06 if dif <= 1 else (-0.15 if dif > 3 else 0)
    editorial = doc.get("editorial") or doc.get("ext_editorial")
    if editorial and ficha.get("editoriales"):
        ne = normalizar(editorial)
        if any(ne in normalizar(e) or normalizar(e) in ne for e in ficha["editoriales"]):
            p += 0.06
            confirmado = True
    sistema = doc.get("sistema")
    if sistema and ficha.get("sistema"):
        ns = normalizar(sistema)
        if any(ns in normalizar(s) for s in ficha["sistema"]):
            p += 0.04
            confirmado = True
    if p_entero < 0.9 and not confirmado and len(consultas) > 1:
        p -= 0.1
    return round(min(p, 1.0), 3)


def emparejar(cliente: Cliente, doc: dict) -> dict:
    """Busca el documento y devuelve {'estado', 'confianza', 'ref', 'datos'} para la tabla extra."""
    tipo = TIPO_POR_CATEGORIA.get(doc.get("categoria") or "", "rpgitem")
    if tipo == "boardgame":
        # BGG tiene una ficha por juego, no por PDF: se busca el juego de la carpeta.
        consultas = [juego_para_bgg(doc.get("carpeta") or "", doc.get("titulo") or "")]
        if normalizar(consultas[0]) in GENERICAS:
            consultas = []
    else:
        consultas = variantes(doc["titulo"], doc.get("sistema"))
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
    return {
        "estado": estado, "confianza": mejor["puntos"], "ref": str(mejor["id"]),
        "datos": {"tipo": tipo, "consultas": consultas, "elegido": mejor,
                  "candidatos": [{k: f.get(k) for k in ("id", "nombre", "anio", "editoriales", "url", "puntos")}
                                 for f in puntuados]},
    }


def a_json(d: dict) -> str:
    return json.dumps(d, ensure_ascii=False, separators=(",", ":"))
