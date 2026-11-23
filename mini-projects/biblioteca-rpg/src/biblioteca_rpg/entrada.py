"""Clasificador: vigila la carpeta de entrada (_Inbox), propone destino y mueve lo aprobado.

    python -m biblioteca_rpg.entrada            # bucle: cada minuto
    python -m biblioteca_rpg.entrada --una-vez  # una pasada y termina

Es el ÚNICO proceso que escribe en la biblioteca. Reglas, las mismas de la reorganización:
- nunca sobrescribe ni borra un fichero: si el nombre ya existe con otro contenido se añade
  " (2)"; si es idéntico, el que llega va a _Duplicados;
- los comprimidos se descomprimen y el original se guarda en _Duplicados;
- cada movimiento queda en _Reorganizacion/clasificador_AAAA-MM.csv (Fecha;Accion;Origen;Destino;Nota).
"""

import argparse
import errno
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

from . import config as config_mod
from . import entrada_lotes, estadisticas, llm, packs, texto_pdf
from .clasificador import (
    VECINOS,
    Bayes,
    con_segunda_opinion,
    es_seguro,
    madre_segura,
    palabras,
    por_serie,
    proponer,
    rasgos,
)
from .db import conectar
from .enriquecer import pasada_pdf
from .entrada_lotes import EBOOKS, NOVELAS_SUELTAS, es_ebook
from .indexador import _procesar_pdf, en_paralelo, generar_portadas, sincronizar

COMPRIMIDOS = {".zip", ".rar", ".7z", ".cbz", ".cbr"}
ESTABLE_SEG = 120          # sin cambios durante este tiempo = terminó de copiarse/descargarse
DUPLICADOS = "_Duplicados/_Inbox"
COMPRIMIDOS_HECHOS = "_Duplicados/_Inbox comprimidos"
ACTIVOS = ("nuevo", "pendiente", "aprobado", "error")

# Novelas: el parecido de nombres no sirve ("Dead Drop" acaba en Drop Dead Studios), así que los
# ebooks se colocan por el tema del canal de Telegram del que llegaron o por su propio nombre.
# Todo ebook es una novela: si no lo dice el canal ni el nombre, Haiku dice de qué juego (a su Fiction)
# o, si no es de ninguno, va a NOVELAS_SUELTAS.
NOVELAS_40K = "Wargames/Games Workshop/Warhammer 40K/Any Edition/Fiction"
# (patrón, carpeta, ¿vale también el canal?). Los canales de D&D («D&D Book Share») comparten
# novelas de todo, así que para D&D solo cuenta el nombre.
REGLAS_NOVELAS = [
    (re.compile(r"necromunda", re.I), "Wargames/Games Workshop/Necromunda/Any Edition/Fiction", True),
    (re.compile(r"horus heresy", re.I), NOVELAS_40K + "/Horus Heresy", True),
    (re.compile(r"warhammer|40\.?000|\b40k\b|black library|space marine", re.I), NOVELAS_40K, True),
    (re.compile(r"battle ?tech|mech ?warrior", re.I), "Wargames/Catalyst Game Labs/BattleTech/Fiction", True),
    (re.compile(r"^FR[-_ ]|forgotten realms|drizzt|dragonlance|greyhawk|ravenloft|dark sun|athas|eberron|"
                r"\bd ?& ?d\b|dungeons (&|and) dragons|salvatore|margaret weis|hickman|greenwood|elminster",
                re.I), "RPG/D&D/Any Edition/Fiction", False),
    (re.compile(r"everquest", re.I), "RPG/E/EverQuest/Fiction", False),
]


def junto_a_sus_hermanas(p: dict, nombre: str) -> dict:
    """Para una carpeta que llega: si la propuesta es una carpeta que se llama igual (sin contar los
    números), el sitio es al lado, no dentro: "White Dwarf 110-119" va junto a "White Dwarf 020-029"
    (y "Gimmick-Zero" se fusiona con "Gimmick Zero"). Las alternativas que acaban en el mismo
    sitio suman sus votos, y con ellos la confianza.
    """
    propias = set(palabras(nombre))

    def igual(ultimo: str) -> bool:
        # Iguales salvo etiquetas cortas de la serie ("UK", "WH"): "200-209" es hermana de "240-249 UK".
        otras = set(palabras(ultimo))
        return bool(otras) and all(len(x) <= 3 for x in otras ^ propias) and len(otras & propias) >= 2 \
            or otras == propias

    def sitio(carpeta: str) -> str:
        # Hacia arriba mientras se llame igual (por si alguna ya quedó anidada dentro de otra).
        while propias and "/" in carpeta and igual(carpeta.rsplit("/", 1)[-1]):
            carpeta = carpeta.rsplit("/", 1)[0]
        return carpeta

    if not p.get("propuesta"):
        return p
    sumas: dict[str, float] = {}
    for carpeta, pct in p["alternativas"]:
        sumas[sitio(carpeta)] = sumas.get(sitio(carpeta), 0) + pct
    alternativas = [[c, round(v)] for c, v in sorted(sumas.items(), key=lambda x: -x[1])]
    propuesta = sitio(p["propuesta"])
    return {**p, "propuesta": propuesta, "alternativas": alternativas,
            "confianza": max(p["confianza"], round(sumas.get(propuesta, 0) / 100, 3))}


