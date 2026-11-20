"""Segunda lectura con Claude (API de Anthropic) para lo que el clasificador no sabe colocar.

Hace lo que haría una persona: mira la portada y las primeras páginas, ve de qué juego, edición y
tipo de libro se trata, y elige entre las carpetas candidatas que propone el clasificador (cada
una con ficheros de ejemplo y sus subcarpetas, para que vea cómo está organizada la biblioteca).

    python -m biblioteca_rpg.llm piloto --modelo haiku --libros 200 --pendientes 100

La clave va en ANTHROPIC_API_KEY (en el .env de OMV3, nunca en el código).
"""

import argparse
import base64
import json
import random
import sqlite3
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from . import config as config_mod
from .clasificador import (
    POR_CLASIFICAR,
    _valida,
    con_segunda_opinion,
    es_seguro,
    madre_segura,
    por_serie,
    proponer,
    rasgos,
)
from .db import conectar
from .indexador import ruta_portada

MODELOS = {"haiku": "claude-haiku-5-5", "sonnet": "claude-sonnet-5-5", "opus": "claude-opus-5-5"}
# Dólares por millón de tokens (entrada, salida), para llevar la cuenta del gasto.
PRECIOS = {"claude-haiku-5-5": (0.10, 0.50), "claude-sonnet-5-5": (2.0, 10.0), "claude-opus-5-5": (4.0, 20.0)}
MAX_TEXTO = 5000  # caracteres de las primeras páginas

SISTEMA = """Eres el bibliotecario de una gran biblioteca personal de PDF de juegos de rol, wargames,
juegos de mesa y revistas, organizada en carpetas por juego, edición, editorial y tipo de libro.
Te llega un fichero nuevo y tienes que decidir en qué carpeta va, como lo haría su dueño:
mirando la portada y las primeras páginas para saber de qué juego, edición y editorial es, y si es
un libro básico, una aventura, un suplemento, un mapa, una revista…

Te doy carpetas candidatas con ficheros de ejemplo y sus subcarpetas: fíjate en cómo están
organizadas (qué tipo de libros hay en cada una) y elige la que mejor encaje. Puedes elegir una
subcarpeta de una candidata si encaja mejor. Si tienes claro el juego y la edición pero no la
subcarpeta, responde "<carpeta del juego o edición>/_Por clasificar". No inventes carpetas que no
aparezcan, salvo ese caso. Sé honesto con tu seguridad: "alta" solo si lo tienes claro."""

ESQUEMA = {
    "type": "object",
    "properties": {
        "carpeta": {"type": "string", "description": "Ruta completa de la carpeta elegida"},
        "seguridad": {"type": "string", "enum": ["alta", "media", "baja"]},
        "juego": {"type": "string", "description": "Juego y edición, p. ej. 'D&D 5e' o 'Warhammer 40K 9e'"},
        "tipo": {"type": "string", "description": "Básico, aventura, suplemento, revista, mapa…"},
        "motivo": {"type": "string", "description": "Una frase"},
    },
    "required": ["carpeta", "seguridad", "juego", "tipo", "motivo"],
    "additionalProperties": False,
}


# --- Lo que se le enseña -------------------------------------------------------------

def texto_inicio(ruta_pdf: Path) -> str:
    import pymupdf
    pymupdf.TOOLS.mupdf_display_errors(False)
    try:
        with pymupdf.open(ruta_pdf) as doc:
            texto = "\n".join(doc[i].get_text() for i in range(min(3, doc.page_count)))
    except Exception:
        return ""
    return " ".join(texto.split())[:MAX_TEXTO]


def candidatas(con, p: dict) -> list[str]:
    """Carpetas que se le ofrecen: las del clasificador, la de Bayes y la del juego."""
    res = [c for c, _ in p.get("alternativas", [])]
    for extra in (p.get("bayes"), p.get("madre")):
        if extra and extra not in res:
            res.append(extra)
    return [c for c in res if c and _valida(c)][:7]


def describir_carpeta(con, carpeta: str, excluir_id: int | None) -> str:
    ejemplos = [r[0] for r in con.execute(
        "SELECT nombre FROM docs WHERE carpeta = ? AND id IS NOT ? ORDER BY random() LIMIT 6", (carpeta, excluir_id))]
    hijas = sorted({r[0][len(carpeta) + 1:].split("/", 1)[0] for r in con.execute(
        "SELECT DISTINCT carpeta FROM docs WHERE carpeta LIKE ? AND carpeta <> ?", (carpeta + "/%", carpeta))})
    hijas = [h for h in hijas if not h.startswith("_")][:15]
    lineas = [f"- {carpeta}"]
    if ejemplos:
        lineas.append("    ficheros: " + " | ".join(ejemplos))
    if hijas:
        lineas.append("    subcarpetas: " + " | ".join(hijas))
    return "\n".join(lineas)


