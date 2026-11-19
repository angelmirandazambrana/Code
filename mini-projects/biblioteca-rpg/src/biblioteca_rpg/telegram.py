"""Descarga a la carpeta de entrada (_Inbox) los ficheros nuevos de canales y grupos de Telegram.

Solo descarga lo que no está ya en la biblioteca (mismo tamaño), en la entrada o descargado antes
(mismo documento de Telegram). Lo descargado lo recoge el clasificador como cualquier otra cosa
de _Inbox. Escribe ÚNICAMENTE en _Inbox.

    python -m biblioteca_rpg.telegram login       # una vez, interactivo: teléfono y código
    python -m biblioteca_rpg.telegram canales     # lista tus grupos y canales con su id
    python -m biblioteca_rpg.telegram vigilar     # servicio: revisa los canales cada N minutos
    python -m biblioteca_rpg.telegram historial --canal ID --desde 2026-01-01 [--limite 500]

Configuración (variables de entorno):
    TELEGRAM_API_ID, TELEGRAM_API_HASH   de https://my.telegram.org (API development tools)
    TELEGRAM_CANALES                     ids o @nombres separados por comas
    TELEGRAM_EXTENSIONES                 por defecto pdf,zip,rar,7z,cbz,cbr,epub
    TELEGRAM_MAX_MB                      tamaño máximo por fichero (4000)
    TELEGRAM_CADA_MIN                    minutos entre revisiones (10)

La primera vez que se revisa un canal NO se descarga su historia: se empieza desde ese momento.
Para traer lo anterior está la orden `historial`.
"""

import argparse
import asyncio
import os
import re
import sys
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from . import config as config_mod
from .db import conectar
from .entrada import COMPRIMIDOS_HECHOS, _libre
from .texto_pdf import normalizar

ESTADOS_TENIDOS = ("descargado", "ya_en_biblioteca", "ya_en_entrada", "repetido")
MIN_TAMANO_FIABLE = 200 * 1024  # por debajo, el mismo tamaño no basta para dar algo por repetido


def _env_lista(nombre: str, defecto: str) -> list[str]:
    return [x.strip() for x in os.environ.get(nombre, defecto).split(",") if x.strip()]


@dataclass
class Ajustes:
    api_id: int = 0
    api_hash: str = ""
    canales: tuple = ()
    extensiones: tuple = ()
    max_mb: int = 4000
    cada_min: float = 10

    @classmethod
    def cargar(cls) -> "Ajustes":
        return cls(
            api_id=int(os.environ.get("TELEGRAM_API_ID") or 0),
            api_hash=os.environ.get("TELEGRAM_API_HASH", ""),
            canales=tuple(_env_lista("TELEGRAM_CANALES", "")),
            extensiones=tuple("." + e.lower().lstrip(".")
                              for e in _env_lista("TELEGRAM_EXTENSIONES", "pdf,zip,rar,7z,cbz,cbr,epub")),
            max_mb=int(os.environ.get("TELEGRAM_MAX_MB") or 4000),
            cada_min=float(os.environ.get("TELEGRAM_CADA_MIN") or 10),
        )

    @property
    def configurado(self) -> bool:
        return bool(self.api_id and self.api_hash)


def ruta_sesion(cfg) -> Path:
    d = cfg.datos / "telegram"
    d.mkdir(parents=True, exist_ok=True)
    os.chmod(d, 0o700)  # la sesión da acceso a la cuenta: solo para el dueño
    return d / "biblioteca"


@dataclass
class Adjunto:
    canal: str
    msg_id: int
    tg_doc_id: int | None
    nombre: str
    tamano: int
    texto: str = ""
    fecha: int = 0


RE_PROHIBIDOS = re.compile(r'[\\/:*?"<>|\x00-\x1f]')


def nombre_seguro(nombre: str | None, msg_id: int, ext: str = "") -> str:
    n = RE_PROHIBIDOS.sub("_", (nombre or "").strip()).strip(" .")
    if not n:
        n = f"telegram_{msg_id}{ext or ''}"
    if len(n) > 180:
        base, e = os.path.splitext(n)
        n = base[: 180 - len(e)].rstrip() + e
    return n


# --- ¿Descargar? ------------------------------------------------------------------

