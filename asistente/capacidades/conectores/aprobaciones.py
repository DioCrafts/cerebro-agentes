"""What the operator approved: the tool definitions of each MCP server, by digest (``MCP_DIR/aprobadas.json``).

A server's tools are offered only after a person looked at them: ``python -m asistente.plataforma.cli.mcp aprobar
<clave>`` connects, prints every tool (cleaned description, effect) and writes its definition here. From then on:

* the registry is built from this file alone, so the app starts whether or not the servers are up;
* before every call the live definition's digest is compared with the approved one: a server that changed a
  tool's description or schema after the approval is refused until the operator approves again.

The file holds no secret and no user data: tool names, cleaned descriptions, cleaned schemas and digests.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, datetime
import json
import logging
import os
from pathlib import Path
import tempfile
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from asistente.capacidades.conectores import ajustes
from asistente.capacidades.conectores.definicion import HerramientaRemota, limpiar_descripcion, limpiar_esquema

logger = logging.getLogger(__name__)

FICHERO = "aprobadas.json"


class _M(BaseModel):
    model_config = ConfigDict(extra="forbid")


class HerramientaAprobada(_M):
    huella: str = Field(pattern=r"^[0-9a-f]{64}$")
    # As shown to the operator at approval time, and to the model afterwards.
    descripcion: str = ""
    entrada: dict[str, Any] = Field(default_factory=dict)


class ServidorAprobado(_M):
    aprobado_el: datetime
    herramientas: dict[str, HerramientaAprobada] = Field(default_factory=dict)


class Aprobaciones(_M):
    version: int = 1
    servidores: dict[str, ServidorAprobado] = Field(default_factory=dict)

    def de(self, clave: str) -> dict[str, HerramientaAprobada]:
        servidor = self.servidores.get(clave)
        return dict(servidor.herramientas) if servidor is not None else {}


def ruta(directorio: str | Path | None = None) -> Path:
    return Path(directorio if directorio is not None else ajustes.directorio()) / FICHERO


def leer(directorio: str | Path | None = None) -> Aprobaciones:
    """The approvals on disk. A missing or unreadable file approves nothing (logged)."""
    fichero = ruta(directorio)
    try:
        texto = fichero.read_text(encoding="utf-8")
    except FileNotFoundError:
        return Aprobaciones()
    except OSError:
        logger.error("MCP approvals %s cannot be read: no connector tool is offered", fichero)
        return Aprobaciones()
    try:
        return Aprobaciones.model_validate(json.loads(texto))
    except ValueError, ValidationError:
        logger.error("MCP approvals %s are not valid: no connector tool is offered", fichero)
        return Aprobaciones()


def guardar(aprobaciones: Aprobaciones, directorio: str | Path | None = None) -> Path:
    """Write the file atomically (a reader never sees half of it), readable by the service's user only."""
    fichero = ruta(directorio)
    fichero.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporal = tempfile.mkstemp(prefix=".aprobadas-", suffix=".json", dir=fichero.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as salida:
            salida.write(aprobaciones.model_dump_json(indent=2))
            salida.write("\n")
        os.chmod(temporal, 0o600)
        os.replace(temporal, fichero)
    except BaseException:
        Path(temporal).unlink(missing_ok=True)
        raise
    return fichero


def aprobar(
    clave: str,
    herramientas: Iterable[HerramientaRemota],
    *,
    directorio: str | Path | None = None,
    ahora: datetime | None = None,
) -> ServidorAprobado:
    """Approve exactly ``herramientas`` for server ``clave`` (replacing what was approved before for it)."""
    aprobaciones = leer(directorio)
    servidor = ServidorAprobado(
        aprobado_el=ahora or datetime.now(UTC),
        herramientas={
            h.nombre: HerramientaAprobada(
                huella=h.huella,
                descripcion=limpiar_descripcion(h.descripcion),
                entrada=limpiar_esquema(h.entrada),
            )
            for h in herramientas
        },
    )
    aprobaciones.servidores[clave] = servidor
    guardar(aprobaciones, directorio)
    return servidor


def retirar(clave: str, *, directorio: str | Path | None = None) -> bool:
    """Withdraw every approval of server ``clave``; True when there was one."""
    aprobaciones = leer(directorio)
    if aprobaciones.servidores.pop(clave, None) is None:
        return False
    guardar(aprobaciones, directorio)
    return True


__all__ = [
    "FICHERO",
    "Aprobaciones",
    "HerramientaAprobada",
    "ServidorAprobado",
    "aprobar",
    "guardar",
    "leer",
    "retirar",
    "ruta",
]
