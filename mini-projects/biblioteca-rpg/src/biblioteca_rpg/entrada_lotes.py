"""Lo que espera en la Entrada, leído por Claude Haiku por lotes (cada noche, a mitad de precio).

    python -m biblioteca_rpg.entrada_lotes enviar [--maximo N]   # manda los pendientes sin leer
    python -m biblioteca_rpg.entrada_lotes estado
    python -m biblioteca_rpg.entrada_lotes recoger                # guarda, comprueba juegos nuevos y coloca
    python -m biblioteca_rpg.entrada_lotes colocar [--ensayo]     # vuelve a decidir con lo ya leído

El clasificador busca parecidos con lo que ya hay; lo que no se parece a nada (juegos que no tenemos,
nombres raros) se queda esperando. Haiku mira la portada, el nombre, las primeras páginas y el mensaje
de Telegram, y dice el juego, la edición y el tipo, de la lista de juegos de la biblioteca (lo mismo que
en la oleada E). Se coloca solo:
- lo que tiene claro (seguridad alta) de un juego que ya está, o de uno nuevo que existe de verdad
  (se comprueba con una búsqueda web, con un máximo diario);
- lo de seguridad media si el clasificador también apuntaba a ese juego.
Lo demás sigue esperando, con la carpeta de Haiku como primera opción y agrupado por ella.

El clasificador lo hace solo (`automatico`): manda los lotes a la hora ENTRADA_LOTES_HORA (las 3 por
defecto) y los recoge cuando terminan. ENTRADA_LOTES=off lo apaga. Respeta LLM_MAX_DOLARES_MES.
"""

import argparse
import base64
import json
import os
import sys
import time
from collections import Counter

from . import config as config_mod
from .db import conectar
from .juegos_lotes import (
    ALIAS_JUEGO,
    MODELO,
    NINGUNO,
    VERIFICADOS,
    _esquema,
    _existente,
    _guardar,
    _leer,
    _precio,
    catalogo_juegos,
    destino,
    verificar_nuevos,
)
from .llm import ESQUEMA_USO, gastado_este_mes, texto_inicio
from .oleadas import CUALQUIERA
from .reorganizar import POR_CLASIFICAR

ESTADO = "lotes_entrada.json"
POR_LOTE = 300            # más grandes, el contenedor (768 MB) se queda sin memoria
COSTE_POR_LIBRO = 0.0004   # $ estimados por libro en lote (portada + texto; la lista va en caché)
BUSQUEDAS_DIA = int(os.environ.get("ENTRADA_LOTES_BUSQUEDAS_DIA") or 20)
HORA = int(os.environ.get("ENTRADA_LOTES_HORA") or 3)
MIRAR_CADA = 900           # segundos entre miradas al estado de los lotes
NOTA = "colocado por Haiku"
EBOOKS = {".epub", ".mobi", ".azw", ".azw3"}
NOVELAS_SUELTAS = "RPG/_Novelas"   # novelas que no son de ningún juego


def es_ebook(nombre: str) -> bool:
    return os.path.splitext(nombre or "")[1].lower() in EBOOKS

SISTEMA = """Clasificas lo que llega a la carpeta de entrada de una biblioteca de juegos de rol y wargames
(descargas de Telegram, compras de DriveThruRPG…): un PDF o una carpeta con los PDF de un producto.
Para cada uno di DE QUÉ JUEGO es:
- Si es de un juego de la lista, escribe en "juego" su nombre EXACTAMENTE como en la lista y pon
  en_lista=true. Si el juego tiene ediciones en la lista y sabes cuál, pon en "edicion" el nombre exacto
  de esa edición; si no, déjalo vacío.
- Si es de un juego concreto que NO está en la lista, escribe su nombre oficial (sin "PDF", versiones ni
  editorial), en_lista=false, y su editorial si la sabes.
- Si no es de ningún juego en concreto (consejos genéricos para el máster, tablas aleatorias para
  cualquier sistema, historia, arte, una revista de varios juegos, una novela…), tipo_juego="ninguno".
- tipo_juego: "rol" para juegos de rol (también los de jugar en solitario), "wargame" para wargames,
  juegos de miniaturas o de tablero.
- tipo: el tipo de libro, de los permitidos.
El canal y el mensaje de Telegram, si los hay, suelen decir de qué juego es. Las carpetas que propone
el clasificador son solo pistas por parecido de nombre: pueden estar mal.
Seguridad "alta" solo si lo tienes claro. Material de terceros para un juego (p. ej. una aventura de
otra editorial para D&D 5e) es de ese juego: "D&D 3rd Party" si está en la lista."""