def canal_telegram(con, nombre: str) -> str:
    """Título del canal de Telegram del que llegó el fichero ('' si no vino de Telegram)."""
    f = con.execute("SELECT k.titulo FROM telegram_msgs t JOIN telegram_canales k ON k.canal = t.canal "
                    "WHERE t.archivo = ? ORDER BY t.procesado_en DESC LIMIT 1", (nombre,)).fetchone()
    return f[0] if f and f[0] else ""


def destino_novela(nombre: str, canal: str) -> str | None:
    """Carpeta de novelas para un ebook, según el canal (primero) o el nombre; None si no es una."""
    if not es_ebook(nombre):
        return None
    for es_canal, texto in ((True, canal), (False, nombre)):
        for patron, carpeta, por_canal in REGLAS_NOVELAS:
            if texto and (por_canal or not es_canal) and patron.search(texto):
                return carpeta
    return None


# Mapas de Paizo: van a assets, a la carpeta de su tipo (el nombre lo dice siempre).
# Los Flip-Mats y Flip-Tiles de Paizo van con su juego, como ayudas de juego (assets ya no guarda mapas).
FLIP_PF = "RPG/Pathfinder/Any Edition/Game Aids"
FLIP_SF = "RPG/Starfinder/Game Aids/Flip-Mats"
REGLAS_MAPAS = [
    (re.compile(r"starfinder.*flip|flip.*starfinder", re.I), FLIP_SF),
    (re.compile(r"flip[\s_-]*tiles?", re.I), FLIP_PF + "/Flip-Tiles"),
    (re.compile(r"flip[\s_-]*ma[pt]s?(?![a-z])", re.I), FLIP_PF + "/Flip-Mats"),
    # Map Packs de Paizo: "GameMastery Map Pack - Village", "Map Pack - Graveyard".
    (re.compile(r"(paizo|pzo\d|gamemastery|pathfinder)[^a-z]*.*map[\s_-]*pack|^map[\s_-]*pack\s*-\s", re.I),
     FLIP_PF + "/Map Packs"),
]


def destino_mapa(nombre: str, canal: str = "") -> str | None:
    """Carpeta de mapas para un Flip-Mat / Flip-Tiles, por su nombre; None si no es uno."""
    for patron, carpeta in REGLAS_MAPAS:
        if patron.search(nombre):
            # "Flip-Mat" a secas: si llegó de un canal de Starfinder, es de Starfinder.
            if carpeta == FLIP_PF + "/Flip-Mats" and re.search(r"starfinder", canal, re.I):
                return FLIP_SF
            return carpeta
    return None


def _nombres_dentro(ruta: Path, n: int = 10) -> list[str]:
    """Nombres (sin extensión) de unos pocos ficheros y carpetas de dentro, para reconocer un pack."""
    if not ruta.is_dir():
        return []
    res = []
    try:
        for p in sorted(ruta.iterdir())[:200]:
            if not p.name.startswith("."):
                res.append(p.stem)
            if len(res) >= n:
                break
    except OSError:
        pass
    return res


def ruta_portada_entrada(cfg, entrada_id: int) -> Path:
    return cfg.portadas / "entrada" / f"{entrada_id}.jpg"


# --- Utilidades de ficheros -------------------------------------------------------

def _rel(cfg, ruta: Path) -> str:
    return ruta.relative_to(cfg.raiz).as_posix()


def _ultimo_cambio(ruta: Path) -> float:
    if ruta.is_file():
        return ruta.stat().st_mtime
    ultimo = ruta.stat().st_mtime
    for base, _dirs, ficheros in os.walk(ruta):
        for f in ficheros:
            try:
                ultimo = max(ultimo, os.stat(os.path.join(base, f)).st_mtime)
            except OSError:
                pass
    return ultimo


def _libre(destino: Path) -> Path:
    """'x.pdf' -> 'x (2).pdf' si ya existe (nunca se sobrescribe)."""
    if not destino.exists():
        return destino
    base, ext = (destino.stem, destino.suffix) if destino.is_file() or destino.suffix else (destino.name, "")
    n = 2
    while True:
        cand = destino.with_name(f"{base} ({n}){ext}")
        if not cand.exists():
            return cand
        n += 1


def sha256(ruta: Path) -> str:
    h = hashlib.sha256()
    with open(ruta, "rb") as f:
        for trozo in iter(lambda: f.read(1 << 20), b""):
            h.update(trozo)
    return h.hexdigest()


def _renombrar(origen: Path, destino: Path) -> None:
    destino.parent.mkdir(parents=True, exist_ok=True)
    if destino.exists():
        raise FileExistsError(destino)
    try:
        os.rename(origen, destino)
    except OSError as ex:
        if ex.errno != errno.EXDEV:
            raise
        shutil.move(str(origen), str(destino))


