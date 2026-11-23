// Light/dark theme: follows the OS until the user picks one, then remembers it.
//
// One store for the whole page (every `useTheme()` sees the same value). It writes two things on <html>:
// the `dark` class (the app's colour tokens in index.css) and Primer's `data-color-mode` attributes
// (src/ui/tema.css maps Primer's colour roles onto those tokens). public/tema.js does the same before the
// first paint.

import { useCallback, useSyncExternalStore } from "react";

export type Theme = "light" | "dark";
const KEY = "haciendagpt_theme";

function initialTheme(): Theme {
  try {
    const stored = localStorage.getItem(KEY);
    if (stored === "light" || stored === "dark") return stored;
  } catch {
    // ignore: fall through to the OS preference
  }
  return window.matchMedia?.("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

/** Put `theme` on the document: the app's tokens, Primer's colour mode and the installed app's status bar. */
export function aplicarTema(theme: Theme): void {
  const html = document.documentElement;
  html.classList.toggle("dark", theme === "dark");
  html.dataset.colorMode = theme;
  html.dataset.lightTheme = "light";
  html.dataset.darkTheme = "dark";
  for (const meta of document.querySelectorAll<HTMLMetaElement>('meta[name="theme-color"]')) {
    meta.content = theme === "dark" ? "#10141a" : "#ffffff";
  }
}

let actual: Theme | null = null;
const oyentes = new Set<() => void>();

function leer(): Theme {
  if (actual === null) {
    actual = initialTheme();
    aplicarTema(actual);
  }
  return actual;
}

function suscribir(avisar: () => void): () => void {
  oyentes.add(avisar);
  return () => oyentes.delete(avisar);
}

function cambiar(next: Theme): void {
  actual = next;
  try {
    localStorage.setItem(KEY, next);
  } catch {
    // not persisted; still applies for this session
  }
  aplicarTema(next);
  for (const avisar of oyentes) avisar();
}

export function useTheme(): [Theme, () => void] {
  const theme = useSyncExternalStore(suscribir, leer, (): Theme => "light");
  const toggle = useCallback(() => cambiar(leer() === "dark" ? "light" : "dark"), []);
  return [theme, toggle];
}
