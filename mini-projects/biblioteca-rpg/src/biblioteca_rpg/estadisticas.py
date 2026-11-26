"""Seguimiento en el tiempo: lo que llega a la Entrada, lo que sale de ella y lo que queda por clasificar.

El flujo de la Entrada se reconstruye con sus fechas (llegado_en, decidido_en, movido_en). Lo que no
tiene historia (documentos en «_Por clasificar», tamaño de la biblioteca) se guarda con una foto al
día en la tabla `fotos`: la saca el clasificador en cada pasada (como mucho cada 10 minutos, y
pisa la de ese día) y la web al abrir la página.
"""

import time
from datetime import date, datetime, timedelta

from .reorganizar import POR_CLASIFICAR

ESQUEMA = """
CREATE TABLE IF NOT EXISTS fotos (
    dia TEXT PRIMARY KEY,           -- AAAA-MM-DD (hora local)
    docs INTEGER NOT NULL,          -- documentos en la biblioteca
    por_clasificar INTEGER NOT NULL,-- de ellos, en carpetas «_Por clasificar»
    entrada INTEGER NOT NULL,       -- en la Entrada sin decidir (nuevo, pendiente, error)
    revisar INTEGER NOT NULL,       -- emparejados de RPGGeek por revisar
    creado_en INTEGER NOT NULL
);
"""
CADA = 600  # segundos mínimos entre fotos
AGRUPACIONES = {"dia": "Días", "semana": "Semanas", "mes": "Meses", "anio": "Años"}
MESES = ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic"]


def crear(con) -> None:
    con.executescript(ESQUEMA)
    # Cuándo aceptaste o rechazaste un emparejado de RPGGeek/BGG (los anteriores a esto no lo tienen).
    if "decidido_en" not in {f[1] for f in con.execute("PRAGMA table_info(extra)")}:
        con.execute("ALTER TABLE extra ADD COLUMN decidido_en INTEGER")


def fotografiar(con, ahora: float | None = None, forzar: bool = False) -> bool:
    """Guarda (o actualiza) la foto de hoy. Devuelve True si la ha sacado."""
    crear(con)
    ahora = time.time() if ahora is None else ahora
    dia = date.fromtimestamp(ahora).isoformat()
    if not forzar:
        fila = con.execute("SELECT creado_en FROM fotos WHERE dia=?", (dia,)).fetchone()
        if fila and ahora - fila[0] < CADA:
            return False
    docs, por_clasificar = con.execute(
        "SELECT COUNT(*), COALESCE(SUM(carpeta LIKE ?), 0) FROM docs", (f"%{POR_CLASIFICAR}%",)).fetchone()
    entrada = con.execute("SELECT COUNT(*) FROM entrada WHERE estado IN ('nuevo', 'pendiente', 'error')").fetchone()[0]
    revisar = con.execute("SELECT COUNT(*) FROM extra WHERE fuente='geek' AND estado='pendiente'").fetchone()[0]
    with con:
        con.execute("INSERT INTO fotos (dia, docs, por_clasificar, entrada, revisar, creado_en) VALUES (?,?,?,?,?,?) "
                    "ON CONFLICT(dia) DO UPDATE SET docs=excluded.docs, por_clasificar=excluded.por_clasificar, "
                    "entrada=excluded.entrada, revisar=excluded.revisar, creado_en=excluded.creado_en",
                    (dia, docs, por_clasificar, entrada, revisar, int(ahora)))
    return True


# --- periodos ---------------------------------------------------------------------------------

def inicio_periodo(d: date, agrupar: str) -> date:
    if agrupar == "semana":
        return d - timedelta(days=d.weekday())  # lunes
    if agrupar == "mes":
        return d.replace(day=1)
    if agrupar == "anio":
        return d.replace(month=1, day=1)
    return d