class Registro:
    """CSV con el mismo formato que los de la reorganización."""

    def __init__(self, cfg):
        self.ruta = cfg.raiz / "_Reorganizacion" / f"clasificador_{time.strftime('%Y-%m')}.csv"

    def __call__(self, accion: str, origen: str, destino: str, nota: str = "") -> None:
        self.ruta.parent.mkdir(parents=True, exist_ok=True)
        nuevo = not self.ruta.exists()
        with open(self.ruta, "a", encoding="utf-8-sig" if nuevo else "utf-8", newline="") as f:
            if nuevo:
                f.write("Fecha;Accion;Origen;Destino;Nota\r\n")
            campos = [time.strftime("%Y-%m-%d %H:%M:%S"), accion, origen, destino, nota]
            f.write(";".join(c.replace(";", ",") for c in campos) + "\r\n")


# --- 1. Escanear ------------------------------------------------------------------

def _permisos_normales(raiz: Path) -> None:
    """Los comprimidos traen sus propios permisos (a veces 700): se dejan como el resto de la biblioteca."""
    for base, dirs, ficheros in os.walk(raiz):
        for d in dirs:
            os.chmod(os.path.join(base, d), 0o2775)
        for f in ficheros:
            os.chmod(os.path.join(base, f), 0o664)
    os.chmod(raiz, 0o2775)


def descomprimir(con, cfg, archivo: Path, log: Registro) -> None:
    rel = _rel(cfg, archivo)
    if shutil.disk_usage(archivo.parent).free < archivo.stat().st_size * 4:
        raise OSError("poco espacio libre para descomprimir")
    tmp = archivo.parent / f".extrayendo-{archivo.stem}"
    if tmp.exists():
        shutil.rmtree(tmp)  # restos de un intento anterior (solo lo que extrajimos nosotros)
    tmp.mkdir()
    # bsdtar (libarchive) lee zip, rar y 7z, y no extrae rutas absolutas ni con '..'.
    r = subprocess.run([os.environ.get("BIBLIOTECA_TAR") or shutil.which("bsdtar") or "tar", "-xf", str(archivo), "-C", str(tmp)], capture_output=True, text=True, check=False,
                       timeout=1800)
    siete = shutil.which("7zz") or shutil.which("7z")
    if r.returncode != 0 and siete:
        # bsdtar no abre algunos zip (Deflate64, el de Windows para ficheros grandes): 7-Zip sí.
        # 7-Zip tampoco extrae rutas absolutas ni con '..'.
        shutil.rmtree(tmp, ignore_errors=True)
        tmp.mkdir()
        r = subprocess.run([siete, "x", "-y", "-bd", f"-o{tmp}", str(archivo)], capture_output=True, text=True,
                           check=False, timeout=1800)
    if r.returncode != 0:
        shutil.rmtree(tmp, ignore_errors=True)
        raise RuntimeError(f"no se pudo descomprimir: {(r.stderr or '').strip()[:200]}")
    _permisos_normales(tmp)
    hijos = [h for h in tmp.iterdir() if not h.name.startswith(("__MACOSX", "."))]
    # Un comprimido con una sola carpeta dentro: se usa esa carpeta, sin anidar.
    origen = hijos[0] if len(hijos) == 1 and hijos[0].is_dir() else tmp
    destino = _libre(archivo.parent / (origen.name if origen is not tmp else archivo.stem))
    os.rename(origen, destino)
    if tmp.exists():
        shutil.rmtree(tmp)  # solo quedan restos tipo __MACOSX
    guardado = _libre(cfg.raiz / COMPRIMIDOS_HECHOS / archivo.name)
    _renombrar(archivo, guardado)
    log("Descomprimir", rel, _rel(cfg, destino))
    log("Guardar comprimido", rel, _rel(cfg, guardado))


def nombre_legible(nombre: str) -> str | None:
    """Un nombre que no es UTF-8 (sale de un zip de DOS/Windows: "\\udc82\\udcae…") en su forma legible.
    None si ya es válido. CP866 si da cirílico (zips rusos), si no CP437 (el de los zip por defecto)."""
    try:
        nombre.encode("utf-8")
        return None
    except UnicodeEncodeError:
        pass
    crudo = nombre.encode("utf-8", "surrogateescape")
    ruso = crudo.decode("cp866", errors="replace")
    if sum("Ѐ" <= c <= "ӿ" for c in ruso) >= len(ruso.replace(" ", "")) / 2:
        return ruso
    return crudo.decode("cp437", errors="replace")


def arreglar_nombres(carpeta: Path, log: Registro | None = None) -> int:
    """Renombra (de dentro afuera) lo que tenga un nombre no UTF-8: si no, no cabe en el catálogo."""
    n = 0
    for dirpath, dirnames, filenames in os.walk(carpeta, topdown=False):
        for nombre in dirnames + filenames:
            nuevo = nombre_legible(nombre)
            if nuevo is None:
                continue
            viejo = Path(dirpath) / nombre
            final = _libre(Path(dirpath) / nuevo.replace("/", "_"))
            viejo.rename(final)
            n += 1
            if log:
                log("Renombrar", str(viejo).encode("utf-8", "surrogateescape").decode("utf-8", "replace"), str(final),
                    "nombre no UTF-8 (zip)")
    return n


