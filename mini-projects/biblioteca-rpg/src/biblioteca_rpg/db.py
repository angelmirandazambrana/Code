"""Catálogo en SQLite (modo WAL: la web lee mientras el indexador escribe)."""

import sqlite3
from pathlib import Path

CAMPOS_RUTA = [
    "carpeta", "nombre", "titulo", "categoria", "formato", "sistema", "edicion",
    "editorial", "anio", "ambientacion", "tipo", "idioma", "codigo", "codigo_orden",
]

ESQUEMA = """
CREATE TABLE IF NOT EXISTS docs (
    id INTEGER PRIMARY KEY,
    ruta TEXT NOT NULL UNIQUE,          -- relativa a la raíz, con '/'
    carpeta TEXT, nombre TEXT, titulo TEXT,
    categoria TEXT, formato TEXT, sistema TEXT, edicion TEXT, editorial TEXT,
    anio INTEGER, ambientacion TEXT, tipo TEXT, idioma TEXT,
    tamano INTEGER, mtime INTEGER,
    paginas INTEGER, pdf_titulo TEXT, pdf_autor TEXT,
    portada INTEGER NOT NULL DEFAULT 0, -- 0 pendiente, 1 hecha, -1 error
    error TEXT,
    llegado_en INTEGER,                 -- primera vez que el indexador lo vio
    actualizado_en INTEGER
);
CREATE INDEX IF NOT EXISTS ix_categoria ON docs(categoria);
CREATE INDEX IF NOT EXISTS ix_sistema ON docs(sistema);
CREATE INDEX IF NOT EXISTS ix_editorial ON docs(editorial);
CREATE INDEX IF NOT EXISTS ix_edicion ON docs(edicion);
CREATE INDEX IF NOT EXISTS ix_tipo ON docs(tipo);
CREATE INDEX IF NOT EXISTS ix_carpeta ON docs(carpeta);
CREATE INDEX IF NOT EXISTS ix_llegado ON docs(llegado_en);
CREATE INDEX IF NOT EXISTS ix_portada ON docs(portada);

CREATE VIRTUAL TABLE IF NOT EXISTS docs_fts USING fts5(
    titulo, carpeta, pdf_titulo, pdf_autor,
    content='docs', content_rowid='id', tokenize='unicode61 remove_diacritics 2'
);
CREATE TRIGGER IF NOT EXISTS docs_ai AFTER INSERT ON docs BEGIN
    INSERT INTO docs_fts(rowid, titulo, carpeta, pdf_titulo, pdf_autor)
    VALUES (new.id, new.titulo, new.carpeta, new.pdf_titulo, new.pdf_autor);
END;
CREATE TRIGGER IF NOT EXISTS docs_ad AFTER DELETE ON docs BEGIN
    INSERT INTO docs_fts(docs_fts, rowid, titulo, carpeta, pdf_titulo, pdf_autor)
    VALUES ('delete', old.id, old.titulo, old.carpeta, old.pdf_titulo, old.pdf_autor);
END;
CREATE TRIGGER IF NOT EXISTS docs_au AFTER UPDATE OF titulo, carpeta, pdf_titulo, pdf_autor ON docs BEGIN
    INSERT INTO docs_fts(docs_fts, rowid, titulo, carpeta, pdf_titulo, pdf_autor)
    VALUES ('delete', old.id, old.titulo, old.carpeta, old.pdf_titulo, old.pdf_autor);
    INSERT INTO docs_fts(rowid, titulo, carpeta, pdf_titulo, pdf_autor)
    VALUES (new.id, new.titulo, new.carpeta, new.pdf_titulo, new.pdf_autor);
END;

-- Datos que no salen de la ruta: del texto del PDF ('pdf') o de RPGGeek/BGG ('geek').
-- estado: auto (aceptado por confianza), aceptado / rechazado (a mano), pendiente (revisar),
--         sin_resultado (no se encontró nada).
-- Datos escritos a mano en la ficha: mandan sobre los deducidos (ver manual.py).
CREATE TABLE IF NOT EXISTS manual (
    doc_id INTEGER NOT NULL REFERENCES docs(id) ON DELETE CASCADE,
    campo TEXT NOT NULL,
    valor TEXT NOT NULL,
    creado_en INTEGER,
    PRIMARY KEY (doc_id, campo)
);
CREATE TABLE IF NOT EXISTS extra (
    doc_id INTEGER NOT NULL REFERENCES docs(id) ON DELETE CASCADE,
    fuente TEXT NOT NULL,
    estado TEXT NOT NULL,
    confianza REAL,
    ref TEXT,                           -- id en la fuente (p. ej. rpgitem de RPGGeek)
    datos TEXT NOT NULL DEFAULT '{}',   -- JSON
    creado_en INTEGER,
    PRIMARY KEY (doc_id, fuente)
);
CREATE INDEX IF NOT EXISTS ix_extra_estado ON extra(fuente, estado);

-- Lo que llega a la carpeta de entrada (_Inbox): un fichero o una carpeta = un lote.
-- estado: nuevo (sin analizar), pendiente (revisar), aprobado (en cola para mover),
--         movido, descartado (movido a _Duplicados), desaparecido (lo quitó otro), error.
CREATE TABLE IF NOT EXISTS entrada (
    id INTEGER PRIMARY KEY,
    nombre TEXT NOT NULL,               -- nombre con el que llegó
    ruta_actual TEXT NOT NULL,          -- relativa a la raíz, donde está ahora
    es_carpeta INTEGER NOT NULL DEFAULT 0,
    tamano INTEGER, n_pdfs INTEGER, paginas INTEGER,
    pdf_titulo TEXT, editorial TEXT, publicado INTEGER,
    portada INTEGER NOT NULL DEFAULT 0,
    duplicado_de INTEGER,               -- docs.id idéntico (mismo SHA-256)
    duplicados INTEGER NOT NULL DEFAULT 0,  -- cuántos de sus PDF ya están en la biblioteca
    propuesta TEXT, confianza REAL, apoyos INTEGER, alternativas TEXT,
    estado TEXT NOT NULL DEFAULT 'nuevo',
    destino TEXT, nuevo_nombre TEXT,
    auto INTEGER NOT NULL DEFAULT 0,    -- 1 si lo movió el clasificador sin preguntar
    nota TEXT,
    llegado_en INTEGER, decidido_en INTEGER, movido_en INTEGER
);
CREATE INDEX IF NOT EXISTS ix_entrada_estado ON entrada(estado);

-- Libros que se piden quitar desde la ficha (repetidos, malos escaneos…). La web solo anota la
-- petición; el clasificador mueve el fichero a _Papelera/<misma ruta> y quita la ficha.
-- estado: pedido, hecho, error.
CREATE TABLE IF NOT EXISTS borrados (
    id INTEGER PRIMARY KEY,
    doc_id INTEGER NOT NULL,
    ruta TEXT NOT NULL,
    titulo TEXT,
    estado TEXT NOT NULL DEFAULT 'pedido',
    papelera TEXT,                      -- dónde quedó, relativa a la raíz
    nota TEXT,
    pedido_en INTEGER, hecho_en INTEGER
);
CREATE INDEX IF NOT EXISTS ix_borrados_estado ON borrados(estado);

-- Telegram: hasta dónde se ha leído cada canal y qué se hizo con cada fichero.
CREATE TABLE IF NOT EXISTS telegram_canales (
    canal TEXT PRIMARY KEY,             -- id de Telegram (p. ej. -1001234567890)
    titulo TEXT,
    ultimo_id INTEGER NOT NULL DEFAULT 0,
    revisado_en INTEGER
);
-- estado: descargado, ya_en_biblioteca, ya_en_entrada, repetido, ignorado, error
CREATE TABLE IF NOT EXISTS telegram_msgs (
    canal TEXT NOT NULL,
    msg_id INTEGER NOT NULL,
    tg_doc_id INTEGER,                  -- id del documento en Telegram (igual si se reenvía)
    nombre TEXT, tamano INTEGER,
    texto TEXT,                         -- el mensaje que acompaña al fichero
    fecha INTEGER,
    estado TEXT NOT NULL,
    doc_id INTEGER,                     -- el de la biblioteca, si ya lo teníamos
    archivo TEXT,                       -- nombre con el que quedó en _Inbox
    nota TEXT,
    intentos INTEGER NOT NULL DEFAULT 0,
    procesado_en INTEGER,
    PRIMARY KEY (canal, msg_id)
);
CREATE INDEX IF NOT EXISTS ix_tg_doc ON telegram_msgs(tg_doc_id);
CREATE INDEX IF NOT EXISTS ix_tg_archivo ON telegram_msgs(archivo);
CREATE INDEX IF NOT EXISTS ix_tg_procesado ON telegram_msgs(procesado_en);

-- Respuestas de la API de RPGGeek/BGG ya pedidas (para no repetir consultas).
CREATE TABLE IF NOT EXISTS geek_cache (
    clave TEXT PRIMARY KEY,
    respuesta TEXT NOT NULL,
    creado_en INTEGER
);

-- Noticias de webs de rol y wargames (sus RSS): pistas para Haiku sobre lo que llega a la Entrada.
CREATE TABLE IF NOT EXISTS novedades (
    url TEXT PRIMARY KEY,
    fuente TEXT NOT NULL,
    titulo TEXT NOT NULL,
    resumen TEXT,
    fecha TEXT,              -- AAAA-MM-DD
    leido_en INTEGER
);
CREATE INDEX IF NOT EXISTS ix_novedades_fecha ON novedades(fecha);

-- Listas de productos de wikis (Traveller Wiki, Wikipedia), para emparejar con los libros (referencias.py).
CREATE TABLE IF NOT EXISTS referencias (
    id INTEGER PRIMARY KEY,
    fuente TEXT NOT NULL,       -- traveller | wikipedia
    pagina TEXT NOT NULL,
    carpetas TEXT NOT NULL,     -- JSON: prefijos de ruta donde buscar sus libros
    titulo TEXT NOT NULL,
    anio INTEGER,
    mes INTEGER,
    editorial TEXT,
    codigo TEXT,                -- TSR9039, PZO1110…
    modulo TEXT,                -- código de módulo (A1, B2…)
    isbn TEXT,                  -- ISBN-13
    autores TEXT,
    seccion TEXT,
    url TEXT
);
CREATE INDEX IF NOT EXISTS ix_referencias_pagina ON referencias(pagina);
"""

