"""Propone la carpeta de destino de un PDF nuevo a partir de la propia biblioteca.

Busca los documentos con el nombre más parecido (FTS5, bm25) y vota por sus carpetas:
lo ya clasificado sirve de ejemplo, así que cada fichero bien colocado mejora las propuestas.
"""

import re
import sqlite3

from .texto_pdf import normalizar

VECINOS = 25
# Palabras que no ayudan a decidir la carpeta.
VACIAS = set("""
the a an of and or for to in on at by with from vs pdf ebook book books vol volume part
de la el los las del y en con para un una le les du des et der die das und von zu mit
final web print hq hd bookmarked rev revised version edition ed copy scan english eng
""".split())  # noqa: SIM905


def palabras(texto: str) -> list[str]:
    # "Yokai_Companion", "DungeonCrawlerCarl" -> palabras separadas
    t = re.sub(r"([a-z])([A-Z])", r"\1 \2", texto or "")
    t = re.sub(r"\.(pdf|zip|rar|7z|cbz|cbr|epub)$", "", t, flags=re.I)
    vistas, res = set(), []
    for p in normalizar(t).split():
        if len(p) < 2 or p in VACIAS or p.isdigit() and len(p) < 4 or p in vistas:
            continue
        vistas.add(p)
        res.append(p)
    return res[:20]


def _valida(carpeta: str) -> bool:
    # Carpetas de trabajo de la reorganización: no son destinos.
    return bool(carpeta) and "/_" not in "/" + carpeta


def proponer(con: sqlite3.Connection, texto: str, editorial: str | None = None,
             excluir_id: int | None = None) -> dict:
    """{'propuesta', 'confianza' (0-1), 'apoyos' (nº de vecinos), 'alternativas': [[carpeta, %], ...]}"""
    ps = palabras(texto)
    vacio = {"propuesta": None, "confianza": 0.0, "apoyos": 0, "alternativas": []}
    if not ps:
        return vacio
    consulta = " OR ".join(f'"{p}"' for p in ps)
    filas = con.execute(
        "SELECT d.id, d.carpeta, COALESCE(d.editorial, d.ext_editorial) AS ed, bm25(docs_fts, 10.0, 3.0, 4.0, 1.0) AS r "
        "FROM docs_fts JOIN docs d ON d.id = docs_fts.rowid WHERE docs_fts MATCH ? ORDER BY r LIMIT ?",
        (consulta, VECINOS + 1),
    ).fetchall()
    filas = [f for f in filas if f["id"] != excluir_id][:VECINOS]
    if not filas:
        return vacio
    mejor_r = filas[0]["r"]  # bm25: más negativo = mejor
    votos: dict[str, float] = {}
    apoyos: dict[str, int] = {}
    ned = normalizar(editorial) if editorial else None
    for f in filas:
        if not _valida(f["carpeta"]):
            continue
        # Peso relativo al mejor resultado: los vecinos flojos casi no cuentan.
        peso = (f["r"] / mejor_r) ** 3 if mejor_r < 0 else 1.0
        if ned and f["ed"] and normalizar(f["ed"]) == ned:
            peso *= 2
        votos[f["carpeta"]] = votos.get(f["carpeta"], 0) + peso
        apoyos[f["carpeta"]] = apoyos.get(f["carpeta"], 0) + 1
    if not votos:
        return vacio
    total = sum(votos.values())
    orden = sorted(votos.items(), key=lambda x: -x[1])
    propuesta = orden[0][0]
    return {
        "propuesta": propuesta,
        "confianza": round(orden[0][1] / total, 3),
        "apoyos": apoyos[propuesta],
        "alternativas": [[c, round(v / total * 100)] for c, v in orden[:5]],
    }


def es_seguro(p: dict, umbral: float) -> bool:
    """¿Bastante claro para mover sin preguntar? Varios vecinos de acuerdo y mayoría amplia."""
    return bool(umbral) and p.get("propuesta") is not None and p["confianza"] >= umbral and p["apoyos"] >= 3
