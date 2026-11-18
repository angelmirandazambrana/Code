"""Web del catálogo. Solo lee la base de datos y los PDF.

    uvicorn biblioteca_rpg.web:app --host 0.0.0.0 --port 8090
"""

import time
from pathlib import Path
from urllib.parse import quote_plus, urlencode

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from . import config as config_mod
from .consultas import FACETAS, ORDENES, SIN_VALOR, buscar, facetas, subcarpetas
from .db import conectar
from .indexador import ruta_portada

AQUI = Path(__file__).parent
cfg = config_mod.cargar()
app = FastAPI(title="Biblioteca", docs_url=None, redoc_url=None)
app.mount("/static", StaticFiles(directory=AQUI / "static"), name="static")
plantillas = Jinja2Templates(directory=AQUI / "templates")

PORTADA_VACIA = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 300 400">
<rect width="300" height="400" fill="#8883"/><path d="M110 150h80v100h-80z" fill="none" stroke="#8888" stroke-width="6"/>
<path d="M125 175h50M125 195h50M125 215h30" stroke="#8888" stroke-width="6"/></svg>"""


def _con():
    if not hasattr(app.state, "con"):
        app.state.con = conectar(cfg.db)
    return app.state.con


def _tamano(n: int | None) -> str:
    if not n:
        return ""
    for unidad in ("B", "KB", "MB", "GB"):
        if n < 1024 or unidad == "GB":
            return f"{n:.0f} {unidad}" if unidad in ("B", "KB") else f"{n:.1f} {unidad}"
        n /= 1024
    return ""


def _fecha(ts: int | None) -> str:
    return time.strftime("%d/%m/%Y", time.localtime(ts)) if ts else ""


plantillas.env.filters["tamano"] = _tamano
plantillas.env.filters["fecha"] = _fecha
plantillas.env.filters["qp"] = quote_plus
# Cambia en cada arranque: el navegador recarga CSS/JS tras una actualización.
plantillas.env.globals["version"] = str(int(time.time()))


_cache: dict = {}


def _cacheado(con, clave, calcular):
    """Facetas y subcarpetas solo cambian cuando el indexador añade, cambia o borra ficheros."""
    version = tuple(con.execute("SELECT COUNT(*), MAX(id), MAX(actualizado_en) FROM docs").fetchone())
    if _cache.get("version") != version:
        _cache.clear()
        _cache["version"] = version
    if clave not in _cache:
        if len(_cache) > 200:
            _cache.clear()
            _cache["version"] = version
        _cache[clave] = calcular()
    return _cache[clave]


def _leer_filtros(request: Request) -> dict:
    return {f: request.query_params.getlist(f) for f in FACETAS if request.query_params.getlist(f)}


def _url(base: dict, **cambios) -> str:
    """URL de la portada con los parámetros actuales cambiados. None borra; una tupla (faceta, valor) alterna."""
    params = {k: list(v) if isinstance(v, list) else v for k, v in base.items()}
    for k, v in cambios.items():
        if k == "alternar":
            faceta, valor = v
            actuales = params.get(faceta, [])
            params[faceta] = [x for x in actuales if x != valor] if valor in actuales else [*actuales, valor]
            params.pop("pagina", None)
        elif v is None:
            params.pop(k, None)
        else:
            params[k] = v
            if k != "pagina":
                params.pop("pagina", None)
    pares = []
    for k, v in params.items():
        for x in v if isinstance(v, list) else [v]:
            if x not in (None, ""):
                pares.append((k, x))
    return "/?" + urlencode(pares) if pares else "/"


@app.get("/", response_class=HTMLResponse)
def inicio(request: Request, q: str = "", carpeta: str = "", orden: str = "", pagina: int = 1):
    con = _con()
    filtros = _leer_filtros(request)
    q = q.strip()
    if orden not in ORDENES:
        orden = "relevancia" if q else "titulo"
    res = buscar(con, filtros, q or None, carpeta or None, orden, pagina)
    clave_cache = (q.lower(), carpeta, tuple(sorted((k, tuple(v)) for k, v in filtros.items())))
    base = {**{k: v for k, v in filtros.items()}, "q": q, "carpeta": carpeta, "orden": orden}
    if orden == ("relevancia" if q else "titulo"):
        base.pop("orden")

    migas = []
    if carpeta:
        acum = []
        for seg in carpeta.strip("/").split("/"):
            acum.append(seg)
            migas.append((seg, _url(base, carpeta="/".join(acum))))

    return plantillas.TemplateResponse(request, "index.html", {
        "q": q, "carpeta": carpeta, "orden": orden, "ordenes": ORDENES,
        "res": res, "pagina": pagina,
        "facetas": _cacheado(con, ("f", clave_cache),
                             lambda: facetas(con, filtros, q or None, carpeta or None)),
        "etiquetas": FACETAS, "filtros": filtros, "sin_valor": SIN_VALOR,
        "subcarpetas": _cacheado(con, ("s", clave_cache),
                                 lambda: subcarpetas(con, filtros, q or None, carpeta or None)),
        "migas": migas, "url": lambda **c: _url(base, **c),
        "total_catalogo": con.execute("SELECT COUNT(*) FROM docs").fetchone()[0],
    })


@app.get("/libro/{doc_id}", response_class=HTMLResponse)
def ficha(request: Request, doc_id: int):
    con = _con()
    d = con.execute("SELECT * FROM docs WHERE id=?", (doc_id,)).fetchone()
    if not d:
        raise HTTPException(404, "No existe")
    hermanos = con.execute(
        "SELECT id, titulo, portada FROM docs WHERE carpeta=? AND id<>? ORDER BY titulo COLLATE NOCASE LIMIT 24",
        (d["carpeta"], doc_id),
    ).fetchall()
    migas, acum = [], []
    for seg in (d["carpeta"] or "").split("/"):
        if seg:
            acum.append(seg)
            migas.append((seg, _url({}, carpeta="/".join(acum))))
    ruta_red = ""
    if cfg.ruta_smb:
        ruta_red = cfg.ruta_smb.rstrip("\\") + "\\" + d["ruta"].replace("/", "\\")
    busqueda = d["titulo"]
    es_wargame = d["categoria"] in ("Wargames", "Juegos de mesa")
    tienda = "https://www.wargamevault.com" if es_wargame else "https://www.drivethrurpg.com"
    geek = (
        f"https://boardgamegeek.com/geeksearch.php?action=search&objecttype=boardgame&q={quote_plus(busqueda)}"
        if es_wargame else
        f"https://rpggeek.com/geeksearch.php?action=search&objecttype=rpgitem&q={quote_plus(busqueda)}"
    )
    return plantillas.TemplateResponse(request, "ficha.html", {
        "d": d, "hermanos": hermanos, "migas": migas, "ruta_red": ruta_red,
        "enlace_tienda": f"{tienda}/en/browse?keyword={quote_plus(busqueda)}",
        "nombre_tienda": "Wargame Vault" if es_wargame else "DriveThruRPG",
        "enlace_geek": geek, "nombre_geek": "BoardGameGeek" if es_wargame else "RPGGeek",
        "filtro": lambda faceta, valor: _url({}, alternar=(faceta, str(valor))),
    })


@app.get("/portada/{doc_id}.jpg")
def portada(doc_id: int):
    ruta = ruta_portada(cfg.portadas, doc_id)
    if ruta.is_file():
        return FileResponse(ruta, media_type="image/jpeg",
                            headers={"Cache-Control": "public, max-age=604800"})
    return Response(PORTADA_VACIA, media_type="image/svg+xml", headers={"Cache-Control": "max-age=600"})


@app.get("/pdf/{doc_id}/{nombre}")
def pdf(doc_id: int, nombre: str = ""):
    d = _con().execute("SELECT ruta, nombre FROM docs WHERE id=?", (doc_id,)).fetchone()
    if not d:
        raise HTTPException(404, "No existe")
    raiz = cfg.raiz.resolve()
    ruta = (raiz / d["ruta"]).resolve()
    if raiz not in ruta.parents or not ruta.is_file():
        raise HTTPException(404, "Fichero no encontrado en la biblioteca")
    return FileResponse(ruta, media_type="application/pdf", filename=d["nombre"],
                        content_disposition_type="inline")


@app.get("/salud")
def salud():
    n = _con().execute("SELECT COUNT(*) FROM docs").fetchone()[0]
    return {"ok": True, "documentos": n}
