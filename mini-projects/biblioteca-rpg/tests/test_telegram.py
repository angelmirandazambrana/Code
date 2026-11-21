import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace

import pymupdf
import pytest

from biblioteca_rpg import telegram as tg


def _pdf_bytes(texto: str, relleno: int = 0) -> bytes:
    doc = pymupdf.open()
    doc.new_page().insert_text((72, 72), texto)
    datos = doc.tobytes()
    doc.close()
    return datos + b"%" + b"x" * relleno  # relleno tras el final: sigue siendo un PDF válido


@pytest.fixture()
def entorno(tmp_path, monkeypatch):
    raiz = tmp_path / "biblio"
    (raiz / "RPG/Shadowrun/5e (Catalyst, 2013)").mkdir(parents=True)
    tenido = raiz / "RPG/Shadowrun/5e (Catalyst, 2013)/Shadowrun Sprawl Wilds.pdf"
    tenido.write_bytes(_pdf_bytes("Sprawl Wilds", 300_000))
    for n in ("Shadowrun Stolen Souls", "Shadowrun Data Trails", "Shadowrun Run Faster"):
        (raiz / f"RPG/Shadowrun/5e (Catalyst, 2013)/{n}.pdf").write_bytes(_pdf_bytes(n))
    (raiz / "_Inbox").mkdir()
    monkeypatch.setenv("BIBLIOTECA_RAIZ", str(raiz))
    monkeypatch.setenv("BIBLIOTECA_DATOS", str(tmp_path / "datos"))
    monkeypatch.setenv("BIBLIOTECA_PROCESOS", "1")
    from biblioteca_rpg import config, db, entrada, indexador
    monkeypatch.setattr(entrada, "ESTABLE_SEG", -10)
    cfg = config.cargar()
    con = db.conectar(cfg.db)
    indexador.sincronizar(con, cfg, cfg.areas)
    aj = tg.Ajustes(api_id=1, api_hash="x", canales=("@canal",),
                    extensiones=(".pdf", ".zip", ".rar"), max_mb=10)
    return cfg, con, aj, raiz, tenido


def test_nombre_seguro():
    assert tg.nombre_seguro('a/b:c?.pdf', 5) == "a_b_c_.pdf"
    assert tg.nombre_seguro(None, 42, ".pdf") == "telegram_42.pdf"
    largo = tg.nombre_seguro("x" * 300 + ".pdf", 1)
    assert len(largo) == 180 and largo.endswith(".pdf")


def test_decidir(entorno):
    cfg, con, aj, raiz, tenido = entorno
    tam = tenido.stat().st_size

    def a(nombre, tamano, doc=None):
        return tg.Adjunto("c", 1, doc, nombre, tamano)

    assert tg.decidir(con, cfg, aj, a("foto.jpg", 10))[0] == "ignorado"
    assert tg.decidir(con, cfg, aj, a("enorme.pdf", 11 * 1024 * 1024))[0] == "ignorado"
    # Mismo tamaño (y grande): ya lo tienes, aunque se llame distinto.
    estado, doc_id, _ = tg.decidir(con, cfg, aj, a("SR5 - Sprawl Wilds [OEF].pdf", tam))
    assert estado == "ya_en_biblioteca" and doc_id
    # Pequeño con el mismo tamaño pero otro nombre: no basta, se descarga.
    pequeno = (raiz / "RPG/Shadowrun/5e (Catalyst, 2013)/Shadowrun Data Trails.pdf").stat().st_size
    assert tg.decidir(con, cfg, aj, a("Otra cosa.pdf", pequeno))[0] == "descargar"
    assert tg.decidir(con, cfg, aj, a("Shadowrun Data Trails.pdf", pequeno))[0] == "ya_en_biblioteca"
    # El original de un comprimido ya descomprimido.
    guardado = raiz / "_Duplicados/_Inbox comprimidos/pack.zip"
    guardado.parent.mkdir(parents=True)
    guardado.write_bytes(b"z" * 250_000)
    assert tg.decidir(con, cfg, aj, a("pack.zip", 250_000))[0] == "ya_en_entrada"


