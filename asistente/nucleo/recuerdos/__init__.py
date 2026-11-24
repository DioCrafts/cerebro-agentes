"""Conversation memory on Hindsight: what the user told the assistant, remembered across conversations.

* :mod:`~asistente.nucleo.recuerdos.servicio` — the memory as the brain, the API and the account erasure use it
  (one Hindsight bank per owner; only the user's own words are retained, after the privacy gates);
* :mod:`~asistente.nucleo.recuerdos.cliente` — the thin client of Hindsight's REST API;
* :mod:`~asistente.nucleo.recuerdos.daemon` — where Hindsight runs (``HINDSIGHT_URL`` or the local embedded daemon);
* :mod:`~asistente.nucleo.recuerdos.pausa` — the user's «pausar la memoria» switch (the only table here).

Hindsight (https://github.com/vectorize-io/hindsight) extracts facts with its own model, keeps them with their
dates and source, consolidates them into observations (a newer fact supersedes an older one), and recalls with
semantic, BM25, graph and temporal search plus a reranker. See ``docs/cerebro/memoria.md``.
"""
