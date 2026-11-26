"""Completa el catálogo con datos que no salen de la ruta.

    python -m biblioteca_rpg.enriquecer pdf                 # año, editorial e ISBN del texto de los PDF
    python -m biblioteca_rpg.enriquecer codigos             # código de la editorial (si el nombre no lo trae)
    python -m biblioteca_rpg.enriquecer geek --minutos 120  # RPGGeek / BGG (necesita BGG_TOKEN)
    python -m biblioteca_rpg.enriquecer geek --muestra 200  # prueba con 200 documentos al azar
    python -m biblioteca_rpg.enriquecer aplicar             # recalcula los valores efectivos
    python -m biblioteca_rpg.enriquecer resumen             # cuántos hay de cada cosa

Nunca toca la biblioteca ni los datos sacados de la ruta: escribe en la tabla `extra` y en las
columnas publicado, ext_editorial, autores, isbn y geek_id del catálogo.
"""

import argparse
import json
import random
import re
import sys
import time

from . import codigos as codigos_mod
from . import config as config_mod
from . import geek as geek_mod
from . import manual as manual_mod
from . import texto_pdf
from .db import conectar
from .indexador import en_paralelo

RE_LINEA_UTIL = re.compile(r"©|\(c\)|copyright|isbn", re.I)

# Tipos de documento que no tienen sentido en RPGGeek/BGG.
TIPOS_SIN_GEEK = ("Hojas de personaje", "Tokens", "Fan y homebrew", "Pintura y modelismo", "Listas de ejército")


# --- Texto de los PDF ---------------------------------------------------------

def _leer_pdf(ruta_pdf: str) -> dict:
    """En un proceso aparte: solo devuelve las líneas con copyright o ISBN (poco que pasar)."""
    import pymupdf

    pymupdf.TOOLS.mupdf_display_errors(False)
    pymupdf.TOOLS.mupdf_display_warnings(False)
    with pymupdf.open(ruta_pdf) as doc:
        texto = texto_pdf.leer_texto(doc)
    lineas = [ln for ln in texto.splitlines() if RE_LINEA_UTIL.search(ln)]
    return {"caracteres": len(texto.strip()), "lineas": "\n".join(lineas)[:20000]}


def pasada_pdf(con, cfg, limite: int | None = None, solo: list[str] | None = None) -> dict:
    sql = ("SELECT id, ruta, nombre FROM docs WHERE portada <> -1 AND NOT EXISTS "
           "(SELECT 1 FROM extra e WHERE e.doc_id = docs.id AND e.fuente = 'pdf')")
    params: list = []
    if solo:
        sql += " AND (" + " OR ".join("ruta LIKE ?" for _ in solo) + ")"
        params += [s.strip("/").replace("\\", "/") + "/%" for s in solo]
    sql += " ORDER BY id"
    if limite:
        sql += f" LIMIT {int(limite)}"
    filas = con.execute(sql, params).fetchall()
    if not filas:
        return {"leidos": 0}
    nombres = {f["id"]: f["nombre"] for f in filas}
    rutas = {f["id"]: f["ruta"] for f in filas}
    conocidas = [r[0] for r in con.execute("SELECT DISTINCT editorial FROM docs WHERE editorial IS NOT NULL")]
    dicc = texto_pdf.diccionario_editoriales(conocidas)
    total = len(filas)
    print(f"Texto de PDF pendiente: {total} (procesos: {cfg.procesos})", flush=True)
    cuenta = {"leidos": 0, "con_datos": 0, "sin_texto": 0, "errores": 0}
    lote: list = []
    inicio = time.time()

    def volcar():
        with con:
            con.executemany("INSERT OR REPLACE INTO extra (doc_id, fuente, estado, confianza, ref, datos, creado_en) "
                            "VALUES (?, 'pdf', ?, ?, NULL, ?, ?)", lote)
            aplicar(con, [x[0] for x in lote])
        lote.clear()
        hecho = cuenta["leidos"] + cuenta["errores"]
        ritmo = hecho / max(time.time() - inicio, 1)
        print(f"  {hecho}/{total}  con datos {cuenta['con_datos']}  sin texto {cuenta['sin_texto']}  "
              f"{ritmo:.1f}/s  quedan ~{(total - hecho) / ritmo / 60 if ritmo else 0:.0f} min", flush=True)

    def guardar(doc_id, res=None, error=None):
        ahora = int(time.time())
        if error is not None:
            cuenta["errores"] += 1
            lote.append((doc_id, "sin_resultado", 0.0, geek_mod.a_json({"error": str(error)[:300]}), ahora))
        else:
            cuenta["leidos"] += 1
            datos = texto_pdf.extraer(res["lineas"], nombres[doc_id], dicc, ruta=rutas[doc_id])
            if res["caracteres"] < 200:
                cuenta["sin_texto"] += 1
                datos["sin_texto"] = True
            if any(k in datos for k in ("publicado", "editorial", "isbn")):
                cuenta["con_datos"] += 1
                lote.append((doc_id, "auto", 0.8, geek_mod.a_json(datos), ahora))
            else:
                lote.append((doc_id, "sin_resultado", 0.0, geek_mod.a_json(datos), ahora))
        if len(lote) >= 100:
            volcar()

    try:
        en_paralelo(cfg, [(f["id"], f["ruta"]) for f in filas], _leer_pdf,
                    lambda doc_id, rel: (str(cfg.raiz / rel),), guardar)
    finally:
        if lote:
            volcar()
    return cuenta


