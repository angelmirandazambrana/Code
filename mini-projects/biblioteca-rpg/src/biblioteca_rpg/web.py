"""Web del catálogo. Solo lee la base de datos y los PDF.

    uvicorn biblioteca_rpg.web:app --host 0.0.0.0 --port 8090
"""

import json
import re
import sqlite3
import time
from pathlib import Path
from typing import Annotated
from urllib.parse import quote_plus, urlencode

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from . import config as config_mod
from .clasificador import POR_CLASIFICAR
from .consultas import FACETAS, NO, ORDENES, SIN_VALOR, buscar, consulta_fts, facetas, subcarpetas
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
# "Libro.epub" -> "EPUB"
plantillas.env.filters["ext"] = lambda nombre: (nombre or "").rsplit(".", 1)[-1].upper() if "." in (nombre or "") else ""
# Cambia en cada arranque: el navegador recarga CSS/JS tras una actualización.
plantillas.env.globals["version"] = str(int(time.time()))
plantillas.env.globals["pendientes_revisar"] = lambda: _con().execute(
    "SELECT COUNT(*) FROM extra WHERE fuente='geek' AND estado='pendiente'").fetchone()[0]
plantillas.env.globals["pendientes_entrada"] = lambda: _con().execute(
    "SELECT COUNT(*) FROM entrada WHERE estado IN ('pendiente', 'error')").fetchone()[0]
plantillas.env.globals["hay_telegram"] = lambda: _con().execute(
    "SELECT EXISTS (SELECT 1 FROM telegram_canales)").fetchone()[0]
NOMBRE_FUENTE = {"pdf": "el PDF", "rpgitem": "RPGGeek", "boardgame": "BoardGameGeek"}


_cache: dict = {}


def _cacheado(con, clave, calcular):
    """Facetas y subcarpetas solo cambian cuando el indexador añade, cambia o borra ficheros
    (o cuando se recalculan los nombres unificados de las editoriales)."""
    version = tuple(con.execute(
        "SELECT COUNT(*), MAX(id), MAX(actualizado_en), (SELECT COUNT(*) || MAX(forma) FROM editorial_alias) "
        "FROM docs").fetchone())
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
    """{"editorial": [...], "no_sistema": [...]}: lo incluido y lo excluido ("todo menos")."""
    claves = [*FACETAS, *(NO + f for f in FACETAS)]
    return {f: request.query_params.getlist(f) for f in claves if request.query_params.getlist(f)}


def _url(base: dict, **cambios) -> str:
    """URL de la portada con los parámetros actuales cambiados. None borra; una tupla (faceta, valor) alterna.

    La faceta puede ser "no_editorial" (excluir). Un valor no puede estar incluido y excluido a la vez:
    al activarlo en un lado se quita del otro.
    """
    params = {k: list(v) if isinstance(v, list) else v for k, v in base.items()}
    for k, v in cambios.items():
        if k == "alternar":
            faceta, valor = v
            actuales = params.get(faceta, [])
            if valor in actuales:
                params[faceta] = [x for x in actuales if x != valor]
            else:
                params[faceta] = [*actuales, valor]
                opuesta = faceta[len(NO):] if faceta.startswith(NO) else NO + faceta
                if opuesta in params:
                    params[opuesta] = [x for x in params[opuesta] if x != valor]
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
        "etiquetas": FACETAS, "filtros": filtros, "sin_valor": SIN_VALOR, "no": NO,
        "subcarpetas": _cacheado(con, ("s", clave_cache),
                                 lambda: subcarpetas(con, filtros, q or None, carpeta or None)),
        "migas": migas, "url": lambda **c: _url(base, **c),
        "total_catalogo": con.execute("SELECT COUNT(*) FROM docs").fetchone()[0],
    })


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
    if es_wargame:
        # BGG tiene una ficha por juego, no por PDF: se busca el juego ("Blue Moon City"),
        # no el fichero ("Blue Moon City - Cards"), que no encuentra nada.
        busqueda = juego_para_bgg(d["carpeta"] or "", d["titulo"] or "")
    tienda = "https://www.wargamevault.com" if es_wargame else "https://www.drivethrurpg.com"
    geek = (
        f"https://boardgamegeek.com/geeksearch.php?action=search&objecttype=boardgame&q={quote_plus(busqueda)}"
        if es_wargame else
        f"https://rpggeek.com/geeksearch.php?action=search&objecttype=rpgitem&q={quote_plus(busqueda)}"
    )
    borrado = con.execute("SELECT * FROM borrados WHERE doc_id=? AND estado IN ('pedido', 'error') "
                          "ORDER BY id DESC LIMIT 1", (doc_id,)).fetchone()
    return plantillas.TemplateResponse(request, "ficha.html", {
        "d": d, "hermanos": hermanos, "borrado": borrado, "migas": migas, "ruta_red": ruta_red,
        "enlace_tienda": f"{tienda}/en/browse?keyword={quote_plus(busqueda)}",
        "nombre_tienda": "Wargame Vault" if es_wargame else "DriveThruRPG",
        "enlace_geek": geek, "nombre_geek": "BoardGameGeek" if es_wargame else "RPGGeek",
        "filtro": lambda faceta, valor: _url({}, alternar=(faceta, str(valor))),
        "extras": extras, "origen": origen, "volver": f"/libro/{doc_id}",
    })


