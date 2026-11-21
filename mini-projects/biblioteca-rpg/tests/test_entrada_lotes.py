import json

from biblioteca_rpg import entrada_lotes
from biblioteca_rpg.entrada import con_claude
from biblioteca_rpg.entrada_lotes import decidir
from biblioteca_rpg.web import agrupar_pendientes

JUEGOS = {
    "Shadowrun": {"base": "RPG/Shadowrun", "ediciones": ["5e (Catalyst, 2013)", "6e (Catalyst, 2019)"]},
    "Cairn": {"base": "RPG/Cairn", "ediciones": []},
}


def r(**k):
    base = {"tipo_juego": "rol", "juego": "Cairn", "en_lista": True, "edicion": "", "editorial": "",
            "tipo": "Adventures", "seguridad": "alta"}
    return {**base, **k}


def test_decidir_alta_media_y_baja():
    assert decidir(r(), [], JUEGOS, {})[:2] == ("RPG/Cairn/Adventures", True)
    # Media: solo si el clasificador también apuntaba a ese juego.
    assert decidir(r(seguridad="media"), [["RPG/Otra/Misc", 30]], JUEGOS, {})[:2] == ("RPG/Cairn/Adventures", False)
    assert decidir(r(seguridad="media"), [["RPG/Cairn/Settings", 25]], JUEGOS, {})[:2] == ("RPG/Cairn/Adventures", True)
    # Baja: la carpeta sirve de primera opción, pero no se mueve.
    assert decidir(r(seguridad="baja"), [], JUEGOS, {})[:2] == ("RPG/Cairn/Adventures", False)
    assert decidir(r(tipo_juego="ninguno"), [], JUEGOS, {})[:2] == (None, False)
    # Edición no clara: al _Por clasificar del juego.
    assert decidir(r(juego="Shadowrun"), [], JUEGOS, {})[:2] == ("RPG/Shadowrun/_Por clasificar", True)


def test_decidir_juego_nuevo():
    v = {"Scrawl": {"existe": True, "nombre_oficial": "Scrawl", "editorial": "", "tipo_juego": "rol"}}
    assert decidir(r(juego="Scrawl", en_lista=False, tipo="Core Rules"), [], JUEGOS, v)[:2] == \
        ("RPG/Scrawl/Core Rules", True)
    assert decidir(r(juego="Otro", en_lista=False), [], JUEGOS, v)[:2] == (None, False)  # sin comprobar
    assert decidir(r(juego="Scrawl", en_lista=False, seguridad="media"), [], JUEGOS, v)[1] is False


def test_nuevos_a_comprobar():
    filas = [{"llm": json.dumps({"respuesta": x})} for x in (
        r(juego="Nuevo", en_lista=False), r(juego="Nuevo", en_lista=False), r(juego="cairn", en_lista=False),
        r(juego="Dudoso", en_lista=False, seguridad="media"), r())]
    assert entrada_lotes._nuevos_de(filas, JUEGOS) == {"Nuevo": {"tipo_juego": "rol", "editorial": "", "n": 2}}


def test_alternativas_y_grupos_con_claude():
    llm = json.dumps({"carpeta": "RPG/Cairn/Adventures", "valida": True})
    assert con_claude([["RPG/X", 40], ["RPG/Cairn/Adventures", 10]], llm) == \
        [["RPG/Cairn/Adventures", None], ["RPG/X", 40]]
    assert con_claude([["RPG/X", 40]], None) == [["RPG/X", 40]]
    pend = [{"id": i, "estado": "pendiente", "nombre": f"Raro {i}", "propuesta": "RPG/Five parsecs/Misc",
             "confianza": 0.2, "alternativas": [], "llm": {"carpeta": "RPG/Cairn/Adventures", "valida": True}}
            for i in range(3)]
    grupos = agrupar_pendientes(pend)
    assert [g["carpeta"] for g in grupos] == ["RPG/Cairn/Adventures"] and len(grupos[0]["entradas"]) == 3


