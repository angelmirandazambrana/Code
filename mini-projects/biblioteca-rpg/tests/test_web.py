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