def escanear(con, cfg, log: Registro) -> int:
    dir_entrada = cfg.raiz / cfg.entrada
    if not dir_entrada.is_dir():
        return 0
    arreglar_nombres(dir_entrada, log)
    activos = {r["ruta_actual"]: r for r in con.execute(
        f"SELECT id, ruta_actual, estado FROM entrada WHERE estado IN ({','.join('?' * len(ACTIVOS))})", ACTIVOS)}
    ahora = time.time()
    nuevos = 0
    for item in sorted(dir_entrada.iterdir()):
        if item.name.startswith((".", "@", "_")) or item.name in ("Thumbs.db", "desktop.ini"):
            continue
        rel = _rel(cfg, item)
        if rel in activos:
            r = activos[rel]
            if r["estado"] == "nuevo" and item.is_file() and item.suffix.lower() in COMPRIMIDOS:
                # Un comprimido que falló y se pidió «Reintentar»: se vuelve a descomprimir.
                try:
                    descomprimir(con, cfg, item, log)
                    estado, nota = "desaparecido", "descomprimido al reintentar"
                except Exception as ex:
                    estado, nota = "error", str(ex)[:300]
                with con:
                    con.execute("UPDATE entrada SET estado=?, nota=? WHERE id=?", (estado, nota, r["id"]))
            continue
        try:
            if ahora - _ultimo_cambio(item) < ESTABLE_SEG:
                continue  # todavía se está copiando
            if item.is_file() and item.suffix.lower() in COMPRIMIDOS:
                descomprimir(con, cfg, item, log)
                continue  # la carpeta resultante se recoge en la siguiente pasada
        except Exception as ex:
            with con:
                con.execute("INSERT INTO entrada (nombre, ruta_actual, es_carpeta, estado, nota, llegado_en) "
                            "VALUES (?, ?, 0, 'error', ?, ?)", (item.name, rel, str(ex)[:300], int(ahora)))
            print(f"  error con {rel}: {ex}", file=sys.stderr)
            continue
        with con:
            con.execute("INSERT INTO entrada (nombre, ruta_actual, es_carpeta, estado, llegado_en) "
                        "VALUES (?, ?, ?, 'nuevo', ?)", (item.name, rel, int(item.is_dir()), int(ahora)))
        nuevos += 1
    # Lo que alguien quitó de _Inbox a mano.
    for rel, r in activos.items():
        if r["estado"] != "aprobado" and not (cfg.raiz / rel).exists():
            with con:
                con.execute("UPDATE entrada SET estado='desaparecido' WHERE id=?", (r["id"],))
    return nuevos


# --- 2. Analizar ------------------------------------------------------------------

def _analizar_pdf(ruta_pdf: str, ruta_jpg: str) -> dict:
    """En un proceso aparte: portada, metadatos y líneas de copyright del PDF principal."""
    import pymupdf

    res = _procesar_pdf(ruta_pdf, ruta_jpg)
    with pymupdf.open(ruta_pdf) as doc:
        texto = texto_pdf.leer_texto(doc)
    res["lineas"] = "\n".join(ln for ln in texto.splitlines() if "©" in ln or "opyright" in ln or "ISBN" in ln)
    return res


def _pdfs(ruta: Path) -> list[Path]:
    if ruta.is_file():
        return [ruta] if ruta.suffix.lower() == ".pdf" else []
    return sorted((p for p in ruta.rglob("*") if p.is_file() and p.suffix.lower() == ".pdf"),
                  key=lambda p: p.as_posix().lower())


def texto_telegram(con, nombre: str) -> str:
    """Mensaje de Telegram del fichero (o del comprimido del que salió la carpeta)."""
    candidatos = [nombre] + [nombre + ext for ext in COMPRIMIDOS]
    f = con.execute(f"SELECT texto FROM telegram_msgs WHERE archivo IN ({','.join('?' * len(candidatos))}) "
                    "AND texto <> '' ORDER BY procesado_en DESC LIMIT 1", candidatos).fetchone()
    return f[0] if f else ""


def buscar_duplicado(con, cfg, pdf: Path, tam: int) -> int | None:
    """id del documento de la biblioteca idéntico (mismo tamaño y mismo SHA-256)."""
    candidatos = con.execute("SELECT id, ruta FROM docs WHERE tamano=?", (tam,)).fetchall()
    if not candidatos:
        return None
    propio = sha256(pdf)
    for c in candidatos:
        try:
            if sha256(cfg.raiz / c["ruta"]) == propio:
                return c["id"]
        except OSError:
            continue
    return None


_BAYES: dict = {}


def modelo_bayes(con) -> Bayes:
    """El modelo de toda la biblioteca, rehecho solo cuando cambia el catálogo (tarda unos segundos)."""
    version = con.execute("SELECT COUNT(*), MAX(id), MAX(actualizado_en) FROM docs").fetchone()
    if _BAYES.get("version") != tuple(version):
        _BAYES.update(version=tuple(version), modelo=Bayes.de(con))
    return _BAYES["modelo"]


