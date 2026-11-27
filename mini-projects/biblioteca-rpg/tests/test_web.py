import importlib

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def cliente(tmp_path, monkeypatch):
    raiz = tmp_path / "biblio"
    for rel in [
        "RPG/Shadowrun/3e (FASA, 1998)/SR3 Core.pdf",
        "RPG/Shadowrun/5e (Catalyst, 2013)/SR5 Core.pdf",
        "Magazines/Dragon Magazine/Dragon 100.pdf",
    ]:
        p = raiz / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"%PDF-1.4 falso")
    monkeypatch.setenv("BIBLIOTECA_RAIZ", str(raiz))
    monkeypatch.setenv("BIBLIOTECA_DATOS", str(tmp_path / "datos"))

    from biblioteca_rpg import config, db, indexador
    cfg = config.cargar()
    con = db.conectar(cfg.db)
    indexador.sincronizar(con, cfg, cfg.areas)
    con.close()

    from biblioteca_rpg import web
    importlib.reload(web)
    return TestClient(web.app)


def test_portada_lista_y_facetas(cliente):
    r = cliente.get("/")
    assert r.status_code == 200
    assert "SR3 Core" in r.text and "Dragon 100" in r.text
    assert "Shadowrun" in r.text


def test_subcarpetas(cliente):
    r = cliente.get("/api/subcarpetas", params={"ruta": ""}).json()
    assert [c["carpeta"] for c in r["carpetas"]] == ["Magazines", "RPG"]
    r = cliente.get("/api/subcarpetas", params={"ruta": "rpg/shadowrun/"}).json()
    assert r["padre"] == "RPG/Shadowrun" and r["existe_padre"]
    assert [(c["carpeta"], c["n"], c["sub"]) for c in r["carpetas"]] == [
        ("RPG/Shadowrun/3e (FASA, 1998)", 1, 0), ("RPG/Shadowrun/5e (Catalyst, 2013)", 1, 0)]
    r = cliente.get("/api/subcarpetas", params={"ruta": "RPG/sha"}).json()
    assert [c["carpeta"] for c in r["carpetas"]] == ["RPG/Shadowrun"] and r["exacta"] is None
    r = cliente.get("/api/subcarpetas", params={"ruta": "RPG/Shadowrun/catal"}).json()
    assert [c["carpeta"] for c in r["carpetas"]] == ["RPG/Shadowrun/5e (Catalyst, 2013)"]  # también en medio
    r = cliente.get("/api/subcarpetas", params={"ruta": "rpg/Nueva/Algo"}).json()
    assert r["padre"] == "rpg/Nueva" and not r["existe_padre"] and r["carpetas"] == []


def test_mover_sin_explicacion(cliente):
    # Desde «Revisar con el PDF» sin pedir propuesta, explicacion_id llega vacío: no es un error de formulario.
    r = cliente.post("/entrada/999", data={"accion": "mover", "destino": "RPG/X", "explicacion_id": "",
                                           "volver": "/entrada/revisar"})
    assert r.status_code == 404  # no existe esa entrada (antes: 422 por el campo vacío)


def test_filtro_y_busqueda(cliente):
    r = cliente.get("/", params={"editorial": "FASA"})
    assert "SR3 Core" in r.text and "SR5 Core" not in r.text
    r = cliente.get("/", params={"q": "drag"})
    assert "Dragon 100" in r.text and "SR3 Core" not in r.text
    r = cliente.get("/", params={"carpeta": "RPG/Shadowrun"})
    assert "SR5 Core" in r.text and "Dragon 100" not in r.text


def test_ficha_pdf_y_portada_vacia(cliente):
    r = cliente.get("/", params={"q": "SR3"})
    import re
    doc_id = re.search(r'href="/libro/(\d+)"', r.text).group(1)
    assert cliente.get(f"/libro/{doc_id}").status_code == 200
    pdf = cliente.get(f"/pdf/{doc_id}/x.pdf")
    assert pdf.status_code == 200 and pdf.headers["content-type"] == "application/pdf"
    assert cliente.get(f"/portada/{doc_id}.jpg").headers["content-type"].startswith("image/svg")
    assert cliente.get("/libro/999999").status_code == 404


