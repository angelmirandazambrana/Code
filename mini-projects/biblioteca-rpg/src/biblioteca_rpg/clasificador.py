"""Propone la carpeta de destino de un PDF nuevo a partir de la propia biblioteca.

Busca los documentos con el nombre más parecido (FTS5, bm25) y vota por sus carpetas:
lo ya clasificado sirve de ejemplo, así que cada fichero bien colocado mejora las propuestas.
"""

import math
import re
import sqlite3

from .texto_pdf import normalizar

VECINOS = 25
# Palabras que no ayudan a decidir la carpeta.
VACIAS = set("""
the a an of and or for to in on at by with from vs pdf ebook book books vol volume part
de la el los las del y en con para un una le les du des et der die das und von zu mit
final web print hq hd bookmarked rev revised version edition ed copy scan english eng
libro libros basico basica edicion castellano espanol traducido traduccion
""".split())  # noqa: SIM905
# "9 Edicion", "9ª edición", "9th Edition" -> "9e", como en las carpetas ("9e (2020)").
RE_EDICION = re.compile(r"\b(\d{1,2})\s*(?:a|ª|º|°|th|st|nd|rd)?[\s_]*(?:edici[oó]n|edition|ed)\b", re.I)


def palabras(texto: str) -> list[str]:
    # "Yokai_Companion", "DungeonCrawlerCarl" -> palabras separadas
    t = RE_EDICION.sub(r" \1e ", (texto or "").replace("_", " "))
    t = re.sub(r"([a-z])([A-Z])", r"\1 \2", t)
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
    """¿Bastante claro para mover sin preguntar?

    Varios vecinos de acuerdo con mayoría amplia, o vecinos y Bayes señalando la misma carpeta.
    """
    if not umbral or p.get("propuesta") is None:
        return False
    if p["confianza"] >= umbral and p["apoyos"] >= 3:
        return True
    return (p.get("bayes") == p["propuesta"] and p["confianza"] >= ACUERDO_VECINOS
            and p.get("bayes_p", 0) >= ACUERDO_BAYES and p["apoyos"] >= 2)


# --- Segunda opinión: Naive Bayes sobre toda la biblioteca -----------------------------
#
# Los vecinos miran solo los 25 nombres más parecidos; Naive Bayes aprende qué palabras (y qué
# editoriales) aparecen en cada carpeta de toda la biblioteca. Por separado aciertan parecido,
# pero cuando los dos señalan la MISMA carpeta casi siempre es la buena: con 1.500 libros de
# prueba, mover solo lo que tiene ≥90% de los vecinos movía el 16% (94% bien); añadir "los dos
# de acuerdo, vecinos ≥50% y Bayes ≥80%" sube al ~35% movido con ~89% bien.

ACUERDO_VECINOS = 0.5
ACUERDO_BAYES = 0.8
ALFA = 0.1  # suavizado


def rasgos(texto: str, editorial: str | None = None, titulo: str | None = None) -> list[str]:
    r = palabras(texto) + [f"t:{p}" for p in palabras(titulo or "")[:8]]
    if editorial:
        r.append("ed:" + normalizar(editorial))
    return r


class Bayes:
    def __init__(self, filas):
        self.docs: dict[str, int] = {}
        self.tokens: dict[str, int] = {}           # carpeta -> nº de rasgos
        self.cuenta: dict[str, dict[str, int]] = {}  # rasgo -> {carpeta: veces}
        for nombre, carpeta, editorial, titulo in filas:
            if not _valida(carpeta):
                continue
            r = rasgos(nombre, editorial, titulo)
            self.docs[carpeta] = self.docs.get(carpeta, 0) + 1
            self.tokens[carpeta] = self.tokens.get(carpeta, 0) + len(r)
            for x in r:
                por = self.cuenta.setdefault(x, {})
                por[carpeta] = por.get(carpeta, 0) + 1
        self.n = sum(self.docs.values())
        self.v = len(self.cuenta)

    @classmethod
    def de(cls, con: sqlite3.Connection) -> "Bayes":
        return cls(con.execute("SELECT nombre, carpeta, COALESCE(editorial, ext_editorial), pdf_titulo FROM docs"))

    def mejor(self, r: list[str]) -> tuple[str | None, float]:
        """(carpeta más probable, probabilidad) entre las carpetas que comparten algún rasgo."""
        r = [x for x in r if x in self.cuenta]
        candidatas = set().union(*(self.cuenta[x] for x in r)) if r else set()
        if not candidatas:
            return None, 0.0
        punt = {}
        for c in candidatas:
            s = math.log(self.docs[c] / self.n)
            base = self.tokens[c] + ALFA * self.v
            for x in r:
                s += math.log((self.cuenta[x].get(c, 0) + ALFA) / base)
            punt[c] = s
        m = max(punt.values())
        c = max(punt, key=punt.get)
        return c, 1 / sum(math.exp(v - m) for v in punt.values())


def con_segunda_opinion(p: dict, bayes: "Bayes | None", r: list[str]) -> dict:
    """Añade a la propuesta lo que dice Bayes ('bayes', 'bayes_p') para decidir con es_seguro."""
    if bayes is None or not p.get("propuesta"):
        return p
    c, prob = bayes.mejor(r)
    return {**p, "bayes": c, "bayes_p": round(prob, 3)}
