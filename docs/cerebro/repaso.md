# The proactive pass (`asistente/nucleo/repaso`)

The brain that thinks without being asked. Once a week (or once a day) it looks at everything that is new or still
open for a case — every module's notices, the mail, deadlines, BOE changes that affect the user, pending actions,
long-running tasks waiting for them, their own decisions, what happened on the event bus — and writes **one**
prioritised message: «esta semana importa esto, por esto, y te propongo esto».

Before it, each module wrote its own notices with its own fixed rules, and the weekly digest
(`proactive/bandeja.resumen_semanal`) listed them by kind and date. Nobody looked at the whole.

**Principles.** Code decides what exists, what is proposed and in which order; the small model only writes, and a
deterministic gate checks every word that reaches the user. Every claim traces to a signal (the card shows where
each point comes from). Nothing is executed: proposals are buttons on the existing confirmation endpoints. Cheap
(small tier, one call per case and period, batched). It never fails the inbox: any error gives the deterministic
digest exactly as before.

## Pipeline

```
recoger (senales.py) ──► priorizar (priorizacion.py) ──► redactar (redaccion.py) ──► puerta (puerta.py) ──► escribir (servicio.py)
   Senal[]                  Punto[] + por qué            small model, data only     every figure/date/proposal      ONE notice
   (facts, source,          (decisions applied,           (or batch)                 checked; 1 repair; rescue        resumen_semanal:<periodo>
    proposal if any)         grouped, capped)                                        → deterministic rendering
```

### 1. Signals (`senales.py`)