def test_excluir_valor_de_faceta(cliente):
    # "Todo menos FASA": quedan el de Catalyst y el que no tiene editorial.
    r = cliente.get("/", params={"no_editorial": "FASA"})
    assert "SR3 Core" not in r.text and "SR5 Core" in r.text and "Dragon 100" in r.text
    assert "todo menos FASA" in r.text
    # El valor excluido sigue en la lista lateral (para poder deshacerlo).
    assert 'class="excluido"' in r.text
    # Excluir también "(sin indicar)".
    r = cliente.get("/", params=[("no_editorial", "FASA"), ("no_editorial", "(sin indicar)")])
    assert "SR5 Core" in r.text and "Dragon 100" not in r.text and "SR3 Core" not in r.text


def test_incluir_y_excluir_se_reemplazan():
    from biblioteca_rpg.web import _url
    assert _url({"editorial": ["FASA"]}, alternar=("no_editorial", "FASA")) == "/?no_editorial=FASA"
    assert _url({"no_editorial": ["FASA"]}, alternar=("editorial", "FASA")) == "/?editorial=FASA"
    assert _url({"no_editorial": ["FASA"]}, alternar=("no_editorial", "FASA")) == "/"


def test_sugerencias(cliente):
    r = cliente.get("/api/sugerencias", params={"q": "fas"}).json()
    assert {"etiqueta": "Editorial", "valor": "FASA", "n": 1, "url": "/?editorial=FASA"} in r["valores"]
    r = cliente.get("/api/sugerencias", params={"q": "sr3 co"}).json()
    assert [l["titulo"] for l in r["libros"]] == ["SR3 Core"]
    assert r["libros"][0]["url"].startswith("/libro/")
    r = cliente.get("/api/sugerencias", params={"q": "shadowrun"}).json()
    assert any(v["etiqueta"] == "Sistema / línea" for v in r["valores"])
    assert cliente.get("/api/sugerencias", params={"q": "x"}).json() == {"valores": [], "libros": []}


def test_novedades_no_cuenta_la_primera_carga():
    import sqlite3

    from biblioteca_rpg import web
    con = sqlite3.connect(":memory:")
    con.execute("CREATE TABLE docs (llegado_en INTEGER)")
    t0 = 1_800_000_000
    con.executemany("INSERT INTO docs VALUES (?)", [(t0 + i,) for i in range(0, 1200, 3)])  # 20 min de carga
    assert web._fin_primera_carga(con) > t0 + 1197  # sin pausa todavía: nada es novedad
    con.execute("INSERT INTO docs VALUES (?)", (t0 + 5 * 3600,))  # llega algo horas después
    fin = web._fin_primera_carga(con)
    assert t0 + 1197 < fin <= t0 + 1198


def test_busqueda_en_bgg_por_el_juego():
    from biblioteca_rpg.web import juego_para_bgg as j
    assert j("Juegos de mesa/Card games/Blue Moon City", "Blue Moon City - Cards") == "Blue Moon City"
    assert j("Juegos de mesa/Card games/Photosynthesis PnP", "Photosynthesis - Rules") == "Photosynthesis"
    assert j("Juegos de mesa/Card games/Deadwood & Expansions", "Deadwood-Card-Backs") == "Deadwood"
    assert j("Juegos de mesa/Pandemic - Contagion (Z-Man)", "x") == "Pandemic"
    assert j("Wargames/Avalon Hill/ASL/Rules", "ASL Rulebook 2nd ed") == "Advanced Squad Leader"
    assert j("Wargames/_Reference/Jane's", "Jane's Fighting Ships - 1990") == "Jane's Fighting Ships"
    assert j("Juegos de mesa/_Sueltos", "Catan - Rules") == "Catan"


