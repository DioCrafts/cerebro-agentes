"""Long-running tasks: goals of several steps over several days, with state, deadlines and confirmations.

The central brain plans (a versioned template, or a plan a model proposes behind a deterministic gate) and the
engine advances the plan step by step across the modules and the capabilities (e-mail, browser, documents,
research), waiting for dates and events of the bus, asking the user when a step needs them, and never acting
outside the app without their confirmation. See README «Tareas de larga duración».

* :mod:`~asistente.nucleo.tareas.modelo` — Tarea, Paso (closed set of step types), history;
* :mod:`~asistente.nucleo.tareas.almacen` — the tables in the cases' database (exported, erased with the case);
* :mod:`~asistente.nucleo.tareas.validacion` — the gate every plan passes;
* :mod:`~asistente.nucleo.tareas.plantillas` — the declarative templates (``plantillas/*.json``);
* :mod:`~asistente.nucleo.tareas.capacidades` — e-mail, browser, documents, research (official sites only);
* :mod:`~asistente.nucleo.tareas.motor` — the deterministic state machine, the bus consumer and timers;
* :mod:`~asistente.nucleo.tareas.servicio` — what the user can do (accept, confirm, skip, pause, cancel…);
* :mod:`~asistente.nucleo.tareas.herramientas` — the central agent's tools;
* :mod:`~asistente.nucleo.tareas.planificacion` — a model's plan proposal behind the gate.

The package will move to ``asistente/nucleo/tareas`` with the rename; nothing outside imports its internals
except the API router, the agent hook and the export.
"""

from __future__ import annotations

from typing import Any

from asistente.nucleo.tareas.almacen import TareaStore, exportar_tareas
from asistente.nucleo.tareas.modelo import Paso, PasoSpec, Tarea
from asistente.nucleo.tareas.motor import ConsumidorTareas, Motor, ciclo, servicio_tareas


def registrar_en_plataforma(registro: Any) -> None:
    """The platform worker runs the task engine as a service (``asistente.plataforma.procesos``)."""
    from asistente.plataforma.procesos import Servicio

    registro.registrar_servicio(
        Servicio(
            "tareas",
            "Tareas de larga duración: fechas, eventos del bus, acciones confirmadas y consultas a especialistas.",
            servicio_tareas,
        )
    )


__all__ = [
    "ConsumidorTareas",
    "Motor",
    "Paso",
    "PasoSpec",
    "Tarea",
    "TareaStore",
    "ciclo",
    "exportar_tareas",
    "registrar_en_plataforma",
    "servicio_tareas",
]
