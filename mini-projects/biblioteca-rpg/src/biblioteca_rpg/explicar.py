"""El dueño explica qué es un fichero de la Entrada y Haiku propone la carpeta exacta.

«Esto es una ficha de personaje fan-made de World of Darkness, lo pondría en Vampire Classic» ->
RPG/World of Darkness/Vampire - The Masquerade/…/Character Sheets, con una línea de por qué.

Haiku ve lo que sabemos del fichero, lo que dice el dueño, sus decisiones anteriores (como ejemplos) y
las carpetas que pueden servir: las que comparten palabras con la explicación y el nombre, y todo el
árbol de los juegos que salen. Solo propone: mover lo confirma el dueño. Cada explicación se guarda y
sirve de ejemplo a la lectura nocturna de la Entrada (entrada_lotes).
"""

import json
import math
import os
import re
import time

from .juegos_lotes import MODELO
from .llm import ESQUEMA_USO, PRECIOS, gastado_este_mes, texto_inicio
from .texto_pdf import normalizar

ESQUEMA_EXPLICACIONES = """
CREATE TABLE IF NOT EXISTS explicaciones (
    id INTEGER PRIMARY KEY,
    entrada_id INTEGER,
    nombre TEXT,
    explicacion TEXT NOT NULL,
    propuesta TEXT,
    motivo TEXT,
    carpeta_final TEXT,          -- donde acabó (lo que confirmó el dueño)
    creado_en INTEGER,
    decidido_en INTEGER
);
"""

SISTEMA = """Ayudas a colocar ficheros (PDF de juegos de rol, wargames, revistas…) en una biblioteca
organizada por carpetas. El dueño de la biblioteca mira el fichero y te explica con sus palabras qué es
y dónde lo pondría. Tú eliges la carpeta EXACTA:
- Lo que dice el dueño manda sobre cualquier otra pista (nombre, Telegram, clasificador, lectura anterior).
- Si una carpeta de la lista encaja, escribe su ruta EXACTAMENTE como en la lista (con mayúsculas,
  paréntesis y espacios) y pon nueva=false.
- Si hace falta una subcarpeta que no existe (p. ej. «Fan & Homebrew» o «Character Sheets» dentro de
  la edición que dice), propón la ruta colgando de una carpeta que SÍ existe, con el estilo de nombres
  de sus carpetas hermanas, y pon nueva=true. No inventes juegos ni ediciones si ya existen con otro nombre.
- Si el dueño nombra un juego que tiene carpeta propia en la lista (p. ej. «Vampire» -> «Vampire - The
  Masquerade»), va en la carpeta de ESE juego, no en una carpeta general de su línea o su editorial.
- Respeta la estructura habitual: área (RPG, Wargames, Magazines…) / juego / edición / tipo (Core Rules,
  Adventures, Supplements, Settings, Game Aids, Fan & Homebrew…).
- motivo: una frase corta en español con por qué esa carpeta.
- Palabras como «Classic», «5e», «2nd», «Revised» o «V20» junto al nombre de un juego suelen ser su
  EDICIÓN: «Vampire Classic» es Vampire - The Masquerade (la clásica), no una carpeta «Classic» de otra línea.
- Si dice fan-made, homebrew o no oficial y el juego tiene «Fan & Homebrew», va ahí (o se crea dentro del
  juego o de su edición). «Misc» y las carpetas generales son el último recurso."""

ESQUEMA = {"type": "object", "additionalProperties": False,
           "required": ["carpeta", "nueva", "motivo", "juego", "tipo"],
           "properties": {"carpeta": {"type": "string"}, "nueva": {"type": "boolean"},
                          "motivo": {"type": "string"}, "juego": {"type": "string"}, "tipo": {"type": "string"}}}

VACIAS = frozenset(re.findall(r"\w+", """esto este esta estos estas eso una uno unos unas del los las que por para con sin pero
aunque como tipo pondria pondría seria sería creo pues porque cosa algo the and for with from this that
fichero pdf libro juego juegos clase"""))
MAX_POR_JUEGO = 160
MAX_CARPETAS = 400


def limite_mes() -> float:
    return float(os.environ.get("LLM_MAX_DOLARES_MES") or 10)


def activo() -> bool:
    return bool(os.environ.get("ANTHROPIC_API_KEY"))


def _palabras(*textos: str) -> set[str]:
    return {w for t in textos if t for w in normalizar(t).split() if len(w) >= 3 and w not in VACIAS}


def raiz_juego(carpeta: str) -> str:
    """RPG/Vampire - The Masquerade/… -> RPG/Vampire - The Masquerade; Wargames/GW/Necromunda/… -> 3 tramos."""
    p = carpeta.split("/")
    return "/".join(p[:3] if p[0] == "Wargames" else p[:2])


