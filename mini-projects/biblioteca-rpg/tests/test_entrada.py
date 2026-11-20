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


def test_novelas_de_warhammer_por_canal_o_nombre():
    from biblioteca_rpg.entrada import NOVELAS_40K, destino_novela
    assert destino_novela("Mike Brooks - Dead Drop.epub", "Necromunda Vault") == NOVELAS_40K + "/Necromunda"
    assert destino_novela("Uprising.epub", "Codex Warhammer 40000") == NOVELAS_40K
    assert destino_novela("Horus Heresy 01 - Horus Rising.mobi", "") == NOVELAS_40K + "/Horus Heresy"
    assert destino_novela("Codex Orks.pdf", "Codex Warhammer 40000") is None
    assert destino_novela("Dune.epub", "Libros de ciencia ficción") is None


def test_palabras_de_edicion_y_genericas():
    from biblioteca_rpg.clasificador import palabras
    assert palabras("Warhammer_40k_9_Edicion_Libro_Basico_edicion_INDOMITUS_castellano.pdf") == \
        ["warhammer", "40k", "9e", "indomitus"]
    assert "5e" in palabras("D&D 5th Edition Player's Handbook")


def test_carpeta_nueva_para_un_juego_que_no_teniamos(entorno):
    cfg, con, raiz, entrada = entorno
    for n in ("Gimmick Zero Core", "Gimmick Zero Quickstart"):
        _pdf(raiz / f"_Inbox/Gimmick-Zero/{n}.pdf", n)
    entrada.pasada(con, cfg)
    e = _estado(con, "Gimmick-Zero")
    assert e["estado"] == "pendiente"
    import importlib

    from biblioteca_rpg import web
    importlib.reload(web)  # la web lee la configuración al importarse
    cli = TestClient(web.app)
    r = cli.post(f"/entrada/{e['id']}", data={"accion": "mover", "destino": "__otra",
                                              "destino_otra": "RPG/Gimmick-zero"}, follow_redirects=False)
    assert r.status_code == 303
    entrada.pasada(con, cfg)
    # La carpeta que llega ES la nueva: no queda RPG/Gimmick-zero/Gimmick-Zero.
    assert (raiz / "RPG/Gimmick-zero/Gimmick Zero Core.pdf").is_file()
    assert not (raiz / "RPG/Gimmick-zero/Gimmick-Zero").exists()
    assert con.execute("SELECT COUNT(*) FROM docs WHERE carpeta='RPG/Gimmick-zero'").fetchone()[0] == 2


def test_serie_de_revistas_va_junto_a_sus_hermanas():
    from biblioteca_rpg.entrada import junto_a_sus_hermanas
    base = "Magazines/White Dwarf Magazine/Old"
    p = {"propuesta": f"{base}/White Dwarf - 020-029 UK", "confianza": 0.53, "apoyos": 9,
         "alternativas": [[f"{base}/White Dwarf - 020-029 UK", 53], [f"{base}/White Dwarf - 030-039 UK", 30],
                          ["RPG/Otra", 17]]}
    r = junto_a_sus_hermanas(p, "White Dwarf - 110-119 UK")
    assert r["propuesta"] == base and r["confianza"] == 0.83
    assert r["alternativas"] == [[base, 83], ["RPG/Otra", 17]]


def test_papercraft_es_un_area():
    from biblioteca_rpg import config, rutas
    cfg = config.cargar()
    assert config.en_areas(cfg.areas, "assets/Papercraft/Fat Dragon Games/E Z Dungeons")
    assert not config.en_areas(cfg.areas, "assets/Tokens/x")
    assert not config.en_areas(cfg.areas, "assets/PapercraftX")
    d = rutas.analizar("assets/Papercraft/Fat Dragon Games/E Z Dungeons/Dungeon.pdf")
    assert (d.categoria, d.editorial) == ("Papercraft", "Fat Dragon Games")


def test_hermanas_ya_anidadas_suben_hasta_su_sitio():
    from biblioteca_rpg.entrada import junto_a_sus_hermanas
    base = "Magazines/WD/Old"
    anidada = f"{base}/White Dwarf - 020-029 UK/White Dwarf - 050-059 UK"
    p = {"propuesta": anidada, "confianza": 0.3, "apoyos": 8, "alternativas": [[anidada, 30], [base, 70]]}
    assert junto_a_sus_hermanas(p, "White Dwarf - 110-119 UK")["alternativas"] == [[base, 100]]