# --- Código de catálogo de la editorial -----------------------------------------

def _leer_codigos(ruta_pdf: str) -> str:
    """En un proceso aparte: solo las líneas donde podría haber un código."""
    import pymupdf

    pymupdf.TOOLS.mupdf_display_errors(False)
    pymupdf.TOOLS.mupdf_display_warnings(False)
    with pymupdf.open(ruta_pdf) as doc:
        texto = texto_pdf.leer_texto(doc)
    return "\n".join(ln for ln in texto.splitlines() if codigos_mod.RE_CODIGO.search(ln))[:20000]


def pasada_codigos(con, cfg, limite: int | None = None, solo: list[str] | None = None) -> dict:
    """Busca el código en el texto de los PDF cuyo nombre no lo trae. Cada PDF se lee una vez."""
    sql = ("SELECT id, ruta, editorial_unif FROM docs WHERE portada <> -1 AND codigo IS NULL "
           "AND lower(ruta) LIKE '%.pdf' AND NOT EXISTS "
           "(SELECT 1 FROM extra e WHERE e.doc_id = docs.id AND e.fuente = 'codigo')")
    params: list = []
    if solo:
        sql += " AND (" + " OR ".join("ruta LIKE ?" for _ in solo) + ")"
        params += [s.strip("/").replace("\\", "/") + "/%" for s in solo]
    sql += " ORDER BY id"
    if limite:
        sql += f" LIMIT {int(limite)}"
    filas = {f["id"]: f for f in con.execute(sql, params).fetchall()}
    if not filas:
        return {"leidos": 0}
    total = len(filas)
    print(f"Códigos en el texto de los PDF: {total} pendientes (procesos: {cfg.procesos})", flush=True)
    cuenta = {"leidos": 0, "con_codigo": 0, "dudosos": 0, "errores": 0}
    lote: list = []
    inicio = time.time()

    def volcar():
        with con:
            con.executemany("INSERT OR REPLACE INTO extra (doc_id, fuente, estado, confianza, ref, datos, creado_en) "
                            "VALUES (?, 'codigo', ?, ?, NULL, ?, ?)", lote)
            aplicar(con, [x[0] for x in lote])
        lote.clear()
        hecho = cuenta["leidos"] + cuenta["errores"]
        ritmo = hecho / max(time.time() - inicio, 1)
        print(f"  {hecho}/{total}  con código {cuenta['con_codigo']}  dudosos {cuenta['dudosos']}  "
              f"{ritmo:.1f}/s  quedan ~{(total - hecho) / ritmo / 60 if ritmo else 0:.0f} min", flush=True)

    def guardar(doc_id, res=None, error=None):
        ahora = int(time.time())
        if error is not None:
            cuenta["errores"] += 1
            lote.append((doc_id, "sin_resultado", 0.0, geek_mod.a_json({"error": str(error)[:300]}), ahora))
        else:
            cuenta["leidos"] += 1
            f = filas[doc_id]
            codigo, candidatos = codigos_mod.del_texto(res, f["editorial_unif"], f["ruta"])
            if codigo:
                cuenta["con_codigo"] += 1
                lote.append((doc_id, "auto", 0.8, geek_mod.a_json({"codigo": codigo, "candidatos": candidatos}),
                             ahora))
            else:
                cuenta["dudosos"] += bool(candidatos)
                lote.append((doc_id, "sin_resultado", 0.0, geek_mod.a_json({"candidatos": candidatos}), ahora))
        if len(lote) >= 100:
            volcar()

    try:
        en_paralelo(cfg, [(i, f["ruta"]) for i, f in filas.items()], _leer_codigos,
                    lambda doc_id, rel: (str(cfg.raiz / rel),), guardar)
    finally:
        if lote:
            volcar()
    return cuenta


