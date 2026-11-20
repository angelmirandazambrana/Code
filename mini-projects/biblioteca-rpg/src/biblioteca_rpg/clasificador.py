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
        **_madre(votos, apoyos, total),
    }


# --- Si no está clara la carpeta, ¿lo está el juego? ------------------------------------
#
# A menudo los vecinos no se ponen de acuerdo en la subcarpeta (Codex, Rules, FAQ…) pero sí en
# el juego o la edición. Entonces el fichero va a "<juego>/_Por clasificar" en vez de esperar en
# la Entrada. En la prueba con 1.500 libros: con 90% de acuerdo, un 20% más se coloca solo y
# casi nunca en un juego equivocado (0,4%).

POR_CLASIFICAR = "_Por clasificar"


def es_nivel_de_juego(carpeta: str) -> bool:
    """¿Es al menos un juego (no una carpeta que solo agrupa)? "RPG/Shadowrun" sí; "Wargames/Miniaturas" no."""
    from .rutas import AGRUPA_EDITORIALES, EDITORIALES, FORMATOS_WARGAMES
    p = carpeta.split("/")
    if p[0] == "RPG":
        if len(p) > 1 and p[1] == "D&D Third Party":
            return len(p) >= 4  # RPG/D&D Third Party/<edición>/<editorial>
        if len(p) > 1 and p[1] in AGRUPA_EDITORIALES:
            return len(p) >= 3
        return len(p) >= 2
    if p[0] == "Wargames":
        i = 2 if len(p) > 1 and p[1] in FORMATOS_WARGAMES else 1
        if len(p) > i and p[i] in EDITORIALES:
            i += 1  # Wargames/Miniaturas/Games Workshop/<juego>
        return len(p) > i
    if p[0] == "assets":
        return len(p) >= 3
    return len(p) >= 2


def _madre(votos: dict[str, float], apoyos: dict[str, int], total: float) -> dict:
    """La carpeta más profunda (de nivel de juego o más) que reúne la mayoría de los votos."""
    suma: dict[str, float] = {}
    cuenta: dict[str, int] = {}
    for c, v in votos.items():
        partes = c.split("/")
        for i in range(1, len(partes)):  # solo antecesoras: la carpeta exacta ya la mira es_seguro
            a = "/".join(partes[:i])
            suma[a] = suma.get(a, 0) + v
            cuenta[a] = cuenta.get(a, 0) + apoyos[c]
    from .rutas import RE_EDICION, RE_EDICION_NUMERADA

    def edicion(seg: str) -> bool:
        return bool(RE_EDICION.match(seg) or RE_EDICION_NUMERADA.match(seg))

    def con_edicion_si_hace_falta(a: str) -> bool:
        # Si debajo hay ediciones ("Warhammer 40K/9e (2020)"), tiene que ser ya una edición:
        # "D&D/_Por clasificar" mezclaría todas las ediciones.
        if any(edicion(s) for s in a.split("/")):
            return True
        return not any(edicion(s) for c in votos if c.startswith(a + "/") for s in c[len(a) + 1:].split("/"))

    candidatas = [a for a in suma if es_nivel_de_juego(a) and con_edicion_si_hace_falta(a)]
    if not candidatas:
        return {}
    mejor = max(candidatas, key=lambda a: (round(suma[a] / total, 2) >= 0.9, a.count("/"), suma[a]))
    return {"madre": mejor, "madre_confianza": round(suma[mejor] / total, 3), "madre_apoyos": cuenta[mejor]}


def madre_segura(p: dict, umbral: float) -> str | None:
    """Carpeta "<juego>/_Por clasificar" si el juego está claro (aunque la subcarpeta no), o None."""
    if not umbral or not p.get("madre"):
        return None
    if p["madre_confianza"] >= umbral and p["madre_apoyos"] >= 3:
        return f"{p['madre']}/{POR_CLASIFICAR}"
    return None


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
        self.series: dict[str, dict[str, int]] = {}  # "ddal09" -> {carpeta: veces}
        for nombre, carpeta, editorial, titulo in filas:
            if not _valida(carpeta):
                continue
            r = rasgos(nombre, editorial, titulo)
            self.docs[carpeta] = self.docs.get(carpeta, 0) + 1
            self.tokens[carpeta] = self.tokens.get(carpeta, 0) + len(r)
            for x in r:
                por = self.cuenta.setdefault(x, {})
                por[carpeta] = por.get(carpeta, 0) + 1
            for x in series(nombre):
                por = self.series.setdefault(x, {})
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


# --- Códigos de serie: "DDAL09-06", "CCC-BMG-01", "WD 145" ------------------------------
#
# Si los ficheros con el mismo código de serie están casi todos en una carpeta, ahí va el nuevo.
# En la biblioteca: dispara en ~2,5% de los libros y acierta el ~97% (≥3 ficheros, ≥90% juntos).

RE_SERIE = re.compile(r"(?<![a-z0-9])([a-z]{2,6})[ _-]?(\d{1,3})(?=[^0-9]|$)")
NO_SERIE = {"vol", "ed", "no", "part", "book", "issue", "pt", "ch", "season", "nr", "num", "rev", "ver", "vers",
            "chapter", "level", "lvl", "page", "pag", "tier", "mk", "the", "and", "of"}
SERIE_MINIMO = 3
SERIE_CUOTA = 0.9


def series(nombre: str) -> set[str]:
    """Códigos de serie del nombre: "DDAL09-06 - Infernal Insurgency.pdf" -> {"ddal09"}."""
    base = re.sub(r"\.[a-z0-9]{2,4}$", "", (nombre or "").lower())
    return {f"{a}{b}" for a, b in RE_SERIE.findall(base) if a not in NO_SERIE}


def por_serie(bayes: "Bayes | None", nombre: str) -> dict | None:
    """Propuesta por código de serie, o None si no hay uno claro."""
    if bayes is None:
        return None
    mejor = None
    for s in series(nombre):
        por = bayes.series.get(s) or {}
        total = sum(por.values())
        if total < SERIE_MINIMO:
            continue
        carpeta, k = max(por.items(), key=lambda x: x[1])
        if k >= SERIE_MINIMO and k / total >= SERIE_CUOTA and _valida(carpeta) and (mejor is None or k > mejor[1]):
            mejor = (carpeta, k, total)
    if not mejor:
        return None
    carpeta, k, total = mejor
    return {"propuesta": carpeta, "confianza": round(k / total, 3), "apoyos": k, "alternativas": [[carpeta, 100]],
            "serie": True}


def con_segunda_opinion(p: dict, bayes: "Bayes | None", r: list[str]) -> dict:
    """Añade a la propuesta lo que dice Bayes ('bayes', 'bayes_p') para decidir con es_seguro."""
    if bayes is None or not p.get("propuesta"):
        return p
    c, prob = bayes.mejor(r)
    return {**p, "bayes": c, "bayes_p": round(prob, 3)}
