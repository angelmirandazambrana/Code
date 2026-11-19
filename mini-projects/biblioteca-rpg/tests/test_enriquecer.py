import json
import re

import pytest
from fastapi.testclient import TestClient

from biblioteca_rpg import geek, texto_pdf

DICC = texto_pdf.diccionario_editoriales(["Goodman Games", "Arcane Library", "Subclasses"])


@pytest.mark.parametrize("texto, esperado", [
    ("Codex: Grey Knights © Copyright Games Workshop Limited 2017. GW, Games Workshop",
     {"publicado": 2017, "editorial": "Games Workshop"}),
    ("Printed in the U.S.A. ©2008 Wizards of the Coast, LLC.", {"publicado": 2008, "editorial": "Wizards of the Coast"}),
    ("© 1984, Fantasy Factory. All Rights Reserved.", {"publicado": 1984}),
    # La licencia OGL (año 2000) no manda: manda el copyright del propio libro.
    (("System Reference Document Copyright 2000, Wizards of the Coast, Inc.\n"
     "Temple of the Basilisk Cult. Copyright 2014, Arcane Library, LLC."),
     {"publicado": 2014, "editorial": "Arcane Library"}),
    # Los créditos de arte cuentan para el año pero no para la editorial.
    ("Text ©2009, ©2012, ©2017 Michael Curtis, art and design elements ©2018 Goodman Games.",
     {"publicado": 2018}),
    ("Copyright -1 987 by Game Designers' Workshop.", {}),
])
def test_extraer_copyright(texto, esperado):
    res = texto_pdf.extraer(texto, "x.pdf", DICC, anio_max=2026)
    res.pop("isbn", None)
    assert res == esperado


def test_isbn_valido_y_en_nombre():
    # El segundo no cuadra con su dígito de control (errata u OCR): se descarta.
    assert texto_pdf.isbns("ISBN 1-56882-132-8 y ISBN 0-88038-481-9") == ["1568821328"]
    assert texto_pdf.isbns("", "Deep Magic -- Kobold Press -- 9781936781317 -- x.pdf") == ["9781936781317"]


def test_variantes_y_parecido():
    v = geek.variantes("Savage Worlds - Frontier Towns - Fort Griffin [v1.2]", "Savage Worlds")
    assert v[0] == "Savage Worlds - Frontier Towns - Fort Griffin"
    assert "Fort Griffin" in v
    assert geek._parecido("The Tomb of Horrors", "Tomb of Horrors") == 1.0
    assert geek._parecido("S1 Tomb of Horrors", "Tomb of Horrors") > 0.85
    assert geek._parecido("Monster Manual", "Tomb of Horrors") < 0.5


class ClienteFalso:
    peticiones = 0

    def __init__(self, resultados, fichas):
        self.resultados, self._fichas = resultados, fichas

    def buscar(self, q, tipo):
        return self.resultados.get(q, [])

    def fichas(self, ids, tipo):
        return {i: self._fichas[i] for i in ids if i in self._fichas}


def _ficha(i, nombre, anio, editorial):
    return {"id": i, "tipo": "rpgitem", "nombre": nombre, "alternativos": [], "anio": anio,
            "editoriales": [editorial], "autores": ["Gary Gygax"], "sistema": [], "url": f"u/{i}"}


def test_emparejar_auto_pendiente_y_nada():
    doc = {"titulo": "Tomb of Horrors", "categoria": "Rol", "sistema": "D&D", "editorial": "TSR", "publicado": 1978}
    c = ClienteFalso({"Tomb of Horrors": [{"id": 1, "nombre": "S1: Tomb of Horrors", "anio": 1978},
                                          {"id": 2, "nombre": "Return to the Tomb of Horrors", "anio": 1998}]},
                     {1: _ficha(1, "Tomb of Horrors", 1978, "TSR"), 2: _ficha(2, "Return to the Tomb of Horrors", 1998, "TSR")})
    r = geek.emparejar(c, doc)
    assert r["estado"] == "auto" and r["ref"] == "1"

    dudoso = {**doc, "titulo": "Tomb Horrors Remix", "publicado": None, "editorial": None}
    c2 = ClienteFalso({"Tomb Horrors Remix": [{"id": 1, "nombre": "Tomb of Horrors", "anio": 1978}]},
                      {1: _ficha(1, "Tomb of Horrors", 1978, "TSR")})
    assert geek.emparejar(c2, dudoso)["estado"] == "pendiente"

    assert geek.emparejar(ClienteFalso({}, {}), doc)["estado"] == "sin_resultado"


