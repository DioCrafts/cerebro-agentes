// Client for the FastAPI backend.
//
// The shapes come from the backend's OpenAPI schema (src/api/esquema.d.ts, `npm run tipos`); this file only
// gives them readable names. What the schema cannot describe (the SSE progress events) is typed here by hand.

import { trimHistory } from "./lib/historial";
import { type AuthMode } from "./lib/session";
import { leerCuerpoSse } from "./lib/sse";
import { isRecord } from "./lib/guards";
import type { Esquemas, Respuesta } from "./api/tipos";

// The /agent verdicts, plus "cited" / "uncited" from the legacy /qa grounding gate (older chats kept in
// localStorage still carry them).
export type AnswerMode = Esquemas["AgentMode"] | "cited" | "uncited";
export type RiskLevel = Esquemas["RiskLevel"];

// A source the answer cites (/agent): the id it cites inline ("F2"), its provenance and its validity per the
// doctrine graph (vigente | matizada | superada | discutida).
export type Citation = Esquemas["AgentCitation"];
export type ClaimCheck = Esquemas["ClaimCheck"];
export type ClaimVerdict = ClaimCheck["verdict"];
export type ToolTraceEntry = Esquemas["ToolTraceEntry"];
export type AgentResponse = Respuesta<"/agent", "post">;

// What a chat turn keeps of the answer (lib/storage.ts): built by the client from an AgentResponse, so it has no
// schema of its own.
export interface AnswerEnvelope {
  answer: string;
  mode: AnswerMode;
  citations: Citation[];
  raw_answer?: string | null;
  reason?: string | null;
  min_citations_required?: number;
  // Claims the verifier could not back with the cited fragment.
  warnings?: string[];
  // Cited sources whose validity is in question, and whether the answer itself
  // tells the user that the question has no settled criterion.
  conflicts?: string[];
  sinCriterioFirme?: boolean;
}

// ---- The household brain's report (asistente/nucleo/cerebro/contrato.py -> InformeCerebro) ----
export type EncargoCerebro = Esquemas["Encargo"];
export type IntencionCerebro = Esquemas["Intencion"];
export type CifraCerebro = Esquemas["Cifra"];
export type ResumenInformeCerebro = Esquemas["ResumenInforme"];
export type AtribucionCifraCerebro = Esquemas["AtribucionCifra"];
export type PuertaCerebro = Esquemas["ResultadoPuerta"];
export type InformeCerebro = Esquemas["InformeCerebro"];

// Live progress of a running agent turn (asistente/nucleo/agent/agent.py -> emit).
export type ProgressEvent =
  | { type: "thinking"; step: number }
  | { type: "text_delta"; text: string }
  // `agent` is set when a member of the team (not the advisor) made the call.
  | { type: "tool_call"; tool: string; arguments: Record<string, unknown>; agent?: string }
  | { type: "tool_result"; tool: string; summary: string; is_error: boolean; agent?: string }
  // Team mode (complex questions): planner, researchers and critic at work.
  | { type: "team"; stage: string; agent: string; status: string; complex?: boolean; objections?: boolean; modulo?: string }
  | { type: "verifying"; citations: number }
  | { type: "revising"; problems: number }
  // The household brain: its plan (again on a replan), each specialist's run, requests between them, the synthesis
  // and the figures gate (asistente/nucleo/cerebro/contrato.py -> EVENTO_*).
  | { type: "plan"; intencion: IntencionCerebro; replan?: boolean }
  | { type: "especialista"; clave: string; nombre: string; encargo_id: string; estado: string; resultado?: string }
  | { type: "peticion"; peticion?: { solicitante: string; destinatario: string; dato: string }; estado?: string }
  | { type: "sintesis"; especialistas: string[] }
  | { type: "puerta"; violaciones: number };

