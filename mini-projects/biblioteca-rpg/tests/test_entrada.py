import os
import shutil
import sys
import zipfile
from pathlib import Path

import pymupdf
import pytest
from fastapi.testclient import TestClient


def _pdf(ruta: Path, texto: str) -> None:
    ruta.parent.mkdir(parents=True, exist_ok=True)
    doc = pymupdf.open()
    doc.new_page().insert_text((72, 72), texto)
    doc.save(ruta)
    doc.close()


@pytest.fixture()
def entorno(tmp_path, monkeypatch):
    raiz = tmp_path / "biblio"
    sr = raiz / "RPG/Shadowrun/5e (Catalyst, 2013)"
    for nombre in ["Shadowrun Sprawl Wilds", "Shadowrun Stolen Souls", "Shadowrun Data Trails", "Shadowrun Run Faster"]:
        _pdf(sr / f"{nombre}.pdf", f"{nombre}\n© 2014 Catalyst Game Labs")
    _pdf(raiz / "RPG/Pathfinder/1e/Adventure Path/Rise of the Runelords.pdf", "Runelords")
    _pdf(raiz / "RPG/Traveller/Classic/Traveller Book 1.pdf", "Traveller")
    (raiz / "_Inbox").mkdir()
    monkeypatch.setenv("BIBLIOTECA_RAIZ", str(raiz))
    monkeypatch.setenv("BIBLIOTECA_DATOS", str(tmp_path / "datos"))
    monkeypatch.setenv("BIBLIOTECA_PROCESOS", "1")
    if sys.platform == "win32" and Path(r"C:\Windows\System32\tar.exe").exists():
        monkeypatch.setenv("BIBLIOTECA_TAR", r"C:\Windows\System32\tar.exe")

    from biblioteca_rpg import config, db, entrada, indexador
    monkeypatch.setattr(entrada, "ESTABLE_SEG", -10)  # el zip redondea la hora hacia arriba
    cfg = config.cargar()
    con = db.conectar(cfg.db)
    indexador.sincronizar(con, cfg, cfg.areas)
    return cfg, con, raiz, entrada


def _estado(con, nombre):
    return con.execute("SELECT * FROM entrada WHERE nombre=? ORDER BY id DESC", (nombre,)).fetchone()


def test_automatico_duplicado_y_dudoso(entorno):
    cfg, con, raiz, entrada = entorno
    inbox = raiz / "_Inbox"
    _pdf(inbox / "Shadowrun Kill Code.pdf", "Kill Code\n© 2016 Catalyst Game Labs")
    shutil.copy(raiz / "RPG/Traveller/Classic/Traveller Book 1.pdf", inbox / "copia con otro nombre.pdf")
    _pdf(inbox / "Cosa rarisima.pdf", "nada que ver")
    entrada.pasada(con, cfg)

    # Muy claro: movido solo, catalogado y con portada.
    kc = _estado(con, "Shadowrun Kill Code.pdf")
    assert kc["estado"] == "movido" and kc["auto"] == 1
    assert kc["ruta_actual"] == "RPG/Shadowrun/5e (Catalyst, 2013)/Shadowrun Kill Code.pdf"
    assert (raiz / kc["ruta_actual"]).is_file() and not (inbox / "Shadowrun Kill Code.pdf").exists()
    doc = con.execute("SELECT portada, publicado FROM docs WHERE ruta=?", (kc["ruta_actual"],)).fetchone()
    assert doc["portada"] == 1 and doc["publicado"] == 2016

    # Idéntico a uno que ya está: va a _Duplicados, nada se borra.
    dup = _estado(con, "copia con otro nombre.pdf")
    assert dup["estado"] == "descartado" and dup["duplicado_de"]
    assert (raiz / "_Duplicados/_Inbox/copia con otro nombre.pdf").is_file()

    # Sin parecidos: se queda esperando revisión.
    assert _estado(con, "Cosa rarisima.pdf")["estado"] == "pendiente"
    assert (inbox / "Cosa rarisima.pdf").is_file()

    csv = next((raiz / "_Reorganizacion").glob("clasificador_*.csv")).read_text(encoding="utf-8-sig")
    assert csv.startswith("Fecha;Accion;Origen;Destino;Nota") and "Shadowrun Kill Code.pdf" in csv


