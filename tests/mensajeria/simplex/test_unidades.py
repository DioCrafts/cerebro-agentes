"""Unit tests: configuration gate, command strings, tolerant models, version range, text splitting, file checks."""

from __future__ import annotations

import json
import os
from pathlib import Path
import stat

import pytest

from asistente.plataforma.mensajeria.contrato import MAX_ADJUNTO_BYTES
from asistente.plataforma.mensajeria.simplex import CanalSimplex, dividir_texto
from asistente.plataforma.mensajeria.simplex import protocolo as p
from asistente.plataforma.mensajeria.simplex.archivos import (
    ArchivoRechazado,
    RecepcionArchivos,
    detectar_tipo,
    nombre_saneado,
)
from asistente.plataforma.mensajeria.simplex.canal import PRESUPUESTO_BYTES, bytes_json
from asistente.plataforma.mensajeria.simplex.configuracion import (
    ConfiguracionSimplex,
    ConfiguracionSimplexInvalida,
    url_para_mostrar,
)

# --- configuration -------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "url", ["ws://127.0.0.1:5225", "ws://localhost:5225", "ws://[::1]:5225", "wss://127.0.0.1:5225", "ws://127.0.0.2"]
)
def test_urls_locales_admitidas(url):
    assert ConfiguracionSimplex(ws_url=url).ws_url == url


@pytest.mark.parametrize("url", ["ws://192.168.1.10:5225", "ws://bot.ejemplo.org:5225", "wss://bot.ejemplo.org/ws"])
def test_url_remota_rechazada_sin_permiso(url):
    with pytest.raises(ConfiguracionSimplexInvalida, match="otra máquina"):
        ConfiguracionSimplex(ws_url=url)
    with pytest.raises(ConfiguracionSimplexInvalida):
        CanalSimplex(ConfiguracionSimplex.model_construct(ws_url=url))  # the channel checks again (defence in depth)


def test_url_remota_solo_con_permiso_y_wss():
    with pytest.raises(ConfiguracionSimplexInvalida, match="wss://"):
        ConfiguracionSimplex(ws_url="ws://bot.ejemplo.org:5225", permitir_remoto=True)
    cfg = ConfiguracionSimplex(ws_url="wss://usuario:clave@bot.ejemplo.org/ws", permitir_remoto=True)
    assert cfg.destino == "bot.ejemplo.org" and "clave" not in cfg.destino


@pytest.mark.parametrize("url", ["http://127.0.0.1:5225", "127.0.0.1:5225", "ws://:5225", "ws://127.0.0.1:99999"])
def test_url_mal_formada(url):
    with pytest.raises(ConfiguracionSimplexInvalida):
        ConfiguracionSimplex(ws_url=url)


def test_desde_settings(monkeypatch, tmp_path):
    from asistente.plataforma import settings

    monkeypatch.setattr(settings, "SIMPLEX_WS_URL", "ws://127.0.0.1:6000", raising=False)
    monkeypatch.setattr(settings, "SIMPLEX_ARCHIVOS_DIR", str(tmp_path / "a"), raising=False)
    monkeypatch.setattr(settings, "SIMPLEX_ARCHIVOS_MAX_BYTES", MAX_ADJUNTO_BYTES * 4, raising=False)
    monkeypatch.setattr(settings, "SIMPLEX_SERVIDORES_SMP", "smp://a@x.org  smp://b@y.org", raising=False)
    cfg = ConfiguracionSimplex.desde_settings()
    assert cfg.ws_url == "ws://127.0.0.1:6000" and cfg.destino == "127.0.0.1:6000"
    assert cfg.max_bytes_archivo == MAX_ADJUNTO_BYTES  # never above the gateway's limit
    assert cfg.recepcion == tmp_path / "a" / "recepcion"
    assert cfg.servidores_smp == ("smp://a@x.org", "smp://b@y.org")
    assert cfg.autoaceptar is True and cfg.bot_nombre == "Asistente del hogar"
    assert cfg.bienvenida  # the default welcome is set


def test_desde_settings_no_filtra_credenciales(monkeypatch):
    from asistente.plataforma import settings

    monkeypatch.setattr(settings, "SIMPLEX_WS_URL", "ws://usuario:clave-secreta@remoto.org:5225", raising=False)
    with pytest.raises(ConfiguracionSimplexInvalida) as e:
        ConfiguracionSimplex.desde_settings()
    assert "clave-secreta" not in str(e.value)
    monkeypatch.setattr(settings, "SIMPLEX_WS_URL", "ws://127.0.0.1:5225", raising=False)
    monkeypatch.setattr(settings, "SIMPLEX_TIMEOUT_COMANDO_S", -1.0, raising=False)
    with pytest.raises(ConfiguracionSimplexInvalida, match="timeout_comando_s"):
        ConfiguracionSimplex.desde_settings()


