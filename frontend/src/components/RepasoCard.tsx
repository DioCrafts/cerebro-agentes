/* ui-legado: pendiente de migrar a src/ui (docs/ui/sistema-de-diseno.md) */
// The brain's weekly (or daily) digest in the inbox: what matters now, in the order code ranked it, why, where
// each thing comes from, and what it proposes. Every proposal is answered through its OWN existing endpoint
// (pending actions, tasks, mail proposals): this card never acts by itself, and an action that reaches outside the
// app asks once more before it is confirmed. When the structured digest is not available (an older digest, or the
// brain was off), the notification's text is shown as before.

import { CalendarClock, Check, ChevronDown, ListChecks, MessageCircleQuestion, ShieldCheck, X } from "lucide-react";
import clsx from "clsx";
import { useEffect, useId, useState } from "react";

import { ApiError, getRepaso, type AdvisorNotification, type PropuestaRepaso, type PuntoRepaso, type Repaso } from "../api";
import { decidirPropuesta } from "../apiCorreo";
import { confirmarAccion, rechazarAccion } from "../apiModulos";
import { cancelarTarea, confirmarPaso, crearTarea, operarTarea } from "../apiTareas";
import { errorMessage } from "../lib/memoria";
import { formatDate, relativeTime } from "../lib/presentation";
import { RichText } from "./RichText";

interface RepasoCardProps {
  notification: AdvisorNotification;
  caseId: string;
  onMarkRead: (id: string) => void;
  onRefresh: () => void;
  onAsk: (question: string) => void;
}

type Carga = { estado: "cargando" } | { estado: "listo"; repaso: Repaso } | { estado: "sin_detalle" };

const BOTON = "inline-flex min-h-8 items-center gap-1 rounded-lg px-2.5 py-1 text-[12.5px] font-semibold transition disabled:opacity-50";
const PRIMARIO = clsx(BOTON, "bg-primary text-white hover:bg-primary-dark");
const SECUNDARIO = clsx(BOTON, "border border-line text-ink-soft hover:border-primary hover:text-ink");

export function RepasoCard({ notification: n, caseId, onMarkRead, onRefresh, onAsk }: RepasoCardProps) {
  const [carga, setCarga] = useState<Carga>({ estado: "cargando" });
  const unread = !n.read_at;

  useEffect(() => {
    const controller = new AbortController();
    getRepaso({ caseId, notificationId: n.id, signal: controller.signal }).then(
      (repaso) => setCarga({ estado: "listo", repaso }),
      () => {
        // 404 (no structured digest) or any error: the notification's own text is enough.
        if (!controller.signal.aborted) setCarga({ estado: "sin_detalle" });
      },
    );
    return () => controller.abort();
  }, [caseId, n.id]);

  const repaso = carga.estado === "listo" ? carga.repaso : null;
  return (
    <article
      aria-label={n.title}
      className={clsx(
        "rounded-xl border bg-surface px-4 py-3.5 shadow-card",
        n.severity === "urgente" ? "border-risk-high-fg/40" : unread ? "border-primary/50" : "border-line",
      )}
    >
      <p className="flex flex-wrap items-center gap-x-2 gap-y-1 text-[11.5px] font-semibold uppercase tracking-[0.08em] text-muted">
        <ListChecks size={14} aria-hidden className={n.severity === "urgente" ? "text-risk-high-fg" : "text-primary-dark"} />
        {repaso?.frecuencia === "diaria" ? "Repaso del día" : "Repaso de la semana"}
        {n.severity === "urgente" && <span className="rounded-pill bg-risk-high-bg px-2 py-px normal-case tracking-normal text-risk-high-fg">Urgente</span>}
        {unread && <span className="rounded-pill bg-primary px-2 py-px normal-case tracking-normal text-white">Nuevo</span>}
        <span className="ml-auto font-normal normal-case tracking-normal">{relativeTime(Date.parse(n.created_at))}</span>
      </p>
      <h4 className="mt-1.5 text-[15px] font-bold leading-snug text-ink">{n.title}</h4>

      {carga.estado === "cargando" && (
        <p role="status" className="mt-2 text-[13px] text-muted">
          Cargando el repaso…
        </p>
      )}

      {repaso ? (
        <>
          <p className="mt-2 text-[14.5px] font-semibold leading-snug text-ink">{repaso.titular}</p>
          <ol className="mt-3 space-y-3" aria-label="Lo que importa, de más a menos">
            {repaso.puntos.map((punto, i) => (
              <Punto key={punto.id} punto={punto} numero={i + 1} caseId={caseId} onChanged={onRefresh} />
            ))}
          </ol>
          {repaso.restantes > 0 && (
            <p className="mt-2 text-[12.5px] text-muted">
              Y {repaso.restantes} aviso{repaso.restantes === 1 ? "" : "s"} más en tu bandeja.
            </p>
          )}
          <p className="mt-3 flex items-start gap-1.5 text-[11.5px] leading-snug text-muted">
            <ShieldCheck size={13} aria-hidden className="mt-px shrink-0" />
            <span>
              No he hecho nada por mi cuenta: cada propuesta espera a que la confirmes. Cada importe es el de su módulo; no se suman entre sí.
              {repaso.origen === "determinista" && " Este repaso está redactado sin el modelo, solo con los datos."}
            </span>
          </p>
        </>
      ) : (
        carga.estado === "sin_detalle" && (
          <div className="mt-2 text-[13.5px] [&_div]:text-[13.5px] [&_div]:leading-snug">
            <RichText text={n.body} />
          </div>
        )
      )}

      <div className="mt-3 flex flex-wrap gap-2">
        <button
          type="button"
          onClick={() => onAsk(`Sobre mi repaso «${n.title}»: ¿por dónde empiezo y qué me recomiendas hacer?`)}
          className={SECUNDARIO}
        >
          <MessageCircleQuestion size={13} aria-hidden />
          Preguntar al asesor
        </button>
        {unread && (
          <button type="button" onClick={() => onMarkRead(n.id)} className={SECUNDARIO} aria-label="Ya está: marcar el repaso como leído">
            <Check size={13} aria-hidden />
            Ya está
          </button>
        )}
      </div>
    </article>
  );
}