# Columnas añadidas después de la v1: se crean al conectar si faltan.
# Son los valores "efectivos" que salen de la tabla extra (los de la ruta nunca se tocan).
COLUMNAS_EXTRA = {
    "publicado": "INTEGER",      # año de publicación (el 'anio' de la ruta es el de la edición)
    "publicado_mes": "INTEGER",  # mes, si la fuente del año lo sabe
    "ext_editorial": "TEXT",     # editorial cuando la ruta no la dice
    "autores": "TEXT",
    "isbn": "TEXT",
    "geek_id": "INTEGER",
    "geek_tipo": "TEXT",         # rpgitem | boardgame
    "origen": "TEXT",            # JSON {campo: fuente}
    "editorial_unif": "TEXT",    # editorial con el nombre unificado (ver editoriales.py)
    "codigo": "TEXT",            # código de catálogo de la editorial, del nombre del fichero
    "codigo_orden": "TEXT",      # el mismo, con el número alineado para ordenar (codigos.orden)
    "ext_codigo": "TEXT",        # código sacado del texto del PDF, si el nombre no lo tiene
    "ext_codigo_orden": "TEXT",
}

# Editorial unificada: forma -> nombre (lo rellena editoriales.recalcular) y disparadores que
# mantienen docs.editorial_unif al día cuando cambia la editorial de un documento.
_EDITORIAL = "COALESCE(new.editorial, new.ext_editorial)"
_UNIF = f"COALESCE((SELECT nombre FROM editorial_alias WHERE forma = {_EDITORIAL}), {_EDITORIAL})"
ESQUEMA_EDITORIALES = f"""
CREATE TABLE IF NOT EXISTS editorial_alias (forma TEXT PRIMARY KEY, nombre TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS ix_editorial_unif ON docs(editorial_unif);
CREATE INDEX IF NOT EXISTS ix_codigo_orden ON docs(codigo_orden);
CREATE INDEX IF NOT EXISTS ix_ext_codigo_orden ON docs(ext_codigo_orden);
CREATE TRIGGER IF NOT EXISTS docs_editorial_ai AFTER INSERT ON docs BEGIN
    UPDATE docs SET editorial_unif = {_UNIF} WHERE id = new.id;
END;
CREATE TRIGGER IF NOT EXISTS docs_editorial_au AFTER UPDATE OF editorial, ext_editorial ON docs BEGIN
    UPDATE docs SET editorial_unif = {_UNIF} WHERE id = new.id;
END;
"""


