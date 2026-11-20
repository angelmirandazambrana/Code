import csv

from test_entrada import _pdf

from biblioteca_rpg import reorganizar as reo


def _biblioteca(tmp_path, monkeypatch):
    raiz = tmp_path / "biblio"
    base = raiz / "Wargames/Miniaturas/Games Workshop/Warhammer 40K"
    _pdf(base / "8e (2017)/1) Codex & Index/2) Codexes/Codex Orks.pdf", "Orks")
    _pdf(base / "8e (2017)/5) FAQs/2018 FAQs/Big FAQ 1.pdf", "FAQ")
    _pdf(base / "Old Editions/2nd Edition/Eldar (2ed).pdf", "Eldar")
    (base / "Old Editions/2nd Edition/Eldar (2ed) notas.txt").write_text("notas")
    escaneado = base / "Old Editions/2nd Edition/Grey Knights escaneado"
    escaneado.mkdir()
    (escaneado / "001.jpg").write_bytes(b"jpg")
    _pdf(base / "Black Library/Novels/Necromunda/Survival Instinct.pdf", "Novela")
    (raiz / "_Inbox").mkdir()
    monkeypatch.setenv("BIBLIOTECA_RAIZ", str(raiz))
    monkeypatch.setenv("BIBLIOTECA_DATOS", str(tmp_path / "datos"))
    from biblioteca_rpg import config, db, indexador
    cfg = config.cargar()
    con = db.conectar(cfg.db)
    indexador.sincronizar(con, cfg, cfg.areas)
    return cfg, con, raiz, base


def test_ensayo_ejecutar_y_deshacer(tmp_path, monkeypatch):
    cfg, con, raiz, base = _biblioteca(tmp_path, monkeypatch)
    ids = {r["nombre"]: r["id"] for r in con.execute("SELECT id, nombre FROM docs")}
    tabla = tmp_path / "tabla.csv"
    reo.ensayo(con, "warhammer40k", str(tabla))
    with open(tabla, encoding="utf-8-sig") as f:
        filas = {r[0].rsplit("/", 1)[1]: r[2] for r in list(csv.reader(f, delimiter=";"))[1:]}
    nuevo = "Wargames/Games Workshop/Warhammer 40K"
    assert filas["Codex Orks.pdf"] == f"{nuevo}/8e (Games Workshop, 2017)/Army Books"
    assert filas["Big FAQ 1.pdf"] == f"{nuevo}/8e (Games Workshop, 2017)/Errata & FAQ"
    assert filas["Eldar (2ed).pdf"] == f"{nuevo}/2e (Games Workshop, 1993)/Army Books"
    assert filas["Survival Instinct.pdf"] == "Wargames/Games Workshop/Necromunda/Any Edition/Fiction"

    # Ensayo de la ejecución: no mueve nada.
    reo.ejecutar(con, cfg, "warhammer40k", str(tabla), de_verdad=False)
    assert (base / "8e (2017)/1) Codex & Index/2) Codexes/Codex Orks.pdf").is_file()

    registro = reo.ejecutar(con, cfg, "warhammer40k", str(tabla), de_verdad=True)
    orks = raiz / nuevo / "8e (Games Workshop, 2017)/Army Books/Codex Orks.pdf"
    assert orks.is_file()
    # La ficha es la misma (misma id) y su ruta y datos cambian.
    d = con.execute("SELECT * FROM docs WHERE id=?", (ids["Codex Orks.pdf"],)).fetchone()
    assert d["ruta"] == orks.relative_to(raiz).as_posix() and d["editorial"] == "Games Workshop"
    # Lo que no tiene ficha va con sus libros; la carpeta escaneada, entera.
    assert (raiz / nuevo / "2e (Games Workshop, 1993)/Army Books/Eldar (2ed) notas.txt").is_file()
    assert (raiz / nuevo / "2e (Games Workshop, 1993)/Army Books/Grey Knights escaneado/001.jpg").is_file()
    assert not base.exists()  # el árbol viejo queda vacío y se quita

    reo.deshacer(con, cfg, registro)
    assert (base / "8e (2017)/1) Codex & Index/2) Codexes/Codex Orks.pdf").is_file()
    assert (base / "Old Editions/2nd Edition/Grey Knights escaneado/001.jpg").is_file()
    d = con.execute("SELECT ruta FROM docs WHERE id=?", (ids["Codex Orks.pdf"],)).fetchone()
    assert d["ruta"].endswith("2) Codexes/Codex Orks.pdf")


