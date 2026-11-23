// What jsdom lacks and Primer needs (ActionMenu and IconButton read window.matchMedia; ConfirmationDialog and
// Overlay observe their size). Test-only: call it at the top of a test file. Candidate for src/test/setup.ts.

import { vi } from "vitest";

export function simularNavegador(): void {
  if (typeof window.matchMedia !== "function") {
    vi.stubGlobal(
      "matchMedia",
      (query: string): MediaQueryList =>
        ({
          matches: false,
          media: query,
          onchange: null,
          addListener: () => undefined,
          removeListener: () => undefined,
          addEventListener: () => undefined,
          removeEventListener: () => undefined,
          dispatchEvent: () => false,
        }) as MediaQueryList,
    );
  }
  if (typeof globalThis.ResizeObserver !== "function") {
    vi.stubGlobal(
      "ResizeObserver",
      class {
        observe() {}
        unobserve() {}
        disconnect() {}
      },
    );
  }
}
