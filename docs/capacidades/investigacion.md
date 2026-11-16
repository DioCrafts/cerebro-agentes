# Investigar con fuentes (`hacienda_gpt/capacidades/investigacion`)

A capability that answers questions the app's structured data does not cover — «¿qué documentos necesito para
renovar el pasaporte?», «¿qué dice la DGT sobre deducir el gimnasio?», «compara seguros de hogar», «¿cuándo empieza
la campaña de la renta 2026?» — with a report in which **every claim carries a literal quote of a retrieved
source**, checked by code.

It is a capability under the capability contract (`hacienda_gpt/capacidades/contrato.py`): any domain, the central
agent, a long-running task and the API reach it through `pedir("investigacion", "investigar", …)`. It has **one
operation, read-only** (`efecto="lectura"`): it never writes, sends or acts on anything.

## Method

Deterministic around the model (`servicio.py`):

1. **Plan** (`plan.py`, purpose `investigacion:planificar`, small tier). The model splits the question into
   sub-questions with a corpus query, 1–3 words for BOE titles and a web query. Code trims the plan to the depth's
   size; without a model, over budget or with an unusable plan, the question itself is the plan.
2. **Gather**, in priority order (`fuentes.py`, `web.py`):
   1. the app's legal corpus — the same searcher as the agent's `buscar_fuentes` (FAISS index) with the doctrine
      validity graph (superseded / disputed doctrine is noted);
   2. the BOE — the consolidated-legislation open-data API (`/datosabiertos/api/legislacion-consolidada`,
      search by title, by text only when no title matches; repealed norms skipped) and the norm's consolidated
      text (`buscar/act.php?id=…`, read with `conocimiento/texto.py`);
   3. the open web — only through the model provider's **server-side** web search and web fetch tools (the app
      never downloads an arbitrary page), only when the operator switched it on, the user consented and the
      provider supports it (below).
   Each source gets an id (`S1`, `S2`…, in that order), its retrieval date, whether it is **official** (decided by
   host name, `dominios.py`) and whether it carries injection bait.
3. **Synthesise** (`sintesis.py`, purpose `investigacion:sintetizar`, medium tier). The model receives the
   question and an extract of each source between untrusted-data marks, and returns claims, each with
   `{fuente, cita}` pairs, conflicts and what it could not answer. It has no tools.
