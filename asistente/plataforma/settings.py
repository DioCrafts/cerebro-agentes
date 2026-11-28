import logging
import os
from pathlib import Path


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


# --- Embeddings -------------------------------------------------------------
# The app uses a single local embedder via MLX (Apple Silicon native, bf16,
# no quantization). The converted model directory contains both the bf16
# weights for the MLX runtime AND the HuggingFace tokenizer files that the
# Docling HybridChunker uses for token-aware chunk splitting — so a single
# ``EMBEDDING_MODEL`` path serves indexing, querying, and chunking. Convert a
# new model with ``scripts/convert_to_mlx.py``.
EMBEDDING_MODEL = os.environ.get("EMBEDDING_MODEL", "data/models/qwen3-emb-mlx-bf16")
# How many chunks the embedder processes per forward pass. Default 32 is the
# empirical optimum on M-series for Qwen3-Embedding-0.6B. Counter-intuitively,
# larger batches slow things down because the underlying tokenizer pads each
# batch to its longest member without length-sorting; bigger batches end up
# containing more length variance, wasting attention compute on padding
# tokens. Override only after measuring on your corpus + hardware.
EMBEDDING_BATCH_SIZE = int(os.environ.get("EMBEDDING_BATCH_SIZE", "32"))
# Hard cap on per-input tokens at encode time. Qwen3-Embedding ships with
# ``max_seq_length=32768`` (its native context). Capping at 512 matches the
# chunker's budget and protects throughput from any chunk that accidentally
# slips past the chunker (defence-in-depth). The cap is **defensive only**:
# benchmarks on the current corpus show no measurable speedup from it,
# because attention cost depends on actual sequence length, not on the cap.
EMBEDDING_MAX_SEQ_LENGTH = int(os.environ.get("EMBEDDING_MAX_SEQ_LENGTH", "512"))

# --- Reranker (Qwen3-Reranker on MLX) ---------------------------------------
# Cross-encoder pass after first-stage dense retrieval. Scores (query, doc)
# pairs directly and re-orders the candidate set, surfacing the docs that
# actually answer the query (vs. docs that are merely topically similar).
#
# Measured A/B against the full 11 k-doc corpus (15-scenario fictional-user
# battery): +2 abstain→cited rescues, -1 cited→abstain regression, net +1
# cited, latency ~2× per query (6.5 s → 12.3 s median). Zero hallucinations
# in both modes. The rescues are substantively meaningful (plusvalía
# devolución + ISD donaciones — concrete tax advice users actually need);
# the regression is "abstain" rather than wrong advice. Net positive for a
# fiscal advisor where correctness >> latency, so we enable by default.
#
# OFF by default since 2026-11-26. That A/B was one run of 15 questions. On the retrieval benchmark (77 queries
# with a known article) the reranker never earned its 3 seconds: over the Docling chunks it gained 2 queries net
# (the 24 November baseline), and over the law indexed by article it makes the result worse — the right article
# is first in 56 % of the queries without it and in 38 % with it, among the first three in 73 % and 68 %
# (docs/cerebro/benchmark-recuperacion.md). Not measured: its effect on doctrine and AEAT pages, which this
# machine's benchmark corpus does not have. ``RERANKER_ENABLED=true`` turns it back on.
RERANKER_ENABLED = _env_bool("RERANKER_ENABLED", default=False)
RERANKER_MODEL = os.environ.get("RERANKER_MODEL", "data/models/qwen3-reranker-mlx-bf16")
# The task the reranker is told it is judging (Qwen3-Reranker follows an instruction; its model card recommends
# writing it in English). Empty: the model card's generic one, about web search.
RERANKER_INSTRUCTION = os.environ.get("RERANKER_INSTRUCTION", "").strip()
# After reranking, keep the top-K documents to send to the LLM.
# ``RERANKER_FIRST_STAGE_K`` controls first-stage recall (how many docs reach
# the reranker); this controls precision of what the LLM finally sees. Raised from 5 to 8
# (2026-06-02) to ATTACK FABRICATION: when the LLM was given only the top-5
# reranked chunks for under-specified queries (e.g. "reducción 95% empresa
# familiar"), it fell back to general knowledge and produced fluent-but-
# ungrounded answers (faith=0 with confident tone). 8 chunks widens the
# material the LLM has, reducing the temptation to fill gaps. Cost: +50%
# context tokens to the LLM, +5-10% latency. Acceptable for fiscal advisor
# where hallucination is strictly forbidden.
RERANKER_TOP_K = int(os.environ.get("RERANKER_TOP_K", "8"))
# Reranker first-stage k: when reranker is enabled we bump the first-stage
# top-k so the reranker has more candidates to reorder. Raised from 20 to 30
# alongside the RERANKER_TOP_K bump — the reranker needs proportionally more
# raw recall to keep choosing 8 *good* chunks instead of 5.
RERANKER_FIRST_STAGE_K = int(os.environ.get("RERANKER_FIRST_STAGE_K", "30"))

# --- Contextual embeddings (Anthropic September 2024) -----------------------
# At index time, prepend each chunk with a 1-sentence contextual prefix
# generated by a local Qwen3 instruct model. The intuition: a chunk that
# reads "El plazo es de 30 días hábiles" is unanchored — the embedder
# cannot disambiguate which deadline (ISD donaciones, IVA, modelo 720…).
# The contextual prefix supplies the anchor, so colloquial queries
# ("¿plazo donaciones?", "¿régimen Beckham?") align with chunks whose
# raw text uses the formal terminology ("art 9.1.a LIRPF", "art 93
# LIRPF"). Anthropic reported 35-50 % retrieval-failure reduction on
# their benchmarks.
#
# Implementation note: changing this flag (or the model / max-tokens)
# **invalidates the entire corpus index** via the pipeline fingerprint —
# the chunks under it are embeddings of a different text. Off by default
# so existing indexes stay valid; flip to ``true`` and run the processor
# with ``--full --overwrite-output`` to rebuild.
CONTEXTUAL_EMBEDDINGS_ENABLED = _env_bool("CONTEXTUAL_EMBEDDINGS_ENABLED", default=False)
# Local MLX bf16 model that generates the prefix. Qwen3-1.7B is the
# sweet spot: Qwen3-0.6B echoes the prompt schema instead of filling it,
# Qwen3-4B + would make the full-corpus pass too slow.
CONTEXTUAL_MODEL_PATH = os.environ.get("CONTEXTUAL_MODEL_PATH", "data/models/qwen3-1.7b-instruct-mlx-bf16")
# Per-chunk generation budget. 80 tokens is enough for a single Spanish
# sentence (~30 words) plus end-of-turn marker; smaller values risk
# truncating, larger ones inflate ingest wall-clock without quality gain.
CONTEXTUAL_MAX_TOKENS = int(os.environ.get("CONTEXTUAL_MAX_TOKENS", "80"))

