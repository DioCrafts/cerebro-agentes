"""The brain that thinks without being asked: a weekly or daily pass that writes ONE prioritised digest.

Read ``docs/cerebro/repaso.md`` first. The pass is a pipeline where code decides and the model only writes:

1. :mod:`~asistente.nucleo.repaso.senales` — collect every :class:`Senal` that is open or new for a case (the
   inbox of every module, pending actions, long-running tasks, the mail's proposals, the user's decisions, the
   case's events). A signal carries its facts (Decimal amounts, dates) and its source; it carries a proposal only
   when the module / task / action layer produced a concrete one. The model can never create one.
2. :mod:`~asistente.nucleo.repaso.priorizacion` — a deterministic, explainable score per signal (urgency, legal
   consequence, money, novelty, a decision waiting), the user's decisions applied (what they rejected does not come
   back), signals about the same matter grouped, capped: a ranked list of :class:`Punto`, each with its «por qué».
3. :mod:`~asistente.nucleo.repaso.redaccion` — the small model (purpose ``cerebro:repaso``) gets the points as DATA
   and returns a headline and one text per point; :mod:`~asistente.nucleo.repaso.puerta` checks every number, date,
   proposal and the order against the points (one repair, then the deterministic rendering, always available).
4. :mod:`~asistente.nucleo.repaso.servicio` — one notification per case and period (the weekly digest's kind and
   key), idempotent, batched through the provider's batch API when there is one, never failing other jobs.

This ``__init__`` imports nothing on purpose (the inbox imports the service lazily).
"""
