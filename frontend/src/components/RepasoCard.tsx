// The brain's weekly (or daily) digest in the inbox: what matters now, in the order code ranked it, why, where
// each thing comes from, and what it proposes. Every proposal is answered through its OWN existing endpoint
// (pending actions, tasks, mail proposals): this card never acts by itself, and an action that reaches outside the
// app asks once more before it is confirmed. When the structured digest is not available (an older digest, or the
// brain was off), the notification's text is shown as before.

//
// Design system: like every notice it is a row of the inbox's Box (no box of its own). The points are a numbered Box
// list; «¿Por qué?» is a Details; a proposal is a small Box with its buttons, and an action with effects outside the
// app asks again in a ConfirmationDialog (useConfirm).

import { useEffect, useState } from "react";

import { ApiError, getRepaso, type AdvisorNotification, type PropuestaRepaso, type PuntoRepaso, type Repaso } from "../api";
import { decidirPropuesta } from "../apiCorreo";
import { confirmarAccion, rechazarAccion } from "../apiModulos";
import { cancelarTarea, confirmarPaso, crearTarea, operarTarea } from "../apiTareas";
import { errorMessage } from "../lib/memoria";
import { formatDate, relativeTime } from "../lib/presentation";
import { Button, Caja, CargandoTexto, CounterLabel, Details, Flash, Label, useConfirm } from "../ui";
import { CalendarIcon, CheckIcon, ChevronRightIcon, CommentDiscussionIcon, ShieldCheckIcon, TasklistIcon, XIcon } from "../ui/iconos";
import { RichText } from "./RichText";

interface RepasoCardProps {
  notification: AdvisorNotification;
  caseId: string;
  onMarkRead: (id: string) => void;
  onRefresh: () => void;
  onAsk: (question: string) => void;
}

type Carga = { estado: "cargando" } | { estado: "listo"; repaso: Repaso } | { estado: "sin_detalle" };

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
  const urgente = n.severity === "urgente";
  return (
    <article aria-label={n.title} className="flex flex-col gap-2">
      <div className="flex flex-wrap items-center gap-2 text-xs text-fg-muted">
        <TasklistIcon className={urgente ? "text-fg-danger" : "text-fg-muted"} />
        <span>{repaso?.frecuencia === "diaria" ? "Repaso del día" : "Repaso de la semana"}</span>
        {urgente && <Label variant="danger">Urgente</Label>}
        {unread && <Label variant="accent">Nuevo</Label>}
        <span className="ml-auto">{relativeTime(Date.parse(n.created_at))}</span>
      </div>
      <h4 className="m-0 text-base font-semibold">{n.title}</h4>

      {carga.estado === "cargando" && <CargandoTexto lineas={3} etiqueta="Cargando el repaso…" />}

      {repaso ? (
        <>
          <p className="m-0 font-semibold">{repaso.titular}</p>
          <Caja>
            <ol className="m-0 p-0" aria-label="Lo que importa, de más a menos">
              {repaso.puntos.map((punto, i) => (
                <Punto key={punto.id} punto={punto} numero={i + 1} caseId={caseId} onChanged={onRefresh} />
              ))}
            </ol>
          </Caja>
          {repaso.restantes > 0 && (
            <p className="m-0 text-xs text-fg-muted">
              Y {repaso.restantes} aviso{repaso.restantes === 1 ? "" : "s"} más en tu bandeja.
            </p>
          )}
          <p className="m-0 flex items-start gap-2 text-xs text-fg-muted">
            <ShieldCheckIcon size={12} className="mt-0.5 shrink-0" />
            <span>
              No he hecho nada por mi cuenta: cada propuesta espera a que la confirmes. Cada importe es el de su módulo; no se suman entre sí.
              {repaso.origen === "determinista" && " Este repaso está redactado sin el modelo, solo con los datos."}
            </span>
          </p>
        </>
      ) : (
        carga.estado === "sin_detalle" && <RichText text={n.body} />
      )}

      <div className="flex flex-wrap gap-2">
        <Button size="small" leadingVisual={CommentDiscussionIcon} onClick={() => onAsk(`Sobre mi repaso «${n.title}»: ¿por dónde empiezo y qué me recomiendas hacer?`)}>
          Preguntar a Cerebro
        </Button>
        {unread && (
          <Button size="small" variant="invisible" leadingVisual={CheckIcon} onClick={() => onMarkRead(n.id)} aria-label="Ya está: marcar el repaso como leído">
            Ya está
          </Button>
        )}
      </div>
    </article>
  );
}