/** An API failure with the server's human-readable `detail`, when it sent one. */
export class ApiError extends Error {
  readonly status: number | null;
  constructor(message: string, status: number | null = null, options?: ErrorOptions) {
    super(message, options);
    this.name = "ApiError";
    this.status = status;
  }
}

export type ObligationCandidate = Esquemas["ObligationCandidate"];
export type CaseStatus = Esquemas["CaseStatus"];
export type Fact = Esquemas["Fact"];
export type MissingFact = Esquemas["MissingFact"];
export type CaseState = Respuesta<"/cases/{case_id}", "get">;

// The FastAPI default is http://127.0.0.1:8000; Vite proxies /api/* to
// avoid CORS in dev. In production both should be served under the same
// origin so this remains an empty prefix.
const API_BASE = (import.meta.env.VITE_API_BASE as string) || "/api";

// Abort any single request that outlives this budget, so a hung backend
// surfaces a clear timeout instead of leaving the UI stuck on "Consultando…".
const DEFAULT_TIMEOUT_MS = 30_000;

// With accounts on (AUTH_MODE=required) the session travels in an HttpOnly cookie that this script
// never sees (`credentials: "same-origin"`). `X-Requested-With` marks the request as ours: a write the
// cookie authenticates needs it (or an Origin of ours) to pass the server's CSRF check.
const CREDENTIALS: RequestCredentials = "same-origin";

function requestHeaders(hasBody: boolean): Record<string, string> {
  const headers: Record<string, string> = { "X-Requested-With": "cerebro" };
  if (hasBody) headers["Content-Type"] = "application/json";
  return headers;
}

let sessionExpiredHandler: (() => void) | null = null;

/** Called when the server rejects the session (expired, revoked, account deleted). */
export function onSessionExpired(handler: (() => void) | null): void {
  sessionExpiredHandler = handler;
}

// Runtime shape guards. A 200 with an unexpected/partial body must NOT be cast
// blindly to the typed interface — that would defer the failure to a confusing
// `undefined.map` deep in render. We validate the fields the UI actually reads
// (extra keys are ignored) and throw a clear error otherwise.
const AGENT_MODES: ReadonlySet<string> = new Set([
  "verified",
  "partially_verified",
  "unverified",
  "clarification",
  "abstained",
]);

function isAgentResponse(value: unknown): value is AgentResponse {
  return (
    isRecord(value) &&
    typeof value.answer === "string" &&
    typeof value.mode === "string" &&
    AGENT_MODES.has(value.mode) &&
    Array.isArray(value.citations) &&
    Array.isArray(value.claims) &&
    Array.isArray(value.warnings) &&
    Array.isArray(value.tool_trace) &&
    Array.isArray(value.obligations) &&
    Array.isArray(value.facts)
  );
}

function isCaseState(value: unknown): value is CaseState {
  return isRecord(value) && typeof value.case_id === "string";
}

export interface RequestOptions {
  signal?: AbortSignal;
  timeoutMs?: number;
  method?: "POST" | "PUT" | "PATCH" | "DELETE" | "GET";
}

/** FastAPI errors are `{"detail": "..."}`; surface that sentence, not raw JSON. */
async function errorFromResponse(path: string, resp: Response): Promise<ApiError> {
  const text = await resp.text().catch(() => "");
  let detail = text || resp.statusText;
  try {
    const parsed: unknown = JSON.parse(text);
    if (isRecord(parsed) && typeof parsed.detail === "string") detail = parsed.detail;
  } catch {
    // not JSON: keep the raw text
  }
  // The account endpoints explain their own 401/403/429 (wrong password, too many attempts).
  const account = path.startsWith("/auth/") || path.startsWith("/me");
  if (resp.status === 401) {
    if (resp.headers.get("WWW-Authenticate")?.toLowerCase().startsWith("bearer")) {
      detail = "Tu sesión ha caducado. Vuelve a iniciar sesión.";
      sessionExpiredHandler?.();
    } else if (!account) {
      detail = "El servicio requiere una clave de acceso que esta aplicación no tiene configurada.";
    }
  }
  if (resp.status === 429 && !account) detail = "Demasiadas consultas seguidas. Espera un momento y vuelve a intentarlo.";
  return new ApiError(detail || `${path}: error ${resp.status}`, resp.status);
}

