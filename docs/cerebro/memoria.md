# Conversation memory (`asistente/nucleo/recuerdos`)

Until this package the server kept no conversations: the browser sent the history, so every new session started
almost from zero (only the case facts and the decisions memory survived). The conversation memory keeps:

* **each conversation** of a user (title, a short summary, and the raw messages for a limited time);
* **«lo que sé de ti»**: remembered items (`dato`, `preferencia`, `contexto`), **each with its origin** — the
  conversation, the date, and the user's literal words (`cita`) verified to be in that conversation's user
  messages.

The brain retrieves what is relevant on each question; the user sees it, corrects it, deletes it or pauses it; it is
in the account export and in the account erasure.

Principles: the model proposes, a deterministic gate decides; nothing is remembered that the user did not say in
their own words; privacy first (no credentials, masked identifiers, no special-category data unless asked, strict
per-owner isolation, retention); a failing memory never fails the user's turn.

## How the brain uses it

```python
from asistente.nucleo.recuerdos.servicio import servicio_memoria

memoria = servicio_memoria()            # ProveedorMemoria; MemoriaNula when MEMORIA_CONVERSACIONES=false
bloque = memoria.recuperar(case_id=…, user_id=…, consulta=pregunta, conversacion_id=…, limite=8)
prompt += bloque.como_contexto()        # <memoria_del_usuario> … data, never instructions, each item with its date
…
memoria.registrar_turno(TurnoConversacion(…, recuerdos_usados=[…]))
```

* `recuperar` makes **no model call**: a few SQLite reads and a BM25 over the owner's rows (plus an embedding of the
  query when `MEMORIA_EMBEDDINGS` is on). `registrar_turno` is **one write** (both messages, masked). Both **never
  raise**: on any failure they log and return an empty block / do nothing.
* The owner is `(case_id, user_id)` **exactly as passed**. The API and the brain must resolve `user_id` the same way:
  the signed-in account's id, else the case's `user_id` (the value `api.py` already uses for `contexto_consumo`).
* `BloqueMemoria.vacio_por`: `sin_cuenta` (no case and no user: nothing is stored either), `desactivada` (the user
  paused the memory), `sin_resultados`.
