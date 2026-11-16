// The conversations kept in this browser, the one on screen, and asking the advisor (streamed turn by turn).
// Moved out of App.tsx unchanged: same requests, same history format, same error handling.

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import type { CaseState, ProgressEvent } from "../api";
import { createCase, deleteCaseFact, streamAgent, updateCaseFact } from "../api";
import { conversacionDeVuelta } from "../lib/correo";
import { newId } from "../lib/id";
import { activityRows, sourceAnchorId } from "../lib/presentation";
import { type ChatTurn, type Conversation, emptyConversation, loadConversations, saveConversations } from "../lib/storage";
import { errorMessage } from "./errores";

// A turn that is still running: the question plus the agent's live progress.
export interface PendingTurn {
  text: string;
  events: ProgressEvent[];
  startedAt: number;
  // The answer text streamed so far for the current model step (unverified).
  draft: string;
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
  const [pending, setPending] = useState<PendingTurn | null>(null);
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
  const busy = pending !== null;

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

  // While the advisor works, follow its progress at the bottom. When the answer
  // lands, bring its START into view instead: a long answer must be read from
  // the top, not from its last source card.
  // (The draft grows token by token; re-scroll only every ~120 characters.)
  const draftBucket = Math.floor((pending?.draft.length ?? 0) / 120);
  useEffect(() => {
    if (busy) bottomRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [busy, pending?.events.length, draftBucket]);
  useEffect(() => {
    lastTurnRef.current?.scrollIntoView({ behavior: "smooth", block: "start" });
  }, [active.turns.length, activeId]);

  // Cancel the in-flight request and pending timers if the app unmounts.
  useEffect(
    () => () => {
      abortRef.current?.abort();
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
    setPending({ text, events: [], startedAt, draft: "" });
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
        const resp = await streamAgent({ query: text, chatHistory, caseId, signal: controller.signal }, (event) => {
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
        };
        upsert(conversation.id, (c) => ({
          ...c,
          facts: resp.facts,
          obligations: resp.obligations,
          taxPeriod: resp.tax_period ?? c.taxPeriod,
        }));
        if (resp.pending_actions && resp.pending_actions.length > 0) alPrepararAcciones();
      } catch (err) {
        if (controller.signal.aborted) {
          // Stopped by the user: no turn is recorded and the question goes back
          // into the composer so it is not lost.
          setComposerSeed((seed) => ({ id: seed.id + 1, text }));
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
    }
  }

  function stop() {
    abortRef.current?.abort();
  }

  function switchTo(id: string) {
    stop();
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
    pending,
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
