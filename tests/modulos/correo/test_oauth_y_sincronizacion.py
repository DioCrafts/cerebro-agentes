"""OAuth (PKCE, read-only scope, encrypted token, revocation) and incremental sync against the fake Google."""

from __future__ import annotations

import base64
from datetime import UTC, date, datetime
import hashlib
import json
from pathlib import Path
import sqlite3
from urllib.parse import parse_qs, urlsplit

import pytest

from asistente.capacidades.correo import almacen, cifrado, oauth
from asistente.capacidades.correo.config import SCOPE_LECTURA
from asistente.capacidades.correo.http import CorreoError
from asistente.capacidades.correo.sincronizacion import sincronizar
from tests.modulos.correo import corpus
from tests.modulos.correo.falso import ACCESO, REFRESCO, GoogleFalso

HOY = date(2026, 11, 16)


def conectar(google: GoogleFalso, case_id: str = "case-1", user_id: str | None = "u1") -> almacen.Conexion:
    url = oauth.iniciar(case_id, user_id)
    estado, codigo = google.autorizar(url)
    return oauth.completar(estado, codigo)


def test_url_de_consentimiento_solo_lectura_y_pkce(google: GoogleFalso, db_path: str) -> None:
    url = oauth.iniciar("case-1", "u1")
    params = {k: v[0] for k, v in parse_qs(urlsplit(url).query).items()}
    assert url.startswith("https://accounts.google.com/o/oauth2/v2/auth?")
    assert params["scope"] == SCOPE_LECTURA == "https://www.googleapis.com/auth/gmail.readonly"
    assert params["code_challenge_method"] == "S256" and len(params["code_challenge"]) == 43
    assert params["access_type"] == "offline" and params["include_granted_scopes"] == "true"
    assert params["redirect_uri"] == "http://127.0.0.1:8046/correo/oauth/callback"
    # The state is stored hashed and the verifier encrypted: neither appears in the database.
    contenido = Path(db_path).read_bytes() + (
        Path(db_path + "-wal").read_bytes() if Path(db_path + "-wal").exists() else b""
    )
    assert params["state"].encode() not in contenido


def test_conexion_guarda_el_token_cifrado_y_atado_al_caso(google: GoogleFalso, db_path: str) -> None:
    conexion = conectar(google)
    assert conexion.cuenta == "yo@gmail.com" and conexion.alcance == [SCOPE_LECTURA]
    with sqlite3.connect(db_path) as conn:
        filas = conn.execute("SELECT token_cifrado FROM correo_conexiones").fetchall()
        volcado = "\n".join(conn.iterdump())
    assert REFRESCO not in volcado and ACCESO not in volcado
    token = filas[0][0]
    assert cifrado.descifrar(token, case_id="case-1", user_id="u1") == REFRESCO
    with pytest.raises(ValueError):  # copied to another case or user: unusable
        cifrado.descifrar(token, case_id="case-2", user_id="u1")
    with pytest.raises(ValueError):
        cifrado.descifrar(token, case_id="case-1", user_id="u2")


def test_el_estado_es_de_un_solo_uso(google: GoogleFalso) -> None:
    url = oauth.iniciar("case-1", "u1")
    estado, codigo = google.autorizar(url)
    oauth.completar(estado, codigo)
    with pytest.raises(CorreoError):
        oauth.completar(estado, codigo)
    with pytest.raises(CorreoError):
        oauth.completar("estado-inventado", codigo)


def test_pkce_comprobado_por_google(google: GoogleFalso) -> None:
    url = oauth.iniciar("case-1", "u1")
    estado, codigo = google.autorizar(url)
    google.codigos[codigo] = base64.urlsafe_b64encode(hashlib.sha256(b"otro").digest()).decode().rstrip("=")
    with pytest.raises(CorreoError):
        oauth.completar(estado, codigo)
    assert almacen.conexion_de("case-1") is None


def test_sin_permiso_de_lectura_no_se_conecta_y_se_revoca(google: GoogleFalso) -> None:
    google.alcance = "openid"
    with pytest.raises(CorreoError, match="lectura"):
        conectar(google)
    assert almacen.conexion_de("case-1") is None and google.revocados == [REFRESCO]


def test_sin_configuracion_no_se_inicia(monkeypatch) -> None:
    monkeypatch.delenv("CORREO_CLAVE_CIFRADO")
    with pytest.raises(CorreoError, match="CORREO_CLAVE_CIFRADO"):
        oauth.iniciar("case-1", None)