* Items handed to the brain: remembered items (`r_…`), summaries of earlier conversations (`conv-<id>`, tipo
  `resumen`, quoting the user's first question), and — so the memory works before the summariser runs — the user's
  own words in conversations not summarised yet (`msg-<id>-<n>`, tipo `contexto`, same privacy filters). The current
  conversation (`conversacion_id`) is never handed back: the brain has it.
* `recuerdos_usados` of a turn increments the items' use counter (a small ranking prior; the panel shows it).

## Storage (`almacen.py`, migrations owner `recuerdos`)

Same SQLite file as the cases (`DECISION_STATE_DB_PATH`), WAL, `secure_delete=ON`, a WAL checkpoint after every
erasure.

| Table | Content |
|---|---|
| `recuerdos_conversaciones` | owner, id (unique **per owner**), title, summary, the user's quote it rests on, state (`abierta`, `en_lote`, `resumida`, `sin_resumen`), messages / summarised counters, index terms, optional embedding |
| `recuerdos_mensajes` | raw messages (masked), kept `MEMORIA_RETENCION_MENSAJES_DIAS` |
| `recuerdos` | items: type, normalised key (`hipoteca.banco`), text, origin (conversation, date, quote), every source, corrected / sensitive flags, `sustituido_por`, state (`activo`, `sustituido`, `olvidado`), uses, index terms, optional embedding |
| `recuerdos_lotes` | summaries waiting in a provider batch: **ids and counts only, never text** |
| `recuerdos_interruptor` | the user's «pause» switch (per user; per case without accounts) |

Every query filters on both owner columns; a conversation id sent by another owner creates a conversation of its own
(`UNIQUE (case_id, user_id, id)`). Triggers erase the memory with the case (`case_states`) and with the account
(`users`); `DELETE /me` also erases it explicitly (and checkpoints the WAL). The store runs the catalogue's own
statements when `MIGRACIONES_AUTOMATICAS=false`, so both paths build the same schema byte for byte.

## Privacy gates (`privacidad.py`, deterministic)

| Rule | Where | Why |
|---|---|---|
| Credentials (passwords, PIN, CVV, codes, tokens, API keys, private keys, «la clave de la app es …») are replaced by `[dato secreto omitido]` | raw messages before storing; summaries; candidates are **dropped** | a credential is never «something I know about you», and is never stored |
| IBAN → «IBAN terminado en 1234», cards (Luhn) → last four, DNI/NIE, phones, e-mails, long digit runs → labels | everywhere | the memory needs the fact, not the identifier |
| Special categories (health, religion, politics, union, sexual orientation, ethnic origin, biometric/genetic, criminal records) | candidates dropped; sentences dropped from summaries; unsummarised messages not retrievable | GDPR art. 9/10. Kept, flagged `sensible`, only when the user asked to remember it **in the same message** («recuerda que…», «apúntalo», «tenlo en cuenta») |
| Pasted text (quoted `> …` lines, fenced blocks, forwarded / original messages) is not the user's words | the quote check | what an e-mail or a document says is not what the user said |
| Injection bait (`conocimiento/llm/security.py`) | candidates dropped; free text neutralised | memory is data, never instructions |

## The summariser (`resumen.py`, `prompts.py`)

A conversation idle for `MEMORIA_RESUMEN_TRAS_MINUTOS` is summarised by the job `memoria-resumir` (every 15 min) with
the purpose `memoria:resumen` (small tier), prompt role `memoria_resumen` (`prompt_activo`, fallback
`RESUMEN_SYSTEM_PROMPT`). The model proposes a title, a 1–3 sentence summary and up to 12 candidates. **The gate**
keeps a candidate only if:

1. its `cita` is in one of this conversation's **user** messages, in the user's own words (same normalisation and
   minimum length as `registrar_decision` / `registrar_hechos`, `agent/procedencia.cita_valida`);
2. every figure in its text is one the user wrote (`procedencia.numeros_del_usuario`): the memory never carries a
   number the assistant computed;
3. it has no credential and no injection bait, and it is not special-category data unless asked (above).

Summary and title: every sentence with a secret, special-category data, a neutralised injection or a figure the user
did not write is dropped; an unusable title keeps the provisional one (the first question).

Merging (`aplicar`): same normalised key + same content → **duplicate** (one item, one more source); same key,
different content → the **newer words supersede** (the old item stays as history with `sustituido_por`); older words
never override newer ones; the user's correction wins over words older than the correction; a source already known
is never learnt twice (re-summarising a continued conversation is idempotent); a message the user made the memory
**forget** is never learnt from again (word-free tombstone: conversation + message number, removed when the messages
are purged); at most `MEMORIA_MAX_RECUERDOS` active items (context items and the least used go first, corrections
last).

**Batch mode.** With a provider that has a batch API (`ProveedorDeLotes`: Claude) and `MEMORIA_RESUMEN_LOTES`, the job
collects finished batches and submits the new conversations as one batch (cheaper); a result is applied through the
same gate only if the conversation still exists, has not changed since it was sent and the user has not paused the
memory; errors put it back in the queue; usage is recorded at batch prices under `memoria:resumen`. Unlike
`agent/providers/lotes.py` (meant for public texts), no request text is ever written outside the database. Note: the
provider keeps batch results for a while on its side (Anthropic: 29 days), like any request.
Otherwise: one call per conversation, each inside its own `contexto_consumo` (attributed to its user, per-question
cap). `MEMORIA_RESUMEN_EN_SEGUNDO_PLANO` also summarises from the API process in a background thread (carrying the
request's `contextvars`) when a user starts a new conversation.

## Retrieval (`recuperacion.py`)

Why not SQLite FTS5: a global index mixes every user's postings and statistics (IDF) — a weak cross-user signal —,
keeps erased words in its segments until a merge, and makes the schema depend on the SQLite build. One owner's memory
is bounded, so the lexical index is a **per-owner BM25** over stored, normalised terms (text, key and the user's quote).

* **Lexical**: BM25 per query concept (a word and its household synonyms at 0.6), divided by the score of a document
  matching every concept once: the IDF-weighted share of the question covered (a concept absent from the owner's memory
  counts as half a rare one). Spanish normalisation: accents, stopwords, a light stemmer (plural, gender, infinitive,
  gerund); question and time words weigh 0.3; a document must share at least one **topic** word.
* **Semantic** (optional, `MEMORIA_EMBEDDINGS`): cosine of the project's embeddings blended 55/45. Never required.
* **Priors**: type (preferencia/dato 1.0, contexto 0.85, resumen 0.8), recency (half-life by type, never below 60 %),
  corrected / used items +5 %.
* **Explicit references** are always included: «¿qué te dije de la hipoteca?» (the items about the topic), «¿qué sabes
  de mí?» (facts and preferences), «la otra vez», «lo que hablamos» (the latest conversation).
* **MMR** (λ = 0.75) for diversity; threshold 0.12; relevance in 0..1.

**Measured quality** (`tests/recuerdos/test_recuperacion.py`, 44 queries over a 40-item synthetic household memory
plus 8 conversation summaries and another household's items): recall@5 **0.989**, hit@1 **0.932**, MRR **0.966**;
0 of 8 unrelated questions retrieve anything; another owner's items never appear. The test fails below recall@5 0.97,
MRR 0.92, hit@1 0.88.

## For the user

API (`asistente/plataforma/api/routers/recuerdos.py`; API key, session, ownership — somebody else's case, item or
conversation is a 404):

| Endpoint | |
|---|---|
| `GET /recuerdos?case_id=` | items with their origin (date, quote, conversation title), earlier versions, the switch, the retention, a plain-Spanish explanation |
| `PATCH /recuerdos/{id}?case_id=` | correct (`texto`, `tipo`): the user's wording wins (`corregido`); credentials are refused (422) |
| `DELETE /recuerdos/{id}?case_id=` | forget one (with its earlier versions) |
| `DELETE /recuerdos?case_id=` | forget everything: items, summaries, conversations, messages |
| `GET` / `PUT /recuerdos/interruptor?case_id=` | pause / resume (pausing stores, summarises and retrieves nothing; it deletes nothing) |
| `GET /conversaciones?case_id=` | conversations with titles, summaries, retained messages, what was learnt |
| `GET /conversaciones/{id}?case_id=` | one, with its messages while retained |
| `DELETE /conversaciones/{id}?case_id=` | its messages, its summary and the items that rested only on it |

Without `case_id`: the signed-in user's memory outside any case. Listing and erasing work even with
`MEMORIA_CONVERSACIONES=false`.

UI: the panel «Lo que sé de ti» (`frontend/src/components/RecuerdosPanel.tsx`, `?panel=recuerdos`): items grouped by
type, each with «Me lo dijiste el 12/11/2026: “…”», inline correction, forget, «Olvidar todo», the on/off switch with
what is kept and for how long, the history of changes, and «Conversaciones» with summaries and delete.

Export: `GET /me/export` has `recuerdos` in each case and at the top level (memory without a case). Erasure:
`DELETE /me` erases all of it.

## Operation

| Setting | Default | |
|---|---|---|
| `MEMORIA_CONVERSACIONES` | `true` | off: `servicio_memoria()` is `MemoriaNula` |
| `MEMORIA_RETENCION_MENSAJES_DIAS` | `30` | raw messages; summaries and items stay until the user deletes them |
| `MEMORIA_MAX_RECUERDOS` | `200` | active items per owner |
| `MEMORIA_RESUMEN_TRAS_MINUTOS` | `30` | idle time before summarising |
| `MEMORIA_RESUMEN_LOTES` | `true` | batch API when the provider has one |
| `MEMORIA_RESUMEN_EN_SEGUNDO_PLANO` | `false` | also summarise from the API process |
| `MEMORIA_EMBEDDINGS` | `false` | semantic ranking with the project's embedder (MLX) |

Job: `memoria-resumir` every 15 minutes (`python -m asistente.nucleo.recuerdos resumir`): collect batches, summarise,
then apply the retention; without a configured model it only applies the retention. `… purgar` applies only the
retention.
