// The council in the chat.
//
//   · ConvocarConsejo — the card of ONE session: while it waits for the user, the estimate (cost, cap, time), the
//     members and «Confirmar / No, gracias» (nothing runs and nothing is spent until «Confirmar»); then the live
//     phases with «Cancelar»; then «Dictamen listo» with the ruling on demand.
//   · ConsejoEnHilo — under the conversation: the case's convocations waiting for the user and the sessions running
//     (the brain's `convocar_consejo` creates them), and, when the last question looks like a hard decision the brain
//     did not take to the council (the deterministic detector, no model), a quiet offer to do it.
// Data: asistente/plataforma/api/routers/consejo.py.

//
// Design system: a session is a Box with its state as a StateLabel in the header (it sits in the conversation's
// column, between the comments); the offer is a timeline event, like GitHub's «suggested reviewers».

import { Suspense, lazy, useEffect, useState } from "react";

import {
  ESTADO_LABEL,
  EN_MARCHA,
  cancelarSesion,
  confirmarSesion,
  convocarConsejo,
  detectarDecision,
  listarSesiones,
  rechazarSesion,
  usd,
  type Deteccion,
  type ResumenSesion,
  type SesionConsejo,
} from "../apiConsejo";
import { errorMessage } from "../lib/memoria";
import { useSesionConsejo } from "../shell/useSesionConsejo";
import { Button, Caja, CargandoTexto, EventoConversacion, Flash } from "../ui";
import { ChevronDownIcon, ChevronUpIcon, PeopleIcon, ShieldCheckIcon, XIcon } from "../ui/iconos";
import { EstadoSesionLabel } from "./conversacion/EstadoSesion";

// The council's report and progress view are heavy and rarely shown: loaded on demand, out of the initial
// bundle (this component is part of every conversation).
const DictamenConsejo = lazy(() => import("./DictamenConsejo").then((m) => ({ default: m.DictamenConsejo })));
const ProgresoConsejo = lazy(() => import("./DictamenConsejo").then((m) => ({ default: m.ProgresoConsejo })));

interface ConvocarConsejoProps {
  caseId: string;
  sesionId: string;
  /** Called after the user decided (confirmed, declined, cancelled): the list above may change. */
  onCambio?: () => void;
}