def test_comprimido_y_colision(entorno):
    cfg, con, raiz, entrada = entorno
    inbox = raiz / "_Inbox"
    tmp = raiz.parent / "zipsrc"
    _pdf(tmp / "Gimmick Zero/Gimmick Zero Core.pdf", "core")
    _pdf(tmp / "Gimmick Zero/Gimmick Zero Maps.pdf", "maps")
    with zipfile.ZipFile(inbox / "Gimmick-Zero.zip", "w") as z:
        for p in (tmp / "Gimmick Zero").iterdir():
            z.write(p, f"Gimmick Zero/{p.name}")
    entrada.pasada(con, cfg)
    # Descomprimido sin anidar y el original guardado.
    assert (inbox / "Gimmick Zero/Gimmick Zero Core.pdf").is_file()
    assert (raiz / "_Duplicados/_Inbox comprimidos/Gimmick-Zero.zip").is_file()
    entrada.pasada(con, cfg)
    lote = _estado(con, "Gimmick Zero")
    assert lote["es_carpeta"] == 1 and lote["n_pdfs"] == 2 and lote["estado"] == "pendiente"

    # Un fichero distinto con el mismo nombre que uno de la biblioteca: no se sobrescribe.
    _pdf(inbox / "Shadowrun Sprawl Wilds.pdf", "otra versión distinta")
    entrada.pasada(con, cfg)
    sw = _estado(con, "Shadowrun Sprawl Wilds.pdf")
    assert sw["estado"] == "movido"
    assert sw["ruta_actual"].endswith("Shadowrun Sprawl Wilds (2).pdf")
    original = raiz / "RPG/Shadowrun/5e (Catalyst, 2013)/Shadowrun Sprawl Wilds.pdf"
    assert "Sprawl Wilds" in pymupdf.open(original)[0].get_text()


def test_web_decidir_y_corregir(entorno):
    import importlib

    cfg, con, raiz, entrada = entorno
    _pdf(raiz / "_Inbox/Cosa rarisima.pdf", "nada")
    entrada.pasada(con, cfg)
    eid = _estado(con, "Cosa rarisima.pdf")["id"]

    from biblioteca_rpg import web
    importlib.reload(web)
    cli = TestClient(web.app)
    pagina = cli.get("/entrada").text
    assert "Cosa rarisima.pdf" in pagina and 'class="globo">1<' in pagina
    assert cli.get(f"/entrada/{eid}/pdf").headers["content-type"] == "application/pdf"
    # Búsqueda por palabras sueltas, en cualquier parte de la ruta, sin mayúsculas ni acentos.
    def carpetas(q):
        return [r["carpeta"] for r in cli.get("/api/carpetas", params={"q": q}).json()]
    assert carpetas("shadow") == ["RPG/Shadowrun", "RPG/Shadowrun/5e (Catalyst, 2013)"]
    assert carpetas("5E shadow") == ["RPG/Shadowrun/5e (Catalyst, 2013)"]
    assert carpetas("catalyst") == ["RPG/Shadowrun/5e (Catalyst, 2013)"]
    assert carpetas("rpg adventure") == ["RPG/Pathfinder/1e/Adventure Path"]
    assert carpetas("zzz") == []
    assert cli.get("/api/carpetas", params={"q": "shadow"}).json()[0]["n"] == 4

    # Destinos fuera de la biblioteca o con '..': rechazados.
    for malo in ["/etc", "RPG/../../etc", "Otra/Cosa", "C:/Windows"]:
        r = cli.post(f"/entrada/{eid}", data={"accion": "mover", "destino": "__otra", "destino_otra": malo})
        assert r.status_code == 400, malo

    r = cli.post(f"/entrada/{eid}", data={"accion": "mover", "destino": "__otra",
                                          "destino_otra": "RPG/Traveller/Classic", "nombre": "Cosa rara"},
                 follow_redirects=False)
    assert r.status_code == 303
    # Mientras está en cola no se puede volver a cambiar.
    assert cli.post(f"/entrada/{eid}", data={"accion": "descartar"}).status_code == 409
    entrada.pasada(con, cfg)
    assert (raiz / "RPG/Traveller/Classic/Cosa rara.pdf").is_file()

    # Corregir: a otra carpeta (nueva). El catálogo sigue al fichero.
    cli.post(f"/entrada/{eid}", data={"accion": "mover", "destino": "__otra", "destino_otra": "RPG/Varios"})
    entrada.pasada(con, cfg)
    assert (raiz / "RPG/Varios/Cosa rara.pdf").is_file()
    rutas = [r[0] for r in con.execute("SELECT ruta FROM docs WHERE titulo LIKE 'Cosa%'")]
    assert rutas == ["RPG/Varios/Cosa rara.pdf"]
    assert cli.get("/novedades").status_code == 200
    assert os.listdir(raiz / "_Inbox") == []
