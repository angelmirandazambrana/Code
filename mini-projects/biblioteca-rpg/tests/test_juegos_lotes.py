from biblioteca_rpg.juegos_lotes import destino

JUEGOS = {
    "Shadowrun": {"base": "RPG/Shadowrun", "ediciones": ["5e (Catalyst, 2013)", "6e (Catalyst, 2019)"]},
    "Cairn": {"base": "RPG/Cairn", "ediciones": []},
    "Games Workshop/Mordheim": {"base": "Wargames/Games Workshop/Mordheim", "ediciones": []},
}


def r(**k):
    base = {"tipo_juego": "rol", "juego": "Cairn", "en_lista": True, "edicion": "", "editorial": "",
            "tipo": "Adventures", "seguridad": "alta"}
    return {**base, **k}


def test_destino_juego_de_la_lista():
    assert destino(None, r(), JUEGOS, {})[0] == "RPG/Cairn/Adventures"
    assert destino(None, r(juego="Shadowrun", edicion="5e (catalyst, 2013)"), JUEGOS, {})[0] == \
        "RPG/Shadowrun/5e (Catalyst, 2013)/Adventures"
    assert destino(None, r(juego="Shadowrun"), JUEGOS, {})[0] == "RPG/Shadowrun/_Por clasificar"
    assert destino(None, r(juego="Games Workshop/Mordheim", tipo="Core Rules"), JUEGOS, {})[0] == \
        "Wargames/Games Workshop/Mordheim/Rules"
    assert destino(None, r(juego="cairn", en_lista=False), JUEGOS, {})[0] == "RPG/Cairn/Adventures"


def test_destino_dudoso_o_de_ningun_juego_se_queda():
    assert destino(None, r(seguridad="baja"), JUEGOS, {})[0] is None
    assert destino(None, r(tipo_juego="ninguno"), JUEGOS, {})[0] is None
    assert destino(None, r(juego="Inventado", en_lista=False), JUEGOS, {})[0] is None  # sin comprobar


def test_destino_juego_nuevo_comprobado():
    v = {"Scrawl": {"existe": True, "nombre_oficial": "Scrawl: Ultimate Edition", "editorial": "Slloyd14",
                    "tipo_juego": "rol"},
         "Falso": {"existe": False, "nombre_oficial": "", "editorial": "", "tipo_juego": "rol"},
         "Tanques": {"existe": True, "nombre_oficial": "Tanques", "editorial": "", "tipo_juego": "wargame"}}
    assert destino(None, r(juego="Scrawl", en_lista=False, tipo="Core Rules"), JUEGOS, v)[0] == \
        "RPG/Scrawl - Ultimate Edition/Core Rules"
    assert destino(None, r(juego="Falso", en_lista=False), JUEGOS, v)[0] is None
    assert destino(None, r(juego="Tanques", en_lista=False, tipo="Core Rules"), JUEGOS, v)[0] == \
        "Wargames/Other Publishers/Tanques/Rules"


def test_mismo_juego_con_otro_nombre():
    from biblioteca_rpg.juegos_lotes import _nuevos
    juegos = {**JUEGOS, "Basic Fantasy": {"base": "RPG/Basic Fantasy", "ediciones": []}}
    assert destino(None, r(juego="Basic Fantasy RPG", en_lista=False), juegos, {})[0] == "RPG/Basic Fantasy/Adventures"
    v = {"BFRPG": {"existe": True, "nombre_oficial": "Basic Fantasy Role-Playing Game", "editorial": "", "tipo_juego": "rol"}}
    assert destino(None, r(juego="BFRPG", en_lista=False), juegos, v)[0] == "RPG/Basic Fantasy/Adventures"
    resp = {"1": r(juego="Basic Fantasy RPG", en_lista=False), "2": r(juego="", en_lista=False),
            "3": r(juego="Nuevo", en_lista=False)}
    assert list(_nuevos(resp, juegos)) == ["Nuevo"]


def test_alias_de_dnd():
    juegos = {**JUEGOS, "D&D": {"base": "RPG/D&D", "ediciones": ["5e (Wizards of the Coast, 2014)"]}}
    assert destino(None, r(juego="Dungeons & Dragons", edicion="5e (Wizards of the Coast, 2014)"), juegos, {})[0] == \
        "RPG/D&D/5e (Wizards of the Coast, 2014)/Adventures"
