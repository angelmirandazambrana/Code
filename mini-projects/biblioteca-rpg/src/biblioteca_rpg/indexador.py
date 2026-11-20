"""Indexador: recorre la biblioteca, actualiza el catálogo y genera las portadas.

Solo LEE la biblioteca. Escribe únicamente en la carpeta de datos (catálogo y miniaturas).

    python -m biblioteca_rpg.indexador                 # pasada completa
    python -m biblioteca_rpg.indexador --sin-portadas  # solo recorrer y actualizar el catálogo
    python -m biblioteca_rpg.indexador --limite 500    # portadas: como máximo 500 en esta pasada
    python -m biblioteca_rpg.indexador --solo "RPG/Shadowrun" --solo "Magazines/Dragon Magazine"

Se puede interrumpir y relanzar cuando se quiera: solo procesa lo nuevo o lo cambiado.
"""

import argparse
import concurrent.futures as cf
import os
import sys
import time
from pathlib import Path

from . import config as config_mod
from .db import CAMPOS_RUTA, conectar
from .rutas import analizar

EXTENSIONES = {".pdf", ".epub", ".mobi"}
EBOOKS = {".epub", ".mobi"}  # libros sin páginas fijas: el nº de páginas depende de la pantalla
ANCHO_PORTADA = 360
TIEMPO_MAX_PDF = 90  # segundos por fichero antes de darlo por colgado


def ruta_portada(dir_portadas: Path, doc_id: int) -> Path:
    return dir_portadas / f"{doc_id // 1000:03d}" / f"{doc_id}.jpg"


# --- Recorrido ---------------------------------------------------------------

def recorrer(raiz: Path, carpetas: list[str]):
    """Devuelve (ruta_relativa_con_barras, tamaño, mtime) de cada fichero indexable."""
    pila = [raiz / c for c in carpetas if (raiz / c).is_dir()]
    while pila:
        d = pila.pop()
        try:
            with os.scandir(d) as it:
                for e in it:
                    try:
                        if e.is_dir(follow_symlinks=False):
                            if not e.name.startswith(".") and e.name != "@eaDir":
                                pila.append(Path(e.path))
                        elif os.path.splitext(e.name)[1].lower() in EXTENSIONES:
                            st = e.stat(follow_symlinks=False)
                            rel = Path(e.path).relative_to(raiz).as_posix()
                            yield rel, st.st_size, int(st.st_mtime)
                    except OSError as ex:
                        print(f"  aviso: {e.path}: {ex}", file=sys.stderr)
        except OSError as ex:
            print(f"  aviso: no puedo leer {d}: {ex}", file=sys.stderr)


def sincronizar(con, cfg, carpetas: list[str]) -> dict:
    """Pone el catálogo al día con lo que hay en disco (sin abrir los PDF)."""
    ahora = int(time.time())
    existentes = {
        r["ruta"]: (r["id"], r["tamano"], r["mtime"])
        for r in con.execute("SELECT id, ruta, tamano, mtime FROM docs")
    }
    vistos = set()
    nuevos, cambiados, iguales = [], [], []
    for rel, tam, mt in recorrer(cfg.raiz, carpetas):
        vistos.add(rel)
        datos = analizar(rel).dict()
        fila = [datos[c] for c in CAMPOS_RUTA]
        previo = existentes.get(rel)
        if previo is None:
            nuevos.append([rel, *fila, tam, mt, ahora, ahora])
        elif previo[1] != tam or previo[2] != mt:
            cambiados.append([*fila, tam, mt, ahora, previo[0]])
        else:
            iguales.append([*fila, previo[0]])

    cols = ", ".join(CAMPOS_RUTA)
    marcas = ", ".join("?" * len(CAMPOS_RUTA))
    asignar = ", ".join(f"{c}=?" for c in CAMPOS_RUTA)
    with con:
        con.executemany(
            f"INSERT INTO docs (ruta, {cols}, tamano, mtime, llegado_en, actualizado_en) "
            f"VALUES (?, {marcas}, ?, ?, ?, ?)",
            nuevos,
        )
        # Cambió el fichero: hay que volver a sacar portada y datos del PDF.
        con.executemany(
            f"UPDATE docs SET {asignar}, tamano=?, mtime=?, actualizado_en=?, portada=0, error=NULL "
            f"WHERE id=?",
            cambiados,
        )
        # Y a releer su texto.
        con.executemany("DELETE FROM extra WHERE doc_id=? AND fuente='pdf'", [(c[-1],) for c in cambiados])
        # Mismo fichero: se recalculan los datos de la ruta por si mejoró el analizador.
        con.executemany(f"UPDATE docs SET {asignar} WHERE id=?", iguales)

    # Borrados: solo dentro de las carpetas recorridas.
    prefijos = tuple(c.rstrip("/") + "/" for c in carpetas)
    desaparecidos = [
        (ruta, v[0]) for ruta, v in existentes.items()
        if ruta.startswith(prefijos) and ruta not in vistos
    ]
    with con:
        con.executemany("DELETE FROM docs WHERE id=?", [(i,) for _, i in desaparecidos])
    for _, i in desaparecidos:
        ruta_portada(cfg.portadas, i).unlink(missing_ok=True)

    if nuevos or cambiados or desaparecidos:
        from .editoriales import recalcular  # aquí: editoriales importa db y texto_pdf
        recalcular(con)

    return {"nuevos": len(nuevos), "cambiados": len(cambiados), "iguales": len(iguales),
            "borrados": len(desaparecidos)}