def activo() -> bool:
    return bool(os.environ.get("ANTHROPIC_API_KEY")) and os.environ.get("ENTRADA_LOTES", "on") != "off"


def limite_mes() -> float:
    return float(os.environ.get("LLM_MAX_DOLARES_MES") or 10)


def _estado(cfg) -> dict:
    return _leer(cfg, ESTADO, {"lotes": [], "enviado_dia": "", "busquedas": {}})


def por_leer(con, cfg) -> list:
    """Pendientes sin lectura de Claude, que no estén ya en un lote y que tengan algún PDF (o sean un ebook)."""
    from .entrada import _pdfs
    ya = {i for lote in _estado(cfg)["lotes"] for i in lote["ids"]}
    res = []
    for f in con.execute("SELECT * FROM entrada WHERE estado='pendiente' AND destino IS NULL AND llm IS NULL "
                         "ORDER BY id"):
        if f["id"] in ya:
            continue
        ruta = cfg.raiz / f["ruta_actual"]
        if ruta.exists() and (_pdfs(ruta) or (ruta.is_file() and es_ebook(ruta.name))):
            res.append(f)
    return res


def _peticion(con, cfg, f, lista: str) -> dict:
    from .entrada import _pdfs, canal_telegram, ruta_portada_entrada
    ruta = cfg.raiz / f["ruta_actual"]
    pdfs = _pdfs(ruta) or [ruta]  # un ebook suelto: pymupdf también lo lee
    principal = max(pdfs, key=lambda p: p.stat().st_size)
    contenido: list = []
    portada = ruta_portada_entrada(cfg, f["id"])
    if portada.is_file():
        contenido.append({"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                                                       "data": base64.standard_b64encode(portada.read_bytes()).decode()}})
    lineas = [f"{'Carpeta' if f['es_carpeta'] else 'Fichero'}: {f['nombre']}"]
    if not f["es_carpeta"] and es_ebook(f["nombre"]):
        lineas.append("Es un ebook: una novela. Di de qué juego es su mundo (tipo Fiction), o ninguno.")
    if f["es_carpeta"]:
        lineas.append("PDF de dentro: " + " | ".join(p.name for p in pdfs[:15]))
    if f["pdf_titulo"]:
        lineas.append(f"Título interno del PDF: {f['pdf_titulo']}")
    if f["editorial"]:
        lineas.append(f"Editorial según el PDF: {f['editorial']}")
    canal = canal_telegram(con, f["nombre"])
    if canal:
        lineas.append(f"Canal de Telegram: {canal}")
    if f["origen_texto"]:
        lineas.append(f"Mensaje de Telegram: {f['origen_texto'][:500]}")
    alternativas = json.loads(f["alternativas"] or "[]")
    if alternativas:
        lineas.append("El clasificador propone (por parecido): " +
                      "; ".join(f"{c} ({pct}%)" for c, pct in alternativas[:3] if pct is not None))
    texto = texto_inicio(principal)[:2500]
    lineas += ["", "Primeras páginas:", texto or "(sin texto)"]
    contenido.append({"type": "text", "text": "\n".join(lineas)})
    return {"model": MODELO, "max_tokens": 1500,
            "system": [{"type": "text", "text": SISTEMA},
                       {"type": "text", "text": lista, "cache_control": {"type": "ephemeral"}}],
            "messages": [{"role": "user", "content": contenido}],
            "output_config": {"effort": "low", "format": {"type": "json_schema", "schema": _esquema()}}}


def enviar(con, cfg, maximo: int | None = None) -> int:
    import anthropic
    filas = por_leer(con, cfg)[:maximo]
    if not filas:
        print("Entrada (Haiku): nada que mandar.", flush=True)
        return 0
    queda = limite_mes() - gastado_este_mes(con)
    cabe = int(queda / COSTE_POR_LIBRO)
    if cabe < len(filas):
        print(f"Entrada (Haiku): el presupuesto del mes solo da para {max(cabe, 0)} de {len(filas)}", flush=True)
        filas = filas[:max(cabe, 0)]
        if not filas:
            return 0
    lista, juegos = catalogo_juegos(con)
    cliente = anthropic.Anthropic()
    for i in range(0, len(filas), POR_LOTE):
        trozo = filas[i:i + POR_LOTE]
        peticiones = []
        for f in trozo:
            try:
                peticiones.append({"custom_id": f"ent-{f['id']}", "params": _peticion(con, cfg, f, lista)})
            except (OSError, ValueError) as ex:
                print(f"  {f['nombre']}: no se puede leer ({ex})", file=sys.stderr)
        if not peticiones:
            continue
        lote = cliente.messages.batches.create(requests=peticiones)
        estado = _estado(cfg)  # se relee: otro proceso puede haberlo tocado
        estado["lotes"].append({"id": lote.id, "ids": [int(p["custom_id"][4:]) for p in peticiones],
                                "recogido": False, "enviado": int(time.time())})
        _guardar(cfg, ESTADO, estado)
        print(f"Entrada (Haiku): lote {lote.id} con {len(peticiones)} pendientes "
              f"(lista de {len(juegos)} juegos)", flush=True)
    return len(filas)


def ver_estado(cfg) -> None:
    import anthropic
    cliente = anthropic.Anthropic()
    for lote in _estado(cfg)["lotes"]:
        if lote["recogido"]:
            print(f"{lote['id']}: recogido ({len(lote['ids'])})")
            continue
        b = cliente.messages.batches.retrieve(lote["id"])
        c = b.request_counts
        print(f"{lote['id']}: {b.processing_status}, {c.succeeded} hechos, {c.processing} en curso, {c.errored} con error")


def recoger(con, cfg) -> int:
    """Guarda en cada pendiente lo que dijo Haiku (en entrada.llm). Devuelve cuántas respuestas nuevas."""
    import anthropic
    cliente = anthropic.Anthropic()
    con.executescript(ESQUEMA_USO)
    estado = _estado(cfg)
    nuevas = 0
    for lote in estado["lotes"]:
        if lote["recogido"] or cliente.messages.batches.retrieve(lote["id"]).processing_status != "ended":
            continue
        gasto = 0.0
        with con:
            for r in cliente.messages.batches.results(lote["id"]):
                if r.result.type != "succeeded":
                    continue
                gasto += _precio(r.result.message.usage)
                try:
                    resp = json.loads(next((b.text for b in r.result.message.content if b.type == "text"), "{}"))
                except ValueError:
                    continue
                llm = {"origen": "lote", "respuesta": resp, "juego": resp.get("juego") or "-",
                       "tipo": resp.get("tipo") or "-", "seguridad": resp.get("seguridad"),
                       "carpeta": "", "valida": False, "motivo": ""}
                n = con.execute("UPDATE entrada SET llm=? WHERE id=? AND llm IS NULL",
                                (json.dumps(llm, ensure_ascii=False), int(r.custom_id[4:]))).rowcount
                nuevas += n
            con.execute("INSERT INTO llm_uso (fecha, modelo, entrada_id, tokens_entrada, tokens_salida, dolares) "
                        "VALUES (?, ?, NULL, NULL, NULL, ?)", (int(time.time()), MODELO + " (lotes, entrada)", gasto))
        estado = _estado(cfg)
        for x in estado["lotes"]:
            if x["id"] == lote["id"]:
                x["recogido"] = True
        _guardar(cfg, ESTADO, estado)
        print(f"Entrada (Haiku): recogido {lote['id']}, gasto {gasto:.2f} $", flush=True)
    return nuevas


def _juego_de(r: dict, juegos: dict, verificados: dict) -> str | None:
    """El juego de la lista del que es (también si Haiku lo llamó de otra forma)."""
    nombre = ALIAS_JUEGO.get(r.get("juego", "").strip(), r.get("juego", "").strip())
    if not nombre:
        return None
    if r.get("en_lista") and nombre in juegos:
        return nombre
    oficial = (verificados.get(nombre) or {}).get("nombre_oficial") or ""
    return _existente(nombre, juegos) or (_existente(oficial, juegos) if oficial else None)


def decidir(r: dict, alternativas: list, juegos: dict, verificados: dict) -> tuple[str | None, bool, str]:
    """(carpeta de Haiku o None, ¿se coloca sola?, motivo)."""
    if r.get("tipo_juego") == NINGUNO:
        return None, False, "no es de ningún juego en concreto"
    # La carpeta se calcula aunque la seguridad sea baja: sirve de primera opción para revisar.
    carpeta, motivo = destino(None, {**r, "seguridad": "alta"}, juegos, verificados)
    if not carpeta:
        return None, False, motivo
    seguridad = r.get("seguridad")
    juego = _juego_de(r, juegos, verificados)
    if seguridad == "alta":
        return carpeta, True, motivo
    if seguridad == "media" and juego:
        base = juegos[juego]["base"]
        if any(c == base or c.startswith(base + "/") for c, _ in alternativas):
            return carpeta, True, f"{motivo}; el clasificador también lo veía de {juego}"
    return carpeta, False, f"{motivo} (seguridad {seguridad})"


def como_novela(r: dict, alternativas: list, juegos: dict, verificados: dict) -> tuple[str | None, bool, str]:
    """Un ebook es siempre una novela: a la Fiction de su juego (sin edición si no la sabe), o a
    NOVELAS_SUELTAS si no es de ningún juego."""
    if r.get("tipo_juego") == NINGUNO:
        return NOVELAS_SUELTAS, True, "novela que no es de ningún juego"
    carpeta, sola, motivo = decidir({**r, "tipo": "Fiction"}, alternativas, juegos, verificados)
    if not carpeta:
        return NOVELAS_SUELTAS, False, f"novela; {motivo}"
    base, ultimo = carpeta.rsplit("/", 1)
    if ultimo == POR_CLASIFICAR:
        base = f"{base}/{CUALQUIERA}"
    return f"{base}/Fiction", sola, f"novela; {motivo}"


def _nuevos_de(filas, juegos: dict) -> dict[str, dict]:
    """Juegos que no tenemos y que Haiku ve claros: a comprobar en la web."""
    nuevos: dict[str, dict] = {}
    for f in filas:
        r = json.loads(f["llm"])["respuesta"]
        nombre = r.get("juego", "").strip()
        if (not nombre or r.get("tipo_juego") == NINGUNO or r.get("seguridad") != "alta"
                or nombre in ALIAS_JUEGO or _existente(nombre, juegos)):
            continue
        n = nuevos.setdefault(nombre, {"tipo_juego": r["tipo_juego"], "editorial": r.get("editorial", ""), "n": 0})
        n["n"] += 1
    return nuevos


def _leidos(con):
    return [f for f in con.execute("SELECT * FROM entrada WHERE estado='pendiente' AND destino IS NULL "
                                   "AND llm LIKE '%\"origen\": \"lote\"%' ORDER BY id")]


def comprobar_nuevos(con, cfg, maximo: int | None = None) -> int:
    """Busca en la web los juegos nuevos, con el máximo del día."""
    estado = _estado(cfg)
    hoy = time.strftime("%Y-%m-%d")
    hechas = estado.get("busquedas", {}).get(hoy, 0)
    cupo = (maximo if maximo is not None else BUSQUEDAS_DIA) - hechas
    if cupo <= 0 or gastado_este_mes(con) >= limite_mes():
        return 0
    _, juegos = catalogo_juegos(con)
    n = verificar_nuevos(con, cfg, _nuevos_de(_leidos(con), juegos), cupo, "web, entrada")
    estado = _estado(cfg)
    estado.setdefault("busquedas", {})[hoy] = hechas + n
    _guardar(cfg, ESTADO, estado)
    return n


def colocar(con, cfg, ensayo: bool = False) -> Counter:
    """Decide con lo que dijo Haiku: lo seguro se aprueba (lo mueve el clasificador), lo demás espera."""
    _, juegos = catalogo_juegos(con)
    verificados = _leer(cfg, VERIFICADOS, {})
    cuenta: Counter = Counter()
    ahora = int(time.time())
    for f in _leidos(con):
        llm = json.loads(f["llm"])
        alternativas = json.loads(f["alternativas"] or "[]")
        sin_claude = [a for a in alternativas if a[1] is not None]
        decide = como_novela if not f["es_carpeta"] and es_ebook(f["nombre"]) else decidir
        carpeta, sola, motivo = decide(llm["respuesta"], sin_claude, juegos, verificados)
        cuenta["se coloca" if sola and cfg.auto else motivo.split(" (")[0].split(";")[0]] += 1
        if ensayo:
            if sola:
                print(f"  {f['nombre']} -> {carpeta}")
            continue
        llm.update(carpeta=carpeta or "", valida=bool(carpeta), motivo=motivo)
        nuevas = ([[carpeta, None]] if carpeta else []) + [a for a in sin_claude if a[0] != carpeta]
        with con:
            if sola and cfg.auto:
                con.execute("UPDATE entrada SET llm=?, alternativas=?, estado='aprobado', destino=?, auto=1, "
                            "decidido_en=?, nota=? WHERE id=? AND estado='pendiente'",
                            (json.dumps(llm, ensure_ascii=False), json.dumps(nuevas[:6], ensure_ascii=False),
                             carpeta, ahora, f"{NOTA}: {motivo}"[:300], f["id"]))
                print(f"  Haiku: {f['nombre']} -> {carpeta}", flush=True)
            else:
                con.execute("UPDATE entrada SET llm=?, alternativas=? WHERE id=? AND estado='pendiente'",
                            (json.dumps(llm, ensure_ascii=False), json.dumps(nuevas[:6], ensure_ascii=False), f["id"]))
    print("Entrada (Haiku): " + ", ".join(f"{n} {k}" for k, n in cuenta.most_common()), flush=True)
    return cuenta


_MIRADO: dict = {}


def automatico(con, cfg) -> None:
    """Desde el bucle del clasificador: recoge lo que haya terminado y, a su hora, manda lo nuevo."""
    if not activo():
        return
    ahora = time.time()
    if ahora - _MIRADO.get("cuando", 0) < MIRAR_CADA:
        return
    _MIRADO["cuando"] = ahora
    estado = _estado(cfg)
    try:
        if any(not lote["recogido"] for lote in estado["lotes"]) and recoger(con, cfg):
            comprobar_nuevos(con, cfg)
            colocar(con, cfg)
        elif (estado.get("busquedas", {}).get(time.strftime("%Y-%m-%d"), 0) < BUSQUEDAS_DIA and _leidos(con)
              and comprobar_nuevos(con, cfg)):
            colocar(con, cfg)  # los juegos nuevos que no cupieron ayer en el máximo de búsquedas
        hoy = time.strftime("%Y-%m-%d")
        if time.localtime().tm_hour == HORA and estado.get("enviado_dia") != hoy:
            enviar(con, cfg)
            estado = _estado(cfg)
            estado["enviado_dia"] = hoy
            _guardar(cfg, ESTADO, estado)
    except Exception as ex:  # que un fallo de la API no pare el clasificador
        print(f"Entrada (Haiku): {type(ex).__name__}: {ex}", file=sys.stderr, flush=True)


def main(argv=None) -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("orden", choices=["enviar", "estado", "recoger", "comprobar", "colocar"])
    ap.add_argument("--maximo", type=int)
    ap.add_argument("--ensayo", action="store_true")
    args = ap.parse_args(argv)
    cfg = config_mod.cargar()
    con = conectar(cfg.db)
    if args.orden == "enviar":
        enviar(con, cfg, args.maximo)
    elif args.orden == "estado":
        ver_estado(cfg)
    elif args.orden == "recoger":
        print(f"Respuestas nuevas: {recoger(con, cfg)}")
        comprobar_nuevos(con, cfg, args.maximo)
        colocar(con, cfg)
    elif args.orden == "comprobar":
        comprobar_nuevos(con, cfg, args.maximo)
    else:
        colocar(con, cfg, args.ensayo)
    sys.exit(0)


if __name__ == "__main__":
    main()