4. **Gate** (`puerta.py`, no model). A claim survives only if
   * each kept quote names a source of this research and is **literally in that source's full text** (compared
     as the legal-data checks compare quotes: NFKC, lower case, no whitespace or quotation marks), ≥ 12 characters;
   * every figure in the claim is printed in one of its quotes;
   * neither the claim nor its quotes carry injection bait, and the claim names no web address that is not a source.
   Then: official / not official per claim; claims of the same topic resting on different sources with different
   figures are flagged as a **conflict** (the model's own conflict notes are kept only with two real sources);
   the **confidence** is `alta` (every claim has an official source, nothing dropped, no conflict, no suspicious
   source), `baja` (nothing verified, or only non-official sources) or `media`. The **answer is assembled from the
   surviving claims only**, each followed by its source ids; what was dropped is listed under «no verificado».

When the synthesis output has claims but none survives the gate, the metering wrapper retries it once on the next
tier (escalation, `agent/enrutado.py`), with the gate as the validator.

## The report (`modelo.Informe`)

`respuesta`, `afirmaciones` (text, topic, quotes, official), `fuentes` (id, type, title, URL, BOE id / reference,
domain, official, retrieval date, cited, suspicious, note), `conflictos`, `confianza`, `no_verificado`, `avisos`,
`web` (asked, used, why not, searches, pages read, results seen but not read — never cited) and `coste` (model calls,
tokens, web searches, USD or unknown, the cap that stopped it).

## The web

* **Off by default**: `INVESTIGACION_WEB=false`. When on, each user still has to give their own consent
  (`POST /investigacion/consentimiento`, in the «Investigar» panel); it is stored append-only with the wording
  version, and withdrawing it also drops that user's cached reports that used the web.
* **Provider**: Claude only. Tools `web_search_20260209` / `web_fetch_20260209` (dynamic filtering) on the models
  that support them (Opus 4.6+, Sonnet 4.6 / 5 / 5.5, Fable, Mythos), the basic `web_search_20250305` /
  `web_fetch_20250910` otherwise. `pause_turn` is continued at most twice by re-sending the turn; server-tool errors
  arrive as result blocks and are reported, never raised. **OpenAI / local providers: the web is unavailable** and
  the report says so (their equivalent is not documented in this project and was never tried).
* **Lists**: a request is «official sites only» by default → `allowed_domains` = `INVESTIGACION_WEB_PERMITIDOS`
  (empty = the built-in list in `dominios.py`: `*.gob.es`, boe.es, the Tax Agency, the Social Security, dgt.es,
  regional portals, europa.eu) minus the block-list. The open web → `blocked_domains` =
  `INVESTIGACION_WEB_BLOQUEADOS`. Never both (the API refuses it), at most 64 entries. **The same lists are applied
  again by code** to every page that comes back.
* **What is used**: only pages the model *fetched* (their full text is what quotes are checked against). Search
  results carry no readable text: they are listed as «seen, not read» and never cited.
* **Price**: web search is billed per search, not per token: `INVESTIGACION_PRECIO_BUSQUEDA_USD` (default 0.01, i.e.
  «$10 per 1,000 searches» as read in Anthropic's API documentation in November 2026 — check the current price).
  Web fetch has no per-call fee there; its content is billed as input tokens.

## Untrusted content

Retrieved text — web pages above all — is data, never instructions:

* it reaches the synthesis model only inside `envolver_no_confiable` marks (control characters removed, angle
  brackets neutralised, `llm/security.py` patterns redacted) and after this capability's broader patterns
  («ignora tus instrucciones», «disregard…», «nuevas instrucciones») are replaced;
* the web model is asked only to search and read; its own text is discarded;
* the gate drops any claim or quote with injection bait, any claim that sends the user to a web that is not a
  source, any figure not in its quotes; a source with bait is shown with a warning and lowers the confidence;
* the capability has no write or act operation, and its output to the brain is wrapped as untrusted data by
  `pedir` (`datos_de_terceros=True`).

Tests with adversarial pages: `tests/capacidades/investigacion/test_investigacion_servicio.py`
(`test_an_adversarial_page_cannot_steer_the_report`) and `test_investigacion_puerta.py`.

## Budget and cache

* Per research (`presupuesto.py`): model calls by depth (`rapida` 5, `normal` 6, `a_fondo` 8 — the plan never eats
  the calls reserved for the web and the synthesis), `INVESTIGACION_MAX_TOKENS` (300 000) and `INVESTIGACION_MAX_USD`
  (0.50), checked before each call. Every call also goes through the metering wrapper (`ops/consumo.py`): recorded in
  `llm_usage` under its purpose and subject to the daily limits. The web call is recorded with `registrar_externo`.
  The search fee is counted in the research's USD but **not** in `llm_usage` (a token table): the daily USD limits
  do not see it.
* Cache (`almacen.py`): one report per user, normalised question, sources, depth, web mode, relevant settings and
  **day (Europe/Madrid)**; asking again the same day costs nothing (`desde_cache`). Kept `INVESTIGACION_CACHE_DIAS`
  (7) days. Failed or capped researches are not cached.

## Interfaces

* **Contract**: `pedir("investigacion", "investigar", {"pregunta", "ambito"?, "fuentes"?, "profundidad"?,
  "solo_oficiales"?}, ContextoPeticion(...))` → `ResultadoCapacidad` with `datos` = the report, `citas` = the cited
  sources, `texto_para_modelo` = the report as text, wrapped as untrusted data.
* **Agent tool** `investigar` (`herramienta.py`): offered by `agent/agent.py` only with `INVESTIGACION_AGENTE=true`
  (default off: the agent's tools and prompt are unchanged). It **refuses a question that contains a value of the
  user's case** (figures, names, places) that the user did not write in the conversation.
* **Long-running tasks** (`tareas.py`): `CapacidadInvestigacionTareas` adds the operation `investigar` to the tasks'
  `investigacion` capability (`webs_oficiales` unchanged), with the same privacy gate.
* **API** (`api/routers/investigacion.py`): `GET /investigacion/estado`, `POST /investigacion/consentimiento`,
  `POST /investigacion`, `GET /investigacion/exportar`, `DELETE /investigacion/datos`.
* **UI**: panel «Investigar» (`frontend/src/components/InvestigarPanel.tsx`): question, sources, web consent,
  official-only, depth; the report with quotes, sources table, conflicts, what is unverified and the cost.

## Privacy

* **What is sent to the model provider**: the question (and the optional domain hint) to plan and synthesise, and
  the retrieved public texts. With the web on and consented, the question also goes to the provider's search
  engine. **Never the user's case**: the capability does not read it; the agent tool and the task step refuse a
  question carrying case values. The UI says so next to the question.
* **What is stored** (own SQLite file `INVESTIGACION_DB_PATH`): the web consent history and the cached reports
  (question included), per user (`local` without accounts). `GET /investigacion/exportar` returns them;
  `DELETE /investigacion/datos` erases them; on first open, with accounts on, rows of accounts that no longer exist
  are purged. For «delete my account» / «download my data», call `capacidad.borrar_usuario(user_id)` /
  `capacidad.exportar_usuario(user_id)` (see the wiring list below).

## Settings

| Setting | Default | Meaning |
|---|---|---|
| `INVESTIGACION_WEB` | false | Allow the web (each user must still consent). |
| `INVESTIGACION_WEB_PERMITIDOS` | (built-in official list) | Allow-list for «official sites only». |
| `INVESTIGACION_WEB_BLOQUEADOS` | (none) | Hosts never used. |
| `INVESTIGACION_MAX_USD` / `INVESTIGACION_MAX_TOKENS` | 0.50 / 300000 | Caps per research. |
| `INVESTIGACION_PRECIO_BUSQUEDA_USD` | 0.01 | Fee per web search. |
| `INVESTIGACION_CACHE_DIAS` | 7 | Days a report is kept. |
| `INVESTIGACION_DB_PATH` | `./data/investigacion.sqlite3` | The capability's store. |
| `INVESTIGACION_AGENTE` | false | Offer `investigar` to the central agent. |

Routing purposes (`agent/enrutado.py`): `investigacion:planificar` (small), `investigacion:web` (medium — must be a
model that takes the web tools), `investigacion:sintetizar` (medium).

## What never ran against reality

* The web search / web fetch call with a real API key (no key on the build machine): request shape, parsing,
  `pause_turn` and usage were tested with a fake client built from the documented shapes.
* The planner and synthesis prompts with a real model (scripted fakes only).
* The BOE search and consolidated texts **did** run against www.boe.es.
