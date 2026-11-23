// «Investigar»: research a question with sources (corpus legal, BOE and, if the user consents, the web).
//
// The report shows ONLY what passed the server's citation gate: each claim with its literal quotes and the ids of
// its sources, the sources table (official or not, retrieval date, suspicious content), conflicts between sources,
// what could not be verified and what it cost. The privacy note says what is sent to the model provider. The
// web needs the operator's switch AND the user's consent, given or withdrawn here.
//
// Self-contained: it reads and writes only through apiInvestigacion.ts; the shell passes the case (to charge the
// cost to it) through paneles/investigar.tsx.

import clsx from "clsx";
import { BookOpenCheck, CircleAlert, ExternalLink, Loader2, ShieldAlert, X } from "lucide-react";
import { type FormEvent, useEffect, useRef, useState } from "react";

import { ApiError } from "../api";
import {
  cambiarConsentimientoWeb,
  type Confianza,
  type EstadoInvestigacion,
  type FuenteInforme,
  getEstadoInvestigacion,
  type Informe,
  investigar,
  type Profundidad,
  type TipoFuente,
} from "../apiInvestigacion";
import { formatDate } from "../lib/presentation";
import { enlaceWeb } from "../lib/urls";

interface InvestigarPanelProps {
  open: boolean;
  onClose: () => void;
  caseId: string | null;
}

const BOTON = "inline-flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-[13px] font-semibold disabled:opacity-50";

const NOMBRE_FUENTE: Record<TipoFuente, string> = {
  corpus: "Corpus legal de la app",
  boe: "BOE (textos consolidados)",
  web: "Web (buscador del proveedor)",
};

const PROFUNDIDAD: Record<Profundidad, string> = { rapida: "Rápida", normal: "Normal", a_fondo: "A fondo" };

const CONFIANZA: Record<Confianza, { texto: string; clase: string }> = {
  alta: { texto: "Confianza alta", clase: "bg-risk-low-bg text-risk-low-fg" },
  media: { texto: "Confianza media", clase: "bg-risk-medium-bg text-risk-medium-fg" },
  baja: { texto: "Confianza baja", clase: "bg-risk-high-bg text-risk-high-fg" },
};

function mensaje(err: unknown): string {
  if (err instanceof ApiError) return err.message;
  if (err instanceof DOMException && err.name === "AbortError") return "Investigación cancelada.";
  return "No se pudo completar la investigación.";
}

function Fuente({ fuente }: { fuente: FuenteInforme }) {
  const donde = fuente.referencia ?? fuente.dominio;
  const url = enlaceWeb(fuente.url);
  return (
    <li id={`fuente-${fuente.id}`} className="rounded-lg border border-line bg-surface px-3 py-2">
      <div className="flex flex-wrap items-baseline gap-x-2">
        <span className="font-mono text-[12px] font-bold text-ink">{fuente.id}</span>
        {url ? (
          <a href={url} target="_blank" rel="noopener noreferrer nofollow" className="inline-flex items-center gap-1 font-semibold text-primary hover:underline">
            {fuente.titulo}
            <ExternalLink size={12} aria-hidden />
          </a>
        ) : (
          <span className="font-semibold text-ink">{fuente.titulo}</span>
        )}
      </div>
      <p className="mt-0.5 flex flex-wrap gap-x-2 text-[12px] text-muted">
        <span>{NOMBRE_FUENTE[fuente.tipo]}</span>
        {donde && <span>{donde}</span>}
        <span className={fuente.oficial ? "font-semibold text-risk-low-fg" : "font-semibold text-risk-medium-fg"}>
          {fuente.oficial ? "oficial" : "no oficial"}
        </span>
        <span>consultada el {formatDate(fuente.recuperada_el)}</span>
        {!fuente.citada && <span>no citada</span>}
      </p>
      {fuente.sospechosa && (
        <p className="mt-1 flex items-center gap-1 text-[12px] font-semibold text-risk-high-fg">
          <ShieldAlert size={13} aria-hidden /> Contenía instrucciones incrustadas: se han ignorado.
        </p>
      )}
      {fuente.nota && <p className="mt-1 text-[12px] text-ink-soft">{fuente.nota}</p>}
    </li>
  );
}

