import json
from types import SimpleNamespace as NS

from test_entrada import _pdf

from biblioteca_rpg import tipos_lotes as tl


class Falso:
    """Cliente de la API de lotes de mentira: responde según el nombre del fichero."""

    def __init__(self):
        self.lotes = {}
        self.messages = NS(batches=NS(create=self.create, retrieve=self.retrieve, results=self.results))

    def create(self, requests):
        self.lotes["lote1"] = requests
        return NS(id="lote1")

    def retrieve(self, i):
        return NS(processing_status="ended", request_counts=NS(succeeded=1, processing=0, errored=0))

    def results(self, i):
        for r in self.lotes[i]:
            texto = r["params"]["messages"][0]["content"][-1]["text"]
            resp = {"tipo": "Adventures", "seguridad": "alta"} if "Cripta" in texto else \
                {"tipo": "Supplements", "seguridad": "media"}
            msg = NS(content=[NS(type="text", text=json.dumps(resp))], usage=NS(input_tokens=2000, output_tokens=50))
            yield NS(custom_id=r["custom_id"], result=NS(type="succeeded", message=msg))


def test_lotes_tipan_y_mueven_lo_claro(tmp_path, monkeypatch):
    raiz = tmp_path / "biblio"
    _pdf(raiz / "RPG/Gimmick/_Por clasificar/La Cripta.pdf", "Cripta")
    _pdf(raiz / "RPG/Gimmick/_Por clasificar/Otra Cosa.pdf", "Cosa")
    (raiz / "_Inbox").mkdir()
    monkeypatch.setenv("BIBLIOTECA_RAIZ", str(raiz))
    monkeypatch.setenv("BIBLIOTECA_DATOS", str(tmp_path / "datos"))
    from biblioteca_rpg import config, db, indexador
    cfg = config.cargar()
    con = db.conectar(cfg.db)
    indexador.sincronizar(con, cfg, cfg.areas)
    falso = Falso()
    import anthropic
    monkeypatch.setattr(anthropic, "Anthropic", lambda: falso)

    tl.enviar(con, cfg, None)
    assert len(falso.lotes["lote1"]) == 2
    tl.enviar(con, cfg, None)  # no se reenvía lo ya enviado
    assert len(json.loads((cfg.datos / tl.ESTADO).read_text())["lotes"]) == 1

    tl.recoger(con, cfg, de_verdad=True)
    assert (raiz / "RPG/Gimmick/Adventures/La Cripta.pdf").is_file()           # seguridad alta: se mueve
    assert (raiz / "RPG/Gimmick/_Por clasificar/Otra Cosa.pdf").is_file()      # media: se queda
    assert con.execute("SELECT ruta FROM docs WHERE nombre='La Cripta.pdf'").fetchone()[0] == \
        "RPG/Gimmick/Adventures/La Cripta.pdf"