/** Shared by the other API modules (apiAutonomos.ts): same base URL, session cookie, timeout and errors. */
export async function request<T>(
  path: string,
  body: unknown,
  validate: (value: unknown) => value is T,
  { signal, timeoutMs = DEFAULT_TIMEOUT_MS, method = "POST" }: RequestOptions = {},
): Promise<T> {
  // Either the caller (e.g. "Nueva consulta" / unmount) or the timeout can
  // abort the request; combine both signals into one.
  const timeout = AbortSignal.timeout(timeoutMs);
  const combined = signal ? AbortSignal.any([signal, timeout]) : timeout;

  let resp: Response;
  try {
    resp = await fetch(`${API_BASE}${path}`, {
      method,
      headers: requestHeaders(body !== undefined),
      body: body === undefined ? undefined : JSON.stringify(body),
      credentials: CREDENTIALS,
      signal: combined,
    });
  } catch (err) {
    // A timeout gets a clear message; a caller-cancel or genuine network
    // failure propagates the original error (already an AbortError on cancel).
    if (timeout.aborted) throw new ApiError("El servicio tardó demasiado en responder.", null, { cause: err });
    throw err;
  }

  if (!resp.ok) throw await errorFromResponse(path, resp);

  // 204 (logout, account deletion) has no body.
  const data: unknown = resp.status === 204 ? null : await resp.json();
  if (!validate(data)) {
    throw new ApiError("El servidor devolvió una respuesta con un formato inesperado.", resp.status);
  }
  return data;
}

export function createCase(opts: {
  userId: string;
  taxPeriod: string;
  jurisdiction?: string;
  signal?: AbortSignal;
}): Promise<CaseState> {
  return request(
    "/cases",
    { user_id: opts.userId, tax_period: opts.taxPeriod, jurisdiction: opts.jurisdiction ?? "ES" },
    isCaseState,
    { signal: opts.signal },
  );
}

// The shape the backend accepts for a chat's id (AgentRequest.conversacion_id); anything else is not sent.
const CONVERSACION_ID = /^[A-Za-z0-9_-]{8,64}$/;

// If the stream goes silent for this long the connection is dead: the server
// sends a keep-alive comment every ~10 s even while the model is thinking.
const STREAM_IDLE_TIMEOUT_MS = 60_000;

const PROGRESS_TYPES: ReadonlySet<string> = new Set([
  "thinking",
  "text_delta",
  "tool_call",
  "tool_result",
  "team",
  "verifying",
  "revising",
  // The household brain's events.
  "plan",
  "especialista",
  "peticion",
  "sintesis",
  "puerta",
]);

function isProgressEvent(value: unknown): value is ProgressEvent {
  return isRecord(value) && typeof value.type === "string" && PROGRESS_TYPES.has(value.type);
}

/**
 * Run one agent turn over its Server-Sent Events stream, reporting live progress.
 *
 * Events: `progress` (data: the agent's event), `cola` (the server waits for a free slot), then one
 * terminal `result` (data: AgentResponse) or `error` (data: {status, detail}); `: ping` comments keep
 * the connection alive. Aborting `signal` closes the stream, and the server then stops the turn.
 *
 * Resolves with the final AgentResponse; rejects with ApiError on a server
 * error (HTTP or in-stream), a dead connection, or a malformed stream.
 */
