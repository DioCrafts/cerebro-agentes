"""Hardening of the browser agent: ids that are not one path component, and no run without the websocket filter."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from asistente.capacidades.navegador import vigilancia
from asistente.capacidades.navegador.ejecutor import Ejecutor, ResultadoEjecucion
from asistente.capacidades.navegador.guion import guion_activo, validar_parametros
from asistente.capacidades.navegador.seguridad import origenes_efectivos
from tests.navegador.test_ejecutor import PARAMS_DATADIS


def test_punto_y_dos_puntos_no_son_ids(tmp_path: Path) -> None:
    hermano = tmp_path / "otro.json"
    hermano.write_text("{}")
    (tmp_path / "diagnostico").mkdir()
    for malo in (".", "..", "a\n", "a/b", ""):
        assert vigilancia.borrar_de_caso(malo, base=tmp_path) == 0
        assert vigilancia.guardar_diagnostico(malo, "t", {}, base=tmp_path) is None
        assert vigilancia.guardar_diagnostico("c", malo, {}, base=tmp_path) is None
        assert vigilancia.diagnosticos_de(malo, base=tmp_path) == []
    assert hermano.exists() and (tmp_path / "diagnostico").is_dir()
    assert vigilancia.guardar_diagnostico("caso.1", "t-1", {"x": 1}, base=tmp_path) is not None


class _Contexto:
    def __init__(self) -> None:
        self.paginas = 0
        self.cerrado = False

    def route(self, *_: Any) -> None:
        return None

    def new_page(self) -> Any:
        self.paginas += 1
        raise AssertionError("no page may open without the websocket filter")

    def on(self, *_: Any) -> None:
        return None

    def close(self) -> None:
        self.cerrado = True


class _Navegador:
    def __init__(self) -> None:
        self.contexto = _Contexto()

    def new_context(self, **_: Any) -> _Contexto:
        return self.contexto


def test_sin_filtro_de_websockets_la_tarea_no_se_ejecuta(tmp_path: Path) -> None:
    guion = guion_activo("datadis_consumo").reglas
    ejecutor = Ejecutor(
        guion,
        validar_parametros(guion, PARAMS_DATADIS),
        origenes=origenes_efectivos(guion, {}),
        modo="ventana",
    )
    navegador = _Navegador()
    resultado = ResultadoEjecucion(estado="fallido", mensaje="")
    ejecutor._ejecutar_en(navegador, resultado, tmp_path)
    assert resultado.estado == "fallido" and "websockets" in resultado.mensaje
    assert navegador.contexto.paginas == 0
