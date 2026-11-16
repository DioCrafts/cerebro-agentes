// «Tareas»: the user's long-running tasks (renew the passport, prepare the tax return, change the electricity
// tariff…). A list, and for each task the timeline of its steps with what each one produced, what it waits for
// and the buttons the user decides with: start a proposal, mark a step done (with a date when it asks for one),
// skip it, retry, pause, resume, cancel (with a reason, which is remembered). A pending ACTION is confirmed or
// rejected with the same call as in «Módulos» (`onAccion`): this panel never runs anything outside the app.
//
// Self-contained: it reads and writes only through apiTareas.ts; the shell passes the case and the action
// handler (paneles/tareas.tsx).

import clsx from "clsx";
import {
  ArrowLeft,
  Check,
  CircleAlert,
  CircleDashed,
  Clock,
  Hourglass,
  ListChecks,
  Loader2,
  Pause,
  Play,
  RotateCcw,
  SkipForward,
  X,
} from "lucide-react";
import { type FormEvent, useEffect, useRef, useState } from "react";

import { ApiError } from "../api";
import {
  cancelarTarea,
  confirmarPaso,
  crearTarea,
  ESTADO_PASO,
  ESTADO_TAREA,
  EVENTO_HISTORIAL,
  type EstadoTarea,
  getPlantillas,
  getTarea,
  getTareas,
  omitirPaso,
  operarTarea,
  type OperacionTarea,
  type Paso,
  type Plantilla,
  type ResumenTarea,
  type TareaDetalle,
} from "../apiTareas";
import { formatDate } from "../lib/presentation";

interface TareasPanelProps {
  open: boolean;
  onClose: () => void;
  caseId: string | null;
  /** Confirm (true) or reject (false) a pending action: the same handler as «Módulos». */
  onAccion: (accionId: string, confirmar: boolean) => Promise<void>;
  /** Something changed that other panels show (notices, pending actions). */
  onCambio?: () => void;
}

const BOTON = "inline-flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-[13px] font-semibold disabled:opacity-50";
const PRIMARIO = clsx(BOTON, "bg-primary text-white hover:bg-primary-hover");
const SECUNDARIO = clsx(BOTON, "border border-line bg-surface text-ink hover:bg-line/40");
const INPUT = "w-full rounded-lg border border-line bg-surface px-3 py-2 text-[14px] text-ink outline-none focus:border-primary";

const TONO: Record<EstadoTarea, string> = {
  propuesta: "bg-primary-soft text-ink",
  activa: "bg-cited-bg text-cited-fg",
  pausada: "bg-line/60 text-ink-soft",
  esperando_usuario: "bg-grounding-uncited-bg text-ink",
  esperando_evento: "bg-line/60 text-ink-soft",
  completada: "bg-cited-bg text-cited-fg",
  cancelada: "bg-line/60 text-muted",
  fallida: "bg-risk-high-bg text-risk-high-fg",
};

function mensaje(err: unknown): string {
  return err instanceof ApiError ? err.message : "No se ha podido completar. Inténtalo de nuevo.";
}

function IconoPaso({ paso }: { paso: Paso }) {
  const props = { size: 16, "aria-hidden": true as const };
  switch (paso.estado) {
    case "hecho":
      return <Check {...props} className="text-cited-fg" />;
    case "omitido":
      return <SkipForward {...props} className="text-muted" />;
    case "fallido":
      return <CircleAlert {...props} className="text-risk-high-fg" />;
    case "esperando":
      return paso.espera_actual?.motivo === "fecha" ? <Clock {...props} className="text-ink-soft" /> : <Hourglass {...props} className="text-ink" />;
    case "cancelado":
      return <X {...props} className="text-muted" />;
    default:
      return <CircleDashed {...props} className="text-muted" />;
  }
}

function Insignia({ estado }: { estado: EstadoTarea }) {
  return <span className={clsx("rounded-full px-2 py-0.5 text-[11.5px] font-semibold", TONO[estado])}>{ESTADO_TAREA[estado]}</span>;
}

