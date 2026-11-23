// jsdom has no window.matchMedia, and Primer's Spinner (shown by a Button while `loading`) asks for it.
// The calculators' tests install this stub; src/test/setup.ts could do it once for every test.

export function simularMatchMedia(): void {
  if (typeof window.matchMedia === "function") return;
  window.matchMedia = (query: string): MediaQueryList =>
    ({
      matches: false,
      media: query,
      onchange: null,
      addListener: () => undefined,
      removeListener: () => undefined,
      addEventListener: () => undefined,
      removeEventListener: () => undefined,
      dispatchEvent: () => false,
    }) as MediaQueryList;
}
