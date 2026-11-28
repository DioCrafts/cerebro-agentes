"""The operator's declaration of the external MCP servers (``MCP_SERVIDORES``, a JSON file).

::

    {
      "servidores": {
        "notas": {
          "nombre": "Notas",
          "descripcion": "Las notas del hogar en el servidor de notas.",
          "transporte": "stdio",
          "comando": "uvx",
          "argumentos": ["servidor-de-notas"],
          "entorno": {"NOTAS_TOKEN": "env:NOTAS_TOKEN"},
          "lectura": ["buscar", "leer_nota"]
        },
        "calendario": {
          "nombre": "Calendario",
          "descripcion": "El calendario compartido.",
          "transporte": "http",
          "url": "https://calendario.example/mcp",
          "cabeceras": {"Authorization": "env:CALENDARIO_AUTORIZACION"},
          "lectura": ["listar_eventos"]
        }
      }
    }

What the file decides, and the server never does:

* which tools only read (``lectura``, by the server's own tool names). Every other tool acts outside the app and
  waits for the user's confirmation, whatever the server's annotations say;
* which tools are offered at all (``herramientas``; omitted: every approved one).

Secrets are never written in the file: a value ``env:NOMBRE`` is read from the environment when the server is
connected. A file that does not validate switches every connector off and is reported (``errores()``), it never
stops the app.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import json
import logging
import os
from pathlib import Path
import re
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from asistente.capacidades.conectores import ajustes

logger = logging.getLogger(__name__)

# No underscore: the chat tool is ``conectores_<clave>_<operación>`` and must split back unambiguously.
CLAVE_RE = re.compile(r"^[a-z][a-z0-9]{1,15}$")
PREFIJO_CAPACIDAD = "mcp_"
_REF_ENTORNO = re.compile(r"^env:([A-Za-z_][A-Za-z0-9_]{0,80})$")
_HOSTS_LOCALES = frozenset({"127.0.0.1", "localhost", "::1"})
# Variables a server process must never have rewritten for it: they change what code it loads.
_ENTORNO_PROHIBIDO = re.compile(r"^(LD_|DYLD_|PYTHONPATH$|PYTHONSTARTUP$|NODE_OPTIONS$|PATH$)", re.IGNORECASE)


class SecretoAusenteError(RuntimeError):
    """A value ``env:NOMBRE`` whose variable is not set (message in Spanish, names the variable only)."""


class _M(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ServidorMCP(_M):
    nombre: str = Field(min_length=1, max_length=60)
    descripcion: str = Field(min_length=1, max_length=300)
    transporte: Literal["stdio", "http"]
    # stdio: the program the operator installed and reviewed. Runs with the service's permissions.
    comando: str | None = Field(default=None, min_length=1, max_length=400)
    argumentos: list[str] = Field(default_factory=list, max_length=40)
    entorno: dict[str, str] = Field(default_factory=dict)
    directorio: str | None = None
    # http: the Streamable HTTP endpoint.
    url: str | None = Field(default=None, max_length=600)
    cabeceras: dict[str, str] = Field(default_factory=dict)
    # Tool names (as the server lists them) the operator declares read-only.
    lectura: list[str] = Field(default_factory=list, max_length=200)
    # Tool names offered; None: every tool the operator approved.
    herramientas: list[str] | None = Field(default=None, max_length=200)
    # Seconds one call may take (None: MCP_TIEMPO_LLAMADA_S).
    tiempo_s: float | None = Field(default=None, gt=0, le=600)
    activo: bool = True

    @model_validator(mode="after")
    def _coherente(self) -> ServidorMCP:
        problemas: list[str] = []
        if self.transporte == "stdio":
            if not self.comando:
                problemas.append("un servidor «stdio» necesita «comando»")
            if self.url or self.cabeceras:
                problemas.append("«url» y «cabeceras» son del transporte «http»")
        else:
            if not self.url:
                problemas.append("un servidor «http» necesita «url»")
            else:
                partes = urlsplit(self.url)
                local = (partes.hostname or "").lower() in _HOSTS_LOCALES
                if partes.scheme != "https" and not (partes.scheme == "http" and local):
                    problemas.append("la «url» debe ser https (http solo hacia esta misma máquina)")
                if not partes.hostname or partes.username or partes.password:
                    problemas.append("la «url» no puede llevar usuario ni contraseña: usa «cabeceras» con env:")
            if self.comando or self.argumentos or self.entorno or self.directorio:
                problemas.append("«comando», «argumentos», «entorno» y «directorio» son del transporte «stdio»")
        for nombre in self.entorno:
            if _ENTORNO_PROHIBIDO.match(nombre):
                problemas.append(f"la variable de entorno {nombre} no se puede fijar para un servidor")
        for nombre, valor in self.cabeceras.items():
            if nombre.lower() in {"authorization", "cookie", "x-api-key"} and not _REF_ENTORNO.match(valor):
                problemas.append(f"la cabecera {nombre} lleva un secreto: escríbela como env:NOMBRE_DE_VARIABLE")
        if self.herramientas is not None:
            fuera = sorted(set(self.lectura) - set(self.herramientas))
            if fuera:
                problemas.append(f"«lectura» nombra herramientas que «herramientas» no ofrece: {', '.join(fuera)}")
        if problemas:
            raise ValueError("; ".join(problemas))
        return self

    def tiempo_llamada(self) -> float:
        return float(self.tiempo_s) if self.tiempo_s is not None else ajustes.tiempo_llamada()

    def ofrece(self, herramienta: str) -> bool:
        return self.herramientas is None or herramienta in self.herramientas


class ConfiguracionMCP(_M):
    servidores: dict[str, ServidorMCP] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _claves(self) -> ConfiguracionMCP:
        malas = sorted(clave for clave in self.servidores if not CLAVE_RE.match(clave))
        if malas:
            raise ValueError(
                "clave de servidor no válida (minúsculas y cifras, de 2 a 16, sin guiones): " + ", ".join(malas)
            )
        return self

    def activos(self) -> dict[str, ServidorMCP]:
        return {clave: servidor for clave, servidor in sorted(self.servidores.items()) if servidor.activo}


@dataclass(frozen=True)
class Lectura:
    """The declaration as read: the configuration (empty when unreadable) and why it could not be read."""

    configuracion: ConfiguracionMCP = field(default_factory=ConfiguracionMCP)
    error: str | None = None


def clave_capacidad(clave_servidor: str) -> str:
    return f"{PREFIJO_CAPACIDAD}{clave_servidor}"


def leer(ruta: str | Path | None = None) -> Lectura:
    """Read ``MCP_SERVIDORES`` (or ``ruta``). Never raises: a broken file is an empty configuration with an error."""
    origen = str(ruta) if ruta is not None else ajustes.servidores()
    if not origen.strip():
        return Lectura()
    try:
        crudo = json.loads(Path(origen).read_text(encoding="utf-8"))
    except OSError as exc:
        return Lectura(error=f"no se puede leer {origen}: {type(exc).__name__}")
    except ValueError as exc:
        return Lectura(error=f"{origen} no es JSON válido: {exc}")
    try:
        return Lectura(configuracion=ConfiguracionMCP.model_validate(crudo))
    except ValidationError as exc:
        detalle = "; ".join(f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in exc.errors()[:6])
        return Lectura(error=f"{origen} no es una declaración válida: {detalle}")


def resolver(valores: dict[str, str]) -> dict[str, str]:
    """``valores`` with every ``env:NOMBRE`` replaced by that variable. Raises :class:`SecretoAusenteError`."""
    resueltos: dict[str, str] = {}
    for nombre, valor in valores.items():
        referencia = _REF_ENTORNO.match(valor)
        if referencia is None:
            resueltos[nombre] = valor
            continue
        variable = referencia.group(1)
        encontrado = os.environ.get(variable)
        if not encontrado:
            raise SecretoAusenteError(f"Falta la variable de entorno {variable}.")
        resueltos[nombre] = encontrado
    return resueltos


__all__ = [
    "CLAVE_RE",
    "PREFIJO_CAPACIDAD",
    "ConfiguracionMCP",
    "Lectura",
    "SecretoAusenteError",
    "ServidorMCP",
    "clave_capacidad",
    "leer",
    "resolver",
]
