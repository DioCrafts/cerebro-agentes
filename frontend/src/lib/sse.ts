// Server-Sent Events over fetch: an incremental parser and a follower that reconnects.
//
// Not EventSource: the agent's turn is a POST (EventSource only GETs), and the follower needs its own
// growing wait between attempts (EventSource retries at a fixed pace and gives up on any HTTP error).
// The follower sends `Last-Event-ID` on every reconnection, so the server does not repeat what was seen.

export interface EventoSse {
  event: string;
  data: string;
  id: string | null;
}

/** Feed it text as it arrives; it returns the complete events (comments and partial lines are kept back). */
export class LectorSse {
  private resto = "";
  private nombre = "";
  private datos: string[] = [];
  /** The last `id:` seen, kept across events as the SSE spec says. */
  ultimoId: string | null = null;

  leer(texto: string): EventoSse[] {
    this.resto += texto;
    const eventos: EventoSse[] = [];
    // Lines end in \n, \r\n or \r; the last piece may be incomplete and waits for the next chunk.
    const lineas = this.resto.split(/\r\n|\n|\r/);
    this.resto = lineas.pop() ?? "";
    for (const linea of lineas) {
      if (linea === "") {
        if (this.datos.length > 0) eventos.push({ event: this.nombre || "message", data: this.datos.join("\n"), id: this.ultimoId });
        this.nombre = "";
        this.datos = [];
        continue;
      }
      if (linea.startsWith(":")) continue;
      const dosPuntos = linea.indexOf(":");
      const campo = dosPuntos === -1 ? linea : linea.slice(0, dosPuntos);
      let valor = dosPuntos === -1 ? "" : linea.slice(dosPuntos + 1);
      if (valor.startsWith(" ")) valor = valor.slice(1);
      if (campo === "event") this.nombre = valor;
      else if (campo === "data") this.datos.push(valor);
      else if (campo === "id" && !valor.includes("\0")) this.ultimoId = valor;
    }
    return eventos;
  }
}

/** Read a response body as SSE, calling `alEvento` for each event; stops early when it returns "fin". */
export async function leerCuerpoSse(
  cuerpo: ReadableStream<Uint8Array>,
  alEvento: (evento: EventoSse) => "seguir" | "fin",
  alLeer?: () => void,
): Promise<"fin" | "cerrado"> {
  const reader = cuerpo.getReader();
  const decoder = new TextDecoder();
  const lector = new LectorSse();
  try {
    for (;;) {
      const { done, value } = await reader.read();
      alLeer?.();
      const eventos = lector.leer(decoder.decode(value ?? new Uint8Array(), { stream: !done }));
      for (const evento of eventos) {
        if (alEvento(evento) === "fin") return "fin";
      }
      if (done) return "cerrado";
    }
  } finally {
    reader.cancel().catch(() => undefined);
  }
}

const ESPERA_BASE_MS = 4000;
const ESPERA_MAXIMA_MS = 60_000;

/** How long to wait before retry number `intento` (0-based): 4 s, 8 s, 16 s… up to a minute. */
export function esperaReintento(intento: number): number {
  return Math.min(ESPERA_BASE_MS * 2 ** intento, ESPERA_MAXIMA_MS);
}

export interface OpcionesFlujo {
  /** Open the stream (with `Last-Event-ID` when there is one); rejects on an HTTP or network error. */
  abrir: (ultimoId: string | null, signal: AbortSignal) => Promise<ReadableStream<Uint8Array>>;
  /** Each event; "fin" ends the follow-up for good. */
  alEvento: (evento: EventoSse) => "seguir" | "fin";
  /** A failed attempt; `reintentara` says whether another one is coming. */
  alError: (err: unknown, reintentara: boolean) => void;
  /** Whether an error is worth another attempt (network trouble and server errors, not a refusal). */
  reintentable: (err: unknown) => boolean;
  maxReintentos?: number;
  /** A stream with no bytes (not even the server's keep-alive) for this long is taken as dead. */
  silencioMaximoMs?: number;
}

// The server closes a long stream on purpose (its maximum duration): reconnect soon, not after a back-off.
const ESPERA_TRAS_CIERRE_MS = 500;
const SILENCIO_MAXIMO_MS = 45_000;

function dormir(ms: number, signal: AbortSignal): Promise<void> {
  return new Promise((resolve) => {
    if (signal.aborted) return resolve();
    const id = setTimeout(() => {
      signal.removeEventListener("abort", alAbortar);
      resolve();
    }, ms);
    const alAbortar = () => {
      clearTimeout(id);
      resolve();
    };
    signal.addEventListener("abort", alAbortar, { once: true });
  });
}

/**
 * Follow a stream until it says "fin", the caller stops it, or a non-retryable error. Reconnects with
 * `Last-Event-ID` after a dropped connection and waits longer after each consecutive failure (an event
 * received resets the count). Returns the function that stops it (for an effect's cleanup).
 */
export function seguirFlujo(opciones: OpcionesFlujo): () => void {
  const parar = new AbortController();
  const maxReintentos = opciones.maxReintentos ?? 6;
  const silencioMaximo = opciones.silencioMaximoMs ?? SILENCIO_MAXIMO_MS;

  void (async () => {
    let ultimoId: string | null = null;
    let fallos = 0;
    while (!parar.signal.aborted) {
      // One controller per connection: stopping, or a stream gone silent, aborts it.
      const conexion = new AbortController();
      const alParar = () => conexion.abort();
      parar.signal.addEventListener("abort", alParar, { once: true });
      let vigilante: ReturnType<typeof setTimeout> | undefined;
      let callado = false;
      const vigilar = () => {
        clearTimeout(vigilante);
        vigilante = setTimeout(() => {
          callado = true;
          conexion.abort();
        }, silencioMaximo);
      };
      try {
        vigilar();
        const cuerpo = await opciones.abrir(ultimoId, conexion.signal);
        const final = await leerCuerpoSse(
          cuerpo,
          (evento) => {
            if (evento.id !== null) ultimoId = evento.id;
            fallos = 0;
            return opciones.alEvento(evento);
          },
          vigilar,
        );
        if (final === "fin") return;
        await dormir(ESPERA_TRAS_CIERRE_MS, parar.signal);
      } catch (err) {
        if (parar.signal.aborted) return;
        const error = callado ? new Error("El servidor dejó de responder.", { cause: err }) : err;
        const reintentara = (callado || opciones.reintentable(error)) && fallos < maxReintentos;
        opciones.alError(error, reintentara);
        if (!reintentara) return;
        await dormir(esperaReintento(fallos++), parar.signal);
      } finally {
        clearTimeout(vigilante);
        parar.signal.removeEventListener("abort", alParar);
      }
    }
  })();

  return () => parar.abort();
}