# --- RPGGeek / BGG -------------------------------------------------------------

def vecinos_geek(con, carpeta: str, sin: int | None = None, cache: dict | None = None) -> dict:
    """Perfil de lo ya emparejado en esa carpeta (lo que aceptaste pesa el doble que lo automático)."""
    if cache is not None and carpeta in cache:
        elegidos = cache[carpeta]
    else:
        elegidos = []
        for doc_id, estado, datos in con.execute(
                "SELECT e.doc_id, e.estado, e.datos FROM extra e JOIN docs d ON d.id = e.doc_id "
                "WHERE d.carpeta = ? AND e.fuente = 'geek' AND e.estado IN ('aceptado', 'auto')", (carpeta,)):
            elegido = json.loads(datos).get("elegido")
            if elegido:
                elegidos.append((doc_id, elegido, 2 if estado == "aceptado" else 1))
        if cache is not None:
            cache[carpeta] = elegidos
    return geek_mod.perfil_vecinos([(el, peso) for i, el, peso in elegidos if i != sin])


def reevaluar_geek(con, estados=("pendiente",), cliente=None, limite: int | None = None) -> dict:
    """Vuelve a emparejar los que están en esos estados con el método actual (casi todo sale de la
    caché de BGG). No toca lo que has decidido tú (aceptado/rechazado)."""
    estados = [e for e in estados if e not in ("aceptado", "rechazado")]
    filas = [dict(f) for f in con.execute(
        f"SELECT d.* FROM docs d JOIN extra e ON e.doc_id = d.id AND e.fuente = 'geek' "
        f"WHERE e.estado IN ({','.join('?' * len(estados))}) ORDER BY d.id", estados)]
    if limite:
        filas = filas[:limite]
    cliente = cliente or geek_mod.Cliente(con)
    cache: dict = {}
    cuenta: dict = {}
    for i, doc in enumerate(filas, 1):
        doc["vecinos"] = vecinos_geek(con, doc["carpeta"], doc["id"], cache)
        try:
            r = geek_mod.emparejar(cliente, doc)
        except geek_mod.SinToken:
            raise
        except Exception as ex:
            print(f"  error con {doc['ruta']}: {type(ex).__name__}: {ex}", file=sys.stderr)
            continue
        cuenta[r["estado"]] = cuenta.get(r["estado"], 0) + 1
        with con:
            con.execute("UPDATE extra SET estado=?, confianza=?, ref=?, datos=? WHERE doc_id=? AND fuente='geek' "
                        "AND estado NOT IN ('aceptado', 'rechazado')",
                        (r["estado"], r["confianza"], r["ref"], geek_mod.a_json(r["datos"]), doc["id"]))
            aplicar(con, [doc["id"]])
        if r["estado"] == "auto":
            cache.pop(doc["carpeta"], None)  # cuenta para los siguientes de la carpeta
        if i % 200 == 0:
            print(f"  {i}/{len(filas)}  {cuenta}  peticiones {cliente.peticiones}", flush=True)
    return cuenta