def proponer_entrada(con, cfg, bayes, f, pdfs: list, editorial, pdf_titulo, origen_texto: str, canal: str) -> dict:
    """La propuesta de carpeta para una entrada (lo mismo al llegar que al repasar los pendientes)."""
    # Texto para buscar parecidos: nombre, título interno, nombres de los PDF de dentro
    # y el mensaje de Telegram con el que llegó (si vino de allí).
    consulta = " ".join([f["nombre"], pdf_titulo or "", (origen_texto or "")[:300], canal,
                         *[p.stem for p in pdfs[:10] if p.name != f["nombre"]]])
    novela = destino_mapa(f["nombre"], canal) or (None if f["es_carpeta"] else destino_novela(f["nombre"], canal))
    if novela:
        p = {"propuesta": novela, "confianza": 1.0, "apoyos": VECINOS, "alternativas": [[novela, 100]]}
    elif not f["es_carpeta"] and es_ebook(f["nombre"]):
        # Novela de juego desconocido: el parecido de nombres no sirve; espera a Haiku (no se mueve sola).
        p = {"propuesta": NOVELAS_SUELTAS, "confianza": 0.5, "apoyos": 0, "alternativas": [[NOVELAS_SUELTAS, 50]]}
    elif not pdfs and Path(f["nombre"]).suffix.lower() not in EBOOKS:
        # Sin PDF (pack de mapas, tokens, música…): se compara con las carpetas de assets,
        # usando también los nombres de lo que trae dentro.
        p = packs.proponer(con, " ".join([consulta, *_nombres_dentro(cfg.raiz / f["ruta_actual"])]))
    else:
        p = proponer(con, consulta, editorial)
        p = con_segunda_opinion(p, bayes, rasgos(consulta, editorial, pdf_titulo))
        serie = None if es_seguro(p, cfg.auto) else por_serie(bayes, f["nombre"])
        if serie:
            # "DDAL09-06": casi todos los DDAL09 que tenemos están en la misma carpeta.
            otras = [a for a in p["alternativas"] if a[0] != serie["propuesta"]]
            p = {**serie, "alternativas": [*serie["alternativas"], *otras][:5]}
    if f["es_carpeta"]:
        p = junto_a_sus_hermanas(p, f["nombre"])
    return p


def producto_ajeno(con, carpeta: str) -> bool:
    """¿Es la carpeta (o está dentro de) otro producto que llegó como carpeta por la Entrada?

    Un starter set de Fria Ligan se parece mucho a otro starter set de Fria Ligan, pero no va dentro
    de él: eso no se decide nunca solo."""
    for ruta, nombre, nuevo in con.execute(
            "SELECT ruta_actual, nombre, nuevo_nombre FROM entrada WHERE es_carpeta=1 AND estado='movido' AND "
            "(ruta_actual = ? OR substr(?, 1, length(ruta_actual) + 1) = ruta_actual || '/')", (carpeta, carpeta)):
        # Solo si sigue siendo su propia carpeta (no la de tipo o de juego con la que se fusionó).
        if ruta.rsplit("/", 1)[-1] in (nombre, nuevo):
            return True
    return False


def con_claude(alternativas: list, llm_json: str | None) -> list:
    """Las alternativas, con la carpeta que propuso Claude (si la hay y vale) la primera."""
    claude = json.loads(llm_json) if llm_json else {}
    if not (claude.get("valida") and claude.get("carpeta")):
        return alternativas
    otras = [a for a in alternativas if a[0] != claude["carpeta"] and a[1] is not None]
    return [[claude["carpeta"], None], *otras][:6]


_REPASO: dict = {}
REPASO_CADA = 600  # segundos: como mucho un repaso cada 10 minutos


def repasar_pendientes(con, cfg, log: "Registro | None" = None, forzar: bool = False) -> int:
    """Vuelve a proponer carpeta para lo que espera revisión cuando la biblioteca ha cambiado.

    Lo que se coloca (a mano o solo) sirve de ejemplo para lo parecido: un pendiente de ayer puede
    estar claro hoy. Solo recalcula la propuesta (sin releer los PDF); lo que ahora es seguro se
    aprueba solo, como lo que llega nuevo, y el resto queda con la propuesta al día.
    """
    version = tuple(con.execute("SELECT COUNT(*), MAX(id), MAX(actualizado_en) FROM docs").fetchone())
    ahora = time.time()
    if not forzar and (_REPASO.get("version") == version or ahora - _REPASO.get("cuando", 0) < REPASO_CADA):
        return 0
    _REPASO.update(version=version, cuando=ahora)
    filas = con.execute("SELECT * FROM entrada WHERE estado='pendiente' AND destino IS NULL ORDER BY id").fetchall()
    if not filas:
        return 0
    bayes = modelo_bayes(con)
    aprobados = cambiados = 0
    for f in filas:
        ruta = cfg.raiz / f["ruta_actual"]
        if not ruta.exists():
            continue  # lo marcará el escaneo
        p = proponer_entrada(con, cfg, bayes, f, _pdfs(ruta), f["editorial"], f["pdf_titulo"],
                             f["origen_texto"] or "", canal_telegram(con, f["nombre"]))
        if not p.get("propuesta"):
            if f["propuesta"]:  # la que tenía ya no vale (p. ej. una carpeta de assets que se borró)
                with con:
                    con.execute("UPDATE entrada SET propuesta=NULL, confianza=0, apoyos=0, alternativas=? "
                                "WHERE id=? AND estado='pendiente'",
                                (json.dumps(con_claude([], f["llm"]), ensure_ascii=False), f["id"]))
                cambiados += 1
            continue
        destino = p["propuesta"] if es_seguro(p, cfg.auto) else madre_segura(p, cfg.auto)
        if destino and producto_ajeno(con, destino):
            destino = None
        alternativas = json.dumps(con_claude(p["alternativas"], f["llm"]), ensure_ascii=False)
        if not destino and p["propuesta"] == f["propuesta"] and alternativas == f["alternativas"]:
            continue
        with con:
            if destino:
                con.execute("UPDATE entrada SET propuesta=?, confianza=?, apoyos=?, alternativas=?, estado='aprobado', "
                            "destino=?, auto=1, decidido_en=?, nota=? WHERE id=? AND estado='pendiente'",
                            (p["propuesta"], p["confianza"], p["apoyos"], alternativas, destino, int(ahora),
                             "colocado al repasar los pendientes", f["id"]))
                aprobados += 1
                print(f"  repaso: {f['nombre']} -> {destino} ({p['confianza']:.0%}, {p['apoyos']} apoyos)", flush=True)
            else:
                con.execute("UPDATE entrada SET propuesta=?, confianza=?, apoyos=?, alternativas=? "
                            "WHERE id=? AND estado='pendiente'",
                            (p["propuesta"], p["confianza"], p["apoyos"], alternativas, f["id"]))
                cambiados += 1
    print(f"Repaso de pendientes: {len(filas)} mirados, {aprobados} se colocan solos, "
          f"{cambiados} con propuesta nueva", flush=True)
    return aprobados


