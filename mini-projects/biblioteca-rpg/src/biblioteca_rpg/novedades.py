"""Noticias de webs de rol y wargames, leídas de sus RSS: pistas para lo que llega a la Entrada.

    python -m biblioteca_rpg.novedades leer [--paginas N]   # N > 1: también las páginas antiguas del feed
    python -m biblioteca_rpg.novedades buscar "Nombre del fichero.pdf"

Lo que baja de Telegram suele ser de lo que hablan estas webs («sale Dark Sun para D&D 5e, de Wizards»).
Las noticias cuyo título se parece al del fichero se le pasan a Haiku junto al resto de pistas: dicen el
juego, la editorial y cuándo salió. Solo se leen los RSS, que existen para leerse así, y despacio.
También los posts nuevos de unos subreddits, si están las claves de Reddit en el .env (ver leer_reddit).
"""

import argparse
import base64
import email.utils
import html
import json
import math
import os
import re
import sys
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET

from . import config as config_mod
from .db import conectar

# (nombre, url, cómo pide páginas antiguas: "wp" = ?paged=N, "ghost" = /N/, None = no se puede)
FEEDS = [
    ("EN World", "https://www.enworld.org/ewr-porta/index.rss", None),
    ("Rascal", "https://rascal.news/rss/", None),
    ("Bell of Lost Souls", "https://www.belloflostsouls.net/feed", "wp"),
    ("Geek Native", "https://www.geeknative.com/feed/", "wp"),
    ("Wargamer", "https://www.wargamer.com/feed", None),
    ("Beasts of War", "https://www.beastsofwar.com/feed/", "wp"),
    ("Spikey Bits", "https://www.spikeybits.com/feed", "wp"),
    ("Goonhammer", "https://www.goonhammer.com/feed/?post_type=post", "wp"),
    ("Tabletop Gaming", "https://www.tabletopgaming.co.uk/feed/", "wp"),
    ("TheGamer", "https://www.thegamer.com/feed/category/tabletop/", None),
]
PAUSA = 3          # segundos entre peticiones
LEER_CADA = 6 * 3600
MAX_PISTAS = 4
ATOM = "{http://www.w3.org/2005/Atom}"

PALABRAS_VACIAS = frozenset(re.findall(r"\w+", """
the and for with from into your you our are new now out its this that what how why who all more
pdf rpg rpgs ttrpg edition ed book books game games print digital free part vol volume issue complete
del los las por para con una uno que pdf libro juego
"""))


def _url_pagina(url: str, modo: str | None, n: int) -> str | None:
    if n == 1:
        return url
    if modo == "wp":
        return f"{url}{'&' if '?' in url else '?'}paged={n}"
    if modo == "ghost":
        return f"{url.rstrip('/')}/{n}/"
    return None


def _texto(s: str | None) -> str:
    s = re.sub(r"<[^>]+>", " ", html.unescape(s or ""))
    return " ".join(html.unescape(s).split())


def _fecha(s: str | None) -> str | None:
    if not s:
        return None
    m = re.match(r"\d{4}-\d{2}-\d{2}", s.strip())
    if m:
        return m.group(0)
    try:
        return email.utils.parsedate_to_datetime(s).strftime("%Y-%m-%d")
    except (TypeError, ValueError):
        return None


def parsear(xml: bytes) -> list[dict]:
    """[{url, titulo, resumen, fecha}] de un RSS 2.0 o un Atom."""
    raiz = ET.fromstring(xml)
    res = []
    for it in raiz.iter("item"):
        res.append({"url": (it.findtext("link") or "").strip(), "titulo": _texto(it.findtext("title")),
                    "resumen": _texto(it.findtext("description"))[:400], "fecha": _fecha(it.findtext("pubDate"))})
    for it in raiz.iter(f"{ATOM}entry"):
        enlace = it.find(f"{ATOM}link")
        res.append({"url": enlace.get("href", "") if enlace is not None else "",
                    "titulo": _texto(it.findtext(f"{ATOM}title")),
                    "resumen": _texto(it.findtext(f"{ATOM}summary") or it.findtext(f"{ATOM}content"))[:400],
                    "fecha": _fecha(it.findtext(f"{ATOM}published") or it.findtext(f"{ATOM}updated"))})
    return [r for r in res if r["url"] and r["titulo"]]


