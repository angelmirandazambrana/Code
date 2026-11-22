"""Datos escritos a mano en la ficha (sistema, editorial, año…): mandan sobre lo que se deduce.

Se guardan aparte (tabla `manual`) y se vuelven a poner encima cada vez que el indexador
recalcula los datos de la ruta o se aplican los extras (PDF, RPGGeek, wikis). Borrar un dato
a mano devuelve el automático.
"""

import time

# campo de docs -> etiqueta. Los de la ruta los recalcula el indexador; los otros, enriquecer.aplicar.
DE_RUTA = {
    "sistema": "Sistema / línea", "edicion": "Edición", "editorial": "Editorial", "tipo": "Tipo",
    "ambientacion": "Ambientación", "idioma": "Idioma", "formato": "Formato",
}
DE_EXTRA = {"publicado": "Año de publicación", "publicado_mes": "Mes de publicación", "autores": "Autores"}
CAMPOS = {**DE_RUTA, **DE_EXTRA}
ENTEROS = {"publicado": (1900, 2100), "publicado_mes": (1, 12)}


def valores(con, ids=None) -> dict[int, dict]:
    """{doc_id: {campo: valor}} de lo escrito a mano (de todos, o de esos ids)."""
    sql, params = "SELECT doc_id, campo, valor FROM manual", []
    if ids is not None:
        ids = list(ids)
        if not ids:
            return {}
        sql += f" WHERE doc_id IN ({','.join('?' * len(ids))})"
        params = ids
    res: dict[int, dict] = {}
    for doc_id, campo, valor in con.execute(sql, params):
        if campo in ENTEROS:
            valor = int(valor)
        res.setdefault(doc_id, {})[campo] = valor
    return res


def normalizar_valor(campo: str, valor) -> str | int | None:
    """Limpia lo escrito; None = vacío. Lanza ValueError si no vale."""
    if campo not in CAMPOS:
        raise ValueError(f"Campo desconocido: {campo}")
    v = " ".join(str(valor or "").split())
    if not v:
        return None
    if campo in ENTEROS:
        lo, hi = ENTEROS[campo]
        if not v.isdigit() or not lo <= int(v) <= hi:
            raise ValueError(f"{CAMPOS[campo]}: tiene que ser un número entre {lo} y {hi}")
        return int(v)
    return v[:200]


def guardar(con, doc_id: int, cambios: dict) -> None:
    """cambios: {campo: valor}; valor None borra el dato a mano (vuelve el automático)."""
    from .db import CAMPOS_RUTA
    from .enriquecer import aplicar
    from .rutas import analizar

    ahora = int(time.time())
    with con:
        for campo, valor in cambios.items():
            if valor is None:
                con.execute("DELETE FROM manual WHERE doc_id=? AND campo=?", (doc_id, campo))
            else:
                con.execute("INSERT INTO manual (doc_id, campo, valor, creado_en) VALUES (?, ?, ?, ?) "
                            "ON CONFLICT(doc_id, campo) DO UPDATE SET valor=excluded.valor, creado_en=excluded.creado_en",
                            (doc_id, campo, str(valor), ahora))
        # Rehacer este documento: datos de la ruta + lo escrito a mano, y luego los extras.
        ruta = con.execute("SELECT ruta FROM docs WHERE id=?", (doc_id,)).fetchone()[0]
        datos = analizar(ruta).dict()
        sobre_ruta(datos, valores(con, [doc_id]).get(doc_id, {}))
        con.execute(f"UPDATE docs SET {', '.join(c + '=?' for c in CAMPOS_RUTA)}, actualizado_en=? WHERE id=?",
                    (*[datos[c] for c in CAMPOS_RUTA], ahora, doc_id))
        aplicar(con, [doc_id])


def sobre_ruta(datos: dict, man: dict) -> dict:
    """Pone lo escrito a mano encima de los datos sacados de la ruta (los modifica y los devuelve)."""
    for campo in DE_RUTA:
        if campo in man:
            datos[campo] = man[campo]
    return datos
