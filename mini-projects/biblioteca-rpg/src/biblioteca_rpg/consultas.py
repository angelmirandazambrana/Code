"""Búsqueda con filtros y recuento por faceta."""

import re
import sqlite3

from .codigos import orden as orden_codigo

# faceta -> etiqueta en la web
FACETAS = {
    "categoria": "Categoría",
    "formato": "Formato",
    "sistema": "Sistema / línea",
    "edicion": "Edición",
    "editorial": "Editorial",
    "tipo": "Tipo",
    "ambientacion": "Ambientación",
    "idioma": "Idioma",
    "decada": "Década",
}
SIN_VALOR = "(sin indicar)"
NO = "no_"  # prefijo de los filtros de exclusión: ?no_editorial=X = "todo menos X"
POR_PAGINA = 60

ORDENES = {
    "relevancia": "Relevancia",
    "titulo": "Título",
    "recientes": "Llegados recientemente",
    "anio": "Año",
    "paginas": "Nº de páginas",
    "codigo": "Código de editorial",
}
CODIGO_ORDEN = "COALESCE(codigo_orden, ext_codigo_orden)"
# "MGP40039", "mgp 40039", "TSR-9123": una búsqueda que es un código.
RE_BUSCA_CODIGO = re.compile(r"^\s*([A-Za-z]{2,4}7?)[ _#.-]?(\d{1,6}[A-Za-z]?)\s*$")


def consulta_fts(texto: str) -> str | None:
    """'shadowrun 3e core' -> '"shadowrun"* "3e"* "core"*' (todas las palabras, por prefijo)."""
    palabras = re.findall(r"[\w&'.-]+", texto, flags=re.UNICODE)
    palabras = [p.replace('"', "") for p in palabras if p.strip(".-'")]
    if not palabras:
        return None
    return " ".join(f'"{p}"*' for p in palabras)


def _expr(faceta: str) -> str:
    if faceta == "decada":
        # Año de publicación si se conoce (PDF o RPGGeek); si no, el de la edición de la ruta.
        return "CASE WHEN COALESCE(publicado, anio) IS NULL THEN NULL ELSE (COALESCE(publicado, anio) / 10) * 10 END"
    if faceta == "editorial":
        return "editorial_unif"  # nombre unificado (editoriales.py)
    return faceta


def construir_where(filtros: dict, q: str | None, carpeta: str | None, excluir: str | None = None):
    condiciones, params = [], []
    if q:
        fts = consulta_fts(q)
        m = RE_BUSCA_CODIGO.match(q)
        if fts and m:
            # También por el código guardado (puede venir del PDF y no estar en el nombre).
            # Se compara la clave de orden: "DCC 71" encuentra "DCC071".
            clave = orden_codigo((m.group(1) + m.group(2)).upper())
            condiciones.append("(docs.id IN (SELECT rowid FROM docs_fts WHERE docs_fts MATCH ?) "
                               "OR codigo_orden = ? OR ext_codigo_orden = ?)")
            params += [fts, clave, clave]
        elif fts:
            condiciones.append("docs.id IN (SELECT rowid FROM docs_fts WHERE docs_fts MATCH ?)")
            params.append(fts)
    if carpeta:
        carpeta = carpeta.strip("/")
        # carpeta exacta o cualquier subcarpeta ('/' + 1 = '0')
        condiciones.append("(carpeta = ? OR (carpeta >= ? AND carpeta < ?))")
        params += [carpeta, carpeta + "/", carpeta + "0"]
    for clave, valores in filtros.items():
        negado = clave.startswith(NO)
        faceta = clave[len(NO):] if negado else clave
        if faceta == excluir or not valores or faceta not in FACETAS:
            continue
        expr = _expr(faceta)
        partes = []
        normales = [v for v in valores if v != SIN_VALOR]
        if faceta == "decada":
            normales = [int(v) for v in normales if str(v).isdigit()]
        sin_valor = SIN_VALOR in valores
        if negado:
            # "Todo menos X": lo que no tiene valor tampoco es X, así que se queda (salvo que se excluya).
            if normales:
                no_en = f"{expr} NOT IN ({','.join('?' * len(normales))})"
                partes.append(no_en if sin_valor else f"({expr} IS NULL OR {no_en})")
                params += normales
            if sin_valor:
                partes.append(f"{expr} IS NOT NULL")
            if partes:
                condiciones.append("(" + " AND ".join(partes) + ")")
            continue
        if normales:
            partes.append(f"{expr} IN ({','.join('?' * len(normales))})")
            params += normales
        if sin_valor:
            partes.append(f"{expr} IS NULL")
        if partes:
            condiciones.append("(" + " OR ".join(partes) + ")")
    where = (" WHERE " + " AND ".join(condiciones)) if condiciones else ""
    return where, params