def test_nunca_sobrescribe(tmp_path, monkeypatch):
    cfg, con, raiz, _ = _biblioteca(tmp_path, monkeypatch)
    ocupado = raiz / "Wargames/Games Workshop/Warhammer 40K/8e (Games Workshop, 2017)/Army Books/Codex Orks.pdf"
    ocupado.parent.mkdir(parents=True)
    ocupado.write_bytes(b"otro")
    tabla = tmp_path / "tabla.csv"
    reo.ensayo(con, "warhammer40k", str(tabla))
    reo.ejecutar(con, cfg, "warhammer40k", str(tabla), de_verdad=True)
    assert ocupado.read_bytes() == b"otro"
    assert ocupado.with_name("Codex Orks (2).pdf").is_file()


def test_oleada_a_reglas_de_rol():
    from biblioteca_rpg.oleadas import proponer_rol as p
    assert p("Shadowrun", "5e (Catalyst, 2013)/Adventures", "x.pdf")[0] == "RPG/Shadowrun/5e (Catalyst, 2013)/Adventures"
    assert p("L5R", "Misc", "L5R - Character Sheet 4.pdf")[0] == "RPG/L5R/Game Aids"      # Misc solo si no hay nada mejor
    assert p("Ptolus", "", "Ptolus Adventure Tiles - Sewers.pdf")[0] == "RPG/Ptolus/Game Aids"
    assert p("Call of Cthulhu", "_Varios/1920s Adventures", "x.pdf")[0] == "RPG/Call of Cthulhu/Adventures"
    assert p("Savage Worlds", "Suzerain", "Savage Worlds - Suzerain - x.pdf")[0] == \
        "RPG/Savage Worlds/Suzerain/_Por clasificar"                                        # se conserva la línea
    assert p("Middle-earth", "MERP/1st Edition", "ICE8002.pdf")[0] == "RPG/Middle-earth/MERP/1st Edition/_Por clasificar"
    assert p("Savage Worlds", "Sundered Skies", "SW - Sundered Skies - Character Sheet.pdf")[0] ==         "RPG/Savage Worlds/Sundered Skies/Game Aids"
    assert p("Star Wars", "WEG d6/Adventures/Tatooine Manhunt", "x.pdf")[0] == \
        "RPG/Star Wars/WEG d6/Adventures/Tatooine Manhunt"                                  # línea del juego
    assert p("Aquelarre (d100) [ES]", "", "Aquelarre - Reglas basicas.pdf")[0] == "RPG/Aquelarre (d100) [ES]/Core Rules"


def test_oleada_b_familias():
    from biblioteca_rpg.oleadas import juego_de_familia as j
    assert j("World of Darkness", ["Classic", "VtM (WoD)", "V20"]) == ("Vampire - The Masquerade", ["V20"])
    assert j("World of Darkness", ["Classic", "_General"]) == ("World of Darkness", ["Classic", "_General"])
    assert j("OSR(ic)", ["S+W", "Adventures"]) == ("Swords & Wizardry", ["Adventures"])
    assert j("OSR(ic)", ["Castles & Crusades; Siege Engine"]) == ("Castles & Crusades", [])
    assert j("Fate", ["2. FATE Core (FATE)"]) == ("Fate Core", [])
    assert j("Anime & Cartoon RPGs", ["1) Related to Specific Anime or Cartoon", "Naruto"]) == ("Naruto", [])
    assert j("Anime & Cartoon RPGs", ["3) Common Tropes & Themes", "Mecha & Kaiju"]) == ("Anime & Cartoon", ["Mecha & Kaiju"])


def test_carpeta_vieja_con_el_nombre_de_una_nueva(tmp_path, monkeypatch):
    """Un libro va a "Hacks/Adventures" y la carpeta vieja "Hacks" (sin libros) se mueve entera:
    el libro no puede acabar dentro de ella."""
    from biblioteca_rpg import config, db, indexador
    raiz = tmp_path / "biblio"
    _pdf(raiz / "RPG/Juego/Other/Hacks/One Shot/OSW Adventure.pdf", "x")
    (raiz / "RPG/Juego/Hacks/Arte").mkdir(parents=True)
    (raiz / "RPG/Juego/Hacks/Arte/a.jpg").write_bytes(b"jpg")
    (raiz / "_Inbox").mkdir()
    monkeypatch.setenv("BIBLIOTECA_RAIZ", str(raiz))
    monkeypatch.setenv("BIBLIOTECA_DATOS", str(tmp_path / "datos"))
    cfg = config.cargar()
    con = db.conectar(cfg.db)
    indexador.sincronizar(con, cfg, cfg.areas)
    tabla = [("RPG/Juego/Other/Hacks/One Shot/OSW Adventure.pdf", "RPG/Juego/Hacks/Adventures/OSW Adventure.pdf")]
    reo.ejecutar_tabla(con, cfg, "prueba", tabla, ["RPG/Juego"], True)
    ruta = con.execute("SELECT ruta FROM docs").fetchone()[0]
    assert (raiz / ruta).is_file() and ruta == "RPG/Juego/Hacks/Adventures/OSW Adventure.pdf"


