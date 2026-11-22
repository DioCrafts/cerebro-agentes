"""The searchers' tool: search and read the web. Default = the existing research capability's web searcher.

``asistente.capacidades.investigacion.web`` already searches and reads (server-side web search + fetch, domain
allow / block lists). The army reuses it through :class:`WebDeCapacidad`; tests pass a fake ``HerramientaWeb``.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol


@dataclass(frozen=True)
class Documento:
    url: str
    titulo: str
    texto: str
    recuperado_el: datetime | None = None


class HerramientaWeb(Protocol):
    def buscar_y_leer(self, *, pregunta: str, consultas: Sequence[str], max_lecturas: int) -> list[Documento]: ...


class WebDeCapacidad:
    """Adapter over ``asistente.capacidades.investigacion.web.BuscadorWeb`` (``web_para(proveedor)``)."""

    def __init__(self, buscador: Any, *, permitidos: Sequence[str] | None = None, bloqueados: Sequence[str] = ()):
        self.buscador = buscador
        self.permitidos = permitidos
        self.bloqueados = tuple(bloqueados)

    def buscar_y_leer(self, *, pregunta: str, consultas: Sequence[str], max_lecturas: int) -> list[Documento]:
        r = self.buscador.buscar(
            pregunta=pregunta,
            consultas=list(consultas),
            permitidos=self.permitidos,
            bloqueados=self.bloqueados,
            max_busquedas=max(1, len(consultas)),
            max_lecturas=max_lecturas,
        )
        return [Documento(d.url, d.titulo, d.texto, d.recuperado_el) for d in r.documentos]


def web_por_defecto(proveedor: Any) -> HerramientaWeb:
    from asistente.capacidades.investigacion.web import web_para

    return WebDeCapacidad(web_para(proveedor))
