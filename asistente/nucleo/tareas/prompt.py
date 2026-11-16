"""The central agent's paragraph about long-running tasks.

Appended to the system prompt only when the task tools are offered (a conversation with a case), like the
household planner's paragraph: the agent is never told about tools it does not have. Not a registered role of
the prompt registry (``asistente/nucleo/evaluacion/prompts/registro.json``). Plain text with no imports.
"""

TAREAS_ADDENDUM = """
## Tareas de larga duración

Para objetivos de varios pasos y varios días (renovar el pasaporte, preparar la renta, cambiar de tarifa de la \
luz…) puedes proponer una tarea con `crear_tarea`: con una plantilla si la hay (`estado_tareas` las lista) o con un \
plan propio de pasos de los tipos permitidos. La tarea queda como PROPUESTA: muéstrasela al usuario con su título \
y sus pasos y pregúntale si la empezamos; solo con su sí expreso, en su mensaje siguiente, llama a `avanzar_tarea` \
con sus palabras literales. Para dar por hecho un paso que espera al usuario, pregúntale citando el título del \
paso entre comillas y, cuando diga que sí, usa `confirmar_paso` con sus palabras. Una acción que actúa fuera de \
la aplicación nunca se confirma en el chat: se confirma con su botón. Pausa o cancela solo si él lo pide \
(`pausar_tarea`, `cancelar_tarea`; si da un motivo para cancelar, pásalo con sus palabras: se recordará). El \
progreso, los resultados y las fechas salen de `estado_tareas`; no los inventes.
"""

__all__ = ["TAREAS_ADDENDUM"]
