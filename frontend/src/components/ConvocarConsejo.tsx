/* ui-legado: pendiente de migrar a src/ui (docs/ui/sistema-de-diseno.md) */
// The council in the chat.
//
//   · ConvocarConsejo — the card of ONE session: while it waits for the user, the estimate (cost, cap, time), the
//     members and «Confirmar / No, gracias» (nothing runs and nothing is spent until «Confirmar»); then the live
//     phases with «Cancelar»; then «Dictamen listo» with the ruling on demand.
//   · ConsejoEnHilo — under the conversation: the case's convocations waiting for the user and the sessions running
//     (the brain's `convocar_consejo` creates them), and, when the last question looks like a hard decision the brain
//     did not take to the council (the deterministic detector, no model), a quiet offer to do it.
// Data: asistente/plataforma/api/routers/consejo.py.

import clsx from "clsx";
import { ChevronDown, Loader2, ShieldCheck, UsersRound, X } from "lucide-react";
import { useEffect, useState } from "react";

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
import { useSesionConsejo } from "../shell/useSesionConsejo";
import { errorMessage } from "../lib/memoria";
import { DictamenConsejo, ProgresoConsejo } from "./DictamenConsejo";

const PRIMARIO =
  "inline-flex min-h-9 items-center gap-1.5 rounded-lg bg-primary px-3 py-1.5 text-[13px] font-semibold text-white transition hover:bg-primary-dark disabled:opacity-50 focus-visible:outline-solid focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-primary";
const SECUNDARIO =
  "inline-flex min-h-9 items-center gap-1.5 rounded-lg border border-line px-3 py-1.5 text-[13px] font-semibold text-ink-soft transition hover:border-primary hover:text-ink disabled:opacity-50 focus-visible:outline-solid focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-primary";

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
      <p role="alert" className="text-[13px] text-risk-high-fg">
        {errorCarga}
      </p>
    ) : null;
  }
  const pendiente = sesion.estado === "pendiente_confirmacion";
  const trabajando = EN_MARCHA.has(sesion.estado);
  return (
    <article aria-label="Consejo del hogar" className={clsx("rounded-xl border bg-surface px-4 py-3.5 shadow-card", pendiente ? "border-primary/60" : "border-line")}>
      <p className="flex flex-wrap items-center gap-x-2 gap-y-1 text-[11.5px] font-semibold uppercase tracking-[0.08em] text-primary-dark">
        <UsersRound size={14} aria-hidden />
        Consejo del hogar
        <span className="rounded-pill bg-line/70 px-2 py-px normal-case tracking-normal text-ink-soft">{ESTADO_LABEL[sesion.estado]}</span>
      </p>
      <h4 className="mt-1.5 text-[15px] font-bold leading-snug text-ink">
        {pendiente
          ? "¿Lo consultamos con el consejo?"
          : sesion.estado === "terminada"
            ? sesion.parcial
              ? "Dictamen parcial del consejo"
              : "Dictamen del consejo listo"
            : "Tu decisión, en el consejo"}
      </h4>
      <p className="mt-1 text-[13.5px] leading-snug text-ink-soft">«{sesion.pregunta_usuario}»</p>

      {pendiente && (
        <>
          <p className="mt-2 text-[13px] leading-snug text-ink-soft">
            Varias IA la estudian por separado, se critican sin saber de quién es cada propuesta y emiten un dictamen con el
            consenso y el desacuerdo. Trabajan con las cifras de los especialistas y sin tus datos personales.
          </p>
          {sesion.motivo && <p className="mt-1 text-[12.5px] text-muted">Por qué te lo propongo: {sesion.motivo}</p>}
          <dl className="mt-3 grid grid-cols-1 gap-x-4 gap-y-1 text-[13px] sm:grid-cols-3">
            <div>
              <dt className="text-[11.5px] uppercase tracking-[0.06em] text-muted">Coste estimado</dt>
              <dd className="font-semibold text-ink">{usd(sesion.estimacion_usd)}</dd>
            </div>
            <div>
              <dt className="text-[11.5px] uppercase tracking-[0.06em] text-muted">Tope</dt>
              <dd className="font-semibold text-ink">{usd(sesion.tope_usd)}</dd>
            </div>
            <div>
              <dt className="text-[11.5px] uppercase tracking-[0.06em] text-muted">Tiempo</dt>
              <dd className="font-semibold text-ink">unos {sesion.minutos_estimados} min</dd>
            </div>
          </dl>
          {!sesion.precios_conocidos && (
            <p className="mt-1 text-[12px] text-muted">Algún modelo no tiene precio publicado: la estimación es un máximo.</p>
          )}
          {sesion.miembros.length > 0 && (
            <p className="mt-2 text-[12.5px] text-ink-soft">
              <span className="font-semibold text-ink">Miembros:</span> {sesion.miembros.join(" · ")}
            </p>
          )}
          <p className="mt-2 flex items-start gap-1.5 text-[12px] leading-snug text-muted">
            <ShieldCheck size={13} aria-hidden className="mt-0.5 shrink-0 text-primary-dark" />
            Nunca gasta más del tope: si llega a él, para y te lo dice. Corre en segundo plano y te aviso al terminar.
          </p>
          <div className="mt-3 flex flex-wrap gap-2">
            <button type="button" disabled={ocupado} onClick={() => void decidir(() => confirmarSesion(caseId, sesion.id))} className={PRIMARIO}>
              {ocupado ? <Loader2 size={14} aria-hidden className="animate-spin" /> : <UsersRound size={14} aria-hidden />}
              Confirmar
            </button>
            <button type="button" disabled={ocupado} onClick={() => void decidir(() => rechazarSesion(caseId, sesion.id))} className={SECUNDARIO}>
              No, gracias
            </button>
          </div>
        </>
      )}

      {trabajando && (
        <>
          <ProgresoConsejo sesion={sesion} />
          <div className="mt-3">
            <button type="button" disabled={ocupado} onClick={() => void decidir(() => cancelarSesion(caseId, sesion.id))} className={SECUNDARIO}>
              <X size={14} aria-hidden />
              Cancelar
            </button>
          </div>
        </>
      )}

      {sesion.estado === "terminada" && (
        <>
          {sesion.parcial && <p className="mt-2 text-[12.5px] text-grounding-abstain-fg">Parcial: {sesion.parcial}</p>}
          {sesion.recomendacion && (
            <p className="mt-2 text-[13.5px] text-ink">
              <span className="font-semibold">Recomendación:</span> {sesion.recomendacion}
              {sesion.consenso ? <span className="text-muted"> ({sesion.consenso})</span> : null}
            </p>
          )}
          <button type="button" onClick={() => setVerDictamen((v) => !v)} aria-expanded={verDictamen} className={clsx(SECUNDARIO, "mt-3")}>
            <ChevronDown size={14} aria-hidden className={clsx("transition", verDictamen && "rotate-180")} />
            {verDictamen ? "Ocultar el dictamen" : "Ver el dictamen"}
          </button>
          {verDictamen && (
            <div className="mt-3 border-t border-line pt-3">
              <DictamenConsejo sesion={sesion} />
            </div>
          )}
        </>
      )}

      {(sesion.estado === "fallida" || sesion.estado === "cancelada") && (
        <p className="mt-2 text-[13px] text-ink-soft">{sesion.error ?? ESTADO_LABEL[sesion.estado]}</p>
      )}
      {sesion.estado === "rechazada" && <p className="mt-2 text-[13px] text-muted">De acuerdo, no lo consulto con el consejo.</p>}
      {error && (
        <p role="alert" className="mt-2 text-[13px] text-risk-high-fg">
          {error}
        </p>
      )}
    </article>
  );
}

