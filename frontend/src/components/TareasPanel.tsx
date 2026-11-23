// «Tareas»: the user's long-running tasks (renew the passport, prepare the tax return, change the electricity
// tariff…). A list, and for each task the timeline of its steps with what each one produced, what it waits for
// and the buttons the user decides with: start a proposal, mark a step done (with a date when it asks for one),
// skip it, retry, pause, resume, cancel (with a reason, which is remembered). A pending ACTION is confirmed or
// rejected with the same call as in «Módulos» (`onAccion`): this panel never runs anything outside the app.
//
// Migrated to the design system (docs/ui/sistema-de-diseno.md). It looks like GitHub's issues and Actions: the
// list is an issue list (a StateLabel per task), a task is a workflow run (progress bar, one row per step with
// its status icon) and its history a Timeline. The shell shows it as a page (registro.ts: presentacion "pagina").
//
// Self-contained: it reads and writes only through apiTareas.ts; the shell passes the case and the action
// handler (paneles/tareas.tsx).

import { type FormEvent, useEffect, useId, useRef, useState } from "react";

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
import { AvisoError, Blankslate, Button, Caja, CargandoLista, CounterLabel, Flash, FormControl, Heading, Link, ProgressBar, Select, StateLabel, TextInput, Timeline } from "../ui";
import {
  ArrowLeftIcon,
  CheckCircleFillIcon,
  CheckIcon,
  CircleIcon,
  ClockIcon,
  HourglassIcon,
  PlayIcon,
  SkipIcon,
  SyncIcon,
  TasklistIcon,
  XCircleFillIcon,
  XIcon,
} from "../ui/iconos";

interface TareasPanelProps {
  open: boolean;
  caseId: string | null;
  /** Confirm (true) or reject (false) a pending action: the same handler as «Módulos». */
  onAccion: (accionId: string, confirmar: boolean) => Promise<void>;
  /** Something changed that other panels show (notices, pending actions). */
  onCambio?: () => void;
}

type EstadoLabel = Parameters<typeof StateLabel>[0]["status"];

/** Each task state as GitHub would show it: open (green), queued (amber), merged (done), closed… */
const ESTADO_VISUAL: Record<EstadoTarea, EstadoLabel> = {
  propuesta: "draft",
  activa: "issueOpened",
  pausada: "unavailable",
  esperando_usuario: "pullQueued",
  esperando_evento: "pullQueued",
  completada: "pullMerged",
  cancelada: "issueClosedNotPlanned",
  fallida: "pullClosed",
};

const EN_MARCHA: EstadoTarea[] = ["activa", "esperando_usuario", "esperando_evento", "fallida"];

function mensaje(err: unknown): string {
  return err instanceof ApiError ? err.message : "No se ha podido completar. Inténtalo de nuevo.";
}

function Estado({ estado }: { estado: EstadoTarea }) {
  return (
    <StateLabel status={ESTADO_VISUAL[estado]} size="small">
      {ESTADO_TAREA[estado]}
    </StateLabel>
  );
}

/** The step's status icon, as in a workflow run. */
function IconoPaso({ paso }: { paso: Paso }) {
  switch (paso.estado) {
    case "hecho":
      return <CheckCircleFillIcon className="text-fg-success" />;
    case "omitido":
      return <SkipIcon className="text-fg-muted" />;
    case "fallido":
      return <XCircleFillIcon className="text-fg-danger" />;
    case "esperando":
      return paso.espera_actual?.motivo === "fecha" ? <ClockIcon className="text-fg-attention" /> : <HourglassIcon className="text-fg-attention" />;
    case "cancelado":
      return <XIcon className="text-fg-muted" />;
    default:
      return <CircleIcon className="text-fg-muted" />;
  }
}

