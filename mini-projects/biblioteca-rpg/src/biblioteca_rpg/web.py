"""Web del catálogo. Solo lee la base de datos y los PDF.

    uvicorn biblioteca_rpg.web:app --host 0.0.0.0 --port 8090
"""

import json
import time
from pathlib import Path
from urllib.parse import quote_plus, urlencode

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from . import config as config_mod
from .consultas import FACETAS, ORDENES, SIN_VALOR, buscar, facetas, subcarpetas
from .db import conectar
from .enriquecer import aplicar
from .entrada import DUPLICADOS, destino_valido, ruta_portada_entrada
from .indexador import ruta_portada
from .texto_pdf import normalizar

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
plantillas.env.globals["pendientes_revisar"] = lambda: _con().execute(
    "SELECT COUNT(*) FROM extra WHERE fuente='geek' AND estado='pendiente'").fetchone()[0]
plantillas.env.globals["pendientes_entrada"] = lambda: _con().execute(
    "SELECT COUNT(*) FROM entrada WHERE estado IN ('pendiente', 'error')").fetchone()[0]
NOMBRE_FUENTE = {"pdf": "el PDF", "rpgitem": "RPGGeek", "boardgame": "BoardGameGeek"}


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
    extras = {f["fuente"]: {**dict(f), "datos": json.loads(f["datos"])}
              for f in con.execute("SELECT * FROM extra WHERE doc_id=?", (doc_id,))}
    geek_nombre = NOMBRE_FUENTE.get(d["geek_tipo"] or "", "RPGGeek")
    origen = {k: (geek_nombre if v == "geek" else NOMBRE_FUENTE.get(v, v))
              for k, v in json.loads(d["origen"] or "{}").items()}
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
        "extras": extras, "origen": origen, "volver": f"/libro/{doc_id}",
    })