@app.post("/libro/{doc_id}/papelera")
def pedir_borrado(doc_id: int, accion: str = Form("pedir")):
    """Anota que este libro sobra (repetido, mal escaneo…). Lo mueve a _Papelera el clasificador."""
    con = _con()
    d = con.execute("SELECT id, ruta, titulo, carpeta FROM docs WHERE id=?", (doc_id,)).fetchone()
    if not d:
        raise HTTPException(404, "No existe")
    with con:
        if accion == "anular":
            con.execute("DELETE FROM borrados WHERE doc_id=? AND estado IN ('pedido', 'error')", (doc_id,))
        elif accion == "pedir":
            if not con.execute("SELECT 1 FROM borrados WHERE doc_id=? AND estado='pedido'", (doc_id,)).fetchone():
                con.execute("INSERT INTO borrados (doc_id, ruta, titulo, pedido_en) VALUES (?, ?, ?, ?)",
                            (doc_id, d["ruta"], d["titulo"], int(time.time())))
        else:
            raise HTTPException(400, "Acción desconocida")
    return RedirectResponse(f"/libro/{doc_id}", status_code=303)


@app.get("/revisar", response_class=HTMLResponse)
def revisar(request: Request, pagina: int = 1):
    """Emparejados dudosos con RPGGeek/BGG: se confirma o descarta cada uno."""
    con = _con()
    total = con.execute("SELECT COUNT(*) FROM extra WHERE fuente='geek' AND estado='pendiente'").fetchone()[0]
    por_pagina = 30
    filas = con.execute(
        "SELECT d.id, d.titulo, d.carpeta, d.sistema, d.editorial, d.ext_editorial, d.editorial_unif, d.publicado, d.anio, "
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

def _alternativas_grupo(carpeta: str, entradas: list[dict], n: int = 5) -> list[str]:
    """Carpetas para elegir en un grupo: la propuesta y las que más se repiten entre sus alternativas."""
    suma: dict[str, float] = {}
    for e in entradas:
        for c, pct in e.get("alternativas") or []:
            suma[c] = suma.get(c, 0) + (pct if pct is not None else 60)  # None: la propone Claude
    otras = [c for c, _ in sorted(suma.items(), key=lambda x: -x[1]) if c != carpeta]
    return [carpeta, *otras[: n - 1]]


# Palabras de una ruta que no dicen de qué juego es (tipos, áreas, ediciones…).
GENERICAS_RUTA = set("""
rpg wargames assets juegos mesa other publishers por clasificar core rules supplements settings adventures
organized play game aids fan homebrew fiction background misc army books scenarios campaigns errata faq
any edition rulebook rulebooks reglas
""".split())  # noqa: SIM905


def _distintivas(texto: str) -> set[str]:
    from .clasificador import palabras
    return {p for p in palabras(texto) if p not in GENERICAS_RUTA and len(p) > 2
            and not re.fullmatch(r"\d+e|v\d+|\d+", p)}


def encaja(e: dict, carpeta: str) -> bool:
    """¿Comparte el fichero alguna palabra distintiva con la carpeta propuesta? «battlerune…» no
    encaja en «Five parsecs from home» aunque los dos digan «rulebook» o «digital»."""
    return bool(_distintivas(f"{e.get('nombre') or ''} {e.get('pdf_titulo') or ''}")
                & _distintivas(carpeta.replace("/", " ")))


def agrupar_pendientes(pendientes: list[dict], minimo: int = 2, comprobar: bool = True) -> list[dict]:
    """Los pendientes con la misma carpeta propuesta, de los grupos más grandes a los más pequeños.

    Para decidir de golpe: «estos 15 van a Kobold Press». Solo grupos de `minimo` o más.
    Lo que no encaja con su propuesta (ninguna palabra distintiva en común) no se mezcla con los
    que sí: se agrupa con los que empiezan por la misma palabra («battlerune», 7 ficheros), sin
    carpeta marcada, para darles una de una vez.
    """
    por: dict[str, list[dict]] = {}
    sin_carpeta: dict[str, list[dict]] = {}
    for e in pendientes:
        if e["estado"] != "pendiente":
            continue
        if e.get("propuesta") and (not comprobar or encaja(e, e["propuesta"])):
            por.setdefault(e["propuesta"], []).append(e)
        elif comprobar:
            from .clasificador import palabras
            distintas = _distintivas(e.get("nombre") or "")
            primera = next((p for p in palabras(e.get("nombre") or "") if p in distintas), None)
            if primera:
                sin_carpeta.setdefault(primera, []).append(e)
    grupos = [{"carpeta": c, "clave": c, "entradas": es,
               "confianza": round(sum(e["confianza"] or 0 for e in es) / len(es) * 100),
               "alternativas": _alternativas_grupo(c, es)}
              for c, es in por.items() if len(es) >= minimo]
    grupos += [{"carpeta": None, "clave": f"sin:{p}", "palabra": p, "entradas": es,
                "confianza": round(sum(e["confianza"] or 0 for e in es) / len(es) * 100),
                "alternativas": _alternativas_grupo(None, es)[1:]}
               for p, es in sin_carpeta.items() if len(es) >= max(minimo, 2)]
    return sorted(grupos, key=lambda g: (g["carpeta"] is None, -len(g["entradas"]), -g["confianza"],
                                         g["carpeta"] or g["palabra"]))


TIPOS_CARPETA = {
    "Core Rules", "Supplements", "Settings", "Adventures", "Organized Play", "Game Aids", "Fan & Homebrew",
    "Fiction", "Background", "Misc", "Rules", "Army Books", "Scenarios & Campaigns", "Errata & FAQ",
}


def carpetas_de_tipo(juego: str) -> list[str]:
    """Las carpetas de tipo que existen en el juego (o en sus ediciones): 'RPG/X/5e/Adventures'."""
    base = cfg.raiz / juego
    if not base.is_dir():
        return []
    res = []
    for d in sorted(base.iterdir()):
        if not d.is_dir():
            continue
        if d.name in TIPOS_CARPETA:
            res.append(f"{juego}/{d.name}")
        elif not d.name.startswith("_"):  # una edición o línea: sus tipos
            res += [f"{juego}/{d.name}/{s.name}" for s in sorted(d.iterdir()) if s.is_dir() and s.name in TIPOS_CARPETA]
    return res


def agrupar_por_clasificar(entradas: list[dict]) -> list[dict]:
    """Lo que está en "<juego>/_Por clasificar", por juego y, dentro, por la subcarpeta más probable.

    [{"juego", "n", "grupos": [{"carpeta", "entradas", "confianza"}], "sueltos": [entrada]}]
    """
    por_juego: dict[str, list[dict]] = {}
    tipos_de: dict[str, list[str]] = {}
    for e in entradas:
        if not (cfg.raiz / e["ruta_actual"]).exists():
            continue  # se movió después (reorganización): ya no está aquí
        juego = e["ruta_actual"].split(f"/{POR_CLASIFICAR}/", 1)[0]
        if juego not in tipos_de:
            tipos_de[juego] = carpetas_de_tipo(juego)
        # Candidatas: las carpetas de tipo que hay en el juego, ordenadas por lo que sugirió el
        # clasificador (una alternativa dentro de "Adventures/…" cuenta para "Adventures").
        puntos = {c: sum((pct or 60) for a, pct in e["alternativas"] if a == c or a.startswith(c + "/"))
                  for c in tipos_de[juego]}
        mejor = max(puntos, key=puntos.get) if puntos and max(puntos.values()) > 0 else None
        e["propuesta"], e["confianza"] = (mejor, min(puntos[mejor], 100) / 100) if mejor else (None, 0)
        e["alternativas"] = [[c, p] for c, p in sorted(puntos.items(), key=lambda x: -x[1])]
        e["estado"] = "pendiente"  # para agrupar_pendientes
        por_juego.setdefault(juego, []).append(e)
    res = []
    for juego, es in sorted(por_juego.items(), key=lambda x: -len(x[1])):
        grupos = agrupar_pendientes(es, minimo=1, comprobar=False)
        for g in grupos:  # solo carpetas de tipo del juego
            g["alternativas"] = [c for c in g["alternativas"] if c in tipos_de[juego]]
        res.append({"juego": juego, "n": len(es), "grupos": grupos,
                    "sueltos": [e for e in es if not e["propuesta"]]})
    return res


@app.get("/entrada", response_class=HTMLResponse)
def entrada(request: Request, vista: str = ""):
    con = _con()

    def lista(sql, *params):
        out = []
        for f in con.execute(sql, params):
            e = dict(f)
            e["alternativas"] = json.loads(e["alternativas"] or "[]")
            e["llm"] = json.loads(e["llm"]) if e.get("llm") else None
            out.append(e)
        return out

    pendientes = lista("SELECT * FROM entrada WHERE estado IN ('pendiente', 'error') ORDER BY llegado_en, id")
    por_grupos = vista == "grupos"
    grupos = agrupar_pendientes(pendientes) if por_grupos else []
    marca = f"%/{POR_CLASIFICAR}/%"
    n_por_clasificar = con.execute("SELECT COUNT(*) FROM entrada WHERE estado='movido' AND ruta_actual LIKE ?",
                                   (marca,)).fetchone()[0]
    juegos = agrupar_por_clasificar(lista("SELECT * FROM entrada WHERE estado='movido' AND ruta_actual LIKE ? "
                                          "ORDER BY ruta_actual", marca)) if vista == "por_clasificar" else []
    return plantillas.TemplateResponse(request, "entrada.html", {
        "pendientes": pendientes, "grupos": grupos, "por_grupos": por_grupos, "vista": vista,
        "n_por_clasificar": n_por_clasificar, "juegos": juegos, "por_clasificar": POR_CLASIFICAR,
        "n_agrupados": sum(len(g["entradas"]) for g in grupos),
        "en_cola": lista("SELECT * FROM entrada WHERE estado IN ('nuevo', 'aprobado') ORDER BY id"),
        "recientes": lista("SELECT * FROM entrada WHERE estado IN ('movido', 'descartado') "
                           "ORDER BY movido_en DESC LIMIT 40"),
        "carpeta_entrada": cfg.entrada, "auto": cfg.auto, "duplicados": DUPLICADOS,
    })


@app.post("/entrada/lote")
def decidir_lote(destino: Annotated[str, Form()], ids: Annotated[list[int] | None, Form()] = None,
                 vista: Annotated[str, Form()] = "grupos", destino_otra: Annotated[str, Form()] = ""):
    """Mueve de golpe los marcados de un grupo a la carpeta elegida (la ejecuta el clasificador)."""
    if destino == "__otra" or (destino_otra.strip() and not destino):
        destino = destino_otra
    valido = destino_valido(cfg, destino)
    if not valido:
        raise HTTPException(400, f"Carpeta no válida: «{destino}»")
    vista = vista if vista in ("grupos", "por_clasificar") else "grupos"
    if not ids:
        return RedirectResponse(f"/entrada?vista={vista}", status_code=303)
    con = _con()
    with con:
        # Solo los que siguen pendientes: lo que ya está en cola o movido no se toca.
        # Solo los pendientes y lo que espera en una carpeta "_Por clasificar".
        con.execute(f"UPDATE entrada SET estado='aprobado', destino=?, nuevo_nombre=NULL, auto=0, decidido_en=?, "
                    f"nota=NULL WHERE (estado='pendiente' OR (estado='movido' AND ruta_actual LIKE ?)) "
                    f"AND id IN ({','.join('?' * len(ids))})",
                    (valido, int(time.time()), f"%/{POR_CLASIFICAR}/%", *ids))
    return RedirectResponse(f"/entrada?vista={vista}", status_code=303)


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
        # Las carpetas de assets sin PDF (packs de mapas, tokens, música…) también son destinos.
        try:
            for (ruta,) in con.execute("SELECT ruta FROM packs WHERE length(ruta) - length(replace(ruta, '/', '')) <= 3"):
                totales.setdefault(ruta, 0)
        except sqlite3.OperationalError:
            pass  # aún no hay índice de packs
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
    con = _con()
    res = []
    for carpeta, norm, ultimo, n in _indice_carpetas(con):
        if all(p in norm for p in palabras):
            en_nombre = sum(p in ultimo for p in palabras)
            res.append((-en_nombre, carpeta.count("/"), -n, carpeta, n))
    res.sort()
    salida = [{"carpeta": r[3], "n": r[4]} for r in res[:20]]
    # Y las carpetas donde hay ficheros que se llaman así: "DDAL" encuentra dónde están los DDAL09-xx.
    vistas = {s["carpeta"] for s in salida}
    for f in _carpetas_por_ficheros(con, q):
        if f["carpeta"] not in vistas:
            salida.append(f)
    return JSONResponse(salida[:30])


def _carpetas_por_ficheros(con, q: str, n: int = 12) -> list[dict]:
    """Carpetas con ficheros cuyo nombre encaja con lo escrito, de la que más tiene a la que menos.

    [{"carpeta", "n" (documentos en la carpeta), "coinciden", "ejemplo"}]
    """
    fts = consulta_fts(q)
    if not fts:
        return []
    try:
        filas = con.execute(
            "SELECT d.carpeta, COUNT(*) AS k, MIN(d.nombre) AS ejemplo FROM docs_fts JOIN docs d ON d.id = docs_fts.rowid "
            "WHERE docs_fts MATCH ? GROUP BY d.carpeta ORDER BY k DESC LIMIT ?",
            ("{titulo pdf_titulo} : (" + fts + ")", n * 2)).fetchall()
    except sqlite3.OperationalError:
        return []
    totales = {c: k for c, _, _, k in _indice_carpetas(con)}
    return [{"carpeta": f["carpeta"], "n": totales.get(f["carpeta"], f["k"]), "coinciden": f["k"], "ejemplo": f["ejemplo"]}
            for f in filas if f["carpeta"] and "/_" not in "/" + f["carpeta"]][:n]


# Facetas que se sugieren al escribir en el buscador.
FACETAS_SUGERIDAS = ("editorial", "sistema", "ambientacion")


def _indice_valores(con) -> list[tuple[str, str, str, int]]:
    """[(faceta, valor, valor normalizado, nº de documentos)] de las facetas sugeridas."""
    def calcular():
        res = []
        for faceta in FACETAS_SUGERIDAS:
            expr = "editorial_unif" if faceta == "editorial" else faceta
            for f in con.execute(f"SELECT {expr} AS v, COUNT(*) AS n FROM docs WHERE {expr} IS NOT NULL GROUP BY v"):
                res.append((faceta, f["v"], normalizar(f["v"]), f["n"]))
        return res
    return _cacheado(con, ("valores",), calcular)


@app.get("/api/sugerencias")
def api_sugerencias(q: str = ""):
    """Escritura predictiva del buscador: editoriales/sistemas que tenemos y títulos de libros.

    Todas las palabras tienen que aparecer (la última puede estar a medias). Sin distinguir acentos.
    """
    con = _con()
    palabras = normalizar(q).split()
    if not palabras or len(q.strip()) < 2:
        return JSONResponse({"valores": [], "libros": []})
    valores = []
    for faceta, valor, norm, n in _indice_valores(con):
        if all(p in norm for p in palabras):
            empieza = norm.startswith(palabras[0]) or f" {palabras[0]}" in f" {norm}"
            valores.append((not empieza, -n, faceta, valor, n))
    valores.sort()
    libros, vistos = [], set()
    fts = consulta_fts(q)
    # Primero los que tienen las palabras en el título; si faltan, los que las tienen en la carpeta
    # ("cthulhu mon" -> los Monsters de Call of Cthulhu). Sin repetir el mismo título.
    consultas = ["{titulo pdf_titulo} : (" + fts + ")", fts] if fts else []
    for consulta in consultas:
        filas = con.execute(
            "SELECT d.id, d.titulo, d.sistema, d.edicion, COALESCE(d.publicado, d.anio) AS anio "
            "FROM docs_fts JOIN docs d ON d.id = docs_fts.rowid "
            "WHERE docs_fts MATCH ? ORDER BY bm25(docs_fts, 10.0, 1.0, 4.0, 1.0) LIMIT 40",
            [consulta],
        ).fetchall()
        for f in filas:
            clave = normalizar(f["titulo"])
            if clave in vistos or len(libros) == 7:
                continue
            vistos.add(clave)
            detalle = " · ".join(str(x) for x in (f["sistema"], f["edicion"], f["anio"]) if x)
            libros.append({"titulo": f["titulo"], "detalle": detalle, "url": f"/libro/{f['id']}"})
        if len(libros) >= 4:
            break
    return JSONResponse({
        "valores": [{"etiqueta": FACETAS[fa], "valor": v, "n": n, "url": _url({}, alternar=(fa, v))}
                    for _, _, fa, v, n in valores[:5]],
        "libros": libros,
    })


def _fin_primera_carga(con) -> int:
    """Momento en que acabó la primera indexación (toda la biblioteca de golpe: eso no son novedades).

    Es el final del primer bloque de llegadas seguidas: la primera pausa de más de una hora.
    """
    primera = con.execute("SELECT MIN(llegado_en) FROM docs").fetchone()[0]
    if primera in _PRIMERA_CARGA:  # una vez encontrada la pausa, ya no cambia
        return _PRIMERA_CARGA[primera]
    anterior = None
    for (t,) in con.execute("SELECT llegado_en FROM docs WHERE llegado_en IS NOT NULL ORDER BY llegado_en"):
        if anterior is not None and t - anterior > 3600:
            _PRIMERA_CARGA[primera] = anterior + 1
            return anterior + 1
        anterior = t
    return (anterior or 0) + 1


_PRIMERA_CARGA: dict[int, int] = {}  # primera llegada -> fin de la primera carga


@app.get("/novedades", response_class=HTMLResponse)
def novedades(request: Request, dias: int = 30):
    con = _con()
    desde = max(_fin_primera_carga(con), int(time.time()) - dias * 86400)
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


ESTADOS_TELEGRAM = {
    "descargado": "Descargado", "ya_en_biblioteca": "Ya lo tenías", "ya_en_entrada": "Ya en la entrada",
    "repetido": "Repetido", "ignorado": "Ignorado", "error": "Error",
}


@app.get("/telegram", response_class=HTMLResponse)
def telegram(request: Request, estado: str = ""):
    con = _con()
    canales = con.execute(
        "SELECT c.*, (SELECT COUNT(*) FROM telegram_msgs m WHERE m.canal = c.canal AND m.estado='descargado') AS bajados "
        "FROM telegram_canales c ORDER BY c.titulo COLLATE NOCASE").fetchall()
    titulos = {c["canal"]: c["titulo"] for c in canales}
    cuentas = dict(con.execute("SELECT estado, COUNT(*) FROM telegram_msgs GROUP BY estado").fetchall())
    filtro, params = ("WHERE estado=?", [estado]) if estado in ESTADOS_TELEGRAM else ("", [])
    mensajes = con.execute(f"SELECT * FROM telegram_msgs {filtro} ORDER BY procesado_en DESC, msg_id DESC LIMIT 150",
                           params).fetchall()
    # ¿Dónde está ahora lo descargado? (lo sigue el clasificador)
    destinos = {}
    for m in mensajes:
        if m["archivo"]:
            e = con.execute("SELECT id, estado, ruta_actual FROM entrada WHERE nombre IN (?, ?) "
                            "ORDER BY id DESC LIMIT 1", (m["archivo"], m["archivo"].rsplit(".", 1)[0])).fetchone()
            if e:
                destinos[(m["canal"], m["msg_id"])] = e
    return plantillas.TemplateResponse(request, "telegram.html", {
        "canales": canales, "titulos": titulos, "cuentas": cuentas, "mensajes": mensajes, "destinos": destinos,
        "estados": ESTADOS_TELEGRAM, "estado": estado,
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
    tipo = TIPOS_FICHERO.get(ruta.suffix.lower(), "application/octet-stream")
    # El PDF se abre en el navegador; un ebook se descarga para leerlo en su aplicación.
    return FileResponse(ruta, media_type=tipo, filename=d["nombre"],
                        content_disposition_type="inline" if tipo == "application/pdf" else "attachment")


TIPOS_FICHERO = {".pdf": "application/pdf", ".epub": "application/epub+zip", ".mobi": "application/x-mobipocket-ebook"}


@app.get("/salud")
def salud():
    n = _con().execute("SELECT COUNT(*) FROM docs").fetchone()[0]
    return {"ok": True, "documentos": n}
