"""Fixtures of the MCP connector tests: an in-process MCP server, its declaration and its approvals."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest

from asistente.capacidades import registro as registro_capacidades
from asistente.capacidades.conectores import aprobaciones, conexiones
from asistente.capacidades.conectores.aprobaciones import Aprobaciones
from asistente.capacidades.conectores.capacidad import capacidades
from asistente.capacidades.conectores.config import ConfiguracionMCP, ServidorMCP
from asistente.capacidades.contrato import CapacidadBase
from asistente.nucleo.modulos import registro as registro_modulos
from tests.conectores.servidor_de_prueba import Notas

CLAVE = "notas"
CAPACIDAD = "mcp_notas"


@dataclass
class Entorno:
    notas: Notas
    servidor: ServidorMCP
    gestor: conexiones.GestorConexiones
    directorio: Path

    def configuracion(self) -> ConfiguracionMCP:
        return ConfiguracionMCP(servidores={CLAVE: self.servidor})

    def aprobar(self, *nombres: str) -> Aprobaciones:
        """What ``cli mcp aprobar`` does: approve the definitions the server lists NOW."""
        vivas = self.gestor.listar(CLAVE, self.servidor)
        aprobaciones.aprobar(
            CLAVE, [h for h in vivas if not nombres or h.nombre in nombres], directorio=self.directorio
        )
        return aprobaciones.leer(self.directorio)

    def capacidad(self) -> CapacidadBase:
        (capacidad,) = capacidades(self.configuracion(), aprobaciones.leer(self.directorio))
        return capacidad

    def registro(self) -> registro_capacidades.RegistroCapacidades:
        capacidad = self.capacidad()
        return registro_capacidades.construir_registro([(capacidad.clave, capacidad)])


@pytest.fixture(autouse=True)
def registros_limpios() -> Iterator[None]:
    registro_capacidades.reiniciar()
    registro_modulos.reiniciar()
    yield
    registro_capacidades.reiniciar()
    registro_modulos.reiniciar()


@pytest.fixture
def entorno(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Entorno]:
    notas = Notas()
    servidor_mcp = notas.servidor()
    gestor = conexiones.GestorConexiones(lambda clave, servidor: servidor_mcp)
    conexiones.reiniciar(gestor)
    monkeypatch.setenv("MCP_DIR", str(tmp_path / "mcp"))
    monkeypatch.setenv("MCP_TIEMPO_CONEXION_S", "5")
    monkeypatch.delenv("MCP_SERVIDORES", raising=False)
    servidor = ServidorMCP(
        nombre="Notas",
        descripcion="Las notas del hogar",
        transporte="stdio",
        comando="servidor-de-notas",
        lectura=["buscarNotas", "leer-nota-maliciosa", "lenta"],
        tiempo_s=1.5,
    )
    try:
        yield Entorno(notas=notas, servidor=servidor, gestor=gestor, directorio=tmp_path / "mcp")
    finally:
        conexiones.reiniciar()
