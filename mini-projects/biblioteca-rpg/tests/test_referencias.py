from biblioteca_rpg import referencias as R
from biblioteca_rpg.enriquecer import _efectivos

HTML = """<h2>Modules</h2><table class="wikitable"><tr><th>Code</th><th>TSR#</th><th>Title</th><th>Published</th></tr>
<tr><td>A1</td><td>9039</td><td>Slave Pits of the Undercity<sup>[1]</sup></td><td>1980</td></tr>
<tr><td colspan="4">Boxed sets</td></tr>
<tr><td>X</td><td></td><td>Sins of the Fathers</td><td>Dungeon #12 (July 1988)</td></tr></table>
<table class="wikitable"><tr><th>Title</th><th>Date</th><th>ISBN</th><th>Code</th></tr>
<tr><td>Rise of the Runelords #1: Burnt Offerings</td><td>August 2007</td><td>ISBN 978-1-60125-035-3</td>
<td>PZO9001</td></tr></table>"""

TRAV = """{{InfoboxBook2
|name      = Azhanti High Lightning
|author    = Frank Chadwick, [[Marc Miller]]
|footnote  = Product #818
|publisher = [[Game Designers' Workshop|Game Designers Workshop]]
|year      = 1980
}}
Texto."""


def test_fecha_e_isbn():
    assert R.fecha("August 13, 2009") == (2009, 8)
    assert R.fecha("June 2004") == (2004, 6)
    assert R.fecha("08/01/2000 PDF Archive") == (2000, 8)
    assert R.fecha("1980") == (1980, None)
    assert R.isbn13("0-7869-3274-0") == "9780786932740"


def test_filas_wikipedia():
    filas = R.filas_wikipedia(HTML, R._dnd, "TSR")
    assert [(f["titulo"], f["anio"], f["mes"], f["codigo"], f["editorial"]) for f in filas] == [
        ("Slave Pits of the Undercity", 1980, None, "TSR9039", "TSR"),
        ("Rise of the Runelords #1: Burnt Offerings", 2007, 8, "PZO9001", "Wizards of the Coast")]
    assert filas[0]["modulo"] == "A1" and filas[1]["isbn"] == "9781601250353"


def test_traveller():
    p = R.producto_traveller("Azhanti High Lightning", TRAV)
    assert (p["titulo"], p["anio"], p["editorial"], p["codigo"], p["autores"]) == \
        ("Azhanti High Lightning", 1980, "Game Designers Workshop", "818", "Frank Chadwick, Marc Miller")


def _ref(titulo, anio, **kw):
    return {"titulo": titulo, "anio": anio, "mes": None, "isbn": None, "codigo": None, **kw}


def test_titulo_doc():
    assert R.titulo_doc("MGT2 - The Third Imperium [2016].pdf") == "third imperium"
    assert R.titulo_doc("TSR 9039 - A1 - Slave Pits of the Undercity (OCR).pdf") == "slave pits of the undercity"
    assert R.titulo_doc("CT_Azhanti_High_Lightning.pdf") == "azhanti high lightning"
    assert R.titulo_doc("MGT2 - Central Supply Catalogue (Update 2023) [2022-12-22].pdf") == \
        "central supply catalogue update 2023"
    assert R.titulo_doc("Pirates of Drinax 2 (alt scan) (2016) (MGP40009).pdf") == "pirates of drinax 2"


def test_edicion_de_la_carpeta():
    assert R.anio_de_carpeta("Magazines/Traveller/JTAS (Mongoose 2019)/JTAS 6.pdf") == 2019
    assert R.anio_de_carpeta("RPG/Traveller/Misc/x.pdf") is None
    ix = R.Indice([_ref("Journal of the Travellers' Aid Society 6", 1980)])
    assert R.emparejar({"nombre": "Journal of the Travellers Aid Society 6.pdf",
                        "ruta": "Magazines/Traveller/JTAS (Mongoose 2019)/x.pdf"}, ix) is None


def test_numeros_distintos_no_emparejan():
    ix = R.Indice([_ref("Pirates of Drinax 1", 2016), _ref("Central Supply Catalogue", 2016)])
    assert R.emparejar({"nombre": "Pirates of Drinax 2.pdf"}, ix) is None
    assert R.emparejar({"nombre": "Central Supply Catalogue (Update 2023).pdf"}, ix) is None
    assert R.emparejar({"nombre": "Pirates of Drinax 01.pdf"}, ix)[0]["titulo"] == "Pirates of Drinax 1"


def test_emparejar():
    refs = [_ref("Player's Handbook", 1978), _ref("Player's Handbook", 2000), _ref("Player's Handbook", 2014),
            _ref("Slave Pits of the Undercity", 1980, codigo="TSR9039"),
            _ref("Rise of the Runelords #1: Burnt Offerings", 2007, isbn="9781601250353")]
    ix = R.Indice(refs)
    assert R.emparejar({"nombre": "Players Handbook.pdf", "anio": 2000}, ix)[0]["anio"] == 2000
    assert R.emparejar({"nombre": "Players Handbook.pdf", "anio": None}, ix) is None  # ¿qué edición?
    assert R.emparejar({"nombre": "A1 Slave Pits.pdf", "codigo": "TSR9039"}, ix)[2] == "codigo"
    assert R.emparejar({"nombre": "Burnt Offerings.pdf", "anio": 2009}, ix)[0]["anio"] == 2007
    assert R.emparejar({"nombre": "x.pdf", "isbn": "9781601250353"}, ix)[2] == "isbn"
    assert R.emparejar({"nombre": "Burnt Offerings.pdf"}, ix)[2] == "titulo"
    assert R.emparejar({"nombre": "Slave Pits of the Undercity.pdf", "anio": 1989}, ix) is None  # otra edición
    assert R.emparejar({"nombre": "Players Handbook.pdf", "anio": 2014}, ix)[0]["anio"] == 2014
    assert R.emparejar({"nombre": "Something Else Entirely.pdf"}, ix) is None


def test_ref_en_efectivos():
    v = _efectivos({"ref": {"publicado": 1980, "mes": 6, "editorial": "TSR"}, "pdf": {"publicado": 1995}}, "x.pdf")
    assert (v["publicado"], v["publicado_mes"], v["ext_editorial"]) == (1980, 6, "TSR")
    v = _efectivos({"ref": {"publicado": 1980, "mes": 6}, "geek": {"elegido": {"anio": 1981}}}, "x.pdf")
    assert (v["publicado"], v["publicado_mes"]) == (1981, None)
