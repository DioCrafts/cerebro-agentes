"""Online backups (plain and encrypted, with key rotation) and revoking every session."""

from __future__ import annotations

import sqlite3
import stat

from click.testing import CliRunner
import pytest

from asistente.nucleo.decision.state_store_sqlite import SQLiteCaseStateStore
from asistente.plataforma import settings
from asistente.plataforma.auth.store import AuthStore
from asistente.plataforma.cli.plataforma import cli
from asistente.plataforma.operaciones import (
    SUFIJO_CIFRADO,
    CopiaError,
    copiar,
    descifrar,
    nueva_clave,
    recifrar,
    revocar_sesiones,
)
from tests.proactive import make_case


@pytest.fixture
def base(tmp_path) -> str:
    path = str(tmp_path / "casos.sqlite3")
    store = SQLiteCaseStateStore(path)
    store.save_case(make_case("caso-secreto-77"))
    # Kept open while the copy is taken: the online backup does not need the writers to stop.
    return path


def _casos(path: str) -> list[str]:
    conn = sqlite3.connect(path)
    try:
        return [f[0] for f in conn.execute("SELECT case_id FROM case_states")]
    finally:
        conn.close()


def test_a_plain_backup_is_a_consistent_private_copy(base: str, tmp_path) -> None:
    copia = copiar(base, str(tmp_path / "copias"))
    assert copia.suffix == ".sqlite3" and _casos(str(copia)) == ["caso-secreto-77"]
    assert stat.S_IMODE(copia.stat().st_mode) == 0o600
    assert not list((tmp_path / "copias").glob(".*"))  # no partial file left behind


def test_an_encrypted_backup_never_leaves_plain_data_and_survives_key_rotation(base: str, tmp_path) -> None:
    vieja, nueva = nueva_clave(), nueva_clave()
    copia = copiar(base, str(tmp_path / "copias"), clave=vieja)
    assert copia.name.endswith(".sqlite3" + SUFIJO_CIFRADO)
    assert [p.name for p in (tmp_path / "copias").iterdir()] == [copia.name]
    assert b"SQLite format" not in copia.read_bytes() and b"caso-secreto-77" not in copia.read_bytes()
    with pytest.raises(CopiaError):
        descifrar(str(copia), str(tmp_path / "x.sqlite3"), clave=nueva)
    # Rotation: the new key first, the old one after it until every copy is re-encrypted.
    recifrar(str(copia), clave=f"{nueva},{vieja}")
    plano = descifrar(str(copia), str(tmp_path / "restaurada.sqlite3"), clave=nueva)
    assert _casos(str(plano)) == ["caso-secreto-77"]


def test_a_missing_database_or_a_bad_key_is_refused(tmp_path) -> None:
    with pytest.raises(CopiaError):
        copiar(str(tmp_path / "no.sqlite3"), str(tmp_path / "copias"))
    with pytest.raises(CopiaError):
        copiar(str(tmp_path / "no.sqlite3"), str(tmp_path / "copias"), clave="no-es-una-clave")


def test_revoking_sessions_logs_everyone_out(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "AUTH_SCRYPT_N", 2**8)
    path = str(tmp_path / "casos.sqlite3")
    auth = AuthStore(path)
    usuario = auth.create_user("ana@example.com", "una-clave-larga-1")
    token, _sesion = auth.create_session(usuario.id)
    assert auth.get_session_user(token) is not None
    assert revocar_sesiones(path) == 1
    assert auth.get_session_user(token) is None
    assert revocar_sesiones(str(tmp_path / "sin_cuentas.sqlite3")) == 0


def test_the_cli_requires_a_key_unless_told_otherwise(base: str, tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    runner = CliRunner()
    monkeypatch.delenv("PLATAFORMA_CLAVE_COPIAS", raising=False)
    destino = str(tmp_path / "copias")
    assert runner.invoke(cli, ["copia", "--db-path", base, "--destino", destino]).exit_code != 0
    resultado = runner.invoke(cli, ["copia", "--db-path", base, "--destino", destino, "--sin-cifrar"])
    assert resultado.exit_code == 0 and resultado.output.strip().endswith(".sqlite3")
    monkeypatch.setenv("PLATAFORMA_CLAVE_COPIAS", runner.invoke(cli, ["nueva-clave"]).output.strip())
    resultado = runner.invoke(cli, ["copia", "--db-path", base, "--destino", destino])
    assert resultado.exit_code == 0 and resultado.output.strip().endswith(SUFIJO_CIFRADO)
    cifrada = resultado.output.strip()
    resultado = runner.invoke(cli, ["descifrar-copia", cifrada, str(tmp_path / "r.sqlite3")])
    assert resultado.exit_code == 0 and _casos(str(tmp_path / "r.sqlite3")) == ["caso-secreto-77"]


def test_copias_privadas_desde_el_primer_byte(tmp_path, monkeypatch) -> None:
    """Even with umask 0, no copy (plain, encrypted or decrypted) is ever readable by others, not even while
    it is being written: the file is created 0600 before SQLite or the encrypted bytes touch it."""
    import os
    import sqlite3 as _sqlite3
    import stat

    from asistente.plataforma import operaciones as ops

    origen = tmp_path / "casos.sqlite3"
    with _sqlite3.connect(origen) as conn:
        conn.execute("CREATE TABLE t (x)")
    modos: list[int] = []
    conectar = _sqlite3.connect

    def espia(ruta, *a, **k):
        if str(ruta) != str(origen) and os.path.exists(ruta):
            modos.append(stat.S_IMODE(os.stat(ruta).st_mode))
        return conectar(ruta, *a, **k)

    monkeypatch.setattr(ops.sqlite3, "connect", espia)
    anterior = os.umask(0)
    try:
        plana = ops.copiar(str(origen), str(tmp_path / "copias"))
        clave = ops.nueva_clave()
        cifrada = ops.copiar(str(origen), str(tmp_path / "copias"), clave=clave)
        descifrada = ops.descifrar(str(cifrada), str(tmp_path / "fuera" / "x.sqlite3"), clave=clave)
    finally:
        os.umask(anterior)
    assert modos and set(modos) == {0o600}
    for ruta in (plana, cifrada, descifrada):
        assert stat.S_IMODE(os.stat(ruta).st_mode) == 0o600, ruta
    assert stat.S_IMODE(os.stat(tmp_path / "copias").st_mode) == 0o700