def decidir(con, cfg, aj: Ajustes, a: Adjunto) -> tuple[str, int | None, str]:
    """('descargar' | 'ignorado' | 'repetido' | 'ya_en_biblioteca' | 'ya_en_entrada', doc_id, nota)."""
    ext = os.path.splitext(a.nombre)[1].lower()
    if ext not in aj.extensiones:
        return "ignorado", None, f"tipo {ext or 'sin extensión'}"
    if a.tamano > aj.max_mb * 1024 * 1024:
        return "ignorado", None, f"más de {aj.max_mb} MB"

    if a.tg_doc_id:
        f = con.execute(f"SELECT canal, msg_id FROM telegram_msgs WHERE tg_doc_id=? AND estado IN "
                        f"({','.join('?' * len(ESTADOS_TENIDOS))}) LIMIT 1",
                        (a.tg_doc_id, *ESTADOS_TENIDOS)).fetchone()
        if f:
            return "repetido", None, "el mismo fichero ya llegó por Telegram"

    fiable = a.tamano >= MIN_TAMANO_FIABLE
    nombre_n = normalizar(os.path.splitext(a.nombre)[0])

    def mismo(nombre: str) -> bool:
        return fiable or normalizar(os.path.splitext(nombre)[0]) == nombre_n

    for d in con.execute("SELECT id, nombre FROM docs WHERE tamano=?", (a.tamano,)):
        if mismo(d["nombre"]):
            return "ya_en_biblioteca", d["id"], ""
    for e in con.execute("SELECT nombre FROM entrada WHERE tamano=? AND es_carpeta=0 AND estado NOT IN "
                         "('desaparecido', 'error')", (a.tamano,)):
        if mismo(e["nombre"]):
            return "ya_en_entrada", None, "ya pasó por la entrada"
    # Lo que está ahora en _Inbox y los comprimidos ya descomprimidos (sus originales se guardan).
    for carpeta in (cfg.raiz / cfg.entrada, cfg.raiz / COMPRIMIDOS_HECHOS):
        if not carpeta.is_dir():
            continue
        for p in carpeta.iterdir():
            try:
                if p.is_file() and p.stat().st_size == a.tamano and mismo(p.name):
                    return "ya_en_entrada", None, f"ya está en {carpeta.name}"
            except OSError:
                continue
    return "descargar", None, ""


def registrar(con, a: Adjunto, estado: str, doc_id=None, archivo=None, nota="") -> None:
    with con:
        con.execute(
            "INSERT INTO telegram_msgs (canal, msg_id, tg_doc_id, nombre, tamano, texto, fecha, estado, "
            "doc_id, archivo, nota, intentos, procesado_en) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(canal, msg_id) DO UPDATE SET estado=excluded.estado, doc_id=excluded.doc_id, "
            "archivo=excluded.archivo, nota=excluded.nota, procesado_en=excluded.procesado_en, "
            "intentos=telegram_msgs.intentos + excluded.intentos",
            (a.canal, a.msg_id, a.tg_doc_id, a.nombre, a.tamano, (a.texto or "")[:2000], a.fecha, estado,
             doc_id, archivo, nota or None, 1 if estado == "error" else 0, int(time.time())),
        )


def limpiar_parciales(cfg) -> None:
    """Descargas a medias de una ejecución anterior (solo nuestros ficheros temporales)."""
    d = cfg.raiz / cfg.entrada
    if d.is_dir():
        for p in d.glob(".descargando-*"):
            p.unlink(missing_ok=True)


# --- Telethon -----------------------------------------------------------------------

def _peer_id(entidad) -> str:
    try:
        from telethon import utils
        return str(utils.get_peer_id(entidad))
    except Exception:
        return str(entidad.id)


def _referencia(texto: str):
    t = texto.strip()
    t = re.sub(r"^(https?://)?t\.me/", "", t)
    return int(t) if re.fullmatch(r"-?\d+", t) else t


def adjunto_de(canal: str, msg) -> Adjunto | None:
    if not getattr(msg, "document", None) or not getattr(msg, "file", None):
        return None
    f = msg.file
    return Adjunto(
        canal=canal, msg_id=msg.id, tg_doc_id=getattr(msg.document, "id", None),
        nombre=nombre_seguro(f.name, msg.id, f.ext), tamano=f.size or 0,
        texto=msg.message or "", fecha=int(msg.date.timestamp()) if msg.date else 0,
    )


async def _descargar(client, msg, cfg, a: Adjunto) -> str:
    import shutil

    dir_entrada = cfg.raiz / cfg.entrada
    if shutil.disk_usage(dir_entrada).free < a.tamano * 3 + 2 * 1024**3:
        raise OSError("poco espacio libre en el disco")
    tmp = dir_entrada / f".descargando-{a.msg_id}-{a.nombre}"
    await client.download_media(msg, file=str(tmp))
    final = _libre(dir_entrada / a.nombre)
    os.rename(tmp, final)
    os.chmod(final, 0o664)
    return final.name


