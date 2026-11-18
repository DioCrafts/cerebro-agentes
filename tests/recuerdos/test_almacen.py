"""The memory's tables: migrations and the store's own DDL agree, erasure triggers, strict per-owner isolation."""

from __future__ import annotations

from datetime import date
import sqlite3

import pytest

from asistente.nucleo.decision.state_store_sqlite import SQLiteCaseStateStore
from asistente.nucleo.recuerdos.almacen import AlmacenRecuerdos, Fuente, Recuerdo, Titular, tabla_existe
from asistente.plataforma import settings
from asistente.plataforma.auth.store import AuthStore
from asistente.plataforma.migraciones import migrar, volcado
from asistente.plataforma.migraciones.catalogo import PROPIETARIOS
from tests.proactive import make_case
from .conftest import ANA, BEA, T0

TABLAS = {"recuerdos_conversaciones", "recuerdos_mensajes", "recuerdos", "recuerdos_lotes", "recuerdos_interruptor"}


def _objetos(path: str) -> list[tuple[str, ...]]:
    return [f for f in volcado(path) if f[1].startswith(("recuerdos", "idx_recuerdos"))]


def test_the_owner_is_in_the_catalogue_with_every_table() -> None:
    propietario = next(p for p in PROPIETARIOS if p.nombre == "recuerdos")
    assert set(propietario.tablas) == TABLAS
    assert propietario.depende_de == ("decision", "auth")


def test_migrations_and_the_store_without_them_build_the_same_schema(tmp_path, monkeypatch) -> None:
    migrada, propia = str(tmp_path / "m.sqlite3"), str(tmp_path / "p.sqlite3")
    AlmacenRecuerdos(migrada)._get()
    monkeypatch.setattr(settings, "MIGRACIONES_AUTOMATICAS", False)
    SQLiteCaseStateStore(propia).close()
    AuthStore(propia)._get_conn().close()
    AlmacenRecuerdos(propia)._get()
    assert _objetos(migrada) == _objetos(propia) != []
    nombres = {f[1] for f in _objetos(migrada)}
    assert TABLAS <= nombres and {"recuerdos_borrar_con_caso", "recuerdos_borrar_con_usuario"} <= nombres
    # And a full migration of a fresh database creates exactly the same objects.
    completa = str(tmp_path / "c.sqlite3")
    monkeypatch.setattr(settings, "MIGRACIONES_AUTOMATICAS", True)
    migrar(completa)
    assert _objetos(completa) == _objetos(migrada)


