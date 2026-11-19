"""Catálogo en SQLite (modo WAL: la web lee mientras el indexador escribe)."""

import sqlite3
from pathlib import Path

CAMPOS_RUTA = [
    "carpeta", "nombre", "titulo", "categoria", "formato", "sistema", "edicion",
    "editorial", "anio", "ambientacion", "tipo", "idioma",
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

-- Respuestas de la API de RPGGeek/BGG ya pedidas (para no repetir consultas).
CREATE TABLE IF NOT EXISTS geek_cache (
    clave TEXT PRIMARY KEY,
    respuesta TEXT NOT NULL,
    creado_en INTEGER
);
"""

# Columnas añadidas después de la v1: se crean al conectar si faltan.
# Son los valores "efectivos" que salen de la tabla extra (los de la ruta nunca se tocan).
COLUMNAS_EXTRA = {
    "publicado": "INTEGER",      # año de publicación (el 'anio' de la ruta es el de la edición)
    "ext_editorial": "TEXT",     # editorial cuando la ruta no la dice
    "autores": "TEXT",
    "isbn": "TEXT",
    "geek_id": "INTEGER",
    "geek_tipo": "TEXT",         # rpgitem | boardgame
    "origen": "TEXT",            # JSON {campo: fuente}
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
    existentes = {r[1] for r in con.execute("PRAGMA table_info(docs)")}
    for col, tipo in COLUMNAS_EXTRA.items():
        if col not in existentes:
            try:
                con.execute(f"ALTER TABLE docs ADD COLUMN {col} {tipo}")
            except sqlite3.OperationalError as ex:  # otro proceso la creó a la vez
                if "duplicate column" not in str(ex):
                    raise
    con.execute("CREATE INDEX IF NOT EXISTS ix_ext_editorial ON docs(ext_editorial)")
    con.commit()