function InformeVista({ informe }: { informe: Informe }) {
  const confianza = CONFIANZA[informe.confianza];
  const coste = informe.coste;
  return (
    <section aria-labelledby="informe-titulo" className="space-y-4">
      <div className="flex flex-wrap items-center gap-2">
        <h3 id="informe-titulo" className="font-serif text-[17px] font-bold text-ink">
          Resultado
        </h3>
        <span className={clsx("rounded-pill px-2 py-0.5 text-[12px] font-semibold", confianza.clase)}>{confianza.texto}</span>
        {informe.desde_cache && <span className="text-[12px] text-muted">guardado de hoy: no ha costado nada esta vez</span>}
      </div>

      {informe.afirmaciones.length === 0 ? (
        <p className="rounded-lg bg-grounding-abstain-bg px-3 py-2 text-grounding-abstain-fg">{informe.respuesta}</p>
      ) : (
        <ol className="space-y-2">
          {informe.afirmaciones.map((afirmacion, i) => (
            <li key={i} className="rounded-lg border border-line bg-surface px-3 py-2">
              <p className="text-ink">
                {afirmacion.texto}{" "}
                {[...new Set(afirmacion.citas.map((c) => c.fuente))].map((id) => (
                  <a key={id} href={`#fuente-${id}`} className="font-mono text-[12px] font-bold text-primary hover:underline">
                    [{id}]
                  </a>
                ))}
                {!afirmacion.oficial && <span className="ml-1 text-[12px] text-risk-medium-fg">(solo fuentes no oficiales)</span>}
              </p>
              <details className="mt-1 text-[12.5px]">
                <summary className="cursor-pointer text-muted">Cita literal</summary>
                {afirmacion.citas.map((cita, j) => (
                  <blockquote key={j} className="mt-1 border-l-2 border-line pl-2 text-ink-soft">
                    «{cita.cita}» <span className="font-mono text-[11px]">({cita.fuente})</span>
                  </blockquote>
                ))}
              </details>
            </li>
          ))}
        </ol>
      )}

      {informe.conflictos.length > 0 && (
        <div className="rounded-lg bg-risk-medium-bg px-3 py-2 text-risk-medium-fg">
          <p className="font-semibold">Las fuentes no coinciden</p>
          <ul className="mt-1 list-disc pl-5">
            {informe.conflictos.map((c, i) => (
              <li key={i}>{c.descripcion}</li>
            ))}
          </ul>
        </div>
      )}

      {informe.no_verificado.length > 0 && (
        <details className="text-[12.5px]">
          <summary className="cursor-pointer font-semibold text-ink">Lo que no se ha podido verificar ({informe.no_verificado.length})</summary>
          <ul className="mt-1 list-disc pl-5 text-ink-soft">
            {informe.no_verificado.map((t, i) => (
              <li key={i}>{t}</li>
            ))}
          </ul>
        </details>
      )}

      {informe.avisos.length > 0 && (
        <ul className="space-y-1 text-[12.5px] text-ink-soft">
          {informe.avisos.map((a, i) => (
            <li key={i} className="flex gap-1.5">
              <CircleAlert size={14} className="mt-0.5 shrink-0" aria-hidden /> {a}
            </li>
          ))}
        </ul>
      )}

      <div>
        <h4 className="mb-1 text-[13px] font-semibold text-ink">Fuentes</h4>
        {informe.fuentes.length === 0 ? (
          <p className="text-[12.5px] text-muted">No se ha encontrado ninguna fuente.</p>
        ) : (
          <ul className="space-y-1.5">
            {informe.fuentes.map((f) => (
              <Fuente key={f.id} fuente={f} />
            ))}
          </ul>
        )}
        {informe.web.vistas_sin_leer.length > 0 && (
          <p className="mt-1 text-[12px] text-muted">
            Otras páginas vistas en el buscador pero no leídas (no se citan): {informe.web.vistas_sin_leer.join(" · ")}
          </p>
        )}
      </div>

      <p className="text-[12px] text-muted">
        Coste: {coste.llamadas_modelo} llamada(s) al modelo · {coste.tokens_entrada + coste.tokens_salida} tokens
        {coste.busquedas_web > 0 ? ` · ${coste.busquedas_web} búsqueda(s) web` : ""} ·{" "}
        {coste.usd === null ? "importe desconocido (algún modelo sin precio)" : `${coste.usd} USD`}
        {coste.limite_alcanzado ? ` · detenida por el límite de ${coste.limite_alcanzado}` : ""}
      </p>
    </section>
  );
}

