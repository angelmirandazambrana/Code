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
    # En un wargame o juego de mesa no hay revisión a mano: o coincide el juego o nada.
    assert geek.emparejar(c2, {**dudoso, "categoria": "Wargames"})["estado"] == "sin_resultado"

    assert geek.emparejar(ClienteFalso({}, {}), doc)["estado"] == "sin_resultado"


def test_motivos_y_umbral_sin_dudas():
    # «Pathfinder 3rd Party» del catálogo es «Pathfinder 1E Compatible Product» en RPGGeek.
    assert geek.coincide_sistema("Pathfinder 3rd Party", {"sistema": ["Pathfinder 1E Compatible Product"]})
    # Sin otra duda y con algo que lo confirme (aquí la editorial) basta con 0,85; si no, a revisar y
    # se apunta por qué.
    doc = {"titulo": "Rats in the Street", "categoria": "Rol", "editorial": "Frog God Games"}
    for puntos, editorial, estado in ((0.88, "Frog God Games", "auto"), (0.80, "Frog God Games", "pendiente"),
                                      (0.88, None, "pendiente")):
        orig = geek.puntuar
        geek.puntuar = lambda *a, p=puntos: p
        try:
            r = geek.emparejar(ClienteFalso({doc["titulo"]: [{"id": 1, "nombre": doc["titulo"], "anio": 2010}]},
                                            {1: _ficha(1, doc["titulo"], 2010, "Frog God Games")}),
                               {**doc, "editorial": editorial})
        finally:
            geek.puntuar = orig
        assert r["estado"] == estado, (puntos, editorial)
        assert r["datos"].get("motivos") == (None if estado == "auto" else ["puntos"])


def test_preseleccion_con_titulos_comunes():
    # RPGGeek llama «Dungeon Master's Guide (D&D 5e)» a la de 2014 y deja sin etiqueta las de 1979 y 1989:
    # la etiqueta no cuenta y desempata el año. Y la búsqueda viene por orden alfabético: el bueno puede
    # estar más allá del resultado 40.
    doc = {"titulo": "Dungeon Master's Guide", "categoria": "Rol", "sistema": "D&D",
           "editorial": "Wizards of the Coast", "publicado": 2014}
    relleno = [{"id": 100 + i, "nombre": f"Adventurers League Dungeon Master's Guide {i}", "anio": 2016}
               for i in range(45)]
    nombres = {1: ("Dungeon Master's Guide", 1979), 2: ("Dungeon Master's Guide", 1989),
               3: ("Dungeon Master's Guide (D&D 3e)", 2000), 4: ("Dungeon Master's Guide (D&D 3.5e)", 2003),
               5: ("Dungeon Master's Guide (D&D 4e)", 2008), 6: ("Dungeon Master's Guide (D&D 5e)", 2014)}
    buscados = relleno + [{"id": i, "nombre": n, "anio": a} for i, (n, a) in nombres.items()]
    fichas = {i: _ficha(i, n, a, "Wizards of the Coast" if a > 1995 else "TSR") for i, (n, a) in nombres.items()}
    r = geek.emparejar(ClienteFalso({doc["titulo"]: buscados}, fichas), doc)
    assert r["ref"] == "6" and r["estado"] == "auto"


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
    assert "2005" not in cli.get(f"/libro/{tomb}").text  # el año de RPGGeek se va con el rechazo
    assert cli.post(f"/revisar/{cosa}", params={"accion": "aceptar"}).status_code == 400


def test_juego_de_mesa_se_busca_por_la_carpeta():
    # «TMB - Rulebook» acababa en «DreadBall: Rulebook»: hay que buscar el juego, no «Rulebook».
    bones = {"id": 5, "tipo": "boardgame", "nombre": "Too Many Bones", "alternativos": [], "anio": 2017,
             "editoriales": ["Chip Theory Games"], "autores": [], "sistema": [], "url": "u/5"}
    c = ClienteFalso({"Too Many Bones": [{"id": 5, "nombre": "Too Many Bones", "anio": 2017}],
                      "Rulebook": [{"id": 9, "nombre": "DreadBall: Rulebook", "anio": 2012}]}, {5: bones})
    doc = {"titulo": "TMB - Rulebook", "categoria": "Juegos de mesa", "carpeta": "Juegos de mesa/Too Many Bones"}
    r = geek.emparejar(c, doc)
    assert r["estado"] == "auto" and r["ref"] == "5"
    assert "Rulebook" not in geek.variantes("TMB - Rulebook")