def test_without_migrations_the_triggers_wait_for_the_cases_and_accounts_tables(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(settings, "MIGRACIONES_AUTOMATICAS", False)
    path = str(tmp_path / "s.sqlite3")
    store = AlmacenRecuerdos(path)
    store._get()
    assert "recuerdos_borrar_con_caso" not in {f[1] for f in volcado(path)}
    SQLiteCaseStateStore(path).close()
    AuthStore(path)._get_conn().close()
    store._get()
    assert {"recuerdos_borrar_con_caso", "recuerdos_borrar_con_usuario"} <= {f[1] for f in volcado(path)}


def test_tabla_existe_never_creates_the_file(tmp_path) -> None:
    path = tmp_path / "no.sqlite3"
    assert not tabla_existe(str(path)) and not path.exists()
    AlmacenRecuerdos(str(path))._get()
    assert tabla_existe(str(path))


def _recuerdo(id_: str, texto: str = "Tiene una hipoteca con ING") -> Recuerdo:
    fuente = Fuente(conversacion_id="conv-ana-0001", fecha=date(2026, 11, 12), cita="tengo una hipoteca con ING", n=1)
    return Recuerdo(
        id=id_,
        tipo="dato",
        clave="hipoteca.banco",
        texto=texto,
        origen=fuente,
        fuentes=[fuente],
        creado=T0,
        actualizado=T0,
    )


def test_another_owners_conversation_id_writes_into_a_conversation_of_its_own(db: str) -> None:
    store = AlmacenRecuerdos(db)
    store.registrar_turno(ANA, "conv-compartida", fecha=T0, pregunta="Soy Ana", respuesta="Hola Ana")
    # Bea's client sends the same conversation id (guessed or reused): her messages never land in Ana's.
    store.registrar_turno(BEA, "conv-compartida", fecha=T0, pregunta="Soy Bea", respuesta="Hola Bea")
    assert [m.texto for m in store.mensajes(ANA, "conv-compartida")] == ["Soy Ana", "Hola Ana"]
    assert [m.texto for m in store.mensajes(BEA, "conv-compartida")] == ["Soy Bea", "Hola Bea"]
    assert store.conversacion(ANA, "conv-compartida").mensajes == 2  # type: ignore[union-attr]
    # Same case, another user: another owner too.
    otro = Titular(ANA.case_id, "intruso")
    assert store.conversacion(otro, "conv-compartida") is None and store.mensajes(otro, "conv-compartida") == []


def test_items_are_invisible_and_untouchable_across_owners(db: str) -> None:
    store = AlmacenRecuerdos(db)
    store.guardar(ANA, _recuerdo("r_ana000000001"))
    assert store.recuerdo(BEA, "r_ana000000001") is None
    assert store.recuerdos(BEA) == []
    assert store.olvidar(BEA, ["r_ana000000001"]) == 0
    assert store.eliminar(BEA, ["r_ana000000001"]) == 0
    # A write under the same id by another owner never overwrites Ana's row.
    store.guardar(BEA, _recuerdo("r_ana000000001", texto="Texto de Bea"))
    assert store.recuerdo(ANA, "r_ana000000001").texto == "Tiene una hipoteca con ING"  # type: ignore[union-attr]
    assert store.recuerdo(BEA, "r_ana000000001") is None
    assert store.borrar_conversacion(BEA, "conv-ana-0001") is False
    assert store.recuerdo(ANA, "r_ana000000001") is not None


def test_the_switch_is_the_users_across_their_cases(db: str) -> None:
    store = AlmacenRecuerdos(db)
    assert store.activa(ANA)
    store.fijar_activa(ANA, False, T0)
    assert not store.activa(ANA) and not store.activa(Titular("otro-caso-de-ana", "ana"))
    assert store.activa(BEA)
    # Without an account the switch is the case's.
    anonimo = Titular("caso-x", "")
    store.fijar_activa(anonimo, False, T0)
    assert not store.activa(anonimo) and store.activa(Titular("caso-y", ""))


def test_deleting_the_case_erases_its_memory(db: str) -> None:
    casos = SQLiteCaseStateStore(db)
    casos.save_case(make_case("caso-ana"))
    store = AlmacenRecuerdos(db)
    store.registrar_turno(ANA, "conv-ana-0001", fecha=T0, pregunta="Hola", respuesta="Hola")
    store.guardar(ANA, _recuerdo("r_ana000000001"))
    store.registrar_turno(BEA, "conv-bea-0001", fecha=T0, pregunta="Hola", respuesta="Hola")
    assert casos.delete_case("caso-ana")
    with sqlite3.connect(db) as conn:
        for tabla in ("recuerdos", "recuerdos_mensajes", "recuerdos_conversaciones"):
            assert conn.execute(f"SELECT COUNT(*) FROM {tabla} WHERE case_id = 'caso-ana'").fetchone()[0] == 0
    assert store.conversacion(BEA, "conv-bea-0001") is not None


def test_deleting_the_account_erases_its_memory(db: str) -> None:
    auth = AuthStore(db)
    usuario = auth.create_user("ana@example.com", "una-clave-larga-1")
    titular = Titular("", usuario.id)
    store = AlmacenRecuerdos(db)
    store.registrar_turno(titular, "conv-ana-0001", fecha=T0, pregunta="Hola", respuesta="Hola")
    store.fijar_activa(titular, False, T0)
    auth.delete_user(usuario.id)
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT COUNT(*) FROM recuerdos_mensajes").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM recuerdos_interruptor").fetchone()[0] == 0


@pytest.mark.parametrize("pregunta", ["", "   \n  ", "x" * 200])
def test_the_provisional_title_is_short_and_never_empty(db: str, pregunta: str) -> None:
    store = AlmacenRecuerdos(db)
    store.registrar_turno(ANA, "conv-ana-0009", fecha=T0, pregunta=pregunta or " ", respuesta="ok")
    titulo = store.conversacion(ANA, "conv-ana-0009").titulo  # type: ignore[union-attr]
    assert 0 < len(titulo) <= 80