def analizar(con, cfg) -> int:
    filas = con.execute("SELECT * FROM entrada WHERE estado='nuevo' ORDER BY id").fetchall()
    if not filas:
        return 0
    bayes = modelo_bayes(con)
    conocidas = [r[0] for r in con.execute("SELECT DISTINCT editorial FROM docs WHERE editorial IS NOT NULL")]
    dicc = texto_pdf.diccionario_editoriales(conocidas)
    info: dict[int, dict] = {}
    trabajos = []
    for f in filas:
        ruta = cfg.raiz / f["ruta_actual"]
        pdfs = _pdfs(ruta)
        tam_total = ruta.stat().st_size if ruta.is_file() else sum(p.stat().st_size for p in ruta.rglob("*") if p.is_file())
        principal = max(pdfs, key=lambda p: p.stat().st_size) if pdfs else None
        info[f["id"]] = {"pdfs": pdfs, "tamano": tam_total, "principal": principal}
        if principal:
            trabajos.append((f["id"], _rel(cfg, principal)))

    resultados: dict[int, dict] = {}

    def guardar(eid, res=None, error=None):
        resultados[eid] = res or {"error": error}

    if trabajos:
        en_paralelo(cfg, trabajos, _analizar_pdf,
                    lambda eid, rel: (str(cfg.raiz / rel), str(ruta_portada_entrada(cfg, eid))), guardar)

    for f in filas:
        i, r = info[f["id"]], resultados.get(f["id"], {})
        datos = texto_pdf.extraer(r.get("lineas", ""), f["nombre"], dicc) if "lineas" in r else {}
        # ¿Ya lo tenemos?
        duplicado_de, n_dup = None, 0
        for p in i["pdfs"][:200]:
            d = buscar_duplicado(con, cfg, p, p.stat().st_size)
            if d:
                n_dup += 1
                duplicado_de = duplicado_de or d
        todo_duplicado = bool(i["pdfs"]) and n_dup == len(i["pdfs"])
        # Texto para buscar parecidos: nombre, título interno, nombres de los PDF de dentro
        # y el mensaje de Telegram con el que llegó (si vino de allí).
        origen_texto = texto_telegram(con, f["nombre"])
        canal = canal_telegram(con, f["nombre"])  # "Warhammer 40000 9°Edicion": dice de qué juego es
        p = proponer_entrada(con, cfg, bayes, f, i["pdfs"], datos.get("editorial"), r.get("pdf_titulo"),
                             origen_texto, canal)
        destino = p["propuesta"]
        llm_json = None
        if todo_duplicado:
            estado, auto, destino = ("aprobado", 1, DUPLICADOS) if cfg.auto else ("pendiente", 0, DUPLICADOS)
        elif es_seguro(p, cfg.auto) and not producto_ajeno(con, p["propuesta"]):
            estado, auto = "aprobado", 1
        elif madre_segura(p, cfg.auto) and not producto_ajeno(con, madre_segura(p, cfg.auto)):
            # Se sabe el juego pero no la subcarpeta: a "<juego>/_Por clasificar".
            estado, auto, destino = "aprobado", 1, madre_segura(p, cfg.auto)
        else:
            estado, auto = "pendiente", 0
            # Segunda lectura con Claude: mira la portada y las primeras páginas.
            claude = None if f["es_carpeta"] and not i["pdfs"] else llm.segunda_lectura(
                con, cfg, f["id"], f["nombre"], ruta_portada_entrada(cfg, f["id"]), i["principal"], p)
            if claude:
                llm_json = json.dumps(claude, ensure_ascii=False)
                if claude["valida"]:
                    # Su carpeta, la primera de las opciones para revisar.
                    otras = [a for a in p["alternativas"] if a[0] != claude["carpeta"]]
                    p = {**p, "alternativas": [[claude["carpeta"], None], *otras][:6]}
                if claude["valida"] and claude["seguridad"] == "alta" and cfg.auto:
                    estado, auto, destino = "aprobado", 1, claude["carpeta"]
        with con:
            con.execute("UPDATE entrada SET llm=? WHERE id=?", (llm_json, f["id"]))
            con.execute(
                "UPDATE entrada SET tamano=?, n_pdfs=?, paginas=?, pdf_titulo=?, editorial=?, publicado=?, "
                "portada=?, duplicado_de=?, duplicados=?, propuesta=?, confianza=?, apoyos=?, alternativas=?, "
                "estado=?, destino=?, auto=?, nota=?, decidido_en=?, origen_texto=? WHERE id=?",
                (i["tamano"], len(i["pdfs"]), r.get("paginas"), r.get("pdf_titulo"), datos.get("editorial"),
                 datos.get("publicado"), 1 if "paginas" in r else (-1 if i["principal"] else 0),
                 duplicado_de, n_dup, p["propuesta"], p["confianza"], p["apoyos"],
                 json.dumps(p["alternativas"], ensure_ascii=False), estado,
                 destino if estado == "aprobado" else None, auto, r.get("error"),
                 int(time.time()) if estado == "aprobado" else None, origen_texto or None, f["id"]),
            )
        print(f"  {f['nombre']}: {estado} -> {destino or '-'} ({p['confianza']:.0%}, {p['apoyos']} apoyos)"
              f"{'  [Claude]' if auto and llm_json and destino and json.loads(llm_json)['carpeta'] == destino else ''}"
              f"{'  DUPLICADO' if todo_duplicado else ''}", flush=True)
    return len(filas)