/** The quiet offer under an answer that looks like a hard decision (no session for it yet). */
function Sugerencia({ caseId, pregunta, deteccion, onConvocada, onDescartar }: { caseId: string; pregunta: string; deteccion: Deteccion; onConvocada: (id: string) => void; onDescartar: () => void }) {
  const [ocupado, setOcupado] = useState(false);
  const [error, setError] = useState<string | null>(null);
  return (
    <aside aria-label="Propuesta de consultar al consejo" className="rounded-xl border border-dashed border-primary/50 bg-paper px-4 py-3">
      <p className="flex items-center gap-1.5 text-[13.5px] font-semibold text-ink">
        <UsersRound size={15} aria-hidden className="text-primary-dark" />
        Parece una decisión difícil
      </p>
      <p className="mt-1 text-[12.5px] leading-snug text-ink-soft">
        {deteccion.motivos.slice(0, 3).join(" · ")}. Si quieres, el consejo la estudia aparte; antes te digo cuánto costaría.
      </p>
      <div className="mt-2 flex flex-wrap gap-2">
        <button
          type="button"
          disabled={ocupado}
          className={SECUNDARIO}
          onClick={() => {
            setOcupado(true);
            setError(null);
            convocarConsejo(caseId, { pregunta, opciones: deteccion.opciones_detectadas })
              .then((s) => onConvocada(s.id))
              .catch((err: unknown) => setError(errorMessage(err)))
              .finally(() => setOcupado(false));
          }}
        >
          {ocupado && <Loader2 size={14} aria-hidden className="animate-spin" />}
          Consultarlo con el consejo
        </button>
        <button type="button" onClick={onDescartar} className="rounded-lg px-3 py-1.5 text-[13px] font-semibold text-muted hover:bg-line/60">
          No hace falta
        </button>
      </div>
      {error && (
        <p role="alert" className="mt-2 text-[12.5px] text-risk-high-fg">
          {error}
        </p>
      )}
    </aside>
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
    <section aria-label="Consejo del hogar" className="flex flex-col gap-3">
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