# --- Corpus index -------------------------------------------------------------
# One SQLite file, <INDICE_DIR>/indice.sqlite: the vectors in sqlite-vec (cosine), the text in FTS5 (BM25) and the
# metadata in JSON. No pickle, so opening it never runs anyone's code. The directory also holds the ingestion
# manifest (manifest.json) that the processor writes next to it.
INDICE_DIR = os.environ.get("INDICE_DIR", "data/indice")
INDICE_SQLITE_PATH = os.environ.get("INDICE_SQLITE_PATH", os.path.join(INDICE_DIR, "indice.sqlite"))
if os.environ.get("FAISS_INDEX_PATH") and not os.environ.get("INDICE_DIR"):
    # The FAISS index is gone (docs/cerebro/benchmark-recuperacion.md): say so instead of silently searching nothing.
    logging.getLogger(__name__).warning(
        "FAISS_INDEX_PATH ya no se usa: el índice es %s. Define INDICE_DIR (y migra o reconstruye el índice).",
        INDICE_SQLITE_PATH,
    )
# First stage of the corpus search: dense (sqlite-vec) alone, or hybrid, the weighted RRF of the dense and the
# FTS5 rankings. See docs/cerebro/benchmark-recuperacion.md for the measurement behind the default.
# On by default since 2026-11-24: on the 77 measurable benchmark queries RRF put 2 more citations in the top-3 and
# lost none, at the price of 3 fewer first places; the agent reads 6 fragments per search, not one.
# Weights 0.9 / 0.1 since 2026-11-26 (they were 0.6 / 0.4). With the law indexed by article, lexical weight costs
# first places on the benchmark: the right article is first in 56 % of the 77 queries with dense search alone, in
# 49 % at 0.9 / 0.1, 45 % at 0.8 / 0.2 and 42 % at 0.6 / 0.4. Among the 6 fragments the agent reads it is there in
# 82 % with dense alone and down to 0.8 / 0.2, and in 78 % at 0.6 / 0.4. The lexical side stays on, light, for what
# an embedding blurs and the benchmark does not ask about: form numbers and identifiers in AEAT pages and rulings.
BM25_ENABLED = _env_bool("BM25_ENABLED", default=True)
BM25_DENSE_WEIGHT = float(os.environ.get("BM25_DENSE_WEIGHT", "0.9"))
BM25_BM25_WEIGHT = float(os.environ.get("BM25_BM25_WEIGHT", "0.1"))

DECISION_DEBUG_MODE = _env_bool("DECISION_DEBUG_MODE", default=False)
DECISION_STATE_DB_PATH = os.environ.get("DECISION_STATE_DB_PATH", "./data/decision_state.sqlite3")
# Validation / repeal status of the decree-laws some parameters depend on (written by `proactive normas-check`).
NORM_STATUS_PATH = os.environ.get("NORM_STATUS_PATH", "./data/_estado_normas.json")
# Decision rules live at the repo root (sibling of the asistente package).
# Resolve to an ABSOLUTE path so the engine loads them regardless of the process
# working directory (uvicorn may start elsewhere). A relative "rules"
# silently found nothing from any other CWD — and RuleSet(min_length=1) then
# raised — stalling every turn. Overridable via the RULES_DIR env var.
RULES_DIR = os.environ.get("RULES_DIR", str(Path(__file__).resolve().parents[2] / "rules"))
# --- API edge (auth + rate limiting) ----------------------------------------
# Both opt-in; defaults keep local dev and tests unauthenticated/unthrottled.
# Set HACIENDA_API_KEY (enforced via the X-API-Key header) and a positive
# API_RATE_LIMIT_PER_MIN before exposing the API beyond localhost — /agent spends
# LLM tokens and loads multi-GB models per process.
# Requests per minute per client (peer IP, or the configured API key). On by default; 0 disables it.
API_RATE_LIMIT_PER_MIN = int(os.environ.get("API_RATE_LIMIT_PER_MIN", "300"))
# Agent turns streamed at the same time by this process (/agent/stream). Each one runs in its own thread and
# spends model tokens; a turn over the cap waits (up to AGENT_STREAM_ESPERA_SEGUNDOS) before it is refused.
AGENT_MAX_TURNOS_SIMULTANEOS = max(1, int(os.environ.get("AGENT_MAX_TURNOS_SIMULTANEOS", "4")))
AGENT_STREAM_ESPERA_SEGUNDOS = float(os.environ.get("AGENT_STREAM_ESPERA_SEGUNDOS", "30"))

