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
from . import packs, texto_pdf
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
from .indexador import _procesar_pdf, en_paralelo, generar_portadas, sincronizar

COMPRIMIDOS = {".zip", ".rar", ".7z", ".cbz", ".cbr"}
ESTABLE_SEG = 120          # sin cambios durante este tiempo = terminó de copiarse/descargarse
DUPLICADOS = "_Duplicados/_Inbox"
COMPRIMIDOS_HECHOS = "_Duplicados/_Inbox comprimidos"
ACTIVOS = ("nuevo", "pendiente", "aprobado", "error")

# Novelas: el parecido de nombres no sirve ("Dead Drop" acaba en Drop Dead Studios), así que los
# ebooks se colocan por el tema del canal de Telegram del que llegaron o por su propio nombre.
EBOOKS = {".epub", ".mobi", ".azw", ".azw3"}
NOVELAS_40K = "Wargames/Miniaturas/Games Workshop/Warhammer 40K/Black Library/Novels"
REGLAS_NOVELAS = [
    (re.compile(r"necromunda", re.I), NOVELAS_40K + "/Necromunda"),
    (re.compile(r"horus heresy", re.I), NOVELAS_40K + "/Horus Heresy"),
    (re.compile(r"warhammer|40\.?000|\b40k\b|black library|space marine|necromunda", re.I), NOVELAS_40K),
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
    if Path(nombre).suffix.lower() not in EBOOKS:
        return None
    for texto in (canal, nombre):
        for patron, carpeta in REGLAS_NOVELAS:
            if texto and patron.search(texto):
                return carpeta
    return None


# Mapas de Paizo: van a assets, a la carpeta de su tipo (el nombre lo dice siempre).
PAIZO = "assets/By Artist or Source/Paizo"
REGLAS_MAPAS = [
    (re.compile(r"starfinder.*flip|flip.*starfinder", re.I), PAIZO + "/Starfinder/Flip-Mats"),
    (re.compile(r"flip[\s_-]*tiles?", re.I), PAIZO + "/Pathfinder/Flip-Tiles"),
    (re.compile(r"flip[\s_-]*ma[pt]s?(?![a-z])", re.I), PAIZO + "/Pathfinder/Flip Mats"),
]


def destino_mapa(nombre: str, canal: str = "") -> str | None:
    """Carpeta de mapas para un Flip-Mat / Flip-Tiles, por su nombre; None si no es uno."""
    for patron, carpeta in REGLAS_MAPAS:
        if patron.search(nombre):
            # "Flip-Mat" a secas: si llegó de un canal de Starfinder, es de Starfinder.
            if carpeta.endswith("/Pathfinder/Flip Mats") and re.search(r"starfinder", canal, re.I):
                return PAIZO + "/Starfinder/Flip-Mats"
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


def escanear(con, cfg, log: Registro) -> int:
    dir_entrada = cfg.raiz / cfg.entrada
    if not dir_entrada.is_dir():
        return 0
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
        consulta = " ".join([f["nombre"], r.get("pdf_titulo") or "", origen_texto[:300], canal,
                             *[p.stem for p in i["pdfs"][:10] if p.name != f["nombre"]]])
        novela = destino_mapa(f["nombre"], canal) or (None if f["es_carpeta"] else destino_novela(f["nombre"], canal))
        if novela:
            p = {"propuesta": novela, "confianza": 1.0, "apoyos": VECINOS, "alternativas": [[novela, 100]]}
        elif not i["pdfs"] and Path(f["nombre"]).suffix.lower() not in EBOOKS:
            # Sin PDF (pack de mapas, tokens, música…): se compara con las carpetas de assets,
            # usando también los nombres de lo que trae dentro.
            p = packs.proponer(con, " ".join([consulta, *_nombres_dentro(cfg.raiz / f["ruta_actual"])]))
        else:
            p = proponer(con, consulta, datos.get("editorial"))
            p = con_segunda_opinion(p, bayes, rasgos(consulta, datos.get("editorial"), r.get("pdf_titulo")))
            serie = None if es_seguro(p, cfg.auto) else por_serie(bayes, f["nombre"])
            if serie:
                # "DDAL09-06": casi todos los DDAL09 que tenemos están en la misma carpeta.
                otras = [a for a in p["alternativas"] if a[0] != serie["propuesta"]]
                p = {**serie, "alternativas": [*serie["alternativas"], *otras][:5]}
        if f["es_carpeta"]:
            p = junto_a_sus_hermanas(p, f["nombre"])
        destino = p["propuesta"]
        if todo_duplicado:
            estado, auto, destino = ("aprobado", 1, DUPLICADOS) if cfg.auto else ("pendiente", 0, DUPLICADOS)
        elif es_seguro(p, cfg.auto):
            estado, auto = "aprobado", 1
        elif madre_segura(p, cfg.auto):
            # Se sabe el juego pero no la subcarpeta: a "<juego>/_Por clasificar".
            estado, auto, destino = "aprobado", 1, madre_segura(p, cfg.auto)
        else:
            estado, auto = "pendiente", 0
        with con:
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
    ejecutar_aprobados(con, cfg, log)


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
        except Exception as ex:  # que un fallo raro no pare el servicio
            print(f"Error en la pasada: {type(ex).__name__}: {ex}", file=sys.stderr, flush=True)
        if args.una_vez:
            break
        time.sleep(args.cada)
    con.close()


if __name__ == "__main__":
    main()