function Punto({ punto, numero, caseId, onChanged }: { punto: PuntoRepaso; numero: number; caseId: string; onChanged: () => void }) {
  const vence = formatDate(punto.vence);
  return (
    <Caja.Fila className="flex items-start gap-3">
      <CounterLabel variant={punto.urgente ? "primary" : "secondary"} aria-hidden className="mt-0.5">
        {numero}
      </CounterLabel>
      <div className="flex min-w-0 flex-1 flex-col gap-2">
        <p className="m-0 flex flex-wrap items-center gap-2 font-semibold">
          {punto.titulo}
          {punto.urgente && <Label variant="danger">Urgente</Label>}
        </p>
        {punto.texto && <p className="m-0">{punto.texto}</p>}

        <ul className="m-0 flex list-none flex-wrap gap-1 p-0" aria-label="De dónde sale">
          {vence && (
            <li>
              <Label variant="attention">
                <CalendarIcon size={12} className="mr-1" />
                hasta el {vence}
              </Label>
            </li>
          )}
          {punto.fuentes.slice(0, 4).map((f) => (
            <li key={`${f.tipo}:${f.id}`}>
              <Label variant="secondary" title={f.titulo}>
                {f.nombre_modulo ?? f.modulo ?? "Aviso"}
                {f.fecha ? ` · ${formatDate(f.fecha)}` : ""}
              </Label>
            </li>
          ))}
        </ul>

        <Details className="group">
          <Details.Summary className="inline-flex cursor-pointer items-center gap-1 text-xs font-semibold text-fg-accent hover:underline">
            <ChevronRightIcon size={12} className="transition-transform group-open:rotate-90" />
            ¿Por qué?
          </Details.Summary>
          <div className="mt-1 rounded-medium bg-canvas-muted p-2 text-xs">
            {punto.por_que && <p className="m-0">{punto.por_que}</p>}
            {punto.motivos.length > 0 && (
              <ul className="m-0 mt-1 list-disc pl-4">
                {punto.motivos.map((m) => (
                  <li key={m}>{m}</li>
                ))}
              </ul>
            )}
            {punto.fuentes.length > 0 && <p className="m-0 mt-1 text-fg-muted">Fuentes: {punto.fuentes.map((f) => f.titulo).join(" · ")}</p>}
          </div>
        </Details>

        {punto.propuestas.map((p) => (
          <Propuesta key={p.id} propuesta={p} caseId={caseId} onChanged={onChanged} />
        ))}
      </div>
    </Caja.Fila>
  );
}

type Paso = "inicio" | "enviando" | "hecho" | "creada";

function Propuesta({ propuesta: p, caseId, onChanged }: { propuesta: PropuestaRepaso; caseId: string; onChanged: () => void }) {
  const [paso, setPaso] = useState<Paso>("inicio");
  const [mensaje, setMensaje] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [tareaCreada, setTareaCreada] = useState<string | null>(null);
  const confirmar = useConfirm();

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

  async function confirmarYEjecutar() {
    const si = await confirmar({
      title: "¿Lo hago ahora?",
      content: `${p.resumen}. Esto tiene efectos fuera de la app.`,
      confirmButtonContent: "Sí, confirmar",
      cancelButtonContent: "Cancelar",
      confirmButtonType: "primary",
    });
    if (si) await ejecutar(() => confirmarAccion(p.ref), "Confirmada: se ha ejecutado la acción. Mira el resultado en «Acciones pendientes».");
  }

  if (p.estado === "decidida" && paso === "inicio") {
    return <p className="m-0 text-xs text-fg-muted">Propuesta ya decidida: {p.etiqueta.toLowerCase()}.</p>;
  }
  const ocupado = paso === "enviando";
  return (
    <div className="flex flex-col gap-2 rounded-medium border border-border-default p-2">
      <p className="m-0">
        <span className="font-semibold">Te propongo: </span>
        {p.resumen}
      </p>
      {paso !== "hecho" && (
        <div className="flex flex-wrap gap-2" role="group" aria-label={`Responder a la propuesta: ${p.etiqueta}`}>
          {p.tipo === "accion" && (
            <>
              <Button size="small" variant="primary" disabled={ocupado} onClick={() => void confirmarYEjecutar()}>
                {p.etiqueta}
              </Button>
              <Button size="small" leadingVisual={XIcon} disabled={ocupado} onClick={() => ejecutar(() => rechazarAccion(p.ref), "Descartada: no se hará.")}>
                Descartar
              </Button>
            </>
          )}
          {p.tipo === "tarea" && (
            <>
              <Button size="small" variant="primary" disabled={ocupado} onClick={() => ejecutar(() => operarTarea(p.ref, "aceptar"), "Tarea aceptada: la sigues en «Tareas».")}>
                {p.etiqueta}
              </Button>
              <Button size="small" leadingVisual={XIcon} disabled={ocupado} onClick={() => ejecutar(() => cancelarTarea(p.ref, null), "Tarea descartada.")}>
                Descartar
              </Button>
            </>
          )}
          {p.tipo === "paso" && p.paso && (
            <Button size="small" variant="primary" disabled={ocupado} onClick={() => ejecutar(() => confirmarPaso(p.ref, p.paso as string), "Anotado: la tarea sigue con el paso siguiente.")}>
              {p.etiqueta}
            </Button>
          )}
          {p.tipo === "correo" && (
            <>
              <Button size="small" variant="primary" disabled={ocupado} onClick={() => ejecutar(() => decidirPropuesta(p.ref, true), "Guardado en su módulo.")}>
                {p.etiqueta}
              </Button>
              <Button size="small" leadingVisual={XIcon} disabled={ocupado} onClick={() => ejecutar(() => decidirPropuesta(p.ref, false), "Descartado: no se guarda.")}>
                Descartar
              </Button>
            </>
          )}
          {p.tipo === "plantilla" && paso !== "creada" && (
            <Button
              size="small"
              variant="primary"
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
            </Button>
          )}
          {p.tipo === "plantilla" && paso === "creada" && tareaCreada && (
            <Button size="small" variant="primary" onClick={() => ejecutar(() => operarTarea(tareaCreada, "aceptar"), "Tarea aceptada: la sigues en «Tareas».")}>
              Aceptar la tarea
            </Button>
          )}
        </div>
      )}
      {mensaje && (
        <p role="status" className="m-0 text-xs font-semibold text-fg-success">
          {mensaje}
        </p>
      )}
      {error && (
        <Flash variant="danger" role="alert">
          {error}
        </Flash>
      )}
    </div>
  );
}