# --- Agent (tool-using advisor) ---------------------------------------------
# Which LLM drives the agent loop and the verifier:
#   anthropic (default) | openai | local (any OpenAI-compatible server).
LLM_PROVIDER = os.environ.get("LLM_PROVIDER", "anthropic")
ANTHROPIC_MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-opus-5-5")
# Thinking depth / token spend for Claude. Legal reasoning over several sources
# is intelligence-sensitive, so the floor is "high" (Opus 5.5 would otherwise
# default to "medium"). low | medium | high | xhigh | max.
ANTHROPIC_EFFORT = os.environ.get("ANTHROPIC_EFFORT", "high")
# Re-run a request another Claude model when a safety classifier declines it
# (server-side fallback). Only applied to models that support it.
ANTHROPIC_REFUSAL_FALLBACK = _env_bool("ANTHROPIC_REFUSAL_FALLBACK", default=True)
# gpt-6.2-sol: OpenAI's newest general model open to every developer (pricing page read on 2026-11-24), at the same
# price as Claude Sonnet 5.5. gpt-4o-mini (2024) was the default before.
OPENAI_AGENT_MODEL = os.environ.get("OPENAI_AGENT_MODEL", "gpt-6.2-sol")
# Which OpenAI wire API the openai/local providers speak: "responses" (OpenAI's own API) or
# "chat" (Chat Completions, what Ollama, LM Studio, mlx_lm.server and vLLM implement).
# Empty = automatic: chat for the local provider and for a local OPENAI_BASE_URL, else responses.
OPENAI_API_STYLE = os.environ.get("OPENAI_API_STYLE", "").strip().lower()
LOCAL_LLM_BASE_URL = os.environ.get("LOCAL_LLM_BASE_URL", "http://127.0.0.1:11434/v1")  # Ollama default
LOCAL_LLM_MODEL = os.environ.get("LOCAL_LLM_MODEL", "")
LOCAL_LLM_API_KEY = os.environ.get("LOCAL_LLM_API_KEY", "local")
LOCAL_LLM_TIMEOUT = float(os.environ.get("LOCAL_LLM_TIMEOUT", "300"))
# The verifier may run on a different provider/model than the agent (e.g. a
# cheaper one, or a different vendor for an independent read). Empty = same.
VERIFIER_PROVIDER = os.environ.get("VERIFIER_PROVIDER", "")
VERIFIER_MODEL = os.environ.get("VERIFIER_MODEL", "")
# Hard cap on model turns per question (each turn may issue several searches).
AGENT_MAX_STEPS = int(os.environ.get("AGENT_MAX_STEPS", "8"))
# How many times the agent may rewrite an answer the verifier rejected.
AGENT_MAX_REVISIONS = int(os.environ.get("AGENT_MAX_REVISIONS", "1"))
AGENT_SEARCH_DEFAULT_K = int(os.environ.get("AGENT_SEARCH_DEFAULT_K", "6"))
# Thinking depth for the fact extractor when it runs on Claude.
DECISION_EXTRACTOR_EFFORT = os.environ.get("DECISION_EXTRACTOR_EFFORT", "low")
# Model for the fact extractor (empty: the provider's). The extractor reads the environment at call time;
# this copy lets the model routing (agent/enrutado.py) see that a model was chosen for purpose "hechos".
DECISION_EXTRACTOR_MODEL = os.environ.get("DECISION_EXTRACTOR_MODEL", "")

# --- Doctrine graph ----------------------------------------------------------
# SQLite file built by `python -m asistente.plataforma.cli.doctrine_graph build`. When it
# exists the agent annotates every retrieved ruling/consulta with its validity
# (current, qualified, superseded, disputed); when it does not, nothing changes.
DOCTRINE_GRAPH_PATH = os.environ.get("DOCTRINE_GRAPH_PATH", "./data/doctrine_graph.sqlite3")

# --- Agent team (planner + researchers + critic) ----------------------------
# auto   : a single agent answers; the team is used only when the question spans
#          several taxes (per the planner) or the single agent hit conflicting or
#          superseded sources it did not resolve.
# always : every question goes through the team (slow and costly; for evaluation).
# never  : single agent only.
AGENT_TEAM_MODE = os.environ.get("AGENT_TEAM_MODE", "auto")
# Model turns each researcher / the critic may take.
SUBAGENT_MAX_STEPS = int(os.environ.get("SUBAGENT_MAX_STEPS", "4"))
# Run the two researchers concurrently (their LLM calls; corpus searches are serialised).
SUBAGENTS_PARALLEL = _env_bool("SUBAGENTS_PARALLEL", default=True)

# --- Notification delivery outside the app ----------------------------------
# All optional; an empty value keeps that channel off. There are no user
# accounts yet, so everything goes to ONE owner/operator destination (see
# asistente/nucleo/proactive/entrega.py). Secrets (the webhook URL, the SMTP
# password) must come from the environment, never from code.
NOTIFY_WEBHOOK_URL = os.environ.get("NOTIFY_WEBHOOK_URL", "")
# json : POST {"title": ..., "text": ...} (Slack-compatible and generic webhooks).
# text : POST the plain-text body with a ``Title`` header (ntfy.sh topics).
NOTIFY_WEBHOOK_FORMAT = os.environ.get("NOTIFY_WEBHOOK_FORMAT", "json")
NOTIFY_SMTP_HOST = os.environ.get("NOTIFY_SMTP_HOST", "")
NOTIFY_SMTP_PORT = int(os.environ.get("NOTIFY_SMTP_PORT", "587"))
NOTIFY_SMTP_USER = os.environ.get("NOTIFY_SMTP_USER", "")
NOTIFY_SMTP_PASSWORD = os.environ.get("NOTIFY_SMTP_PASSWORD", "")
# STARTTLS after connecting (the norm on port 587). Port 465 always uses
# implicit TLS, whatever this says.
NOTIFY_SMTP_STARTTLS = _env_bool("NOTIFY_SMTP_STARTTLS", default=True)
NOTIFY_EMAIL_FROM = os.environ.get("NOTIFY_EMAIL_FROM", "")
NOTIFY_EMAIL_TO = os.environ.get("NOTIFY_EMAIL_TO", "")

# --- Automatic re-encoding of legal parameters (asistente/conocimiento/recodificacion) ---
# When a group of parameters stops matching the text of its law, a model re-reads
# the law and PROPOSES new data; deterministic checks and an independent reviewer
# decide whether the proposal stands. Proposals are JSON files under this directory.
RECODE_PROPOSALS_PATH = os.environ.get("RECODE_PROPOSALS_PATH", "./data/recodificacion")
# true: a proposal that passed every check with nothing left to look at, and that the
# reviewer accepted, is written to the parameter files without waiting for a person
# (the calculator still says "pendiente de revisión humana" until ``params-confirm``).
# false (default): every proposal waits for ``params-apply``.
RECODE_AUTO_APPLY = _env_bool("RECODE_AUTO_APPLY", default=False)
# Reader passes per group before giving up ("sin_converger").
RECODE_MAX_ITER = int(os.environ.get("RECODE_MAX_ITER", "3"))
# Groups re-read in one run (each costs up to 2 LLM calls per pass); the rest wait for the next run.
RECODE_MAX_PER_RUN = int(os.environ.get("RECODE_MAX_PER_RUN", "10"))
# Models of the reader and of the reviewer; empty = the provider's default model.
# A different reviewer model makes the review more independent.
RECODE_MODEL = os.environ.get("RECODE_MODEL", "")
RECODE_REVIEWER_MODEL = os.environ.get("RECODE_REVIEWER_MODEL", "")
# true: ``boe-novedades`` runs the autopilot (detect -> verify -> re-encode -> notify)
# when it finishes, as ``--autopilot`` does. Off by default.
RECODE_AUTOPILOT = _env_bool("RECODE_AUTOPILOT", default=False)