def test_grupos_no_mezclan_lo_que_no_encaja():
    from biblioteca_rpg.web import agrupar_pendientes
    five = "Wargames/Other Publishers/Five/Five parsecs from home/3E/Game Aids"

    def e(i, nombre, propuesta=five, conf=0.2):
        return {"id": i, "nombre": nombre, "pdf_titulo": None, "estado": "pendiente", "propuesta": propuesta,
                "confianza": conf, "alternativas": [[propuesta, 20]]}
    pend = [e(1, "Five Parsecs from Alderaan v1.1.pdf"), e(2, "MUH084V032_Five_Parsecs_From_Home_Compendium.pdf"),
            e(3, "battlerune-champion-builder-beta-v1-0-digital-us-letter.pdf"),
            e(4, "battlerune_star_vault_unit_rulebook_alpha_v4_7_digital_us_letter.pdf"),
            e(5, "battlerune-scifi-rulebook-alpha-v4-9.pdf", propuesta="RPG/Savage Worlds/Core Rules")]
    g = agrupar_pendientes(pend)
    assert [x["carpeta"] for x in g] == [five, None]
    assert {x["id"] for x in g[0]["entradas"]} == {1, 2}
    assert g[1]["palabra"] == "battlerune" and {x["id"] for x in g[1]["entradas"]} == {3, 4, 5}