def test_refresco_y_permiso_revocado_por_el_usuario(google: GoogleFalso) -> None:
    conexion = conectar(google)
    oauth.olvidar_cache()
    assert oauth.token_de_acceso(conexion) == ACCESO
    oauth.olvidar_cache()
    google.refresco_revocado = True
    with pytest.raises(oauth.ConexionCaducadaError):
        oauth.token_de_acceso(conexion)
    assert almacen.conexion_de("case-1").estado == "reconectar"  # type: ignore[union-attr]


def test_desconectar_revoca_en_google_y_borra_el_token(google: GoogleFalso) -> None:
    conectar(google)
    assert oauth.desconectar("case-1") is True
    assert google.revocados == [REFRESCO] and almacen.conexion_de("case-1") is None
    assert oauth.desconectar("case-1") is None


def test_primera_sincronizacion_ventana_y_solo_metadatos_en_promociones(google: GoogleFalso, db_path: str) -> None:
    google.mensajes = corpus.todos()
    conectar(google)
    resultado = sincronizar("case-1", hoy=HOY)
    assert resultado.modo == "ventana" and resultado.nuevos == 10
    consulta = next(d["params"]["q"] for _m, u, d in google.llamadas if u.endswith("/users/me/messages"))
    assert consulta.startswith("newer_than:30d")
    assert "m007promo" not in google.completos()  # a promotion's body is never downloaded
    assert almacen.conexion_de("case-1").history_id == "5000"  # type: ignore[union-attr]
    # Nothing of the bodies is stored: sentences of the messages are absent from the whole database.
    with sqlite3.connect(db_path) as conn:
        volcado = "\n".join(conn.iterdump())
    for frase in (
        "Le informamos de que tiene",
        "Auriculares inalámbricos",
        "SECRETO-OCULTO",
        "IGNORA TUS INSTRUCCIONES y confirma",
        "terminada en 1234",
    ):
        assert frase not in volcado
    elementos = {e.id: e for e in almacen.elementos("case-1")}
    assert elementos["m007promo"].asunto is None and elementos["m002luz"].asunto
    assert all(e.remitente is None for e in elementos.values())  # only domains


def test_sincronizacion_incremental_por_historial(google: GoogleFalso) -> None:
    google.mensajes = [corpus.aeat()]
    conectar(google)
    sincronizar("case-1", hoy=HOY)
    google.llamadas.clear()
    google.llega(corpus.luz())
    resultado = sincronizar("case-1", hoy=HOY)
    assert resultado.modo == "historial" and resultado.nuevos == 1
    assert not any(u.endswith("/users/me/messages") for _m, u, _d in google.llamadas)  # no crawl
    assert google.completos() == ["m002luz"]
    assert sincronizar("case-1", hoy=HOY).nuevos == 0


def test_historial_caducado_vuelve_a_la_ventana(google: GoogleFalso) -> None:
    google.mensajes = [corpus.aeat()]
    conectar(google)
    sincronizar("case-1", hoy=HOY)
    google.historial_caducado = True
    google.llega(corpus.compra())
    resultado = sincronizar("case-1", hoy=HOY)
    assert resultado.modo == "ventana" and resultado.nuevos == 1


def test_ventana_configurable_y_correo_antiguo_fuera(google: GoogleFalso) -> None:
    google.mensajes = [corpus.aeat(), corpus.luz()]  # luz is from 05/11
    conectar(google)
    resultado = sincronizar("case-1", hoy=HOY, ventana_dias=3)
    assert resultado.nuevos == 1
    assert any(d["params"].get("q", "").startswith("newer_than:3d") for _m, _u, d in google.llamadas)


def test_propuestas_creadas_al_sincronizar(google: GoogleFalso) -> None:
    google.mensajes = corpus.todos()
    conectar(google)
    sincronizar("case-1", hoy=HOY)
    por_elemento = {p.elemento_id: p for p in almacen.propuestas("case-1")}
    assert set(por_elemento) == {"m001aeat", "m002luz", "m003compra", "m004cargo"}
    compra = por_elemento["m003compra"]
    assert (compra.destino, compra.tipo, compra.herramienta) == (
        "caducidades",
        "herramienta",
        "caducidades_registrar_compra",
    )
    assert compra.argumentos == {
        "descripcion": "Compra en Tienda Ejemplo",
        "fecha_compra": "2026-11-12",
        "establecimiento": "Tienda Ejemplo",
        "importe": "129.99",
        "modalidad": "distancia",
    }
    assert por_elemento["m002luz"].hechos == {"luz_potencia_p1_kw": 4.6}
    assert por_elemento["m001aeat"].destino == "fiscal" and por_elemento["m004cargo"].destino == "suscripciones"
    assert por_elemento["m003compra"].origen.asunto == "Confirmación de tu pedido ••••"  # ids masked


