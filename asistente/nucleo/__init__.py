"""The core: the central brain and what it coordinates.

* :mod:`~asistente.nucleo.agent` — the brain (tool-using agent), its specialists, prompts, model providers and
  routing by purpose;
* :mod:`~asistente.nucleo.modulos` — the contract every domain implements (v1 and v2 hooks), the registry, the
  household profile, pending actions, the user's module choices and the bridges between domains;
* :mod:`~asistente.nucleo.tareas` — long-running tasks; :mod:`~asistente.nucleo.eventos` — the event bus;
* :mod:`~asistente.nucleo.memoria` — what the user decided; :mod:`~asistente.nucleo.decision` — the case store,
  fact extraction and audit trail;
* :mod:`~asistente.nucleo.proactive` — the inbox, the weekly digest and delivery outside the app;
* :mod:`~asistente.nucleo.evaluacion` / :mod:`~asistente.nucleo.evals` — continuous evaluation;
* :mod:`~asistente.nucleo.planificador` — the household planner (cross-domain: it weighs several domains'
  calculators against each other, so it belongs to no single domain).
"""