def test_oleada_c():
    from biblioteca_rpg.oleadas import proponer_rol, ruta_c

    def p(base, carpeta, nombre="x.pdf"):
        juego, debajo = ruta_c(base, carpeta.split("/") if carpeta else [])
        return proponer_rol(juego, "/".join(debajo), nombre)[0]
    ed5 = "5e (Wizards of the Coast, 2014)"
    assert p("D&D", f"Adventures/{ed5}/Adventure League/S9 - Descent") == \
        f"RPG/D&D/{ed5}/Organized Play/Adventure League/S9 - Descent"
    assert p("D&D", f"Rulebooks/{ed5}/Core Books") == f"RPG/D&D/{ed5}/Core Rules"
    assert p("D&D", f"Rulebooks/{ed5}/Unearthed Arcana") == f"RPG/D&D/{ed5}/Supplements/Unearthed Arcana"
    assert p("D&D", "Settings/Living Greyhawk/Modules") == \
        "RPG/D&D/3e-3.5e (Wizards of the Coast, 2000)/Organized Play/Living Greyhawk/Modules"
    assert p("D&D", "Settings/Ravenloft") == "RPG/D&D/Any Edition/Settings/Ravenloft"
    assert p("D&D Third Party", "5e (2014)/Kobold Press", "Tome of Beasts.pdf") == \
        "RPG/D&D 3rd Party/5e (2014)/_Por clasificar"
    assert p("D&D Third Party", "5e (2014)/__ Homebrew/00. Adventures") == \
        "RPG/D&D 3rd Party/5e (2014)/Fan & Homebrew/Adventures"
    assert p("Paizo", "2nd Edition/Society/Season 1 - Year of the Open Road") == \
        "RPG/Pathfinder/2e (Paizo, 2019)/Organized Play/Society/Season 1 - Year of the Open Road"
    assert p("Paizo", "1st Edition/3rd Party/Legendary Games/Adventure Paths") == \
        "RPG/Pathfinder 3rd Party/1e (2009)/Adventures"
    assert p("Necromancer - Frog God Games", "3E/W1, The Crucible of Freya (Lvl 1-2)") == \
        "RPG/D&D 3rd Party/3e-3.5e/Adventures"
    assert p("Necromancer - Frog God Games", "The Lost Lands/Rappan Athuk/2012 - PF1E") == \
        "RPG/Pathfinder 3rd Party/1e (2009)/Adventures/Rappan Athuk"


def test_oleada_d_wargames():
    from biblioteca_rpg.oleadas import juego_war, proponer_rol, tipo_war
    assert juego_war(["Miniaturas", "Games Workshop", "Mordheim", "Rules"]) == ("Games Workshop", "Mordheim", ["Rules"])
    assert juego_war(["Tablero", "Avalon Hill"]) == ("Avalon Hill", None, [])
    assert juego_war(["Miniaturas", "BattleTech", "BattleTech Novels"]) == (None, "BattleTech", ["BattleTech Novels"])
    assert juego_war(["AWG Fantasy", "SAGA"]) == (None, "SAGA", [])
    assert juego_war(["_Referencia", "Historia militar"]) is None
    assert juego_war(["Games Workshop", "Warhammer 40K", "Any Edition"]) is None

    def p(juego, rel, nombre="x.pdf"):
        return proponer_rol(juego, rel, nombre, raiz="Wargames", tipo_de=tipo_war)[0]
    assert p("Catalyst Game Labs/BattleTech", "BattleTech Novels") == "Wargames/Catalyst Game Labs/BattleTech/Fiction"
    assert p("Catalyst Game Labs/BattleTech", "hexpacks & maps") == "Wargames/Catalyst Game Labs/BattleTech/Game Aids"
    assert p("Baroque/Baroque", "Army Lists") == "Wargames/Baroque/Baroque/Army Books"


