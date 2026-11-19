"""SimpleX Chat channel of the messaging gateway: ``CanalSimplex`` talks to a local simplex-chat CLI (WebSocket API).

Modules: ``canal`` (the ``contrato.Canal`` implementation), ``cliente`` (WebSocket JSON client), ``protocolo`` (the
commands, events and types used, from the official bot API reference), ``archivos`` (receiving files safely),
``ejecutor`` (handler threads, in order per contact), ``configuracion`` (settings). Setup guide:
``docs/mensajeria/simplex.md``.
"""

from asistente.plataforma.mensajeria.simplex.canal import CanalSimplex, VersionNoSoportada, dividir_texto
from asistente.plataforma.mensajeria.simplex.configuracion import ConfiguracionSimplex, ConfiguracionSimplexInvalida

__all__ = [
    "CanalSimplex",
    "ConfiguracionSimplex",
    "ConfiguracionSimplexInvalida",
    "VersionNoSoportada",
    "dividir_texto",
]