def juegos_nombrados(carpetas: list[str], buscadas: set[str], maximo: int = 20) -> list[str]:
    """Los juegos (sus carpetas raíz) cuyo nombre tiene palabras de las buscadas; las raras pesan más.

    «vampire classic» -> RPG/Vampire - The Masquerade antes que RPG/Four Against Darkness.
    """
    raices = {raiz_juego(c) for c in carpetas if c.count("/") >= 1}
    nombres = {r: set(normalizar(r.split("/", 1)[1]).split()) for r in raices}
    df: dict[str, int] = {}
    for ws in nombres.values():
        for w in ws:
            df[w] = df.get(w, 0) + 1
    total = max(len(nombres), 1)
    puntos = []
    for r, ws in nombres.items():
        p = sum(math.log(total / df[w]) for w in ws & buscadas)
        if p > 0:
            puntos.append((-p, len(r), r))
    return [r for *_, r in sorted(puntos)[:maximo]]


def carpetas_candidatas(carpetas: list[tuple[str, int]], explicacion: str, e: dict) -> list[tuple[str, int]]:
    """Las carpetas que pueden servir: con palabras en común y el árbol de sus juegos."""
    llm = e.get("llm") or {}
    buscadas = _palabras(explicacion, e.get("nombre", ""), e.get("pdf_titulo") or "", llm.get("juego") or "")
    normal = [(c, n, normalizar(c)) for c, n in carpetas if "/_" not in "/" + c]
    puntos = []
    for c, n, norm in normal:
        tramos = set(norm.split())
        comunes = sum(1 for w in buscadas if w in tramos)
        if comunes:
            puntos.append((-comunes, c.count("/"), -n, c))
    puntos.sort()
    elegidas = [c for *_, c in puntos[:40]]
    raices = list(dict.fromkeys(
        juegos_nombrados([c for c, _, _ in normal], buscadas) +
        [raiz_juego(c) for c in elegidas[:12]] +
        [raiz_juego(c) for c, _ in (e.get("alternativas") or []) if c] +
        ([raiz_juego(llm["carpeta"])] if llm.get("carpeta") else [])))
    por_ruta = {c: n for c, n, _ in normal}
    # Por niveles: primero lo de arriba de cada juego (ediciones, tipos), luego lo más hondo; así un
    # juego enorme no deja fuera a los demás.
    arbol = []
    for orden, r in enumerate(raices):
        hijas = sorted((c for c in por_ruta if c == r or c.startswith(r + "/")),
                       key=lambda c: (c.count("/"), c))[:MAX_POR_JUEGO]
        arbol += [(c.count("/") - r.count("/"), orden, c) for c in hijas]
    salida: dict[str, int] = {}
    for c in elegidas[:15]:
        salida.setdefault(c, por_ruta.get(c, 0))
    for *_, c in sorted(arbol):
        salida.setdefault(c, por_ruta[c])
    for c in elegidas[15:]:
        salida.setdefault(c, por_ruta.get(c, 0))
    elegidas_final = list(salida.items())[:MAX_CARPETAS]
    return sorted(elegidas_final)  # en orden de ruta: Haiku ve cada árbol junto


def ejemplos(con, n: int = 25) -> str:
    """Las últimas decisiones del dueño con su explicación, para Haiku (aquí y en la lectura nocturna)."""
    con.executescript(ESQUEMA_EXPLICACIONES)
    filas = con.execute("SELECT nombre, explicacion, carpeta_final FROM explicaciones WHERE carpeta_final IS NOT NULL "
                        "ORDER BY decidido_en DESC LIMIT ?", (n,)).fetchall()
    if not filas:
        return ""
    return "\n".join(["DECISIONES ANTERIORES DEL DUEÑO (fichero — lo que dijo → dónde acabó):"] +
                     [f"- «{f[0]}» — «{f[1]}» → {f[2]}" for f in filas])