class Cliente:
    """Imita lo que usamos de TelegramClient."""

    def __init__(self, mensajes):
        self.mensajes = mensajes
        self.descargas = 0

    async def get_entity(self, ref):
        return SimpleNamespace(id=777, title="Canal de pruebas")

    async def get_messages(self, entidad, limit=None, ids=None):
        if ids is not None:
            return [m for m in self.mensajes if m.id in ids]
        return sorted(self.mensajes, key=lambda m: -m.id)[:limit]

    async def iter_messages(self, entidad, reverse=False, min_id=0, offset_date=None, limit=None):
        for m in sorted(self.mensajes, key=lambda m: m.id):
            if m.id > (min_id or 0) and (offset_date is None or m.date >= offset_date):
                yield m

    async def download_media(self, msg, file):
        self.descargas += 1
        with open(file, "wb") as f:  # noqa: ASYNC230 (es un doble de prueba)
            f.write(msg.contenido)


def _msg(i, nombre, contenido, doc_id, texto=""):
    return SimpleNamespace(
        id=i, message=texto, date=datetime(2026, 11, 19, tzinfo=UTC), contenido=contenido,
        document=SimpleNamespace(id=doc_id),
        file=SimpleNamespace(name=nombre, ext="." + nombre.rsplit(".", 1)[-1], size=len(contenido)),
    )


def test_vigilar_canal_y_clasificar(entorno):
    cfg, con, aj, raiz, tenido = entorno
    from biblioteca_rpg import entrada

    viejo = _msg(10, "Antiguo.pdf", _pdf_bytes("viejo"), 1)
    cliente = Cliente([viejo])
    # Primera vez: se empieza desde ahora, no se baja la historia.
    asyncio.run(tg.revisar_canal(cliente, con, cfg, aj, "@canal"))
    assert cliente.descargas == 0
    assert con.execute("SELECT ultimo_id FROM telegram_canales").fetchone()[0] == 10

    nuevo = _pdf_bytes("Kill Code", 250_000)
    cliente.mensajes += [
        _msg(11, "Shadowrun Kill Code.pdf", nuevo, 2, "#Shadowrun 5e Catalyst"),
        _msg(12, "sprawl.pdf", tenido.read_bytes(), 3),          # ya en la biblioteca
        _msg(13, "portada.jpg", b"jpg", 4),                       # tipo no deseado
        _msg(14, "Shadowrun Kill Code (reenvío).pdf", nuevo, 2),  # mismo documento reenviado
    ]
    r = asyncio.run(tg.revisar_canal(cliente, con, cfg, aj, "@canal"))
    assert r == {"descargado": 1, "ya_en_biblioteca": 1, "ignorado": 1, "repetido": 1}
    assert cliente.descargas == 1
    assert (raiz / "_Inbox/Shadowrun Kill Code.pdf").read_bytes() == nuevo
    assert not list((raiz / "_Inbox").glob(".descargando-*"))
    # Volver a revisar no repite nada.
    assert asyncio.run(tg.revisar_canal(cliente, con, cfg, aj, "@canal")) == {}

    # El clasificador lo recoge y usa el mensaje de Telegram.
    entrada.pasada(con, cfg)
    e = con.execute("SELECT * FROM entrada WHERE nombre='Shadowrun Kill Code.pdf'").fetchone()
    assert e["origen_texto"] == "#Shadowrun 5e Catalyst"
    assert e["estado"] == "movido" and e["ruta_actual"].startswith("RPG/Shadowrun/5e")

    # Historial: trae lo anterior sin volver a descargar lo ya visto.
    r = asyncio.run(tg.revisar_canal(cliente, con, cfg, aj, "@canal",
                                     desde=datetime(2026, 1, 1, tzinfo=UTC)))
    assert r == {"descargado": 1} and (raiz / "_Inbox/Antiguo.pdf").exists()


def test_reintenta_errores(entorno, monkeypatch):
    cfg, con, aj, raiz, _ = entorno
    cliente = Cliente([_msg(1, "Primero.pdf", b"%PDF-1.4 a", 1)])
    asyncio.run(tg.revisar_canal(cliente, con, cfg, aj, "@canal"))
    roto = _msg(2, "Roto.pdf", b"%PDF-1.4 b", 9)
    cliente.mensajes.append(roto)

    async def falla(msg, file):
        raise ConnectionError("se cortó")
    monkeypatch.setattr(cliente, "download_media", falla)
    assert asyncio.run(tg.revisar_canal(cliente, con, cfg, aj, "@canal")) == {"error": 1}
    monkeypatch.undo()
    monkeypatch.setattr(tg.time, "time", lambda: datetime.now().timestamp() + 7200)  # una hora después
    assert asyncio.run(tg.revisar_canal(cliente, con, cfg, aj, "@canal")) == {"descargado": 1}
    assert (raiz / "_Inbox/Roto.pdf").exists()


