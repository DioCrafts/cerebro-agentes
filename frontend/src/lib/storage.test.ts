import { afterEach, describe, expect, it } from "vitest";

import {
  borrarConversaciones,
  type Conversation,
  conversacionActivaGuardada,
  emptyConversation,
  guardarConversacionActiva,
  loadConversations,
  saveConversations,
} from "./storage";

function conTurno(id: string): Conversation {
  return { ...emptyConversation(id), turns: [{ id: `t-${id}` } as never] };
}

afterEach(() => localStorage.clear());

describe("the conversation on screen", () => {
  it("is reopened after a reload when it is still in the history", () => {
    saveConversations([conTurno("a"), conTurno("b")]);
    guardarConversacionActiva("b");
    expect(conversacionActivaGuardada(loadConversations())).toBe("b");
  });

  it("is not reopened when nothing was remembered", () => {
    saveConversations([conTurno("a")]);
    expect(conversacionActivaGuardada(loadConversations())).toBeNull();
  });

  it("is not reopened when it was an empty draft that was never saved", () => {
    saveConversations([conTurno("a"), emptyConversation("borrador")]);
    guardarConversacionActiva("borrador");
    expect(conversacionActivaGuardada(loadConversations())).toBeNull();
  });

  it("is not reopened once another account's history was discarded", () => {
    saveConversations([conTurno("ajena")]);
    guardarConversacionActiva("ajena");
    saveConversations([]); // what App.tsx does when the history belonged to someone else
    expect(conversacionActivaGuardada(loadConversations())).toBeNull();
  });

  it("is forgotten with the history on sign-out", () => {
    saveConversations([conTurno("a")]);
    guardarConversacionActiva("a");
    borrarConversaciones();
    expect(localStorage.getItem("haciendagpt_active_conversation_v1")).toBeNull();
    saveConversations([conTurno("a")]);
    expect(conversacionActivaGuardada(loadConversations())).toBeNull();
  });
});
