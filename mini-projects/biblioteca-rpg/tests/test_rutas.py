import pytest

from biblioteca_rpg.rutas import analizar


@pytest.mark.parametrize(
    "ruta, esperado",
    [
        (
            "RPG/Shadowrun/3e (FASA, 1998)/SR3 Core.pdf",
            dict(categoria="Rol", sistema="Shadowrun", edicion="3e", editorial="FASA", anio=1998),
        ),
        (
            "RPG/Cyberpunk/2e - Cyberpunk 2020 (R. Talsorian, 1990)/CP2020.pdf",
            dict(sistema="Cyberpunk", edicion="2e - Cyberpunk 2020", editorial="R. Talsorian", anio=1990),
        ),
        (
            "RPG/D&D/Adventures/1e - AD&D (TSR, 1977)/B2.pdf",
            dict(sistema="D&D", edicion="1e - AD&D", editorial="TSR", anio=1977, tipo="Aventura"),
        ),
        (
            "RPG/D&D/Rulebooks/Basic - BECMI (TSR, 1977-1991)/Basic.pdf",
            dict(edicion="Basic - BECMI", editorial="TSR", anio=1977, tipo="Reglas"),
        ),
        (
            "RPG/D&D/Settings/Dark Sun/Dark Sun Boxed Set.pdf",
            dict(sistema="D&D", ambientacion="Dark Sun", tipo="Ambientación"),
        ),
        (
            "RPG/D&D Third Party/3e-3.5e/AEG/Toolbox.pdf",
            dict(sistema="D&D", edicion="3e-3.5e", editorial="AEG"),
        ),
        (
            "RPG/D&D Third Party/5e (2014)/_Sin editorial/Algo.pdf",
            dict(sistema="D&D", edicion="5e", anio=2014, editorial=None),
        ),
        (
            "RPG/Paizo/1st Edition/3rd Party/Drop Dead Studios/Wizard's Academy.pdf",
            dict(sistema="Pathfinder", edicion="1st Edition", editorial="Drop Dead Studios"),
        ),
        (
            "RPG/Traveller/08 - Mongoose Traveller (2008)/Core.pdf",
            dict(sistema="Traveller", edicion="Mongoose Traveller", anio=2008),
        ),
        (
            "RPG/DrivethroughRPG/26 Letter Publishing/Thing.pdf",
            dict(sistema=None, editorial="26 Letter Publishing"),
        ),
        (
            "Wargames/Tablero/Avalon Hill/Panzer Leader & Panzer Blitz/Fall Gelb/x.pdf",
            dict(categoria="Wargames", formato="Tablero", editorial="Avalon Hill", sistema="Panzer Leader & Panzer Blitz"),
        ),
        (
            "Wargames/Miniaturas/Games Workshop/Mordheim/Warbands/Black Orc.pdf",
            dict(formato="Miniaturas", editorial="Games Workshop", sistema="Mordheim", tipo="Listas de ejército"),
        ),
        (
            "Wargames/Miniaturas/BattleTech/TRO 3025.pdf",
            dict(formato="Miniaturas", sistema="BattleTech", editorial=None),
        ),
        (
            "Magazines/Dragon Magazine/Dragon 100.pdf",
            dict(categoria="Revistas", sistema="Dragon Magazine", tipo="Revista"),
        ),
        (
            "Magazines/Backstab [FR]/Backstab 01.pdf",
            dict(idioma="Francés"),
        ),
        (
            "RPG/_GM Books/Bestiaries/Hybrid_Classes_Vol_I.pdf",
            dict(sistema="GM Books", tipo="Bestiario"),
        ),
    ],
)
def test_analizar(ruta, esperado):
    d = analizar(ruta).dict()
    for clave, valor in esperado.items():
        assert d[clave] == valor, (clave, d)


def test_titulo_y_carpeta():
    d = analizar("RPG\\Fate\\Fate_Core__System.pdf")
    assert d.titulo == "Fate Core System"
    assert d.carpeta == "RPG/Fate"
    assert d.nombre == "Fate_Core__System.pdf"
