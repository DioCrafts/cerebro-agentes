"""MCP connectors: the external MCP servers the operator declares, as capabilities of the app.

* ``config.py`` — the operator's declaration (``MCP_SERVIDORES``): servers, transports, which tools only read;
* ``definicion.py`` — a tool definition's digest, and the cleaned description and schema a model may see;
* ``aprobaciones.py`` — the definitions a person approved (``MCP_DIR/aprobadas.json``);
* ``conexiones.py`` — one session per server on a loop thread; time limits; no sampling, no elicitation;
* ``capacidad.py`` — each server as a capability ``mcp_<clave>`` behind :func:`pedir`;
* ``modulo.py`` — the chat front (module ``conectores``), one tool per approved operation.

The package exposes ``CAPACIDADES`` (a callable: several capabilities, read from the operator's files on every
discovery) instead of one ``CAPACIDAD``, and ``MODULO`` only while some connector has an approved tool.
The MCP SDK is imported lazily, on the first connection. Design: ``docs/adr/0008-mcp-y-agente-de-navegador.md``.
"""

from asistente.capacidades.conectores.capacidad import capacidades as CAPACIDADES  # noqa: N812 - a registry hook
from asistente.capacidades.conectores.modulo import construir_modulo

MODULO = construir_modulo()

__all__ = ["CAPACIDADES", "MODULO"]