# --- Scheduled-job watchdog (asistente/plataforma/ops/vigia.py) ---------------------
# JSON file with the last run / last success / last error of every scheduled job.
JOBS_STATE_PATH = os.environ.get("JOBS_STATE_PATH", "./data/_jobs_state.json")
# Dead-man's-switch URL (healthchecks.io convention) pinged after every watched
# job: the URL itself on success, ``<url>/fail`` on failure. A job-specific
# ``HEALTHCHECK_URL_<JOB_NAME_IN_UPPERCASE>`` (e.g. HEALTHCHECK_URL_BOE_NOVEDADES)
# takes precedence and is read when the job finishes.
HEALTHCHECK_URL = os.environ.get("HEALTHCHECK_URL", "")

# --- Format drift watch (asistente/conocimiento/formatos.py) -----------------------
# Record, on every real read, whether each external format (BOE JSON/XML/HTML,
# crawler listings, user documents) is still understood. Deterministic, no LLM.
FORMAT_WATCH = _env_bool("FORMAT_WATCH", default=True)
# JSON file with the recent observations and the status of every source.
FORMAT_STATE_PATH = os.environ.get("FORMAT_STATE_PATH", "./data/_formatos_state.json")
# Samples (known-good and latest bad, public sources only), applied specs,
# proposals and developer reports.
FORMAT_DIR = os.environ.get("FORMAT_DIR", "./data/formatos")
# Apply a proposed extraction spec without a person when it passed the
# deterministic gate AND the independent reflection had no objection. Off by
# default: proposals wait for `ops formatos apply`.
FORMAT_AUTO_APPLY = _env_bool("FORMAT_AUTO_APPLY", default=False)
# Diagnosis rounds per source before giving up with a report for a developer
# ("sin_converger"; each round costs up to two LLM calls: diagnosis and reflection).
FORMAT_MAX_ITER = int(os.environ.get("FORMAT_MAX_ITER", "3"))
# Models of the diagnosis and of the reflection; empty = the provider's default
# model. A different reviewer model makes the reflection more independent.
FORMAT_MODEL = os.environ.get("FORMAT_MODEL", "")
FORMAT_REVIEWER_MODEL = os.environ.get("FORMAT_REVIEWER_MODEL", "")
# Keep an ANONYMISED skeleton (known labels only; every other word and every
# figure blanked) of user documents whose reading failed, to diagnose reader
# drift. Off by default: nothing derived from a user document is stored.
FORMAT_DOCUMENT_SKELETONS = _env_bool("FORMAT_DOCUMENT_SKELETONS", default=False)

# --- User accounts and privacy (asistente/plataforma/auth) --------------------------
# off      : no accounts; anyone holding a case id can read that case (the
#            historical behaviour — only acceptable on a single-user machine).
# required : every case-scoped endpoint needs a session (Authorization: Bearer)
#            and a case is visible only to the account that created it.
# An unknown value is refused at start-up: a typo must not silently leave the
# cases unprotected.
AUTH_MODE = os.environ.get("AUTH_MODE", "off").strip().lower()
if AUTH_MODE not in {"off", "required"}:
    raise ValueError(f"AUTH_MODE must be 'off' or 'required', not {AUTH_MODE!r}")
# How long a session token is valid after login.
AUTH_SESSION_TTL_HOURS = float(os.environ.get("AUTH_SESSION_TTL_HOURS", "24"))
# scrypt work factor for password hashes (memory is 128 * N * r bytes: 128 MB at
# the default). Stored with every hash, so raising it never locks anyone out.
AUTH_SCRYPT_N = int(os.environ.get("AUTH_SCRYPT_N", str(2**17)))
# Failed logins allowed per e-mail + client address inside the window.
AUTH_LOGIN_MAX_ATTEMPTS = int(os.environ.get("AUTH_LOGIN_MAX_ATTEMPTS", "5"))
AUTH_LOGIN_WINDOW_SECONDS = int(os.environ.get("AUTH_LOGIN_WINDOW_SECONDS", "900"))
# Where the operator publishes the privacy notice shown on the register screen.
# Empty: the app shows its built-in technical summary, which is NOT a legal notice.
PRIVACY_NOTICE_URL = os.environ.get("PRIVACY_NOTICE_URL", "")

# --- LLM spend: measurement and limits (asistente/plataforma/ops/consumo.py) ---------
# All optional; empty = no limit. The USD limits are per calendar day in
# Europe/Madrid and rely on the price table (asistente/plataforma/ops/data/precios_llm.json):
# calls to a model without a known price are recorded with no cost and do not
# count towards them. Read when the agent is built; an invalid value is refused.
LLM_DAILY_BUDGET_USD = os.environ.get("LLM_DAILY_BUDGET_USD", "")
LLM_DAILY_BUDGET_USD_PER_USER = os.environ.get("LLM_DAILY_BUDGET_USD_PER_USER", "")
# Hard cap on model calls spent on ONE user question (agent turns, planner,
# researchers, critic and verifier together). On by default: a loop must never
# spend without end; set it to an empty value explicitly to lift it.
LLM_MAX_CALLS_PER_QUESTION = os.environ.get("LLM_MAX_CALLS_PER_QUESTION", "60")
# Replacement price table (same format as the bundled one), for when prices change.
LLM_PRICES_PATH = os.environ.get("LLM_PRICES_PATH", "")