# --- Portadas (en procesos aparte: un PDF roto no tumba el indexador) --------

def _procesar_pdf(ruta_pdf: str, ruta_jpg: str) -> dict:
    import pymupdf

    pymupdf.TOOLS.mupdf_display_errors(False)
    pymupdf.TOOLS.mupdf_display_warnings(False)
    with pymupdf.open(ruta_pdf) as doc:
        meta = doc.metadata or {}
        # En un ebook contar páginas obliga a maquetar el libro entero (lento y sin sentido).
        ebook = Path(ruta_pdf).suffix.lower() in EBOOKS
        res = {
            "paginas": None if ebook else doc.page_count,
            "pdf_titulo": (meta.get("title") or "").strip()[:300] or None,
            "pdf_autor": (meta.get("author") or "").strip()[:300] or None,
        }
        if ebook or doc.page_count:
            pagina = doc.load_page(0)  # en un epub, la portada
            ancho = pagina.rect.width or 600
            alto = pagina.rect.height or 800
            zoom = ANCHO_PORTADA / ancho
            zoom = min(zoom, 1400 / alto, 4.0)  # páginas raras (tiras, pósteres)
            pix = pagina.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), alpha=False)
            Path(ruta_jpg).parent.mkdir(parents=True, exist_ok=True)
            pix.save(ruta_jpg, jpg_quality=78)
    return res


def _nuevo_pool(n):
    # Cada proceso se renueva tras 40 PDF: MuPDF no siempre devuelve la memoria al sistema.
    return cf.ProcessPoolExecutor(max_workers=n, max_tasks_per_child=40)


def _matar_pool(pool):
    for p in list(getattr(pool, "_processes", {}).values()):
        try:
            p.kill()
        except Exception:
            pass
    pool.shutdown(wait=False, cancel_futures=True)


def en_paralelo(cfg, pendientes: list, funcion, args_de, al_acabar) -> None:
    """Ejecuta funcion(*args_de(doc_id, rel)) para cada (doc_id, rel) en procesos aparte.

    al_acabar(doc_id, res=..., error=...) se llama en el proceso principal con cada resultado.
    Un PDF que cuelga o tumba un proceso se marca con error y el resto sigue.
    """
    rutas = dict(pendientes)
    cola = list(pendientes)
    pool = _nuevo_pool(cfg.procesos)
    try:
        en_vuelo: dict = {}
        while cola or en_vuelo:
            while cola and len(en_vuelo) < cfg.procesos * 3:
                doc_id, rel = cola.pop(0)
                f = pool.submit(funcion, *args_de(doc_id, rel))
                en_vuelo[f] = (doc_id, time.time())
            listos, _ = cf.wait(list(en_vuelo), timeout=5, return_when=cf.FIRST_COMPLETED)
            roto = False
            for f in listos:
                doc_id, _t = en_vuelo.pop(f)
                try:
                    al_acabar(doc_id, res=f.result())
                except cf.process.BrokenProcessPool:
                    roto = True
                    cola.insert(0, (doc_id, rutas[doc_id]))
                except Exception as ex:
                    al_acabar(doc_id, error=f"{type(ex).__name__}: {ex}")
            colgados = [f for f, (_, t) in en_vuelo.items() if time.time() - t > TIEMPO_MAX_PDF]
            if roto or colgados:
                # Un PDF ha colgado o tumbado un proceso: se marca y se rehace el pool.
                for f in colgados:
                    doc_id, _ = en_vuelo.pop(f)
                    al_acabar(doc_id, error="tiempo agotado")
                for f, (doc_id, _) in en_vuelo.items():
                    cola.insert(0, (doc_id, rutas[doc_id]))
                en_vuelo.clear()
                _matar_pool(pool)
                pool = _nuevo_pool(cfg.procesos)
                if roto:
                    # El culpable es uno de los reencolados: se prueban de uno en uno.
                    doc_id, rel = cola.pop(0)
                    try:
                        with _nuevo_pool(1) as p1:
                            res = p1.submit(funcion, *args_de(doc_id, rel)).result(timeout=TIEMPO_MAX_PDF)
                    except Exception as ex:
                        al_acabar(doc_id, error=f"proceso caído: {type(ex).__name__}")
                    else:
                        al_acabar(doc_id, res=res)
    finally:
        _matar_pool(pool)


