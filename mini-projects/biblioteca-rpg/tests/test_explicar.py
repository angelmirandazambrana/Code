import json
from types import SimpleNamespace

from biblioteca_rpg import explicar

CARPETAS = [
    ("RPG", 100), ("RPG/World of Darkness", 50), ("RPG/World of Darkness/Vampire - The Masquerade", 30),
    ("RPG/World of Darkness/Vampire - The Masquerade/Classic (White Wolf, 1991)", 20),
    ("RPG/World of Darkness/Vampire - The Masquerade/Classic (White Wolf, 1991)/Character Sheets", 4),
    ("RPG/World of Darkness/Vampire - The Masquerade/Classic (White Wolf, 1991)/Fan & Homebrew", 3),
    ("RPG/Traveller", 40), ("RPG/Traveller/Misc", 2), ("RPG/D&D/_Por clasificar", 9),
]


def test_candidatas_trae_el_arbol_del_juego():
    e = {"nombre": "Fiend sheet.pdf", "alternativas": [["RPG/Traveller/Misc", 8]], "llm": None}
    c = [x for x, _ in explicar.carpetas_candidatas(CARPETAS, "ficha fan-made de vampire classic", e)]
    assert "RPG/World of Darkness/Vampire - The Masquerade/Classic (White Wolf, 1991)/Character Sheets" in c
    assert "RPG/World of Darkness/Vampire - The Masquerade/Classic (White Wolf, 1991)/Fan & Homebrew" in c
    assert "RPG/Traveller/Misc" in c          # la alternativa del clasificador, por si acaso
    assert not any("/_" in x for x in c)      # nada de _Por clasificar
    assert explicar.raiz_juego("Wargames/GW/Necromunda/x/y") == "Wargames/GW/Necromunda"


class _Cliente:
    def __init__(self, respuesta):
        self.messages = SimpleNamespace(create=lambda **kw: SimpleNamespace(
            stop_reason="end_turn", content=[SimpleNamespace(type="text", text=json.dumps(respuesta))],
            usage=SimpleNamespace(input_tokens=1000, output_tokens=50, cache_read_input_tokens=0,
                                  cache_creation_input_tokens=0)))


def test_proponer_y_confirmar(tmp_path, monkeypatch):
    from biblioteca_rpg import config, db
    monkeypatch.setenv("BIBLIOTECA_RAIZ", str(tmp_path / "b"))
    monkeypatch.setenv("BIBLIOTECA_DATOS", str(tmp_path / "d"))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")
    (tmp_path / "b" / "_Inbox").mkdir(parents=True)
    cfg = config.cargar()
    con = db.conectar(cfg.db)
    e = {"id": 7, "nombre": "Fiend sheet.pdf", "ruta_actual": "_Inbox/Fiend sheet.pdf", "alternativas": [], "llm": None}
    nueva = "RPG/World of Darkness/Vampire - The Masquerade/Classic (White Wolf, 1991)/Fan & Homebrew/Character Sheets"
    r = explicar.proponer(con, cfg, e, "ficha fan-made de vampire classic", CARPETAS,
                          cliente=_Cliente({"carpeta": nueva, "nueva": True, "motivo": "Fan-made.", "juego": "VtM",
                                            "tipo": "Game Aids"}))
    assert r["carpeta"] == nueva and not r["existe"] and r["valida"]
    assert explicar.ejemplos(con) == ""  # aún no se ha confirmado
    explicar.confirmar(con, r["id"], 7, nueva)
    assert "«Fiend sheet.pdf» — «ficha fan-made de vampire classic» → " + nueva in explicar.ejemplos(con)
    assert con.execute("SELECT COUNT(*) FROM llm_uso WHERE entrada_id=7").fetchone()[0] == 1
    assert explicar.proponer(con, cfg, e, "  ", CARPETAS)["error"]


def test_juegos_nombrados_prefiere_palabras_raras():
    carpetas = ["RPG/Vampire - The Masquerade/Fan & Homebrew", "RPG/World of Darkness/Classic/Game Aids",
                "RPG/Four Against Darkness/Adventures", "RPG/Heroes Against Darkness/Core Rules",
                "RPG/Tour of Darkness/Misc", "RPG/Traveller/Misc"]
    r = explicar.juegos_nombrados(carpetas, {"vampire", "classic", "world", "darkness"})
    assert set(r[:2]) == {"RPG/Vampire - The Masquerade", "RPG/World of Darkness"} and "RPG/Traveller" not in r