def test_url_para_mostrar():
    assert url_para_mostrar("ws://u:p@[::1]:5225/x") == "[::1]:5225"


# --- protocol ------------------------------------------------------------------------------------------------------


def test_ordenes_con_la_sintaxis_documentada():
    assert p.cmd_version() == "/version"
    assert p.cmd_usuario_activo() == "/user" and p.cmd_usuarios() == "/users"
    assert p.cmd_activar_usuario(3) == "/_user 3"
    assert p.cmd_mostrar_direccion(3) == "/_show_address 3" and p.cmd_crear_direccion(3) == "/_address 3"
    assert p.cmd_ajustes_direccion(3, {"businessAddress": False}) == '/_address_settings 3 {"businessAddress":false}'
    assert p.cmd_invitaciones_grupo(3, False) == "/_set accept group invitations 3 off"
    assert p.cmd_servidores(3) == "/_servers 3"
    assert (
        p.cmd_enviar_texto(7, "hola") == '/_send @7 json [{"msgContent":{"type":"text","text":"hola"},"mentions":{}}]'
    )
    assert p.cmd_enviar_texto(7, "hola", 42).endswith(',"quotedItemId":42}]')
    assert (
        p.cmd_editar_texto(7, 42, "hola")
        == '/_update item @7 42 json {"msgContent":{"type":"text","text":"hola"},"mentions":{}}'
    )
    assert p.cmd_recibir_archivo(5, "/r/x.part") == "/freceive 5 approved_relays=on encrypt=off /r/x.part"
    assert p.cmd_cancelar_archivo(5) == "/fcancel 5"
    assert p.cmd_borrar_contacto(7) == "/_delete @7 full"
    nuevo = json.loads(p.cmd_crear_usuario(p.perfil_bot("Bot", archivos=True, voz=False))[len("/_create user ") :])
    assert nuevo["profile"]["peerType"] == "bot" and nuevo["profile"]["preferences"]["voice"] == {"allow": "no"}
    assert set(nuevo) == {"profile", "pastTimestamp", "userChatRelay", "clientService"}


def test_el_texto_no_rompe_la_orden():
    orden = p.cmd_enviar_texto(7, 'línea 1\nlínea "2" \\ 😀 /_delete @1 full')
    assert "\n" not in orden and orden.isascii()
    assert json.loads(orden.split(" json ", 1)[1])[0]["msgContent"]["text"].startswith("línea 1\nlínea")


@pytest.mark.parametrize("valor", [0, -1, True, "7"])
def test_ids_no_validos(valor):
    with pytest.raises(ValueError):
        p.cmd_enviar_texto(valor, "x")  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        p.cmd_recibir_archivo(5, "/a\n/_delete @1")


@pytest.mark.parametrize(
    ("texto", "esperado", "soportada"),
    [
        ("6.4.2.0", (6, 4, 2), False),
        ("6.4.3", (6, 4, 3), True),
        ("6.5.6.1", (6, 5, 6), True),
        ("7.1.4.0", (7, 1, 4), True),
        ("v7.0.0-beta.1", (7, 0, 0), True),
        ("8.0.0.0", (8, 0, 0), False),
        ("basura", None, False),
        (None, None, False),
    ],
)
def test_rango_de_versiones(texto, esperado, soportada):
    assert p.parsear_version(texto) == esperado
    assert p.version_soportada(p.parsear_version(texto)) is soportada


def test_codigos_de_error_sin_contenido():
    resp = {
        "type": "chatCmdError",
        "chatError": {"type": "errorStore", "storeError": {"type": "userContactLinkNotFound"}},
    }
    assert p.codigo_error(resp) == p.ERROR_SIN_DIRECCION
    resp = {
        "type": "chatCmdError",
        "chatError": {"type": "error", "errorType": {"type": "commandError", "message": "txt"}},
    }
    assert p.codigo_error(resp) == "error:commandError"
    assert p.codigo_error({"chatError": {"type": "raro<>"}}) == "raro__"
    assert p.codigo_error({}) == "desconocido"


