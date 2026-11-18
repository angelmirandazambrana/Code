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
"""


def conectar(ruta: Path, solo_lectura: bool = False) -> sqlite3.Connection:
    ruta.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(ruta, timeout=30, check_same_thread=False)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA synchronous=NORMAL")
    con.execute("PRAGMA foreign_keys=ON")
    if not solo_lectura:
        con.executescript(ESQUEMA)
    return con