def pasada_geek(con, limite: int | None = None, minutos: float | None = None, muestra: int | None = None,
                solo: list[str] | None = None, cliente=None) -> dict:
    categorias = list(geek_mod.TIPO_POR_CATEGORIA)
    sql = (f"SELECT * FROM docs WHERE categoria IN ({','.join('?' * len(categorias))}) "
           f"AND (tipo IS NULL OR tipo NOT IN ({','.join('?' * len(TIPOS_SIN_GEEK))})) "
           "AND NOT EXISTS (SELECT 1 FROM extra e WHERE e.doc_id = docs.id AND e.fuente = 'geek')")
    params: list = [*categorias, *TIPOS_SIN_GEEK]
    if solo:
        sql += " AND (" + " OR ".join("ruta LIKE ?" for _ in solo) + ")"
        params += [s.strip("/").replace("\\", "/") + "/%" for s in solo]
    filas = [dict(f) for f in con.execute(sql + " ORDER BY id", params)]
    if muestra:
        random.seed(42)
        filas = random.sample(filas, min(muestra, len(filas)))
    if limite:
        filas = filas[:limite]
    cliente = cliente or geek_mod.Cliente(con)
    fin = time.time() + minutos * 60 if minutos else None
    cuenta = {"auto": 0, "pendiente": 0, "sin_resultado": 0, "errores": 0}
    print(f"RPGGeek/BGG: {len(filas)} documentos por buscar", flush=True)
    for i, doc in enumerate(filas, 1):
        if fin and time.time() > fin:
            print("  tiempo agotado; se sigue en la próxima pasada")
            break
        doc["vecinos"] = vecinos_geek(con, doc["carpeta"], doc["id"])
        try:
            r = geek_mod.emparejar(cliente, doc)
        except geek_mod.SinToken:
            raise
        except Exception as ex:
            cuenta["errores"] += 1
            print(f"  error con {doc['ruta']}: {type(ex).__name__}: {ex}", file=sys.stderr)
            continue
        cuenta[r["estado"]] += 1
        with con:
            con.execute("INSERT OR REPLACE INTO extra (doc_id, fuente, estado, confianza, ref, datos, creado_en) "
                        "VALUES (?, 'geek', ?, ?, ?, ?, ?)",
                        (doc["id"], r["estado"], r["confianza"], r["ref"], geek_mod.a_json(r["datos"]),
                         int(time.time())))
            aplicar(con, [doc["id"]])
        if i % 25 == 0:
            print(f"  {i}/{len(filas)}  {cuenta}  peticiones {cliente.peticiones}", flush=True)
    return cuenta


# --- Valores efectivos ----------------------------------------------------------

# Año en el nombre del fichero: entre corchetes o paréntesis ("[2013]", "(MACE 2013)"), junto a un mes
# ("April 1983", "2019-12") o, fuera de los wargames, suelto ("Gongfarmer's Almanac 2019"). Desde 1960:
# antes suelen ser batallas ("Peleliu 1944"); y fuera los años que son parte del título.
_ANIO = r"(19[6-9]\d|20[0-9]\d)"
_MES = (r"(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec|ene|abr|ago|dic)[a-z]*\.?|spring|summer|autumn|fall|"
        r"winter|primavera|verano|oto[ñn]o|invierno")
# Sin letras pegadas: "GDW2017" es un código y "Fox2000" un usuario.
RE_ANIO_CAJA = re.compile(r"[\[(][^\])]*?(?<![A-Za-z\d])" + _ANIO + r"(?![A-Za-z\d])[^\])]*[\])]")
RE_ANIO_FECHA = re.compile(rf"(?<![a-z])({_MES})[\s_-]*{_ANIO}(?!\d)|(?<![\d.]){_ANIO}[\s_-]+(0[1-9]|1[0-2])(?!\d)|"
                           rf"(?<![\d.])(0?[1-9]|1[0-2])[-/]{_ANIO}(?!\d)", re.I)
RE_ANIO_SUELTO = re.compile(r"(?<![A-Za-z\d.,])" + _ANIO + r"(?![A-Za-z\d.,]|'s\b)")  # no "1990's"
RE_TITULO_CON_ANIO = re.compile(r"cyberpunk[\s_-]*20[0-9]{2}|twilight[\s_:-]*20[0-9]{2}|2300[\s_]*ad|"
                                r"space[\s_-]*1889|\b1984\b|year[\s_-]*20[0-9]{2}|datafortress[\s_-]*2020|"
                                r"pacesetter[\s_-]*\d{4}", re.I)


def anio_del_nombre(nombre: str, ruta: str = "", anio_max: int | None = None) -> int | None:
    anio_max = anio_max or time.localtime().tm_year
    s = RE_TITULO_CON_ANIO.sub(" ", re.sub(r"\.[a-z0-9]{2,4}$", "", nombre or "", flags=re.I))
    patrones = [RE_ANIO_CAJA, RE_ANIO_FECHA] + ([] if (ruta or "").startswith("Wargames/") else [RE_ANIO_SUELTO])
    for patron in patrones:
        for m in patron.finditer(s):
            anio = int(next(g for g in m.groups() if g and len(g) == 4 and g.isdigit()))
            if anio <= anio_max:
                return anio
    return None