function PasoTimeline({
  paso,
  enMarcha,
  ocupado,
  onConfirmar,
  onOmitir,
  onAccion,
}: {
  paso: Paso;
  enMarcha: boolean;
  ocupado: boolean;
  onConfirmar: (fecha: string | null) => void;
  onOmitir: () => void;
  onAccion: (confirmar: boolean) => void;
}) {
  const [fecha, setFecha] = useState("");
  const espera = paso.espera_actual;
  const esperaUsuario = enMarcha && paso.estado === "esperando" && espera !== null;
  const esAccion = espera?.motivo === "accion" && espera.accion_id;
  const pideFecha = paso.tipo === "confirmacion_usuario" && esperaUsuario && !espera?.reintentar;
  const idFecha = `fecha-${paso.id}`;
  return (
    <li className="relative border-l-2 border-line pb-4 pl-5 last:pb-0">
      <span className="absolute -left-[9px] top-0.5 flex h-4 w-4 items-center justify-center rounded-full bg-paper">
        <IconoPaso paso={paso} />
      </span>
      <div className="flex flex-wrap items-baseline gap-x-2">
        <h4 className="text-[14px] font-semibold text-ink">{paso.titulo}</h4>
        <span className="text-[12px] text-muted">
          {ESTADO_PASO[paso.estado]}
          {paso.opcional ? " · opcional" : ""}
        </span>
      </div>
      {paso.descripcion && <p className="text-[13px] leading-snug text-ink-soft">{paso.descripcion}</p>}
      {espera && paso.estado === "esperando" && (
        <p className="mt-1 rounded-lg bg-grounding-uncited-bg px-2.5 py-1.5 text-[12.5px] text-ink">
          {espera.detalle || "Esperando."}
          {espera.hasta ? ` (${espera.motivo === "fecha" ? "el" : "hasta el"} ${formatDate(espera.hasta)})` : ""}
        </p>
      )}
      {paso.error && paso.estado !== "hecho" && (
        <p role="alert" className="mt-1 text-[12.5px] font-semibold text-risk-high-fg">
          {paso.error}
        </p>
      )}
      {paso.resultado?.resumen && (
        <details className="mt-1 text-[12.5px] text-ink-soft">
          <summary className="cursor-pointer text-muted">Resultado</summary>
          <p className="mt-1 whitespace-pre-wrap">{paso.resultado.resumen}</p>
          {paso.resultado.fecha && <p className="mt-1">Fecha: {formatDate(paso.resultado.fecha)}</p>}
          {paso.resultado.evidencias.length > 0 && (
            <p className="mt-1 text-muted">Fuentes: {paso.resultado.evidencias.join(" · ")}</p>
          )}
        </details>
      )}
      {paso.resultado?.borrador && (
        <details className="mt-1 text-[12.5px]" open>
          <summary className="cursor-pointer font-semibold text-ink">Borrador (no se ha enviado nada)</summary>
          <pre className="mt-1 whitespace-pre-wrap rounded-lg border border-line bg-surface p-2 font-sans text-ink">{paso.resultado.borrador}</pre>
        </details>
      )}
      {esperaUsuario && (
        <div className="mt-2 flex flex-wrap items-end gap-2">
          {esAccion ? (
            <>
              <button type="button" className={PRIMARIO} disabled={ocupado} onClick={() => onAccion(true)}>
                <Check size={14} aria-hidden /> Confirmar la acción
              </button>
              <button type="button" className={SECUNDARIO} disabled={ocupado} onClick={() => onAccion(false)}>
                <X size={14} aria-hidden /> Rechazarla
              </button>
            </>
          ) : (
            <>
              {pideFecha && (
                <div className="flex flex-col gap-1">
                  <label htmlFor={idFecha} className="text-[12px] text-muted">
                    Fecha (opcional)
                  </label>
                  <input id={idFecha} type="date" className={clsx(INPUT, "py-1")} value={fecha} onChange={(e) => setFecha(e.target.value)} />
                </div>
              )}
              <button type="button" className={PRIMARIO} disabled={ocupado} onClick={() => onConfirmar(fecha || null)}>
                {espera?.reintentar || espera?.motivo === "modulo" ? (
                  <>
                    <RotateCcw size={14} aria-hidden /> Reintentar
                  </>
                ) : (
                  <>
                    <Check size={14} aria-hidden /> Hecho
                  </>
                )}
              </button>
            </>
          )}
          <button type="button" className={SECUNDARIO} disabled={ocupado} onClick={onOmitir}>
            <SkipForward size={14} aria-hidden /> Saltar
          </button>
        </div>
      )}
    </li>
  );
}