def test_parte_del_titulo_sin_confirmar_no_es_automatico():
    witch = {"id": 3, "tipo": "rpgitem", "nombre": "Witch Hunter: The Invisible World",
             "alternativos": ["The Invisible World"], "anio": 2007, "editoriales": ["Alligator Alley"],
             "autores": [], "sistema": [], "url": "u/3"}
    c = ClienteFalso({"The Invisible World": [{"id": 3, "nombre": "Witch Hunter: The Invisible World"}]}, {3: witch})
    doc = {"titulo": "Sky Realms of Jorune Isho - The Invisible World", "categoria": "Rol",
           "sistema": "Skyrealms of Jorune"}
    assert geek.emparejar(c, doc)["estado"] == "pendiente"
    clue = {**witch, "tipo": "boardgame", "nombre": "Clue: Star Wars", "alternativos": []}
    assert geek.puntuar({}, clue, ["Star Wars"]) < geek.UMBRAL_AUTO


def test_variantes_con_lo_que_ya_sabemos():
    v = geek.variantes
    assert v("JG48 Wilderlands of High Fantasy", "Wilderlands", "JG48")[0] == "Wilderlands of High Fantasy"
    assert "Red Crypt" in " | ".join(v("The Fantasy Trip SJG Red Crypt", "The Fantasy Trip"))
    assert "Samaris" in v("Dark Dungeon Samaris", "Dark Dungeon")
    assert v("Story Engine Corebook", "Story Engine") == ["Story Engine"]
    assert v("the-good-the-bad-and-the-troll")[0] == "the good the bad and the troll"
    assert "The Sting of the Spider" in v("Top Secret S.I. - TSR7636 - TSE2 The Sting of the Spider", None, "TSR7636")


def test_juego_de_la_carpeta_en_wargames():
    j = geek.juego_para_bgg
    assert j("Wargames/Avalon Hill/ASL/Critical Hit/Scenarios & Campaigns", "x") == "Advanced Squad Leader"
    assert j("Wargames/Games Workshop/Warhammer 40K/Any Edition/Supplements", "x") == "Warhammer 40,000"
    assert j("Wargames/Avalon Hill/Panzer Leader & Panzer Blitz/Scenarios", "x") == "Panzer Leader"
    assert j("Wargames/Other Publishers/Five/Five parsecs from home/3E", "x") == "Five parsecs from home"
    assert j("Wargames/WRG/Scenarios & Campaigns", "De Bellis Antiquitatis") == "De Bellis Antiquitatis"
    assert j("Wargames/Solo/games/Escape from Stalingrad Z", "Escape from Stalingrad Z") == "Escape from Stalingrad Z"
    assert geek.no_es_juego("Wargames/_Reference/Military History/Osprey")


def test_puntuar_con_isbn_codigo_y_sistema():
    ficha = {"id": 1, "tipo": "rpgitem", "nombre": "Riverworld", "alternativos": [], "anio": 1989,
             "editoriales": ["Steve Jackson Games"], "sistema": ["GURPS (3rd Edition)"], "serie": [],
             "versiones": [{"anio": 1990, "isbn": ["1556341601"], "codigo": "6028", "paginas": 128, "editoriales": []}]}
    base = geek.puntuar({"titulo": "x"}, ficha, ["Riverworld Book"])
    assert geek.puntuar({"isbn": "9781556341601"}, ficha, ["Riverworld Book"]) > base  # ISBN-10 = ISBN-13
    assert geek.version_que_coincide({"codigo": "SJG6028"}, ficha)[1] == "codigo"
    assert geek.coincide_sistema("GURPS", ficha) and geek.coincide_sistema("Hero System", ficha) is False
    assert geek.puntuar({"sistema": "Hero System"}, ficha, ["Riverworld Book"]) < base
    # Wargame: el suplemento de 2010 de un juego de 1984 no resta; «Mordheim» = «Mordheim: City of the Damned».
    bt = {"id": 2, "tipo": "boardgame", "nombre": "BattleTech", "alternativos": [], "anio": 1984, "editoriales": []}
    assert geek.puntuar({"publicado": 2010}, bt, ["BattleTech"]) >= geek.UMBRAL_AUTO
    mh = {**bt, "nombre": "Mordheim: City of the Damned"}
    assert geek.puntuar({}, mh, ["Mordheim"]) >= geek.UMBRAL_AUTO


