"""Tipo de lo que está en "_Por clasificar", con Claude Haiku por lotes (Message Batches: la mitad de precio).

    python -m biblioteca_rpg.tipos_lotes enviar          # manda los lotes (no espera)
    python -m biblioteca_rpg.tipos_lotes estado          # cómo van
    python -m biblioteca_rpg.tipos_lotes recoger         # mueve lo que Haiku tiene claro (--ejecutar)

Haiku mira la portada y las primeras páginas y elige el tipo de la lista del esquema. Solo se mueve
lo que tiene claro (seguridad «alta»); el resto se queda en _Por clasificar. Todo queda en un CSV.
"""

import argparse
import base64
import csv
import json
import sys
import time

from . import config as config_mod
from .db import conectar
from .indexador import ruta_portada
from .llm import ESQUEMA_USO, texto_inicio
from .reorganizar import POR_CLASIFICAR, TIPOS_WARGAMES, _mover

MODELO = "claude-haiku-5-5"
PRECIO_LOTE = (0.05, 0.25)  # $/millón de tokens (entrada, salida): la mitad del precio normal de Haiku
POR_LOTE = 2000
TIPOS_ROL = ["Core Rules", "Supplements", "Settings", "Adventures", "Organized Play", "Game Aids",
             "Fan & Homebrew", "Fiction", "Background", "Misc"]
TIPOS_WAR = sorted({t for _, t in TIPOS_WARGAMES})
ESTADO = "lotes_tipos.json"

SISTEMA = """Clasificas libros de una biblioteca de juegos de rol y wargames. Mira la portada, el nombre
y las primeras páginas y di qué tipo de libro es, eligiendo de la lista que te doy:
- Core Rules: reglas básicas, manual del jugador o del máster, kit de inicio.
- Supplements: reglas opcionales, clases, bestiarios, equipo, conjuros, expansiones.
- Settings: mundos, regiones, ciudades (sin aventura concreta).
- Adventures: aventuras, módulos, escenarios, campañas jugables.
- Organized Play: aventuras de juego organizado (Adventure League, Society, Living…).
- Game Aids: fichas, pantallas, cartas, mapas, hojas de referencia.
- Fan & Homebrew: material no oficial.  Fiction: novelas, relatos, cómics.  Background: trasfondo sin reglas.
- Army Books (wargames): codex y listas de ejército.  Rules (wargames): reglas básicas.
- Scenarios & Campaigns (wargames): misiones y campañas.  Errata & FAQ: erratas.
- Misc: revistas o lo que no encaje.
Seguridad "alta" solo si lo tienes claro."""


def _esquema(tipos: list[str]) -> dict:
    return {"type": "object", "additionalProperties": False, "required": ["tipo", "seguridad"],
            "properties": {"tipo": {"type": "string", "enum": tipos},
                           "seguridad": {"type": "string", "enum": ["alta", "media", "baja"]}}}


def _pendientes(con) -> list:
    return con.execute(f"SELECT id, ruta, carpeta, nombre FROM docs WHERE carpeta LIKE '%/{POR_CLASIFICAR}' "
                       f"OR carpeta LIKE '%/{POR_CLASIFICAR}/%' ORDER BY id").fetchall()


