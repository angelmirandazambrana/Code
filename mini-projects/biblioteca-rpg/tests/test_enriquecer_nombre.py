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