def _contexto(con, cfg, e: dict, explicacion: str, cands: list[tuple[str, int]]) -> str:
    from .entrada import _pdfs, canal_telegram
    llm = e.get("llm") or {}
    lineas = [f"{'Carpeta' if e.get('es_carpeta') else 'Fichero'}: {e['nombre']}"]
    detalles = [f"{e['paginas']} páginas" if e.get("paginas") else None,
                f"editorial según el PDF: {e['editorial']}" if e.get("editorial") else None,
                f"año: {e['publicado']}" if e.get("publicado") else None]
    if any(detalles):
        lineas.append(" · ".join(d for d in detalles if d))
    if e.get("pdf_titulo"):
        lineas.append(f"Título interno: {e['pdf_titulo']}")
    canal = canal_telegram(con, e["nombre"])
    if canal:
        lineas.append(f"Canal de Telegram: {canal}")
    if e.get("origen_texto"):
        lineas.append(f"Mensaje de Telegram: {e['origen_texto'][:400]}")
    if llm:
        lineas.append(f"Lectura anterior de Claude: {llm.get('juego')} · {llm.get('tipo')} — {llm.get('motivo', '')}")
    alts = [c for c, _ in (e.get("alternativas") or []) if c]
    if alts:
        lineas.append("El clasificador proponía: " + "; ".join(alts[:3]))
    try:
        ruta = cfg.raiz / e["ruta_actual"]
        pdfs = _pdfs(ruta) if ruta.is_dir() else [ruta]
        if pdfs:
            texto = texto_inicio(max(pdfs, key=lambda p: p.stat().st_size))[:1500]
            if texto:
                lineas += ["", "Primeras páginas:", texto]
    except (OSError, ValueError):
        pass
    lineas += ["", f"LO QUE DICE EL DUEÑO: «{explicacion}»", ""]
    ej = ejemplos(con)
    if ej:
        lineas += [ej, ""]
    lineas += ["CARPETAS QUE EXISTEN Y PUEDEN SERVIR (ruta · nº de libros):"] + [f"{c} · {n}" for c, n in cands]
    return "\n".join(lineas)


def proponer(con, cfg, e: dict, explicacion: str, carpetas: list[tuple[str, int]], cliente=None) -> dict:
    """{carpeta, nueva, existe, motivo, juego, tipo, id} o {error}."""
    import anthropic

    from .entrada import destino_valido
    explicacion = (explicacion or "").strip()
    if not explicacion:
        return {"error": "Escribe qué es el fichero."}
    if not activo():
        return {"error": "No hay clave de Anthropic en la web."}
    if gastado_este_mes(con) >= limite_mes():
        return {"error": f"Este mes ya se ha gastado el máximo ({limite_mes():.0f} $)."}
    cands = carpetas_candidatas(carpetas, explicacion, e)
    cliente = cliente or anthropic.Anthropic()
    r = cliente.messages.create(
        model=MODELO, max_tokens=800,
        system=SISTEMA,
        messages=[{"role": "user", "content": _contexto(con, cfg, e, explicacion, cands)}],
        output_config={"effort": "low", "format": {"type": "json_schema", "schema": ESQUEMA}})
    entrada_tok, salida_tok = r.usage.input_tokens, r.usage.output_tokens
    leidos = getattr(r.usage, "cache_read_input_tokens", 0) or 0
    creados = getattr(r.usage, "cache_creation_input_tokens", 0) or 0
    p_in, p_out = PRECIOS[MODELO]
    dolares = (entrada_tok * p_in + leidos * p_in * 0.1 + creados * p_in * 1.25 + salida_tok * p_out) / 1e6
    con.executescript(ESQUEMA_USO + ESQUEMA_EXPLICACIONES)
    with con:
        con.execute("INSERT INTO llm_uso (fecha, modelo, entrada_id, tokens_entrada, tokens_salida, dolares) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (int(time.time()), MODELO, e["id"], entrada_tok + leidos + creados, salida_tok, dolares))
    if r.stop_reason == "refusal":
        return {"error": "Claude no ha querido responder."}
    try:
        res = json.loads(next(b.text for b in r.content if b.type == "text"))
    except (StopIteration, json.JSONDecodeError):
        return {"error": "Respuesta ilegible; prueba otra vez."}
    carpeta = destino_valido(cfg, res.get("carpeta", "")) or ""
    existentes = {c for c, _ in carpetas}
    with con:
        cur = con.execute("INSERT INTO explicaciones (entrada_id, nombre, explicacion, propuesta, motivo, creado_en) "
                          "VALUES (?, ?, ?, ?, ?, ?)",
                          (e["id"], e["nombre"], explicacion, carpeta, res.get("motivo"), int(time.time())))
    return {"carpeta": carpeta, "existe": carpeta in existentes, "motivo": res.get("motivo", ""),
            "juego": res.get("juego", ""), "tipo": res.get("tipo", ""), "id": cur.lastrowid,
            "dolares": round(dolares, 4), "valida": bool(carpeta)}


def confirmar(con, explicacion_id: int, entrada_id: int, carpeta: str) -> None:
    """Anota dónde acabó el fichero de una explicación (lo que la convierte en ejemplo)."""
    con.executescript(ESQUEMA_EXPLICACIONES)
    with con:
        con.execute("UPDATE explicaciones SET carpeta_final=?, decidido_en=? WHERE id=? AND entrada_id=?",
                    (carpeta, int(time.time()), explicacion_id, entrada_id))
