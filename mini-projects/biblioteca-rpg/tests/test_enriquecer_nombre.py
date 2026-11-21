from biblioteca_rpg.enriquecer import _efectivos, anio_del_nombre


def test_anio_del_nombre():
    assert anio_del_nombre("Pandemic - Contagion [2016].pdf") == 2016
    assert anio_del_nombre("Paper Games Collection [3M 1974].pdf") == 1974
    assert anio_del_nombre("White Dwarf #392 UK (2012 08) 124pag Cropped.pdf") == 2012
    assert anio_del_nombre("Polyhedron 139 - December 1999.pdf") == 1999
    assert anio_del_nombre("Da Curated Archive 2022-07-27.pdf") == 2022
    assert anio_del_nombre("Pyramid 2003.pdf", "Magazines/Pyramid") == 2003
    # Años que son parte del título, batallas y cifras que no son años.
    assert anio_del_nombre("Cyberpunk 2020 - Net - Vehicles.pdf", "RPG/Cyberpunk") is None
    assert anio_del_nombre("Twilight 2000 - Tracked Light Combat Vehicles.pdf") is None
    assert anio_del_nombre("CAM 110 - Peleliu 1944.pdf", "Wargames/Osprey") is None
    assert anio_del_nombre("Falklands 1982 - Scenarios.pdf", "Wargames/X") is None
    assert anio_del_nombre("Falklands 1982 - Scenarios [2015].pdf", "Wargames/X") == 2015
    assert anio_del_nombre("Warhammer 40000 Codex.pdf") is None
    assert anio_del_nombre("1990's Handbook.pdf") is None
    assert anio_del_nombre("Libro 2099.pdf", anio_max=2026) is None


def test_el_del_pdf_manda_sobre_el_del_nombre():
    assert _efectivos({"pdf": {"publicado": 2010}}, "Algo [2016].pdf")["publicado"] == 2010
    v = _efectivos({}, "Algo [2016].pdf")
    assert v["publicado"] == 2016 and "nombre" in v["origen"]


def test_codigos_y_usuarios_no_son_anios():
    assert anio_del_nombre("Twilight 2000 - GDW2017 East Europe Sourcebook.pdf") is None
    assert anio_del_nombre("GDW Fact Book Gulf War (By Fox2000).pdf") is None
    assert anio_del_nombre("Bloody_Awful_(MACE_2013)_WhiteBG.pdf") == 2013
    assert anio_del_nombre("Pacesetter 2005 - Chill 1st ed - Things.pdf") is None
    assert anio_del_nombre("MoveCards_PnP_Singles_August2016.pdf") == 2016


def test_wotc_solo_en_sus_lineas():
    from biblioteca_rpg.texto_pdf import diccionario_editoriales, extraer
    dicc = diccionario_editoriales(["Wizards of the Coast", "Kobold Press"])
    pie = "©2016 Wizards of the Coast LLC, PO Box 707, Renton, WA 98057-0707, USA.\n"
    ogl = "System Reference Document 5.1 Copyright 2016, Wizards of the Coast, Inc.\nCopyright 2018 Kobold Press\n"
    assert extraer(pie, "x.pdf", dicc, ruta="RPG/D&D 3rd Party/5e (2014)/Adventures/x.pdf") == {}
    assert extraer(pie, "x.pdf", dicc, ruta="RPG/D&D/5e (Wizards of the Coast, 2014)/Core Rules/x.pdf") == \
        {"publicado": 2016, "editorial": "Wizards of the Coast"}
    assert extraer(ogl, "x.pdf", dicc, ruta="RPG/D&D 3rd Party/5e (2014)/x.pdf") == \
        {"publicado": 2018, "editorial": "Kobold Press"}
    assert _efectivos({"pdf": {"editorial": "Wizards of the Coast"}}, "x.pdf",
                      "RPG/D&D 3rd Party/5e (2014)/x.pdf")["ext_editorial"] is None


def test_el_anio_de_la_ogl_no_es_el_del_libro():
    from biblioteca_rpg.texto_pdf import diccionario_editoriales, extraer
    dicc = diccionario_editoriales(["Kobold Press"])
    ogl = "Open Game License v 1.0a Copyright 2000,\nWizards of the Coast, LLC.\n"
    assert extraer(ogl, "x.pdf", dicc, ruta="RPG/D&D 3rd Party/5e (2014)/x.pdf") == {}


def test_wotc_partido_entre_lineas():
    from biblioteca_rpg.texto_pdf import diccionario_editoriales, extraer
    dicc = diccionario_editoriales(["Kobold Press"])
    for texto in ("the Coast, Inc. and is Copyright 2000 Wizards of\n", "Copyright 2000, Wiz-\n"):
        assert extraer(texto, "x.pdf", dicc, ruta="RPG/D&D 3rd Party/5e (2014)/x.pdf") == {}