export async function streamAgent(
  opts: {
    query: string;
    chatHistory?: Array<{ role: "user" | "assistant"; content: string }>;
    caseId?: string | null;
    conversacionId?: string | null;
    signal?: AbortSignal;
  },
  onProgress: (event: ProgressEvent) => void,
): Promise<AgentResponse> {
  const body: Record<string, unknown> = { query: opts.query, chat_history: trimHistory(opts.chatHistory ?? []) };
  if (opts.caseId) body.case_id = opts.caseId;
  if (opts.conversacionId && CONVERSACION_ID.test(opts.conversacionId)) body.conversacion_id = opts.conversacionId;

  const idle = new AbortController();
  const combined = opts.signal ? AbortSignal.any([opts.signal, idle.signal]) : idle.signal;
  let idleTimer = setTimeout(() => idle.abort(), STREAM_IDLE_TIMEOUT_MS);
  const touch = () => {
    clearTimeout(idleTimer);
    idleTimer = setTimeout(() => idle.abort(), STREAM_IDLE_TIMEOUT_MS);
  };

  try {
    const resp = await fetch(`${API_BASE}/agent/stream`, {
      method: "POST",
      headers: { ...requestHeaders(true), Accept: "text/event-stream" },
      body: JSON.stringify(body),
      credentials: CREDENTIALS,
      signal: combined,
    });
    if (!resp.ok) throw await errorFromResponse("/agent/stream", resp);
    if (!resp.body) throw new ApiError("El servidor no devolvió ninguna respuesta.", resp.status);

    // Set from the event callback (a holder, so the compiler does not narrow it to null).
    const salida: { result: AgentResponse | null } = { result: null };
    await leerCuerpoSse(
      resp.body,
      ({ event, data }) => {
        if (event === "progress") {
          const parsed: unknown = JSON.parse(data);
          if (isProgressEvent(parsed)) onProgress(parsed);
          return "seguir";
        }
        if (event === "error") {
          const parsed: unknown = JSON.parse(data);
          const detail = isRecord(parsed) ? parsed : {};
          throw new ApiError(
            typeof detail.detail === "string" ? detail.detail : "El servicio no pudo procesar la consulta.",
            typeof detail.status === "number" ? detail.status : null,
          );
        }
        if (event === "result") {
          const parsed: unknown = JSON.parse(data);
          if (!isAgentResponse(parsed)) {
            throw new ApiError("El servidor devolvió una respuesta con un formato inesperado.");
          }
          salida.result = parsed;
          return "fin";
        }
        return "seguir"; // `cola` and anything newer: nothing to show yet
      },
      touch,
    );
    if (salida.result === null) throw new ApiError("La conexión se cerró antes de recibir la respuesta.");
    return salida.result;
  } catch (err) {
    if (idle.signal.aborted && !opts.signal?.aborted) {
      throw new ApiError("Se perdió la conexión con el servicio mientras preparaba la respuesta.", null, {
        cause: err,
      });
    }
    if (err instanceof SyntaxError) {
      throw new ApiError("El servidor devolvió una respuesta ilegible.", null, { cause: err });
    }
    throw err;
  } finally {
    clearTimeout(idleTimer);
  }
}

/**
 * Open a GET Server-Sent Events stream of the API (the council's session, a browser task), resuming
 * after `ultimoId` when there is one. Rejects with ApiError when the server refuses it.
 */
export async function abrirFlujo(path: string, ultimoId: string | null, signal: AbortSignal): Promise<ReadableStream<Uint8Array>> {
  const headers: Record<string, string> = { ...requestHeaders(false), Accept: "text/event-stream" };
  if (ultimoId) headers["Last-Event-ID"] = ultimoId;
  const resp = await fetch(`${API_BASE}${path}`, { method: "GET", headers, credentials: CREDENTIALS, cache: "no-store", signal });
  if (!resp.ok) throw await errorFromResponse(path, resp);
  if (!resp.body) throw new ApiError("El servidor no devolvió ninguna respuesta.", resp.status);
  return resp.body;
}

/** The case as stored now. */
export function getCase(opts: { caseId: string; signal?: AbortSignal }): Promise<CaseState> {
  return request(`/cases/${encodeURIComponent(opts.caseId)}`, undefined, isCaseState, { signal: opts.signal, method: "GET" });
}