# En BoardGameGeek la ficha es la del juego: su año vale para el reglamento y los componentes,
# no para un suplemento, una campaña o una revista de ese juego publicados años después.
TIPOS_DEL_PROPIO_JUEGO = (None, "Reglas", "Ayudas de juego")


def _anio_geek_vale(g: dict, ruta: str) -> bool:
    if g.get("tipo") != "boardgame" or g.get("version_por"):
        return True
    from .rutas import analizar
    return not ruta or analizar(ruta).tipo in TIPOS_DEL_PROPIO_JUEGO


def _efectivos(extras: dict, nombre: str = "", ruta: str = "") -> dict:
    """extras: {fuente: datos} ya aceptados -> columnas del catálogo (el nombre, para el año si falta)."""
    pdf = extras.get("pdf") or {}
    g = (extras.get("geek") or {}).get("elegido") or {}
    ref = extras.get("ref") or {}  # listas de wikis (referencias.py)
    v: dict = {"publicado": None, "publicado_mes": None, "ext_editorial": None, "autores": None, "isbn": None,
               "geek_id": None, "geek_tipo": None, "ext_codigo": None, "ext_codigo_orden": None}
    origen: dict = {}
    if g.get("anio") and _anio_geek_vale(g, ruta):
        v["publicado"], origen["publicado"] = g["anio"], "geek"
        if ref.get("publicado") == g["anio"]:
            v["publicado_mes"] = ref.get("mes")
    elif ref.get("publicado"):
        v["publicado"], v["publicado_mes"], origen["publicado"] = ref["publicado"], ref.get("mes"), "ref"
    elif pdf.get("publicado"):
        v["publicado"], origen["publicado"] = pdf["publicado"], "pdf"
    elif anio_del_nombre(nombre, ruta):
        v["publicado"], origen["publicado"] = anio_del_nombre(nombre, ruta), "nombre"
    if g.get("editoriales"):
        v["ext_editorial"], origen["editorial"] = g["editoriales"][0], "geek"
    elif ref.get("editorial"):
        v["ext_editorial"], origen["editorial"] = ref["editorial"], "ref"
    elif pdf.get("editorial") and (pdf["editorial"] != "Wizards of the Coast" or texto_pdf.es_de_wotc(ruta)):
        v["ext_editorial"], origen["editorial"] = pdf["editorial"], "pdf"
    if g.get("autores"):
        v["autores"], origen["autores"] = ", ".join(g["autores"][:6]), "geek"
    if pdf.get("isbn"):
        v["isbn"] = pdf["isbn"][0]
    if g.get("id"):
        v["geek_id"], v["geek_tipo"] = g["id"], g.get("tipo")
    codigo = (extras.get("codigo") or {}).get("codigo")
    if codigo:
        v["ext_codigo"], v["ext_codigo_orden"], origen["codigo"] = codigo, codigos_mod.orden(codigo), "pdf"
    v["origen"] = json.dumps(origen) if origen else None
    return v


def _a_mano(v: dict, man: dict) -> None:
    """Lo escrito a mano en la ficha manda (origen «mano»)."""
    origen = json.loads(v["origen"]) if v["origen"] else {}
    for campo in manual_mod.DE_EXTRA:
        if campo in man:
            v[campo] = man[campo]
            origen[{"publicado_mes": "publicado"}.get(campo, campo)] = "mano"
    if "publicado" in man and "publicado_mes" not in man and origen.get("publicado") == "mano":
        v["publicado_mes"] = None  # el mes de otra fuente no vale para un año puesto a mano
    if "editorial" in man:
        origen["editorial"] = "mano"
    v["origen"] = json.dumps(origen) if origen else None


COLS = ["publicado", "publicado_mes", "ext_editorial", "autores", "isbn", "geek_id", "geek_tipo", "ext_codigo", "ext_codigo_orden",
        "origen"]