def test_leer_ficha_xml():
    import xml.etree.ElementTree as ET
    xml = """<items><item type="rpgitem" id="42"><name type="primary" value="Tomb of Horrors"/>
    <name type="alternate" value="S1"/><yearpublished value="1978"/>
    <link type="rpgpublisher" id="1" value="TSR"/><link type="rpgdesigner" id="2" value="Gary Gygax"/>
    <link type="rpgsystem" id="3" value="AD&amp;D 1e"/></item></items>"""
    f = geek.leer_ficha(ET.fromstring(xml).find("item"), "rpgitem")
    assert f["nombre"] == "Tomb of Horrors" and f["anio"] == 1978
    assert f["editoriales"] == ["TSR"] and f["autores"] == ["Gary Gygax"] and f["sistema"] == ["AD&D 1e"]
    assert f["url"].endswith("/rpgitem/42")


@pytest.fixture()
def catalogo(tmp_path, monkeypatch):
    raiz = tmp_path / "biblio"
    for rel in ["RPG/D&D/1e/Tomb of Horrors.pdf", "RPG/Varios/Cosa.pdf"]:
        p = raiz / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"%PDF-1.4 falso")
    monkeypatch.setenv("BIBLIOTECA_RAIZ", str(raiz))
    monkeypatch.setenv("BIBLIOTECA_DATOS", str(tmp_path / "datos"))
    from biblioteca_rpg import config, db, indexador
    cfg = config.cargar()
    con = db.conectar(cfg.db)
    indexador.sincronizar(con, cfg, cfg.areas)
    return cfg, con


def test_aplicar_y_revisar_en_la_web(catalogo):
    import importlib

    from biblioteca_rpg import enriquecer, web
    _cfg, con = catalogo
    tomb = con.execute("SELECT id FROM docs WHERE titulo LIKE 'Tomb%'").fetchone()[0]
    cosa = con.execute("SELECT id FROM docs WHERE titulo = 'Cosa'").fetchone()[0]
    with con:
        con.execute("INSERT INTO extra VALUES (?, 'pdf', 'auto', 0.8, NULL, ?, 0)",
                    (cosa, json.dumps({"publicado": 2019, "editorial": "Kobold Press", "isbn": ["9781936781317"]})))
        datos = {"tipo": "rpgitem", "elegido": _ficha(1, "Tomb of Horrors", 1978, "TSR"),
                 "candidatos": [{**_ficha(1, "Tomb of Horrors", 1978, "TSR"), "puntos": 0.8},
                                {**_ficha(7, "Tomb of Horrors (reprint)", 2005, "WotC"), "puntos": 0.79}]}
        con.execute("INSERT INTO extra VALUES (?, 'geek', 'pendiente', 0.8, '1', ?, 0)", (tomb, json.dumps(datos)))
        assert enriquecer.aplicar(con) == 1
    fila = con.execute("SELECT publicado, ext_editorial, isbn FROM docs WHERE id=?", (cosa,)).fetchone()
    assert tuple(fila) == (2019, "Kobold Press", "9781936781317")
    con.close()

    importlib.reload(web)
    cli = TestClient(web.app)
    # La editorial sacada del PDF ya sirve para filtrar.
    r = cli.get("/", params={"editorial": "Kobold Press"})
    assert "Cosa" in r.text and "Tomb of Horrors" not in r.text
    assert "según el PDF" in cli.get(f"/libro/{cosa}").text

    r = cli.get("/revisar")
    assert "Tomb of Horrors (reprint)" in r.text and re.search(r'class="globo">1<', r.text)
    r = cli.post(f"/revisar/{tomb}", params={"accion": "aceptar", "ref": 7, "volver": f"/libro/{tomb}"},
                 follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == f"/libro/{tomb}"
    ficha = cli.get(f"/libro/{tomb}").text
    assert "Tomb of Horrors (reprint)" in ficha and "confirmado a mano" in ficha and "2005" in ficha
    cli.post(f"/revisar/{tomb}", params={"accion": "rechazar"}, follow_redirects=False)
    assert "Publicado" not in cli.get(f"/libro/{tomb}").text
    assert cli.post(f"/revisar/{cosa}", params={"accion": "aceptar"}).status_code == 400