function Punto({ punto, numero, caseId, onChanged }: { punto: PuntoRepaso; numero: number; caseId: string; onChanged: () => void }) {
  const [abierto, setAbierto] = useState(false);
  const detalleId = useId();
  const vence = formatDate(punto.vence);
  return (
    <li className="rounded-lg border border-line/80 bg-paper px-3 py-2.5">
      <div className="flex items-start gap-2.5">
        <span
          aria-hidden
          className={clsx(
            "mt-0.5 flex h-6 w-6 shrink-0 items-center justify-center rounded-full text-[12px] font-bold",
            punto.urgente ? "bg-risk-high-bg text-risk-high-fg" : "bg-line/70 text-ink",
          )}
        >
          {numero}
        </span>
        <div className="min-w-0 flex-1">
          <p className="text-[14px] font-bold leading-snug text-ink">
            {punto.titulo}
            {punto.urgente && <span className="ml-2 rounded-pill bg-risk-high-bg px-2 py-px align-middle text-[11px] font-semibold text-risk-high-fg">Urgente</span>}
          </p>
          {punto.texto && <p className="mt-1 text-[13.5px] leading-snug text-ink">{punto.texto}</p>}

          <ul className="mt-1.5 flex flex-wrap gap-1.5" aria-label="De dónde sale">
            {vence && (
              <li className="inline-flex items-center gap-1 rounded-pill bg-grounding-abstain-bg px-2 py-0.5 text-[11.5px] font-semibold text-grounding-abstain-fg">
                <CalendarClock size={12} aria-hidden />
                hasta el {vence}
              </li>
            )}
            {punto.fuentes.slice(0, 4).map((f) => (
              <li key={`${f.tipo}:${f.id}`} className="rounded-pill bg-line/70 px-2 py-0.5 text-[11.5px] text-ink-soft" title={f.titulo}>
                {f.nombre_modulo ?? f.modulo ?? "Aviso"}
                {f.fecha ? ` · ${formatDate(f.fecha)}` : ""}
              </li>
            ))}
          </ul>

          <button
            type="button"
            aria-expanded={abierto}
            aria-controls={detalleId}
            onClick={() => setAbierto((v) => !v)}
            className="mt-1.5 inline-flex items-center gap-1 text-[12.5px] font-semibold text-primary-dark hover:underline"
          >
            <ChevronDown size={13} aria-hidden className={clsx("transition", abierto && "rotate-180")} />
            ¿Por qué?
          </button>
          {abierto && (
            <div id={detalleId} className="mt-1 rounded-md bg-line/40 px-2.5 py-2 text-[12.5px] leading-snug text-ink-soft">
              {punto.por_que && <p>{punto.por_que}</p>}
              {punto.motivos.length > 0 && (
                <ul className="mt-1 list-disc space-y-0.5 pl-4">
                  {punto.motivos.map((m) => (
                    <li key={m}>{m}</li>
                  ))}
                </ul>
              )}
              {punto.fuentes.length > 0 && (
                <p className="mt-1 text-muted">Fuentes: {punto.fuentes.map((f) => f.titulo).join(" · ")}</p>
              )}
            </div>
          )}

          {punto.propuestas.map((p) => (
            <Propuesta key={p.id} propuesta={p} caseId={caseId} onChanged={onChanged} />
          ))}
        </div>
      </div>
    </li>
  );
}

type Paso = "inicio" | "confirmando" | "enviando" | "hecho" | "creada";