def _peticion(cfg, d) -> dict:
    tipos = TIPOS_WAR if d["ruta"].startswith("Wargames/") else TIPOS_ROL
    juego = d["carpeta"].split(f"/{POR_CLASIFICAR}")[0]
    contenido: list = []
    portada = ruta_portada(cfg.portadas, d["id"])
    if portada.is_file():
        contenido.append({"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                                                       "data": base64.standard_b64encode(portada.read_bytes()).decode()}})
    texto = texto_inicio(cfg.raiz / d["ruta"])[:2500] if d["ruta"].lower().endswith(".pdf") else ""
    contenido.append({"type": "text", "text": f"Juego: {juego}\nFichero: {d['nombre']}\n\nPrimeras páginas:\n"
                                              f"{texto or '(sin texto)'}\n\nTipos posibles: {', '.join(tipos)}"})
    return {"model": MODELO, "max_tokens": 1500, "system": SISTEMA,
            "messages": [{"role": "user", "content": contenido}],
            "output_config": {"effort": "low", "format": {"type": "json_schema", "schema": _esquema(tipos)}}}


def enviar(con, cfg, maximo: int | None) -> None:
    import anthropic
    estado_f = cfg.datos / ESTADO
    estado = json.loads(estado_f.read_text()) if estado_f.exists() else {"lotes": []}
    ya = {i for lote in estado["lotes"] for i in lote["ids"]}
    docs = [d for d in _pendientes(con) if d["id"] not in ya][:maximo]
    if not docs:
        print("Nada nuevo en _Por clasificar.")
        return
    cliente = anthropic.Anthropic()
    for i in range(0, len(docs), POR_LOTE):
        trozo = docs[i:i + POR_LOTE]
        peticiones = [{"custom_id": f"doc-{d['id']}", "params": _peticion(cfg, d)} for d in trozo]
        lote = cliente.messages.batches.create(requests=peticiones)
        # Se relee: mientras se preparan los lotes, «recoger» puede haber marcado otros como recogidos.
        estado = json.loads(estado_f.read_text()) if estado_f.exists() else {"lotes": []}
        estado["lotes"].append({"id": lote.id, "ids": [d["id"] for d in trozo], "enviado": int(time.time()),
                                "recogido": False})
        estado_f.write_text(json.dumps(estado))
        print(f"Lote {lote.id}: {len(trozo)} libros", flush=True)


def ver_estado(cfg) -> None:
    import anthropic
    estado = json.loads((cfg.datos / ESTADO).read_text())
    cliente = anthropic.Anthropic()
    for lote in estado["lotes"]:
        b = cliente.messages.batches.retrieve(lote["id"])
        c = b.request_counts
        print(f"{lote['id']}: {b.processing_status}, {c.succeeded} hechos, {c.processing} en curso, "
              f"{c.errored} con error{' (recogido)' if lote['recogido'] else ''}")


def recoger(con, cfg, de_verdad: bool) -> None:
    import anthropic
    estado_f = cfg.datos / ESTADO
    estado = json.loads(estado_f.read_text())
    cliente = anthropic.Anthropic()
    con.executescript(ESQUEMA_USO)
    registro = cfg.raiz / "_Reorganizacion" / f"tipos_haiku_{time.strftime('%Y-%m-%d_%H%M%S')}.csv"
    movidos = dudosos = 0
    gasto = 0.0
    filas_log = []
    for lote in estado["lotes"]:
        if lote["recogido"] or cliente.messages.batches.retrieve(lote["id"]).processing_status != "ended":
            continue
        gasto_lote = 0.0
        for r in cliente.messages.batches.results(lote["id"]):
            if r.result.type != "succeeded":
                continue
            msg = r.result.message
            gasto_lote += msg.usage.input_tokens / 1e6 * PRECIO_LOTE[0] + msg.usage.output_tokens / 1e6 * PRECIO_LOTE[1]
            try:
                resp = json.loads(next((b.text for b in msg.content if b.type == "text"), "{}"))
            except ValueError:
                continue
            d = con.execute("SELECT id, ruta, carpeta FROM docs WHERE id=?", (int(r.custom_id[4:]),)).fetchone()
            if not d or POR_CLASIFICAR not in d["carpeta"]:
                continue  # ya lo colocó alguien
            if resp.get("seguridad") != "alta":
                dudosos += 1
                continue
            nueva = d["ruta"].replace(f"/{POR_CLASIFICAR}", f"/{resp['tipo']}", 1)
            filas_log.append([time.strftime("%Y-%m-%d %H:%M:%S"), "Mover libro", d["ruta"], nueva, "tipo por Claude Haiku"])
            if de_verdad:
                try:
                    filas_log[-1][3] = _mover(cfg, con, "libro", d["ruta"], nueva)
                    movidos += 1
                except OSError as ex:
                    filas_log[-1][1], filas_log[-1][4] = "Error libro", str(ex)[:200]
            else:
                movidos += 1
        gasto += gasto_lote
        if de_verdad:
            lote["recogido"] = True
            with con:  # un apunte por lote, con lo de ese lote (no el acumulado)
                con.execute("INSERT INTO llm_uso (fecha, modelo, entrada_id, tokens_entrada, tokens_salida, dolares) "
                            "VALUES (?, ?, NULL, NULL, NULL, ?)", (int(time.time()), MODELO + " (lotes)", gasto_lote))
    if de_verdad:
        estado_f.write_text(json.dumps(estado))
        registro.parent.mkdir(parents=True, exist_ok=True)
        with open(registro, "w", encoding="utf-8-sig", newline="") as f:
            w = csv.writer(f, delimiter=";")
            w.writerow(["Fecha", "Accion", "Origen", "Destino", "Nota"])
            w.writerows(filas_log)
    print(f"{'Movidos' if de_verdad else 'Se moverían'}: {movidos}; dudosos (se quedan): {dudosos}; gasto {gasto:.2f} $"
          + (f"\nRegistro (también para deshacer con reorganizar deshacer): {registro}" if de_verdad else ""))


def main(argv=None) -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("orden", choices=["enviar", "estado", "recoger"])
    ap.add_argument("--maximo", type=int, help="enviar: como mucho estos libros")
    ap.add_argument("--ejecutar", action="store_true", help="recoger: mover de verdad")
    args = ap.parse_args(argv)
    cfg = config_mod.cargar()
    con = conectar(cfg.db)
    if args.orden == "enviar":
        enviar(con, cfg, args.maximo)
    elif args.orden == "estado":
        ver_estado(cfg)
    else:
        recoger(con, cfg, args.ejecutar)
    sys.exit(0)


if __name__ == "__main__":
    main()