/** Remove a fact the advisor recorded about the user; returns the updated case. */
export function deleteCaseFact(opts: { caseId: string; factName: string; signal?: AbortSignal }): Promise<CaseState> {
  return request(
    `/cases/${encodeURIComponent(opts.caseId)}/facts/${encodeURIComponent(opts.factName)}`,
    undefined,
    isCaseState,
    { signal: opts.signal, method: "DELETE" },
  );
}

/** Set a case fact to a value the user typed; returns the updated case. */
export function updateCaseFact(opts: {
  caseId: string;
  factName: string;
  value: string | number | boolean;
  signal?: AbortSignal;
}): Promise<CaseState> {
  return request(
    `/cases/${encodeURIComponent(opts.caseId)}/facts/${encodeURIComponent(opts.factName)}`,
    { value: opts.value },
    isCaseState,
    { signal: opts.signal, method: "PUT" },
  );
}

// ---- Step 4: simulator, calendar, documents --------------------------------

export type CalcLine = Esquemas["Linea"];
export type IRPFResult = Esquemas["IRPFResult"];
export type Benefit = Esquemas["Beneficio"];
export type BenefitState = Benefit["estado"];
export type Simulation = Respuesta<"/simulate", "post">;

function isSimulation(value: unknown): value is Simulation {
  return (
    isRecord(value) &&
    typeof value.ejercicio === "number" &&
    Array.isArray(value.escenarios) &&
    value.escenarios.length > 0 &&
    Array.isArray(value.beneficios)
  );
}

/** Deterministic IRPF simulation (no LLM): instant, safe to call on every keystroke. */
export function simulate(opts: {
  caseId?: string | null;
  ejercicio?: number | null;
  datos?: Record<string, unknown>;
  escenarios?: Array<{ nombre: string; cambios: Record<string, unknown> }>;
  // Judge the "ways to pay less" against the last scenario, not the saved profile.
  leversForLastScenario?: boolean;
  signal?: AbortSignal;
}): Promise<Simulation> {
  const body: Record<string, unknown> = { datos: opts.datos ?? {} };
  if (opts.caseId) body.case_id = opts.caseId;
  if (opts.ejercicio) body.ejercicio = opts.ejercicio;
  if (opts.escenarios) body.escenarios = opts.escenarios;
  if (opts.leversForLastScenario) body.beneficios_sobre_ultimo = true;
  return request("/simulate", body, isSimulation, { signal: opts.signal });
}

export type Deadline = Esquemas["Vencimiento"];
export type CalendarResponse = Respuesta<"/cases/{case_id}/calendar", "get">;

function isCalendar(value: unknown): value is CalendarResponse {
  return isRecord(value) && Array.isArray(value.vencimientos) && typeof value.aviso === "string";
}

export function getCalendar(opts: { caseId: string; dias?: number; signal?: AbortSignal }): Promise<CalendarResponse> {
  return request(
    `/cases/${encodeURIComponent(opts.caseId)}/calendar?dias=${opts.dias ?? 365}`,
    undefined,
    isCalendar,
    { signal: opts.signal, method: "GET" },
  );
}

// `origen: "imagen"`: read from a scan or photo (two readings agreed); it cannot be checked against text.
export type ProposedFact = Esquemas["HechoPropuesto"];
export type DocumentAnalysis = Respuesta<"/cases/{case_id}/documents", "post">;

function isDocumentAnalysis(value: unknown): value is DocumentAnalysis {
  return isRecord(value) && Array.isArray(value.hechos_propuestos) && Array.isArray(value.avisos);
}

// Reading a document is an LLM call over its full text.
const DOCUMENT_TIMEOUT_MS = 120_000;