/** The card of one council session in the chat. */
export function ConvocarConsejo({ caseId, sesionId, onCambio }: ConvocarConsejoProps) {
  const { sesion, error: errorCarga, recargar } = useSesionConsejo(caseId, sesionId);
  const [ocupado, setOcupado] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [verDictamen, setVerDictamen] = useState(false);

  async function decidir(accion: () => Promise<SesionConsejo>) {
    setOcupado(true);
    setError(null);
    try {
      await accion();
      recargar();
      onCambio?.();
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setOcupado(false);
    }
  }

  if (!sesion) {
    return errorCarga ? (
      <Flash variant="danger" role="alert">
        {errorCarga}
      </Flash>
    ) : null;
  }
  const pendiente = sesion.estado === "pendiente_confirmacion";
  const trabajando = EN_MARCHA.has(sesion.estado);
  const titulo = pendiente
    ? "¿Lo consultamos con el consejo?"
    : sesion.estado === "terminada"
      ? sesion.parcial
        ? "Dictamen parcial del consejo"
        : "Dictamen del consejo listo"
      : "Tu decisión, en el consejo";
  return (
    <Caja as="article" aria-label="Consejo del hogar" variante={pendiente ? "acento" : "normal"}>
      <Caja.Cabecera className="flex-wrap">
        <PeopleIcon className="shrink-0 text-fg-muted" />
        <h4 className="m-0 min-w-0 flex-1 text-sm font-semibold">
          <span className="text-fg-muted">Consejo del hogar · </span>
          {titulo}
        </h4>
        <EstadoSesionLabel estado={sesion.estado} />
      </Caja.Cabecera>
      <Caja.Cuerpo className="flex flex-col gap-3">
        <blockquote className="m-0 border-l-4 border-border-default pl-3 text-fg-default">{sesion.pregunta_usuario}</blockquote>

        {pendiente && (
          <>
            <p className="m-0">
              Varias IA la estudian por separado, se critican sin saber de quién es cada propuesta y emiten un dictamen con el
              consenso y el desacuerdo. Trabajan con las cifras de los especialistas y sin tus datos personales.
            </p>
            {sesion.motivo && <p className="m-0 text-xs text-fg-muted">Por qué te lo propongo: {sesion.motivo}</p>}
            <dl className="m-0 grid grid-cols-1 gap-x-4 gap-y-2 sm:grid-cols-3">
              <div>
                <dt className="text-xs text-fg-muted">Coste estimado</dt>
                <dd className="m-0 font-semibold">{usd(sesion.estimacion_usd)}</dd>
              </div>
              <div>
                <dt className="text-xs text-fg-muted">Tope</dt>
                <dd className="m-0 font-semibold">{usd(sesion.tope_usd)}</dd>
              </div>
              <div>
                <dt className="text-xs text-fg-muted">Tiempo</dt>
                <dd className="m-0 font-semibold">unos {sesion.minutos_estimados} min</dd>
              </div>
            </dl>
            {!sesion.precios_conocidos && <p className="m-0 text-xs text-fg-muted">Algún modelo no tiene precio publicado: la estimación es un máximo.</p>}
            {sesion.miembros.length > 0 && (
              <p className="m-0">
                <span className="font-semibold">Miembros:</span> {sesion.miembros.join(" · ")}
              </p>
            )}
            <p className="m-0 flex items-start gap-2 text-xs text-fg-muted">
              <ShieldCheckIcon size={12} className="mt-0.5 shrink-0" />
              Nunca gasta más del tope: si llega a él, para y te lo dice. Corre en segundo plano y te aviso al terminar.
            </p>
          </>
        )}

        {trabajando && (
          <Suspense fallback={<CargandoTexto lineas={2} etiqueta="Cargando el progreso del consejo…" />}>
            <ProgresoConsejo sesion={sesion} />
          </Suspense>
        )}

        {sesion.estado === "terminada" && (
          <>
            {sesion.parcial && <p className="m-0 text-fg-attention">Parcial: {sesion.parcial}</p>}
            {sesion.recomendacion && (
              <p className="m-0">
                <span className="font-semibold">Recomendación:</span> {sesion.recomendacion}
                {sesion.consenso ? <span className="text-fg-muted"> ({sesion.consenso})</span> : null}
              </p>
            )}
            {verDictamen && (
              <div className="border-t border-border-muted pt-3">
                <Suspense fallback={<CargandoTexto lineas={4} etiqueta="Cargando el dictamen…" />}>
                  <DictamenConsejo sesion={sesion} />
                </Suspense>
              </div>
            )}
          </>
        )}

        {(sesion.estado === "fallida" || sesion.estado === "cancelada") && <p className="m-0">{sesion.error ?? ESTADO_LABEL[sesion.estado]}</p>}
        {sesion.estado === "rechazada" && <p className="m-0 text-fg-muted">De acuerdo, no lo consulto con el consejo.</p>}
        {error && (
          <Flash variant="danger" role="alert">
            {error}
          </Flash>
        )}
      </Caja.Cuerpo>
      {(pendiente || trabajando || sesion.estado === "terminada") && (
        <Caja.Pie className="flex flex-wrap gap-2">
          {pendiente && (
            <>
              <Button variant="primary" leadingVisual={PeopleIcon} disabled={ocupado} loading={ocupado} onClick={() => void decidir(() => confirmarSesion(caseId, sesion.id))}>
                Confirmar
              </Button>
              <Button disabled={ocupado} onClick={() => void decidir(() => rechazarSesion(caseId, sesion.id))}>
                No, gracias
              </Button>
            </>
          )}
          {trabajando && (
            <Button leadingVisual={XIcon} disabled={ocupado} onClick={() => void decidir(() => cancelarSesion(caseId, sesion.id))}>
              Cancelar
            </Button>
          )}
          {sesion.estado === "terminada" && (
            <Button onClick={() => setVerDictamen((v) => !v)} aria-expanded={verDictamen} trailingVisual={verDictamen ? ChevronUpIcon : ChevronDownIcon}>
              {verDictamen ? "Ocultar el dictamen" : "Ver el dictamen"}
            </Button>
          )}
        </Caja.Pie>
      )}
    </Caja>
  );
}

