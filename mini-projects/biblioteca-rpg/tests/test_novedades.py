from biblioteca_rpg import novedades
from biblioteca_rpg.db import conectar

RSS = b"""<?xml version="1.0"?><rss version="2.0"><channel><title>X</title>
<item><title>Dark Sun returns for D&amp;D 5e from Wizards of the Coast</title><link>https://a/1</link>
<pubDate>Tue, 03 Mar 2026 10:00:00 +0000</pubDate><description>&lt;p&gt;The Athas setting is back.&lt;/p&gt;</description></item>
<item><title>Kobold Press announces Tome of Beasts 3</title><link>https://a/2</link>
<pubDate>Wed, 04 Mar 2026 10:00:00 +0000</pubDate></item>
<item><title>Warhammer 40,000 pre-orders this weekend</title><link>https://a/3</link></item>
</channel></rss>"""

ATOM = b"""<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom">
<entry><title>Shadowdark Cursed Scroll 4</title><link href="https://b/1"/><published>2026-05-01T09:00:00Z</published>
<summary>Zine for Shadowdark RPG</summary></entry></feed>"""


def _con(tmp_path):
    con = conectar(tmp_path / "c.sqlite")
    for i in novedades.parsear(RSS) + novedades.parsear(ATOM):
        con.execute("INSERT INTO novedades (url, fuente, titulo, resumen, fecha) VALUES (?,?,?,?,?)",
                    (i["url"], "X", i["titulo"], i["resumen"], i["fecha"]))
    return con


def test_parsear():
    items = novedades.parsear(RSS)
    assert items[0]["fecha"] == "2026-03-03" and items[0]["resumen"] == "The Athas setting is back."
    assert items[2]["fecha"] is None
    assert novedades.parsear(ATOM)[0] == {"url": "https://b/1", "titulo": "Shadowdark Cursed Scroll 4",
                                          "resumen": "Zine for Shadowdark RPG", "fecha": "2026-05-01"}


def test_parecidas(tmp_path):
    con = _con(tmp_path)
    assert [n["titulo"] for n in novedades.parecidas(con, "Dark_Sun_5e_Campaign_Setting.pdf")] == \
        ["Dark Sun returns for D&D 5e from Wizards of the Coast"]
    assert novedades.parecidas(con, "TomeOfBeasts3.pdf")[0]["fecha"] == "2026-03-04"
    assert novedades.parecidas(con, "Cursed Scroll 4 - Shadowdark.pdf")
    # Una sola palabra en común no basta.
    assert novedades.parecidas(con, "Dark Heresy Core.pdf") == []
    assert novedades.parecidas(con, "") == []


def test_como_pista():
    assert novedades.como_pista([]) == []
    lineas = novedades.como_pista([{"fuente": "X", "titulo": "T", "resumen": "", "fecha": None}])
    assert lineas[1] == "- [?, X] T"
