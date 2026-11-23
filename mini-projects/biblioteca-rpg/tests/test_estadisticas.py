from datetime import date, datetime

from biblioteca_rpg import db
from biblioteca_rpg import estadisticas as est


def _ts(*a):
    return datetime(*a).timestamp()


def _con(tmp_path):
    con = db.conectar(tmp_path / "c.sqlite")
    filas = [  # estado, auto, llegado, decidido, movido
        ("movido", 1, _ts(2026, 11, 2, 10), _ts(2026, 11, 2, 11), _ts(2026, 11, 2, 11)),
        ("movido", 0, _ts(2026, 11, 2, 10), _ts(2026, 11, 4, 9), _ts(2026, 11, 4, 9)),
        ("descartado", 0, _ts(2026, 11, 3, 10), _ts(2026, 11, 3, 12), None),
        ("pendiente", 0, _ts(2026, 11, 3, 10), None, None),
        ("desaparecido", 0, _ts(2026, 11, 3, 10), None, None),
    ]
    with con:
        for e, a, ll, de, mo in filas:
            con.execute("INSERT INTO entrada (nombre, ruta_actual, estado, auto, llegado_en, decidido_en, movido_en) "
                        "VALUES ('x', 'x', ?, ?, ?, ?, ?)", (e, a, ll, de, mo))
        con.execute("INSERT INTO docs (ruta, carpeta) VALUES ('RPG/A/_Por clasificar/a.pdf', 'RPG/A/_Por clasificar')")
        con.execute("INSERT INTO docs (ruta, carpeta) VALUES ('RPG/B/b.pdf', 'RPG/B')")
    return con


def test_serie_por_dias(tmp_path):
    con = _con(tmp_path)
    s = est.serie(con, date(2026, 11, 1), date(2026, 11, 5), "dia", ahora=_ts(2026, 11, 5, 12))
    assert s["etiquetas"] == ["1 nov", "2 nov", "3 nov", "4 nov", "5 nov"]
    assert s["llegados"] == [0, 2, 3, 0, 0]
    assert s["solos"] == [0, 1, 0, 0, 0] and s["a_mano"] == [0, 0, 0, 1, 0]
    assert s["duplicados"] == [0, 0, 1, 0, 0]
    # sin decidir al acabar el día (el desaparecido no cuenta)
    assert s["en_entrada"] == [0, 1, 2, 1, 1]


def test_semanas_meses_y_fotos(tmp_path):
    con = _con(tmp_path)
    assert est.inicio_periodo(date(2026, 11, 5), "semana") == date(2026, 11, 2)
    assert est.siguiente(date(2026, 12, 1), "mes") == date(2027, 1, 1)
    assert est.agrupacion_auto(date(2026, 1, 1), date(2026, 4, 1)) == "semana"
    assert est.fotografiar(con, ahora=_ts(2026, 11, 4, 12))
    assert not est.fotografiar(con, ahora=_ts(2026, 11, 4, 12, 5))  # menos de 10 minutos
    s = est.serie(con, date(2026, 11, 1), date(2026, 11, 30), "semana", ahora=_ts(2026, 11, 5, 12))
    assert s["periodos"][0] == "2026-10-26" and s["llegados"][:2] == [0, 5]
    assert s["por_clasificar"][1] == 1 and s["docs"][1] == 2 and s["por_clasificar"][2] is None
    assert s["en_entrada"][2] is None  # semana que aún no ha llegado