/** Send a document for analysis. Returns PROPOSED facts; nothing is saved server-side. */
export function analyzeDocument(opts: {
  caseId: string;
  filename: string;
  contentBase64: string;
  signal?: AbortSignal;
}): Promise<DocumentAnalysis> {
  return request(
    `/cases/${encodeURIComponent(opts.caseId)}/documents`,
    { filename: opts.filename, content_base64: opts.contentBase64 },
    isDocumentAnalysis,
    { signal: opts.signal, timeoutMs: DOCUMENT_TIMEOUT_MS },
  );
}

// ---- Proactive notifications -------------------------------------------------

// `kind`: the tax kinds (plazo, fin_de_ejercicio, cambio_normativo), the weekly digest (resumen_semanal) and one
// kind per household module ("modulo:<clave>").
export type AdvisorNotification = Esquemas["Notification"];
export type NotificationKind = AdvisorNotification["kind"];
export type NotificationSeverity = AdvisorNotification["severity"];
export type NotificationsResponse = Respuesta<"/cases/{case_id}/notifications", "get">;

function isNotifications(value: unknown): value is NotificationsResponse {
  return isRecord(value) && typeof value.unread === "number" && Array.isArray(value.notifications);
}

export function getNotifications(opts: { caseId: string; signal?: AbortSignal }): Promise<NotificationsResponse> {
  return request(`/cases/${encodeURIComponent(opts.caseId)}/notifications`, undefined, isNotifications, {
    signal: opts.signal,
    method: "GET",
  });
}

export function markNotificationRead(opts: { caseId: string; notificationId: string }): Promise<NotificationsResponse> {
  return request(
    `/cases/${encodeURIComponent(opts.caseId)}/notifications/${encodeURIComponent(opts.notificationId)}/read`,
    {},
    isNotifications,
  );
}

/** Run the deterministic proactive jobs (deadlines, year-end plan) for this case now. */
export function runProactive(opts: { caseId: string; signal?: AbortSignal }): Promise<NotificationsResponse> {
  return request(`/cases/${encodeURIComponent(opts.caseId)}/proactive/run`, {}, isNotifications, { signal: opts.signal });
}

// ---- The brain's proactive pass (asistente/plataforma/api/routers/repaso.py) --------
// The structured weekly/daily digest behind a `resumen_semanal` notification. Its proposals are answered through
// their own endpoints (acciones, tareas, correo); this endpoint never acts.

export type Repaso = Respuesta<"/cases/{case_id}/repaso", "get">["repaso"];
export type PuntoRepaso = Repaso["puntos"][number];
export type FuenteRepaso = PuntoRepaso["fuentes"][number];
export type PropuestaRepaso = PuntoRepaso["propuestas"][number];
// accion | tarea | paso | correo | plantilla
export type TipoPropuestaRepaso = PropuestaRepaso["tipo"];
// Read now from the layer that owns the proposal: decided elsewhere since → no buttons.
export type EstadoPropuestaRepaso = PropuestaRepaso["estado"];

function isRepaso(value: unknown): value is Respuesta<"/cases/{case_id}/repaso", "get"> {
  return isRecord(value) && isRecord(value.repaso) && Array.isArray((value.repaso).puntos);
}

/** The structured digest of a `resumen_semanal` notification (404 when the brain did not write it). */
export function getRepaso(opts: { caseId: string; notificationId?: string; signal?: AbortSignal }): Promise<Repaso> {
  const q = opts.notificationId ? `?notification_id=${encodeURIComponent(opts.notificationId)}` : "";
  return request(`/cases/${encodeURIComponent(opts.caseId)}/repaso${q}`, undefined, isRepaso, {
    signal: opts.signal,
    method: "GET",
  }).then((resp) => resp.repaso);
}

// ---- Accounts and privacy (asistente/plataforma/api/routers/auth.py) -----------------

