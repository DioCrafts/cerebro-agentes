// Test helpers: what jsdom lacks and some Primer components use. Import it in a test that renders them:
//   - matchMedia: Primer's Spinner (and so any Button with `loading`) asks it about reduced motion;
//   - ResizeObserver: Primer's Dialog (and so useConfirm) watches its body for overflow.

if (typeof window.matchMedia !== "function") {
  window.matchMedia = (query: string) =>
    ({
      matches: false,
      media: query,
      onchange: null,
      addEventListener: () => undefined,
      removeEventListener: () => undefined,
      addListener: () => undefined,
      removeListener: () => undefined,
      dispatchEvent: () => false,
    }) as MediaQueryList;
}

if (typeof globalThis.ResizeObserver !== "function") {
  globalThis.ResizeObserver = class {
    observe() {}
    unobserve() {}
    disconnect() {}
  };
}