/** The quiet offer under an answer that looks like a hard decision (no session for it yet): a timeline event. */
function Sugerencia({ caseId, pregunta, deteccion, onConvocada, onDescartar }: { caseId: string; pregunta: string; deteccion: Deteccion; onConvocada: (id: string) => void; onDescartar: () => void }) {
  const [ocupado, setOcupado] = useState(false);
  const [error, setError] = useState<string | null>(null);
  return (
    <section aria-label="Propuesta de consultar al consejo">
      <EventoConversacion icono={PeopleIcon}>
        <div className="flex flex-col gap-2 py-1">
          <p className="m-0">
            <span className="font-semibold text-fg-default">Parece una decisión difícil.</span> {deteccion.motivos.slice(0, 3).join(" · ")}. Si
            quieres, el consejo la estudia aparte; antes te digo cuánto costaría.
          </p>
          <div className="flex flex-wrap gap-2">
            <Button
              size="small"
              disabled={ocupado}
              loading={ocupado}
              onClick={() => {
                setOcupado(true);
                setError(null);
                convocarConsejo(caseId, { pregunta, opciones: deteccion.opciones_detectadas })
                  .then((s) => onConvocada(s.id))
                  .catch((err: unknown) => setError(errorMessage(err)))
                  .finally(() => setOcupado(false));
              }}
            >
              Consultarlo con el consejo
            </Button>
            <Button size="small" variant="invisible" onClick={onDescartar}>
              No hace falta
            </Button>
          </div>
          {error && (
            <Flash variant="danger" role="alert">
              {error}
            </Flash>
          )}
        </div>
      </EventoConversacion>
    </section>
  );
}

interface ConsejoEnHiloProps {
  caseId: string | null;
  /** The number of finished turns: the list is read again after each one. */
  turnos: number;
  /** The user's last question (for the detector's offer). */
  ultimaPregunta?: string;
  /** A turn is running: nothing is read meanwhile. */
  ocupado: boolean;
}

const DESCARTADAS = "consejo_sugerencias_descartadas_v1";

function descartadas(): Set<string> {
  try {
    return new Set(JSON.parse(sessionStorage.getItem(DESCARTADAS) ?? "[]") as string[]);
  } catch {
    return new Set();
  }
}

/** The council's cards under the conversation (see the module comment). */
export function ConsejoEnHilo({ caseId, turnos, ultimaPregunta, ocupado }: ConsejoEnHiloProps) {
  const [sesiones, setSesiones] = useState<ResumenSesion[]>([]);
  const [seguidas, setSeguidas] = useState<Set<string>>(new Set());
  const [deteccion, setDeteccion] = useState<{ pregunta: string; deteccion: Deteccion } | null>(null);
  const [tick, setTick] = useState(0);
  const [ocultas, setOcultas] = useState<Set<string>>(descartadas);

  useEffect(() => {
    if (!caseId || ocupado) return;
    const controller = new AbortController();
    listarSesiones(caseId, controller.signal).then(
      (lista) => {
        setSesiones(lista.sesiones);
        const vivas = lista.sesiones.filter((s) => s.estado === "pendiente_confirmacion" || EN_MARCHA.has(s.estado)).map((s) => s.id);
        if (vivas.length) setSeguidas((prev) => new Set([...prev, ...vivas]));
        const pregunta = ultimaPregunta?.trim();
        const yaConvocada = pregunta ? lista.sesiones.some((s) => s.pregunta_usuario.trim() === pregunta) : true;
        if (!lista.disponible || !pregunta || yaConvocada || vivas.length > 0) {
          setDeteccion(null);
          return;
        }
        detectarDecision(caseId, pregunta, controller.signal).then(
          (d) => setDeteccion(d.dificil ? { pregunta, deteccion: d } : null),
          () => setDeteccion(null),
        );
      },
      () => undefined, // the council is optional: the chat works without it
    );
    return () => controller.abort();
  }, [caseId, turnos, ultimaPregunta, ocupado, tick]);

  if (!caseId) return null;
  const visibles = sesiones.filter((s) => seguidas.has(s.id));
  const sugerencia = deteccion && !ocultas.has(deteccion.pregunta) ? deteccion : null;
  if (visibles.length === 0 && !sugerencia) return null;
  return (
    <section aria-label="Consejo del hogar" className="flex flex-col gap-4">
      {visibles.map((s) => (
        <ConvocarConsejo key={s.id} caseId={caseId} sesionId={s.id} onCambio={() => setTick((n) => n + 1)} />
      ))}
      {sugerencia && (
        <Sugerencia
          caseId={caseId}
          pregunta={sugerencia.pregunta}
          deteccion={sugerencia.deteccion}
          onConvocada={(id) => {
            setSeguidas((prev) => new Set([...prev, id]));
            setDeteccion(null);
            setTick((n) => n + 1);
          }}
          onDescartar={() => {
            const nuevas = new Set([...ocultas, sugerencia.pregunta]);
            setOcultas(nuevas);
            try {
              sessionStorage.setItem(DESCARTADAS, JSON.stringify([...nuevas].slice(-50)));
            } catch {
              // private mode: the offer simply comes back next time
            }
          }}
        />
      )}
    </section>
  );
}