# --- LLM routing by purpose, prompt caching, batches (asistente/nucleo/agent/enrutado.py) ---
# Every model call has a purpose (agente, verificador, especialista:<clave>, recodificacion…)
# mapped to a tier (pequeno | mediano | grande). All empty = the historical behaviour: the
# provider's model for every purpose. "recomendado" = the documented profile (docs/operacion.md).
LLM_ROUTING_PROFILE = os.environ.get("LLM_ROUTING_PROFILE", "")
# Model (and Claude effort) of each tier; wins over the profile. Empty = profile / provider's model.
LLM_MODEL_SMALL = os.environ.get("LLM_MODEL_SMALL", "")
LLM_MODEL_MEDIUM = os.environ.get("LLM_MODEL_MEDIUM", "")
LLM_MODEL_LARGE = os.environ.get("LLM_MODEL_LARGE", "")
LLM_EFFORT_SMALL = os.environ.get("LLM_EFFORT_SMALL", "")
LLM_EFFORT_MEDIUM = os.environ.get("LLM_EFFORT_MEDIUM", "")
LLM_EFFORT_LARGE = os.environ.get("LLM_EFFORT_LARGE", "")
# JSON object purpose (glob allowed) -> tier name or model id, e.g. '{"especialista:*": "pequeno"}'.
# A single purpose can also be set with LLM_ROUTE_<PURPOSE> (upper case, non-alphanumerics -> "_").
LLM_ROUTES = os.environ.get("LLM_ROUTES", "")
# Retry ONCE on the next tier an output that fails its validation, when that tier is another model.
LLM_ESCALATION = _env_bool("LLM_ESCALATION", default=True)
# Prompt-cache lifetime for Claude: 5m (default, cheaper writes) or 1h (writes cost more; survives gaps).
LLM_CACHE_TTL = os.environ.get("LLM_CACHE_TTL", "5m")
# Run the recodificación (params-recode, the autopilot, params-eval) through the provider's batch
# API: about half the price, results within hours instead of seconds. Off by default.
RECODE_BATCH = _env_bool("RECODE_BATCH", default=False)
# Resumable state of the batches in flight (one JSON file per kind of run).
LLM_BATCH_STATE_PATH = os.environ.get("LLM_BATCH_STATE_PATH", "./data/_lotes")
LLM_BATCH_POLL_SECONDS = float(os.environ.get("LLM_BATCH_POLL_SECONDS", "60"))
# Give up waiting (the state is kept: the next run resumes the same batch).
LLM_BATCH_MAX_WAIT_HOURS = float(os.environ.get("LLM_BATCH_MAX_WAIT_HOURS", "24"))

# --- Household modules (asistente/ambitos) ---------------------------------
# Every package ``asistente.ambitos.<clave>`` exposing ``MODULO`` is discovered at
# start-up (see asistente/nucleo/modulos/registro.py). Comma-separated keys listed here
# are switched off for everyone (their tools, notices and facts are not offered).
MODULOS_DESACTIVADOS = os.environ.get("MODULOS_DESACTIVADOS", "")
# Modules active for an account that has not chosen yet, and for every case when
# AUTH_MODE=off (a personal deployment has no per-user choice). Comma-separated
# keys, or "todos". The default is privacy by default (art. 25.2 GDPR): only the
# tax module, the service the user signed up for; each other module is opted into.
MODULOS_ACTIVOS_POR_DEFECTO = os.environ.get("MODULOS_ACTIVOS_POR_DEFECTO", "fiscal")
# Model turns a module specialist may take when the central agent consults it.
ESPECIALISTA_MAX_STEPS = int(os.environ.get("ESPECIALISTA_MAX_STEPS", "4"))
# Baseline of the BOE update date of every module source, for ``params-check``
# (a module's law updated after that date is listed for re-reading).
MODULOS_FUENTES_STATE_PATH = os.environ.get("MODULOS_FUENTES_STATE_PATH", "./data/_modulos_fuentes_state.json")

# --- Event bus (asistente/nucleo/eventos) -------------------------------------------
# Profile changes, new BOE norms, new mortgage indices, statement imports and module switches write an
# event to the cases' database; the API (in-process) and ``python -m asistente.plataforma.cli.eventos worker`` wake
# only the modules concerned. ``proactive run-daily`` stays as the safety net. false: no event is written.
EVENTOS_ACTIVOS = _env_bool("EVENTOS_ACTIVOS", True)
# The API drains the bus right after each change (a background thread). false: only the worker does.
EVENTOS_DESPACHO_EN_PROCESO = _env_bool("EVENTOS_DESPACHO_EN_PROCESO", True)
# Seconds between polls when idle (worker and in-process dispatcher).
EVENTOS_INTERVALO_SEGUNDOS = float(os.environ.get("EVENTOS_INTERVALO_SEGUNDOS", "5"))
# Attempts per delivery before it goes to the dead letters; backoff base (doubles each retry, max 1 h).
EVENTOS_MAX_INTENTOS = int(os.environ.get("EVENTOS_MAX_INTENTOS", "5"))
EVENTOS_BACKOFF_SEGUNDOS = float(os.environ.get("EVENTOS_BACKOFF_SEGUNDOS", "30"))
# Lease of a claimed delivery: a worker that dies holding it loses it after this long.
EVENTOS_ARRIENDO_SEGUNDOS = float(os.environ.get("EVENTOS_ARRIENDO_SEGUNDOS", "300"))
# ``ops doctor`` reports the bus when work has been waiting longer than this (0: never).
EVENTOS_ATRASO_MAX_MINUTOS = float(os.environ.get("EVENTOS_ATRASO_MAX_MINUTOS", "30"))