def test_entrada_sin_pdf_enseña_el_contenido(cliente):
    import os
    from pathlib import Path

    from biblioteca_rpg import config, db
    raiz = Path(os.environ["BIBLIOTECA_RAIZ"])
    carpeta = raiz / "_Inbox" / "Volo Arts"
    carpeta.mkdir(parents=True)
    (carpeta / "dragon.png").write_bytes(b"\x89PNG falso")
    (carpeta / "leeme.txt").write_text("hola")
    con = db.conectar(config.cargar().db)
    with con:
        con.execute("INSERT INTO entrada (id, nombre, ruta_actual, es_carpeta, estado) "
                    "VALUES (77, 'Volo Arts', '_Inbox/Volo Arts', 1, 'pendiente')")
    r = cliente.get("/entrada/77/pdf", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/entrada/77/contenido"
    r = cliente.get("/entrada/77/contenido")
    assert "2 ficheros, 1 imágenes" in r.text and "leeme.txt" in r.text
    assert cliente.get("/entrada/77/fichero", params={"r": "dragon.png"}).content == b"\x89PNG falso"
    assert cliente.get("/entrada/77/fichero", params={"r": "../../x"}).status_code == 404


def test_datos_a_mano_mandan_y_se_conservan(cliente):
    import os
    import re
    from pathlib import Path

    from biblioteca_rpg import config, db, enriquecer, indexador
    r = cliente.get("/", params={"q": "Dragon 100"})
    doc_id = int(re.search(r'href="/libro/(\d+)"', r.text).group(1))
    ficha = cliente.get(f"/libro/{doc_id}").text
    assert "Sistema / línea" in ficha and "Corregir o completar datos" in ficha  # vacío, pero se ve y se edita

    r = cliente.post(f"/libro/{doc_id}/datos", data={
        "sistema": "Deadlands", "antes_sistema": "", "editorial": "Pinnacle", "antes_editorial": "",
        "publicado": "1996", "antes_publicado": "", "publicado_mes": "4", "antes_publicado_mes": "",
        "tipo": "", "antes_tipo": ""}, follow_redirects=False)
    assert r.status_code == 303 and "guardado=4" in r.headers["location"]
    ficha = cliente.get(f"/libro/{doc_id}").text
    assert "Deadlands" in ficha and "abril de" in ficha and "escrito a mano" in ficha

    # Ni el indexador ni el enriquecido lo pisan.
    cfg = config.cargar()
    con = db.conectar(cfg.db)
    indexador.sincronizar(con, cfg, cfg.areas)
    enriquecer.aplicar(con)
    f = con.execute("SELECT sistema, editorial, publicado, publicado_mes, origen FROM docs WHERE id=?", (doc_id,)).fetchone()
    assert tuple(f)[:4] == ("Deadlands", "Pinnacle", 1996, 4) and '"publicado": "mano"' in f["origen"]

    # El desplegable ofrece lo que hay; vaciar el campo devuelve el dato automático.
    assert cliente.get("/api/valores", params={"campo": "sistema", "q": "dead"}).json() == [{"valor": "Deadlands", "n": 1}]
    cliente.post(f"/libro/{doc_id}/datos", data={"sistema": "", "antes_sistema": "Deadlands"})
    assert con.execute("SELECT sistema FROM docs WHERE id=?", (doc_id,)).fetchone()[0] == "Dragon Magazine"
    assert "número entre" in cliente.post(f"/libro/{doc_id}/datos", data={"publicado": "96", "antes_publicado": "1996"}).text
    assert Path(os.environ["BIBLIOTECA_RAIZ"]).exists()


def test_estadisticas(cliente):
    r = cliente.get("/estadisticas")
    assert r.status_code == 200 and "Clasificados" in r.text and "datos-est" in r.text
    r = cliente.get("/estadisticas", params={"desde": "2020-01-01", "hasta": "nada", "agrupar": "anio"})
    assert r.status_code == 200 and "Años" in r.text


def test_revisar_bgg_y_pasar_al_siguiente(cliente):
    import json

    from biblioteca_rpg import config, db
    con = db.conectar(config.cargar().db)
    ids = [r[0] for r in con.execute("SELECT id FROM docs ORDER BY id")]
    cand = {"candidatos": [{"id": 5, "nombre": "SR3", "url": "https://rpggeek.com/rpgitem/5", "puntos": 0.9}],
            "tipo": "rpgitem"}
    with con:
        for doc_id, conf in zip(ids, (0.9, 0.8, 0.7), strict=True):
            con.execute("INSERT INTO extra (doc_id, fuente, estado, confianza, datos) VALUES (?, 'geek', 'pendiente', ?, ?)",
                        (doc_id, conf, json.dumps(cand)))
    con.close()
    r = cliente.get(f"/libro/{ids[0]}")
    assert 'data-tecla="1"' in r.text and 'data-tecla="0"' in r.text and f'href="/libro/{ids[1]}"' in r.text
    r = cliente.post(f"/revisar/{ids[0]}?accion=aceptar&ref=5&volver=/revisar/siguiente?tras={ids[0]}",
                     follow_redirects=True)
    assert f"/libro/{ids[1]}?revisado={ids[0]}" in str(r.url)
    assert "Guardado" in r.text and "es SR3" in r.text
    r = cliente.post(f"/revisar/{ids[2]}?accion=rechazar&volver=/revisar/siguiente?tras={ids[2]}",
                     follow_redirects=True)
    assert f"/libro/{ids[1]}?revisado={ids[2]}" in str(r.url)  # vuelve al principio
    assert "ninguno era" in r.text
    r = cliente.post(f"/revisar/{ids[1]}?accion=rechazar&volver=/revisar/siguiente?tras={ids[1]}",
                     follow_redirects=True)
    assert str(r.url).endswith("/revisar")


def test_titulo_del_libro(cliente):
    import re

    from biblioteca_rpg import config, db, enriquecer
    r = cliente.get("/", params={"q": "Dragon 100"})
    doc_id = int(re.search(r'href="/libro/(\d+)"', r.text).group(1))
    con = db.conectar(config.cargar().db)
    titulo = con.execute("SELECT titulo FROM docs WHERE id=?", (doc_id,)).fetchone()[0]
    # De RPGGeek: se ve en la ficha y en la lista, y se encuentra buscándolo; el del fichero sigue ahí.
    with con:
        con.execute("INSERT INTO extra (doc_id, fuente, estado, datos) VALUES (?, 'geek', 'aceptado', ?)",
                    (doc_id, '{"elegido": {"id": 1, "tipo": "rpgitem", "nombre": "Dragon Magazine #100 (AD&D 1st Edition)"}}'))
        enriquecer.aplicar(con, [doc_id])
    assert con.execute("SELECT ext_titulo FROM docs WHERE id=?", (doc_id,)).fetchone()[0] == "Dragon Magazine #100"
    ficha = cliente.get(f"/libro/{doc_id}").text
    assert "<h1>Dragon Magazine #100</h1>" in ficha and "según RPGGeek" in ficha and f"en el fichero: {titulo}" in ficha
    assert "Dragon Magazine #100" in cliente.get("/", params={"q": "magazine"}).text
    # A mano manda; vaciarlo devuelve el de RPGGeek.
    cliente.post(f"/libro/{doc_id}/datos", data={"ext_titulo": "El dragón cien", "antes_ext_titulo": "Dragon Magazine #100"})
    assert "<h1>El dragón cien</h1>" in cliente.get(f"/libro/{doc_id}").text
    assert f'href="/libro/{doc_id}"' in cliente.get("/", params={"q": "dragón cien"}).text
    cliente.post(f"/libro/{doc_id}/datos", data={"ext_titulo": "", "antes_ext_titulo": "El dragón cien"})
    assert con.execute("SELECT ext_titulo FROM docs WHERE id=?", (doc_id,)).fetchone()[0] == "Dragon Magazine #100"


def test_busca_tambien_en_la_entrada(cliente):
    import json

    from biblioteca_rpg import config, db
    con = db.conectar(config.cargar().db)
    llm = {"origen": "lote", "juego": "D&D", "tipo": "Supplements", "seguridad": "media",
           "respuesta": {"editorial": "Second World Simulations"}}
    with con:
        con.execute("INSERT INTO entrada (id, nombre, ruta_actual, es_carpeta, estado, editorial, publicado, paginas, "
                    "tamano, confianza, alternativas, propuesta, llm) VALUES (88, '2WS1100e Masters of Arms 3.5 [OEF].pdf', "
                    "'_Inbox/2WS1100e Masters of Arms 3.5 [OEF].pdf', 0, 'pendiente', 'AEG', 2004, 98, 6190491, 0.22, ?, "
                    "'RPG/D&D', ?)", (json.dumps([["RPG/D&D", 22]]), json.dumps(llm)))
        con.execute("INSERT INTO entrada (id, nombre, ruta_actual, es_carpeta, estado, confianza, alternativas, propuesta) "
                    "VALUES (89, '2WS1101 Bodies and Souls.pdf', '_Inbox/2WS1101 Bodies and Souls.pdf', 0, 'pendiente', 0.4, "
                    "?, 'RPG/D&D')", (json.dumps([["RPG/D&D", 40]]),))
    r = cliente.get("/", params={"q": "masters of arms"}).text
    assert "En la Entrada, sin clasificar todavía" in r and 'href="/entrada/revisar?id=88"' in r
    assert "AEG<span class=\"segun\">según el PDF</span>" in r and "Second World Simulations<span class=\"segun\">según Claude</span>" in r
    assert "En la Entrada" in cliente.get("/", params={"q": "second world"}).text  # también por lo que leyó Claude
    assert "En la Entrada" not in cliente.get("/", params={"q": "dragon"}).text
    for pagina in ("/entrada", "/entrada/revisar?id=88"):  # los grupos se ven en el servidor
        t = cliente.get(pagina).text
        assert "según Claude" in t, pagina
    assert "Renombrar el fichero (opcional)" in cliente.get("/entrada").text