def generar_portadas(con, cfg, limite: int | None, solo: list[str] | None) -> dict:
    sql = "SELECT id, ruta FROM docs WHERE portada=0"
    params: list = []
    if solo:
        sql += " AND (" + " OR ".join("ruta LIKE ?" for _ in solo) + ")"
        params += [s.rstrip("/") + "/%" for s in solo]
    sql += " ORDER BY llegado_en DESC, id"
    if limite:
        sql += f" LIMIT {int(limite)}"
    pendientes = [(r["id"], r["ruta"]) for r in con.execute(sql, params)]
    total = len(pendientes)
    if not total:
        return {"hechas": 0, "errores": 0}

    print(f"Portadas pendientes: {total} (procesos: {cfg.procesos})")
    hechas = errores = 0
    inicio = time.time()
    lote: list = []

    def guardar(doc_id, res=None, error=None):
        nonlocal hechas, errores
        if error is None:
            hechas += 1
            lote.append((res["paginas"], res["pdf_titulo"], res["pdf_autor"], 1, None, doc_id))
        else:
            errores += 1
            lote.append((None, None, None, -1, str(error)[:500], doc_id))
        if len(lote) >= 50:
            volcar()

    def volcar():
        with con:
            con.executemany(
                "UPDATE docs SET paginas=COALESCE(?, paginas), pdf_titulo=?, pdf_autor=?, portada=?, error=? "
                "WHERE id=?",
                lote,
            )
        lote.clear()
        hecho = hechas + errores
        ritmo = hecho / max(time.time() - inicio, 1)
        resta = (total - hecho) / ritmo if ritmo else 0
        print(f"  {hecho}/{total}  errores {errores}  {ritmo:.1f}/s  quedan ~{resta / 60:.0f} min", flush=True)

    try:
        en_paralelo(cfg, pendientes, _procesar_pdf,
                    lambda doc_id, rel: (str(cfg.raiz / rel), str(ruta_portada(cfg.portadas, doc_id))),
                    guardar)
    finally:
        if lote:
            volcar()
    return {"hechas": hechas, "errores": errores}


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description="Indexa la biblioteca de PDFs.")
    ap.add_argument("--sin-portadas", action="store_true", help="no abrir los PDF; solo el catálogo")
    ap.add_argument("--solo-portadas", action="store_true",
                    help="no recorrer la biblioteca; solo las portadas pendientes del catálogo")
    ap.add_argument("--limite", type=int, help="máximo de portadas en esta pasada")
    ap.add_argument("--solo", action="append", help="limitar a una carpeta (relativa a la raíz)")
    ap.add_argument("--reintentar-errores", action="store_true", help="volver a intentar los PDF con error")
    args = ap.parse_args(argv)

    cfg = config_mod.cargar()
    if not cfg.raiz.is_dir():
        sys.exit(f"No encuentro la biblioteca en {cfg.raiz}")
    con = conectar(cfg.db)
    carpetas = [s.strip("/\\").replace("\\", "/") for s in args.solo] if args.solo else cfg.areas

    t = time.time()
    if not args.solo_portadas:
        print(f"Recorriendo {cfg.raiz} -> {', '.join(carpetas)}")
        r = sincronizar(con, cfg, carpetas)
        print(f"Catálogo: {r['nuevos']} nuevos, {r['cambiados']} cambiados, {r['iguales']} sin cambios, "
              f"{r['borrados']} borrados ({time.time() - t:.0f} s)")

    if args.reintentar_errores:
        with con:
            con.execute("UPDATE docs SET portada=0, error=NULL WHERE portada=-1")
    if not args.sin_portadas:
        p = generar_portadas(con, cfg, args.limite, carpetas if args.solo else None)
        print(f"Portadas: {p['hechas']} hechas, {p['errores']} con error ({time.time() - t:.0f} s en total)")
    con.execute("PRAGMA optimize")
    con.close()


if __name__ == "__main__":
    main()