def aplicar(con, ids: list[int] | None = None) -> int:
    """Escribe en docs los valores que salen de los extras aceptados. Devuelve cuántos cambiaron."""
    filtro, params = "", []
    if ids is not None:
        if not ids:
            return 0
        filtro = f" AND doc_id IN ({','.join('?' * len(ids))})"
        params = list(ids)
    por_doc: dict[int, dict] = {}
    for f in con.execute(f"SELECT doc_id, fuente, datos FROM extra WHERE estado IN ('auto', 'aceptado'){filtro}",
                         params):
        por_doc.setdefault(f[0], {})[f[1]] = json.loads(f[2])
    a_mano = manual_mod.valores(con, ids)
    if ids is None:
        actuales_sql = "SELECT id, " + ", ".join(COLS) + " FROM docs WHERE origen IS NOT NULL OR isbn IS NOT NULL"
        candidatos = {r[0] for r in con.execute(actuales_sql)} | set(por_doc) | set(a_mano) | {
            r[0] for r in con.execute("SELECT id, nombre, ruta FROM docs WHERE publicado IS NULL")
            if anio_del_nombre(r[1], r[2])}
    else:
        candidatos = set(ids)
    cambios = []
    ahora = int(time.time())
    lista = list(candidatos)
    for i in range(0, len(lista), 500):
        trozo = lista[i:i + 500]
        filas = con.execute(f"SELECT id, nombre, ruta, {', '.join(COLS)} FROM docs "
                            f"WHERE id IN ({','.join('?' * len(trozo))})", trozo)
        for f in filas:
            nuevo = _efectivos(por_doc.get(f[0], {}), f["nombre"], f["ruta"])
            if f[0] in a_mano:
                _a_mano(nuevo, a_mano[f[0]])
            if any(nuevo[c] != f[c] for c in COLS):
                cambios.append([*(nuevo[c] for c in COLS), ahora, f[0]])
    if cambios:
        con.executemany(f"UPDATE docs SET {', '.join(c + '=?' for c in COLS)}, actualizado_en=? WHERE id=?",
                        cambios)
    if cambios and ids is None:
        # Pueden haber aparecido formas nuevas de editoriales. (Con ids sueltos, desde la web,
        # bastan los disparadores con los alias que ya hay.)
        from .editoriales import recalcular
        recalcular(con)
    return len(cambios)


def resumen(con) -> None:
    for f in con.execute("SELECT fuente, estado, COUNT(*) FROM extra GROUP BY 1, 2 ORDER BY 1, 2"):
        print(f"  {f[0]:5} {f[1]:14} {f[2]:>7}")
    tot = con.execute("SELECT COUNT(*), COUNT(publicado), COUNT(COALESCE(editorial, ext_editorial)), "
                      "COUNT(isbn), COUNT(geek_id), COUNT(editorial) FROM docs").fetchone()
    print(f"  documentos {tot[0]}  con año de publicación {tot[1]}  con editorial {tot[2]} "
          f"(de la ruta {tot[5]})  con ISBN {tot[3]}  enlazados a RPGGeek/BGG {tot[4]}")
    cod = con.execute("SELECT COUNT(codigo), COUNT(ext_codigo) FROM docs").fetchone()
    print(f"  con código de editorial {cod[0] + cod[1]} (del nombre {cod[0]}, del PDF {cod[1]})")


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description="Completa el catálogo con datos del PDF y de RPGGeek/BGG.")
    ap.add_argument("orden", choices=["pdf", "codigos", "geek", "reevaluar", "aplicar", "resumen"])
    ap.add_argument("--limite", type=int)
    ap.add_argument("--minutos", type=float, help="geek: parar tras estos minutos")
    ap.add_argument("--muestra", type=int, help="geek: documentos al azar (para probar)")
    ap.add_argument("--solo", action="append", help="limitar a una carpeta (relativa a la raíz)")
    args = ap.parse_args(argv)

    cfg = config_mod.cargar()
    con = conectar(cfg.db)
    t = time.time()
    if args.orden == "pdf":
        print("Texto de PDF:", pasada_pdf(con, cfg, args.limite, args.solo), f"({time.time() - t:.0f} s)")
    elif args.orden == "codigos":
        print("Códigos:", pasada_codigos(con, cfg, args.limite, args.solo), f"({time.time() - t:.0f} s)")
    elif args.orden == "geek":
        try:
            print("RPGGeek/BGG:", pasada_geek(con, args.limite, args.minutos, args.muestra, args.solo))
        except geek_mod.SinToken as ex:
            print(f"RPGGeek/BGG: no se consulta. {ex}")
    elif args.orden == "reevaluar":
        print("Reevaluados:", reevaluar_geek(con, limite=args.limite), f"({time.time() - t:.0f} s)")
    elif args.orden == "aplicar":
        with con:
            print("Cambiados:", aplicar(con))
    resumen(con)
    con.close()


if __name__ == "__main__":
    main()