# --- 3. Mover ---------------------------------------------------------------------

def destino_valido(cfg, destino: str) -> str | None:
    """Normaliza y valida una carpeta de destino escrita a mano. None si no vale."""
    d = (destino or "").replace("\\", "/").strip().strip("/")
    partes = [x.strip() for x in d.split("/") if x.strip()]
    if not partes or any(x in (".", "..") or x.startswith(".") for x in partes):
        return None
    if any(c in x for x in partes for c in '<>:"|?*'):
        return None
    d = "/".join(partes)
    if config_mod.en_areas(cfg.areas, d) or d == DUPLICADOS or d.startswith(DUPLICADOS + "/"):
        return d
    return None


def _fusionar(origen: Path, destino: Path, cfg, log: Registro) -> list[str]:
    """Mueve el contenido de la carpeta origen dentro de destino (que ya existe), fichero a fichero."""
    notas = []
    for p in sorted(origen.rglob("*")):
        if not p.is_file():
            continue
        objetivo = destino / p.relative_to(origen)
        if objetivo.exists():
            if objetivo.stat().st_size == p.stat().st_size and sha256(objetivo) == sha256(p):
                otro = _libre(cfg.raiz / DUPLICADOS / p.name)
                _renombrar(p, otro)
                log("Duplicado", _rel(cfg, p), _rel(cfg, otro), f"idéntico a {_rel(cfg, objetivo)}")
                notas.append(f"{p.name} ya estaba")
                continue
            objetivo = _libre(objetivo)
        _renombrar(p, objetivo)
    # Solo carpetas que se han quedado vacías.
    for d in sorted((x for x in origen.rglob("*") if x.is_dir()), key=lambda x: -len(x.parts)):
        try:
            d.rmdir()
        except OSError:
            pass
    try:
        origen.rmdir()
    except OSError:
        pass
    return notas


def mover(con, cfg, fila, log: Registro) -> tuple[str, str]:
    origen = cfg.raiz / fila["ruta_actual"]
    destino_rel = destino_valido(cfg, fila["destino"])
    if not destino_rel:
        raise ValueError(f"destino no válido: {fila['destino']!r}")
    if not origen.exists():
        raise FileNotFoundError(fila["ruta_actual"])
    nombre = (fila["nuevo_nombre"] or "").strip() or origen.name
    if "/" in nombre or "\\" in nombre or nombre.startswith("."):
        raise ValueError(f"nombre no válido: {nombre!r}")
    if origen.is_file() and not Path(nombre).suffix:
        nombre += origen.suffix
    carpeta = cfg.raiz / destino_rel
    objetivo = carpeta / nombre
    ultimo = destino_rel.rsplit("/", 1)[-1]
    if origen.is_dir() and not (fila["nuevo_nombre"] or "").strip() and "/" in destino_rel \
            and set(palabras(ultimo)) and set(palabras(ultimo)) == set(palabras(origen.name)):
        # "Gimmick-Zero" a "RPG/Gimmick-zero": esa carpeta ES el destino (nueva o para fusionar), no se anida.
        objetivo = carpeta
    nota = ""
    if objetivo.exists() and origen.is_dir() and objetivo.is_dir():
        notas = _fusionar(origen, objetivo, cfg, log)
        nota = "fusionado con la carpeta existente" + (f"; {', '.join(notas[:5])}" if notas else "")
        log("Mover (fusionar)", fila["ruta_actual"], _rel(cfg, objetivo), nota)
    else:
        if objetivo.exists() and origen.is_file() and objetivo.is_file() \
                and objetivo.stat().st_size == origen.stat().st_size and sha256(objetivo) == sha256(origen):
            objetivo = _libre(cfg.raiz / DUPLICADOS / origen.name)
            nota = f"ya estaba en {destino_rel}: va a _Duplicados"
        else:
            objetivo = _libre(objetivo)
        _renombrar(origen, objetivo)
        log("Mover", fila["ruta_actual"], _rel(cfg, objetivo), nota)
    return _rel(cfg, objetivo), nota


