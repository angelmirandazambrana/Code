import pytest

from biblioteca_rpg import codigos as c


@pytest.mark.parametrize(("nombre", "ruta", "esperado"), [
    ("MGT2 - Skandersvik [2020][MGP40039].pdf", "", "MGP40039"),
    ("Starfinder - PZO - Ports of Call [PZO7121].pdf", "", "PZO7121"),
    ("Ice7211 - Silent Death - Sunrunners - ICE7211.pdf", "", "ICE7211"),
    ("TSR 4003 - Revolt on Antares.pdf", "", "TSR4003"),
    ("Gammarauders - Base Set (TSR7105).pdf", "", "TSR7105"),
    ("HU - Core Book - 1st Ed Revised - PAL500P.pdf", "", "PAL500P"),
    ("The Dungeoneer #15 {JG0125}.pdf", "", "JG0125"),
    ("DCC 071 The 13th Skull.pdf", "", "DCC071"),
    ("CB72000 The One Ring.pdf", "", "CB72000"),
    ("Osprey - MAA 190 - American Civil War Armies 4.pdf", "Wargames/Osprey/Men-at-Arms", "MAA190"),
    # No son códigos:
    ("DCC 2016 Holiday Module - Twilight of the Solstice.pdf", "", None),     # un año
    ("Warlock! Traitor's Edition [WAR023].pdf", "RPG/Warlock", None),          # WAR solo en Osprey
    ("40K 8e - Codex - Death Guard (CA 2018 updated).pdf", "", None),
    ("ag 1000 things.pdf", "", None),
    ("Fate Core.pdf", "", None),
])
def test_del_nombre(nombre, ruta, esperado):
    assert c.del_nombre(nombre, ruta) == esperado


def test_orden():
    assert c.orden("PZO9") < c.orden("PZO10") < c.orden("PZO1110")
    assert c.orden("JG0125") == c.orden("JG125") == "JG 000125"
    assert c.orden("CB72000") == "CB7 002000"
    assert c.orden(None) is None


def test_del_texto():
    # El único código, aunque esté en mayúsculas en mitad de una línea.
    assert c.del_texto("Product code: MGP 8880\nWritten by…")[0] == "MGP8880"
    # Varios: el que se repite (portada y contraportada) gana a los anuncios.
    assert c.del_texto("PZO9200\nSee also PZO1110 Core Rulebook\nPZO9200", "Paizo") == (
        "PZO9200", ["PZO9200", "PZO1110"])
    # Varios sin uno claro: ninguno.
    assert c.del_texto("MGP 8880 and MGP 8881")[0] is None
    # Las líneas de copyright no cuentan.
    assert c.del_texto("© TSR 1983. All rights reserved.\nTSR 9123")[0] == "TSR9123"
    # De otra editorial: no.
    assert c.del_texto("PZO1110", "Mongoose Publishing")[0] is None


def test_catalogo_ordenar_y_buscar(tmp_path, monkeypatch):
    raiz = tmp_path / "biblio"
    for rel in [
        "RPG/Traveller/2e (Mongoose Publishing, 2016)/MGT2 - Skandersvik [2020][MGP40039].pdf",
        "RPG/Traveller/2e (Mongoose Publishing, 2016)/MGT2 - Core [MGP40000].pdf",
        "RPG/Traveller/2e (Mongoose Publishing, 2016)/MGT2 - Sin codigo.pdf",
    ]:
        p = raiz / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"%PDF-1.4")
    monkeypatch.setenv("BIBLIOTECA_RAIZ", str(raiz))
    monkeypatch.setenv("BIBLIOTECA_DATOS", str(tmp_path / "datos"))
    from biblioteca_rpg import config, consultas, db, indexador
    cfg = config.cargar()
    con = db.conectar(cfg.db)
    indexador.sincronizar(con, cfg, cfg.areas)
    r = consultas.buscar(con, {}, None, None, "codigo", 1)
    assert [f["codigo"] for f in r["filas"]] == ["MGP40000", "MGP40039", None]
    # El código del PDF también ordena y se encuentra al buscarlo.
    with con:
        con.execute("UPDATE docs SET ext_codigo='MGP40010', ext_codigo_orden=? WHERE codigo IS NULL",
                    [c.orden("MGP40010")])
    r = consultas.buscar(con, {}, None, None, "codigo", 1)
    assert [f["codigo"] or f["ext_codigo"] for f in r["filas"]] == ["MGP40000", "MGP40010", "MGP40039"]
    assert consultas.buscar(con, {}, "mgp 40010", None, "relevancia", 1)["total"] == 1
    assert consultas.buscar(con, {}, "MGP40039", None, "relevancia", 1)["total"] == 1


def test_pasada_codigos_lee_el_pdf(tmp_path, monkeypatch):
    import pymupdf

    raiz = tmp_path / "biblio"
    carpeta = raiz / "RPG/Traveller/2e (Mongoose Publishing, 2016)"
    carpeta.mkdir(parents=True)
    for nombre, texto in [("Aramis.pdf", "Traveller\nMGP 40050\nWritten by"),
                          ("Con codigo [MGP40001].pdf", "nada"),
                          ("Sin nada.pdf", "Traveller adventure")]:
        doc = pymupdf.open()
        doc.new_page().insert_text((72, 72), texto)
        doc.save(carpeta / nombre)
        doc.close()
    monkeypatch.setenv("BIBLIOTECA_RAIZ", str(raiz))
    monkeypatch.setenv("BIBLIOTECA_DATOS", str(tmp_path / "datos"))
    monkeypatch.setenv("BIBLIOTECA_PROCESOS", "1")
    from biblioteca_rpg import config, db, enriquecer, indexador
    cfg = config.cargar()
    con = db.conectar(cfg.db)
    indexador.sincronizar(con, cfg, cfg.areas)
    cuenta = enriquecer.pasada_codigos(con, cfg)
    assert cuenta["leidos"] == 2, cuenta  # el que ya trae el código en el nombre no se lee
    fila = con.execute("SELECT ext_codigo, ext_codigo_orden, origen FROM docs WHERE titulo='Aramis'").fetchone()
    assert fila["ext_codigo"] == "MGP40050" and fila["ext_codigo_orden"] == "MGP 040050"
    assert '"codigo": "pdf"' in fila["origen"]
    assert enriquecer.pasada_codigos(con, cfg) == {"leidos": 0}  # cada PDF se lee una vez


def test_nombres_de_juego_y_revistas_no_dan_codigo():
    assert c.del_nombre("Cyberpunk 2020 - Night City.pdf") is None
    assert c.del_nombre("Hardwired [CP3001].pdf") == "CP3001"
    texto = "TSR 9123\nTSR 9123\n"
    assert c.del_texto(texto, None, "Magazines/Arcane/Arcane 15.pdf") == (None, [])
    assert c.del_texto(texto, None, "RPG/D&D/1e/Modulo.pdf")[0] == "TSR9123"