async def procesar(client, con, cfg, aj: Ajustes, canal: str, msg) -> str | None:
    a = adjunto_de(canal, msg)
    if a is None:
        return None
    if con.execute("SELECT 1 FROM telegram_msgs WHERE canal=? AND msg_id=? AND estado <> 'error'",
                   (canal, a.msg_id)).fetchone():
        return None
    estado, doc_id, nota = decidir(con, cfg, aj, a)
    if estado != "descargar":
        registrar(con, a, estado, doc_id, nota=nota)
        return estado
    try:
        archivo = await _descargar(client, msg, cfg, a)
    except Exception as ex:
        from telethon import errors
        if isinstance(ex, errors.FloodWaitError):
            raise
        registrar(con, a, "error", nota=f"{type(ex).__name__}: {ex}"[:300])
        print(f"  error descargando {a.nombre}: {ex}", file=sys.stderr, flush=True)
        return "error"
    registrar(con, a, "descargado", archivo=archivo)
    print(f"  descargado: {archivo} ({a.tamano / 1024 / 1024:.1f} MB)", flush=True)
    return "descargado"


async def revisar_canal(client, con, cfg, aj: Ajustes, ref, desde: datetime | None = None,
                        limite: int | None = None) -> dict:
    entidad = await client.get_entity(_referencia(str(ref)) if isinstance(ref, str) else ref)
    canal = _peer_id(entidad)
    titulo = getattr(entidad, "title", None) or getattr(entidad, "username", None) or canal
    fila = con.execute("SELECT ultimo_id FROM telegram_canales WHERE canal=?", (canal,)).fetchone()
    cuenta: dict[str, int] = {}
    if fila is None and desde is None:
        # Canal nuevo: se empieza desde ahora (la historia, con `historial`).
        ultimos = await client.get_messages(entidad, limit=1)
        ultimo = ultimos[0].id if ultimos else 0
        with con:
            con.execute("INSERT INTO telegram_canales VALUES (?, ?, ?, ?)", (canal, titulo, ultimo, int(time.time())))
        print(f"  {titulo}: empezando desde el mensaje {ultimo}", flush=True)
        return cuenta
    ultimo = fila["ultimo_id"] if fila else 0
    if desde is None:
        # Reintentar descargas fallidas (como mucho 5 veces y una vez por hora).
        fallidos = [r[0] for r in con.execute(
            "SELECT msg_id FROM telegram_msgs WHERE canal=? AND estado='error' AND intentos < 5 "
            "AND procesado_en < ? ORDER BY msg_id LIMIT 10", (canal, int(time.time()) - 3600))]
        if fallidos:
            for msg in await client.get_messages(entidad, ids=fallidos):
                if msg is not None:
                    r = await procesar(client, con, cfg, aj, canal, msg)
                    if r:
                        cuenta[r] = cuenta.get(r, 0) + 1
    if desde is not None:
        mensajes = client.iter_messages(entidad, reverse=True, offset_date=desde, limit=limite)
    else:
        mensajes = client.iter_messages(entidad, reverse=True, min_id=ultimo, limit=limite)
    nuevo_ultimo = ultimo
    async for msg in mensajes:
        r = await procesar(client, con, cfg, aj, canal, msg)
        if r:
            cuenta[r] = cuenta.get(r, 0) + 1
        nuevo_ultimo = max(nuevo_ultimo, msg.id)
        if desde is None:
            with con:
                con.execute("UPDATE telegram_canales SET ultimo_id=? WHERE canal=?", (nuevo_ultimo, canal))
    with con:
        con.execute("INSERT INTO telegram_canales VALUES (?, ?, ?, ?) ON CONFLICT(canal) DO UPDATE SET "
                    "titulo=excluded.titulo, ultimo_id=MAX(ultimo_id, excluded.ultimo_id), "
                    "revisado_en=excluded.revisado_en", (canal, titulo, nuevo_ultimo, int(time.time())))
    if cuenta:
        print(f"  {titulo}: {cuenta}", flush=True)
    return cuenta


def _cliente(cfg, aj: Ajustes):
    from telethon import TelegramClient
    return TelegramClient(str(ruta_sesion(cfg)), aj.api_id, aj.api_hash,
                          device_model="biblioteca-rpg", system_version="homelab", app_version="1.0")