def test_modelos_tolerantes():
    bruto = {
        "chatInfo": {
            "type": "direct",
            "contact": {"contactId": 3, "profile": {"displayName": "Ana", "nuevo": 1}},
            "x": 1,
        },
        "chatItem": {
            "chatDir": {"type": "directRcv"},
            "meta": {"itemId": 9, "itemTs": "2026-11-19T10:00:00Z", "campoNuevo": [1]},
            "content": {"type": "rcvMsgContent", "msgContent": {"type": "sticker", "otro": True}},
            "file": {"fileId": 1, "fileName": "a.pdf", "fileSize": 3, "fileStatus": {"type": "estadoNuevo"}},
        },
    }
    elemento = p.AElementoChat.model_validate(bruto)
    assert elemento.chat_info.contact is not None and elemento.chat_info.contact.nombre == "Ana"
    assert elemento.chat_item.content.msg_content is not None and elemento.chat_item.content.msg_content.text == ""
    assert elemento.chat_item.meta.item_ts is not None and elemento.chat_item.meta.item_ts.tzinfo is not None


# --- text splitting ------------------------------------------------------------------------------------------------


def test_dividir_texto():
    assert dividir_texto("corto") == ["corto"]
    texto = "\n\n".join(["Uno. " * 300, "Dos. " * 300, "Tres. " * 300])
    partes = dividir_texto(texto, 2000)
    assert len(partes) > 1 and all(len(x) <= 2000 for x in partes)
    emojis = "😀" * 5000  # 4 bytes each: the byte budget decides, not the characters
    partes = dividir_texto(emojis, 4000, PRESUPUESTO_BYTES)
    assert "".join(partes) == emojis and all(bytes_json(x) <= PRESUPUESTO_BYTES for x in partes)
    sin_espacios = "x" * 9000
    assert "".join(dividir_texto(sin_espacios, 4000)) == sin_espacios
    comillas = '"' * 7000  # each one is escaped in JSON: 2 bytes
    assert all(bytes_json(x) <= PRESUPUESTO_BYTES for x in dividir_texto(comillas, 8000))


# --- files ---------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("cabecera", "mime"),
    [
        (b"\xff\xd8\xff\xdb" + b"\0" * 12, "image/jpeg"),
        (b"\x89PNG\r\n\x1a\n" + b"\0" * 8, "image/png"),
        (b"GIF89a" + b"\0" * 10, "image/gif"),
        (b"RIFF\0\0\0\0WEBPVP8 ", "image/webp"),
        (b"%PDF-1.7\n" + b"\0" * 8, "application/pdf"),
        (b"\0\0\0\x18ftypheic\0\0\0\0", "image/heic"),
        (b"\0\0\0\x20ftypM4A \0\0\0\0", "audio/mp4"),
        (b"OggS" + b"\0" * 12, "audio/ogg"),
        (b"RIFF\0\0\0\0WAVEfmt ", "audio/wav"),
    ],
)
def test_detectar_tipo(cabecera, mime):
    tipo = detectar_tipo(cabecera)
    assert tipo is not None and tipo.mime == mime


@pytest.mark.parametrize(
    "cabecera", [b"MZ\x90\0", b"PK\x03\x04", b"#!/bin/sh\n", b"<html>", b"", b"\0\0\0\x18ftypqt  "]
)
def test_tipos_no_permitidos(cabecera):
    assert detectar_tipo(cabecera + b"\0" * 16) is None


@pytest.mark.parametrize(
    ("nombre", "extension", "esperado"),
    [
        ("recibo.pdf", ".pdf", "recibo.pdf"),
        ("../../etc/passwd.pdf", ".pdf", "passwd.pdf"),
        ("..\\..\\x.pdf", ".pdf", "x.pdf"),
        ("..", ".pdf", "adjunto.pdf"),
        ("", ".jpg", "adjunto.jpg"),
        ("foto.exe", ".jpg", "foto.jpg"),
        ("a\x00b‮c.pdf", ".pdf", "abc.pdf"),
        ("nómina $ mayo;rm -rf.pdf", ".pdf", "nómina _ mayo_rm -rf.pdf"),
        (".bashrc", ".pdf", "bashrc.pdf"),
    ],
)
def test_nombre_saneado(nombre, extension, esperado):
    assert nombre_saneado(nombre, extension) == esperado


def _recepcion(tmp_path: Path, **kw) -> RecepcionArchivos:
    r = RecepcionArchivos(
        tmp_path / "a", tmp_path / "a" / "recepcion", kw.get("max_bytes", 10_000), kw.get("voz", True)
    )
    assert r.preparar() is None
    return r


def test_admitir(tmp_path):
    r = _recepcion(tmp_path)
    assert r.admitir("file", "a.PDF", 10) == "pdf"
    assert r.admitir("image", "x", 10) == "imagen"
    for args, motivo in [
        (("file", "a.pdf", 10_001), "demasiado_grande"),
        (("file", "a.pdf", 0), "tamano_desconocido"),
        (("file", "a.docx", 10), "tipo_no_permitido"),
        (("video", "a.mp4", 10), "tipo_no_permitido"),
    ]:
        with pytest.raises(ArchivoRechazado) as e:
            r.admitir(*args)
        assert e.value.motivo == motivo
    sin_voz = RecepcionArchivos(tmp_path / "b", tmp_path / "b" / "r", 10_000, False)
    sin_voz.preparar()
    with pytest.raises(ArchivoRechazado):
        sin_voz.admitir("voice", "v.m4a", 10)


