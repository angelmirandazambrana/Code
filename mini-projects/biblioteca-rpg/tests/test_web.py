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
    assert j("Wargames/Avalon Hill/ASL/Rules", "ASL Rulebook 2nd ed") == "ASL"
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
