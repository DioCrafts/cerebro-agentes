// Coming back from Google's consent screen (mail module): the whole page navigated away, so the app
// remembers, for this tab only, which case was connecting Gmail, and reopens its conversation.

const CLAVE = "haciendagpt_correo_conectando";

export function recordarCasoConectando(caseId: string): void {
  try {
    sessionStorage.setItem(CLAVE, caseId);
  } catch {
    // Without storage the user simply picks the conversation again.
  }
}

/** The id of the conversation to reopen after `?correo=…`, or null. Consumes the reminder. */
export function conversacionDeVuelta(conversaciones: Array<{ id: string; caseId: string | null }>): string | null {
  try {
    if (!new URLSearchParams(window.location.search).has("correo")) return null;
    const caseId = sessionStorage.getItem(CLAVE);
    sessionStorage.removeItem(CLAVE);
    return conversaciones.find((c) => caseId !== null && c.caseId === caseId)?.id ?? null;
  } catch {
    return null;
  }
}