def test_reintentar_un_comprimido_lo_descomprime(entorno, monkeypatch):
    cfg, con, raiz, entrada = entorno
    _pdf(raiz / "tmp/Revista 001.pdf", "Revista")
    with zipfile.ZipFile(raiz / "_Inbox/Revistas.zip", "w") as z:
        z.write(raiz / "tmp/Revista 001.pdf", "Revista 001.pdf")
    real = entrada.descomprimir
    monkeypatch.setattr(entrada, "descomprimir", lambda *a: (_ for _ in ()).throw(RuntimeError("método raro")))
    entrada.pasada(con, cfg)
    e = _estado(con, "Revistas.zip")
    assert e["estado"] == "error"
    monkeypatch.setattr(entrada, "descomprimir", real)
    with con:
        con.execute("UPDATE entrada SET estado='nuevo' WHERE id=?", (e["id"],))  # «Reintentar» en la web
    entrada.pasada(con, cfg)
    assert _estado(con, "Revistas.zip")["estado"] == "desaparecido"
    assert (raiz / "_Inbox/Revistas").is_dir() or _estado(con, "Revistas") is not None


def test_hermanas_con_etiquetas_cortas_distintas():
    from biblioteca_rpg.entrada import junto_a_sus_hermanas
    base = "Magazines/WD/Old"
    p = {"propuesta": f"{base}/Warhammer - White Dwarf - 240-249 UK", "confianza": 0.4, "apoyos": 8,
         "alternativas": [[f"{base}/Warhammer - White Dwarf - 240-249 UK", 40], [base, 30],
                          ["RPG/Warhammer Fantasy Roleplay", 30]]}
    r = junto_a_sus_hermanas(p, "Warhammer - White Dwarf - 200-209")
    assert r["propuesta"] == base and r["alternativas"][0] == [base, 70]
    # Una sola palabra en común no basta: "Warhammer Fantasy Roleplay" no es hermana de "Warhammer 40K".
    q = {"propuesta": "RPG/Warhammer Fantasy Roleplay", "confianza": 1.0, "apoyos": 9,
         "alternativas": [["RPG/Warhammer Fantasy Roleplay", 100]]}
    assert junto_a_sus_hermanas(q, "Warhammer 40K")["propuesta"] == "RPG/Warhammer Fantasy Roleplay"


def test_bayes_y_vecinos_de_acuerdo():
    from biblioteca_rpg.clasificador import Bayes, con_segunda_opinion, es_seguro, rasgos
    filas = [(f"Shadowrun {x}.pdf", "RPG/Shadowrun/5e", "Catalyst", None) for x in ("Core", "Kill Code", "Data Trails")]
    filas += [(f"Traveller {x}.pdf", "RPG/Traveller", "GDW", None) for x in ("Book 1", "Book 2", "Book 3")]
    b = Bayes(filas)
    carpeta, prob = b.mejor(rasgos("Shadowrun Run Faster.pdf", "Catalyst"))
    assert carpeta == "RPG/Shadowrun/5e" and prob > 0.9
    p = {"propuesta": "RPG/Shadowrun/5e", "confianza": 0.6, "apoyos": 2, "alternativas": []}
    assert not es_seguro(p, 0.9)  # los vecinos solos no bastan
    assert es_seguro(con_segunda_opinion(p, b, rasgos("Shadowrun Run Faster.pdf", "Catalyst")), 0.9)
    otro = {**p, "propuesta": "RPG/Traveller"}  # Bayes no está de acuerdo: se pregunta
    assert not es_seguro(con_segunda_opinion(otro, b, rasgos("Shadowrun Run Faster.pdf", "Catalyst")), 0.9)


def test_revision_por_grupos(entorno):
    import importlib

    cfg, con, raiz, entrada = entorno
    for n in ("Cosa rara uno", "Cosa rara dos", "Cosa rara tres", "Otra suelta"):
        _pdf(raiz / f"_Inbox/{n}.pdf", n)
    entrada.pasada(con, cfg)
    with con:  # tres proponen la misma carpeta; una va sola
        con.execute("UPDATE entrada SET estado='pendiente', propuesta='RPG/Traveller/Classic', confianza=0.5 "
                    "WHERE nombre LIKE 'Cosa rara%'")
        con.execute("UPDATE entrada SET estado='pendiente', propuesta='RPG/Pathfinder/1e', confianza=0.4 "
                    "WHERE nombre LIKE 'Otra%'")
    from biblioteca_rpg import web
    importlib.reload(web)
    cli = TestClient(web.app)
    html = cli.get("/entrada?vista=grupos").text
    assert "Mover los marcados aquí" in html and html.count('class="grupo"') == 1  # la suelta no forma grupo
    ids = [_estado(con, f"Cosa rara {n}.pdf")["id"] for n in ("uno", "dos", "tres")]
    # Se desmarca uno: solo se mueven los otros dos.
    r = cli.post("/entrada/lote", data={"destino": "RPG/Traveller/Classic", "ids": ids[:2]}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/entrada?vista=grupos"
    assert cli.post("/entrada/lote", data={"destino": "../etc", "ids": ids[2:]}).status_code == 400
    entrada.pasada(con, cfg)
    assert (raiz / "RPG/Traveller/Classic/Cosa rara uno.pdf").is_file()
    assert (raiz / "RPG/Traveller/Classic/Cosa rara dos.pdf").is_file()
    assert _estado(con, "Cosa rara tres.pdf")["estado"] == "pendiente"
