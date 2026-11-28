"""The three modes of ``NAVEGADOR_CONFINAMIENTO``, and what the macOS profile really allows.

Nothing pinned any of this, and both the ADR and the module's own docstring ended up claiming that a missing
``sandbox-exec`` stops the task. Only the ``sandbox-exec`` mode does that; ``auto`` runs unconfined. These tests
are here so the next person who writes that sentence sees it fail.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from asistente.capacidades.navegador import confinamiento
from asistente.capacidades.navegador.confinamiento import ConfinamientoError, modo_de, perfil_macos, preparar

ORIGENES = ("https://www.boe.es",)


def _preparar(tmp_path: Path, **extra: object) -> confinamiento.Lanzamiento:
    return preparar(
        tmp_path,
        origenes=ORIGENES,
        ejecutable=None,
        ejecutable_por_defecto="/tmp/chromium-de-pega",
        **extra,  # type: ignore[arg-type]
    )


def test_un_valor_desconocido_es_auto_y_no_un_fallo() -> None:
    assert modo_de("sandbox-exec") == "sandbox-exec"
    assert modo_de("ninguno") == "ninguno"
    for valor in ("auto", "", None, "SANDBOX", "sí", 7):
        assert modo_de(valor) == "auto", valor


def test_en_auto_una_plataforma_sin_equivalente_lanza_el_navegador_sin_confinar(tmp_path: Path) -> None:
    lanzamiento = _preparar(tmp_path, plataforma="linux", modo="auto")
    assert lanzamiento.confinado == "ninguno"
    assert lanzamiento.ejecutable is None  # Playwright's own browser, unwrapped
    assert Path(lanzamiento.descargas).is_dir()
    # The run is not refused: this is the case the ADR used to describe as a failed launch.
    assert "--host-resolver-rules" in " ".join(lanzamiento.argumentos)


def test_en_auto_un_macos_sin_sandbox_exec_tampoco_detiene_la_tarea(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(confinamiento, "SANDBOX_EXEC", "/no/existe/sandbox-exec")
    assert _preparar(tmp_path, plataforma="darwin", modo="auto").confinado == "ninguno"
    # ``disponible`` answers for ``/navegador/estado`` and looks the binary up on its own (``shutil.which`` over
    # /usr/bin), so it is not pinned here: it does not read SANDBOX_EXEC and can disagree with ``preparar``.
    assert confinamiento.disponible("ninguno") is False


def test_solo_el_modo_sandbox_exec_falla_cerrado(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(confinamiento, "SANDBOX_EXEC", "/no/existe/sandbox-exec")
    with pytest.raises(ConfinamientoError) as fallo:
        _preparar(tmp_path, plataforma="darwin", modo="sandbox-exec")
    assert "La tarea no se ejecuta" in str(fallo.value)
    with pytest.raises(ConfinamientoError):
        _preparar(tmp_path, plataforma="linux", modo="sandbox-exec")


def test_el_modo_ninguno_no_envuelve_el_proceso_en_ninguna_plataforma(tmp_path: Path) -> None:
    for plataforma in ("darwin", "linux", "win32"):
        assert _preparar(tmp_path, plataforma=plataforma, modo="ninguno").confinado == "ninguno"


def test_el_perfil_deniega_el_directorio_del_usuario_y_permite_el_resto() -> None:
    perfil = perfil_macos(hogar="/Users/quien", navegador="/Apps", temporal="/tmp/run-1")
    # What it does: deny the home, allow everything else. Not «deny everything except the temporary folder».
    assert "(allow default)" in perfil
    assert '(deny file-read* (subpath "/Users/quien"))' in perfil
    assert '(deny file-write* (subpath "/Users/quien"))' in perfil
    # So these stay readable or writable, which is why the module lists them as not covered.
    for ruta in ("/etc", "/Library/Keychains", "/tmp", "/Volumes/otro"):
        assert f'(deny file-read* (subpath "{ruta}"))' not in perfil, ruta
        assert f'(deny file-write* (subpath "{ruta}"))' not in perfil, ruta