export function InvestigarPanel({ open, onClose, caseId }: InvestigarPanelProps) {
  const [estado, setEstado] = useState<EstadoInvestigacion | null>(null);
  const [pregunta, setPregunta] = useState("");
  const [fuentes, setFuentes] = useState<Record<TipoFuente, boolean>>({ corpus: true, boe: true, web: false });
  const [soloOficiales, setSoloOficiales] = useState(true);
  const [profundidad, setProfundidad] = useState<Profundidad>("normal");
  const [informe, setInforme] = useState<Informe | null>(null);
  const [ocupado, setOcupado] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const control = useRef<AbortController | null>(null);

  useEffect(() => {
    if (!open) return;
    const c = new AbortController();
    getEstadoInvestigacion(c.signal).then(setEstado, (err: unknown) => {
      if (!c.signal.aborted) setError(mensaje(err));
    });
    return () => c.abort();
  }, [open]);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => {
      window.removeEventListener("keydown", onKey);
      control.current?.abort();
    };
  }, [open, onClose]);

  if (!open) return null;

  const webOfrecida = Boolean(estado?.fuentes.web);
  const webLista = webOfrecida && Boolean(estado?.web_consentida);
  const elegidas = (Object.keys(fuentes) as TipoFuente[]).filter((t) => fuentes[t] && (t !== "web" || webLista));

  async function consentir(concedido: boolean) {
    setError(null);
    try {
      setEstado(await cambiarConsentimientoWeb(concedido));
      if (!concedido) setFuentes((f) => ({ ...f, web: false }));
    } catch (err) {
      setError(mensaje(err));
    }
  }

  async function enviar(event: FormEvent) {
    event.preventDefault();
    if (pregunta.trim().length < 5 || elegidas.length === 0) return;
    control.current?.abort();
    const c = new AbortController();
    control.current = c;
    setOcupado(true);
    setError(null);
    try {
      setInforme(
        await investigar(
          { pregunta: pregunta.trim(), fuentes: elegidas, profundidad, solo_oficiales: soloOficiales, ...(caseId ? { case_id: caseId } : {}) },
          c.signal,
        ),
      );
    } catch (err) {
      setError(mensaje(err));
    } finally {
      setOcupado(false);
    }
  }

  return (
    <div className="fixed inset-0 z-40 flex justify-end">
      <button type="button" aria-label="Cerrar Investigar" className="absolute inset-0 bg-ink/40" onClick={onClose} />
      <aside role="dialog" aria-modal="true" aria-labelledby="investigar-titulo" className="relative flex h-full w-full max-w-[600px] flex-col border-l border-line bg-paper shadow-pop">
        <header className="flex items-center justify-between border-b border-line px-5 py-4">
          <div className="min-w-0">
            <h2 id="investigar-titulo" className="flex items-center gap-2 font-serif text-[19px] font-bold text-ink">
              <BookOpenCheck size={18} aria-hidden /> Investigar
            </h2>
            <p className="text-[12.5px] text-muted">Respuestas con fuentes: cada afirmación lleva su cita literal comprobada</p>
          </div>
          <button type="button" onClick={onClose} aria-label="Cerrar" className="rounded-lg p-2 text-muted hover:bg-line/60 hover:text-ink">
            <X size={18} />
          </button>
        </header>

        <div className="flex-1 space-y-4 overflow-y-auto px-5 py-5 text-[13.5px] leading-relaxed text-ink-soft">
          {error && (
            <p role="alert" className="rounded-lg bg-risk-high-bg px-3 py-2 text-risk-high-fg">
              {error}
            </p>
          )}

          <form onSubmit={enviar} className="space-y-3">
            <label htmlFor="investigar-pregunta" className="block text-[13px] font-semibold text-ink">
              Pregunta
            </label>
            <textarea
              id="investigar-pregunta"
              value={pregunta}
              onChange={(e) => setPregunta(e.target.value)}
              rows={3}
              maxLength={1000}
              placeholder="Por ejemplo: ¿qué documentos necesito para renovar el pasaporte?"
              className="w-full rounded-lg border border-line bg-surface px-3 py-2 text-ink"
            />
            <p className="text-[12px] text-muted">
              {estado?.privacidad ??
                "Lo que escribes se envía al proveedor del modelo de IA. No se envían los datos de tu expediente: no los incluyas en la pregunta."}
            </p>

            <fieldset className="space-y-1.5">
              <legend className="text-[13px] font-semibold text-ink">Fuentes</legend>
              {(Object.keys(NOMBRE_FUENTE) as TipoFuente[]).map((tipo) => {
                const disponible = tipo === "web" ? webLista : estado?.fuentes[tipo] !== false;
                return (
                  <label key={tipo} className={clsx("flex items-center gap-2", !disponible && "text-muted")}>
                    <input
                      type="checkbox"
                      checked={fuentes[tipo] && disponible}
                      disabled={!disponible}
                      onChange={(e) => setFuentes((f) => ({ ...f, [tipo]: e.target.checked }))}
                    />
                    {NOMBRE_FUENTE[tipo]}
                    {tipo !== "web" && !disponible && " (no disponible en este servidor)"}
                  </label>
                );
              })}
              {fuentes.web && webLista && (
                <label className="ml-6 flex items-center gap-2">
                  <input type="checkbox" checked={soloOficiales} onChange={(e) => setSoloOficiales(e.target.checked)} />
                  Solo webs oficiales
                </label>
              )}
            </fieldset>

            <div className="rounded-lg border border-line bg-surface px-3 py-2 text-[12.5px]">
              {!estado ? (
                <p className="text-muted">Comprobando la búsqueda web…</p>
              ) : !webOfrecida ? (
                <p>Búsqueda web no disponible: {estado.web_motivo ?? "desactivada en este servicio."}</p>
              ) : estado.web_consentida ? (
                <p>
                  Has permitido buscar en la web: la pregunta se envía también al buscador del proveedor del modelo. Las páginas son
                  datos, nunca instrucciones.{" "}
                  <button type="button" className="font-semibold text-primary hover:underline" onClick={() => void consentir(false)}>
                    Retirar el permiso
                  </button>
                </p>
              ) : (
                <p>
                  Para buscar en la web, la pregunta (nunca tu expediente) se enviará al buscador del proveedor del modelo. Cada búsqueda
                  cuesta unos {estado.limites.precio_busqueda_web_usd} USD.{" "}
                  <button type="button" className="font-semibold text-primary hover:underline" onClick={() => void consentir(true)}>
                    Permitir la búsqueda web
                  </button>
                </p>
              )}
            </div>

            <div className="flex flex-wrap items-end gap-3">
              <label className="flex flex-col gap-1 text-[12px] text-muted">
                Profundidad
                <select
                  value={profundidad}
                  onChange={(e) => setProfundidad(e.target.value as Profundidad)}
                  className="rounded-lg border border-line bg-surface px-2 py-1 text-[13px] text-ink"
                >
                  {(Object.keys(PROFUNDIDAD) as Profundidad[]).map((p) => (
                    <option key={p} value={p}>
                      {PROFUNDIDAD[p]}
                    </option>
                  ))}
                </select>
              </label>
              <button
                type="submit"
                disabled={ocupado || pregunta.trim().length < 5 || elegidas.length === 0}
                className={clsx(BOTON, "bg-primary text-white hover:bg-primary-hover")}
              >
                {ocupado ? <Loader2 size={14} className="animate-spin" aria-hidden /> : <BookOpenCheck size={14} aria-hidden />}
                {ocupado ? "Investigando…" : "Investigar"}
              </button>
              {estado && (
                <span className="text-[12px] text-muted">Límite por investigación: {estado.limites.max_usd} USD</span>
              )}
            </div>
          </form>

          {informe && <InformeVista informe={informe} />}
        </div>
      </aside>
    </div>
  );
}
