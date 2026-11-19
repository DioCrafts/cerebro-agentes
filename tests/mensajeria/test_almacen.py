"""The gateway's store: migrations (and the same schema without them), erasure with the case and the account through
the triggers, single-use redemption under concurrency, and the retention purge."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
import sqlite3
import threading

import pytest

from asistente.nucleo.decision.state_store_sqlite import SQLiteCaseStateStore
from asistente.plataforma import settings
from asistente.plataforma.auth.store import AuthStore
from asistente.plataforma.mensajeria.almacen import AlmacenMensajeria, borrar_mensajeria_de_usuario, huella
from asistente.plataforma.mensajeria.emparejamiento import huella_codigo, normalizar, nuevo_codigo
from asistente.plataforma.migraciones import migrar
from asistente.plataforma.migraciones.catalogo import mensajeria as esquema
from tests.cerebro.fakes import caso


def _objetos(path: str) -> dict[str, str]:
    with sqlite3.connect(path) as conn:
        return {
            nombre: " ".join((sql or "").split())
            for nombre, sql in conn.execute(
                "SELECT name, sql FROM sqlite_master WHERE name LIKE '%mensajeria%' "
                "AND name NOT LIKE 'sqlite_autoindex_%' ORDER BY name"
            )
        }


@pytest.fixture
def ruta(tmp_path, entorno_mensajeria: None) -> str:
    path = str(tmp_path / "estado.sqlite3")
    SQLiteCaseStateStore(path).close()
    auth = AuthStore(path)
    auth.close()
    return path


def _vincular(store: AlmacenMensajeria, *, contacto: str, user_id: str | None, case_id: str) -> str:
    ahora = datetime.now(UTC)
    codigo = nuevo_codigo(store, canal="prueba", user_id=user_id, case_id=case_id, momento=ahora)
    normal = normalizar(codigo.codigo)
    assert normal is not None
    resultado = store.emparejar(
        huella_codigo=huella_codigo(normal),
        canal="prueba",
        contacto=contacto,
        nombre_mostrado=None,
        maximo_por_usuario=5,
        momento=ahora,
    )
    assert resultado.vinculo is not None
    return resultado.vinculo.id


def test_the_migration_and_the_store_without_migrations_build_the_same_schema(
    tmp_path, monkeypatch: pytest.MonkeyPatch, entorno_mensajeria: None
) -> None:
    migrada = str(tmp_path / "migrada.sqlite3")
    migrar(migrada)
    monkeypatch.setattr(settings, "MIGRACIONES_AUTOMATICAS", False)
    a_mano = str(tmp_path / "a_mano.sqlite3")
    SQLiteCaseStateStore(a_mano).close()
    AuthStore(a_mano)._get_conn()
    AlmacenMensajeria(a_mano).vinculos()
    assert _objetos(migrada) == _objetos(a_mano)
    assert set(esquema.PROPIETARIO.tablas) | set(esquema.PROPIETARIO.otros_objetos) == set(_objetos(migrada))


def test_erasing_a_case_or_an_account_erases_its_chats_and_queues_their_removal(ruta: str) -> None:
    casos = SQLiteCaseStateStore(ruta)
    auth = AuthStore(ruta)
    store = AlmacenMensajeria(ruta)
    try:
        usuario = auth.create_user("ana@example.com", "una-clave-larga-1", consents={"tratamiento_datos": True})
        casos.save_case(caso())
        casos.save_case(caso().model_copy(update={"case_id": "case-2"}))
        de_caso = _vincular(store, contacto="c1", user_id=None, case_id="case-cerebro")
        de_cuenta = _vincular(store, contacto="c2", user_id=usuario.id, case_id="case-2")
        store.terminar_entrega(de_caso, "n1", "entregada", datetime.now(UTC))
        nuevo_codigo(store, canal="prueba", user_id=usuario.id, case_id="case-2", momento=datetime.now(UTC))

        casos.delete_case("case-cerebro")
        assert store.vinculo(de_caso, de_cualquiera=True) is None and store.entregas_de(de_caso) == set()
        assert store.vinculo(de_cuenta, de_cualquiera=True) is not None
        auth.delete_user(usuario.id)
        assert store.vinculo(de_cuenta, de_cualquiera=True) is None
        assert [(d.contacto, d.motivo) for d in store.desconexiones()] == [("c1", "borrado"), ("c2", "borrado")]
        with sqlite3.connect(ruta) as conn:
            assert conn.execute("SELECT COUNT(*) FROM mensajeria_codigos WHERE user_id != ''").fetchone()[0] == 0
    finally:
        store.close()
        auth.close()
        casos.close()


def test_the_explicit_erasure_of_an_account(ruta: str) -> None:
    store = AlmacenMensajeria(ruta)
    try:
        _vincular(store, contacto="c1", user_id="u1", case_id="case-a")
        _vincular(store, contacto="c2", user_id=None, case_id="case-b")  # a case of theirs, paired without accounts
        _vincular(store, contacto="c3", user_id="u2", case_id="case-c")
    finally:
        store.close()
    assert borrar_mensajeria_de_usuario(ruta, user_id="u1", case_ids=["case-b"]) == 2
    store = AlmacenMensajeria(ruta)
    try:
        assert [v.contacto for v in store.vinculos()] == ["c3"]
        assert sorted(d.contacto for d in store.desconexiones()) == ["c1", "c2"]
    finally:
        store.close()


def test_a_code_typed_twice_at_once_pairs_one_chat(ruta: str) -> None:
    store = AlmacenMensajeria(ruta)
    try:
        ahora = datetime.now(UTC)
        codigo = nuevo_codigo(store, canal="prueba", user_id="u1", case_id="case-a", momento=ahora)
        resultados: list[str] = []
        barrera = threading.Barrier(4)

        def canjear(contacto: str) -> None:
            otra = AlmacenMensajeria(ruta)
            try:
                barrera.wait()
                normal = normalizar(codigo.codigo)
                assert normal is not None
                resultados.append(
                    otra.emparejar(
                        huella_codigo=huella_codigo(normal),
                        canal="prueba",
                        contacto=contacto,
                        nombre_mostrado=None,
                        maximo_por_usuario=10,
                        momento=ahora,
                    ).resultado
                )
            finally:
                otra.close()

        hilos = [threading.Thread(target=canjear, args=(f"c{i}",)) for i in range(4)]
        for hilo in hilos:
            hilo.start()
        for hilo in hilos:
            hilo.join(10)
        assert sorted(resultados) == ["invalido", "invalido", "invalido", "ok"]
        assert len(store.vinculos()) == 1
    finally:
        store.close()


def test_the_purge_keeps_what_is_recent(ruta: str) -> None:
    store = AlmacenMensajeria(ruta)
    try:
        ahora = datetime.now(UTC)
        viejo = ahora - timedelta(days=30)
        assert store.marcar_procesado(huella("viejo"), viejo) and store.marcar_procesado(huella("nuevo"), ahora)
        store.anotar_intento("codigo_fallido", "h", viejo)
        store.anotar_intento("codigo_fallido", "h", ahora)
        nuevo_codigo(store, canal="prueba", user_id="u1", case_id="c", momento=viejo)
        resumen = store.purgar(ahora, dias=7)
        assert resumen["procesados"] == 1 and resumen["intentos"] == 1 and resumen["codigos"] == 1
        assert not store.marcar_procesado(huella("nuevo"), ahora)  # still known: a redelivery is still caught
        assert store.marcar_procesado(huella("viejo"), ahora)
    finally:
        store.close()