def test_anio_de_bgg_solo_para_el_propio_juego():
    from biblioteca_rpg.enriquecer import _efectivos
    g = {"geek": {"elegido": {"id": 1, "tipo": "boardgame", "anio": 1983, "editoriales": ["Games Workshop"]}}}
    supl = _efectivos(g, "Triumph and Treachery.pdf", "Wargames/Games Workshop/Warhammer Fantasy/Supplements/T.pdf")
    assert supl["publicado"] is None and supl["geek_id"] == 1
    reglas = _efectivos(g, "WFB.pdf", "Wargames/Games Workshop/Warhammer Fantasy/Rules/WFB.pdf")
    assert reglas["publicado"] == 1983


def test_no_se_confirma_con_sus_propios_datos():
    tng = {"id": 4, "tipo": "rpgitem", "nombre": "Star Trek: TNG & DS9", "alternativos": [], "anio": 2018,
           "editoriales": ["X"], "autores": [], "sistema": ["JumpChain!"], "serie": [], "versiones": [], "url": "u/4"}
    c = ClienteFalso({"Star Trek DS9": [{"id": 4, "nombre": "Star Trek: TNG & DS9", "anio": 2018}]}, {4: tng})
    doc = {"titulo": "Star Trek DS9 Corebook", "categoria": "Rol", "sistema": "Star Trek", "publicado": 2018,
           "ext_editorial": "X", "origen": '{"publicado": "geek", "editorial": "geek"}'}
    assert geek.emparejar(c, doc)["estado"] == "pendiente"


def test_reglas_aprendidas_de_las_revisiones():
    from biblioteca_rpg import geek as g
    # «D&D» delante del título se quita al buscar y RPGGeek lo nombra de las dos formas.
    assert g._sin_sistema("D&D The Thunder Below", "D&D") == "The Thunder Below"
    assert g.coincide_sistema("D&D", {"sistema": ["5e Game System Product (D&D 5th Edition Compatible)"]})
    assert g.coincide_sistema("D&D", {"sistema": ["Dungeons & Dragons (3.5 Edition)"]})
    assert g.coincide_sistema("D&D", {"sistema": ["Pathfinder"]}) is False
    # El sistema se mira en el sistema/serie de la ficha, no en su nombre.
    assert g.coincide_sistema("Bleeding Edge", {"nombre": "The Bleeding Edge", "sistema": ["Candela Obscura"]}) is False
    # Editoriales parecidas cuentan; «(Web published)» no dice nada.
    assert g.misma_editorial("Sanguine Productions", "Sanguine Games")
    assert not g.misma_editorial("Wizards of the Coast", "Paizo Publishing")
    assert g.coincide_editorial({"editorial": "Wizards of the Coast"}, {"editoriales": ["(Web published)"]}) is None
    # Partes de un nombre: sí «Thin Blue Line: …», no «Behind Enemy Lines».
    assert g.es_parte_del_nombre("Thin Blue Line", "Thin Blue Line: Defenders of the Colonies")
    assert g.es_parte_del_nombre("A is for Assassin", "Strike to the Heart: A is for Assassin")
    assert not g.es_parte_del_nombre("Behind Lines", "Behind Enemy Lines")
    # Etiquetas de sistema de RPGGeek, sí; otros añadidos, no.
    assert g._sin_parentesis("Star Hero (HERO System 5)") == "Star Hero"
    assert g._sin_parentesis("Hillfolk (Kickstarter Preview Edition)") == "Hillfolk (Kickstarter Preview Edition)"
    assert g.RE_ACCESORIO.search("Oriental Adventures Errata") and not g.RE_ACCESORIO.search("Fiend Folio")


def test_lo_confirmado_en_la_carpeta_ayuda():
    from biblioteca_rpg import geek as g
    tsr = {"editoriales": ["Tactical Studies Rules (TSR)"], "sistema": ["Alternity"]}
    perfil = g.perfil_vecinos([(tsr, 2)])  # una aceptada a mano basta
    assert g.coincide_vecinos(perfil, tsr) == 2
    assert g.coincide_vecinos(perfil, {"editoriales": ["Wizards of the Coast"], "sistema": ["Star Wars"]}) == 0
    assert g.perfil_vecinos([(tsr, 1)])["editoriales"] == set()  # un solo automático no basta
    ficha = {"nombre": "Arms & Equipment Guide", "versiones": [], **tsr}
    doc = {"titulo": "Star Drive Arms & Equipment Guide", "sistema": "Star Drive"}
    q = g.variantes(doc["titulo"], doc["sistema"])
    assert g.puntuar({**doc, "vecinos": perfil}, ficha, q) > g.puntuar(doc, ficha, q)