def buscar(con: sqlite3.Connection, filtros: dict, q: str | None, carpeta: str | None,
           orden: str, pagina: int) -> dict:
    where, params = construir_where(filtros, q, carpeta)
    total = con.execute(f"SELECT COUNT(*) FROM docs{where}", params).fetchone()[0]

    if orden == "relevancia" and q and consulta_fts(q):
        # bm25 con más peso al título que a la carpeta
        sql_orden = (
            "ORDER BY (SELECT bm25(docs_fts, 10.0, 10.0, 2.0, 4.0, 1.0) FROM docs_fts "
            "WHERE docs_fts MATCH ? AND rowid = docs.id), COALESCE(ext_titulo, titulo) COLLATE NOCASE"
        )
        params_orden = [consulta_fts(q)]
    else:
        sql_orden = {
            "recientes": "ORDER BY llegado_en DESC, id DESC",
            "anio": "ORDER BY COALESCE(publicado, anio) IS NULL, COALESCE(publicado, anio), COALESCE(ext_titulo, titulo) COLLATE NOCASE",
            "paginas": "ORDER BY paginas IS NULL, paginas DESC",
            # Los que no tienen código, al final; los que sí, por prefijo y número.
            "codigo": f"ORDER BY {CODIGO_ORDEN} IS NULL, {CODIGO_ORDEN}, COALESCE(ext_titulo, titulo) COLLATE NOCASE",
        }.get(orden, "ORDER BY COALESCE(ext_titulo, titulo) COLLATE NOCASE")
        params_orden = []
    desplaz = (max(pagina, 1) - 1) * POR_PAGINA
    filas = con.execute(
        f"SELECT * FROM docs{where} {sql_orden} LIMIT {POR_PAGINA} OFFSET {desplaz}",
        params + params_orden,
    ).fetchall()
    return {"total": total, "filas": filas, "paginas": max(1, -(-total // POR_PAGINA))}


def facetas(con: sqlite3.Connection, filtros: dict, q: str | None, carpeta: str | None) -> dict:
    """Recuentos de cada faceta, aplicando todos los filtros menos los de la propia faceta.

    Así un valor excluido sigue en la lista, con su recuento, y se puede volver a incluir.
    """
    res = {}
    for faceta in FACETAS:
        where, params = construir_where(filtros, q, carpeta, excluir=faceta)
        expr = _expr(faceta)
        filas = con.execute(
            f"SELECT {expr} AS v, COUNT(*) AS n FROM docs{where} GROUP BY v ORDER BY n DESC",
            params,
        ).fetchall()
        valores = [(SIN_VALOR if f["v"] is None else str(f["v"]), f["n"]) for f in filas]
        # Una faceta con un solo valor "(sin indicar)" no aporta nada.
        if len(valores) == 1 and valores[0][0] == SIN_VALOR and not filtros.get(faceta) and not filtros.get(NO + faceta):
            continue
        if valores:
            res[faceta] = valores
    return res


def subcarpetas(con: sqlite3.Connection, filtros: dict, q: str | None, carpeta: str | None) -> list:
    """Hijos directos de la carpeta actual (o de la raíz), con su recuento."""
    where, params = construir_where(filtros, q, carpeta)
    base = (carpeta or "").strip("/")
    corte = len(base) + 1 if base else 0
    filas = con.execute(f"SELECT carpeta, COUNT(*) AS n FROM docs{where} GROUP BY carpeta", params)
    hijos: dict[str, int] = {}
    for f in filas:
        c = f["carpeta"] or ""
        if base and c == base:
            continue
        resto = c[corte:]
        if not resto:
            continue
        hijo = resto.split("/", 1)[0]
        hijos[hijo] = hijos.get(hijo, 0) + f["n"]
    return sorted(hijos.items(), key=lambda x: x[0].lower())