| Source (owner) | Signal | Proposal (only when the owner produced one) |
|---|---|---|
| Inbox (`NotificationStore`): open notices of every module (`bandeja.abiertas`) | `aviso` — amount, due date, days left; `plazo` → official; mail's administration notice → the 10-day rule **read from the mail module's BOE-verified legal file** (`notificaciones.json`, art. 43.2 Ley 39/2015), irreversible; `cambio_normativo` → BOE change | a task template whose declared `ambito_decision` covers the notice's ámbito (e.g. «Podrías ahorrar con otra tarifa» → `cambiar_tarifa_luz`), checked by instantiating it through its own gate, never stored |
| Pending actions (`AccionStore`) | `accion` — what will happen, days waiting | `accion:<acc_…>` → `POST /acciones/{id}/confirmar\|rechazar` |
| Long-running tasks (`TareaStore`) | `tarea` proposed / failed / deadline; `paso` waiting for the user | `tarea:<tar_…>` → `POST /tareas/{id}/aceptar\|cancelar`; `paso:<tar>:<paso>` → `POST /tareas/{id}/pasos/{paso}/confirmar` (only when no later step needs the date the user would give) |
| Mail proposals (`correo_propuestas`) | `propuesta_correo` | `correo:<prop_…>` → `POST /correo/propuestas/{id}/confirmar\|rechazar` |
| Decisions (`asistente.nucleo.memoria`) | decided since the last pass, expiring within 14 days, proposals held back since | — |
| Event bus (`eventos`, the case's events after the watermark) | aggregated counts: mail classified, facts changed, charges imported, modules switched on | — |

A **template** proposal only *prepares* the task (`POST /tareas` with `aceptar: false`); it starts when the user
accepts it. Text that does not come from code (mail, a tool's arguments) is `no_confiable` and neutralised
(`conocimiento/llm/security.py`); notice bodies are never read. One failing source is logged and named; the others
still count.

**Watermark.** The case's previous applied pass (`repaso_pasadas`): what is newer is `nueva`; events are read after
its `evento_hasta`. A case's first pass marks nothing as new and reads one week of events.

### 2. Priority (`priorizacion.py`)

A fixed table (`RASGOS`), each feature with its points and a Spanish reason built from the facts:

| Feature | Points |
|---|---|
| plazo: today / tomorrow / ≤3 / ≤7 / ≤14 / ≤30 / later days; already past | 60 / 55 / 45 / 30 / 18 / 8 / 2; 40 |
| gravedad: `urgente` / `aviso` | 25 / 8 |
| consecuencia: a legal consequence from a legal data file / an official deadline | 20 / 15 |
| normativa: a BOE change that affects the profile | 12 |
| dinero: ≥1.000 / ≥300 / ≥50 / >0 € (the module's amount; never summed) | 15 / 10 / 6 / 3 |
| decision: a concrete proposal waiting; waiting ≥7 days | 12; +5 |
| novedad: new since the last pass | 8 |
| contexto: decisions noted, events | −15 |

**Urgent** (the digest's severity, so the push): the module said so, or an official or irreversible deadline is at
most 2 days away. **Decisions** use the memory's own rules (`memoria.modelo.evaluar`, `cubre`, `BLOQUEAN_AVISOS`): in
force → the signal is held back (or only its proposal, when the decision is about the proposal); expired or with a
reopening condition met → it comes back, saying why; the notice the inbox already let come back once stays.
**Grouping**: explicit links (a task, its action, its notices) and the inbox's «same matter» rule across modules
(`bandeja.mismo_asunto`). **Cap**: `REPASO_MAX_PUNTOS` (1–12); the rest is counted. Context alone writes no digest.

### 3. Writing and the gate (`redaccion.py`, `prompts.py`, `puerta.py`)

The small model (purpose **`cerebro:repaso`**, routing tier `pequeno`; prompt role **`repaso`** read with
`prompt_activo`, falling back to `REPASO_SYSTEM_PROMPT`) receives the points as JSON inside `<datos_repaso>` — facts
already written in Spanish («31,20 €», «20/11/2026») — and returns `{"titular", "puntos": [{punto_id, texto, por_que,
propuesta_id}]}`. The gate:

| Rule | Why |
|---|---|
| every point, once, by its id | the model selects how to say it, never what exists |
| every number and date is in that point's facts (any Spanish spelling: `1.234,56 €`, `31,2`, `20 de noviembre`, «diez días») | zero invented figures; a sum of two points' amounts is in neither |
| `propuesta_id` is one the point carries | the model cannot create a proposal |
| nothing claimed as done («ya lo he pagado», «queda presentado», «se ha confirmado»…) | the pass never acts |
| order within ±1 of the code's rank | the model smooths the reading, it does not re-rank |
| plain text, bounded length, no links/addresses/markup | a link would be invented; markup could hide content |

On violations: **one repair** listing them; then the **rescue** — items that passed keep the model's words, the
others get the deterministic text (`origen` `mixto`); nothing usable or a provider error (budget included) → the
**deterministic rendering** (`origen` `determinista`), built only from facts and reasons, which passes the gate by
construction (tested).

### 4. The digest (`servicio.py`)

* **One notification per case and period**, kind `resumen_semanal`, key `resumen_semanal:2026-W47` (weekly) or
  `resumen_semanal:2026-11-18` (daily): the inbox, the delivery and the decision memory treat it as the digest they
  know (never held back; removed when a decision removes a notice it cites — the body lists each point's sources by
  their titles).
* **Supersedes** a deterministic digest of the same period; **idempotent** per period. When the user's decision
  removed it, the next call rebuilds it from the stored pass without a model call (`adaptar`: texts of the points
  still there with the same facts, deterministic text for the rest).
* **Severity** `urgente` only when a point is urgent (then the inbox pushes it: `push.envio.avisar_al_escribir`),
  otherwise `info` (the push channel of `ops deliver` skips it).
* **Never acts**: the text says so, and every proposal is a button on its own endpoint, with that endpoint's checks.

### Batches

`repasar_todos` (job `repaso`) sends the requests of every case due as **one batch** of the provider's batch API
(`ProveedorDeLotes`, Claude's Message Batches), stores each case's row as `pendiente_lote` with its snapshot of
points, and collects on later runs: each result is gated **against the snapshot the model read**, adapted to the
points of now, applied **once** (atomic claim), and its usage recorded at batch prices for that case. While a batch is
pending, the inbox's weekly digest waits for it. Fallbacks to the synchronous call: provider without batches,
`REPASO_POR_LOTES=false`, failed submission (budget, network), an errored result, or a batch still running after 12
hours (its late result is then ignored). Unlike `agent/providers/lotes.py` (public texts only, JSON state on disk),
the state here lives in the cases' database and goes with the case. `custom_id`s are hashes: no case id leaves.

## The inbox hook

`bandeja.resumen_semanal` first asks `servicio.desde_bandeja`: with `REPASO_ACTIVO` and a configured model, the brain
writes the period's digest (or decides there is nothing to write, or that a batch is pending); otherwise — or on ANY
error — it returns None and the deterministic digest is written exactly as before (the existing tests are unchanged).
So `run-weekly` and the decision memory's rebuild keep working whatever the setting.

## Settings

| Variable | Default | |
|---|---|---|
| `REPASO_ACTIVO` | `false` | Off: the deterministic weekly digest as before. On: the brain's pass (with a model; deterministic rendering without one). |
| `REPASO_FRECUENCIA` | `semanal` | `semanal` \| `diaria`. |
| `REPASO_DIA`, `REPASO_HORA` | `0`, `07:00` | When the job writes the weekly pass (Spanish time); a missed day is caught up that week. |
| `REPASO_POR_LOTES` | `true` | One batch for all cases when the provider has a batch API. |
| `REPASO_MAX_PUNTOS` | `7` | Points per digest (1–12). |

The model of the `pequeno` tier comes from the routing settings (`LLM_ROUTING_PROFILE=recomendado` or
`LLM_MODEL_SMALL`); with none, the provider's model (see `docs/operacion.md`, «Varios modelos según la tarea»).

## Operating it

```bash
python -m asistente.plataforma.cli.repaso trabajo                       # what the worker runs every 30 min
python -m asistente.plataforma.cli.repaso ahora --case <id> --simular   # dry run: points, reasons, rendering
python -m asistente.plataforma.cli.repaso ahora --todos --simular --con-modelo
python -m asistente.plataforma.cli.repaso ahora --case <id>             # write it now (synchronous)
python -m asistente.plataforma.cli.repaso estado --case <id>            # the case's passes (no text)
```

## API and UI

`GET /cases/{case_id}/repaso?notification_id=` (`asistente/plataforma/api/routers/repaso.py`) returns the structured
pass: headline, points (text, «por qué», the code's reasons, urgency, due date), sources (module, title, date,
notice id) and proposals with their **current** state (`pendiente` / `decidida`, read from the owning layer).
`RepasoCard.tsx` renders it in the inbox: ranked points with a «¿Por qué?» disclosure, source chips, proposal buttons
wired to the existing confirm/reject calls (an action that reaches outside the app asks once more), «Ya está» to mark
it read; without a structured pass it shows the notification's text.

## Privacy

The pass reads only the case's own stores. Its rows (`repaso_pasadas`) hold the same titles and figures as the
digest; they are erased with the case (trigger on `case_states`), exportable (`almacen.exportar_repaso`), and purged
after 120 days (each case's latest row stays: it is the watermark).

## Tests

`tests/repaso/`: signal collection from every source (fake notices, actions, tasks, decisions, events, templates,
a failing source), the priority table and the decisions, grouping and the cap, the gate (invented figures, dates,
sums, proposals, unknown/missing/repeated points, «done» claims, order, format), repair → rescue → deterministic, the
service (one digest, idempotency, superseding, rebuild without a model call, daily key, push only when urgent,
erasure, purge), the batch flow (submitted → polled → applied once, fallbacks), the inbox hook (old behaviour when
off, without a model or on any error), the CLI and the API.
