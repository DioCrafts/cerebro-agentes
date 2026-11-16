"""Continuous evaluation of the central assistant and its module specialists.

Every time the assistant is wrong, or the user corrects it, the case can become a regression test; before a
prompt or a model changes, the suite runs and the change is applied only if it improves.

* :mod:`.captura` — user feedback (with explicit opt-in per answer) stored in the user's own case, and the
  operator's review queue; :mod:`.senales` — automatic signals read from the audit trail.
* :mod:`.modelos` / :mod:`.suite` — the versioned suite (``casos/*.json``) and its deterministic expectations;
  expected figures come from re-running the deterministic calculators (:mod:`.cifras`), never from a model.
* :mod:`.juez` — optional LLM-judge rubric with a deterministic gate (the judge must quote the answer).
* :mod:`.runner` — runs the central agent with a prompt/model variant over the suite and reports.
* :mod:`.variantes` — versioned prompts and the ACTIVE variant the agent reads; :mod:`.gate` — ``promote``.

Nothing here is imported by the agent at start-up except :mod:`.variantes`, which is dependency-light.
"""