@app.get("/revisar", response_class=HTMLResponse)
def revisar(request: Request, pagina: int = 1):
    """Emparejados dudosos con RPGGeek/BGG: se confirma o descarta cada uno."""
    con = _con()
    total = con.execute("SELECT COUNT(*) FROM extra WHERE fuente='geek' AND estado='pendiente'").fetchone()[0]
    por_pagina = 30
    filas = con.execute(
        "SELECT d.id, d.titulo, d.carpeta, d.sistema, d.editorial, d.ext_editorial, d.publicado, d.anio, "
        "e.confianza, e.datos FROM extra e JOIN docs d ON d.id = e.doc_id "
        "WHERE e.fuente='geek' AND e.estado='pendiente' ORDER BY e.confianza DESC, d.id "
        f"LIMIT {por_pagina} OFFSET {(max(pagina, 1) - 1) * por_pagina}"
    ).fetchall()
    items = [{**dict(f), "datos": json.loads(f["datos"])} for f in filas]
    return plantillas.TemplateResponse(request, "revisar.html", {
        "items": items, "total": total, "pagina": pagina, "paginas": max(1, -(-total // por_pagina)),
        "volver": f"/revisar?pagina={pagina}",
    })


@app.post("/revisar/{doc_id}")
def decidir(doc_id: int, accion: str, ref: int | None = None, volver: str = "/revisar"):
    """accion=aceptar (con ref = id del candidato elegido) o rechazar."""
    con = _con()
    fila = con.execute("SELECT datos FROM extra WHERE doc_id=? AND fuente='geek'", (doc_id,)).fetchone()
    if not fila or accion not in ("aceptar", "rechazar"):
        raise HTTPException(400, "Nada que decidir")
    datos = json.loads(fila["datos"])
    if accion == "aceptar":
        elegido = datos.get("elegido") or {}
        if ref and ref != elegido.get("id"):
            cand = next((c for c in datos.get("candidatos", []) if c.get("id") == ref), None)
            if not cand:
                raise HTTPException(400, "Ese candidato no está en la lista")
            elegido = {**cand, "tipo": datos.get("tipo"), "autores": []}
        datos["elegido"] = elegido
        estado, ref_txt = "aceptado", str(elegido.get("id"))
    else:
        estado, ref_txt = "rechazado", None
    with con:
        con.execute("UPDATE extra SET estado=?, ref=?, datos=? WHERE doc_id=? AND fuente='geek'",
                    (estado, ref_txt, json.dumps(datos, ensure_ascii=False), doc_id))
        aplicar(con, [doc_id])
    destino = volver if volver.startswith("/") and not volver.startswith("//") else "/revisar"
    return RedirectResponse(destino, status_code=303)


# --- Entrada (_Inbox) y novedades ---------------------------------------------------

@app.get("/entrada", response_class=HTMLResponse)
def entrada(request: Request):
    con = _con()

    def lista(sql, *params):
        out = []
        for f in con.execute(sql, params):
            e = dict(f)
            e["alternativas"] = json.loads(e["alternativas"] or "[]")
            out.append(e)
        return out

    return plantillas.TemplateResponse(request, "entrada.html", {
        "pendientes": lista("SELECT * FROM entrada WHERE estado IN ('pendiente', 'error') ORDER BY llegado_en, id"),
        "en_cola": lista("SELECT * FROM entrada WHERE estado IN ('nuevo', 'aprobado') ORDER BY id"),
        "recientes": lista("SELECT * FROM entrada WHERE estado IN ('movido', 'descartado') "
                           "ORDER BY movido_en DESC LIMIT 40"),
        "carpeta_entrada": cfg.entrada, "auto": cfg.auto, "duplicados": DUPLICADOS,
    })


@app.post("/entrada/{entrada_id}")
def decidir_entrada(entrada_id: int, accion: str = Form(...), destino: str = Form(""),
                    destino_otra: str = Form(""), nombre: str = Form("")):
    """La web solo anota la decisión; el clasificador (el único que escribe en la biblioteca) la ejecuta."""
    con = _con()
    e = con.execute("SELECT estado FROM entrada WHERE id=?", (entrada_id,)).fetchone()
    if not e:
        raise HTTPException(404, "No existe")
    if e["estado"] in ("nuevo", "aprobado", "desaparecido"):
        raise HTTPException(409, "Ahora mismo no se puede cambiar")
    ahora = int(time.time())
    if accion == "reintentar":
        cambios = ("nuevo", None, None, 0)
    elif accion == "descartar":
        cambios = ("aprobado", DUPLICADOS, None, 0)
    elif accion == "mover":
        elegido = destino_otra if destino == "__otra" or (destino_otra.strip() and not destino) else destino
        valido = destino_valido(cfg, elegido)
        if not valido:
            raise HTTPException(400, f"Carpeta no válida: «{elegido}». Tiene que empezar por "
                                     f"{', '.join(cfg.areas)}.")
        limpio = nombre.strip()
        if limpio and ("/" in limpio or "\\" in limpio or limpio.startswith(".")):
            raise HTTPException(400, "Nombre no válido")
        cambios = ("aprobado", valido, limpio or None, 0)
    else:
        raise HTTPException(400, "Acción desconocida")
    with con:
        con.execute("UPDATE entrada SET estado=?, destino=?, nuevo_nombre=?, auto=?, decidido_en=?, nota=NULL "
                    "WHERE id=?", (*cambios, ahora, entrada_id))
    return RedirectResponse("/entrada", status_code=303)


@app.get("/entrada/{entrada_id}/portada.jpg")
def portada_entrada(entrada_id: int):
    ruta = ruta_portada_entrada(cfg, entrada_id)
    if ruta.is_file():
        return FileResponse(ruta, media_type="image/jpeg", headers={"Cache-Control": "max-age=3600"})
    return Response(PORTADA_VACIA, media_type="image/svg+xml", headers={"Cache-Control": "max-age=60"})


@app.get("/entrada/{entrada_id}/pdf")
def pdf_entrada(entrada_id: int):
    e = _con().execute("SELECT ruta_actual FROM entrada WHERE id=?", (entrada_id,)).fetchone()
    if not e:
        raise HTTPException(404, "No existe")
    raiz = cfg.raiz.resolve()
    ruta = (raiz / e["ruta_actual"]).resolve()
    if raiz not in ruta.parents or not ruta.exists():
        raise HTTPException(404, "Ya no está ahí")
    if ruta.is_dir():
        pdfs = [p for p in ruta.rglob("*") if p.is_file() and p.suffix.lower() == ".pdf"]
        if not pdfs:
            raise HTTPException(404, "No hay PDF dentro")
        ruta = max(pdfs, key=lambda p: p.stat().st_size)
    return FileResponse(ruta, media_type="application/pdf", filename=ruta.name, content_disposition_type="inline")


def _indice_carpetas(con) -> list[tuple[str, str, str, int]]:
    """[(carpeta, carpeta normalizada, último tramo normalizado, nº de documentos debajo)].

    Incluye las carpetas intermedias ("RPG/Shadowrun") aunque no tengan PDF sueltos.
    """
    def calcular():
        totales: dict[str, int] = {}
        for f in con.execute("SELECT carpeta, COUNT(*) AS n FROM docs GROUP BY carpeta"):
            partes = (f["carpeta"] or "").split("/")
            for i in range(1, len(partes) + 1):
                c = "/".join(partes[:i])
                totales[c] = totales.get(c, 0) + f["n"]
        return [(c, normalizar(c), normalizar(c.rsplit("/", 1)[-1]), n)
                for c, n in totales.items() if c and "/_" not in "/" + c]
    return _cacheado(con, ("carpetas",), calcular)


@app.get("/api/carpetas")
def api_carpetas(q: str = ""):
    """Carpetas que contienen TODAS las palabras escritas, en cualquier parte de la ruta.

    "shadow 5e" encuentra "RPG/Shadowrun/5e (Catalyst, 2013)". Sin distinguir mayúsculas ni acentos.
    Primero las que tienen las palabras en su propio nombre, luego las más grandes.
    """
    palabras = normalizar(q).split()
    if not palabras or len(q.strip()) < 2:
        return JSONResponse([])
    res = []
    for carpeta, norm, ultimo, n in _indice_carpetas(_con()):
        if all(p in norm for p in palabras):
            en_nombre = sum(p in ultimo for p in palabras)
            res.append((-en_nombre, carpeta.count("/"), -n, carpeta, n))
    res.sort()
    return JSONResponse([{"carpeta": r[3], "n": r[4]} for r in res[:30]])


@app.get("/novedades", response_class=HTMLResponse)
def novedades(request: Request, dias: int = 30):
    con = _con()
    # La primera indexación metió toda la biblioteca de golpe: eso no son novedades.
    primera = con.execute("SELECT MIN(llegado_en) FROM docs").fetchone()[0] or 0
    desde = max(primera + 86400, int(time.time()) - dias * 86400)
    filas = con.execute("SELECT * FROM docs WHERE llegado_en >= ? ORDER BY llegado_en DESC, id DESC LIMIT 240",
                        (desde,)).fetchall()
    grupos: list = []
    for d in filas:
        dia = _fecha(d["llegado_en"])
        if not grupos or grupos[-1][0] != dia:
            grupos.append((dia, []))
        grupos[-1][1].append(d)
    auto = con.execute("SELECT id, nombre, ruta_actual, destino, movido_en FROM entrada "
                       "WHERE auto=1 AND estado='movido' AND movido_en >= ? ORDER BY movido_en DESC LIMIT 50",
                       (desde,)).fetchall()
    return plantillas.TemplateResponse(request, "novedades.html", {
        "grupos": grupos, "total": len(filas), "dias": dias, "auto": auto,
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