def test_oleada_d_tipos():
    from biblioteca_rpg.oleadas import limpiar, proponer_rol, tipo_war
    assert limpiar("1940-1943 North Africa") == "1940-1943 North Africa" and limpiar("01) Rules") == "Rules"
    assert tipo_war("Game Aids") == "Game Aids" and tipo_war("2. Age of Sigmar Battletomes") == "Army Books"
    assert proponer_rol("One Page Rules/Grimdark Future", "Game Aids", "x.pdf", raiz="Wargames", tipo_de=tipo_war)[0] \
        == "Wargames/One Page Rules/Grimdark Future/Game Aids"


def test_base_mezclada_deja_lo_que_no_tiene_destino(tmp_path, monkeypatch):
    from biblioteca_rpg import config, db, indexador
    raiz = tmp_path / "biblio"
    _pdf(raiz / "Wargames/Miniaturas/Mordheim/Rules/Mordheim.pdf", "x")
    (raiz / "Wargames/Miniaturas/Sueltos").mkdir(parents=True)
    (raiz / "Wargames/Miniaturas/Sueltos/a.jpg").write_bytes(b"jpg")
    (raiz / "Wargames/Miniaturas/Mordheim/Rules/b.txt").write_text("b")
    (raiz / "_Inbox").mkdir()
    monkeypatch.setenv("BIBLIOTECA_RAIZ", str(raiz))
    monkeypatch.setenv("BIBLIOTECA_DATOS", str(tmp_path / "datos"))
    cfg = config.cargar()
    con = db.conectar(cfg.db)
    indexador.sincronizar(con, cfg, cfg.areas)
    tabla = [("Wargames/Miniaturas/Mordheim/Rules/Mordheim.pdf", "Wargames/Games Workshop/Mordheim/Rules/Mordheim.pdf")]
    reo.ejecutar_tabla(con, cfg, "prueba", tabla, ["Wargames/Miniaturas"], True, mezclada=True)
    assert (raiz / "Wargames/Games Workshop/Mordheim/Rules/b.txt").is_file()   # va con su libro
    assert (raiz / "Wargames/Miniaturas/Sueltos/a.jpg").is_file()              # sin destino: se queda


def test_oleada_e_referencia():
    from biblioteca_rpg.oleadas import destino_referencia as d
    assert d("Wargames/_Referencia/Historia militar/Osprey") == "Wargames/_Reference/Military History/Osprey"
    assert d("Wargames/_Referencia") == "Wargames/_Reference/Military History"
    assert d("Wargames/_Pintura y modelismo/How to painting & modelling/GW") == "Wargames/_Reference/Painting & Modelling/GW"
    assert d("Wargames/Janes/Jane's Defence") == "Wargames/_Reference/Jane's/Jane's Defence"
    assert d("Wargames/Otra") is None


def test_modo_estricto_en_carpetas_mezcladas(tmp_path, monkeypatch):
    from biblioteca_rpg import config, db, indexador
    raiz = tmp_path / "biblio"
    _pdf(raiz / "RPG/Mezcla/Tema/a.pdf", "a")
    _pdf(raiz / "RPG/Mezcla/Tema/b.pdf", "b")
    (raiz / "RPG/Mezcla/Tema/a.txt").write_text("a")
    _pdf(raiz / "RPG/Mezcla/Juego X/x.pdf", "x")
    (raiz / "RPG/Mezcla/Juego X/x.jpg").write_bytes(b"jpg")
    (raiz / "RPG/Mezcla/Otra sin libros").mkdir(parents=True)
    (raiz / "RPG/Mezcla/Otra sin libros/z.jpg").write_bytes(b"jpg")
    (raiz / "_Inbox").mkdir()
    monkeypatch.setenv("BIBLIOTECA_RAIZ", str(raiz))
    monkeypatch.setenv("BIBLIOTECA_DATOS", str(tmp_path / "datos"))
    cfg = config.cargar()
    con = db.conectar(cfg.db)
    indexador.sincronizar(con, cfg, cfg.areas)
    tabla = [("RPG/Mezcla/Tema/a.pdf", "RPG/A/Core Rules/a.pdf"),          # b.pdf se queda
             ("RPG/Mezcla/Juego X/x.pdf", "RPG/X/Core Rules/x.pdf")]
    reo.ejecutar_tabla(con, cfg, "prueba", tabla, ["RPG/Mezcla"], True, mezclada=True, estricta=True)
    assert (raiz / "RPG/X/Core Rules/x.jpg").is_file()                     # toda su carpeta va junta
    assert (raiz / "RPG/Mezcla/Tema/a.txt").is_file()                      # su carpeta no se mueve entera
    assert (raiz / "RPG/Mezcla/Otra sin libros/z.jpg").is_file()           # sin libros: se queda