function PasoFila({
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
  const reintentar = espera?.reintentar || espera?.motivo === "modulo";
  return (
    <Caja.Fila aria-label={paso.titulo} className="flex gap-3">
      <span className="mt-0.5 shrink-0">
        <IconoPaso paso={paso} />
      </span>
      <div className="flex min-w-0 flex-1 flex-col gap-1">
        <div className="flex flex-wrap items-baseline gap-x-2">
          <h4 className="m-0 text-sm font-semibold">{paso.titulo}</h4>
          <span className="text-xs text-fg-muted">
            {ESTADO_PASO[paso.estado]}
            {paso.opcional ? " · opcional" : ""}
          </span>
        </div>
        {paso.descripcion && <p className="m-0 text-fg-muted">{paso.descripcion}</p>}
        {espera && paso.estado === "esperando" && (espera.detalle !== paso.descripcion || espera.hasta) && (
          <Flash variant="warning">
            {espera.detalle || "Esperando."}
            {espera.hasta ? ` (${espera.motivo === "fecha" ? "el" : "hasta el"} ${formatDate(espera.hasta)})` : ""}
          </Flash>
        )}
        {paso.error && paso.estado !== "hecho" && (
          <Flash variant="danger" role="alert">
            {paso.error}
          </Flash>
        )}
        {paso.resultado?.resumen && (
          <details className="text-xs">
            <summary className="cursor-pointer text-fg-muted">Resultado</summary>
            <p className="m-0 mt-1 whitespace-pre-wrap">{paso.resultado.resumen}</p>
            {paso.resultado.fecha && <p className="m-0 mt-1">Fecha: {formatDate(paso.resultado.fecha)}</p>}
            {paso.resultado.evidencias.length > 0 && <p className="m-0 mt-1 text-fg-muted">Fuentes: {paso.resultado.evidencias.join(" · ")}</p>}
          </details>
        )}
        {paso.resultado?.borrador && (
          <details className="text-xs" open>
            <summary className="cursor-pointer font-semibold">Borrador (no se ha enviado nada)</summary>
            <pre className="m-0 mt-1 whitespace-pre-wrap rounded-medium border border-border-default bg-canvas-muted p-2 font-sans">{paso.resultado.borrador}</pre>
          </details>
        )}
        {esperaUsuario && (
          <div className="mt-1 flex flex-wrap items-end gap-2">
            {esAccion ? (
              <>
                <Button size="small" variant="primary" leadingVisual={CheckIcon} disabled={ocupado} onClick={() => onAccion(true)}>
                  Confirmar la acción
                </Button>
                <Button size="small" leadingVisual={XIcon} disabled={ocupado} onClick={() => onAccion(false)}>
                  Rechazarla
                </Button>
              </>
            ) : (
              <>
                {pideFecha && (
                  <FormControl>
                    <FormControl.Label>Fecha (opcional)</FormControl.Label>
                    <TextInput size="small" type="date" value={fecha} onChange={(e) => setFecha(e.target.value)} />
                  </FormControl>
                )}
                <Button
                  size="small"
                  variant="primary"
                  leadingVisual={reintentar ? SyncIcon : CheckIcon}
                  disabled={ocupado}
                  onClick={() => onConfirmar(fecha || null)}
                >
                  {reintentar ? "Reintentar" : "Hecho"}
                </Button>
              </>
            )}
            <Button size="small" variant="invisible" leadingVisual={SkipIcon} disabled={ocupado} onClick={onOmitir}>
              Saltar
            </Button>
          </div>
        )}
      </div>
    </Caja.Fila>
  );
}

function Detalle({
  detalle,
  ocupado,
  onVolver,
  onOperar,
  onCancelar,
  onConfirmar,
  onOmitir,
  onAccion,
}: {
  detalle: TareaDetalle;
  ocupado: boolean;
  onVolver: () => void;
  onOperar: (op: OperacionTarea) => void;
  onCancelar: (motivo: string | null) => void;
  onConfirmar: (paso: string, fecha: string | null) => void;
  onOmitir: (paso: string) => void;
  onAccion: (paso: Paso, confirmar: boolean) => void;
}) {
  const tarea = detalle.tarea;
  const resumen = detalle.resumen;
  const enMarcha = EN_MARCHA.includes(tarea.estado);
  const [cancelando, setCancelando] = useState(false);
  const [motivo, setMotivo] = useState("");
  const tituloRef = useRef<HTMLHeadingElement | null>(null);
  const idTitulo = useId();
  const progreso = resumen.pasos_total > 0 ? Math.round((resumen.pasos_hechos / resumen.pasos_total) * 100) : 0;

  useEffect(() => {
    tituloRef.current?.focus();
  }, [tarea.id]);

  return (
    <section aria-labelledby={idTitulo} className="flex flex-col gap-4">
      <div>
        <Button size="small" variant="invisible" leadingVisual={ArrowLeftIcon} onClick={onVolver}>
          Todas las tareas
        </Button>
      </div>

      <div className="flex flex-col gap-2 border-b border-border-default pb-4">
        <Heading as="h3" variant="medium" id={idTitulo} ref={tituloRef} tabIndex={-1} className="outline-none">
          {tarea.titulo} <span className="font-normal text-fg-muted">{resumen.ref}</span>
        </Heading>
        <div className="flex flex-wrap items-center gap-2 text-fg-muted">
          <Estado estado={tarea.estado} />
          <span>«{tarea.objetivo}»</span>
        </div>
        {detalle.avisos.map((aviso) => (
          <Flash key={aviso}>{aviso}</Flash>
        ))}
      </div>

      <div className="flex flex-wrap items-center gap-2">
        {tarea.estado === "propuesta" && (
          <Button variant="primary" leadingVisual={PlayIcon} disabled={ocupado} onClick={() => onOperar("aceptar")}>
            Empezar
          </Button>
        )}
        {enMarcha && tarea.estado !== "fallida" && (
          <Button disabled={ocupado} onClick={() => onOperar("pausar")}>
            Pausar
          </Button>
        )}
        {tarea.estado === "pausada" && (
          <Button variant="primary" leadingVisual={PlayIcon} disabled={ocupado} onClick={() => onOperar("reanudar")}>
            Reanudar
          </Button>
        )}
        {tarea.estado === "fallida" && (
          <Button variant="primary" leadingVisual={SyncIcon} disabled={ocupado} onClick={() => onOperar("reintentar")}>
            Reintentar
          </Button>
        )}
        {!["completada", "cancelada"].includes(tarea.estado) && (
          <Button variant="danger" disabled={ocupado} onClick={() => setCancelando((v) => !v)} aria-expanded={cancelando}>
            {tarea.estado === "propuesta" ? "Descartar" : "Cancelar"}
          </Button>
        )}
        {ocupado && (
          <span role="status" className="text-xs text-fg-muted">
            Guardando…
          </span>
        )}
      </div>

      {cancelando && (
        <Caja as="section" aria-label="Cancelar la tarea" variante="peligro">
          <Caja.Cuerpo>
            <form
              className="flex flex-col gap-3"
              onSubmit={(e) => {
                e.preventDefault();
                setCancelando(false);
                onCancelar(motivo.trim() || null);
                setMotivo("");
              }}
            >
              <FormControl>
                <FormControl.Label>¿Por qué?</FormControl.Label>
                <TextInput block maxLength={500} value={motivo} onChange={(e) => setMotivo(e.target.value)} />
                <FormControl.Caption>Opcional: si lo dices, no te lo volveré a proponer mientras siga valiendo.</FormControl.Caption>
              </FormControl>
              <div>
                <Button type="submit" variant="danger" disabled={ocupado}>
                  Confirmar la cancelación
                </Button>
              </div>
            </form>
          </Caja.Cuerpo>
        </Caja>
      )}

      <Caja as="section" aria-label="Pasos">
        <Caja.Cabecera
          titulo="Pasos"
          nivel={4}
          acciones={
            <span className="text-xs text-fg-muted">
              {resumen.pasos_hechos} de {resumen.pasos_total}
            </span>
          }
        />
        <div className="px-4 pt-3">
          <ProgressBar progress={progreso} aria-label={`${resumen.pasos_hechos} de ${resumen.pasos_total} pasos hechos`} barSize="small" />
          <p className="m-0 mt-2 text-xs text-fg-muted">
            {tarea.proxima_revision && tarea.estado === "esperando_evento" ? `Próxima revisión: ${formatDate(tarea.proxima_revision)}` : ""}
            {tarea.proxima_revision && tarea.estado === "esperando_evento" && tarea.vence ? " · " : ""}
            {tarea.vence ? `Plazo: ${formatDate(tarea.vence)}` : ""}
          </p>
        </div>
        <Caja.Lista>
          {tarea.pasos.map((paso) => (
            <PasoFila
              key={paso.id}
              paso={paso}
              enMarcha={enMarcha}
              ocupado={ocupado}
              onConfirmar={(fecha) => onConfirmar(paso.id, fecha)}
              onOmitir={() => onOmitir(paso.id)}
              onAccion={(confirmar) => onAccion(paso, confirmar)}
            />
          ))}
        </Caja.Lista>
      </Caja>

      {detalle.historial.length > 0 && (
        <section aria-label="Historial">
          <Heading as="h4" variant="small" className="mb-2">
            Historial
          </Heading>
          <Timeline clipSidebar>
            {detalle.historial.map((e) => (
              <Timeline.Item key={e.seq} condensed>
                <Timeline.Badge>
                  <TasklistIcon />
                </Timeline.Badge>
                <Timeline.Body>
                  {EVENTO_HISTORIAL[e.evento] ?? e.evento}
                  {e.paso_id ? ` (${tarea.pasos.find((p) => p.id === e.paso_id)?.titulo ?? e.paso_id})` : ""}
                  <span className="text-fg-muted"> · {formatDate(e.momento)}</span>
                </Timeline.Body>
              </Timeline.Item>
            ))}
          </Timeline>
        </section>
      )}
    </section>
  );
}

function NuevaTarea({ plantillas, ocupado, onCrear }: { plantillas: Plantilla[]; ocupado: boolean; onCrear: (plantilla: string, objetivo: string) => void }) {
  const [plantilla, setPlantilla] = useState("");
  const [objetivo, setObjetivo] = useState("");
  const elegida = plantillas.find((p) => p.id === plantilla);
  const valido = Boolean(plantilla) && objetivo.trim().length >= 3;
  function enviar(event: FormEvent) {
    event.preventDefault();
    if (!valido) return;
    onCrear(plantilla, objetivo.trim());
    setObjetivo("");
  }
  return (
    <Caja as="section" aria-label="Nueva tarea">
      <Caja.Cabecera titulo="Nueva tarea" />
      <Caja.Cuerpo>
        <form className="flex flex-col gap-3" onSubmit={enviar}>
          <FormControl>
            <FormControl.Label>¿Qué quieres conseguir?</FormControl.Label>
            <Select block value={plantilla} onChange={(e) => setPlantilla(e.target.value)}>
              <Select.Option value="">Elige…</Select.Option>
              {plantillas.map((p) => (
                <Select.Option key={p.id} value={p.id}>
                  {p.titulo}
                </Select.Option>
              ))}
            </Select>
            {elegida && <FormControl.Caption>{elegida.descripcion}</FormControl.Caption>}
          </FormControl>
          <FormControl>
            <FormControl.Label>Dilo con tus palabras</FormControl.Label>
            <TextInput
              block
              maxLength={1000}
              placeholder="Por ejemplo: renovar el pasaporte antes del viaje de marzo"
              value={objetivo}
              onChange={(e) => setObjetivo(e.target.value)}
            />
            <FormControl.Caption>Verás los pasos antes de empezar. También puedes pedírselo al asistente en el chat.</FormControl.Caption>
          </FormControl>
          <div>
            <Button type="submit" variant="primary" disabled={ocupado || !valido}>
              Ver el plan
            </Button>
          </div>
        </form>
      </Caja.Cuerpo>
    </Caja>
  );
}

function ListaTareas({ tareas, onAbrir }: { tareas: ResumenTarea[]; onAbrir: (id: string) => void }) {
  return (
    <Caja as="section" aria-label="Tus tareas">
      <Caja.Cabecera titulo="Tus tareas" acciones={<CounterLabel>{tareas.length}</CounterLabel>} />
      <Caja.Lista>
        {tareas.map((t) => (
          <Caja.Fila key={t.id} className="flex flex-col gap-1">
            <div className="flex flex-wrap items-center gap-2">
              <Estado estado={t.estado} />
              <Link as="button" type="button" className="text-left font-semibold" onClick={() => onAbrir(t.id)}>
                {t.titulo}
              </Link>
            </div>
            <span className="text-xs text-fg-muted">
              {t.ref} · {t.pasos_hechos} de {t.pasos_total} pasos
              {t.proxima_revision && t.estado === "esperando_evento" ? ` · revisión ${formatDate(t.proxima_revision)}` : ""}
            </span>
            {t.esperando.length > 0 && <span className="text-xs">Espera: {t.esperando.map((e) => e.titulo).join(", ")}</span>}
          </Caja.Fila>
        ))}
      </Caja.Lista>
    </Caja>
  );
}

export function TareasPanel({ open, caseId, onAccion, onCambio }: TareasPanelProps) {
  const [tareas, setTareas] = useState<ResumenTarea[] | null>(null);
  const [plantillas, setPlantillas] = useState<Plantilla[]>([]);
  const [detalle, setDetalle] = useState<TareaDetalle | null>(null);
  const [error, setError] = useState<string | null>(null);
  // Loading the tasks failed: it can be retried (an error acting on one cannot).
  const [errorCarga, setErrorCarga] = useState<string | null>(null);
  const [ocupado, setOcupado] = useState(false);
  const [tick, setTick] = useState(0);

  useEffect(() => {
    if (!open || !caseId) return;
    const control = new AbortController();
    Promise.all([getTareas(caseId, control.signal), getPlantillas(control.signal)]).then(
      ([lista, cat]) => {
        setTareas(lista.tareas);
        setPlantillas(cat.plantillas);
        setErrorCarga(null);
      },
      (err: unknown) => {
        if (!control.signal.aborted) setErrorCarga(mensaje(err));
      },
    );
    return () => control.abort();
  }, [open, caseId, tick]);

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

  if (!caseId) {
    return (
      <Blankslate>
        <Blankslate.Visual>
          <TasklistIcon size={24} />
        </Blankslate.Visual>
        <Blankslate.Heading as="h3">Aún no hay expediente</Blankslate.Heading>
        <Blankslate.Description>Las tareas se guardan en tu expediente: empieza una conversación para tener uno.</Blankslate.Description>
      </Blankslate>
    );
  }

  return (
    <div className="flex flex-col gap-4">
      {errorCarga !== null && <AvisoError frase="No he podido cargar tus tareas." detalle={errorCarga} onReintentar={() => setTick((t) => t + 1)} />}
      {error && <AvisoError frase="No he podido completar lo que pediste." detalle={error} />}
      {detalle ? (
        <Detalle
          detalle={detalle}
          ocupado={ocupado}
          onVolver={() => setDetalle(null)}
          onOperar={(op) => void ejecutar(() => operarTarea(detalle.tarea.id, op))}
          onCancelar={(motivo) => void ejecutar(() => cancelarTarea(detalle.tarea.id, motivo))}
          onConfirmar={(paso, fecha) => void ejecutar(() => confirmarPaso(detalle.tarea.id, paso, { fecha }))}
          onOmitir={(paso) => void ejecutar(() => omitirPaso(detalle.tarea.id, paso))}
          onAccion={(paso, confirmar) =>
            void ejecutar(async () => {
              const id = paso.espera_actual?.accion_id;
              if (id) await onAccion(id, confirmar);
              return operarTarea(detalle.tarea.id, "avanzar");
            })
          }
        />
      ) : (
        <>
          <NuevaTarea
            plantillas={plantillas}
            ocupado={ocupado}
            onCrear={(plantilla, objetivo) => void ejecutar(() => crearTarea({ case_id: caseId, plantilla, objetivo }))}
          />
          {tareas === null ? (
            !error && <CargandoLista filas={2} etiqueta="Cargando tus tareas" />
          ) : tareas.length === 0 ? (
            <Blankslate narrow>
              <Blankslate.Visual>
                <TasklistIcon size={24} />
              </Blankslate.Visual>
              <Blankslate.Heading as="h3">Aún no tienes tareas</Blankslate.Heading>
              <Blankslate.Description>Elige arriba qué quieres conseguir y verás el plan antes de empezar.</Blankslate.Description>
            </Blankslate>
          ) : (
            <ListaTareas tareas={tareas} onAbrir={(id) => void ejecutar(() => getTarea(id))} />
          )}
        </>
      )}
    </div>
  );
}