// `privacy_notice_url` is empty when the operator has not published a privacy notice. The schema says
// `auth_mode: string`; isAuthConfig only lets the two modes this client knows through.
export type AuthConfig = Omit<Respuesta<"/auth/config", "get">, "auth_mode"> & { auth_mode: AuthMode };
export type SessionUser = Respuesta<"/auth/me", "get">;
// `token` is only for a client without a browser; the web client gets the HttpOnly cookie and null here.
export type SessionResponse = Respuesta<"/auth/login", "post">;
export type ConsentState = Esquemas["ConsentState"];
export type ConsentType = ConsentState["type"];
export type ConsentsResponse = Respuesta<"/me/consents", "get">;

function isAuthConfig(value: unknown): value is AuthConfig {
  return isRecord(value) && (value.auth_mode === "off" || value.auth_mode === "required");
}

function isSessionUser(value: unknown): value is SessionUser {
  return isRecord(value) && typeof value.id === "string" && typeof value.email === "string";
}

function isSessionResponse(value: unknown): value is SessionResponse {
  return isRecord(value) && (value.token === null || value.token === undefined || typeof value.token === "string") && isSessionUser(value.user);
}

function isConsents(value: unknown): value is ConsentsResponse {
  return isRecord(value) && Array.isArray(value.consents);
}

function isEmpty(value: unknown): value is null {
  return value === null;
}

// Asked before anything is shown, so it must fail fast when the backend is down.
const CONFIG_TIMEOUT_MS = 4_000;

/** Whether this backend has user accounts. A backend older than accounts answers 404: that is "off". */
export async function getAuthConfig(signal?: AbortSignal): Promise<AuthConfig> {
  try {
    return await request("/auth/config", undefined, isAuthConfig, { signal, method: "GET", timeoutMs: CONFIG_TIMEOUT_MS });
  } catch (err) {
    if (err instanceof ApiError && err.status === 404) {
      return { auth_mode: "off", password_min_length: 10, consent_text_version: "", privacy_notice_url: "" };
    }
    throw err;
  }
}

export function register(opts: {
  email: string;
  password: string;
  consents: Record<ConsentType, boolean>;
}): Promise<SessionResponse> {
  return request("/auth/register", { email: opts.email, password: opts.password, consents: opts.consents }, isSessionResponse);
}

export function login(opts: { email: string; password: string }): Promise<SessionResponse> {
  return request("/auth/login", { email: opts.email, password: opts.password }, isSessionResponse);
}

export function logout(): Promise<null> {
  return request("/auth/logout", {}, isEmpty);
}

/**
 * Revoke a session token kept by an older version of this app in localStorage (sent once, as Bearer,
 * and then forgotten). Best effort: a token the server no longer knows is simply gone already.
 */
export async function revocarTokenAntiguo(token: string): Promise<void> {
  try {
    await fetch(`${API_BASE}/auth/logout`, {
      method: "POST",
      headers: { ...requestHeaders(true), Authorization: `Bearer ${token}` },
      body: "{}",
      credentials: "omit",
      signal: AbortSignal.timeout(DEFAULT_TIMEOUT_MS),
    });
  } catch {
    // offline or refused: nothing else to do
  }
}

export function getMe(signal?: AbortSignal): Promise<SessionUser> {
  return request("/auth/me", undefined, isSessionUser, { signal, method: "GET" });
}

export function getConsents(signal?: AbortSignal): Promise<ConsentsResponse> {
  return request("/me/consents", undefined, isConsents, { signal, method: "GET" });
}

export function updateConsents(changes: Partial<Record<ConsentType, boolean>>): Promise<ConsentsResponse> {
  return request("/me/consents", changes, isConsents, { method: "PUT" });
}

/** Everything the server stores about the signed-in user, as one JSON document. */
export function exportMyData(): Promise<Record<string, unknown>> {
  return request("/me/export", undefined, isRecord, { method: "GET", timeoutMs: 60_000 });
}

/** Erase the account and all its data. Irreversible; the password is asked again. */
export function deleteMyAccount(password: string): Promise<null> {
  return request("/me", { password }, isEmpty, { method: "DELETE" });
}