def test_colocar(tmp_path, monkeypatch):
    monkeypatch.setenv("BIBLIOTECA_RAIZ", str(tmp_path / "biblio"))
    monkeypatch.setenv("BIBLIOTECA_DATOS", str(tmp_path / "datos"))
    monkeypatch.setenv("CLASIFICADOR_AUTO", "0.6")
    (tmp_path / "biblio").mkdir()
    from biblioteca_rpg import config, db
    cfg = config.cargar()
    con = db.conectar(cfg.db)
    monkeypatch.setattr(entrada_lotes, "catalogo_juegos", lambda con: ("", JUEGOS))

    def fila(i, resp, alternativas):
        llm = {"origen": "lote", "respuesta": resp, "juego": resp["juego"], "tipo": resp["tipo"],
               "seguridad": resp["seguridad"], "carpeta": "", "valida": False, "motivo": ""}
        con.execute("INSERT INTO entrada (id, nombre, ruta_actual, estado, alternativas, llm) VALUES (?, ?, ?, "
                    "'pendiente', ?, ?)", (i, f"f{i}.pdf", f"_Inbox/f{i}.pdf", json.dumps(alternativas),
                                           json.dumps(llm, ensure_ascii=False)))

    with con:
        fila(1, r(), [["RPG/X/Misc", 30]])
        fila(2, r(seguridad="baja"), [["RPG/X/Misc", 30]])
    entrada_lotes.colocar(con, cfg)
    a, b = (dict(x) for x in con.execute("SELECT * FROM entrada ORDER BY id"))
    assert a["estado"] == "aprobado" and a["destino"] == "RPG/Cairn/Adventures" and a["auto"] == 1
    assert b["estado"] == "pendiente" and b["destino"] is None
    assert json.loads(b["alternativas"])[0] == ["RPG/Cairn/Adventures", None]
    assert json.loads(b["llm"])["valida"] is True


def test_no_se_mete_solo_dentro_de_otro_producto(tmp_path, monkeypatch):
    monkeypatch.setenv("BIBLIOTECA_RAIZ", str(tmp_path / "biblio"))
    monkeypatch.setenv("BIBLIOTECA_DATOS", str(tmp_path / "datos"))
    (tmp_path / "biblio").mkdir()
    from biblioteca_rpg import config, db
    from biblioteca_rpg.entrada import producto_ajeno
    con = db.conectar(config.cargar().db)
    with con:
        con.execute("INSERT INTO entrada (nombre, ruta_actual, es_carpeta, estado) VALUES "
                    "('TOR_Starter_Set', 'RPG/The One Ring/Core Rules/TOR_Starter_Set', 1, 'movido')")
        # Una carpeta que se fusionó con la de tipo no la convierte en «producto».
        con.execute("INSERT INTO entrada (nombre, ruta_actual, es_carpeta, estado) VALUES "
                    "('arcane unleashed', 'RPG/D&D/5e/Core Rules', 1, 'movido')")
    assert not producto_ajeno(con, "RPG/D&D/5e/Core Rules")
    assert producto_ajeno(con, "RPG/The One Ring/Core Rules/TOR_Starter_Set")
    assert producto_ajeno(con, "RPG/The One Ring/Core Rules/TOR_Starter_Set/Maps")
    assert not producto_ajeno(con, "RPG/The One Ring/Core Rules")
    assert not producto_ajeno(con, "RPG/The One Ring/Core Rules/TOR_Starter_Set 2")


def test_los_ebooks_son_novelas():
    from biblioteca_rpg.entrada import destino_novela
    from biblioteca_rpg.entrada_lotes import NOVELAS_SUELTAS, como_novela
    assert destino_novela("Gaunt's Ghosts - First and Only.epub", "Warhammer 40k libros") == \
        "Wargames/Games Workshop/Warhammer 40K/Any Edition/Fiction"
    assert destino_novela("R.A. Salvatore - Drizzt 01.mobi", "") == "RPG/D&D/Any Edition/Fiction"
    assert destino_novela("Drizzt.pdf", "") is None
    assert como_novela(r(tipo="Adventures"), [], JUEGOS, {})[:2] == ("RPG/Cairn/Fiction", True)
    # Edición no clara: una novela no la necesita.
    assert como_novela(r(juego="Shadowrun"), [], JUEGOS, {})[:2] == ("RPG/Shadowrun/Any Edition/Fiction", True)
    assert como_novela(r(tipo_juego="ninguno"), [], JUEGOS, {})[:2] == (NOVELAS_SUELTAS, True)
    assert como_novela(r(juego="Raro", en_lista=False), [], JUEGOS, {})[:2] == (NOVELAS_SUELTAS, False)


def test_el_canal_de_dnd_no_hace_dnd_cualquier_novela():
    from biblioteca_rpg.entrada import destino_novela
    assert destino_novela("MtG Tarkir Dragonstorm.epub", "D&D Book Share") is None
    assert destino_novela("FR-Shadowbane 02-Shadowbane (Retail).epub", "D&D Book Share") == \
        "RPG/D&D/Any Edition/Fiction"
