// What the first screen will need beyond the initial chunks, requested as soon as the app starts (main.tsx),
// in parallel with /auth/config and /auth/me instead of after them: the conversation's turns (Hilo) when
// the conversation reopened on load has any, since they are then the largest thing on screen.
//
// A deep-linked panel (`/?panel=<id>`) is not requested here: its header (title and description, from the
// registry) paints with the shell and is what the user sees first; its chunk comes right after, and asking
// for it earlier only competed with the shell's own requests (Lighthouse's LCP on a phone went from 3.2 to
// 3.7 s on «Optimizar»). The tabs prefetch their panel on hover and focus (registro.ts).
//
// A plain dynamic import: the module system keeps the promise and React.lazy finds it resolved.

import { conversacionActivaGuardada, loadConversations } from "../lib/storage";

export const cargarHilo = () => import("../components/Hilo");

export function precargarAlArrancar(): void {
  try {
    const conversaciones = loadConversations();
    const activa = conversacionActivaGuardada(conversaciones);
    if (conversaciones.find((c) => c.id === activa)?.turns.length) void cargarHilo().catch(() => undefined);
  } catch {
    // Only a head start: the shell loads it on its own.
  }
}