def test_pagina_telegram(entorno):
    import importlib

    from fastapi.testclient import TestClient

    cfg, con, aj, _raiz, tenido = entorno
    cliente = Cliente([_msg(1, "a.pdf", b"%PDF", 1)])
    asyncio.run(tg.revisar_canal(cliente, con, cfg, aj, "@canal"))
    cliente.mensajes.append(_msg(2, "sprawl.pdf", tenido.read_bytes(), 3, "Sprawl Wilds"))
    asyncio.run(tg.revisar_canal(cliente, con, cfg, aj, "@canal"))
    from biblioteca_rpg import web
    importlib.reload(web)
    http = TestClient(web.app)
    html = http.get("/telegram").text
    assert "Canal de pruebas" in html and "Ya lo tenías" in html and "ver el tuyo" in html
    assert 'href="/telegram"' in http.get("/").text


def test_grandes_se_dejan_para_luego_y_freno_por_disco(entorno, monkeypatch):
    from dataclasses import replace
    cfg, con, aj, raiz, _ = entorno
    cliente = Cliente([_msg(1, "Primero.pdf", b"%PDF-1.4 a", 1)])
    asyncio.run(tg.revisar_canal(cliente, con, cfg, aj, "@canal"))
    cliente.mensajes.append(_msg(2, "Grande.pdf", b"%PDF-1.4 " + b"x" * 2_000_000, 2))
    pequeno = replace(aj, max_mb=1)
    assert asyncio.run(tg.revisar_canal(cliente, con, cfg, pequeno, "@canal")) == {"ignorado": 1}
    # No queda apuntado: un historial con más margen lo trae.
    r = asyncio.run(tg.revisar_canal(cliente, con, cfg, aj, "@canal", desde=datetime(2026, 1, 1, tzinfo=UTC)))
    assert r == {"descargado": 2}

    cliente.mensajes.append(_msg(3, "Otro.pdf", b"%PDF-1.4 c", 3))
    sin_sitio = replace(aj, min_libre_gb=10 ** 9)
    with pytest.raises(tg.SinEspacio):
        asyncio.run(tg.revisar_canal(cliente, con, cfg, sin_sitio, "@canal"))
    assert not (raiz / "_Inbox/Otro.pdf").exists()


def test_mapas_fuera_salvo_paizo_papercraft_y_foundry():
    from biblioteca_rpg.telegram import es_mapa
    # Fuera
    assert es_mapa("Czepeku Maps Pack 12.zip")
    assert es_mapa("Ancient_Ruins_Battlemap_30x40.zip")
    assert es_mapa("Forest Map Pack Vol 2.7z")
    assert es_mapa("Dungeon Tiles - Sewers.pdf")
    assert es_mapa("Token Pack Undead.zip")
    assert es_mapa("Lost Mine of Phandelver Roll20 Assets.zip")
    assert es_mapa("Tavern.zip", "Gridded and gridless versions included")
    assert es_mapa("Curse of Strahd - Maps.pdf")
    # Dentro
    assert not es_mapa("Pathfinder Flip-Mat - Bigger Dungeon.pdf")
    assert not es_mapa("Starfinder Flip-Mat - Space Station.pdf")
    assert not es_mapa("Flip-Tiles - Dungeon Starter Set.pdf")
    assert not es_mapa("Dave Graffam Models - Tavern Papercraft.zip")
    assert not es_mapa("Tomb of Annihilation - Foundry VTT module.zip")
    assert not es_mapa("Curse of Strahd.pdf")
    assert not es_mapa("Player's Handbook 2024.pdf", "Includes maps of the Forgotten Realms")
    assert not es_mapa("Mapmaker's Guide to Worldbuilding.pdf")
    assert not es_mapa("Map Pack - Graveyard.pdf") and not es_mapa("Gamemastery - Map Pack - Village.pdf")
    assert not es_mapa("A Night of Fright.pdf", "Adventure with battle maps, gridded and gridless")
    assert not es_mapa("l'avventura di olidrammara no map.pdf")
    assert not es_mapa("Necromunda - Tokens -25-01-2019.pdf")
    assert not es_mapa("PF2_The_Resurrection_Flood_Interactive_Maps_Triumph_of_The_Tust.pdf")
