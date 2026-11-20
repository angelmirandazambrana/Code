import json

from test_entrada import _estado, _pdf, entorno  # noqa: F401 (fixture)


def test_claude_decide_los_dudosos(entorno, monkeypatch):  # noqa: F811
    from biblioteca_rpg import llm
    cfg, con, raiz, entrada = entorno
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-prueba")
    monkeypatch.setenv("LLM_MAX_DOLARES_MES", "1")
    llamadas = []

    def falsa(cliente, modelo, contenido):
        llamadas.append(contenido)
        texto = contenido[-1]["text"]
        if "Rarisimo" in texto:  # inventa una carpeta: no vale
            return {"carpeta": "RPG/Inventada", "seguridad": "alta", "juego": "?", "tipo": "?", "motivo": "?"}, \
                {"entrada": 1000, "salida": 100}
        return {"carpeta": "RPG/Traveller/Classic", "seguridad": "alta", "juego": "Traveller", "tipo": "Básico",
                "motivo": "Portada de Traveller"}, {"entrada": 1000, "salida": 100}
    monkeypatch.setattr(llm, "clasificar", falsa)
    monkeypatch.setattr(llm, "_CLIENTE", [object()])
    falsa_p = {"propuesta": "RPG/Traveller/Classic", "confianza": 0.4, "apoyos": 1,
               "alternativas": [["RPG/Traveller/Classic", 40], ["RPG/Pathfinder/1e/Adventure Path", 30]]}
    monkeypatch.setattr(entrada, "proponer", lambda *a, **k: dict(falsa_p))
    monkeypatch.setattr(entrada, "con_segunda_opinion", lambda p, *a: p)
    _pdf(raiz / "_Inbox/Traveller Book 2.pdf", "Traveller Book 2")
    _pdf(raiz / "_Inbox/Rarisimo.pdf", "Nada")
    entrada.pasada(con, cfg)

    t = _estado(con, "Traveller Book 2.pdf")
    assert t["estado"] == "movido" and t["ruta_actual"] == "RPG/Traveller/Classic/Traveller Book 2.pdf"
    r = _estado(con, "Rarisimo.pdf")
    assert r["estado"] == "pendiente" and json.loads(r["llm"])["valida"] is False
    assert len(llamadas) == 2
    gasto = con.execute("SELECT SUM(dolares) FROM llm_uso").fetchone()[0]
    assert 0 < gasto < 0.1
    # Sin presupuesto, no se llama.
    monkeypatch.setenv("LLM_MAX_DOLARES_MES", "0")
    assert llm.segunda_lectura(con, cfg, 0, "x.pdf", None, None, falsa_p) is None


def test_destinos_aceptables(entorno):  # noqa: F811
    from biblioteca_rpg import llm
    con = entorno[1]
    cands = ["RPG/Shadowrun"]
    assert llm.destino_aceptable(con, "RPG/Shadowrun", cands)
    assert llm.destino_aceptable(con, "RPG/Shadowrun/5e (Catalyst, 2013)", cands)  # subcarpeta que existe
    assert not llm.destino_aceptable(con, "RPG/Shadowrun/7e", cands)               # no existe
    assert llm.destino_aceptable(con, "RPG/Shadowrun/_Por clasificar", cands)
    assert not llm.destino_aceptable(con, "RPG/Otra/_Por clasificar", cands)