export function TareasPanel({ open, onClose, caseId, onAccion, onCambio }: TareasPanelProps) {
  const [tareas, setTareas] = useState<ResumenTarea[] | null>(null);
  const [plantillas, setPlantillas] = useState<Plantilla[]>([]);
  const [detalle, setDetalle] = useState<TareaDetalle | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [ocupado, setOcupado] = useState(false);
  const [plantilla, setPlantilla] = useState("");
  const [objetivo, setObjetivo] = useState("");
  const [motivo, setMotivo] = useState("");
  const [cancelando, setCancelando] = useState(false);
  const tituloRef = useRef<HTMLHeadingElement | null>(null);

  const [tick, setTick] = useState(0);
  const tareaAbierta = detalle?.tarea.id ?? null;

  useEffect(() => {
    if (!open || !caseId) return;
    const control = new AbortController();
    Promise.all([getTareas(caseId, control.signal), getPlantillas(control.signal)]).then(
      ([lista, cat]) => {
        setTareas(lista.tareas);
        setPlantillas(cat.plantillas);
        setError(null);
      },
      (err: unknown) => {
        if (!control.signal.aborted) setError(mensaje(err));
      },
    );
    return () => control.abort();
  }, [open, caseId, tick]);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  useEffect(() => {
    if (tareaAbierta) tituloRef.current?.focus();
  }, [tareaAbierta]);

  if (!open) return null;

  async function ejecutar(accion: () => Promise<TareaDetalle | void>) {
    setOcupado(true);
    setError(null);
    try {
      const resultado = await accion();
      if (resultado) setDetalle(resultado);
      setTick((t) => t + 1);
      onCambio?.();
    } catch (err) {
      setError(mensaje(err));
    } finally {
      setOcupado(false);
    }
  }

  function abrir(id: string) {
    void ejecutar(() => getTarea(id));
  }

  function crear(event: FormEvent) {
    event.preventDefault();
    if (!caseId || !plantilla || objetivo.trim().length < 3) return;
    void ejecutar(async () => {
      const creada = await crearTarea({ case_id: caseId, plantilla, objetivo: objetivo.trim() });
      setObjetivo("");
      return creada;
    });
  }

  function operar(op: OperacionTarea) {
    if (detalle) void ejecutar(() => operarTarea(detalle.tarea.id, op));
  }

  const tarea = detalle?.tarea;
  const enMarcha = tarea ? ["activa", "esperando_usuario", "esperando_evento", "fallida"].includes(tarea.estado) : false;

  return (
    <div className="fixed inset-0 z-40 flex justify-end">
      <button type="button" aria-label="Cerrar las tareas" className="absolute inset-0 bg-ink/40" onClick={onClose} />
      <aside role="dialog" aria-modal="true" aria-labelledby="tareas-titulo" className="relative flex h-full w-full max-w-[560px] flex-col border-l border-line bg-paper shadow-pop">
        <header className="flex items-center justify-between border-b border-line px-5 py-4">
          <div className="min-w-0">
            <h2 id="tareas-titulo" className="flex items-center gap-2 font-serif text-[19px] font-bold text-ink">
              <ListChecks size={18} aria-hidden /> Tareas
            </h2>
            <p className="text-[12.5px] text-muted">Objetivos de varios pasos y días; nada se hace fuera de la app sin tu confirmación</p>
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
          {!caseId ? (
            <p>Las tareas se guardan en tu expediente: empieza una conversación para tener uno.</p>
          ) : tarea && detalle ? (
            <section aria-labelledby="tarea-titulo" className="space-y-3">
              <button type="button" className="inline-flex items-center gap-1 text-[13px] text-muted hover:text-ink" onClick={() => setDetalle(null)}>
                <ArrowLeft size={14} aria-hidden /> Todas las tareas
              </button>
              <div>
                <div className="flex flex-wrap items-center gap-2">
                  <h3 id="tarea-titulo" ref={tituloRef} tabIndex={-1} className="font-serif text-[17px] font-bold text-ink outline-none">
                    {tarea.titulo}
                  </h3>
                  <Insignia estado={tarea.estado} />
                  <span className="text-[12px] text-muted">{detalle.resumen.ref}</span>
                </div>
                <p className="mt-1">«{tarea.objetivo}»</p>
                <p className="text-[12px] text-muted">
                  {detalle.resumen.pasos_hechos} de {detalle.resumen.pasos_total} pasos
                  {tarea.proxima_revision ? ` · próxima revisión: ${formatDate(tarea.proxima_revision)}` : ""}
                  {tarea.vence ? ` · plazo: ${formatDate(tarea.vence)}` : ""}
                </p>
                {detalle.avisos.map((aviso) => (
                  <p key={aviso} className="mt-1 text-[12.5px] text-ink">
                    {aviso}
                  </p>
                ))}
              </div>

              <div className="flex flex-wrap gap-2">
                {tarea.estado === "propuesta" && (
                  <button type="button" className={PRIMARIO} disabled={ocupado} onClick={() => operar("aceptar")}>
                    <Play size={14} aria-hidden /> Empezar
                  </button>
                )}
                {enMarcha && tarea.estado !== "fallida" && (
                  <button type="button" className={SECUNDARIO} disabled={ocupado} onClick={() => operar("pausar")}>
                    <Pause size={14} aria-hidden /> Pausar
                  </button>
                )}
                {tarea.estado === "pausada" && (
                  <button type="button" className={PRIMARIO} disabled={ocupado} onClick={() => operar("reanudar")}>
                    <Play size={14} aria-hidden /> Reanudar
                  </button>
                )}
                {tarea.estado === "fallida" && (
                  <button type="button" className={PRIMARIO} disabled={ocupado} onClick={() => operar("reintentar")}>
                    <RotateCcw size={14} aria-hidden /> Reintentar
                  </button>
                )}
                {!["completada", "cancelada"].includes(tarea.estado) && (
                  <button type="button" className={SECUNDARIO} disabled={ocupado} onClick={() => setCancelando((v) => !v)} aria-expanded={cancelando}>
                    <X size={14} aria-hidden /> {tarea.estado === "propuesta" ? "Descartar" : "Cancelar"}
                  </button>
                )}
                {ocupado && <Loader2 size={16} className="animate-spin text-muted" aria-label="Guardando" />}
              </div>
              {cancelando && (
                <form
                  className="space-y-2 rounded-xl border border-line bg-surface px-3 py-3"
                  onSubmit={(e) => {
                    e.preventDefault();
                    setCancelando(false);
                    void ejecutar(() => cancelarTarea(tarea.id, motivo.trim() || null));
                    setMotivo("");
                  }}
                >
                  <label htmlFor="tarea-motivo" className="text-[13px] font-semibold text-ink">
                    ¿Por qué? (opcional: si lo dices, no te lo volveré a proponer mientras siga valiendo)
                  </label>
                  <input id="tarea-motivo" className={INPUT} maxLength={500} value={motivo} onChange={(e) => setMotivo(e.target.value)} />
                  <button type="submit" className={PRIMARIO} disabled={ocupado}>
                    Confirmar la cancelación
                  </button>
                </form>
              )}

              <ol aria-label="Pasos" className="ml-2 mt-2">
                {tarea.pasos.map((paso) => (
                  <PasoTimeline
                    key={paso.id}
                    paso={paso}
                    enMarcha={enMarcha}
                    ocupado={ocupado}
                    onConfirmar={(fecha) => void ejecutar(() => confirmarPaso(tarea.id, paso.id, { fecha }))}
                    onOmitir={() => void ejecutar(() => omitirPaso(tarea.id, paso.id))}
                    onAccion={(confirmar) =>
                      void ejecutar(async () => {
                        const id = paso.espera_actual?.accion_id;
                        if (id) await onAccion(id, confirmar);
                        return operarTarea(tarea.id, "avanzar");
                      })
                    }
                  />
                ))}
              </ol>

              <details className="text-[12.5px]">
                <summary className="cursor-pointer text-muted">Historial</summary>
                <ol className="mt-1 space-y-0.5">
                  {detalle.historial.map((e) => (
                    <li key={e.seq}>
                      {formatDate(e.momento)} — {EVENTO_HISTORIAL[e.evento] ?? e.evento}
                      {e.paso_id ? ` (${tarea.pasos.find((p) => p.id === e.paso_id)?.titulo ?? e.paso_id})` : ""}
                    </li>
                  ))}
                </ol>
              </details>
            </section>
          ) : (
            <>
              <section aria-labelledby="tareas-nueva" className="rounded-xl border border-line bg-surface px-4 py-3.5">
                <h3 id="tareas-nueva" className="mb-2 font-serif text-[15.5px] font-bold text-ink">
                  Nueva tarea
                </h3>
                <form className="space-y-2" onSubmit={crear}>
                  <label htmlFor="tareas-plantilla" className="text-[13px] font-semibold text-ink">
                    ¿Qué quieres conseguir?
                  </label>
                  <select id="tareas-plantilla" className={INPUT} value={plantilla} onChange={(e) => setPlantilla(e.target.value)}>
                    <option value="">Elige…</option>
                    {plantillas.map((p) => (
                      <option key={p.id} value={p.id}>
                        {p.titulo}
                      </option>
                    ))}
                  </select>
                  {plantilla && <p className="text-[12.5px]">{plantillas.find((p) => p.id === plantilla)?.descripcion}</p>}
                  <label htmlFor="tareas-objetivo" className="text-[13px] font-semibold text-ink">
                    Dilo con tus palabras
                  </label>
                  <input
                    id="tareas-objetivo"
                    className={INPUT}
                    maxLength={1000}
                    placeholder="Por ejemplo: renovar el pasaporte antes del viaje de marzo"
                    value={objetivo}
                    onChange={(e) => setObjetivo(e.target.value)}
                  />
                  <button type="submit" className={PRIMARIO} disabled={ocupado || !plantilla || objetivo.trim().length < 3}>
                    Ver el plan
                  </button>
                  <p className="text-[12px] text-muted">Verás los pasos antes de empezar. También puedes pedírselo al asistente en el chat.</p>
                </form>
              </section>

              {tareas === null ? (
                !error && (
                  <p role="status" className="flex items-center gap-2">
                    <Loader2 size={14} className="animate-spin" aria-hidden /> Cargando…
                  </p>
                )
              ) : tareas.length === 0 ? (
                <p>Aún no tienes tareas.</p>
              ) : (
                <ul aria-label="Tus tareas" className="space-y-2">
                  {tareas.map((t) => (
                    <li key={t.id}>
                      <button
                        type="button"
                        onClick={() => abrir(t.id)}
                        className="w-full rounded-xl border border-line bg-surface px-4 py-3 text-left hover:border-primary"
                      >
                        <span className="flex flex-wrap items-center gap-2">
                          <span className="font-semibold text-ink">{t.titulo}</span>
                          <Insignia estado={t.estado} />
                        </span>
                        <span className="block text-[12.5px] text-muted">
                          {t.ref} · {t.pasos_hechos}/{t.pasos_total} pasos
                          {t.proxima_revision ? ` · revisión ${formatDate(t.proxima_revision)}` : ""}
                        </span>
                        {t.esperando.length > 0 && (
                          <span className="mt-1 block text-[12.5px] text-ink">Espera: {t.esperando.map((e) => e.titulo).join(", ")}</span>
                        )}
                      </button>
                    </li>
                  ))}
                </ul>
              )}
            </>
          )}
        </div>
      </aside>
    </div>
  );
}
