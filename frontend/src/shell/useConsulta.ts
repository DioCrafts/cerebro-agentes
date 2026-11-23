// The conversations kept in this browser, the one on screen, and asking the advisor (streamed turn by turn).
// The running turn is kept in an external store (turnoEnCurso.ts) so streamed tokens repaint only its bubble.

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import type { CaseState, ProgressEvent } from "../api";
import { createCase, deleteCaseFact, streamAgent, updateCaseFact } from "../api";
import type { TurnoConCerebro } from "../components/ProcedenciaRespuesta";
import { conversacionDeVuelta } from "../lib/correo";
import { newId } from "../lib/id";
import { activityRows, sourceAnchorId } from "../lib/presentation";
import { type ChatTurn, type Conversation, emptyConversation, loadConversations, saveConversations } from "../lib/storage";
import { errorMessage } from "./errores";
import { crearAlmacenTurno } from "./turnoEnCurso";


/** Why the running turn was cancelled: the user pressed stop, or moved to another conversation. */
type MotivoParada = "usuario" | "cambio";

// The household brain's own events: shown as its live plan, kept apart from the advisor's activity timeline.
const EVENTOS_CEREBRO: ReadonlySet<string> = new Set(["plan", "especialista", "peticion", "sintesis", "puerta"]);

function esEventoCerebro(event: ProgressEvent): boolean {
  return EVENTOS_CEREBRO.has(event.type);
}

