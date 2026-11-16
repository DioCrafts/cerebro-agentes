// Which panel is open lives in the URL (`/?panel=optimizador`), so a panel can be linked to (a push
// notice opens `/?panel=avisos`, the installed app's shortcuts open the simulator…) and the browser's
// Back button — or Android's — closes it.
//
// Opening a panel from the app pushes a history entry; closing it goes back to the entry it came from.
// A panel reached by a link (no entry of ours behind it) is closed by replacing the URL instead, so Back
// never leaves the app. Other query parameters (`?correo=…` after Google's consent screen) are kept.

import { useCallback, useSyncExternalStore } from "react";

const PARAM = "panel";
// Marks the history entries this module pushed.
const MARCA = "haciendagptPanel";

const oyentes = new Set<() => void>();

function avisar() {
  for (const oyente of oyentes) oyente();
}

function suscribir(oyente: () => void): () => void {
  oyentes.add(oyente);
  window.addEventListener("popstate", oyente);
  return () => {
    oyentes.delete(oyente);
    window.removeEventListener("popstate", oyente);
  };
}

/** The panel id in the current URL, or null. */
export function panelEnUrl(): string | null {
  try {
    const valor = new URLSearchParams(window.location.search).get(PARAM);
    return valor && /^[a-z][a-z0-9-]{0,40}$/.test(valor) ? valor : null;
  } catch {
    return null;
  }
}

function urlCon(panel: string | null): string {
  const url = new URL(window.location.href);
  if (panel) url.searchParams.set(PARAM, panel);
  else url.searchParams.delete(PARAM);
  return `${url.pathname}${url.search}${url.hash}`;
}

function nuestro(): boolean {
  const estado = window.history.state as Record<string, unknown> | null;
  return Boolean(estado && estado[MARCA]);
}

/**
 * Open `id` (switching from another panel replaces the entry instead of stacking one per panel).
 * `reemplazar`: open it in place, without a history entry (the app opening a panel by itself on load).
 */
export function abrirPanel(id: string, { reemplazar = false }: { reemplazar?: boolean } = {}): void {
  if (panelEnUrl() === id) return;
  if (reemplazar || panelEnUrl() !== null) {
    // Same entry: it keeps its mark, so a panel reached by a link still closes without leaving the app.
    window.history.replaceState(window.history.state, "", urlCon(id));
  } else {
    window.history.pushState({ ...((window.history.state as object | null) ?? {}), [MARCA]: true }, "", urlCon(id));
  }
  avisar();
}

/** Close whatever panel is open. */
export function cerrarPanel(): void {
  if (panelEnUrl() === null) return;
  if (nuestro()) {
    // popstate tells the subscribers.
    window.history.back();
    return;
  }
  const estado = { ...((window.history.state as object | null) ?? {}) };
  delete (estado as Record<string, unknown>)[MARCA];
  window.history.replaceState(estado, "", urlCon(null));
  avisar();
}

/** Remove a query parameter that was only meant to be read once (e.g. `correo`), without a new entry. */
export function quitarParametro(nombre: string): void {
  try {
    const url = new URL(window.location.href);
    if (!url.searchParams.has(nombre)) return;
    url.searchParams.delete(nombre);
    window.history.replaceState(window.history.state, "", `${url.pathname}${url.search}${url.hash}`);
  } catch {
    // Nothing to clean.
  }
}

/** The open panel and the functions to change it. */
export function usePanelUrl(): [string | null, (id: string) => void, () => void] {
  const panel = useSyncExternalStore(suscribir, panelEnUrl, () => null);
  const abrir = useCallback((id: string) => abrirPanel(id), []);
  const cerrar = useCallback(() => cerrarPanel(), []);
  return [panel, abrir, cerrar];
}
