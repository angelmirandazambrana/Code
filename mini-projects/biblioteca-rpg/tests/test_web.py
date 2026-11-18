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