class _Sesion:
    """Conecta SIN pedir teléfono (`async with TelegramClient` lo pediría): exige haber hecho `login`."""

    def __init__(self, cfg, aj: Ajustes):
        self.client = _cliente(cfg, aj)

    async def __aenter__(self):
        await self.client.connect()
        if not await self.client.is_user_authorized():
            await self.client.disconnect()
            raise SinSesion("La sesión de Telegram no está iniciada: ejecuta la orden `login` (ver README).")
        return self.client

    async def __aexit__(self, *exc):
        await self.client.disconnect()


class SinSesion(RuntimeError):
    pass


def _proteger_sesion(cfg) -> None:
    for p in (cfg.datos / "telegram").glob("biblioteca.session*"):
        os.chmod(p, 0o600)


async def _vigilar(cfg, aj: Ajustes) -> None:
    from telethon import errors

    con = conectar(cfg.db)
    limpiar_parciales(cfg)
    async with _Sesion(cfg, aj) as client:
        _proteger_sesion(cfg)
        print(f"Telegram: vigilando {len(aj.canales)} canales cada {aj.cada_min:g} min", flush=True)
        while True:
            for ref in aj.canales:
                try:
                    await revisar_canal(client, con, cfg, aj, ref)
                except errors.FloodWaitError as ex:
                    print(f"  Telegram pide esperar {ex.seconds} s", flush=True)
                    await asyncio.sleep(ex.seconds + 5)
                except Exception as ex:
                    print(f"  error con el canal {ref}: {type(ex).__name__}: {ex}", file=sys.stderr, flush=True)
            await asyncio.sleep(aj.cada_min * 60)


async def _login(cfg, aj: Ajustes) -> None:
    client = _cliente(cfg, aj)
    await client.start()  # pide teléfono, código y, si la hay, la contraseña de dos pasos
    yo = await client.get_me()
    print(f"Sesión iniciada como {yo.first_name or ''} (@{yo.username or '-'}). Guardada en {ruta_sesion(cfg)}.session")
    await client.disconnect()
    _proteger_sesion(cfg)


async def _canales(cfg, aj: Ajustes) -> None:
    async with _Sesion(cfg, aj) as client:
        print(f"{'ID':>16}  {'TIPO':8}  NOMBRE")
        async for d in client.iter_dialogs():
            if d.is_group or d.is_channel:
                tipo = "canal" if d.is_channel and not d.is_group else "grupo"
                usuario = getattr(d.entity, "username", None)
                print(f"{d.id:>16}  {tipo:8}  {d.name}{f'  (@{usuario})' if usuario else ''}")


async def _historial(cfg, aj: Ajustes, canal: str, desde: datetime, limite: int | None) -> None:
    con = conectar(cfg.db)
    async with _Sesion(cfg, aj) as client:
        r = await revisar_canal(client, con, cfg, aj, canal, desde=desde, limite=limite)
        print("Historial:", r or "nada nuevo")


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description="Descarga de Telegram a la carpeta de entrada.")
    ap.add_argument("orden", choices=["login", "canales", "vigilar", "historial"])
    ap.add_argument("--canal")
    ap.add_argument("--desde", help="AAAA-MM-DD (historial)")
    ap.add_argument("--limite", type=int)
    args = ap.parse_args(argv)
    cfg = config_mod.cargar()
    aj = Ajustes.cargar()
    if not aj.configurado:
        msg = "Falta TELEGRAM_API_ID / TELEGRAM_API_HASH en el .env (se piden en https://my.telegram.org)."
        if args.orden == "vigilar":
            # Sin configurar: el servicio espera sin reiniciarse en bucle.
            print(msg + " En espera.", flush=True)
            while True:
                time.sleep(3600)
        sys.exit(msg)
    if args.orden == "login":
        asyncio.run(_login(cfg, aj))
    elif args.orden == "canales":
        try:
            asyncio.run(_canales(cfg, aj))
        except SinSesion as ex:
            sys.exit(str(ex))
    elif args.orden == "historial":
        if not args.canal or not args.desde:
            sys.exit("historial necesita --canal y --desde AAAA-MM-DD")
        desde = datetime.strptime(args.desde, "%Y-%m-%d").replace(tzinfo=UTC)
        asyncio.run(_historial(cfg, aj, args.canal, desde, args.limite))
    else:
        if not aj.canales:
            print("No hay canales en TELEGRAM_CANALES. En espera.", flush=True)
            while True:
                time.sleep(3600)
        while True:
            try:
                asyncio.run(_vigilar(cfg, aj))
            except SinSesion as ex:
                print(f"{ex} Reintento en 10 min.", flush=True)
            except Exception as ex:
                print(f"Error con Telegram: {type(ex).__name__}: {ex}. Reintento en 10 min.", file=sys.stderr, flush=True)
            time.sleep(600)


if __name__ == "__main__":
    main()
