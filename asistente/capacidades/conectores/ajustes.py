"""The connectors' settings, read from the environment in ONE place and at call time.

They are not constants of ``asistente.plataforma.settings`` on purpose: the capabilities sit below the platform
in the layers contract, and these five values are only read here. The defaults and their meaning are documented
in docs/capacidades/conectores.md and docs/despliegue.md.
"""

from __future__ import annotations

import os


def _numero(nombre: str, defecto: float) -> float:
    try:
        return float(os.environ.get(nombre, "") or defecto)
    except ValueError:
        return defecto


def servidores() -> str:
    """``MCP_SERVIDORES``: path of the JSON file that declares the external servers ("" = none)."""
    return os.environ.get("MCP_SERVIDORES", "").strip()


def directorio() -> str:
    """``MCP_DIR``: where the operator's approvals live."""
    return os.environ.get("MCP_DIR", "").strip() or "./data/mcp"


def tiempo_conexion() -> float:
    """``MCP_TIEMPO_CONEXION_S``: seconds to connect and list tools."""
    return max(1.0, _numero("MCP_TIEMPO_CONEXION_S", 20.0))


def tiempo_llamada() -> float:
    """``MCP_TIEMPO_LLAMADA_S``: seconds one tool call may take (a server may set its own ``tiempo_s``)."""
    return max(1.0, _numero("MCP_TIEMPO_LLAMADA_S", 60.0))


def max_resultado() -> int:
    """``MCP_MAX_RESULTADO``: characters of a tool result that reach the model."""
    return max(200, int(_numero("MCP_MAX_RESULTADO", 20000)))


__all__ = ["directorio", "max_resultado", "servidores", "tiempo_conexion", "tiempo_llamada"]