def ejecutar_aprobados(con, cfg, log: Registro) -> int:
    filas = con.execute("SELECT * FROM entrada WHERE estado='aprobado' ORDER BY id").fetchall()
    carpetas_tocadas: set[str] = set()
    for f in filas:
        anterior = f["ruta_actual"]
        try:
            nueva, nota = mover(con, cfg, f, log)
        except Exception as ex:
            with con:
                con.execute("UPDATE entrada SET estado='error', nota=? WHERE id=?",
                            (f"{type(ex).__name__}: {ex}"[:300], f["id"]))
            print(f"  error moviendo {anterior}: {ex}", file=sys.stderr)
            continue
        with con:
            con.execute("UPDATE entrada SET estado=?, ruta_actual=?, nota=?, movido_en=? WHERE id=?",
                        ("descartado" if nueva.startswith("_Duplicados/") else "movido", nueva, nota or None,
                         int(time.time()), f["id"]))
        print(f"  movido: {anterior} -> {nueva}", flush=True)
        for rel in (anterior, nueva):
            if config_mod.en_areas(cfg.areas, rel):
                # El catálogo se pone al día solo en lo tocado: la carpeta (o la que contiene el fichero).
                carpetas_tocadas.add(rel if (cfg.raiz / rel).is_dir() or not Path(rel).suffix else rel.rsplit("/", 1)[0])
    if carpetas_tocadas:
        actualizar_catalogo(con, cfg, sorted(carpetas_tocadas))
    return len(filas)


PAPELERA = "_Papelera"


def ejecutar_borrados(con, cfg, log: Registro) -> int:
    """Lo pedido desde la ficha: el fichero va a _Papelera/<su ruta> (no se borra) y la ficha se quita."""
    from .indexador import ruta_portada
    filas = con.execute("SELECT * FROM borrados WHERE estado='pedido' ORDER BY id").fetchall()
    for f in filas:
        origen = cfg.raiz / f["ruta"]
        try:
            if not origen.is_file():
                raise FileNotFoundError(f"ya no está: {f['ruta']}")
            destino = _libre(cfg.raiz / PAPELERA / f["ruta"])
            destino.parent.mkdir(parents=True, exist_ok=True)
            _renombrar(origen, destino)
        except OSError as ex:
            with con:
                con.execute("UPDATE borrados SET estado='error', nota=?, hecho_en=? WHERE id=?",
                            (str(ex)[:300], int(time.time()), f["id"]))
            print(f"  no se pudo quitar {f['ruta']}: {ex}", file=sys.stderr)
            continue
        rel = _rel(cfg, destino)
        with con:
            con.execute("DELETE FROM docs WHERE id=?", (f["doc_id"],))
            con.execute("UPDATE borrados SET estado='hecho', papelera=?, hecho_en=? WHERE id=?",
                        (rel, int(time.time()), f["id"]))
        ruta_portada(cfg.portadas, f["doc_id"]).unlink(missing_ok=True)
        log("Papelera", f["ruta"], rel, "quitado desde la ficha")
        print(f"  a la papelera: {f['ruta']}", flush=True)
    return len(filas)


def actualizar_catalogo(con, cfg, carpetas: list[str]) -> None:
    r = sincronizar(con, cfg, carpetas)
    # Un pack que entra en assets (o sale): se rehace el índice de su carpeta madre.
    madres = sorted({c.rsplit("/", 1)[0] for c in carpetas if c.startswith(packs.RAIZ + "/") and "/" in c})
    if madres:
        packs.indexar(con, cfg, madres)
    print(f"  catálogo: {r['nuevos']} nuevos, {r['borrados']} quitados", flush=True)
    existentes = [c for c in carpetas if (cfg.raiz / c).is_dir()]
    if existentes:
        generar_portadas(con, cfg, None, existentes)
        pasada_pdf(con, cfg, None, existentes)


# --- Bucle ------------------------------------------------------------------------

def pasada(con, cfg) -> None:
    log = Registro(cfg)
    escanear(con, cfg, log)
    analizar(con, cfg)
    repasar_pendientes(con, cfg, log)
    entrada_lotes.automatico(con, cfg)
    ejecutar_aprobados(con, cfg, log)
    ejecutar_borrados(con, cfg, log)


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description="Clasifica lo que llega a la carpeta de entrada.")
    ap.add_argument("--una-vez", action="store_true")
    ap.add_argument("--cada", type=int, default=60, help="segundos entre pasadas")
    args = ap.parse_args(argv)
    cfg = config_mod.cargar()
    con = conectar(cfg.db)
    print(f"Clasificador: vigilando {cfg.raiz / cfg.entrada} (automático desde {cfg.auto:.0%})", flush=True)
    while True:
        try:
            pasada(con, cfg)
            estadisticas.fotografiar(con)
        except Exception as ex:  # que un fallo raro no pare el servicio
            print(f"Error en la pasada: {type(ex).__name__}: {ex}", file=sys.stderr, flush=True)
        if args.una_vez:
            break
        time.sleep(args.cada)
    con.close()


if __name__ == "__main__":
    main()