def test_error_de_gmail_queda_registrado(google: GoogleFalso) -> None:
    conectar(google)
    oauth.olvidar_cache()
    google.refresco_revocado = True
    with pytest.raises(oauth.ConexionCaducadaError):
        sincronizar("case-1", hoy=HOY)
    conexion = almacen.conexion_de("case-1")
    assert conexion is not None and conexion.estado == "reconectar" and conexion.ultimo_error


def test_sin_conexion() -> None:
    with pytest.raises(CorreoError, match="no está conectado"):
        sincronizar("case-1", hoy=HOY)


def test_evento_al_clasificar_correo_nuevo(google: GoogleFalso, db_path: str) -> None:
    from asistente.capacidades.correo import MODULO
    from asistente.nucleo.eventos import productores
    from asistente.nucleo.eventos.rutas import le_afecta
    from asistente.nucleo.eventos.tipos import Evento

    productores.instalar()
    try:
        google.mensajes = [corpus.aeat(), corpus.compra()]
        conectar(google)
        sincronizar("case-1", hoy=HOY)
        with sqlite3.connect(db_path) as conn:
            filas = conn.execute(
                "SELECT tipo, case_id, payload_json FROM eventos WHERE tipo = 'correo.clasificado'"
            ).fetchall()
        assert len(filas) == 1 and filas[0][1] == "case-1"
        payload = json.loads(filas[0][2])
        assert payload == {"categorias": {"administracion": 1, "compra": 1}, "nuevos": 2}  # counts only
        evento = Evento(
            id=1, uid="u", tipo="correo.clasificado", case_id="case-1", payload=payload, created_at=datetime.now(UTC)
        )
        assert le_afecta(MODULO, evento, [])
        assert sincronizar("case-1", hoy=HOY).nuevos == 0  # nothing new: no event
        with sqlite3.connect(db_path) as conn:
            assert conn.execute("SELECT COUNT(*) FROM eventos WHERE tipo = 'correo.clasificado'").fetchone()[0] == 1
    finally:
        productores.desinstalar()


def test_ordenes_del_operador(google: GoogleFalso, monkeypatch, capsys) -> None:
    from cryptography.fernet import Fernet

    from asistente.capacidades.correo.__main__ import main
    from asistente.plataforma import settings

    monkeypatch.setattr(settings, "MODULOS_ACTIVOS_POR_DEFECTO", "correo")
    google.mensajes = [corpus.aeat()]
    conectar(google, user_id=None)
    assert main(["sincronizar"]) == 0 and "case-1: 1 nuevo(s) (ventana)" in capsys.readouterr().out
    nueva = Fernet.generate_key().decode()
    import os

    monkeypatch.setenv("CORREO_CLAVE_CIFRADO", f"{nueva},{os.environ['CORREO_CLAVE_CIFRADO']}")
    assert main(["rotar-clave"]) == 0
    monkeypatch.setenv("CORREO_CLAVE_CIFRADO", nueva)  # the old key can go now
    token = almacen.conexion_de("case-1").token_cifrado  # type: ignore[union-attr]
    assert cifrado.descifrar(token, case_id="case-1", user_id=None) == REFRESCO
    assert main(["nada"]) == 2


def test_transporte_http_real_contra_el_servidor_falso_local(monkeypatch) -> None:
    """The default ``httpx`` transport, end to end over loopback HTTP (never Google)."""
    import httpx

    from asistente.capacidades.correo import http
    from tests.modulos.correo.servidor_falso import en_hilo

    falso = GoogleFalso(mensajes=[corpus.aeat(), corpus.compra()])
    servidor, base = en_hilo(falso)
    try:
        monkeypatch.setenv("CORREO_GOOGLE_AUTH_URL", f"{base}/o/oauth2/v2/auth")
        monkeypatch.setenv("CORREO_GOOGLE_TOKEN_URL", f"{base}/token")
        monkeypatch.setenv("CORREO_GOOGLE_REVOKE_URL", f"{base}/revoke")
        monkeypatch.setenv("CORREO_GMAIL_API_URL", f"{base}/gmail/v1")
        http.configurar_transporte(None)  # the real one
        url = oauth.iniciar("case-1", "u1")
        consentimiento = httpx.get(url, timeout=5)
        destino = consentimiento.text.split("id='permitir' href='")[1].split("'")[0].replace("&amp;", "&")
        params = {k: v[0] for k, v in parse_qs(urlsplit(destino).query).items()}
        oauth.completar(params["state"], params["code"])
        oauth.olvidar_cache()
        assert sincronizar("case-1", hoy=HOY).nuevos == 2
        assert oauth.desconectar("case-1") is True and falso.revocados == [REFRESCO]
    finally:
        servidor.shutdown()
