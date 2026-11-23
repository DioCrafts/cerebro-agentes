// Test helpers: response bodies in Server-Sent Events format.

const codificar = new TextEncoder();

/** A body that sends `trozos` and then closes, or stays open (until `signal` aborts it) when `abierto`. */
export function cuerpoSse(trozos: string[], { abierto = false, signal }: { abierto?: boolean; signal?: AbortSignal } = {}) {
  return new ReadableStream<Uint8Array>({
    start(c) {
      for (const t of trozos) c.enqueue(codificar.encode(t));
      if (!abierto) c.close();
      signal?.addEventListener("abort", () => c.error(new DOMException("Aborted", "AbortError")));
    },
  });
}
