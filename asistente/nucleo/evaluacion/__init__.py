"""Continuous evaluation of the assistant — and learning from its real mistakes (``docs/cerebro/aprendizaje.md``).

Every «esto está mal» becomes a masked candidate case; a reviewer accepts it into the PRIVATE regression cases or
rejects it; before a prompt or a model changes, the suite plus those real cases run and the change is applied only
if it does not make the evaluation worse.

* :mod:`.captura` — user feedback (with explicit opt-in per answer) stored in the user's own case, with the
  household brain's report; :mod:`.senales` — automatic signals read from the audit trail (some are weak signals
  of a mistake).
* :mod:`.redaccion` — masking before anything becomes a candidate (amounts kept); :mod:`.revision` — candidates,
  dedupe and triage, acceptance with a negative control, rejection; :mod:`.privado` — accepted real cases, kept
  out of the repository, exported and erased with their user.
* :mod:`.modelos` / :mod:`.suite` — the versioned suite (``casos/*.json``) and its deterministic expectations
  (the brain's plan, provenance of figures, memory, the weekly pass included); expected figures come from
  re-running the deterministic calculators (:mod:`.cifras`), never from a model.
* :mod:`.juez` — optional LLM-judge rubric with a deterministic gate (the judge must quote the answer).
* :mod:`.runner` — runs the assistant (or the household brain, through :mod:`.ejecutores`) with a prompt/model
  variant over the cases and reports; :mod:`.reproduccion` — script replays, captured runs included.
* :mod:`.variantes` — versioned prompts (of every package) and the ACTIVE variant; :mod:`.gate` and
  :mod:`.estadistica` — ``promote``: paired statistics, critical cases, cost and latency.

Nothing here is imported by the agent at start-up except :mod:`.variantes`, which is dependency-light.
"""
