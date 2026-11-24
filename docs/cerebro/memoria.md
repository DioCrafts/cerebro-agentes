# Conversation memory (`asistente/nucleo/recuerdos`)

The conversation memory keeps what the user told the assistant in earlier conversations, so the brain does not ask
the same thing twice. It runs on [Hindsight](https://github.com/vectorize-io/hindsight) 1.2.3 (MIT licence, package
`hindsight-all`, pinned in `pyproject.toml`). Hindsight does the memory work: it extracts facts from each message
with its own model call, stores them with their date and the passage they came from, consolidates them into
observations and ranks them at recall time. The code in this repository decides what Hindsight may see and who may
read it back: the privacy gates, one bank per owner, the brain's contract, the user API, the pause switch, the
export and the erasure. Why Hindsight, and what else was considered, is in
[ADR 0007](../adr/0007-memoria-hindsight.md).

This is not the decisions memory (`asistente/nucleo/memoria`, «Lo que sé de tus preferencias»). That one stays as
typed rows in SQLite, because the tax optimiser, the household planner and the inbox read it as hard constraints.

## Architecture

| Piece | What it does |
|---|---|
| `recuerdos/servicio.py` | `ServicioMemoria`: the brain's `ProveedorMemoria` (`recuperar`, `registrar_turno`), the user operations, the export and the account erasure. One instance per process (`servicio()`). |
| `recuerdos/cliente.py` | A thin client of Hindsight's REST API (`/v1/default/banks/{bank}/…`) with only the calls the memory makes. It goes through the project's HTTP client (`asistente/plataforma/http`), so timeouts, retries and the User-Agent follow the same rules as every other outgoing request. |
| `recuerdos/daemon.py` | Where Hindsight runs: the server in `HINDSIGHT_URL`, or the embedded daemon of `hindsight-all`. |
| `recuerdos/pausa.py` | The user's pause switch, the memory's only table in the cases' database (`recuerdos_pausa`, migration owner `recuerdos_hindsight`). |
| `nucleo/privacidad.py` | The deterministic privacy gates. The household council uses the same functions to mask what its members read. |
| `plataforma/api/routers/recuerdos.py` | The user API: list, correct, forget, pause, conversations. |

A turn touches the memory twice. Before answering, `recuperar` makes one recall request to Hindsight. After
answering, the API calls `registrar_turno` from a background task (`_registrar_en_memoria` in `api.py`, only when
the request carries a `conversacion_id` and the brain produced the answer), which sends the user's message to
Hindsight and returns as soon as Hindsight accepts it. Extraction then runs inside Hindsight, out of the turn.

## One bank per owner

An owner is the pair (case, account), with `''` for a missing part. The API and the brain resolve the account in
the same way: the signed-in account's id, else the case's `user_id`. Each owner has its own Hindsight bank, named
`cerebro-` followed by the first 40 hex digits of the SHA-256 of `case_id` and `user_id` (`servicio.banco_de`).
Hindsight never receives a case id or a user id, and a request can only reach the bank computed from the owner the
API resolved. Without a case and without an account there is no owner: nothing is retained and recall answers
`sin_cuenta`.

The bank is created on the first retain (`PERFIL_BANCO`, `CONFIG_BANCO` in `servicio.py`) with:

* missions in Spanish: remember a person and their household (situation, money, home, family, plans, how they like
  things); extract only what the person says about themself and their life, with dates and figures exactly as they
  wrote them; skip generic questions and anything the assistant said;
* observations on, with the instruction to say which value is current, and since when, when a new fact replaces an
  older one (another salary, another bank, another house);
* Hindsight's own `memory_defense` with `redact` on `sensitive_data`, a second regex scrubber behind the app's gates.

## What is retained

Only the user's message of each turn (`TurnoConversacion.pregunta`). The assistant's answers never go to Hindsight.
Two things follow from that: nothing the assistant said can come back later as «something you told me», and the
source passage of every fact holds the user's own words.

Before sending, `servicio.preparar` runs the message through the gates (next section). The message is dropped if
fewer than 12 characters are left, if only the secret placeholder is left, or if it carries special-category data
the user did not ask to remember. A message that passes becomes one Hindsight document:

* id `msg-<conversation>-<timestamp>`, the turn's date as its timestamp;
* tag `conversacion:<id>` and metadata `conversacion_id`, which is how the API rebuilds conversations later;
* a context line telling Hindsight it is a message the user wrote to their personal assistant;
* sent with `async: true`.

Nothing is retained while the owner has paused the memory, with `MEMORIA_CONVERSACIONES=false`, or without an
owner.