function Propuesta({ propuesta: p, caseId, onChanged }: { propuesta: PropuestaRepaso; caseId: string; onChanged: () => void }) {
  const [paso, setPaso] = useState<Paso>("inicio");
  const [mensaje, setMensaje] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [tareaCreada, setTareaCreada] = useState<string | null>(null);

  async function ejecutar(accion: () => Promise<unknown>, exito: string, siguiente: Paso = "hecho") {
    setPaso("enviando");
    setError(null);
    try {
      await accion();
      setMensaje(exito);
      setPaso(siguiente);
      onChanged();
    } catch (err) {
      // 409: decided somewhere else meanwhile — say so rather than an error.
      const decidida = err instanceof ApiError && err.status === 409;
      setError(decidida ? "Esto ya se decidió en otro sitio." : `No se ha podido hacer. ${errorMessage(err)}`);
      setPaso(decidida ? "hecho" : "inicio");
    }
  }

  if (p.estado === "decidida" && paso === "inicio") {
    return <p className="mt-2 text-[12.5px] text-muted">Propuesta ya decidida: {p.etiqueta.toLowerCase()}.</p>;
  }
  const ocupado = paso === "enviando";
  return (
    <div className="mt-2 rounded-md border border-primary/30 bg-surface px-2.5 py-2">
      <p className="text-[12.5px] leading-snug text-ink">
        <span className="font-semibold">Te propongo: </span>
        {p.resumen}
      </p>
      {paso !== "hecho" && (
        <div className="mt-2 flex flex-wrap gap-1.5" role="group" aria-label={`Responder a la propuesta: ${p.etiqueta}`}>
          {p.tipo === "accion" && paso !== "confirmando" && (
            <>
              <button type="button" className={PRIMARIO} disabled={ocupado} onClick={() => setPaso("confirmando")}>
                {p.etiqueta}
              </button>
              <button type="button" className={SECUNDARIO} disabled={ocupado} onClick={() => ejecutar(() => rechazarAccion(p.ref), "Descartada: no se hará.")}>
                <X size={13} aria-hidden />
                Descartar
              </button>
            </>
          )}
          {p.tipo === "accion" && paso === "confirmando" && (
            <div role="alertdialog" aria-label="Confirmar la acción" className="w-full">
              <p className="text-[12.5px] font-semibold text-ink">Esto tiene efectos fuera de la app. ¿Lo hago ahora?</p>
              <div className="mt-1.5 flex flex-wrap gap-1.5">
                <button type="button" className={PRIMARIO} onClick={() => ejecutar(() => confirmarAccion(p.ref), "Confirmada: se ha ejecutado la acción. Mira el resultado en «Acciones pendientes».")}>
                  Sí, confirmar
                </button>
                <button type="button" className={SECUNDARIO} onClick={() => setPaso("inicio")}>
                  Cancelar
                </button>
              </div>
            </div>
          )}
          {p.tipo === "tarea" && (
            <>
              <button type="button" className={PRIMARIO} disabled={ocupado} onClick={() => ejecutar(() => operarTarea(p.ref, "aceptar"), "Tarea aceptada: la sigues en «Tareas».")}>
                {p.etiqueta}
              </button>
              <button type="button" className={SECUNDARIO} disabled={ocupado} onClick={() => ejecutar(() => cancelarTarea(p.ref, null), "Tarea descartada.")}>
                <X size={13} aria-hidden />
                Descartar
              </button>
            </>
          )}
          {p.tipo === "paso" && p.paso && (
            <button type="button" className={PRIMARIO} disabled={ocupado} onClick={() => ejecutar(() => confirmarPaso(p.ref, p.paso as string), "Anotado: la tarea sigue con el paso siguiente.")}>
              {p.etiqueta}
            </button>
          )}
          {p.tipo === "correo" && (
            <>
              <button type="button" className={PRIMARIO} disabled={ocupado} onClick={() => ejecutar(() => decidirPropuesta(p.ref, true), "Guardado en su módulo.")}>
                {p.etiqueta}
              </button>
              <button type="button" className={SECUNDARIO} disabled={ocupado} onClick={() => ejecutar(() => decidirPropuesta(p.ref, false), "Descartado: no se guarda.")}>
                <X size={13} aria-hidden />
                Descartar
              </button>
            </>
          )}
          {p.tipo === "plantilla" && paso !== "creada" && (
            <button
              type="button"
              className={PRIMARIO}
              disabled={ocupado}
              onClick={() =>
                ejecutar(
                  async () => {
                    const detalle = await crearTarea({
                      case_id: caseId,
                      plantilla: p.ref,
                      objetivo: p.objetivo ?? p.etiqueta,
                      parametros: p.parametros,
                      aceptar: false,
                    });
                    setTareaCreada(detalle.tarea.id);
                  },
                  "Tarea preparada. No empieza hasta que la aceptes.",
                  "creada",
                )
              }
            >
              {p.etiqueta}
            </button>
          )}
          {p.tipo === "plantilla" && paso === "creada" && tareaCreada && (
            <button type="button" className={PRIMARIO} onClick={() => ejecutar(() => operarTarea(tareaCreada, "aceptar"), "Tarea aceptada: la sigues en «Tareas».")}>
              Aceptar la tarea
            </button>
          )}
        </div>
      )}
      {mensaje && (
        <p role="status" className="mt-1.5 text-[12.5px] font-semibold text-grounding-cited-fg">
          {mensaje}
        </p>
      )}
      {error && (
        <p role="alert" className="mt-1.5 text-[12.5px] text-risk-high-fg">
          {error}
        </p>
      )}
    </div>
  );
}