COLUMNAS_ENTRADA = {
    "origen_texto": "TEXT",      # mensaje de Telegram que acompañaba al fichero
    "llm": "TEXT",               # lo que dijo Claude (JSON: carpeta, seguridad, juego, tipo, motivo)
}


def conectar(ruta: Path, solo_lectura: bool = False) -> sqlite3.Connection:
    ruta.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(ruta, timeout=30, check_same_thread=False)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA synchronous=NORMAL")
    con.execute("PRAGMA foreign_keys=ON")
    if not solo_lectura:
        con.executescript(ESQUEMA)
        _migrar(con)
    return con


def _migrar(con: sqlite3.Connection) -> None:
    nuevas = set()
    for tabla, columnas in (("docs", COLUMNAS_EXTRA), ("entrada", COLUMNAS_ENTRADA)):
        existentes = {r[1] for r in con.execute(f"PRAGMA table_info({tabla})")}
        for col, tipo in columnas.items():
            if col not in existentes:
                nuevas.add(col)
                try:
                    con.execute(f"ALTER TABLE {tabla} ADD COLUMN {col} {tipo}")
                except sqlite3.OperationalError as ex:  # otro proceso la creó a la vez
                    if "duplicate column" not in str(ex):
                        raise
    con.execute("CREATE INDEX IF NOT EXISTS ix_ext_editorial ON docs(ext_editorial)")
    con.executescript(ESQUEMA_EDITORIALES)
    if "editorial_unif" in nuevas:  # hasta que se recalculen los alias, la editorial tal cual
        con.execute("UPDATE docs SET editorial_unif = COALESCE(editorial, ext_editorial)")
    con.commit()