# --- Browser agent (asistente/capacidades/navegador) -----------------------------------
# Kill switch: false (default) = no browser is ever started and every task endpoint answers 503.
NAVEGADOR_ACTIVO = _env_bool("NAVEGADOR_ACTIVO", default=False)
# How the user logs in: "ventana" (a headed Chromium on the machine running the service, for a
# personal deployment) and/or "sesion" (a session the user hands over for one run).
NAVEGADOR_MODOS = os.environ.get("NAVEGADOR_MODOS", "ventana,sesion")
# Chromium executable; empty = the one Playwright installed.
NAVEGADOR_CHROMIUM = os.environ.get("NAVEGADOR_CHROMIUM", "")
# Rate limits: tasks a user may start per 24 h, and tasks running at once in the whole service.
NAVEGADOR_MAX_TAREAS_DIA = int(os.environ.get("NAVEGADOR_MAX_TAREAS_DIA", "6"))
NAVEGADOR_MAX_SIMULTANEAS = int(os.environ.get("NAVEGADOR_MAX_SIMULTANEAS", "2"))
# Redacted diagnoses of failed steps (per case, erased with it, 30 days at most).
NAVEGADOR_DIR = os.environ.get("NAVEGADOR_DIR", "./data/navegador")
# Ask the LLM for a replacement selector when a page changed (behind a deterministic gate).
NAVEGADOR_REPARAR_SELECTORES = _env_bool("NAVEGADOR_REPARAR_SELECTORES", default=False)
# Tests and demos only: JSON {real origin: LOCAL origin} pointing the scripts at local mock sites.
NAVEGADOR_ORIGENES_SUSTITUTOS = os.environ.get("NAVEGADOR_ORIGENES_SUSTITUTOS", "")

# --- Continuous evaluation (asistente/nucleo/evaluacion) ---------------------------
# The ACTIVE prompt/model variant, written only by `python -m asistente.plataforma.cli.evaluacion promote`.
# Empty = asistente/nucleo/evaluacion/variante_activa.json; without that file the agent runs as always.
EVALUACION_VARIANTE_ACTIVA_PATH = os.environ.get("EVALUACION_VARIANTE_ACTIVA_PATH", "")

# --- Platform: migrations and processes (asistente/plataforma, docs/despliegue.md) ---
# Stores bring their tables up to date (schema_migrations) when they open a connection, and the API migrates
# the cases' database at start-up. false: the stores create their tables as they did before the migrations.
MIGRACIONES_AUTOMATICAS = _env_bool("MIGRACIONES_AUTOMATICAS", default=True)
# The API process drains the event bus itself (with EVENTOS_DESPACHO_EN_PROCESO). false: only the worker
# (`python -m asistente.plataforma.cli.plataforma worker`) dispatches; the API just writes the events.
EVENTOS_DESPACHO_EN_API = _env_bool("EVENTOS_DESPACHO_EN_API", default=True)
# Scheduled jobs the worker runs: empty = the default set; "ninguno" = none; or comma-separated names.
PLATAFORMA_TRABAJOS = os.environ.get("PLATAFORMA_TRABAJOS", "")
# Jobs removed from that set (comma-separated names).
PLATAFORMA_TRABAJOS_DESACTIVADOS = os.environ.get("PLATAFORMA_TRABAJOS_DESACTIVADOS", "")
# A slot missed while no worker ran (restart, deploy) still runs if the worker is back within this many hours.
PLATAFORMA_RECUPERAR_HORAS = float(os.environ.get("PLATAFORMA_RECUPERAR_HORAS", "6"))
# Wall-clock limit of one scheduled child-process job; past it its process group is killed (exit code 124).
PLATAFORMA_TIMEOUT_TRABAJO_S = float(os.environ.get("PLATAFORMA_TIMEOUT_TRABAJO_S", "3600"))
# Seconds between the worker's heartbeats (and scheduler checks).
PLATAFORMA_LATIDO_SEGUNDOS = float(os.environ.get("PLATAFORMA_LATIDO_SEGUNDOS", "30"))
# Extra modules (comma-separated) whose ``registrar_en_plataforma(registro)`` adds services or jobs to the worker.
PLATAFORMA_EXTENSIONES = os.environ.get("PLATAFORMA_EXTENSIONES", "")

# --- Research with sources (asistente/capacidades/investigacion, docs/capacidades/investigacion.md) ---
# The open web through the model provider's server-side search: off by default; each user must also consent.
INVESTIGACION_WEB = _env_bool("INVESTIGACION_WEB", default=False)
# Allow-list for «official sites only» (comma-separated hosts; empty = the built-in official list) and block-list.
INVESTIGACION_WEB_PERMITIDOS = os.environ.get("INVESTIGACION_WEB_PERMITIDOS", "")
INVESTIGACION_WEB_BLOQUEADOS = os.environ.get("INVESTIGACION_WEB_BLOQUEADOS", "")
# Caps of ONE research (on top of the daily LLM limits), the web search fee, cache days, the store, the agent tool.
INVESTIGACION_MAX_USD = os.environ.get("INVESTIGACION_MAX_USD", "0.50")
INVESTIGACION_MAX_TOKENS = os.environ.get("INVESTIGACION_MAX_TOKENS", "300000")
INVESTIGACION_PRECIO_BUSQUEDA_USD = os.environ.get("INVESTIGACION_PRECIO_BUSQUEDA_USD", "0.01")
INVESTIGACION_CACHE_DIAS = os.environ.get("INVESTIGACION_CACHE_DIAS", "7")
INVESTIGACION_DB_PATH = os.environ.get("INVESTIGACION_DB_PATH", "./data/investigacion.sqlite3")
INVESTIGACION_AGENTE = _env_bool("INVESTIGACION_AGENTE", default=False)

# --- cerebro (household brain) ---
# "hogar" (default): the household brain answers (it understands the request, consults the specialists and writes one
# answer; the tax advisor is one of them). "fiscal": the single tax advisor exactly as it was (legacy mode).
CEREBRO_MODO = os.environ.get("CEREBRO_MODO", "hogar")
# Below this confidence in what was asked (0..1) the brain asks one clarifying question instead of guessing.
CEREBRO_UMBRAL_CONFIANZA = float(os.environ.get("CEREBRO_UMBRAL_CONFIANZA", "0.45"))
# The small model (purpose cerebro:intencion) is asked only when the rules are not sure; false: rules only.
CEREBRO_INTENCION_MODELO = _env_bool("CEREBRO_INTENCION_MODELO", default=True)
# The brain's synthesis: model steps (tools included) and revisions after a failed gate (figures or claims).
CEREBRO_MAX_PASOS = int(os.environ.get("CEREBRO_MAX_PASOS", "6"))
CEREBRO_MAX_REVISIONES = int(os.environ.get("CEREBRO_MAX_REVISIONES", "2"))
# How many times the brain may consult the specialists again (replanning) in one turn. How many run at once is the
# collaboration runtime's CEREBRO_MAX_PARALELO (its block, below).
CEREBRO_MAX_REPLANES = int(os.environ.get("CEREBRO_MAX_REPLANES", "2"))
# Remembered items (conversation memory) handed to the brain per turn.
CEREBRO_MEMORIA_LIMITE = int(os.environ.get("CEREBRO_MEMORIA_LIMITE", "8"))

