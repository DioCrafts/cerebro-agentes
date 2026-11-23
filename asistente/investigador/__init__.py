"""Deep research army («ejército investigador»), on the product's own provider layer (ADR 0006).

``investigar(pregunta, presupuesto) -> Informe``: a planner splits the question, searchers and readers work in
parallel (the research capability's web searcher as their tool), a critic tries to refute every finding and a
writer drafts a report where each claim cites a source that was read; unsourced claims are removed (or marked).
See ``docs/cerebro/investigador.md``.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal

from asistente.investigador.ejercito import AlProgresar, Ejercito, Rol
from asistente.investigador.modelo import (
    Afirmacion,
    Critica,
    Evento,
    Fuente,
    Hallazgo,
    Informe,
    Presupuesto,
    Subpregunta,
)
from asistente.investigador.presupuesto import PresupuestoAgotadoError
from asistente.investigador.web import Documento, HerramientaWeb, WebDeCapacidad


def investigar(
    pregunta: str,
    presupuesto: Presupuesto | None = None,
    *,
    llm: Any | Mapping[Rol, Any] | None = None,
    web: HerramientaWeb | None = None,
    al_progresar: AlProgresar | None = None,
    sin_fuente: Literal["eliminar", "marcar"] = "eliminar",
) -> Informe:
    """Research ``pregunta`` within ``presupuesto``. ``llm``: an ``LLMProvider`` (or one per role; default: the
    metered provider of purpose ``investigador``);
    ``web``: the searchers' tool (default: the research capability's web searcher for that model)."""
    if not pregunta.strip():
        raise ValueError("La pregunta está vacía.")
    if llm is None:
        from asistente.investigador.llm import llm_por_defecto

        llm = llm_por_defecto()
    if web is None:
        from asistente.investigador.web import web_por_defecto

        web = web_por_defecto(_proveedor_de(llm))
    return Ejercito(llm, web, presupuesto or Presupuesto(), al_progresar=al_progresar, sin_fuente=sin_fuente).investigar(
        pregunta.strip()
    )


def _proveedor_de(llm: Any) -> Any:
    """The (name, model) the capability's ``web_para`` expects: the provider itself, or one read from a
    ``proveedor/modelo`` model name."""
    uno = next(iter(llm.values())) if isinstance(llm, Mapping) else llm
    if isinstance(getattr(uno, "name", None), str) and "/" not in str(uno.model):
        return uno
    modelo = str(uno.model)
    nombre, _, resto = modelo.partition("/")

    class _P:
        name = nombre if resto else ("anthropic" if "claude" in modelo else nombre)
        model = resto or modelo

    return _P()


__all__ = [
    "Afirmacion", "Critica", "Documento", "Ejercito", "Evento", "Fuente", "Hallazgo", "HerramientaWeb", "Informe",
    "Presupuesto", "PresupuestoAgotadoError", "Subpregunta", "WebDeCapacidad", "investigar",
]