export function useConsulta({
  alCambiarDeConversacion,
  alPrepararAcciones,
}: {
  /** The user moved to another conversation (or a new one). */
  alCambiarDeConversacion: () => void;
  /** A specialist prepared something that acts outside the app: it waits in «Módulos». */
  alPrepararAcciones: () => void;
}) {
  const [conversations, setConversations] = useState<Conversation[]>(loadConversations);
  // Back from Google's consent screen (mail module): reopen the conversation whose case was connecting.
  const [activeId, setActiveId] = useState<string>(() => conversacionDeVuelta(conversations) ?? newId());
  // The running turn lives outside React state (see turnoEnCurso.ts); only `busy` re-renders the shell.
  const [turno] = useState(crearAlmacenTurno);
  const setPending = turno.set;
  const [busy, setBusy] = useState(false);
  const [highlightedId, setHighlightedId] = useState<string | null>(null);
  const [removingFact, setRemovingFact] = useState<string | null>(null);
  const [caseError, setCaseError] = useState<string | null>(null);
  // Remounting the composer is how a stopped question is put back in the box.
  const [composerSeed, setComposerSeed] = useState({ id: 0, text: "" });

  // Synchronous reentrancy lock: `pending` (state) commits asynchronously, so
  // two submits fired before the next render would both see it as null.
  const busyRef = useRef(false);
  const abortRef = useRef<AbortController | null>(null);
  const highlightTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const bottomRef = useRef<HTMLDivElement | null>(null);
  const lastTurnRef = useRef<HTMLElement | null>(null);

  const active = useMemo(
    () => conversations.find((c) => c.id === activeId) ?? emptyConversation(activeId),
    [conversations, activeId],
  );

  // Stable per-browser identity. There is no auth yet, so a generated id
  // persisted in localStorage lets the backend group this user's cases.
  const [userId] = useState(() => {
    const KEY = "haciendagpt_user_id";
    try {
      const existing = localStorage.getItem(KEY);
      if (existing) return existing;
      const fresh = `web-${newId()}`;
      localStorage.setItem(KEY, fresh);
      return fresh;
    } catch {
      return `web-${newId()}`;
    }
  });

  useEffect(() => saveConversations(conversations), [conversations]);

  // When the answer lands, bring its START into view: a long answer must be read from the top, not
  // from its last source card. (While it runs, Hilo's live bubble follows it at the bottom.)
  useEffect(() => {
    lastTurnRef.current?.scrollIntoView({ behavior: "smooth", block: "start" });
  }, [active.turns.length, activeId]);

  // Cancel the in-flight request and pending timers if the app unmounts.
  useEffect(
    () => () => {
      abortRef.current?.abort("cambio");
      if (highlightTimer.current) clearTimeout(highlightTimer.current);
    },
    [],
  );

  /** Insert or update a conversation by id. */
  const upsert = useCallback((id: string, change: (current: Conversation) => Conversation) => {
    setConversations((prev) => {
      const current = prev.find((c) => c.id === id) ?? emptyConversation(id);
      const next = change(current);
      return [next, ...prev.filter((c) => c.id !== id)];
    });
  }, []);

  const handleCite = useCallback((turnId: string, fragmentId: string) => {
    const anchor = sourceAnchorId(turnId, fragmentId);
    document.getElementById(anchor)?.scrollIntoView({ behavior: "smooth", block: "center" });
    setHighlightedId(anchor);
    if (highlightTimer.current) clearTimeout(highlightTimer.current);
    highlightTimer.current = setTimeout(() => setHighlightedId(null), 2000);
  }, []);

  async function submit(text: string, replaceTurnId?: string) {
    if (busyRef.current) return;
    busyRef.current = true;
    const conversation = active;
    const controller = new AbortController();
    abortRef.current = controller;
    const startedAt = Date.now();
    const events: ProgressEvent[] = [];
    const eventosCerebro: ProgressEvent[] = [];
    setPending({ text, events: [], startedAt, draft: "", cerebro: [] });
    setBusy(true);
    if (replaceTurnId) {
      upsert(conversation.id, (c) => ({ ...c, turns: c.turns.filter((t) => t.id !== replaceTurnId) }));
    }

    const priorTurns = conversation.turns.filter((t) => t.id !== replaceTurnId && !t.errored);
    const chatHistory = priorTurns.flatMap((t) => [
      { role: "user" as const, content: t.userText },
      { role: "assistant" as const, content: t.envelope.answer },
    ]);

    try {
      // The case lets the advisor remember facts across turns. It is
      // best-effort: without it the advisor still answers (search-only).
      let caseId = conversation.caseId;
      if (!caseId) {
        // Provisional fiscal period; the advisor refines it from the conversation.
        const taxPeriod = String(new Date().getFullYear());
        caseId = await createCase({ userId, taxPeriod, signal: controller.signal }).then(
          (created) => created.case_id,
          (err) => {
            console.error("case creation failed; continuing without a case", err);
            return null;
          },
        );
        if (caseId) {
          const createdId = caseId;
          upsert(conversation.id, (c) => ({ ...c, caseId: createdId }));
        }
      }

      let turn: ChatTurn;
      try {
        // The chat's id is the conversation's own (a new chat, a new id): the brain's conversation memory.
        const peticion = { query: text, chatHistory, caseId, conversacionId: conversation.id, signal: controller.signal };
        const resp = await streamAgent(peticion, (event) => {
          if (esEventoCerebro(event)) {
            eventosCerebro.push(event);
            setPending((p) => (p ? { ...p, cerebro: [...eventosCerebro] } : p));
            return;
          }
          if (event.type === "text_delta") {
            // Tokens only grow the draft; they are not timeline entries.
            setPending((p) => (p ? { ...p, draft: p.draft + event.text } : p));
            return;
          }
          events.push(event);
          // A new model step, or a tool call, means whatever text came before
          // was not the answer (a preamble, or a draft now being rewritten).
          const resetsDraft = event.type === "thinking" || event.type === "tool_call";
          setPending((p) => (p ? { ...p, events: [...events], draft: resetsDraft ? "" : p.draft } : p));
        });
        turn = {
          id: newId(),
          userText: text,
          envelope: {
            answer: resp.answer,
            mode: resp.mode,
            citations: resp.citations,
            warnings: resp.warnings,
            conflicts: resp.conflicts ?? [],
            sinCriterioFirme: resp.sin_criterio_firme ?? false,
          },
          claims: resp.claims,
          obligations: resp.obligations,
          activity: activityRows(events),
          elapsedMs: Date.now() - startedAt,
          // What the brain did (who it consulted, where each figure comes from); kept with the turn.
          cerebro: resp.cerebro ?? null,
        } as TurnoConCerebro;
        upsert(conversation.id, (c) => ({
          ...c,
          facts: resp.facts,
          obligations: resp.obligations,
          taxPeriod: resp.tax_period ?? c.taxPeriod,
        }));
        if (resp.pending_actions && resp.pending_actions.length > 0) alPrepararAcciones();
      } catch (err) {
        if (controller.signal.aborted) {
          // No turn is recorded. Stopped by the user: the question goes back into the composer so it
          // is not lost. Cancelled by a move to another conversation: it stays out of the new one.
          if ((controller.signal.reason as MotivoParada | undefined) === "usuario") setComposerSeed((seed) => ({ id: seed.id + 1, text }));
          return;
        }
        console.error("agent turn failed", err);
        // Never fabricate content on failure: no answer, no sources.
        turn = {
          id: newId(),
          userText: text,
          envelope: { answer: errorMessage(err), mode: "abstained", citations: [] },
          claims: [],
          obligations: [],
          activity: activityRows(events),
          elapsedMs: Date.now() - startedAt,
          errored: true,
        };
      }

      upsert(conversation.id, (c) => ({
        ...c,
        title: c.turns.length === 0 ? (text.length > 60 ? `${text.slice(0, 57)}…` : text) : c.title,
        turns: [...c.turns, turn],
        updatedAt: Date.now(),
      }));
    } finally {
      if (abortRef.current === controller) abortRef.current = null;
      busyRef.current = false;
      setPending(null);
      setBusy(false);
    }
  }

  function stop(motivo: MotivoParada = "usuario") {
    abortRef.current?.abort(motivo);
  }

  function switchTo(id: string) {
    stop("cambio");
    setActiveId(id);
    alCambiarDeConversacion();
    setCaseError(null);
    setComposerSeed((seed) => ({ id: seed.id + 1, text: "" }));
  }

  function remove(id: string) {
    const target = conversations.find((c) => c.id === id);
    if (!target || !window.confirm(`¿Eliminar la consulta «${target.title}»? No se puede deshacer.`)) return;
    if (id === activeId) switchTo(newId());
    setConversations((prev) => prev.filter((c) => c.id !== id));
  }

  /** Apply a user edit to the case file: a new value, or `null` to delete the fact. */
  async function changeFact(factName: string, value: string | number | boolean | null): Promise<boolean> {
    if (!active.caseId || removingFact) return false;
    setRemovingFact(factName);
    setCaseError(null);
    try {
      const updated =
        value === null
          ? await deleteCaseFact({ caseId: active.caseId, factName })
          : await updateCaseFact({ caseId: active.caseId, factName, value });
      upsert(active.id, (c) => ({
        ...c,
        facts: updated.facts,
        obligations: updated.obligation_candidates,
        taxPeriod: updated.tax_period,
      }));
      return true;
    } catch (err) {
      setCaseError(`No se pudo guardar el cambio. ${errorMessage(err)}`);
      return false;
    } finally {
      setRemovingFact(null);
    }
  }

  function applyCase(updated: CaseState) {
    upsert(active.id, (c) => ({ ...c, facts: updated.facts, obligations: updated.obligation_candidates, taxPeriod: updated.tax_period }));
  }

  return {
    conversations,
    active,
    activeId,
    turno,
    busy,
    highlightedId,
    removingFact,
    caseError,
    composerSeed,
    bottomRef,
    lastTurnRef,
    submit,
    stop,
    switchTo,
    remove,
    changeFact,
    applyCase,
    handleCite,
  };
}