# --- recuerdos (household brain) -------------------------------------------------------
# Conversation memory (asistente/nucleo/recuerdos, docs/cerebro/memoria.md), backed by Hindsight: the user's own
# words of each turn are retained in a Hindsight memory bank per owner (case, account) and recalled for the next
# question. false: nothing new is retained or recalled (what was stored can still be seen, exported and erased).
MEMORIA_CONVERSACIONES = _env_bool("MEMORIA_CONVERSACIONES", default=True)
# An already running Hindsight server (http://host:port). Empty (default): the app starts and reuses a local
# embedded daemon (hindsight-all: its own process and embedded PostgreSQL under ~/.pg0), profile HINDSIGHT_PERFIL.
HINDSIGHT_URL = os.environ.get("HINDSIGHT_URL", "").strip().rstrip("/")
HINDSIGHT_PERFIL = os.environ.get("HINDSIGHT_PERFIL", "cerebro")
# The model Hindsight uses to extract facts and consolidate observations (its own calls, billed to the same key).
HINDSIGHT_LLM_PROVEEDOR = os.environ.get("HINDSIGHT_LLM_PROVEEDOR", "anthropic")
HINDSIGHT_LLM_MODELO = os.environ.get("HINDSIGHT_LLM_MODELO", "claude-haiku-5-5")
# Local multilingual embedder and the PostgreSQL dictionary of the BM25 arm (Hindsight's defaults are English-only).
HINDSIGHT_EMBEDDINGS = os.environ.get("HINDSIGHT_EMBEDDINGS", "BAAI/bge-m3")
HINDSIGHT_IDIOMA = os.environ.get("HINDSIGHT_IDIOMA", "spanish")
# Recall of one turn: Hindsight's budget (low | mid | high) and a hard timeout so a slow daemon never holds a turn.
HINDSIGHT_PRESUPUESTO = os.environ.get("HINDSIGHT_PRESUPUESTO", "mid")
HINDSIGHT_TIMEOUT_S = float(os.environ.get("HINDSIGHT_TIMEOUT_S", "8"))

# --- repaso (household brain) ----------------------------------------------------------
# The brain's proactive pass (asistente/nucleo/repaso, docs/cerebro/repaso.md): a weekly or daily review of what is
# new (inbox, mail, deadlines, BOE changes, tasks, the user's decisions) that writes ONE prioritised digest, in the
# weekly digest's place. Code collects and ranks; the small model (purpose cerebro:repaso) only writes, behind a
# deterministic gate. false (default): the weekly digest stays the deterministic one, exactly as before.
REPASO_ACTIVO = _env_bool("REPASO_ACTIVO", default=False)
# semanal (one digest per ISO week, the weekly digest's key) | diaria (one per day).
REPASO_FRECUENCIA = os.environ.get("REPASO_FRECUENCIA", "semanal")
# Weekly pass: the day (0 = lunes … 6 = domingo) and the time (Spanish, HH:MM) from which the `repaso` job writes it.
REPASO_DIA = int(os.environ.get("REPASO_DIA", "0"))
REPASO_HORA = os.environ.get("REPASO_HORA", "07:00")
# The job sends the cases' requests as ONE batch of the provider's batch API (cheaper; collected on a later run of
# the job). false, or a provider without batches: one synchronous call per case.
REPASO_POR_LOTES = _env_bool("REPASO_POR_LOTES", default=True)
# Points in one digest at most (the rest are counted: «y N avisos más en tu bandeja»).
REPASO_MAX_PUNTOS = int(os.environ.get("REPASO_MAX_PUNTOS", "7"))

# --- cerebro.colaboracion (household brain) -------------------------------------------
# The collaboration runtime (asistente/nucleo/cerebro/colaboracion, docs/cerebro/colaboracion.md): the specialists one
# turn consults run as a DAG of encargos, in parallel. Specialists running at the same time (1 = one after another,
# fully deterministic).
CEREBRO_MAX_PARALELO = int(os.environ.get("CEREBRO_MAX_PARALELO", "4"))
# Seconds one specialist may take on one encargo, its requests to other specialists included; past it the report says
# so and the specialist stops at its next model call. 0: no limit.
CEREBRO_TIMEOUT_ENCARGO_S = float(os.environ.get("CEREBRO_TIMEOUT_ENCARGO_S", "180"))
# Per-attempt HTTP timeout of the Anthropic client; empty = derived from CEREBRO_TIMEOUT_ENCARGO_S so that every
# attempt together ends before the encargo's deadline.
ANTHROPIC_TIMEOUT_S = os.environ.get("ANTHROPIC_TIMEOUT_S", "")
# How deep a request between specialists may go (1: a specialist may ask another, which may not ask a third). Never
# above the contract's MAX_PROFUNDIDAD_PETICIONES (2): a larger value is lowered to it.
CEREBRO_MAX_PROFUNDIDAD_PETICIONES = int(os.environ.get("CEREBRO_MAX_PROFUNDIDAD_PETICIONES", "2"))

# --- evaluacion (household brain) -----------------------------------------------------
# Learning from real mistakes (asistente/nucleo/evaluacion, docs/cerebro/aprendizaje.md). Accepted real cases (users'
# corrections turned into regression cases, masked) live in a PRIVATE SQLite file, never in the repository: empty =
# evaluacion_casos_reales.sqlite3 next to the cases' database (DECISION_STATE_DB_PATH).
EVALUACION_CASOS_REALES_DB_PATH = os.environ.get("EVALUACION_CASOS_REALES_DB_PATH", "")
# Promotion gate (`evaluacion promote`): paired cases needed to decide at all (fewer = «evidencia insuficiente»), and
# the candidate's cost and p95 latency allowed, relative to the active variant's on the same cases.
EVALUACION_GATE_MIN_CASOS = int(os.environ.get("EVALUACION_GATE_MIN_CASOS", "30"))
EVALUACION_GATE_COSTE_MAX_RELATIVO = float(os.environ.get("EVALUACION_GATE_COSTE_MAX_RELATIVO", "1.10"))
EVALUACION_GATE_LATENCIA_MAX_RELATIVA = float(os.environ.get("EVALUACION_GATE_LATENCIA_MAX_RELATIVA", "1.25"))

