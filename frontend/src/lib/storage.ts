// Conversation persistence in localStorage: the "Recientes" list is the
// user's real history on this browser, and reloading never loses a chat.
// Nothing is synced to the server beyond the case each conversation is attached to (its facts and
// obligations live in the case, kept with the account).
//
// With accounts on, this history holds tax facts of the signed-in user, so it lives only as long as
// their session on this browser: it is erased when they sign out, when the session expires or is
// revoked, when the account is deleted, and when an old-style session is migrated (App.tsx).

import type { AnswerEnvelope, ClaimCheck, Fact, ObligationCandidate } from "../api";
import type { ActivityRow } from "./presentation";

export interface ChatTurn {
  id: string;
  userText: string;
  envelope: AnswerEnvelope;
  claims: ClaimCheck[];
  obligations: ObligationCandidate[];
  // What the advisor did to get here, for the collapsible "how I got this".
  activity: ActivityRow[];
  elapsedMs: number;
  errored?: boolean;
}

export interface Conversation {
  id: string;
  title: string;
  caseId: string | null;
  turns: ChatTurn[];
  // The case as of the last turn (facts the advisor holds about the user).
  facts: Fact[];
  obligations: ObligationCandidate[];
  taxPeriod: string | null;
  updatedAt: number;
}

const KEY = "haciendagpt_conversations_v1";
const MAX_CONVERSATIONS = 40;
// The conversation on screen, so a reload (or coming back to the PWA) reopens it instead of a blank one.
const ACTIVE_KEY = "haciendagpt_active_conversation_v1";

function isConversation(value: unknown): value is Conversation {
  if (typeof value !== "object" || value === null) return false;
  const v = value as Record<string, unknown>;
  return typeof v.id === "string" && typeof v.title === "string" && Array.isArray(v.turns) && Array.isArray(v.facts);
}

export function loadConversations(): Conversation[] {
  try {
    const raw = localStorage.getItem(KEY);
    if (!raw) return [];
    const parsed: unknown = JSON.parse(raw);
    if (!Array.isArray(parsed)) return [];
    return parsed
      .filter(isConversation)
      .map((c) => ({ ...c, obligations: Array.isArray(c.obligations) ? c.obligations : [] }))
      .sort((a, b) => b.updatedAt - a.updatedAt);
  } catch {
    return [];
  }
}

export function saveConversations(conversations: Conversation[]): void {
  // Empty drafts are not history; keep only the most recent ones that have content.
  const kept = conversations
    .filter((c) => c.turns.length > 0)
    .sort((a, b) => b.updatedAt - a.updatedAt)
    .slice(0, MAX_CONVERSATIONS);
  try {
    localStorage.setItem(KEY, JSON.stringify(kept));
  } catch {
    // Storage full or disabled: the session keeps working, it just won't persist.
  }
}

/** Erase the whole history from this browser (not just empty it: the key goes too). */
export function borrarConversaciones(): void {
  try {
    localStorage.removeItem(KEY);
    localStorage.removeItem(ACTIVE_KEY);
  } catch {
    // Storage disabled: there was nothing persisted to erase.
  }
}

/** Remember which conversation is on screen. */
export function guardarConversacionActiva(id: string): void {
  try {
    localStorage.setItem(ACTIVE_KEY, id);
  } catch {
    // Storage full or disabled: a reload just opens a new conversation.
  }
}

/**
 * The conversation that was on screen, if it is still in `conversations` (the history this browser
 * keeps for its current owner). A remembered id whose conversation was discarded — another account's,
 * erased on sign-out, or an empty draft never saved — is not reopened.
 */
export function conversacionActivaGuardada(conversations: readonly Conversation[]): string | null {
  try {
    const id = localStorage.getItem(ACTIVE_KEY);
    return id !== null && conversations.some((c) => c.id === id) ? id : null;
  } catch {
    return null;
  }
}

export function emptyConversation(id: string): Conversation {
  return {
    id,
    title: "Nueva consulta",
    caseId: null,
    turns: [],
    facts: [],
    obligations: [],
    taxPeriod: null,
    updatedAt: Date.now(),
  };
}
