"""Oleada E: de qué juego es cada libro de las carpetas mezcladas, con Claude Haiku por lotes.

    python -m biblioteca_rpg.juegos_lotes enviar        # manda los lotes (no espera)
    python -m biblioteca_rpg.juegos_lotes estado
    python -m biblioteca_rpg.juegos_lotes recoger       # guarda las respuestas (no mueve nada)
    python -m biblioteca_rpg.juegos_lotes verificar     # busca en la web los juegos que no tenemos
    python -m biblioteca_rpg.juegos_lotes plan --csv /datos/oleada_E.csv
    python -m biblioteca_rpg.oleadas ejecutar E --csv /datos/oleada_E.csv --ejecutar

Haiku ve la portada, el nombre y las primeras páginas, y la lista de juegos (con sus ediciones) que
ya hay en la biblioteca. Dice el juego, la edición si la hay y el tipo de libro. Un juego que no está
en la lista se comprueba con una búsqueda web (una vez por juego) antes de crearle carpeta. Solo se
mueve lo que Haiku tiene claro; lo que no es de ningún juego (consejos, temas) se queda donde está.
"""

import argparse
import base64
import json
import re
import sys
import unicodedata
import time
from collections import Counter

from . import config as config_mod
from .db import conectar
from .indexador import ruta_portada
from .llm import ESQUEMA_USO, texto_inicio
from .oleadas import CUALQUIERA, OTRAS, _sin_series_pequenas, es_edicion
from .reorganizar import POR_CLASIFICAR
from .tipos_lotes import PRECIO_LOTE, TIPOS_ROL, TIPOS_WAR

MODELO = "claude-haiku-5-5"
POR_LOTE = 2000
ESTADO = "lotes_juegos.json"
RESPUESTAS = "juegos_respuestas.json"
VERIFICADOS = "juegos_verificados.json"
PRECIO_BUSQUEDA = 0.01  # $ por búsqueda web
ORIGENES = [
    "RPG/DrivethroughRPG", "RPG/_GM Books", "RPG/_Indie y storygames", "RPG/000000 Archivist Thread",
    "Wargames/Solo", "Wargames/Tables & Charts", "Wargames/scifi games", "Wargames/AWG Fantasy",
    "Wargames/AWG Sci-Fi", "Wargames/AWG Homebrews", "Wargames/Independent games", "Wargames/Misc",
    "Wargames/Miniaturas/_Reglamentos sueltos",
]
NINGUNO = "ninguno"

SISTEMA = """Clasificas libros de una biblioteca de juegos de rol y wargames que están en carpetas mezcladas
(compras de DriveThruRPG, recopilatorios para el máster, juegos indie, juegos en solitario…).
Para cada libro di DE QUÉ JUEGO es:
- Si es de un juego de la lista, escribe en "juego" su nombre EXACTAMENTE como en la lista y pon
  en_lista=true. Si el juego tiene ediciones en la lista y sabes cuál, pon en "edicion" el nombre exacto
  de esa edición; si no, déjalo vacío.
- Si es de un juego concreto que NO está en la lista, escribe su nombre oficial (sin "PDF", versiones ni
  editorial), en_lista=false, y su editorial si la sabes.
- Si no es de ningún juego en concreto (consejos genéricos para el máster, tablas aleatorias para
  cualquier sistema, historia, arte, una revista de varios juegos…), tipo_juego="ninguno".
- tipo_juego: "rol" para juegos de rol (también los de jugar en solitario), "wargame" para wargames,
  juegos de miniaturas o de tablero.
- tipo: el tipo de libro, de los permitidos.
Seguridad "alta" solo si lo tienes claro. Material de terceros para un juego (p. ej. una aventura de
otra editorial para D&D 5e) es de ese juego: "D&D 3rd Party" si está en la lista."""


