/* ui-legado: pendiente de migrar a src/ui (docs/ui/sistema-de-diseno.md) */
// «Lo que sé de ti»: what the assistant remembers from earlier conversations, grouped by kind, each item with
// where it came from («Me lo dijiste el 12/11/2026: “…”»). The user can correct any item (their wording wins),
// forget one or everything, pause the memory, and see or delete the earlier conversations with their summaries.

import clsx from "clsx";
import { Brain, History, MessageSquareText, Pencil, ShieldAlert, Trash2, X } from "lucide-react";
import { useCallback, useEffect, useId, useState } from "react";

import { formatDate } from "../lib/presentation";
import { errorMessage } from "../lib/memoria";
import {
  TIPOS_RECUERDO,
  TIPO_RECUERDO_LABEL,
  borrarConversacion,
  cambiarInterruptor,
  corregirRecuerdo,
  getConversaciones,
  getRecuerdos,
  olvidarRecuerdo,
  olvidarTodo,
  type ConversacionesResponse,
  type Recuerdo,
  type RecuerdosResponse,
  type TipoRecuerdo,
} from "../apiRecuerdos";

interface RecuerdosPanelProps {
  open: boolean;
  onClose: () => void;
  caseId: string | null;
}

type Pestana = "recuerdos" | "conversaciones";

const BOTON =
  "inline-flex items-center gap-1 rounded-lg border border-line px-2 py-1 text-[12px] font-semibold text-ink-soft hover:border-primary focus-visible:outline-solid focus-visible:outline-2 focus-visible:outline-primary";
const BOTON_PELIGRO =
  "inline-flex items-center gap-1 rounded-lg border border-line px-2 py-1 text-[12px] font-semibold text-ink-soft hover:border-risk-high-fg hover:text-risk-high-fg focus-visible:outline-solid focus-visible:outline-2 focus-visible:outline-primary";

function origenTexto(r: Recuerdo): string {
  const fecha = formatDate(r.origen.fecha) ?? r.origen.fecha;
  return `Me lo dijiste el ${fecha}`;
}

function Edicion({
  recuerdo,
  onGuardar,
  onCancelar,
}: {
  recuerdo: Recuerdo;
  onGuardar: (texto: string, tipo: TipoRecuerdo) => Promise<void>;
  onCancelar: () => void;
}) {
  const [texto, setTexto] = useState(recuerdo.texto);
  const [tipo, setTipo] = useState<TipoRecuerdo>(recuerdo.tipo);
  const [enviando, setEnviando] = useState(false);
  const idTexto = useId();
  return (
    <form
      className="space-y-2 rounded-xl border border-primary/40 bg-surface px-4 py-3"
      onSubmit={(e) => {
        e.preventDefault();
        setEnviando(true);
        void onGuardar(texto.trim(), tipo).finally(() => setEnviando(false));
      }}
    >
      <label htmlFor={idTexto} className="block text-[12.5px] text-ink-soft">
        Cómo quieres que lo recuerde
      </label>
      <textarea
        id={idTexto}
        required
        maxLength={600}
        rows={3}
        value={texto}
        onChange={(e) => setTexto(e.target.value)}
        className="block w-full rounded-sm border border-line bg-paper px-2 py-1 text-[13px] text-ink"
      />
      <label className="block text-[12.5px] text-ink-soft">
        Tipo
        <select
          value={tipo}
          onChange={(e) => setTipo(e.target.value as TipoRecuerdo)}
          className="mt-1 block w-full rounded-sm border border-line bg-paper px-2 py-1 text-[13px] text-ink"
        >
          {TIPOS_RECUERDO.map((t) => (
            <option key={t} value={t}>
              {TIPO_RECUERDO_LABEL[t]}
            </option>
          ))}
        </select>
      </label>
      <p className="text-[11.5px] leading-snug text-muted">Tu versión sustituye a la mía. No escribas contraseñas ni claves: no las guardo.</p>
      <div className="flex gap-2">
        <button type="submit" disabled={enviando || !texto.trim()} className="rounded-lg bg-primary px-3 py-1 text-[12.5px] font-semibold text-white disabled:opacity-50">
          {enviando ? "Guardando…" : "Guardar"}
        </button>
        <button type="button" onClick={onCancelar} className="rounded-lg px-3 py-1 text-[12.5px] font-semibold text-ink-soft hover:bg-line/60">
          Cancelar
        </button>
      </div>
    </form>
  );
}