# --- consejo (household council) ------------------------------------------------------
# The council of AIs for HARD household decisions (asistente/nucleo/consejo, docs/consejo/arquitectura.md): convened
# by the brain or the user, always with a cost estimate the user confirms, run in the background, delivered as a
# «Dictamen del consejo». false: the brain never offers it and the API refuses to convene (what exists stays readable).
CONSEJO_ACTIVO = _env_bool("CONSEJO_ACTIVO", default=True)
# Model families for the members' diversity, comma separated («anthropic,openai»): a member's preferred family is used
# when configured. Empty: every member on the configured provider (diversity by role, instructions and temperature).
CONSEJO_FAMILIAS = os.environ.get("CONSEJO_FAMILIAS", "")
# Members working at the same time in one phase (1 = one after another).
CONSEJO_MAX_PARALELO = int(os.environ.get("CONSEJO_MAX_PARALELO", "3"))
# Deadline of one deliberation phase (all its members together); a member still running is left out.
CONSEJO_PLAZO_FASE_S = float(os.environ.get("CONSEJO_PLAZO_FASE_S", "600"))
# Hard cap of a session = the estimate shown to the user × this factor, never above CONSEJO_TOPE_MAX_USD (an estimate
# above that maximum cannot be convened). Past the cap no further model call is made: the engine rules with what it has
# (a partial ruling written by code, labelled «Dictamen parcial») or the session ends «fallida»; nothing is invented.
CONSEJO_FACTOR_TOPE = os.environ.get("CONSEJO_FACTOR_TOPE", "2.0")
CONSEJO_TOPE_MAX_USD = os.environ.get("CONSEJO_TOPE_MAX_USD", "3.00")
# Model calls one session may make in all (the dossier's specialists included): the session's own «per question» cap.
CONSEJO_MAX_LLAMADAS = int(os.environ.get("CONSEJO_MAX_LLAMADAS", "150"))
# Single-process deployments: start a confirmed session at once in a background thread of the API process (the job
# `consejo` is the safety net and runs what is left queued). false: only the job runs sessions.
CONSEJO_EN_SEGUNDO_PLANO = _env_bool("CONSEJO_EN_SEGUNDO_PLANO", default=True)
# Seconds a worker holds a running session (renewed on every phase); past it, another run may take it over.
CONSEJO_ARRIENDO_S = int(os.environ.get("CONSEJO_ARRIENDO_S", "900"))
# Runs of one session at most (a crash mid-session is retried once, then the session is marked «fallida»).
CONSEJO_MAX_INTENTOS = int(os.environ.get("CONSEJO_MAX_INTENTOS", "2"))
# A convocation the user neither confirms nor declines expires after these hours.
CONSEJO_CADUCIDAD_HORAS = int(os.environ.get("CONSEJO_CADUCIDAD_HORAS", "72"))
# Finished sessions (with their dossier and deliberation) are kept this many days, then purged by the job.
CONSEJO_RETENCION_DIAS = int(os.environ.get("CONSEJO_RETENCION_DIAS", "365"))
# Amount (EUR) from which a decision counts as «large» for the detector of hard decisions.
CONSEJO_IMPORTE_GRANDE_EUR = os.environ.get("CONSEJO_IMPORTE_GRANDE_EUR", "10000")

# --- consejo.deliberacion ---
# The council's deliberation engine (asistente/nucleo/consejo/deliberacion.py, docs/consejo/deliberacion.md). Seats of
# the council, comma separated, in order (3 to 7; known ids: prudente, fiscal, liquidez_familia, abogado_del_diablo,
# largo_plazo — an unknown id is skipped with a warning). CONSEJO_FAMILIAS and CONSEJO_MAX_PARALELO: the block above.
CONSEJO_MIEMBROS = os.environ.get("CONSEJO_MIEMBROS", "prudente,fiscal,liquidez_familia,abogado_del_diablo")
# Seed of the anonymous presentation orders (an integer). Empty: each session derives its own from its id, so a
# session is reproducible and two sessions do not share the same orders.
CONSEJO_SEMILLA = os.environ.get("CONSEJO_SEMILLA", "")

# --- evaluacion.consejo (household council) -------------------------------------------
# Evaluation of the council (asistente/nucleo/evaluacion/consejo_eval.py, docs/consejo/evaluacion.md). Its cases
# (categories decision and sesgo) are expensive: a model run of the suite includes them only with this switch on (or
# through `python -m asistente.nucleo.evaluacion.consejo_eval comparar`, which always runs them).
EVALUACION_CONSEJO = _env_bool("EVALUACION_CONSEJO", default=False)
# «¿Merece la pena el consejo?» — how much more than the brain alone the council may cost, RELATIVE to it: this many
# times when it is not worse, plus this many times per percentage point of rubric gain it demonstrates (the lower
# bound of the gain's 95 % interval), never above the maximum. Its p95 latency may be this many times the brain's
# (it runs in the background).
EVALUACION_CONSEJO_COSTE_RELATIVO_SIN_MEJORA = float(
    os.environ.get("EVALUACION_CONSEJO_COSTE_RELATIVO_SIN_MEJORA", "3.0")
)
EVALUACION_CONSEJO_COSTE_RELATIVO_POR_PUNTO = float(
    os.environ.get("EVALUACION_CONSEJO_COSTE_RELATIVO_POR_PUNTO", "0.5")
)
EVALUACION_CONSEJO_COSTE_RELATIVO_MAX = float(os.environ.get("EVALUACION_CONSEJO_COSTE_RELATIVO_MAX", "12.0"))
EVALUACION_CONSEJO_LATENCIA_MAX_RELATIVA = float(os.environ.get("EVALUACION_CONSEJO_LATENCIA_MAX_RELATIVA", "40.0"))