def catalogo_juegos(con) -> tuple[str, dict[str, dict]]:
    """(texto para Haiku, {nombre en la lista: {"base": carpeta del juego, "ediciones": [...]}})."""
    juegos: dict[str, dict] = {}
    for (carpeta,) in con.execute("SELECT DISTINCT carpeta FROM docs WHERE carpeta LIKE 'RPG/%' OR carpeta LIKE 'Wargames/%'"):
        p = carpeta.split("/")
        if p[0] == "RPG" and len(p) > 1 and not p[1].startswith(("_", "0")) and p[1] != "DrivethroughRPG":
            nombre, base, sig = p[1], "/".join(p[:2]), p[2:3]
        elif p[0] == "Wargames" and len(p) > 2 and not p[1].startswith("_") and p[1] not in (
                "Solo", "Tables & Charts", "Misc", "Miniaturas", "Tablero", "scifi games", "Independent games",
                "AWG Fantasy", "AWG Sci-Fi", "AWG Homebrews") and p[2] != POR_CLASIFICAR:
            nombre, base, sig = f"{p[1]}/{p[2]}", "/".join(p[:3]), p[3:4]
        else:
            continue
        j = juegos.setdefault(nombre, {"base": base, "ediciones": set()})
        if sig and es_edicion(sig[0]) and sig[0] != CUALQUIERA:
            j["ediciones"].add(sig[0])
    for j in juegos.values():
        j["ediciones"] = sorted(j["ediciones"])
    lineas = ["JUEGOS DE ROL (nombre):"] + [
        n + (f"  [ediciones: {' | '.join(j['ediciones'])}]" if j["ediciones"] else "")
        for n, j in sorted(juegos.items()) if j["base"].startswith("RPG/")]
    lineas += ["", "WARGAMES (editorial/juego):"] + [
        n + (f"  [ediciones: {' | '.join(j['ediciones'])}]" if j["ediciones"] else "")
        for n, j in sorted(juegos.items()) if j["base"].startswith("Wargames/")]
    return "\n".join(lineas), juegos


def _esquema() -> dict:
    tipos = sorted(set(TIPOS_ROL) | set(TIPOS_WAR))
    return {"type": "object", "additionalProperties": False,
            "required": ["tipo_juego", "juego", "en_lista", "edicion", "editorial", "tipo", "seguridad"],
            "properties": {
                "tipo_juego": {"type": "string", "enum": ["rol", "wargame", NINGUNO]},
                "juego": {"type": "string"}, "en_lista": {"type": "boolean"},
                "edicion": {"type": "string"}, "editorial": {"type": "string"},
                "tipo": {"type": "string", "enum": tipos},
                "seguridad": {"type": "string", "enum": ["alta", "media", "baja"]}}}


def pendientes(con) -> list:
    filas = []
    for o in ORIGENES:
        filas += con.execute("SELECT id, ruta, carpeta, nombre FROM docs WHERE carpeta = ? OR carpeta LIKE ? ORDER BY id",
                             (o, o + "/%")).fetchall()
    return filas