def test_finalizar_y_permisos(tmp_path):
    r = _recepcion(tmp_path)
    temporal = r.ruta_temporal()
    contenido = b"%PDF-1.4\n" + b"z" * 100
    temporal.write_bytes(contenido)
    adjunto = r.finalizar(temporal, "../informe.pdf", len(contenido), "pdf")
    ruta = Path(adjunto.ruta_local)
    assert ruta.read_bytes() == contenido and ruta.name == "informe.pdf" and adjunto.nombre == "informe.pdf"
    assert stat.S_IMODE(ruta.stat().st_mode) == 0o600 and stat.S_IMODE(ruta.parent.stat().st_mode) == 0o700
    assert not temporal.exists()


@pytest.mark.parametrize("caso", ["enlace", "duro", "tamano", "tipo", "ruta", "falta"])
def test_finalizar_rechaza(tmp_path, caso):
    r = _recepcion(tmp_path)
    temporal = r.ruta_temporal()
    contenido = b"%PDF-1.4\n" + b"z" * 100
    fuera = tmp_path / "fuera.pdf"
    fuera.write_bytes(contenido)
    declarado = len(contenido)
    if caso == "enlace":
        os.symlink(fuera, temporal)
    elif caso == "duro":
        os.link(fuera, temporal)
    elif caso == "tamano":
        temporal.write_bytes(contenido)
        declarado -= 1
    elif caso == "tipo":
        temporal.write_bytes(b"MZ" + b"\0" * 107)
    elif caso == "ruta":
        temporal = tmp_path / "otro.part"
        temporal.write_bytes(contenido)
    with pytest.raises(ArchivoRechazado):
        r.finalizar(temporal, "a.pdf", declarado, "pdf")
    assert [x for x in (tmp_path / "a").iterdir() if x.name != "recepcion"] == []  # nothing accepted
    assert fuera.read_bytes() == contenido  # the target of a link is never touched


def test_preparar_corrige_permisos_y_rechaza_enlaces(tmp_path):
    carpeta = tmp_path / "a"
    carpeta.mkdir(mode=0o755)
    carpeta.chmod(0o755)
    r = RecepcionArchivos(carpeta, carpeta / "recepcion", 100, True)
    assert r.preparar() is None
    assert stat.S_IMODE(carpeta.stat().st_mode) == 0o700
    (tmp_path / "real").mkdir()
    os.symlink(tmp_path / "real", tmp_path / "enlace")
    r = RecepcionArchivos(tmp_path / "enlace", tmp_path / "enlace" / "recepcion", 100, True)
    assert r.preparar() is not None and not r.disponible
    with pytest.raises(ArchivoRechazado):
        r.admitir("file", "a.pdf", 10)


def test_limpieza_de_restos(tmp_path):
    r = _recepcion(tmp_path)
    viejo = r.ruta_temporal()
    viejo.write_bytes(b"x")
    nuevo = r.ruta_temporal()
    nuevo.write_bytes(b"x")
    ajeno = r.recepcion_dir / "ajeno.part"
    ajeno.write_bytes(b"x")
    vacia = tmp_path / "a" / ("0" * 32)
    vacia.mkdir()
    llena = tmp_path / "a" / ("1" * 32)
    llena.mkdir()
    (llena / "doc.pdf").write_bytes(b"%PDF-")
    antiguo = 1_000_000_000
    for ruta in (viejo, ajeno, vacia, llena):
        os.utime(ruta, (antiguo, antiguo))
    assert r.limpiar(3600) == 2
    assert not viejo.exists() and not vacia.exists()
    assert nuevo.exists() and ajeno.exists() and llena.exists()  # recent, not ours, or not empty


def test_canal_desde_settings(monkeypatch, tmp_path):
    from asistente.plataforma import settings

    monkeypatch.setattr(settings, "SIMPLEX_ARCHIVOS_DIR", str(tmp_path / "a"), raising=False)
    canal = CanalSimplex.desde_settings()
    assert canal.estado().detalle == "Sin iniciar."
    monkeypatch.setattr(settings, "SIMPLEX_WS_URL", "ws://10.0.0.5:5225", raising=False)
    with pytest.raises(ConfiguracionSimplexInvalida):
        CanalSimplex.desde_settings()
