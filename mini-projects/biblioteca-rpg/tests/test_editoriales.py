from biblioteca_rpg import editoriales as ed


def test_limpiar_y_clave():
    assert ed.limpiar("d20 4e Goodman Games") == "Goodman Games"
    assert ed.limpiar("Columbia Games Inc_") == "Columbia Games"
    assert ed.limpiar("Glory Games, Inc_") == "Glory Games"
    assert ed.limpiar("Rogue Genius Games (RGG)") == "Rogue Genius Games"
    assert ed.clave("Mongoose") == ed.clave("Mongoose Publishing")
    assert ed.clave("E.N. Publishing") == ed.clave("EN Publishing")
    assert ed.clave("One Page Rules") == ed.clave("onepagerules")
    assert ed.clave("Devil's Workshop") != ed.clave("Games Workshop")
    assert ed.clave("Avalon Hill") != ed.clave("Avalon Games")


def test_agrupar():
    alias = ed.agrupar({
        "Mongoose Publishing": 684, "Mongoose": 101, "d20 4e Mongoose Publishing": 2,
        "Raging Swan Press": 396, "Raging Swan": 2,
        "Green Ronin": 158, "Green Ronin Publishing": 154,       # nombre de la tabla de alias
        "Cubicle 7 Entertainment Ltd_": 46, "Cubicle 7": 111,
        "Petersen games": 7, "Petersen Games": 1,
        "LPJ Design": 15, "Louis Porter Jr. Design": 264,
        "Devil's Workshop": 169, "Games Workshop": 3093,
    })
    assert alias["Mongoose"] == alias["d20 4e Mongoose Publishing"] == "Mongoose Publishing"
    assert alias["Raging Swan"] == "Raging Swan Press"
    assert alias["Green Ronin Publishing"] == "Green Ronin"
    assert alias["Cubicle 7 Entertainment Ltd_"] == "Cubicle 7"
    # Las que solo cambian en mayúsculas suman juntas y gana la bien escrita.
    assert alias["Petersen games"] == "Petersen Games" and "Petersen Games" not in alias
    assert alias["LPJ Design"] == "Louis Porter Jr. Design"
    assert "Devil's Workshop" not in alias and "Games Workshop" not in alias
    assert "Mongoose Publishing" not in alias


def test_recalcular_y_disparadores(tmp_path, monkeypatch):
    raiz = tmp_path / "biblio"
    for rel in [
        "RPG/Traveller/2e (Mongoose Publishing, 2016)/Core.pdf",
        "RPG/Traveller/2e (Mongoose Publishing, 2016)/High Guard.pdf",
        "RPG/Paranoia/2e (Mongoose, 2004)/Paranoia XP.pdf",
    ]:
        p = raiz / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"%PDF-1.4")
    monkeypatch.setenv("BIBLIOTECA_RAIZ", str(raiz))
    monkeypatch.setenv("BIBLIOTECA_DATOS", str(tmp_path / "datos"))
    from biblioteca_rpg import config, consultas, db, indexador
    cfg = config.cargar()
    con = db.conectar(cfg.db)
    indexador.sincronizar(con, cfg, cfg.areas)  # recalcula al acabar
    unif = dict(con.execute("SELECT titulo, editorial_unif FROM docs").fetchall())
    assert unif["Paranoia XP"] == "Mongoose Publishing"
    assert dict(consultas.facetas(con, {}, None, None)["editorial"]) == {"Mongoose Publishing": 3}
    # Un cambio de editorial (p. ej. del PDF) se unifica solo, con los alias que ya hay.
    with con:
        con.execute("UPDATE docs SET editorial=NULL, ext_editorial='Mongoose' WHERE titulo='Core'")
    assert con.execute("SELECT editorial_unif FROM docs WHERE titulo='Core'").fetchone()[0] == "Mongoose Publishing"
    # Y la exclusión funciona con el nombre unificado.
    r = consultas.buscar(con, {"no_editorial": ["Mongoose Publishing"]}, None, None, "titulo", 1)
    assert r["total"] == 0