def _peticion(cfg, d, lista: str) -> dict:
    contenido: list = []
    portada = ruta_portada(cfg.portadas, d["id"])
    if portada.is_file():
        contenido.append({"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                                                       "data": base64.standard_b64encode(portada.read_bytes()).decode()}})
    texto = texto_inicio(cfg.raiz / d["ruta"])[:2500] if d["ruta"].lower().endswith(".pdf") else ""
    contenido.append({"type": "text", "text": f"Carpeta: {d['carpeta']}\nFichero: {d['nombre']}\n\n"
                                              f"Primeras páginas:\n{texto or '(sin texto)'}"})
    return {"model": MODELO, "max_tokens": 1500,
            # La lista de juegos es igual en todas las peticiones: va en caché (se paga una vez).
            "system": [{"type": "text", "text": SISTEMA},
                       {"type": "text", "text": lista, "cache_control": {"type": "ephemeral"}}],
            "messages": [{"role": "user", "content": contenido}],
            "output_config": {"effort": "low", "format": {"type": "json_schema", "schema": _esquema()}}}


def _leer(cfg, nombre: str, defecto):
    f = cfg.datos / nombre
    return json.loads(f.read_text(encoding="utf-8")) if f.exists() else defecto


def _guardar(cfg, nombre: str, datos) -> None:
    (cfg.datos / nombre).write_text(json.dumps(datos, ensure_ascii=False, indent=0), encoding="utf-8")


def enviar(con, cfg, maximo: int | None) -> None:
    import anthropic
    estado = _leer(cfg, ESTADO, {"lotes": []})
    ya = {i for lote in estado["lotes"] for i in lote["ids"]}
    docs = [d for d in pendientes(con) if d["id"] not in ya][:maximo]
    if not docs:
        print("Nada nuevo.")
        return
    lista, juegos = catalogo_juegos(con)
    print(f"Lista: {len(juegos)} juegos, {len(lista)} caracteres; {len(docs)} libros")
    cliente = anthropic.Anthropic()
    for i in range(0, len(docs), POR_LOTE):
        trozo = docs[i:i + POR_LOTE]
        lote = cliente.messages.batches.create(
            requests=[{"custom_id": f"doc-{d['id']}", "params": _peticion(cfg, d, lista)} for d in trozo])
        estado = _leer(cfg, ESTADO, {"lotes": []})  # se relee: otro proceso puede haberlo tocado
        estado["lotes"].append({"id": lote.id, "ids": [d["id"] for d in trozo], "recogido": False})
        _guardar(cfg, ESTADO, estado)
        print(f"Lote {lote.id}: {len(trozo)} libros", flush=True)


def ver_estado(cfg) -> None:
    import anthropic
    cliente = anthropic.Anthropic()
    for lote in _leer(cfg, ESTADO, {"lotes": []})["lotes"]:
        b = cliente.messages.batches.retrieve(lote["id"])
        c = b.request_counts
        print(f"{lote['id']}: {b.processing_status}, {c.succeeded} hechos, {c.processing} en curso, "
              f"{c.errored} con error{' (recogido)' if lote['recogido'] else ''}")


def _precio(u) -> float:
    entrada = (u.input_tokens + (u.cache_creation_input_tokens or 0) * 1.25 + (u.cache_read_input_tokens or 0) * 0.1)
    return entrada / 1e6 * PRECIO_LOTE[0] + u.output_tokens / 1e6 * PRECIO_LOTE[1]


def recoger(con, cfg) -> None:
    import anthropic
    cliente = anthropic.Anthropic()
    con.executescript(ESQUEMA_USO)
    estado = _leer(cfg, ESTADO, {"lotes": []})
    respuestas = _leer(cfg, RESPUESTAS, {})
    total = 0.0
    for lote in estado["lotes"]:
        if lote["recogido"] or cliente.messages.batches.retrieve(lote["id"]).processing_status != "ended":
            continue
        gasto = 0.0
        for r in cliente.messages.batches.results(lote["id"]):
            if r.result.type != "succeeded":
                continue
            gasto += _precio(r.result.message.usage)
            try:
                respuestas[r.custom_id[4:]] = json.loads(
                    next((b.text for b in r.result.message.content if b.type == "text"), "{}"))
            except ValueError:
                pass
        lote["recogido"] = True
        total += gasto
        with con:
            con.execute("INSERT INTO llm_uso (fecha, modelo, entrada_id, tokens_entrada, tokens_salida, dolares) "
                        "VALUES (?, ?, NULL, NULL, NULL, ?)", (int(time.time()), MODELO + " (lotes, juegos)", gasto))
    _guardar(cfg, RESPUESTAS, respuestas)
    _guardar(cfg, ESTADO, estado)
    c = Counter((r.get("tipo_juego"), r.get("en_lista"), r.get("seguridad")) for r in respuestas.values())
    print(f"Respuestas guardadas: {len(respuestas)} (gasto de esta recogida {total:.2f} $)")
    for k, n in c.most_common():
        print(f"  {n:5} {k}")


def _clave(nombre: str) -> str:
    """Para comparar nombres de juego: 'Basic Fantasy RPG' y 'Basic Fantasy' son el mismo."""
    t = unicodedata.normalize("NFKD", nombre.split("/")[-1].lower())
    t = "".join(c for c in t if not unicodedata.combining(c))
    t = re.sub(r"\b(the|rpg|ttrpg|role[- ]?playing game|roleplaying game|game|system)\b", " ", t)
    return re.sub(r"[^a-z0-9]+", "", t)


def _existente(nombre: str, juegos: dict) -> str | None:
    """El juego de la lista que es este con otro nombre (mayúsculas, «RPG», puntuación…)."""
    c = _clave(nombre)
    if not c:
        return None
    return next((k for k in juegos if k.lower() == nombre.strip().lower()), None) or         next((k for k in juegos if _clave(k) == c), None)


def _nuevos(respuestas: dict, juegos: dict | None = None) -> dict[str, dict]:
    """Juegos que no están en la lista (seguridad alta o media): {nombre: {tipo_juego, editorial, n}}."""
    nuevos: dict[str, dict] = {}
    for r in respuestas.values():
        if not r.get("juego", "").strip() or (juegos and _existente(r["juego"], juegos)):
            continue  # sin nombre, o es un juego que ya está con otro nombre
        if r.get("seguridad") in ("alta", "media") and r.get("tipo_juego") != NINGUNO and not r.get("en_lista"):
            n = nuevos.setdefault(r["juego"].strip(), {"tipo_juego": r["tipo_juego"], "editorial": r["editorial"], "n": 0})
            n["n"] += 1
    return nuevos


def verificar(con, cfg, maximo: int) -> None:
    """Una búsqueda web por juego nuevo: ¿existe? ¿cómo se llama de verdad? ¿quién lo publica?"""
    import anthropic
    cliente = anthropic.Anthropic()
    con.executescript(ESQUEMA_USO)
    verificados = _leer(cfg, VERIFICADOS, {})
    nuevos = _nuevos(_leer(cfg, RESPUESTAS, {}), catalogo_juegos(con)[1])
    faltan = sorted((n for n in nuevos if n not in verificados), key=lambda n: -nuevos[n]["n"])[:maximo]
    print(f"Juegos nuevos: {len(nuevos)}; ya comprobados {len(verificados)}; ahora {len(faltan)}")
    esquema = {"type": "object", "additionalProperties": False,
               "required": ["existe", "nombre_oficial", "editorial", "tipo_juego"],
               "properties": {"existe": {"type": "boolean"}, "nombre_oficial": {"type": "string"},
                              "editorial": {"type": "string"},
                              "tipo_juego": {"type": "string", "enum": ["rol", "wargame", NINGUNO]}}}
    gasto = 0.0
    for i, nombre in enumerate(faltan, 1):
        try:
            m = cliente.messages.create(
                model=MODELO, max_tokens=3000,
                tools=[{"type": "web_search_20250305", "name": "web_search", "max_uses": 1}],
                messages=[{"role": "user", "content":
                           f"¿Existe un juego de rol o wargame de mesa llamado «{nombre}» (editorial probable: "
                           f"{nuevos[nombre]['editorial'] or '?'})? Busca en la web. existe=true solo si es un juego "
                           f"publicado (también autoeditado o en DriveThruRPG/itch.io) y no un suplemento de otro "
                           f"juego. nombre_oficial: el nombre del juego, sin versión. tipo_juego='ninguno' si es "
                           f"material genérico para cualquier sistema."}],
                output_config={"effort": "low", "format": {"type": "json_schema", "schema": esquema}})
        except anthropic.APIError as ex:
            print(f"  {nombre}: error {ex}", file=sys.stderr)
            continue
        u = m.usage
        busquedas = getattr(getattr(u, "server_tool_use", None), "web_search_requests", 0) or 0
        coste = (u.input_tokens / 1e6 * 0.10 + u.output_tokens / 1e6 * 0.50 + busquedas * PRECIO_BUSQUEDA)
        gasto += coste
        try:
            verificados[nombre] = json.loads(next(b.text for b in reversed(m.content) if b.type == "text"))
        except (StopIteration, ValueError):
            continue
        if i % 20 == 0:
            _guardar(cfg, VERIFICADOS, verificados)
            print(f"  {i}/{len(faltan)}  ({gasto:.2f} $)", flush=True)
    _guardar(cfg, VERIFICADOS, verificados)
    with con:
        con.execute("INSERT INTO llm_uso (fecha, modelo, entrada_id, tokens_entrada, tokens_salida, dolares) "
                    "VALUES (?, ?, NULL, NULL, NULL, ?)", (int(time.time()), MODELO + " (web, juegos)", gasto))
    si = sum(1 for n in faltan if verificados.get(n, {}).get("existe"))
    print(f"Comprobados {len(faltan)}: existen {si}. Gasto {gasto:.2f} $")


# Cómo llama Haiku a veces a juegos que en la biblioteca tienen otro nombre.
ALIAS_JUEGO = {
    "Dungeons & Dragons": "D&D", "Advanced Dungeons & Dragons": "D&D", "AD&D": "D&D", "5e": "D&D",
    "Dungeons & Dragons 5e": "D&D", "D&D 5e": "D&D", "B/X": "D&D", "Sword & Wizardry": "Swords & Wizardry",
    "Heavy Gear Blitz!": "Heavy Gear", "Heavy Gear Blitz": "Heavy Gear",
}


def _limpio(nombre: str) -> str:
    """Nombre de carpeta: sin barras ni caracteres que Windows no acepta."""
    return re.sub(r'[\\/:*?"<>|]+', " -", nombre).strip(" .")


def destino(d, r: dict, juegos: dict, verificados: dict) -> tuple[str | None, str]:
    """(carpeta nueva o None si se queda, motivo)."""
    if r.get("seguridad") not in ("alta", "media"):  # en la prueba, las «media» también acertaban
        return None, "dudoso"
    if r.get("tipo_juego") == NINGUNO:
        return None, "no es de ningún juego"
    tipo = r["tipo"]
    # Un juego «nuevo» que ya está con otro nombre ("Basic Fantasy RPG") se junta con el que hay; también
    # si el nombre oficial que dio la búsqueda web es el de uno que ya tenemos.
    if r["juego"].strip() in ALIAS_JUEGO:
        r = {**r, "juego": ALIAS_JUEGO[r["juego"].strip()]}
    oficial = (verificados.get(r["juego"].strip()) or {}).get("nombre_oficial") or ""
    ya = _existente(r["juego"], juegos) or (_existente(oficial, juegos) if oficial else None)
    if ya:
        r = {**r, "en_lista": True, "juego": ya}
    if r.get("en_lista"):
        j = juegos.get(r["juego"])
        if not j:
            return None, f"juego «{r['juego']}» no está en la lista"
        es_war = j["base"].startswith("Wargames/")
        if es_war and tipo not in TIPOS_WAR:
            tipo = {"Core Rules": "Rules", "Adventures": "Scenarios & Campaigns", "Settings": "Background"}.get(tipo, "Misc")
        if not es_war and tipo not in TIPOS_ROL:
            tipo = {"Rules": "Core Rules", "Army Books": "Supplements", "Scenarios & Campaigns": "Adventures",
                    "Errata & FAQ": "Misc"}.get(tipo, "Misc")
        if j["ediciones"]:
            ed = next((e for e in j["ediciones"] if e.lower() == r.get("edicion", "").strip().lower()), None)
            if not ed:
                return f"{j['base']}/{POR_CLASIFICAR}", "juego con ediciones, edición no clara"
            return f"{j['base']}/{ed}/{tipo}", "juego de la lista"
        return f"{j['base']}/{tipo}", "juego de la lista"
    v = verificados.get(r["juego"].strip())
    if not v or not v.get("existe") or v.get("tipo_juego") == NINGUNO:
        return None, "juego nuevo sin comprobar o que no existe"
    nombre = _limpio(v["nombre_oficial"] or r["juego"])
    if v["tipo_juego"] == "wargame":
        if tipo not in TIPOS_WAR:
            tipo = {"Core Rules": "Rules", "Adventures": "Scenarios & Campaigns", "Settings": "Background"}.get(tipo, "Misc")
        ed = _limpio(v.get("editorial") or "") or OTRAS
        return f"Wargames/{ed}/{nombre}/{tipo}", "juego nuevo comprobado en la web"
    if tipo not in TIPOS_ROL:
        tipo = {"Rules": "Core Rules", "Army Books": "Supplements", "Scenarios & Campaigns": "Adventures"}.get(tipo, "Misc")
    return f"RPG/{nombre}/{tipo}", "juego nuevo comprobado en la web"


def plan(con, cfg, salida: str) -> None:
    import csv
    _, juegos = catalogo_juegos(con)
    respuestas = _leer(cfg, RESPUESTAS, {})
    verificados = _leer(cfg, VERIFICADOS, {})
    filas, motivos = [], Counter()
    for d in pendientes(con):
        r = respuestas.get(str(d["id"]))
        if not r:
            motivos["sin respuesta"] += 1
            continue
        nueva, motivo = destino(d, r, juegos, verificados)
        motivos[motivo] += 1
        if nueva:
            # Producto de DriveThruRPG con varios ficheros: conserva su carpeta (luego se quitan las pequeñas).
            partes = d["carpeta"].split("/")
            if partes[:2] == ["RPG", "DrivethroughRPG"] and len(partes) > 3 and POR_CLASIFICAR not in nueva:
                nueva = f"{nueva}/{_limpio(partes[3])}"
            filas.append([d["ruta"], f"{nueva}/{d['nombre']}", nueva, f"oleada E: {motivo}"])
    _sin_series_pequenas(filas)  # un producto con menos de MIN_SERIE libros no tiene subcarpeta
    with open(salida, "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.writer(fh, delimiter=";")
        w.writerow(["Origen", "Destino", "Carpeta nueva", "Regla"])
        w.writerows(filas)
    print(f"Se moverían {len(filas)} libros:")
    for k, n in motivos.most_common():
        print(f"  {n:5} {k}")
    print(f"Tabla: {salida}")


def main(argv=None) -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("orden", choices=["enviar", "estado", "recoger", "verificar", "plan"])
    ap.add_argument("--maximo", type=int)
    ap.add_argument("--csv")
    args = ap.parse_args(argv)
    cfg = config_mod.cargar()
    con = conectar(cfg.db)
    if args.orden == "enviar":
        enviar(con, cfg, args.maximo)
    elif args.orden == "estado":
        ver_estado(cfg)
    elif args.orden == "recoger":
        recoger(con, cfg)
    elif args.orden == "verificar":
        verificar(con, cfg, args.maximo or 300)
    else:
        plan(con, cfg, args.csv or str(cfg.datos / "oleada_E.csv"))
    sys.exit(0)


if __name__ == "__main__":
    main()
