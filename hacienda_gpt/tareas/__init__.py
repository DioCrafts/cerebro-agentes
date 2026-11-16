"""Long-running tasks: goals of several steps over several days, with state, deadlines and confirmations.

The central brain plans (a versioned template, or a plan a model proposes behind a deterministic gate) and the
engine advances the plan step by step across the modules and the capabilities (e-mail, browser, documents,
research), waiting for dates and events of the bus, asking the user when a step needs them, and never acting
outside the app without their confirmation. See README «Tareas de larga duración».

* :mod:`~hacienda_gpt.tareas.modelo` — Tarea, Paso (closed set of step types), history;
* :mod:`~hacienda_gpt.tareas.almacen` — the tables in the cases' database (exported, erased with the case);
* :mod:`~hacienda_gpt.tareas.validacion` — the gate every plan passes;
* :mod:`~hacienda_gpt.tareas.plantillas` — the declarative templates (``plantillas/*.json``);
* :mod:`~hacienda_gpt.tareas.capacidades` — e-mail, browser, documents, research (official sites only);
* :mod:`~hacienda_gpt.tareas.motor` — the deterministic state machine, the bus consumer and timers;
* :mod:`~hacienda_gpt.tareas.servicio` — what the user can do (accept, confirm, skip, pause, cancel…);
* :mod:`~hacienda_gpt.tareas.herramientas` — the central agent's tools;
* :mod:`~hacienda_gpt.tareas.planificacion` — a model's plan proposal behind the gate.

The package will move to ``asistente/nucleo/tareas`` with the rename; nothing outside imports its internals
except the API router, the agent hook and the export.
"""

from __future__ import annotations

from hacienda_gpt.tareas.almacen import TareaStore, exportar_tareas
from hacienda_gpt.tareas.modelo import Paso, PasoSpec, Tarea
from hacienda_gpt.tareas.motor import ConsumidorTareas, Motor, ciclo

__all__ = ["ConsumidorTareas", "Motor", "Paso", "PasoSpec", "Tarea", "TareaStore", "ciclo", "exportar_tareas"]
