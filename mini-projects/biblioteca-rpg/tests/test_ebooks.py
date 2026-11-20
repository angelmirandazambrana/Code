import zipfile

from biblioteca_rpg import indexador


def hacer_epub(ruta, titulo="Novela de prueba"):
    with zipfile.ZipFile(ruta, "w") as z:
        z.writestr("mimetype", "application/epub+zip", compress_type=zipfile.ZIP_STORED)
        z.writestr("META-INF/container.xml", """<?xml version="1.0"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
<rootfiles><rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/></rootfiles>
</container>""")
        z.writestr("OEBPS/content.opf", f"""<?xml version="1.0"?>
<package xmlns="http://www.idpf.org/2007/opf" version="2.0" unique-identifier="id">
<metadata xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:title>{titulo}</dc:title>
<dc:creator>Autora</dc:creator><dc:identifier id="id">x</dc:identifier><dc:language>es</dc:language></metadata>
<manifest><item id="c1" href="c1.xhtml" media-type="application/xhtml+xml"/></manifest>
<spine><itemref idref="c1"/></spine></package>""")
        z.writestr("OEBPS/c1.xhtml", """<?xml version="1.0"?>
<html xmlns="http://www.w3.org/1999/xhtml"><head><title>c1</title></head>
<body><h1>Capítulo 1</h1><p>Érase una vez.</p></body></html>""")


def test_portada_y_datos_de_un_epub(tmp_path):
    epub = tmp_path / "Novela.epub"
    hacer_epub(epub)
    jpg = tmp_path / "p.jpg"
    res = indexador._procesar_pdf(str(epub), str(jpg))
    assert res["paginas"] is None
    assert res["pdf_titulo"] == "Novela de prueba" and res["pdf_autor"] == "Autora"
    assert jpg.stat().st_size > 0


def test_el_indexador_recoge_epubs(tmp_path):
    (tmp_path / "Wargames/Novelas").mkdir(parents=True)
    hacer_epub(tmp_path / "Wargames/Novelas/Uno.epub")
    (tmp_path / "Wargames/Novelas/Dos.pdf").write_bytes(b"%PDF-1.4")
    (tmp_path / "Wargames/Novelas/Tres.txt").write_text("no")
    nombres = sorted(r[0] for r in indexador.recorrer(tmp_path, ["Wargames"]))
    assert nombres == ["Wargames/Novelas/Dos.pdf", "Wargames/Novelas/Uno.epub"]