export function RecuerdosPanel({ open, onClose, caseId }: RecuerdosPanelProps) {
  const [datos, setDatos] = useState<RecuerdosResponse | null>(null);
  const [conversaciones, setConversaciones] = useState<ConversacionesResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [editando, setEditando] = useState<string | null>(null);
  const [pestana, setPestana] = useState<Pestana>("recuerdos");
  const [ocupado, setOcupado] = useState(false);
  const [tick, setTick] = useState(0);
  const idTitulo = useId();
  const idExplicacion = useId();

  const recargar = useCallback(() => setTick((n) => n + 1), []);

  useEffect(() => {
    if (!open) return;
    const controller = new AbortController();
    Promise.all([getRecuerdos(caseId, controller.signal), getConversaciones(caseId, controller.signal)]).then(
      ([r, c]) => {
        setDatos(r);
        setConversaciones(c);
        setError(null);
      },
      (err: unknown) => {
        if (!controller.signal.aborted) setError(`No he podido cargar lo que recuerdo. ${errorMessage(err)}`);
      },
    );
    return () => controller.abort();
  }, [open, caseId, tick]);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  if (!open) return null;

  async function hecho(promesa: Promise<unknown>, fallo = "No se ha podido guardar.") {
    setOcupado(true);
    try {
      await promesa;
      setEditando(null);
      setError(null);
      recargar();
    } catch (err) {
      setError(`${fallo} ${errorMessage(err)}`);
    } finally {
      setOcupado(false);
    }
  }

  const recuerdos = datos?.recuerdos ?? [];
  const historial = datos?.historial ?? [];
  const lista = conversaciones?.conversaciones ?? [];
  const activa = datos?.activa ?? false;
  const sinCuenta = datos?.sin_cuenta ?? false;
  const textosPorId = new Map(recuerdos.map((r) => [r.id, r.texto]));

  function tarjeta(r: Recuerdo) {
    if (editando === r.id) {
      return (
        <li key={r.id}>
          <Edicion
            recuerdo={r}
            onCancelar={() => setEditando(null)}
            onGuardar={(texto, tipo) => hecho(corregirRecuerdo(caseId, r.id, { texto, tipo }))}
          />
        </li>
      );
    }
    const veces = new Set(r.fuentes.map((f) => `${f.conversacion_id}:${f.cita}`)).size;
    return (
      <li key={r.id} className="rounded-xl border border-line bg-surface px-4 py-3 shadow-card">
        <p className="text-[14px] font-semibold leading-snug text-ink">{r.texto}</p>
        <p className="mt-1 text-[12.5px] leading-snug text-ink-soft">
          {origenTexto(r)}: <q className="italic">{r.origen.cita}</q>
          {r.origen.titulo_conversacion && <span className="text-muted"> (en «{r.origen.titulo_conversacion}»)</span>}
        </p>
        <p className="mt-1 flex flex-wrap gap-x-2 gap-y-1 text-[11.5px] leading-snug text-muted">
          {r.corregido && <span>Lo corregiste el {formatDate(r.actualizado)}</span>}
          {veces > 1 && <span>Me lo has dicho {veces} veces</span>}
          {r.usos > 0 && <span>Lo he tenido en cuenta {r.usos === 1 ? "una vez" : `${r.usos} veces`}</span>}
        </p>
        {r.sensible && (
          <p className="mt-1 inline-flex items-center gap-1 rounded-pill bg-risk-high-bg px-2 py-0.5 text-[11.5px] font-semibold text-risk-high-fg">
            <ShieldAlert size={12} aria-hidden />
            Dato sensible: lo guardo porque me pediste que lo recordara
          </p>
        )}
        <div className="mt-2 flex gap-2">
          <button type="button" onClick={() => setEditando(r.id)} disabled={ocupado} className={BOTON} aria-label={`Corregir: ${r.texto}`}>
            <Pencil size={12} aria-hidden />
            Corregir
          </button>
          <button
            type="button"
            disabled={ocupado}
            aria-label={`Olvidar: ${r.texto}`}
            onClick={() => {
              if (window.confirm("¿Olvido esto? Se borran tus palabras y no lo volveré a aprender de esa conversación.")) void hecho(olvidarRecuerdo(caseId, r.id), "No se ha podido olvidar.");
            }}
            className={BOTON_PELIGRO}
          >
            <Trash2 size={12} aria-hidden />
            Olvidar
          </button>
        </div>
      </li>
    );
  }

  return (
    <div className="fixed inset-0 z-50 flex justify-end">
      <button type="button" aria-label="Cerrar lo que sé de ti" className="absolute inset-0 bg-ink/40" onClick={onClose} />
      <aside role="dialog" aria-modal="true" aria-labelledby={idTitulo} className="relative flex h-full w-full max-w-[460px] flex-col border-l border-line bg-paper shadow-pop">
        <header className="flex items-center justify-between border-b border-line px-5 py-4">
          <div>
            <h2 id={idTitulo} className="flex items-center gap-2 font-serif text-[19px] font-bold text-ink">
              <Brain size={18} aria-hidden className="text-primary-dark" />
              Lo que sé de ti
            </h2>
            <p className="text-[12.5px] text-muted">Lo que me has contado en nuestras conversaciones, con tus palabras</p>
          </div>
          <button type="button" onClick={onClose} aria-label="Cerrar" className="rounded-lg p-2 text-muted hover:bg-line/60 hover:text-ink">
            <X size={18} />
          </button>
        </header>

        <div className="flex-1 space-y-4 overflow-y-auto px-5 py-5">
          {error && (
            <p role="alert" className="rounded-lg border border-risk-high-fg/30 bg-risk-high-bg px-3 py-2 text-[13px] text-ink-soft">
              {error}
            </p>
          )}

          {datos && sinCuenta && (
            <p className="text-[13.5px] leading-relaxed text-ink-soft">
              Cuando inicies sesión o tengas un expediente, aquí verás lo que recuerdo de nuestras conversaciones.
            </p>
          )}

          {datos && !sinCuenta && (
            <section aria-label="Memoria de conversaciones" className="rounded-xl border border-line bg-surface px-4 py-3 shadow-card">
              <div className="flex items-start gap-3">
                <div className="min-w-0 flex-1">
                  <p className="text-[14.5px] font-bold text-ink">{activa && datos.disponible ? "Memoria activada" : "Memoria en pausa"}</p>
                  <p className="mt-0.5 text-[12.5px] leading-snug text-ink-soft">
                    {activa && datos.disponible
                      ? "Recuerdo lo importante de cada conversación para no preguntarte dos veces."
                      : "No guardo ni uso nada nuevo. Lo que ya está sigue aquí para que lo veas o lo borres."}
                  </p>
                </div>
                <button
                  type="button"
                  role="switch"
                  aria-checked={activa}
                  aria-describedby={idExplicacion}
                  aria-label={activa ? "Pausar la memoria" : "Activar la memoria"}
                  disabled={ocupado || !datos.disponible}
                  onClick={() => void hecho(cambiarInterruptor(caseId, !activa), "No se ha podido cambiar.")}
                  className={clsx(
                    "relative h-7 w-12 shrink-0 rounded-pill transition disabled:cursor-not-allowed disabled:opacity-60",
                    activa ? "bg-primary" : "bg-line",
                  )}
                >
                  <span className={clsx("absolute top-1 h-5 w-5 rounded-full bg-white shadow-sm transition-all", activa ? "left-6" : "left-1")} />
                </button>
              </div>
              <details className="mt-2 text-[12px] leading-relaxed text-ink-soft">
                <summary className="cursor-pointer font-semibold text-primary-dark">¿Qué guardo y durante cuánto tiempo?</summary>
                <p id={idExplicacion} className="mt-1">
                  {datos.explicacion}
                </p>
              </details>
            </section>
          )}

          {datos && !sinCuenta && (
            <div role="tablist" aria-label="Qué ver" className="flex gap-1 rounded-lg bg-line/50 p-1">
              {(
                [
                  ["recuerdos", `Lo que sé (${recuerdos.length})`, Brain],
                  ["conversaciones", `Conversaciones (${lista.length})`, MessageSquareText],
                ] as const
              ).map(([id, etiqueta, Icono]) => (
                <button
                  key={id}
                  type="button"
                  role="tab"
                  aria-selected={pestana === id}
                  onClick={() => setPestana(id)}
                  className={clsx(
                    "inline-flex flex-1 items-center justify-center gap-1.5 rounded-md px-2 py-1.5 text-[12.5px] font-semibold",
                    pestana === id ? "bg-surface text-ink shadow-card" : "text-ink-soft hover:text-ink",
                  )}
                >
                  <Icono size={13} aria-hidden />
                  {etiqueta}
                </button>
              ))}
            </div>
          )}

          {datos && !sinCuenta && pestana === "recuerdos" && (
            <div role="tabpanel" aria-label="Lo que sé de ti" className="space-y-4">
              {recuerdos.length === 0 && (
                <p className="text-[13.5px] leading-relaxed text-ink-soft">
                  Todavía no recuerdo nada de ti. Cuando me cuentes algo que convenga tener en cuenta (tu hipoteca, cómo prefieres que te explique las cosas…), lo verás aquí con tus palabras y la fecha.
                </p>
              )}
              {TIPOS_RECUERDO.map((tipo) => {
                const delTipo = recuerdos.filter((r) => r.tipo === tipo);
                if (delTipo.length === 0) return null;
                return (
                  <section key={tipo} aria-label={TIPO_RECUERDO_LABEL[tipo]} className="space-y-2">
                    <h3 className="text-[12px] font-bold uppercase tracking-[0.08em] text-ink-soft">
                      {TIPO_RECUERDO_LABEL[tipo]} ({delTipo.length})
                    </h3>
                    <ul className="space-y-2">{delTipo.map(tarjeta)}</ul>
                  </section>
                );
              })}
              {historial.length > 0 && (
                <details className="rounded-xl border border-line bg-surface px-4 py-3 text-[12.5px] text-ink-soft">
                  <summary className="flex cursor-pointer items-center gap-1.5 font-semibold text-ink">
                    <History size={13} aria-hidden />
                    Lo que ha cambiado ({historial.length})
                  </summary>
                  <ul className="mt-2 space-y-2">
                    {historial.map((h) => (
                      <li key={h.id} className="leading-snug">
                        <p>
                          <span className="text-muted">Antes ({formatDate(h.origen.fecha)}):</span> {h.texto}
                        </p>
                        {h.sustituido_por && textosPorId.has(h.sustituido_por) && (
                          <p className="text-muted">Ahora: {textosPorId.get(h.sustituido_por)}</p>
                        )}
                        <button
                          type="button"
                          disabled={ocupado}
                          aria-label={`Olvidar la versión anterior: ${h.texto}`}
                          onClick={() => void hecho(olvidarRecuerdo(caseId, h.id), "No se ha podido olvidar.")}
                          className={clsx(BOTON_PELIGRO, "mt-1")}
                        >
                          <Trash2 size={12} aria-hidden />
                          Olvidar
                        </button>
                      </li>
                    ))}
                  </ul>
                </details>
              )}
            </div>
          )}

          {datos && !sinCuenta && pestana === "conversaciones" && (
            <div role="tabpanel" aria-label="Conversaciones anteriores" className="space-y-2">
              {lista.length === 0 && <p className="text-[13.5px] leading-relaxed text-ink-soft">No hay conversaciones guardadas.</p>}
              <ul className="space-y-2">
                {lista.map((c) => (
                  <li key={c.id} className="rounded-xl border border-line bg-surface px-4 py-3 shadow-card">
                    <h4 className="text-[14px] font-bold leading-snug text-ink">{c.titulo}</h4>
                    <p className="text-[11.5px] text-muted">
                      {formatDate(c.actualizada)}
                      {c.recuerdos > 0 && ` · ${c.recuerdos === 1 ? "aprendí una cosa" : `aprendí ${c.recuerdos} cosas`}`}
                      {c.retenidos > 0 ? ` · guardo sus ${c.retenidos} mensajes unos días` : " · ya no guardo sus mensajes"}
                    </p>
                    <p className="mt-1 text-[13px] leading-snug text-ink-soft">{c.resumen ?? "Aún sin resumir."}</p>
                    <button
                      type="button"
                      disabled={ocupado}
                      aria-label={`Borrar la conversación «${c.titulo}»`}
                      onClick={() => {
                        if (window.confirm("¿Borro esta conversación? Se borran sus mensajes, su resumen y lo que aprendí solo de ella.")) void hecho(borrarConversacion(caseId, c.id), "No se ha podido borrar.");
                      }}
                      className={clsx(BOTON_PELIGRO, "mt-2")}
                    >
                      <Trash2 size={12} aria-hidden />
                      Borrar conversación
                    </button>
                  </li>
                ))}
              </ul>
            </div>
          )}

          {datos && !sinCuenta && (recuerdos.length > 0 || lista.length > 0) && (
            <button
              type="button"
              disabled={ocupado}
              onClick={() => {
                if (window.confirm("¿Olvido todo? Se borran lo que sé de ti, los resúmenes y las conversaciones guardadas. No se puede deshacer.")) void hecho(olvidarTodo(caseId), "No se ha podido borrar.");
              }}
              className="inline-flex w-full items-center justify-center gap-1.5 rounded-lg border border-risk-high-fg/40 px-3 py-2 text-[13px] font-semibold text-risk-high-fg hover:bg-risk-high-bg"
            >
              <Trash2 size={14} aria-hidden />
              Olvidar todo
            </button>
          )}
        </div>
        <footer className="border-t border-line px-5 py-3 text-[11.5px] leading-relaxed text-muted">
          Se guarda en el servidor, se incluye en la exportación de tu cuenta y se borra con ella.
          {datos ? ` Los mensajes tal cual se borran a los ${datos.retencion_dias} días.` : ""}
        </footer>
      </aside>
    </div>
  );
}