def _bajar(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "biblioteca-rpg/1.0 (lector RSS)"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read()


def leer(con, paginas: int = 1) -> int:
    """Lee los feeds y guarda las noticias nuevas. Devuelve cuántas son nuevas."""
    nuevas = 0
    for nombre, url, modo in FEEDS:
        for n in range(1, paginas + 1):
            u = _url_pagina(url, modo, n)
            if not u:
                break
            try:
                items = parsear(_bajar(u))
            except (urllib.error.URLError, TimeoutError, ET.ParseError, OSError) as ex:
                print(f"Novedades: {nombre} p{n}: {type(ex).__name__}: {ex}", file=sys.stderr, flush=True)
                break
            nuevas += _guardar(con, nombre, items)
            if not items:
                break
            time.sleep(PAUSA)
    return nuevas + leer_reddit(con)


def _guardar(con, fuente: str, items: list[dict]) -> int:
    antes = con.total_changes
    con.executemany(
        "INSERT OR IGNORE INTO novedades (url, fuente, titulo, resumen, fecha, leido_en) VALUES (?,?,?,?,?,?)",
        [(i["url"], fuente, i["titulo"], i["resumen"], i["fecha"], int(time.time())) for i in items])
    con.commit()
    return con.total_changes - antes


# Reddit: con la aplicación de solo lectura del usuario (REDDIT_CLIENT_ID, REDDIT_CLIENT_SECRET y
# REDDIT_USUARIO en el .env). Sin ellas no se lee: sin registrarse, Reddit lo corta.
SUBREDDITS = ["rpg", "osr", "wargaming", "Traveller", "callofcthulhu", "Pathfinder2e", "solorpgs",
              "Warhammer", "boardwargames"]


def _reddit_claves() -> tuple[str, str, str] | None:
    claves = tuple(os.environ.get(k, "").strip() for k in
                   ("REDDIT_CLIENT_ID", "REDDIT_CLIENT_SECRET", "REDDIT_USUARIO"))
    return claves if all(claves) else None


def parsear_reddit(datos: dict) -> list[dict]:
    res = []
    for hijo in (datos.get("data") or {}).get("children") or []:
        d = hijo.get("data") or {}
        if not d.get("title") or not d.get("permalink") or d.get("stickied"):
            continue
        fecha = time.strftime("%Y-%m-%d", time.gmtime(d["created_utc"])) if d.get("created_utc") else None
        res.append({"url": "https://www.reddit.com" + d["permalink"], "titulo": _texto(d["title"]),
                    "resumen": _texto(d.get("selftext"))[:400], "fecha": fecha})
    return res


def leer_reddit(con) -> int:
    claves = _reddit_claves()
    if not claves:
        return 0
    cliente, secreto, usuario = claves
    agente = f"linux:biblioteca-rpg:1.0 (by /u/{usuario})"
    try:
        req = urllib.request.Request(
            "https://www.reddit.com/api/v1/access_token", data=b"grant_type=client_credentials",
            headers={"User-Agent": agente,
                     "Authorization": "Basic " + base64.b64encode(f"{cliente}:{secreto}".encode()).decode()})
        with urllib.request.urlopen(req, timeout=30) as r:
            token = json.load(r)["access_token"]
    except (urllib.error.URLError, TimeoutError, OSError, KeyError, ValueError) as ex:
        print(f"Novedades: Reddit no da acceso: {type(ex).__name__}", file=sys.stderr, flush=True)
        return 0
    nuevas = 0
    for sub in SUBREDDITS:
        try:
            req = urllib.request.Request(f"https://oauth.reddit.com/r/{sub}/new?limit=100&raw_json=1",
                                         headers={"User-Agent": agente, "Authorization": f"Bearer {token}"})
            with urllib.request.urlopen(req, timeout=30) as r:
                nuevas += _guardar(con, f"r/{sub}", parsear_reddit(json.load(r)))
        except (urllib.error.URLError, TimeoutError, OSError, ValueError) as ex:
            print(f"Novedades: r/{sub}: {type(ex).__name__}: {ex}", file=sys.stderr, flush=True)
        time.sleep(PAUSA)
    return nuevas


def palabras(s: str) -> set[str]:
    s = re.sub(r"\.(pdf|epub|mobi|azw3?|zip|rar|cbz)$", "", s or "", flags=re.I)
    s = re.sub(r"([a-z])([A-Z]|\d)", r"\1 \2", s)  # CamelCase de los nombres de fichero («Beasts3»)
    s = s.lower().replace("&", " and ").replace("d and d", "dnd")
    return {w for w in re.findall(r"[a-z0-9]+", s)
            if w not in PALABRAS_VACIAS and (len(w) >= 3 or w in {"5e", "4e", "3e", "2e", "40k"})}


def parecidas(con, *textos: str, maximo: int = MAX_PISTAS) -> list[dict]:
    """Noticias cuyo título comparte palabras poco comunes con el nombre (o el título) del producto."""
    buscadas = set().union(*(palabras(t) for t in textos if t))
    if not buscadas:
        return []
    filas = con.execute("SELECT fuente, titulo, resumen, fecha FROM novedades").fetchall()
    if not filas:
        return []
    titulos = [palabras(f[1]) for f in filas]
    total = len(filas)
    df: dict[str, int] = {}
    for t in titulos:
        for w in t & buscadas:
            df[w] = df.get(w, 0) + 1
    idf = {w: math.log(total / n) for w, n in df.items()}
    rara = max(3, total * 0.02)  # «dnd», «5e» o «warhammer» salen en muchas: solas no bastan
    puntuadas = []
    for f, t in zip(filas, titulos):
        comunes = t & buscadas
        raras = sum(df[w] <= rara for w in comunes)
        # Tres palabras raras en común, o dos que sean al menos la mitad del nombre del producto.
        if raras >= 3 or (raras >= 2 and len(comunes) * 2 >= len(buscadas)):
            puntuadas.append((sum(idf[w] for w in comunes), f))
    puntuadas.sort(key=lambda x: x[1][3] or "", reverse=True)  # a igual parecido, la más reciente
    puntuadas.sort(key=lambda x: -x[0])
    return [{"fuente": f[0], "titulo": f[1], "resumen": f[2], "fecha": f[3]} for _, f in puntuadas[:maximo]]


def como_pista(noticias: list[dict]) -> list[str]:
    if not noticias:
        return []
    lineas = ["Noticias de webs de rol con título parecido (pueden no tener nada que ver con este fichero):"]
    for n in noticias:
        resumen = (n["resumen"] or "")[:220]
        lineas.append(f"- [{n['fecha'] or '?'}, {n['fuente']}] {n['titulo']}" + (f" — {resumen}" if resumen else ""))
    return lineas


_LEIDO: dict = {}


def automatico(con) -> None:
    """Desde el bucle del clasificador: lee los feeds cada LEER_CADA segundos."""
    if time.time() - _LEIDO.get("cuando", 0) < LEER_CADA:
        return
    _LEIDO["cuando"] = time.time()
    try:
        n = leer(con)
        if n:
            print(f"Novedades: {n} noticias nuevas", flush=True)
    except Exception as ex:  # que una web caída no pare el clasificador
        print(f"Novedades: {type(ex).__name__}: {ex}", file=sys.stderr, flush=True)


def main(argv=None) -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("orden", choices=["leer", "buscar"])
    ap.add_argument("texto", nargs="?")
    ap.add_argument("--paginas", type=int, default=1)
    args = ap.parse_args(argv)
    con = conectar(config_mod.cargar().db)
    if args.orden == "leer":
        print(f"Noticias nuevas: {leer(con, args.paginas)}")
        print([tuple(r) for r in con.execute("SELECT fuente, COUNT(*), MIN(fecha), MAX(fecha) FROM novedades GROUP BY fuente")])
    else:
        print("\n".join(como_pista(parecidas(con, args.texto or ""))) or "Nada parecido.")


if __name__ == "__main__":
    main()