def siguiente(d: date, agrupar: str) -> date:
    if agrupar == "semana":
        return d + timedelta(days=7)
    if agrupar == "mes":
        return date(d.year + d.month // 12, d.month % 12 + 1, 1)
    if agrupar == "anio":
        return date(d.year + 1, 1, 1)
    return d + timedelta(days=1)


def etiqueta(d: date, agrupar: str) -> str:
    if agrupar == "anio":
        return str(d.year)
    if agrupar == "mes":
        return f"{MESES[d.month - 1]} {d.year}"
    if agrupar == "semana":
        return f"sem. {d.day} {MESES[d.month - 1]}"
    return f"{d.day} {MESES[d.month - 1]}"


def agrupacion_auto(desde: date, hasta: date) -> str:
    dias = (hasta - desde).days
    return "dia" if dias <= 62 else "semana" if dias <= 400 else "mes" if dias <= 1500 else "anio"


def _ts(d: date) -> float:
    return datetime(d.year, d.month, d.day).timestamp()


def primer_dia(con) -> date:
    crear(con)
    ts = con.execute("SELECT MIN(llegado_en) FROM entrada").fetchone()[0]
    foto = con.execute("SELECT MIN(dia) FROM fotos").fetchone()[0]
    dias = [date.fromtimestamp(ts)] if ts else []
    if foto:
        dias.append(date.fromisoformat(foto))
    return min(dias) if dias else date.today()


def serie(con, desde: date, hasta: date, agrupar: str, ahora: float | None = None) -> dict:
    """Valores por periodo entre `desde` y `hasta` (ambos incluidos)."""
    crear(con)
    ahora = time.time() if ahora is None else ahora
    periodos = []
    d = inicio_periodo(desde, agrupar)
    while d <= hasta and len(periodos) < 2000:
        periodos.append(d)
        d = siguiente(d, agrupar)
    n = len(periodos)
    if not n:
        return {"agrupar": agrupar, "periodos": [], "etiquetas": [], "totales": {}}
    limites = [_ts(p) for p in periodos] + [_ts(siguiente(periodos[-1], agrupar))]
    t0, t1 = _ts(desde), min(_ts(hasta + timedelta(days=1)), ahora)

    def cubo(ts) -> int | None:
        if ts is None or not t0 <= ts < t1:
            return None
        lo, hi = 0, n - 1
        while lo < hi:  # el último periodo cuyo inicio es <= ts
            m = (lo + hi + 1) // 2
            if limites[m] <= ts:
                lo = m
            else:
                hi = m - 1
        return lo

    llegados, solos, a_mano, duplicados = [0] * n, [0] * n, [0] * n, [0] * n
    filas = con.execute("SELECT estado, auto, llegado_en, decidido_en, movido_en FROM entrada").fetchall()
    for estado, auto, llegado, decidido, movido in filas:
        if (i := cubo(llegado)) is not None:
            llegados[i] += 1
        if estado == "movido" and (i := cubo(movido or decidido)) is not None:
            (solos if auto else a_mano)[i] += 1
        elif estado == "descartado" and (i := cubo(movido or decidido)) is not None:
            duplicados[i] += 1

    # Sin decidir al final de cada periodo: había llegado y aún no se había decidido.
    vivos = [(f[2], f[3] or f[4]) for f in filas if f[0] != "desaparecido" and f[2]]
    en_entrada = []
    for i in range(n):
        fin = min(limites[i + 1], ahora)
        if limites[i] > ahora:
            en_entrada.append(None)
            continue
        en_entrada.append(sum(1 for lleg, dec in vivos if lleg < fin and (dec is None or dec >= fin)))

    # RPGGeek/BGG: emparejados solos (por cuándo se buscaron) y decididos por ti (por cuándo lo
    # decidiste; los de antes de apuntarlo, por cuándo se buscaron). Por revisar al final de cada periodo.
    geek_auto, geek_si, geek_no = [0] * n, [0] * n, [0] * n
    geek = con.execute("SELECT estado, creado_en, decidido_en FROM extra WHERE fuente='geek' "
                       "AND estado IN ('auto', 'aceptado', 'rechazado', 'pendiente')").fetchall()
    revisables = []
    for estado, creado, decidido in geek:
        if estado == "auto":
            if (i := cubo(creado)) is not None:
                geek_auto[i] += 1
            continue
        if estado == "pendiente":
            revisables.append((creado, None))
        elif decidido:
            revisables.append((creado, decidido))
        if estado != "pendiente" and (i := cubo(decidido or creado)) is not None:
            (geek_si if estado == "aceptado" else geek_no)[i] += 1
    por_revisar = []
    for i in range(n):
        fin = min(limites[i + 1], ahora)
        por_revisar.append(None if limites[i] > ahora else sum(
            1 for c, d in revisables if c and c < fin and (d is None or d >= fin)))

    # De las fotos: la última de cada periodo.
    fotos = con.execute("SELECT dia, docs, por_clasificar, revisar FROM fotos WHERE dia BETWEEN ? AND ? ORDER BY dia",
                        (desde.isoformat(), hasta.isoformat())).fetchall()
    docs, por_clasificar, revisar = [None] * n, [None] * n, [None] * n
    for dia, nd, npc, nr in fotos:
        if (i := cubo(_ts(date.fromisoformat(dia)) + 1)) is not None:
            docs[i], por_clasificar[i], revisar[i] = nd, npc, nr

    return {
        "agrupar": agrupar,
        "periodos": [p.isoformat() for p in periodos],
        "etiquetas": [etiqueta(p, agrupar) for p in periodos],
        "llegados": llegados, "solos": solos, "a_mano": a_mano, "duplicados": duplicados,
        "en_entrada": en_entrada, "docs": docs, "por_clasificar": por_clasificar, "revisar": revisar,
        "geek_auto": geek_auto, "geek_si": geek_si, "geek_no": geek_no, "por_revisar": por_revisar,
        "totales": {"llegados": sum(llegados), "solos": sum(solos), "a_mano": sum(a_mano),
                    "duplicados": sum(duplicados), "geek_auto": sum(geek_auto), "geek_si": sum(geek_si),
                    "geek_no": sum(geek_no)},
    }