Hindsight turns each message into facts of two types, which the app maps to its own names: `world` → `hecho`
(something about the person's world: «tiene la hipoteca con ING») and `experience` → `vivencia` (something that
happened to them: «firmó la hipoteca en marzo»). Each fact keeps its date and the chunk it was extracted from.
Hindsight also writes `observation` → `observacion`, a consolidation of several facts. Observations are written by
Hindsight's model, so they are never the user's literal words and carry no quote.

## Privacy gates (`asistente/nucleo/privacidad.py`)

They run in this process, with no model, before the HTTP request. Hindsight and its model provider never see what
they remove.

| Rule | Effect |
|---|---|
| Credentials: passwords, PINs, CVVs, one-time codes, tokens, API keys, private keys, «la clave de la app es …» | Replaced by `[dato secreto omitido]`. A message left with nothing else is not retained. |
| Identifiers | An IBAN becomes «IBAN terminado en 1234»; a Luhn-valid card keeps its last four digits; DNI/NIE, phone numbers, e-mail addresses and runs of ten or more digits are replaced by a label. The memory needs «cobra la nómina en ING», not the account number. |
| Special categories (GDPR art. 9 and 10: health, religion, political opinion, union membership, sexual orientation, ethnic origin, biometric or genetic data, criminal records) | The message is not retained unless the same message asks for it («recuerda que…», «apúntalo», «tenlo en cuenta»). |
| Pasted text: quoted `> …` lines, fenced code blocks, forwarded or original messages | Cut out. What an e-mail or a document says about the user is not something the user said. |
| Injection bait (the shared detector in `conocimiento/llm/security.py`) | Neutralised. |

The rules lean towards forgetting: a lost memory costs one more question, a wrongly kept one is a privacy incident.

## Recall into the brain

```python
from asistente.nucleo.recuerdos.servicio import servicio_memoria

memoria = servicio_memoria()            # ProveedorMemoria; MemoriaNula when MEMORIA_CONVERSACIONES=false
bloque = memoria.recuperar(case_id=…, user_id=…, consulta=pregunta, conversacion_id=…, limite=8)
prompt += bloque.como_contexto()        # <memoria_del_usuario> … data, never instructions, each item with its date
…
memoria.registrar_turno(TurnoConversacion(…))
```

`recuperar` is one Hindsight recall: the question (first 4,000 characters), the budget `HINDSIGHT_PRESUPUESTO`,
up to 2,048 tokens of results, the current time as the query time, and the source chunks included. Hindsight ranks
with four arms, semantic (the local embedder `HINDSIGHT_EMBEDDINGS`, `BAAI/bge-m3` by default), BM25 over
PostgreSQL full-text search with the Spanish dictionary (`HINDSIGHT_IDIOMA`), its entity graph and a temporal arm,
and then reorders with its local reranker (Hindsight's default, `hindsight-ai/hs-rerank-1`; the app does not
change it). Recall makes no language-model call.

Each result becomes a `RecuerdoMemoria`:

* `id` (Hindsight's), `tipo` (`hecho`, `vivencia` or `observacion`) and `texto` (cut at 600 characters);
* `origen`: the conversation (from the tag or the metadata), the date the user said it, and `cita`, the source chunk
  cut at 400 characters (none for an observation);
* `relevancia`: Hindsight's order turned into a number from 1.0 downwards.

Items from the current conversation are left out, since the brain already has it. At most `limite` items
(`CEREBRO_MEMORIA_LIMITE`, 8; between 1 and 30).

`BloqueMemoria.vacio_por` says why a block is empty: `desactivada` (switched off by the operator or paused by the
user), `sin_cuenta`, `sin_resultados` (nothing relevant, or the bank does not exist yet) or `no_disponible`
(Hindsight is starting, unreachable or failed). Neither `recuperar` nor `registrar_turno` raises, and neither waits
for the embedded daemon: if it is not up, `recuperar` starts it in a background thread and the turn goes on without
memory.

### The figures gate

`_Turno.textos_usuario()` in `cerebro/cerebro.py` gives the figures gate the user's own words:
this conversation's messages plus the `cita` of each recalled item. A figure the user gave in an earlier
conversation is therefore theirs and may appear in the answer. A figure that only appears in an observation is not:
observations have no quote, so the gate treats that figure like any other number that no tool and no user wrote.

### The household council

The council's dossier (`consejo/expediente.py`) reads the same block. Items with special-category data are left
out. A fact that states a leaning («prefiere…», «no quiere…», «le gusta…», «evita…», «descarta…») and shares words
with the options is sealed together with the user's preference; Hindsight has no separate preference type, so the
dossier recognises them by their wording. The rest become dossier facts with their id and date.

## For the user

API (`asistente/plataforma/api/routers/recuerdos.py`). Every endpoint needs the API key and the session; somebody
else's case, item or conversation is a 404, exactly like one that does not exist. Without `case_id` the owner is the
signed-in account outside any case.

| Endpoint | What it does |
|---|---|
| `GET /recuerdos?case_id=` | The facts and experiences, newest first, each with its conversation, that conversation's title (the start of its first message), its date and `corregido`. Also the operator's switch (`disponible`), the user's (`activa`), the number of conversations and a plain-Spanish explanation for the panel. Observations are not listed: they are derived from the facts and change with them. |
| `PATCH /recuerdos/{id}?case_id=` | Correct a fact with `texto` (1 to 600 characters). The correction goes through the gates and is retained at once (not in the background) as the user's own words, in a new document `corr-<uuid>` with the same conversation tag and `corrige` in its metadata; the old fact is marked `invalidated` in Hindsight. Returns the facts extracted from the correction, flagged `corregido`. 422 for an observation or for a correction that the gates leave empty. |
| `DELETE /recuerdos/{id}?case_id=` | Forget one item: the document it came from (the user's message) is deleted, with every fact Hindsight extracted from it. An item with no document is invalidated instead. Observations cannot be forgotten one by one (404). |
| `DELETE /recuerdos?case_id=` | «Olvidar todo»: deletes the owner's bank. Returns how many facts and conversations it held. |
| `GET`, `PUT /recuerdos/interruptor?case_id=` | Pause or resume. Pausing writes a row in `recuerdos_pausa`; while it is there nothing is retained or recalled. It deletes nothing. |
| `GET /conversaciones?case_id=` | The conversations, rebuilt from the retained messages grouped by their tag: id, title, first and last date, number of messages. |
| `GET /conversaciones/{id}?case_id=` | One conversation: the user's retained messages (as stored, after the gates) and the facts extracted from them. |
| `DELETE /conversaciones/{id}?case_id=` | Deletes every document of the conversation and what was learnt from them. |

Listing and erasing also work with `MEMORIA_CONVERSACIONES=false`, so what was stored can always be seen and
deleted. The user endpoints never wait for the embedded daemon: while it starts, or if Hindsight does not answer,
they ask for a start in the background and return 503 with «La memoria no responde ahora mismo (está arrancando o se
ha parado). Prueba en un minuto.».

The panel «Lo que sé de ti» (`frontend/src/components/RecuerdosPanel.tsx`, `?panel=recuerdos`) shows the list, the
correction, «Olvidar», «Olvidar todo», the switch and the conversations.

### Export

`GET /me/export` has `recuerdos` in each case and at the top level (the memory outside any case): `motor:
"hindsight"`, `pausada`, the facts and experiences (id, type, text, conversation, date) and each conversation with
the text and date of every retained message, plus the observations Hindsight consolidated. If Hindsight cannot be
reached, the rest of the export still goes out and `recuerdos` carries an `error` saying the memory was not included
and to ask again in a few minutes.

### Account erasure

`DELETE /me` calls `servicio.borrar_de_usuario`, which deletes the account's bank outside any
case, one bank per case the account still owns, and the account's pause rows. If Hindsight cannot be reached it
raises: the erasure stops before the account is deleted and the user can ask again. The steps that run before it in
`auth.py` (the evaluation data, the anonymised usage log, the Gmail revocation) have already happened by then.

## Configuration

| Setting | Default | What it does |
|---|---|---|
| `MEMORIA_CONVERSACIONES` | `true` | `false`: `servicio_memoria()` returns `MemoriaNula`, nothing new is retained or recalled and the daemon is not warmed up. The user can still list and erase. |
| `HINDSIGHT_URL` | empty | An existing Hindsight server (`http://host:port`). Empty: the embedded daemon. |
| `HINDSIGHT_PERFIL` | `cerebro` | Profile of the embedded daemon: its config file and its PostgreSQL directory. |
| `HINDSIGHT_LLM_PROVEEDOR` | `anthropic` | Provider of the model Hindsight uses to extract facts and consolidate observations. The key comes from `ANTHROPIC_API_KEY` (or `OPENAI_API_KEY` for `openai`). |
| `HINDSIGHT_LLM_MODELO` | `claude-haiku-5-5` | Model for those calls. |
| `HINDSIGHT_EMBEDDINGS` | `BAAI/bge-m3` | Local multilingual embedder. Hindsight's own default, `BAAI/bge-small-en-v1.5`, only knows English. |
| `HINDSIGHT_IDIOMA` | `spanish` | PostgreSQL dictionary of the BM25 arm. |
| `HINDSIGHT_PRESUPUESTO` | `mid` | Recall effort: `low`, `mid` or `high`. |
| `HINDSIGHT_TIMEOUT_S` | `8` | Seconds per request to Hindsight. A turn whose recall takes longer goes on without memory. |
| `CEREBRO_MEMORIA_LIMITE` | `8` | Items handed to the brain per turn. |

`HINDSIGHT_LLM_*`, `HINDSIGHT_EMBEDDINGS` and `HINDSIGHT_IDIOMA` configure the embedded daemon only
(`daemon.configuracion_daemon()`). With `HINDSIGHT_URL`, that server's own settings apply; set the same values there.
The bank's missions and `memory_defense` are sent through the API and apply in both cases.

## Operating the daemon

Without `HINDSIGHT_URL` the app uses `HindsightEmbedded` from `hindsight-all`: a daemon in its own OS process,
listening on 127.0.0.1, with an embedded PostgreSQL.

| What | Where |
|---|---|
| Data (PostgreSQL) | `~/.pg0/instances/hindsight-embed-cerebro/` |
| Profile config | `~/.hindsight/profiles/cerebro.env` |
| Log | `~/.hindsight/profiles/cerebro.log` |

`~` is the home directory of the user that runs the API, and `cerebro` is `HINDSIGHT_PERFIL`.

The API starts the daemon in a background thread when it starts (`calentar_memoria()` in its lifespan hook), and
does not try without the model's API key, which Hindsight needs to start. A recall or a user request that finds it
down asks for a start the same way and does not wait. Once running, the daemon is shared by the API, the worker and the CLIs, and
it outlives them.
The first start downloads the local models (the embedder and the reranker) and takes one or two minutes; until it
finishes, turns get `vacio_por="no_disponible"` and answer without memory. Later starts are quick.

The API key is not written into the profile. The daemon inherits it from the environment of the process that starts
it (`HINDSIGHT_API_LLM_API_KEY`, filled from `ANTHROPIC_API_KEY` when unset). After rotating the key, stop the
daemon so that the next start picks up the new one.

The daemon does not stop with the app: Ctrl+C on `make dev` leaves it running. To manage it:

```bash
uv run hindsight-embed -p cerebro daemon status
uv run hindsight-embed -p cerebro daemon logs -f
uv run hindsight-embed -p cerebro daemon stop
```

### Backups

`cli.plataforma copia` copies the SQLite databases only, which hold the pause switch but none of the
memory. To back up the memory, stop the daemon and copy `~/.pg0/instances/hindsight-embed-cerebro/`; encrypt the
copy like the others, since it holds the users' words. To restore, stop the daemon, put the directory back and let
the app start it again. With `HINDSIGHT_URL`, back up that server's PostgreSQL by its own procedure.

### Docker

In the image of `docs/despliegue/` the api and worker containers would each start their own embedded
daemon, with its data inside the container, lost on every rebuild. Run one Hindsight server and point both at it
with `HINDSIGHT_URL`, or set `MEMORIA_CONVERSACIONES=false`.

## Limitations

* Every retained message costs Hindsight one or more calls to `HINDSIGHT_LLM_MODELO` for extraction, plus
  the consolidation of observations, billed to the same API key. These calls do not go through the app's provider
  layer: they are not in `llm_usage` or `/usage` and do not count towards the per-question budget. The provider's
  console is the only place they show up.
* The user's words leave the machine. After the gates, each message goes to the configured LLM provider for
  extraction. By default it is Anthropic, the same provider that already receives every question. With
  `LLM_PROVIDER=local` the app's own calls stay on the machine, but Hindsight's do not unless
  `HINDSIGHT_LLM_PROVEEDOR` is changed as well.
* Recall quality on Spanish personal data has not been measured; there are no figures yet for this engine. The `memoria`
  category of the evaluation suite (14 cases; the four `memoria-real-*` cases retain their items into real Hindsight
  banks) is the place to measure it:
  `uv run python -m asistente.plataforma.cli.evaluacion run --categoria memoria --out reports/evaluacion/memoria.json`.
  It needs Hindsight and its model.
* Forgetting is per message: forgetting one fact deletes the whole message it came from, and with it every other
  fact extracted from that message.
* Observations are not listed in the panel and cannot be corrected or forgotten one by one (they are exported).
  «Olvidar todo» and the account erasure remove them with the bank.
* Cases are only deleted through `DELETE /me`, which deletes every case's bank before the cases themselves. A future
  endpoint that deletes a single case must call `ClienteHindsight.borrar_banco(banco_de(case_id, user_id))` too.
* A message the user writes while the daemon is still starting is not retained.
* PostgreSQL marks deleted rows as dead and reclaims them on `VACUUM` (autovacuum runs it on its
  own schedule); until then the old text can remain in the data files, and in any copy of `~/.pg0`.