def peticion(con, nombre: str, portada: Path | None, texto: str, cands: list[str], excluir_id: int | None) -> list:
    contenido: list = []
    if portada and portada.is_file():
        contenido.append({"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                                                       "data": base64.standard_b64encode(portada.read_bytes()).decode()}})
    bloque = (f"Fichero: {nombre}\n\nTexto de las primeras páginas:\n{texto or '(sin texto: PDF escaneado)'}\n\n"
              "Carpetas candidatas:\n" + "\n".join(describir_carpeta(con, c, excluir_id) for c in cands))
    contenido.append({"type": "text", "text": bloque})
    return contenido


def clasificar(cliente, modelo: str, contenido: list) -> tuple[dict, dict]:
    """(respuesta, uso) para un fichero."""
    r = cliente.messages.create(
        model=modelo, max_tokens=4000, system=SISTEMA,
        messages=[{"role": "user", "content": contenido}],
        output_config={"effort": "low", "format": {"type": "json_schema", "schema": ESQUEMA}},
    )
    if r.stop_reason == "refusal":
        return {"error": "refusal"}, {}
    texto = next((b.text for b in r.content if b.type == "text"), "{}")
    uso = {"entrada": r.usage.input_tokens, "salida": r.usage.output_tokens}
    try:
        return json.loads(texto), uso
    except json.JSONDecodeError:
        return {"error": "json"}, uso


# --- En la Entrada ---------------------------------------------------------------------
#
# Para lo que el clasificador no sabe colocar. Con seguridad «alta» y una carpeta válida, Claude
# decide; si no, su propuesta queda como primera opción para revisar. En la prueba piloto, Sonnet
# con seguridad alta acertó el 81-85% donde nuestro sistema no decidía.

ESQUEMA_USO = """
CREATE TABLE IF NOT EXISTS llm_uso (
    id INTEGER PRIMARY KEY,
    fecha INTEGER NOT NULL,
    modelo TEXT NOT NULL,
    entrada_id INTEGER,
    tokens_entrada INTEGER,
    tokens_salida INTEGER,
    dolares REAL
);
"""


def activo() -> bool:
    import os
    return bool(os.environ.get("ANTHROPIC_API_KEY")) and os.environ.get("LLM_MODELO", "sonnet") in MODELOS


def gastado_este_mes(con) -> float:
    con.executescript(ESQUEMA_USO)
    desde = int(time.mktime(time.localtime()[:2] + (1, 0, 0, 0, 0, 0, -1)))
    return con.execute("SELECT COALESCE(SUM(dolares), 0) FROM llm_uso WHERE fecha >= ?", (desde,)).fetchone()[0]


def destino_aceptable(con, carpeta: str, cands: list[str]) -> bool:
    """Una candidata, una subcarpeta que ya existe de una candidata, o "<juego>/_Por clasificar" de una."""
    carpeta = (carpeta or "").strip().strip("/")
    if not carpeta:
        return False
    if carpeta.endswith("/" + POR_CLASIFICAR):
        juego = carpeta[: -len(POR_CLASIFICAR) - 1]
        return any(c == juego or c.startswith(juego + "/") for c in cands)
    if carpeta in cands:
        return True
    existe = con.execute("SELECT 1 FROM docs WHERE carpeta = ? LIMIT 1", (carpeta,)).fetchone()
    return bool(existe) and any(carpeta.startswith(c + "/") for c in cands)


_CLIENTE: list = []


def segunda_lectura(con, cfg, entrada_id: int, nombre: str, portada: Path | None, pdf: Path | None,
                    p: dict) -> dict | None:
    """Lo que dice Claude de un pendiente: {"carpeta", "seguridad", "juego", "tipo", "motivo", "valida"}, o None."""
    import os

    if not activo():
        return None
    limite = float(os.environ.get("LLM_MAX_DOLARES_MES") or 10)
    if gastado_este_mes(con) >= limite:
        return None
    cands = candidatas(con, p)
    if not cands:
        return None
    import anthropic
    if not _CLIENTE:
        _CLIENTE.append(anthropic.Anthropic())
    modelo = MODELOS[os.environ.get("LLM_MODELO", "sonnet")]
    contenido = peticion(con, nombre, portada, texto_inicio(pdf) if pdf else "", cands, None)
    try:
        resp, uso = clasificar(_CLIENTE[0], modelo, contenido)
    except anthropic.APIError as ex:
        print(f"  Claude no responde: {type(ex).__name__}", file=sys.stderr, flush=True)
        return None
    if uso:
        pe, ps = PRECIOS[modelo]
        with con:
            con.execute("INSERT INTO llm_uso (fecha, modelo, entrada_id, tokens_entrada, tokens_salida, dolares) "
                        "VALUES (?, ?, ?, ?, ?, ?)", (int(time.time()), modelo, entrada_id, uso["entrada"],
                                                      uso["salida"], uso["entrada"] / 1e6 * pe + uso["salida"] / 1e6 * ps))
    if "carpeta" not in resp:
        return None
    resp["carpeta"] = resp["carpeta"].strip().strip("/")
    resp["valida"] = destino_aceptable(con, resp["carpeta"], cands)
    return resp


# --- Prueba piloto ---------------------------------------------------------------------

def _juego(c: str | None) -> str | None:
    """Los tres primeros niveles ("RPG/D&D/Adventures"), para comparar si al menos acierta el juego."""
    return "/".join(c.split("/")[:3]) if c else None


def piloto(args) -> None:
    import anthropic

    from .entrada import _pdfs, canal_telegram, modelo_bayes, ruta_portada_entrada
    cfg = config_mod.cargar()
    con = conectar(cfg.db)
    con.row_factory = sqlite3.Row
    bayes = modelo_bayes(con)
    modelo = MODELOS[args.modelo]
    cliente = anthropic.Anthropic()
    random.seed(args.semilla)

    casos = []
    # Libros ya colocados (su carpeta se oculta: se excluyen de sus propios vecinos).
    libros = con.execute("SELECT id, nombre, ruta, carpeta, COALESCE(editorial, ext_editorial) ed, pdf_titulo FROM docs "
                         "WHERE portada = 1 AND lower(ruta) LIKE '%.pdf'").fetchall()
    libros = [d for d in libros if _valida(d["carpeta"])]
    for d in random.sample(libros, args.libros):
        q = d["nombre"]
        # Sin Bayes: su modelo ya ha visto este libro y haría trampa.
        p = proponer(con, q, d["ed"], excluir_id=d["id"])
        casos.append({"tipo": "libro", "id": d["id"], "nombre": d["nombre"], "real": d["carpeta"], "p": p,
                      "portada": ruta_portada(cfg.portadas, d["id"]), "pdf": cfg.raiz / d["ruta"]})
    # Pendientes reales de la Entrada (sin respuesta conocida: para ver qué haría).
    pend = con.execute("SELECT * FROM entrada WHERE estado = 'pendiente' AND COALESCE(n_pdfs, 1) > 0").fetchall()
    for e in random.sample(pend, min(args.pendientes, len(pend))):
        q = " ".join([e["nombre"], e["pdf_titulo"] or "", (e["origen_texto"] or "")[:300], canal_telegram(con, e["nombre"])])
        p = con_segunda_opinion(proponer(con, q, e["editorial"]), bayes, rasgos(q, e["editorial"], e["pdf_titulo"]))
        pdfs = _pdfs(cfg.raiz / e["ruta_actual"])
        casos.append({"tipo": "pendiente", "id": e["id"], "nombre": e["nombre"], "real": None, "p": p,
                      "portada": ruta_portada_entrada(cfg, e["id"]),
                      "pdf": max(pdfs, key=lambda x: x.stat().st_size) if pdfs else None})

    salida = Path(args.salida)
    gasto = {"entrada": 0, "salida": 0}
    cerrojo = threading.Lock()
    inicio = time.time()

    def uno(caso):
        sistema = caso["p"]
        nuestra = (sistema["propuesta"] if es_seguro(sistema, cfg.auto) else madre_segura(sistema, cfg.auto)) \
            if sistema.get("propuesta") else None
        serie = por_serie(bayes, caso["nombre"])
        if not nuestra and serie and es_seguro(serie, cfg.auto):
            nuestra = serie["propuesta"]
        cands = candidatas(con, sistema)
        texto = texto_inicio(caso["pdf"]) if caso["pdf"] else ""
        with cerrojo:  # la conexión SQLite se comparte entre hilos
            contenido = peticion(con, caso["nombre"], caso["portada"], texto, cands,
                                 caso["id"] if caso["tipo"] == "libro" else None)
        try:
            resp, uso = clasificar(cliente, modelo, contenido)
        except anthropic.APIError as ex:
            resp, uso = {"error": f"{type(ex).__name__}: {ex}"[:200]}, {}
        fila = {"tipo": caso["tipo"], "id": caso["id"], "nombre": caso["nombre"], "real": caso["real"],
                "candidatas": cands, "real_entre_candidatas": caso["real"] in cands if caso["real"] else None,
                "nuestra": nuestra, "propuesta": sistema.get("propuesta"), "llm": resp, "uso": uso,
                "con_texto": bool(texto)}
        with cerrojo:
            gasto["entrada"] += uso.get("entrada", 0)
            gasto["salida"] += uso.get("salida", 0)
            with salida.open("a", encoding="utf-8") as f:
                f.write(json.dumps(fila, ensure_ascii=False) + "\n")
        return fila

    pe, ps = PRECIOS[modelo]
    hechos = 0
    with ThreadPoolExecutor(max_workers=args.hilos) as ex:
        for _ in ex.map(uno, casos):
            hechos += 1
            if hechos % 25 == 0 or hechos == len(casos):
                coste = gasto["entrada"] / 1e6 * pe + gasto["salida"] / 1e6 * ps
                print(f"  {hechos}/{len(casos)}  {coste:.3f} $  ({time.time() - inicio:.0f} s)", flush=True)
                if coste > args.max_dolares:
                    print("Parado: se ha llegado al gasto máximo de la prueba.", flush=True)
                    ex.shutdown(cancel_futures=True)
                    break
    informe(salida, modelo)


def informe(salida: Path, modelo: str | None = None) -> None:
    filas = [json.loads(x) for x in salida.read_text(encoding="utf-8").splitlines() if x.strip()]
    libros = [f for f in filas if f["tipo"] == "libro" and "carpeta" in f["llm"]]
    pe, ps = PRECIOS.get(modelo or "", (0, 0))
    ent = sum(f["uso"].get("entrada", 0) for f in filas)
    sal = sum(f["uso"].get("salida", 0) for f in filas)
    print(f"\n{salida.name}: {len(filas)} ficheros, {ent} tokens de entrada, {sal} de salida"
          + (f", {ent / 1e6 * pe + sal / 1e6 * ps:.2f} $" if pe else ""))
    if not libros:
        return
    n = len(libros)

    def pct(x):
        return f"{x / n:.0%}"

    exacta = sum(f["llm"]["carpeta"] == f["real"] for f in libros)
    juego = sum(_juego(f["llm"]["carpeta"].split(f"/{POR_CLASIFICAR}")[0]) == _juego(f["real"]) for f in libros)
    entre = sum(bool(f["real_entre_candidatas"]) for f in libros)
    print(f"Libros ya colocados ({n}): la carpeta real estaba entre las candidatas en {pct(entre)}")
    print(f"  Claude acierta la carpeta exacta en {pct(exacta)} y el juego en {pct(juego)}")
    for seg in ("alta", "media", "baja"):
        s = [f for f in libros if f["llm"]["seguridad"] == seg]
        if s:
            ok = sum(f["llm"]["carpeta"] == f["real"] for f in s)
            pc = sum(f["llm"]["carpeta"].endswith(POR_CLASIFICAR) and (f["real"] + "/").startswith(
                f["llm"]["carpeta"].split(f"/{POR_CLASIFICAR}")[0] + "/") for f in s)
            print(f"  seguridad {seg:5}: {len(s) / n:4.0%} de los libros, exacta {ok / len(s):.0%}, "
                  f"a «Por clasificar» de su juego {pc / len(s):.0%}")
    nuestras = [f for f in libros if f["nuestra"]]
    ok_n = sum(f["nuestra"] == f["real"] or (f["nuestra"].endswith(POR_CLASIFICAR) and (f["real"] + "/").startswith(
        f["nuestra"].split(f"/{POR_CLASIFICAR}")[0] + "/")) for f in nuestras)
    print(f"  Nuestro sistema solo: decide en {len(nuestras) / n:.0%}, bien {ok_n / max(len(nuestras), 1):.0%}")
    resto = [f for f in libros if not f["nuestra"]]
    alta = [f for f in resto if f["llm"]["seguridad"] == "alta"]
    ok_a = sum(f["llm"]["carpeta"] == f["real"] for f in alta)
    print(f"  Donde nuestro sistema no decide ({len(resto)}): Claude con seguridad alta en {len(alta)}, "
          f"exacta {ok_a / max(len(alta), 1):.0%}")


def main(argv=None) -> None:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="orden", required=True)
    p = sub.add_parser("piloto")
    p.add_argument("--modelo", choices=list(MODELOS), default="haiku")
    p.add_argument("--libros", type=int, default=200)
    p.add_argument("--pendientes", type=int, default=100)
    p.add_argument("--hilos", type=int, default=4)
    p.add_argument("--semilla", type=int, default=1)
    p.add_argument("--max-dolares", type=float, default=10.0)
    p.add_argument("--salida", required=True)
    i = sub.add_parser("informe")
    i.add_argument("salida")
    i.add_argument("--modelo", choices=list(MODELOS))
    args = ap.parse_args(argv)
    if args.orden == "piloto":
        piloto(args)
    else:
        informe(Path(args.salida), MODELOS.get(args.modelo))
    sys.exit(0)


if __name__ == "__main__":
    main()
